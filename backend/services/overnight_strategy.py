"""Auditable intraday implementation of the upgraded overnight strategy."""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import desc, func, select

from database import async_session
from models import (
    OvernightPosition,
    OvernightStrategyRun,
    StockDailyBar,
    StockMinuteBar,
)
from quant.indicators import normalize_snapshot_stock
from quant.risk import CRITICAL_ANNOUNCEMENT_TERMS
from services.data_collector import collector, shanghai_now
from services.history_cache import history_cache
from services.macro_policy_news import macro_policy_news_collector
from services.quote_cache import quote_snapshot_service
from services.report_calendar import report_calendar_service


STRATEGY_CONFIG: dict[str, Any] = {
    "name": "一夜持股",
    "version": "1.0",
    "preliminary_scan": "14:30",
    "entry_window": "14:45-14:55",
    "exit_window": "次一交易日 09:30-10:00",
    "change_pct": [3.0, 5.0],
    "volume_ratio_min": 1.2,
    "turnover_pct": [3.0, 9.0],
    "market_cap_yi": [40.0, 230.0],
    "minimum_listing_sessions": 60,
    "last_five_minute_change_max": 2.0,
    "max_positions": 5,
    "shares_per_position": 100,
    "reference_capital": 1_000_000.0,
    "max_position_pct": 10.0,
    "max_total_position_pct": 50.0,
    "commission_rate": 0.0003,
    "slippage_rate": 0.001,
    "stamp_tax_rate": 0.0005,
}

MAJOR_NEGATIVE_TERMS = tuple(dict.fromkeys((
    *CRITICAL_ANNOUNCEMENT_TERMS,
    "业绩预亏", "业绩大幅下降", "大额减持", "违规担保", "重大诉讼",
    "行政处罚", "债务违约", "终止重组", "下修业绩", "审计保留意见",
)))

RUN_STAGES = {"preliminary", "entry", "exit", "force_exit"}


def _number(value: object) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _datetime(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _local_naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None)


def _condition(
    key: str,
    label: str,
    status: str,
    actual: Any,
    expected: str,
    *,
    source: str,
    detail: str = "",
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "actual": actual,
        "expected": expected,
        "source": source,
        "detail": detail,
    }


def _run_view(row: OvernightStrategyRun | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "stage": row.stage,
        "trigger": row.trigger,
        "status": row.status,
        "progress": row.progress,
        "message": row.message or "",
        "data_date": row.data_date.isoformat() if row.data_date else None,
        "is_realtime": bool(row.is_realtime),
        "scanned_count": row.scanned_count,
        "prefiltered_count": row.prefiltered_count,
        "qualified_count": row.qualified_count,
        "candidates": row.candidates or [],
        "data_quality": row.data_quality or {},
        "error": row.error,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _limit_down_threshold(code: str) -> float:
    if code.startswith(("4", "8", "92")):
        return -29.5
    if code.startswith(("300", "301", "302", "688", "689")):
        return -19.5
    return -9.5


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


class OvernightStrategyService:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()

    async def _set_progress(self, run_id: int, progress: int, message: str) -> None:
        async with async_session() as session:
            row = await session.get(OvernightStrategyRun, run_id)
            if row is None:
                return
            row.status = "running"
            row.progress = min(max(int(progress), 0), 99)
            row.message = message[:300]
            row.started_at = row.started_at or datetime.utcnow()
            await session.commit()

    async def _finish(
        self,
        run_id: int,
        *,
        status: str,
        message: str,
        data_date: date | None = None,
        is_realtime: bool = False,
        scanned_count: int = 0,
        prefiltered_count: int = 0,
        candidates: list[dict] | None = None,
        data_quality: dict | None = None,
        error: str | None = None,
    ) -> None:
        candidate_rows = candidates or []
        async with async_session() as session:
            row = await session.get(OvernightStrategyRun, run_id)
            if row is None:
                return
            row.status = status
            row.progress = 100
            row.message = message[:300]
            row.data_date = data_date
            row.is_realtime = is_realtime
            row.scanned_count = scanned_count
            row.prefiltered_count = prefiltered_count
            row.qualified_count = sum(bool(item.get("qualified")) for item in candidate_rows)
            row.candidates = candidate_rows
            row.data_quality = data_quality or {}
            row.error = error
            row.finished_at = datetime.utcnow()
            await session.commit()

    async def _create_run(self, stage: str, trigger: str) -> tuple[OvernightStrategyRun, bool]:
        normalized = str(stage or "").strip().lower()
        normalized_trigger = str(trigger or "manual").strip().lower()[:20]
        if normalized not in RUN_STAGES:
            raise ValueError("stage 必须是 preliminary、entry、exit 或 force_exit")
        async with async_session() as session:
            active = (await session.execute(
                select(OvernightStrategyRun)
                .where(
                    OvernightStrategyRun.stage == normalized,
                    OvernightStrategyRun.status.in_(["queued", "running"]),
                )
                .order_by(desc(OvernightStrategyRun.id))
                .limit(1)
            )).scalar_one_or_none()
            if active is not None:
                age = datetime.utcnow() - (active.started_at or active.created_at or datetime.utcnow())
                if age <= timedelta(minutes=30):
                    return active, False
                active.status = "failed"
                active.progress = 100
                active.message = "上次运行进程已中断"
                active.error = "WorkerInterrupted"
                active.finished_at = datetime.utcnow()
            if normalized_trigger != "manual":
                recent = (await session.execute(
                    select(OvernightStrategyRun)
                    .where(
                        OvernightStrategyRun.stage == normalized,
                        OvernightStrategyRun.status == "completed",
                    )
                    .order_by(desc(OvernightStrategyRun.id))
                    .limit(1)
                )).scalar_one_or_none()
                if recent is not None:
                    finished_at = recent.finished_at or recent.created_at or datetime.min
                    if datetime.utcnow() - finished_at <= timedelta(minutes=5):
                        return recent, False
            row = OvernightStrategyRun(
                stage=normalized,
                trigger=normalized_trigger,
                status="queued",
                progress=0,
                message="等待策略引擎开始",
                candidates=[],
                data_quality={},
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row, True

    def _spawn(self, run_id: int) -> None:
        task = asyncio.create_task(self._execute(run_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def start(
        self,
        stage: str,
        *,
        trigger: str = "manual",
        background: bool = True,
    ) -> dict[str, Any]:
        row, created = await self._create_run(stage, trigger)
        if created:
            if background:
                self._spawn(row.id)
            else:
                await self._execute(row.id)
                async with async_session() as session:
                    row = await session.get(OvernightStrategyRun, row.id)
        return {"run": _run_view(row), "created": created}

    async def _execute(self, run_id: int) -> None:
        async with self._lock:
            try:
                async with async_session() as session:
                    row = await session.get(OvernightStrategyRun, run_id)
                    if row is None:
                        return
                    stage = row.stage
                if stage in {"preliminary", "entry"}:
                    await self._scan(run_id, stage)
                else:
                    await self._exit(run_id, force=stage == "force_exit")
            except Exception as exc:
                await self._finish(
                    run_id,
                    status="failed",
                    message="一夜持股策略运行失败，可稍后重试",
                    error=type(exc).__name__,
                    data_quality={"exception": type(exc).__name__},
                )

    @staticmethod
    def _stage_window_status(stage: str, now: datetime) -> tuple[bool, str]:
        minute = now.hour * 60 + now.minute
        if stage == "preliminary":
            return 14 * 60 + 25 <= minute <= 14 * 60 + 40, "预扫描只在交易日14:25-14:40执行"
        return 14 * 60 + 45 <= minute <= 14 * 60 + 55, "模拟入场只在交易日14:45-14:55执行"

    @staticmethod
    async def _daily_bars(codes: list[str], today: date) -> dict[str, list[dict]]:
        if not codes:
            return {}
        cutoff = today - timedelta(days=400)
        async with async_session() as session:
            rows = (await session.execute(
                select(StockDailyBar)
                .where(
                    StockDailyBar.stock_code.in_(codes),
                    StockDailyBar.trade_date >= cutoff,
                    StockDailyBar.trade_date < today,
                )
                .order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
            )).scalars().all()
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row.stock_code, []).append({
                "date": row.trade_date.isoformat(),
                "open": row.open_price,
                "close": row.close_price,
                "high": row.high_price,
                "low": row.low_price,
                "volume": row.volume,
                "change_pct": row.change_pct,
            })
        return grouped

    @staticmethod
    def _prefilter(stocks: list[dict]) -> list[dict]:
        selected = []
        for raw in stocks:
            stock = normalize_snapshot_stock(raw)
            name = str(stock.get("name") or "")
            change = _number(stock.get("change_pct"))
            ratio = _number(stock.get("vol_ratio"))
            turnover = _number(stock.get("turnover"))
            market_cap = _number(stock.get("market_cap"))
            price = _number(stock.get("price"))
            if price is None or price <= 0 or "ST" in name.upper() or "退" in name:
                continue
            if change is None or not 3.0 <= change <= 5.0:
                continue
            if ratio is None or ratio <= 1.2:
                continue
            if turnover is None or not 3.0 <= turnover <= 9.0:
                continue
            if market_cap is None or not 40.0 <= market_cap <= 230.0:
                continue
            selected.append(stock)
        return selected

    @staticmethod
    def _moving_average(previous_closes: list[float], current_price: float, period: int) -> float | None:
        values = [*previous_closes, current_price]
        return _average(values[-period:]) if len(values) >= period else None

    @staticmethod
    def _daily_audit(
        stock: dict,
        bars: list[dict],
        *,
        today: date,
        announcements: list[dict] | None,
        announcement_available: bool,
        report_dates: list[str],
        report_available: bool,
    ) -> dict[str, Any]:
        price = _number(stock.get("price"))
        previous_closes = [
            value for item in bars
            for value in [_number(item.get("close"))]
            if value is not None and value > 0
        ]
        ma = {
            period: OvernightStrategyService._moving_average(previous_closes, price, period)
            if price is not None else None
            for period in (5, 10, 20, 30)
        }
        conditions = [
            _condition("change_pct", "当日涨幅", "passed", round(float(stock["change_pct"]), 2), "3%-5%", source="全市场实时行情"),
            _condition("volume_ratio", "量比", "passed", round(float(stock["vol_ratio"]), 2), ">1.2", source="全市场实时行情"),
            _condition("turnover", "换手率", "passed", round(float(stock["turnover"]), 2), "3%-9%", source="全市场实时行情"),
            _condition("market_cap", "总市值", "passed", round(float(stock["market_cap"]), 2), "40-230亿元", source="全市场实时行情"),
        ]

        listing_ok = len(bars) >= STRATEGY_CONFIG["minimum_listing_sessions"]
        conditions.append(_condition(
            "listing_sessions", "上市交易日", "passed" if listing_ok else "unavailable",
            len(bars), ">=60个已缓存交易日", source="本地日线缓存",
            detail="缓存不足时不会把股票当作非次新股",
        ))

        ma_available = all(value is not None for value in ma.values())
        ma_order = bool(ma_available and ma[10] > ma[20] > ma[30])
        price_above = bool(ma_available and price is not None and price > ma[5] and price > ma[10])
        conditions.extend([
            _condition(
                "ma_order", "均线多头排列",
                "passed" if ma_order else "failed" if ma_available else "unavailable",
                {f"ma{key}": round(value, 3) if value is not None else None for key, value in ma.items()},
                "MA10 > MA20 > MA30", source="缓存日线+当前实时价",
            ),
            _condition(
                "above_ma", "价格站上均线",
                "passed" if price_above else "failed" if ma_available else "unavailable",
                round(price, 3) if price is not None else None,
                "价格 > MA5 且价格 > MA10", source="缓存日线+当前实时价",
            ),
        ])

        recent = bars[-5:]
        limit_down_days = [
            item.get("date") for item in recent
            if (_number(item.get("change_pct")) is not None
                and float(item["change_pct"]) <= _limit_down_threshold(str(stock.get("code") or "")))
        ]
        recent_available = len(recent) == 5
        conditions.append(_condition(
            "recent_limit_down", "近5日无跌停",
            "passed" if recent_available and not limit_down_days else "failed" if limit_down_days else "unavailable",
            limit_down_days, "最近5个交易日无跌停", source="本地日线缓存",
        ))

        trailing = bars[-19:]
        highs = [value for item in trailing for value in [_number(item.get("high"))] if value is not None]
        lows = [value for item in trailing for value in [_number(item.get("low"))] if value is not None]
        current_high = _number(stock.get("high")) or price
        current_low = _number(stock.get("low")) or price
        if current_high is not None:
            highs.append(current_high)
        if current_low is not None:
            lows.append(current_low)
        fib = None
        fib_pass = False
        if len(trailing) >= 19 and highs and lows and max(highs) > min(lows) and price is not None:
            high, low = max(highs), min(lows)
            fib = {
                "high": round(high, 3),
                "low": round(low, 3),
                "s382": round(high - (high - low) * 0.382, 3),
                "s500": round(high - (high - low) * 0.5, 3),
                "s618": round(high - (high - low) * 0.618, 3),
            }
            fib_pass = price >= fib["s618"]
        conditions.append(_condition(
            "fibonacci", "20日斐波那契保护",
            "passed" if fib_pass else "failed" if fib is not None else "unavailable",
            {"price": round(price, 3) if price is not None else None, **(fib or {})},
            "价格不得跌破0.618回撤位", source="缓存日线+当前实时价",
            detail="仅作为可检验的价格层，不宣称天然提高胜率",
        ))

        negative_today = []
        for item in announcements or []:
            if _date(item.get("published_at")) != today:
                continue
            title = str(item.get("title") or "")
            if any(term in title for term in MAJOR_NEGATIVE_TERMS):
                negative_today.append(title)
        conditions.append(_condition(
            "major_negative", "当日无重大利空公告",
            "passed" if announcement_available and not negative_today else "failed" if negative_today else "unavailable",
            negative_today, "无重大利空关键词命中的当日公告", source="东方财富公告/FTShare MCP",
        ))

        conditions.append(_condition(
            "report_window", "避开财报前3日",
            "passed" if report_available and not report_dates else "failed" if report_dates else "unavailable",
            report_dates, "未来3日无预约财报披露", source="东方财富预约披露时间表",
        ))

        unavailable = [item["label"] for item in conditions if item["status"] == "unavailable"]
        failed = [item["label"] for item in conditions if item["status"] == "failed"]
        passed = not unavailable and not failed
        trend_spread = (
            (ma[10] - ma[30]) / ma[30] * 100
            if ma.get(10) is not None and ma.get(30) not in (None, 0) else 0.0
        )
        score = 55.0
        score += max(0.0, 12.0 - abs(float(stock.get("change_pct") or 0) - 4.0) * 6.0)
        score += min(10.0, max(0.0, (float(stock.get("vol_ratio") or 0) - 1.2) * 12.0))
        score += min(12.0, max(0.0, trend_spread * 4.0))
        if fib and price is not None and price >= fib["s500"]:
            score += 6.0
        return {
            "conditions": conditions,
            "daily_passed": passed,
            "failed_reasons": failed,
            "unavailable_reasons": unavailable,
            "score": round(min(max(score, 0.0), 100.0), 1),
            "ma": {f"ma{key}": round(value, 4) if value is not None else None for key, value in ma.items()},
            "fib": fib,
        }

    @staticmethod
    def _minute_audit(payload: dict, now: datetime) -> dict[str, Any]:
        today_text = now.date().isoformat()
        bars = [
            item for item in payload.get("bars") or []
            if str(item.get("bar_time") or "").startswith(today_text)
            and (_datetime(item.get("bar_time")) or datetime.min) <= _local_naive(now)
        ]
        latest = bars[-1] if bars else None
        latest_at = _datetime(latest.get("bar_time")) if latest else None
        conditions = []
        fresh = bool(
            latest_at
            and 14 * 60 + 45 <= latest_at.hour * 60 + latest_at.minute <= 14 * 60 + 55
            and 0 <= (_local_naive(now) - latest_at).total_seconds() <= 10 * 60
        )
        conditions.append(_condition(
            "minute_freshness", "入场窗口分钟行情",
            "passed" if fresh else "unavailable",
            latest_at.isoformat(timespec="minutes") if latest_at else None,
            "当日14:45-14:55且延迟不超过10分钟", source="东方财富1分钟分时",
        ))

        last_five_change = None
        if latest_at and latest:
            cutoff = latest_at - timedelta(minutes=5)
            anchor = next(
                (item for item in reversed(bars) if (_datetime(item.get("bar_time")) or datetime.max) <= cutoff),
                None,
            )
            anchor_close = _number((anchor or {}).get("close"))
            latest_close = _number(latest.get("close"))
            if anchor_close not in (None, 0) and latest_close is not None:
                last_five_change = (latest_close / anchor_close - 1) * 100
        conditions.append(_condition(
            "last_five_change", "排除尾盘5分钟急拉",
            "passed" if last_five_change is not None and last_five_change <= 2.0 else "failed" if last_five_change is not None else "unavailable",
            round(last_five_change, 3) if last_five_change is not None else None,
            "最近5分钟涨幅<=2%", source="东方财富1分钟分时",
        ))

        volumes = [
            float(value) for item in bars[-35:]
            for value in [_number(item.get("volume"))]
            if value is not None and value > 0
        ]
        pulse = None
        volume_detail = ""
        if len(volumes) >= 15:
            baseline_values = volumes[:-5]
            recent_values = volumes[-5:]
            baseline = median(baseline_values) if baseline_values else 0.0
            recent_average = _average(recent_values) or 0.0
            pulse = bool(baseline <= 0 or recent_average > baseline * 3 or max(recent_values) > baseline * 5)
            volume_detail = f"近5分钟均量/此前中位数={recent_average / baseline:.2f}" if baseline else "此前成交量基线为0"
        conditions.append(_condition(
            "pulse_volume", "排除脉冲爆量",
            "passed" if pulse is False else "failed" if pulse is True else "unavailable",
            pulse, "近5分钟均量<=基线3倍且单分钟<=基线5倍", source="东方财富1分钟分时",
            detail=volume_detail,
        ))

        failed = [item["label"] for item in conditions if item["status"] == "failed"]
        unavailable = [item["label"] for item in conditions if item["status"] == "unavailable"]
        market_price = _number((latest or {}).get("close"))
        return {
            "conditions": conditions,
            "minute_passed": not failed and not unavailable,
            "failed_reasons": failed,
            "unavailable_reasons": unavailable,
            "latest_bar_at": latest_at.isoformat(timespec="minutes") if latest_at else None,
            "market_price": market_price,
            "entry_price": (
                round(market_price * (1 + STRATEGY_CONFIG["slippage_rate"]), 4)
                if market_price is not None else None
            ),
            "bars": bars,
        }

    @staticmethod
    async def _persist_minute_bars(payloads: list[dict]) -> int:
        rows = []
        for payload in payloads:
            for item in payload.get("bars") or []:
                bar_time = _datetime(item.get("bar_time"))
                if bar_time is None:
                    continue
                rows.append({
                    "stock_code": str(item.get("stock_code") or payload.get("stock_code") or ""),
                    "stock_name": str(item.get("stock_name") or payload.get("stock_name") or ""),
                    "bar_time": bar_time,
                    "interval_minutes": int(item.get("interval_minutes") or 1),
                    "open_price": _number(item.get("open")),
                    "close_price": _number(item.get("close")),
                    "high_price": _number(item.get("high")),
                    "low_price": _number(item.get("low")),
                    "volume": int(_number(item.get("volume")) or 0),
                    "amount": int(_number(item.get("amount")) or 0),
                    "average_price": _number(item.get("average")),
                    "source": str(payload.get("source") or "eastmoney"),
                    "updated_at": datetime.utcnow(),
                })
        return await history_cache._upsert(
            StockMinuteBar, rows, ["stock_code", "bar_time", "interval_minutes"],
        ) if rows else 0

    @staticmethod
    async def _appointment_map(codes: list[str], today: date) -> tuple[dict[str, list[str]], bool]:
        try:
            rows = await report_calendar_service._fetch_appointments(codes, today)
        except Exception:
            return {}, False
        output: dict[str, list[str]] = {code: [] for code in codes}
        deadline = today + timedelta(days=3)
        for item in rows:
            code = str(item.get("SECURITY_CODE") or "")
            publish_date = _date(item.get("APPOINT_PUBLISH_DATE"))
            if code in output and publish_date and today <= publish_date <= deadline:
                output[code].append(publish_date.isoformat())
        return output, True

    async def _scan(self, run_id: int, stage: str) -> None:
        now = shanghai_now()
        window_open, window_message = self._stage_window_status(stage, now)
        if now.weekday() >= 5 or not window_open:
            await self._finish(
                run_id,
                status="unavailable",
                message=window_message,
                data_date=now.date(),
                data_quality={
                    "quote": "not_requested",
                    "missing_policy": "非执行窗口不使用缓存行情建立信号或持仓",
                },
                error="OutsideExecutionWindow",
            )
            return

        await self._set_progress(run_id, 5, "正在获取完整A股实时横截面")
        try:
            snapshot = await collector.fetch_quant_market_snapshot()
        except Exception as exc:
            await self._finish(
                run_id,
                status="unavailable",
                message="完整实时行情不可用，本轮不产生一夜持股信号",
                data_quality={"quote": "unavailable", "exception": type(exc).__name__},
                error=type(exc).__name__,
            )
            return

        data_date = _date(snapshot.get("data_date"))
        realtime = bool(snapshot.get("is_realtime")) and data_date == now.date()
        stocks = list(snapshot.get("stocks") or [])
        if not snapshot.get("complete") or not realtime:
            await self._finish(
                run_id,
                status="unavailable",
                message="行情不是当日完整实时快照，本轮不产生一夜持股信号",
                data_date=data_date,
                scanned_count=len(stocks),
                data_quality={
                    "quote": "stale_or_incomplete",
                    "complete": bool(snapshot.get("complete")),
                    "is_realtime": bool(snapshot.get("is_realtime")),
                    "data_date": snapshot.get("data_date"),
                    "missing_policy": "缓存行情只供查看，不用于尾盘模拟买入",
                },
                error="RealtimeSnapshotRequired",
            )
            return

        prefiltered = self._prefilter(stocks)
        await self._set_progress(run_id, 24, f"静态条件通过 {len(prefiltered)} 只，正在核验日线与黑名单")
        codes = [str(item["code"]) for item in prefiltered]
        bars_result, announcements_result, appointments_result = await asyncio.gather(
            self._daily_bars(codes, now.date()),
            macro_policy_news_collector.get_stock_announcements_audit(codes, max_stocks=min(len(codes), 64)),
            self._appointment_map(codes, now.date()),
            return_exceptions=True,
        )
        bars_by_code = {} if isinstance(bars_result, Exception) else bars_result
        announcement_payload = {} if isinstance(announcements_result, Exception) else announcements_result
        announcements = announcement_payload.get("announcements") or {}
        announcement_status = announcement_payload.get("status") or {}
        appointment_map, report_available = ({}, False) if isinstance(appointments_result, Exception) else appointments_result

        candidates = []
        for stock in prefiltered:
            code = str(stock["code"])
            audit = self._daily_audit(
                stock,
                bars_by_code.get(code, []),
                today=now.date(),
                announcements=announcements.get(code),
                announcement_available=bool((announcement_status.get(code) or {}).get("available")),
                report_dates=appointment_map.get(code, []),
                report_available=report_available,
            )
            candidates.append({
                "code": code,
                "name": str(stock.get("name") or code),
                "sector": str(stock.get("sector") or ""),
                "price": _number(stock.get("price")),
                "previous_close": _number(stock.get("previous_close")),
                "change_pct": _number(stock.get("change_pct")),
                "volume_ratio": _number(stock.get("vol_ratio")),
                "turnover": _number(stock.get("turnover")),
                "market_cap_yi": _number(stock.get("market_cap")),
                "score": audit["score"],
                "daily_passed": audit["daily_passed"],
                "minute_passed": None,
                "qualified": False,
                "selected_for_entry": False,
                "failed_reasons": audit["failed_reasons"],
                "unavailable_reasons": audit["unavailable_reasons"],
                "conditions": audit["conditions"],
                "ma": audit["ma"],
                "fib": audit["fib"],
                "minute": None,
            })

        daily_passed = [item for item in candidates if item["daily_passed"]]
        minute_payloads: list[dict] = []
        minute_covered = 0
        if stage == "entry" and daily_passed:
            await self._set_progress(run_id, 62, f"日线与黑名单通过 {len(daily_passed)} 只，正在复核1分钟分时")
            semaphore = asyncio.Semaphore(8)

            async def fetch_minutes(candidate: dict) -> tuple[str, dict | Exception]:
                async with semaphore:
                    try:
                        return candidate["code"], await collector.fetch_stock_minute_trends(candidate["code"], days=1)
                    except Exception as exc:
                        return candidate["code"], exc

            minute_results = await asyncio.gather(*(fetch_minutes(item) for item in daily_passed))
            by_code = {code: payload for code, payload in minute_results}
            for candidate in daily_passed:
                payload = by_code.get(candidate["code"])
                if not isinstance(payload, dict):
                    candidate["unavailable_reasons"].append("1分钟分时行情")
                    candidate["conditions"].append(_condition(
                        "minute_source", "1分钟分时行情", "unavailable", None,
                        "当日可验证分钟数据", source="东方财富1分钟分时",
                    ))
                    continue
                minute_payloads.append(payload)
                minute_audit = self._minute_audit(payload, now)
                minute_covered += bool(
                    payload.get("is_realtime")
                    and _date(payload.get("data_date")) == now.date()
                    and any(
                        item.get("key") == "minute_freshness" and item.get("status") == "passed"
                        for item in minute_audit["conditions"]
                    )
                )
                candidate["minute_passed"] = minute_audit["minute_passed"]
                candidate["failed_reasons"].extend(minute_audit["failed_reasons"])
                candidate["unavailable_reasons"].extend(minute_audit["unavailable_reasons"])
                candidate["conditions"].extend(minute_audit["conditions"])
                candidate["minute"] = {
                    "latest_bar_at": minute_audit["latest_bar_at"],
                    "market_price": minute_audit["market_price"],
                    "entry_price": minute_audit["entry_price"],
                }
                candidate["qualified"] = bool(candidate["daily_passed"] and candidate["minute_passed"])
                if candidate["qualified"]:
                    candidate["score"] = round(min(100.0, candidate["score"] + 5.0), 1)
            try:
                await self._persist_minute_bars(minute_payloads)
            except Exception:
                pass
        elif stage == "preliminary":
            for candidate in daily_passed:
                candidate["unavailable_reasons"] = ["等待14:45-14:55最终分钟复核"]

        candidates.sort(key=lambda item: (bool(item.get("qualified")), item.get("score") or 0), reverse=True)
        selected_count = 0
        if stage == "entry":
            await self._set_progress(run_id, 88, "分钟条件核验完成，正在执行仓位上限并建立100股模拟持仓")
            selected_count = await self._create_positions(run_id, candidates, now)

        data_quality = {
            "quote": {
                "source": snapshot.get("source", "eastmoney"),
                "data_date": snapshot.get("data_date"),
                "is_realtime": realtime,
                "complete": bool(snapshot.get("complete")),
                "stocks": len(stocks),
            },
            "daily_history": {
                "covered": sum(len(bars_by_code.get(code, [])) >= 60 for code in codes),
                "requested": len(codes),
            },
            "announcements": {
                "covered": sum(bool(item.get("available")) for item in announcement_status.values()),
                "requested": len(codes),
                "status": announcement_status,
            },
            "report_calendar": {"available": report_available},
            "minute": {
                "covered": minute_covered,
                "requested": len(daily_passed) if stage == "entry" else 0,
                "persisted_forward_only": True,
            },
            "missing_policy": "任一强制字段缺失即不入选；不会以日线推断尾盘分钟条件",
            "backtest_limitation": "现有分钟缓存不是历史全市场点时样本，不能据此宣称十年精确回测或固定胜率",
        }
        if stage == "preliminary":
            message = f"14:30预扫描完成：{len(daily_passed)}只等待14:50最终分钟复核"
        else:
            message = f"尾盘复核完成：{sum(item['qualified'] for item in candidates)}只合格，模拟买入{selected_count}只"
        await self._finish(
            run_id,
            status="completed",
            message=message,
            data_date=data_date,
            is_realtime=True,
            scanned_count=len(stocks),
            prefiltered_count=len(prefiltered),
            candidates=candidates[:120],
            data_quality=data_quality,
        )

    async def _create_positions(self, run_id: int, candidates: list[dict], now: datetime) -> int:
        qualified = [item for item in candidates if item.get("qualified")]
        qualified.sort(key=lambda item: (item.get("score") or 0, item.get("code") or ""), reverse=True)
        async with async_session() as session:
            open_rows = (await session.execute(
                select(OvernightPosition).where(OvernightPosition.status == "open")
            )).scalars().all()
            open_codes = {row.stock_code for row in open_rows}
            occupied_pct = sum(float(row.allocated_pct or 0) for row in open_rows)
            remaining_slots = max(0, STRATEGY_CONFIG["max_positions"] - len(open_rows))
            selected = 0
            for candidate in qualified:
                if selected >= remaining_slots:
                    break
                if candidate["code"] in open_codes:
                    candidate["failed_reasons"].append("已有未平仓一夜持股模拟仓位")
                    candidate["qualified"] = False
                    continue
                entry_price = _number((candidate.get("minute") or {}).get("entry_price"))
                entry_at = _datetime((candidate.get("minute") or {}).get("latest_bar_at"))
                if entry_price is None or entry_at is None:
                    candidate["qualified"] = False
                    candidate["unavailable_reasons"].append("有效模拟成交价")
                    continue
                cost = entry_price * STRATEGY_CONFIG["shares_per_position"]
                allocated_pct = cost / STRATEGY_CONFIG["reference_capital"] * 100
                if allocated_pct > STRATEGY_CONFIG["max_position_pct"]:
                    candidate["qualified"] = False
                    candidate["failed_reasons"].append("100股成本超过参考资金10%单股上限")
                    continue
                if occupied_pct + allocated_pct > STRATEGY_CONFIG["max_total_position_pct"]:
                    candidate["qualified"] = False
                    candidate["failed_reasons"].append("短线总仓位将超过50%")
                    continue
                session.add(OvernightPosition(
                    entry_run_id=run_id,
                    stock_code=candidate["code"],
                    stock_name=candidate["name"],
                    sector=candidate.get("sector"),
                    status="open",
                    shares=STRATEGY_CONFIG["shares_per_position"],
                    signal_at=_local_naive(now),
                    entry_at=entry_at,
                    entry_price=entry_price,
                    previous_close=_number(candidate.get("previous_close")),
                    reference_capital=STRATEGY_CONFIG["reference_capital"],
                    allocated_pct=allocated_pct,
                    audit={
                        "strategy": STRATEGY_CONFIG["name"],
                        "score": candidate.get("score"),
                        "conditions": candidate.get("conditions") or [],
                        "entry_market_price": (candidate.get("minute") or {}).get("market_price"),
                        "entry_slippage_rate": STRATEGY_CONFIG["slippage_rate"],
                    },
                ))
                candidate["selected_for_entry"] = True
                occupied_pct += allocated_pct
                open_codes.add(candidate["code"])
                selected += 1
            await session.commit()
        return selected

    @staticmethod
    def _exit_decision(
        position: OvernightPosition,
        payload: dict,
        now: datetime,
        *,
        force: bool,
    ) -> dict[str, Any]:
        entry_day = position.entry_at.date()
        today = now.date()
        if today <= entry_day:
            return {"ready": False, "reason": "A股T+1限制，同日不得卖出", "data_status": "t_plus_one"}
        if force and now.hour * 60 + now.minute < 10 * 60:
            return {"ready": False, "reason": "10:00强制退出尚未到执行时间", "data_status": "outside_window"}
        bars = [
            item for item in payload.get("bars") or []
            if str(item.get("bar_time") or "").startswith(today.isoformat())
            and 9 * 60 + 30 <= ((_datetime(item.get("bar_time")) or datetime.min).hour * 60 + (_datetime(item.get("bar_time")) or datetime.min).minute) <= 10 * 60
        ]
        if not bars:
            return {"ready": False, "reason": "次日09:30-10:00分钟行情缺失", "data_status": "unavailable"}
        first = bars[0]
        first_time = _datetime(first.get("bar_time"))
        open_price = _number(first.get("open")) or _number(first.get("close"))
        prior_close = _number(payload.get("pre_close")) or _number(position.previous_close) or position.entry_price
        if open_price is None or prior_close in (None, 0):
            return {"ready": False, "reason": "开盘价或昨收缺失", "data_status": "unavailable"}
        gap = (open_price / prior_close - 1) * 100
        market_price = None
        exit_time = None
        reason = ""
        if gap >= 3:
            market_price, exit_time, reason = open_price, first_time, "高开3%以上，按开盘纪律清仓"
        elif gap < -1:
            market_price, exit_time, reason = open_price, first_time, "低开，隔夜逻辑失效，按开盘纪律清仓"
        else:
            target = open_price * 1.02 if gap >= 1 else position.entry_price
            target_reason = "高开1%-3%后冲高2%，按计划止盈" if gap >= 1 else "平开后回到成本线，按计划离场"
            stop_price = position.entry_price * 0.97
            for item in bars:
                item_time = _datetime(item.get("bar_time"))
                if (_number(item.get("low")) or math.inf) <= stop_price:
                    market_price, exit_time, reason = stop_price, item_time, "次日跌至入场价-3%，按止损纪律清仓"
                    break
                if (_number(item.get("high")) or -math.inf) >= target:
                    market_price, exit_time, reason = target, item_time, target_reason
                    break

        latest_time = _datetime(bars[-1].get("bar_time"))
        deadline_reached = bool(force or (latest_time and latest_time.hour * 60 + latest_time.minute >= 10 * 60) or now.hour * 60 + now.minute >= 10 * 60)
        if market_price is None and deadline_reached:
            market_price = _number(bars[-1].get("close"))
            exit_time = latest_time
            reason = "10:00前强制清仓，不延长持有"
        if market_price is None or exit_time is None:
            return {
                "ready": False,
                "reason": "尚未触发早盘离场条件，10:00将强制退出",
                "data_status": "monitoring",
                "opening_gap_pct": round(gap, 3),
            }
        execution_price = market_price * (1 - STRATEGY_CONFIG["slippage_rate"])
        shares = int(position.shares or 100)
        buy_amount = position.entry_price * shares
        sell_amount = execution_price * shares
        fees = (
            buy_amount * STRATEGY_CONFIG["commission_rate"]
            + sell_amount * (STRATEGY_CONFIG["commission_rate"] + STRATEGY_CONFIG["stamp_tax_rate"])
        )
        pnl = sell_amount - buy_amount - fees
        return {
            "ready": True,
            "exit_at": exit_time,
            "market_price": market_price,
            "exit_price": round(execution_price, 4),
            "reason": reason,
            "opening_gap_pct": round(gap, 3),
            "fees": round(fees, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / buy_amount * 100, 3) if buy_amount else None,
            "data_status": "available",
        }

    async def _exit(self, run_id: int, *, force: bool) -> None:
        now = shanghai_now()
        await self._set_progress(run_id, 10, "正在读取未平仓一夜持股模拟仓位")
        async with async_session() as session:
            positions = (await session.execute(
                select(OvernightPosition)
                .where(OvernightPosition.status == "open")
                .order_by(OvernightPosition.entry_at)
            )).scalars().all()
        if not positions:
            await self._finish(
                run_id,
                status="completed",
                message="当前没有待退出的一夜持股模拟仓位",
                data_date=now.date(),
                data_quality={"positions": 0, "missing_policy": "无仓位时不生成虚拟卖出记录"},
            )
            return

        await self._set_progress(run_id, 28, f"正在核验 {len(positions)} 只持仓的次日分钟行情")
        semaphore = asyncio.Semaphore(8)

        async def fetch(position: OvernightPosition) -> tuple[int, dict | Exception]:
            async with semaphore:
                try:
                    return position.id, await collector.fetch_stock_minute_trends(position.stock_code, days=1)
                except Exception as exc:
                    return position.id, exc

        results = dict(await asyncio.gather(*(fetch(position) for position in positions)))
        payloads = [item for item in results.values() if isinstance(item, dict)]
        realtime_payloads = [
            item for item in payloads
            if item.get("is_realtime") and _date(item.get("data_date")) == now.date()
        ]
        try:
            await self._persist_minute_bars(payloads)
        except Exception:
            pass
        decisions = []
        exited = 0
        async with async_session() as session:
            for position in positions:
                payload = results.get(position.id)
                if not isinstance(payload, dict):
                    decisions.append({"code": position.stock_code, "ready": False, "reason": "分钟行情源不可用"})
                    continue
                decision = self._exit_decision(position, payload, now, force=force)
                decisions.append({"code": position.stock_code, "name": position.stock_name, **decision})
                if not decision.get("ready"):
                    continue
                row = await session.get(OvernightPosition, position.id)
                if row is None or row.status != "open":
                    continue
                row.status = "closed"
                row.exit_at = decision["exit_at"]
                row.exit_price = decision["exit_price"]
                row.exit_reason = decision["reason"]
                row.pnl = decision["pnl"]
                row.pnl_pct = decision["pnl_pct"]
                audit = dict(row.audit or {})
                audit["exit"] = {
                    key: value.isoformat() if isinstance(value, datetime) else value
                    for key, value in decision.items()
                    if key != "ready"
                }
                row.audit = audit
                exited += 1
            await session.commit()

        unavailable = sum(item.get("data_status") == "unavailable" for item in decisions)
        status = "partial" if unavailable else "completed"
        await self._finish(
            run_id,
            status=status,
            message=f"早盘退出检查完成：已平仓{exited}只，继续监控{len(positions) - exited}只",
            data_date=now.date(),
            is_realtime=bool(positions) and len(realtime_payloads) == len(positions),
            scanned_count=len(positions),
            prefiltered_count=len(positions),
            candidates=decisions,
            data_quality={
                "positions": len(positions),
                "minute_covered": len(realtime_payloads),
                "force_exit": force,
                "t_plus_one": True,
                "fees": {
                    "commission_rate": STRATEGY_CONFIG["commission_rate"],
                    "slippage_rate": STRATEGY_CONFIG["slippage_rate"],
                    "stamp_tax_rate": STRATEGY_CONFIG["stamp_tax_rate"],
                },
                "missing_policy": "卖出分钟价缺失时保留仓位并告警，不伪造成交",
            },
        )

    @staticmethod
    def _position_view(row: OvernightPosition, quote: dict | None = None) -> dict[str, Any]:
        quote = quote or {}
        current_price = row.exit_price if row.status == "closed" else _number(quote.get("price"))
        shares = int(row.shares or 100)
        buy_amount = row.entry_price * shares
        if row.status == "closed":
            pnl = row.pnl
            pnl_pct = row.pnl_pct
        elif current_price is not None:
            sell_amount = current_price * shares
            fees = (
                buy_amount * STRATEGY_CONFIG["commission_rate"]
                + sell_amount * (STRATEGY_CONFIG["commission_rate"] + STRATEGY_CONFIG["stamp_tax_rate"])
            )
            pnl = sell_amount - buy_amount - fees
            pnl_pct = pnl / buy_amount * 100 if buy_amount else None
        else:
            pnl = pnl_pct = None
        return {
            "id": row.id,
            "entry_run_id": row.entry_run_id,
            "code": row.stock_code,
            "name": row.stock_name,
            "sector": row.sector or "",
            "status": row.status,
            "strategy_tag": STRATEGY_CONFIG["name"],
            "shares": shares,
            "signal_at": row.signal_at.isoformat() if row.signal_at else None,
            "entry_at": row.entry_at.isoformat() if row.entry_at else None,
            "entry_price": round(row.entry_price, 4),
            "cost_value": round(buy_amount, 2),
            "allocated_pct": round(float(row.allocated_pct or 0), 3),
            "current_price": round(current_price, 4) if current_price is not None else None,
            "market_value": round(current_price * shares, 2) if current_price is not None else None,
            "exit_at": row.exit_at.isoformat() if row.exit_at else None,
            "exit_price": round(row.exit_price, 4) if row.exit_price is not None else None,
            "exit_reason": row.exit_reason,
            "pnl": round(pnl, 2) if pnl is not None else None,
            "pnl_pct": round(pnl_pct, 3) if pnl_pct is not None else None,
            "audit": row.audit or {},
        }

    async def get_run(self, run_id: int) -> dict[str, Any]:
        async with async_session() as session:
            row = await session.get(OvernightStrategyRun, run_id)
        if row is None:
            raise LookupError("一夜持股运行记录不存在")
        return _run_view(row) or {}

    async def dashboard(self) -> dict[str, Any]:
        async with async_session() as session:
            runs = (await session.execute(
                select(OvernightStrategyRun).order_by(desc(OvernightStrategyRun.id)).limit(20)
            )).scalars().all()
            positions = (await session.execute(
                select(OvernightPosition).order_by(desc(OvernightPosition.entry_at)).limit(100)
            )).scalars().all()
            coverage = (await session.execute(select(
                func.count(StockMinuteBar.id),
                func.count(func.distinct(StockMinuteBar.stock_code)),
                func.min(StockMinuteBar.bar_time),
                func.max(StockMinuteBar.bar_time),
            ))).one()
        open_positions = [row for row in positions if row.status == "open"]
        quote_payload: dict[str, Any] = {"stocks": [], "available": False}
        if open_positions:
            try:
                quote_payload = await quote_snapshot_service.fetch(
                    [row.stock_code for row in open_positions], async_session,
                )
            except Exception:
                pass
        quotes = {str(item.get("code")): item for item in quote_payload.get("stocks") or []}
        position_views = [self._position_view(row, quotes.get(row.stock_code)) for row in positions]
        active = next((row for row in runs if row.status in {"queued", "running"}), None)
        latest_entry = next((row for row in runs if row.stage == "entry" and row.status not in {"queued", "running"}), None)
        latest_preliminary = next((row for row in runs if row.stage == "preliminary" and row.status not in {"queued", "running"}), None)
        completed = [item for item in position_views if item["status"] == "closed" and item["pnl"] is not None]
        all_priced = [item for item in position_views if item["pnl"] is not None]
        total_cost = sum(item["cost_value"] for item in all_priced)
        total_pnl = sum(item["pnl"] for item in all_priced)
        return {
            "updated_at": shanghai_now().isoformat(),
            "strategy": {
                **STRATEGY_CONFIG,
                "enabled": True,
                "execution": "研究用100股模拟成交，不连接券商",
                "selection_limit": "按可审计综合分取前5只",
            },
            "active_run": _run_view(active),
            "latest_entry_run": _run_view(latest_entry),
            "latest_preliminary_run": _run_view(latest_preliminary),
            "runs": [_run_view(row) for row in runs],
            "positions": position_views,
            "open_positions": [item for item in position_views if item["status"] == "open"],
            "closed_positions": [item for item in position_views if item["status"] == "closed"],
            "performance": {
                "positions": len(position_views),
                "open": len(open_positions),
                "closed": len(completed),
                "wins": sum((item["pnl"] or 0) > 0 for item in completed),
                "losses": sum((item["pnl"] or 0) < 0 for item in completed),
                "win_rate": round(sum((item["pnl"] or 0) > 0 for item in completed) / len(completed) * 100, 2) if completed else None,
                "cost_value": round(total_cost, 2) if all_priced else None,
                "pnl": round(total_pnl, 2) if all_priced else None,
                "pnl_pct": round(total_pnl / total_cost * 100, 3) if total_cost else None,
            },
            "quote": {
                "available": bool(quote_payload.get("available")),
                "source": quote_payload.get("source", "eastmoney"),
                "data_date": quote_payload.get("data_date"),
                "is_realtime": bool(quote_payload.get("is_realtime")),
                "cache_used": bool(quote_payload.get("cache_used")),
            },
            "minute_coverage": {
                "bar_count": int(coverage[0] or 0),
                "stock_count": int(coverage[1] or 0),
                "from": coverage[2].isoformat() if coverage[2] else None,
                "to": coverage[3].isoformat() if coverage[3] else None,
                "collection_mode": "从上线日起对实际候选和持仓前向采集",
            },
            "backtest": {
                "available": False,
                "grade": "待积累",
                "reason": "尚无全市场历史点时分钟快照，现有日线不能还原14:30筛选、尾盘5分钟或次日09:30-10:00成交",
                "requirements": [
                    "历史全市场14:30点时快照",
                    "候选股票14:45-14:55逐分钟成交",
                    "次一交易日09:30-10:00逐分钟成交",
                    "退市股票与历史成分保留以避免幸存者偏差",
                ],
            },
            "disclaimer": "斐波那契仅作为价格保护层；系统不会展示未经真实点时回测验证的胜率提升或收益承诺。",
        }

    async def robot_summary(self) -> dict[str, Any]:
        dashboard = await self.dashboard()
        latest = dashboard.get("latest_entry_run") or {}
        return {
            "tag": STRATEGY_CONFIG["name"],
            "schedule": "交易日14:30预扫，14:50复核，次日10:00前退出",
            "run": latest,
            "positions": dashboard.get("open_positions") or [],
            "recent_closed": (dashboard.get("closed_positions") or [])[:10],
            "performance": dashboard.get("performance") or {},
            "data_quality": (latest.get("data_quality") or {}) if isinstance(latest, dict) else {},
        }


overnight_strategy_service = OvernightStrategyService()
