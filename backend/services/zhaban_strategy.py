"""Auditable, research-only scanner for failed limit-up (炸板) events.

The service deliberately separates a daily-bar approximation from an
execution-ready intraday signal. A daily OHLC bar can identify a likely event,
but it cannot prove the first touch time or the post-break volume distribution.
"""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, func, select

from database import async_session
from models import MarketDataCache, MarketSentimentDaily, StockDailyBar
from quant.jobs import create_job, get_job, latest_running_job, spawn, update_job
from quant.market_cache import load_quant_market_snapshot
from quant.storage import quant_store
from services.data_collector import collector, shanghai_now


ZHABAN_CACHE_KEY = "zhaban_strategy_latest_v1"
ZHABAN_BACKTEST_CACHE_KEY = "zhaban_strategy_backtest_latest_v1"

BOARD_LABELS = {
    "main": "主板",
    "chinext": "创业板",
    "star": "科创板",
    "beijing": "北交所",
}
BOARD_IDS = {label: key for key, label in BOARD_LABELS.items()}

DEFAULT_ZHABAN_CONFIG: dict[str, Any] = {
    "id": "zhaban_resilience_research_v1",
    "name": "炸板韧性研究策略",
    "version": "1.0",
    "board_scope": "main",
    "depth_pct_max": 2.0,
    "recovery_rate_min": 0.70,
    "absorption_strength_min": 0.40,
    "close_position_min": 0.60,
    "failed_limit_rate_max_pct": 30.0,
    "prior_5d_return_max_pct": 25.0,
    "turnover_3d_avg_max_pct": 25.0,
    "limit_touch_count_10d_max": 2,
    "require_market_ma20": True,
    "require_sector_linkage": True,
    "sector_limit_touch_min": 2,
    "exclude_tail_touch": True,
    "allow_daily_approximation": True,
    "allow_unknown_market": False,
    "exclude_st": True,
    "max_candidates": 20,
    "max_positions": 5,
    "max_position_pct": 20.0,
    "holding_days": 3,
    "stop_loss_pct": 5.0,
    "take_profit_pct": 8.0,
    "take_profit_partial_pct": 50.0,
    "require_auction_confirmation": False,
    "auction_volume_ratio_min": 2.0,
    "auction_high_open_pct_min": 0.0,
    "auction_high_open_pct_max": 3.0,
    "commission_rate": 0.0003,
    "stamp_tax_rate": 0.0005,
    "slippage_rate": 0.001,
}

FACTOR_SCHEMA = [
    {
        "key": "board_scope",
        "label": "独立板块样本",
        "type": "select",
        "options": [
            {"value": "main", "label": "主板 10%"},
            {"value": "chinext", "label": "创业板 20%"},
            {"value": "star", "label": "科创板 20%"},
            {"value": "beijing", "label": "北交所 30%"},
            {"value": "all", "label": "全部（仅分组对比）"},
        ],
    },
    {"key": "depth_pct_max", "label": "炸板深度上限", "type": "number", "min": 0.1, "max": 10, "step": 0.1, "unit": "%"},
    {"key": "recovery_rate_min", "label": "收复率下限", "type": "number", "min": 0, "max": 1, "step": 0.05},
    {"key": "absorption_strength_min", "label": "吸收强度下限", "type": "number", "min": 0, "max": 1, "step": 0.05},
    {"key": "close_position_min", "label": "收盘位置下限", "type": "number", "min": 0, "max": 1, "step": 0.05},
    {"key": "failed_limit_rate_max_pct", "label": "全市场炸板率上限", "type": "number", "min": 0, "max": 100, "step": 1, "unit": "%"},
    {"key": "prior_5d_return_max_pct", "label": "前5日累计涨幅上限", "type": "number", "min": 0, "max": 100, "step": 1, "unit": "%"},
    {"key": "turnover_3d_avg_max_pct", "label": "近3日平均换手上限", "type": "number", "min": 0, "max": 100, "step": 1, "unit": "%"},
    {"key": "require_market_ma20", "label": "要求上证站上MA20", "type": "boolean"},
    {"key": "require_sector_linkage", "label": "要求板块联动", "type": "boolean"},
    {"key": "exclude_tail_touch", "label": "排除14:30后首次触板", "type": "boolean"},
    {"key": "allow_daily_approximation", "label": "允许日线近似进入研究池", "type": "boolean"},
    {"key": "require_auction_confirmation", "label": "要求历史竞价确认", "type": "boolean"},
    {"key": "holding_days", "label": "回测最长持有天数", "type": "number", "min": 1, "max": 10, "step": 1, "unit": "日"},
]


def _number(value: object) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _limit_spec(code: str) -> tuple[str, float]:
    if code.startswith(("4", "8", "92")):
        return "北交所", 30.0
    if code.startswith(("688", "689")):
        return "科创板", 20.0
    if code.startswith(("300", "301", "302")):
        return "创业板", 20.0
    return "主板", 10.0


def _limit_price(previous_close: float, limit_pct: float) -> float:
    # A-share quotes use a 0.01 yuan tick. The source bars are adjusted daily
    # bars, so this remains an event approximation unless the failed pool also
    # confirms the symbol for that trading date.
    return round(previous_close * (1 + limit_pct / 100) + 1e-10, 2)


def _time_minutes(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) < 4:
        return None
    try:
        hour, minute = int(digits[:2]), int(digits[2:4])
    except ValueError:
        return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour * 60 + minute


def _bar_dict(row: StockDailyBar) -> dict[str, Any]:
    return {
        "code": row.stock_code,
        "name": row.stock_name or row.stock_code,
        "market": row.market or "",
        "date": row.trade_date,
        "open": _number(row.open_price),
        "close": _number(row.close_price),
        "high": _number(row.high_price),
        "low": _number(row.low_price),
        "volume": _number(row.volume),
        "amount": _number(row.amount),
        "change_pct": _number(row.change_pct),
        "turnover": _number(row.turnover),
        "source": row.source or "database_cache",
    }


def _condition(
    key: str,
    label: str,
    status: str,
    actual: Any,
    expected: str,
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


class ZhabanStrategyService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @staticmethod
    def config() -> dict[str, Any]:
        return {
            "strategy": deepcopy(DEFAULT_ZHABAN_CONFIG),
            "factors": deepcopy(FACTOR_SCHEMA),
            "boards": [
                {"id": "all", "label": "全部（仅分组对比）", "limit_pct": None},
                {"id": "main", "label": "主板", "limit_pct": 10},
                {"id": "chinext", "label": "创业板", "limit_pct": 20},
                {"id": "star", "label": "科创板", "limit_pct": 20},
                {"id": "beijing", "label": "北交所", "limit_pct": 30},
            ],
            "status": "research_only",
            "execution_boundary": "日线结果只进入研究候选池；次日竞价和盘中字段完整核验前不产生自动交易指令。",
        }

    @staticmethod
    def _normalize_config(payload: dict[str, Any] | None) -> dict[str, Any]:
        config = {**DEFAULT_ZHABAN_CONFIG}
        for key in config:
            if payload and key in payload and key not in {"id", "version"}:
                config[key] = payload[key]

        limits = {
            "depth_pct_max": (0.1, 10.0),
            "recovery_rate_min": (0.0, 1.0),
            "absorption_strength_min": (0.0, 1.0),
            "close_position_min": (0.0, 1.0),
            "failed_limit_rate_max_pct": (0.0, 100.0),
            "prior_5d_return_max_pct": (0.0, 200.0),
            "turnover_3d_avg_max_pct": (0.0, 100.0),
            "limit_touch_count_10d_max": (0.0, 10.0),
            "sector_limit_touch_min": (1.0, 20.0),
            "max_candidates": (1.0, 100.0),
            "max_positions": (1.0, 20.0),
            "max_position_pct": (1.0, 100.0),
            "holding_days": (1.0, 10.0),
            "stop_loss_pct": (0.1, 30.0),
            "take_profit_pct": (0.1, 100.0),
            "take_profit_partial_pct": (1.0, 99.0),
            "auction_volume_ratio_min": (0.0, 20.0),
            "auction_high_open_pct_min": (-10.0, 20.0),
            "auction_high_open_pct_max": (-10.0, 30.0),
            "commission_rate": (0.0, 0.01),
            "stamp_tax_rate": (0.0, 0.01),
            "slippage_rate": (0.0, 0.02),
        }
        integer_keys = {"limit_touch_count_10d_max", "sector_limit_touch_min", "max_candidates", "max_positions", "holding_days"}
        for key, (minimum, maximum) in limits.items():
            value = _number(config.get(key))
            if value is None or not minimum <= value <= maximum:
                raise ValueError(f"{key} 必须在 {minimum:g} 到 {maximum:g} 之间")
            config[key] = int(value) if key in integer_keys else round(value, 6)
        for key in (
            "require_market_ma20", "require_sector_linkage", "exclude_tail_touch",
            "allow_daily_approximation", "allow_unknown_market", "exclude_st",
            "require_auction_confirmation",
        ):
            config[key] = bool(config.get(key))
        if config["auction_high_open_pct_max"] < config["auction_high_open_pct_min"]:
            raise ValueError("竞价高开幅度上限不能小于下限")
        board_scope = str(config.get("board_scope") or "").strip().lower()
        if board_scope not in {*BOARD_LABELS, "all"}:
            raise ValueError("board_scope 必须是 main、chinext、star、beijing 或 all")
        config["board_scope"] = board_scope
        name = str(config.get("name") or "").strip()
        if not name or len(name) > 80:
            raise ValueError("策略名称必须为1到80个字符")
        config["name"] = name
        return config

    @staticmethod
    def _board_allowed(board: str, config: dict[str, Any]) -> bool:
        scope = str(config.get("board_scope") or "main")
        return scope == "all" or BOARD_IDS.get(board) == scope

    @staticmethod
    def _event_from_history(
        code: str,
        history: list[dict[str, Any]],
        target_date: date,
        *,
        sector: str = "",
        failed_pool_row: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        bars = [item for item in history if item["date"] <= target_date]
        if not bars or bars[-1]["date"] != target_date or len(bars) < 2:
            return None
        current, previous = bars[-1], bars[-2]
        previous_close = _number(previous.get("close"))
        close = _number(current.get("close"))
        high = _number(current.get("high"))
        low = _number(current.get("low"))
        if None in (previous_close, close, high, low) or previous_close <= 0 or close <= 0:
            return None

        board, limit_pct = _limit_spec(code)
        limit_up = _limit_price(previous_close, limit_pct)
        tick_tolerance = 0.0051
        touched = high >= limit_up - tick_tolerance
        closed_at_limit = close >= limit_up - tick_tolerance
        near_limit = high >= limit_up * 0.998
        pool_confirmed = bool(failed_pool_row)
        if pool_confirmed or (touched and not closed_at_limit):
            event_type = "true_zhaban"
        elif touched and closed_at_limit:
            event_type = "resealed"
        elif near_limit:
            event_type = "near_miss"
        else:
            event_type = "none"

        price_range = high - low
        close_position = (close - low) / price_range if price_range > 0 else None
        recovery_denominator = limit_up - low
        # The source document's displayed equation is inverted relative to its
        # own 0..1 example. This implementation uses the intended recovered
        # portion: (close - post-break low) / (limit price - post-break low).
        recovery_rate = (
            _clamp((close - low) / recovery_denominator)
            if recovery_denominator > 0 else None
        )
        depth_pct = max(0.0, (limit_up - close) / limit_up * 100)

        prior_five = bars[-6] if len(bars) >= 6 else None
        prior_five_close = _number(prior_five.get("close")) if prior_five else None
        prior_5d_return = (
            (close / prior_five_close - 1) * 100
            if prior_five_close not in (None, 0) else None
        )
        turnover_values = [
            value for item in bars[-3:]
            if (value := _number(item.get("turnover"))) is not None
        ]
        turnover_3d = _average(turnover_values) if len(turnover_values) == min(3, len(bars)) else None
        prior_volumes = [
            value for item in bars[-6:-1]
            if (value := _number(item.get("volume"))) is not None and value > 0
        ]
        current_volume = _number(current.get("volume"))
        average_volume = _average(prior_volumes)
        daily_volume_ratio = (
            current_volume / average_volume
            if current_volume is not None and average_volume not in (None, 0) else None
        )
        absorption_proxy = (
            recovery_rate * min(daily_volume_ratio / 2, 1)
            if recovery_rate is not None and daily_volume_ratio is not None else None
        )

        prior_touches = 0
        lookback_start = max(1, len(bars) - 11)
        for index in range(lookback_start, len(bars) - 1):
            candidate = bars[index]
            candidate_previous = bars[index - 1]
            candidate_previous_close = _number(candidate_previous.get("close"))
            candidate_high = _number(candidate.get("high"))
            if candidate_previous_close in (None, 0) or candidate_high is None:
                continue
            _, candidate_limit_pct = _limit_spec(code)
            if candidate_high >= _limit_price(candidate_previous_close, candidate_limit_pct) - tick_tolerance:
                prior_touches += 1

        first_touch = (failed_pool_row or {}).get("first_limit_time")
        first_touch_minutes = _time_minutes(first_touch)
        tail_touch = first_touch_minutes >= 14 * 60 + 30 if first_touch_minutes is not None else None
        pool_sector = str((failed_pool_row or {}).get("sector") or "").strip()
        name = str((failed_pool_row or {}).get("name") or current.get("name") or code)
        return {
            "code": code,
            "name": name,
            "sector": pool_sector or sector or "未分类",
            "board": board,
            "limit_pct": limit_pct,
            "event_type": event_type,
            "event_source": "eastmoney_failed_pool+daily_bars" if pool_confirmed else "daily_bar_approximation",
            "pool_confirmed": pool_confirmed,
            "trade_date": target_date.isoformat(),
            "previous_close": round(previous_close, 4),
            "open": current.get("open"),
            "high": round(high, 4),
            "low": round(low, 4),
            "close": round(close, 4),
            "limit_up_price": limit_up,
            "depth_pct": round(depth_pct, 4),
            "recovery_rate": round(recovery_rate, 4) if recovery_rate is not None else None,
            "close_position_ratio": round(close_position, 4) if close_position is not None else None,
            "post_break_volume_ratio": None,
            "daily_volume_ratio_proxy": round(daily_volume_ratio, 4) if daily_volume_ratio is not None else None,
            "absorption_strength": round(absorption_proxy, 4) if absorption_proxy is not None else None,
            "absorption_source": "日线量能代理，非炸板后成交占比",
            "prior_5d_return_pct": round(prior_5d_return, 4) if prior_5d_return is not None else None,
            "turnover_3d_avg_pct": round(turnover_3d, 4) if turnover_3d is not None else None,
            "limit_touch_count_10d": prior_touches,
            "first_touch_time": first_touch,
            "tail_touch": tail_touch,
            "failed_attempts": (failed_pool_row or {}).get("failed_attempts"),
            "source": current.get("source") or "database_cache",
            "intraday_verified": False,
        }

    async def _load_scan_bars(
        self, requested_date: date | None, *, job_id: str | None = None,
    ) -> tuple[date | None, dict[str, list[dict[str, Any]]], list[str]]:
        warnings: list[str] = []
        async with async_session() as session:
            latest = (await session.execute(select(func.max(StockDailyBar.trade_date)))).scalar_one_or_none()
            if latest is None:
                return None, {}, ["历史日线缓存为空，请先完成一年行情回补。"]
            if requested_date is None:
                effective = latest
            else:
                effective = (await session.execute(
                    select(func.max(StockDailyBar.trade_date)).where(StockDailyBar.trade_date <= requested_date)
                )).scalar_one_or_none()
                if effective is None:
                    return None, {}, ["所选日期之前没有可用日线。"]
                if effective != requested_date:
                    warnings.append(f"{requested_date.isoformat()}不是缓存交易日，已使用{effective.isoformat()}。")
            if job_id:
                update_job("zhaban", job_id, phase="daily_cache", progress=15, message=f"已定位数据日{effective.isoformat()}，正在锁定股票池")
            target_rows = list((await session.execute(
                select(StockDailyBar.stock_code)
                .where(StockDailyBar.trade_date == effective)
                .order_by(StockDailyBar.stock_code)
            )).scalars().all())
            codes = [str(code) for code in target_rows]
            if not codes:
                return effective, {}, [*warnings, "所选交易日没有股票日线记录。"]
            # The event rules need at most the previous 11 trading bars. A
            # calendar-day range loads several times more rows around long
            # weekends and made a scan appear frozen at 12% on the hosted DB.
            history_dates = list((await session.execute(
                select(StockDailyBar.trade_date)
                .where(StockDailyBar.trade_date <= effective)
                .distinct()
                .order_by(desc(StockDailyBar.trade_date))
                .limit(15)
            )).scalars().all())
            history_dates.sort()
            if job_id:
                update_job("zhaban", job_id, phase="daily_cache", progress=20, message=f"正在读取{len(history_dates)}个交易日的必要日线")
            rows = list((await session.execute(
                select(StockDailyBar)
                .where(
                    StockDailyBar.stock_code.in_(codes),
                    StockDailyBar.trade_date.in_(history_dates),
                )
                .order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
            )).scalars().all())
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row.stock_code].append(_bar_dict(row))
        if job_id:
            update_job("zhaban", job_id, phase="daily_cache", progress=28, message=f"已读取{len(rows):,}条日线，开始识别事件")
        return effective, dict(grouped), warnings

    @staticmethod
    async def _sector_map() -> tuple[dict[str, str], bool]:
        try:
            snapshot = await load_quant_market_snapshot()
        except Exception:
            snapshot = {}
        if not snapshot.get("stocks"):
            return {}, False
        return {
            str(item.get("code")): str(item.get("sector") or "").strip()
            for item in snapshot.get("stocks") or [] if item.get("code")
        }, True

    @staticmethod
    async def _pool_events(target_date: date) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        up_result, failed_result = await asyncio.gather(
            collector.fetch_limit_up_pool(page_size=500, target_date=target_date),
            collector.fetch_failed_limit_pool(page_size=500, target_date=target_date),
            return_exceptions=True,
        )
        up = {} if isinstance(up_result, Exception) else dict(up_result or {})
        failed = {} if isinstance(failed_result, Exception) else dict(failed_result or {})
        requested = target_date.isoformat()
        up_exact = str(up.get("trade_date") or "") == requested
        failed_exact = str(failed.get("trade_date") or "") == requested
        rows = {
            str(item.get("code")): item
            for item in (failed.get("stocks") or []) if failed_exact and item.get("code")
        }
        return rows, {
            "available": bool(up_exact or failed_exact),
            "up_count": int(up.get("total") or 0) if up_exact else None,
            "failed_count": int(failed.get("total") or 0) if failed_exact else None,
            "trade_date": requested if up_exact or failed_exact else None,
            "returned_dates": sorted({
                str(value) for value in (up.get("trade_date"), failed.get("trade_date")) if value
            }),
            "source": "eastmoney_limit_pools",
        }

    async def _market_audit(
        self,
        target_date: date,
        events: list[dict[str, Any]],
        pool_meta: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        async with async_session() as session:
            sentiment = await session.get(MarketSentimentDaily, target_date)
        derived_failed = sum(item.get("event_type") == "true_zhaban" for item in events)
        derived_resealed = sum(item.get("event_type") == "resealed" for item in events)
        derived_touched = derived_failed + derived_resealed
        failed_count = pool_meta.get("failed_count")
        up_count = pool_meta.get("up_count")
        failed_rate = None
        source = "daily_bar_approximation"
        if sentiment and sentiment.failed_limit_rate is not None:
            failed_rate = float(sentiment.failed_limit_rate)
            failed_count = sentiment.failed_limit_count
            up_count = sentiment.limit_up_count
            source = "market_sentiment_daily"
        elif failed_count is not None and up_count is not None and failed_count + up_count > 0:
            failed_rate = failed_count / (failed_count + up_count) * 100
            source = "eastmoney_limit_pools"
        elif derived_touched:
            failed_count, up_count = derived_failed, derived_resealed
            failed_rate = derived_failed / derived_touched * 100

        above_ma20 = None
        index_value = None
        ma20 = None
        index_source = "unavailable"
        try:
            history = await collector.fetch_shanghai_index_history(days=80)
            dated = [
                item for item in history
                if str(item.get("date") or "") <= target_date.isoformat()
                and _number(item.get("close")) is not None
            ]
            exact = dated and str(dated[-1].get("date")) == target_date.isoformat()
            if exact and len(dated) >= 20:
                closes = [float(item["close"]) for item in dated]
                index_value = closes[-1]
                ma20 = _average(closes[-20:])
                above_ma20 = bool(ma20 is not None and index_value > ma20)
                index_source = "tencent_index_daily"
        except Exception:
            pass

        rate_ok = failed_rate is not None and failed_rate <= float(config["failed_limit_rate_max_pct"])
        market_ok = True if not config["require_market_ma20"] else above_ma20
        known_failure = failed_rate is not None and not rate_ok
        if config["require_market_ma20"] and above_ma20 is False:
            known_failure = True
        missing = failed_rate is None or (config["require_market_ma20"] and above_ma20 is None)
        status = "failed" if known_failure else "unavailable" if missing else "passed"
        conditions = [
            _condition(
                "failed_limit_rate", "全市场炸板率",
                "passed" if rate_ok else "failed" if failed_rate is not None else "unavailable",
                round(failed_rate, 2) if failed_rate is not None else None,
                f"<={config['failed_limit_rate_max_pct']:g}%", source,
            ),
            _condition(
                "market_ma20", "上证MA20环境",
                "passed" if not config["require_market_ma20"] or above_ma20 else "failed" if above_ma20 is False else "unavailable",
                round(index_value, 2) if index_value is not None else None,
                "上证收盘高于MA20" if config["require_market_ma20"] else "未启用",
                index_source,
                f"MA20={ma20:.2f}" if ma20 is not None else "历史指数日期或覆盖不足",
            ),
        ]
        return {
            "status": status,
            "failed_limit_rate_pct": round(failed_rate, 2) if failed_rate is not None else None,
            "failed_limit_count": failed_count,
            "closed_limit_count": up_count,
            "touched_limit_count": (
                int(failed_count or 0) + int(up_count or 0)
                if failed_count is not None and up_count is not None else derived_touched
            ),
            "shanghai_close": round(index_value, 2) if index_value is not None else None,
            "shanghai_ma20": round(ma20, 2) if ma20 is not None else None,
            "above_ma20": above_ma20,
            "conditions": conditions,
            "source": source,
        }

    @staticmethod
    def _score(event: dict[str, Any], config: dict[str, Any], sector_linked: bool) -> float:
        depth = _number(event.get("depth_pct"))
        recovery = _number(event.get("recovery_rate"))
        absorption = _number(event.get("absorption_strength"))
        close_position = _number(event.get("close_position_ratio"))
        prior_return = _number(event.get("prior_5d_return_pct"))
        score = 0.0
        if depth is not None:
            score += 25 * _clamp(1 - depth / max(float(config["depth_pct_max"]), 0.01))
        if recovery is not None:
            score += 25 * _clamp(recovery)
        if absorption is not None:
            score += 20 * _clamp(absorption)
        if close_position is not None:
            score += 15 * _clamp(close_position)
        if prior_return is not None:
            score += 10 * _clamp(1 - max(prior_return, 0) / max(float(config["prior_5d_return_max_pct"]), 0.01))
        if sector_linked:
            score += 5
        return round(score, 2)

    @classmethod
    def _evaluate(
        cls,
        event: dict[str, Any],
        config: dict[str, Any],
        market: dict[str, Any],
        sector_touch_count: int,
    ) -> dict[str, Any]:
        depth = _number(event.get("depth_pct"))
        recovery = _number(event.get("recovery_rate"))
        absorption = _number(event.get("absorption_strength"))
        close_position = _number(event.get("close_position_ratio"))
        prior_return = _number(event.get("prior_5d_return_pct"))
        turnover = _number(event.get("turnover_3d_avg_pct"))
        touch_count = int(event.get("limit_touch_count_10d") or 0)
        sector_linked = sector_touch_count >= int(config["sector_limit_touch_min"])
        tail_touch = event.get("tail_touch")

        def bounded(key: str, label: str, value: float | None, passed: bool, expected: str, source: str) -> dict[str, Any]:
            return _condition(
                key, label,
                "passed" if value is not None and passed else "failed" if value is not None else "unavailable",
                round(value, 4) if value is not None else None,
                expected, source,
            )

        conditions = [
            bounded("depth", "炸板深度", depth, depth is not None and depth <= config["depth_pct_max"], f"<={config['depth_pct_max']:g}%", event["event_source"]),
            bounded("recovery", "收复率", recovery, recovery is not None and recovery >= config["recovery_rate_min"], f">={config['recovery_rate_min']:g}", "日线最低价近似"),
            bounded("absorption", "吸收强度", absorption, absorption is not None and absorption >= config["absorption_strength_min"], f">={config['absorption_strength_min']:g}", event["absorption_source"]),
            bounded("close_position", "收盘位置比率", close_position, close_position is not None and close_position >= config["close_position_min"], f">={config['close_position_min']:g}", "缓存日线OHLC"),
            bounded("prior_5d_return", "前5日累计涨幅", prior_return, prior_return is not None and prior_return <= config["prior_5d_return_max_pct"], f"<={config['prior_5d_return_max_pct']:g}%", "缓存日线"),
            bounded("turnover_3d", "近3日平均换手", turnover, turnover is not None and turnover <= config["turnover_3d_avg_max_pct"], f"<={config['turnover_3d_avg_max_pct']:g}%", "缓存日线"),
            _condition(
                "limit_touch_count_10d", "近10日触板次数",
                "passed" if touch_count <= config["limit_touch_count_10d_max"] else "failed",
                touch_count, f"<={config['limit_touch_count_10d_max']}", "缓存日线",
            ),
            _condition(
                "sector_linkage", "板块联动",
                "passed" if not config["require_sector_linkage"] or sector_linked else "failed",
                sector_touch_count, f">={config['sector_limit_touch_min']}只触板" if config["require_sector_linkage"] else "未启用",
                "同日事件池+当前行业分类",
            ),
            _condition(
                "tail_touch", "尾盘炸板排除",
                "passed" if not config["exclude_tail_touch"] or tail_touch is False else "failed" if tail_touch is True else "unavailable",
                event.get("first_touch_time"), "首次触板早于14:30" if config["exclude_tail_touch"] else "未启用",
                "东方财富炸板池首次触板时间" if event.get("pool_confirmed") else "盘中分钟数据缺失",
            ),
            *market.get("conditions", []),
        ]
        failed = [item["label"] for item in conditions if item["status"] == "failed"]
        unavailable = [item["label"] for item in conditions if item["status"] == "unavailable"]
        approximation_ok = bool(config["allow_daily_approximation"])
        stock_passed = not failed and (approximation_ok or not unavailable)
        market_passed = market.get("status") == "passed" or (
            market.get("status") == "unavailable" and config["allow_unknown_market"]
        )
        qualified = bool(stock_passed and market_passed)
        execution_ready = bool(qualified and event.get("intraday_verified") and tail_touch is False)
        return {
            **event,
            "score": cls._score(event, config, sector_linked),
            "conditions": conditions,
            "failed_reasons": failed,
            "unavailable_reasons": unavailable,
            "qualified": qualified,
            "execution_ready": execution_ready,
            "research_only": True,
            "qualification_label": "研究候选" if qualified else "观察事件",
            "basis": (
                f"炸板深度{event.get('depth_pct')}%，收复率{event.get('recovery_rate')}，"
                f"收盘位置{event.get('close_position_ratio')}；吸收强度为日线代理值。"
            ),
        }

    async def scan(
        self,
        *,
        target_date: date | None = None,
        config_payload: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        config = self._normalize_config(config_payload)
        if target_date and target_date > shanghai_now().date():
            raise ValueError("研究日期不能晚于今天")
        if job_id:
            update_job("zhaban", job_id, phase="daily_cache", progress=12, message="正在读取事件日及必要历史日线")
        effective, grouped, warnings = await self._load_scan_bars(target_date, job_id=job_id)
        if effective is None or not grouped:
            result = {
                "generated_at": shanghai_now().isoformat(), "data_date": None,
                "status": "unavailable", "strategy": config, "candidates": [],
                "events": [], "warnings": warnings,
                "data_quality": {"audit_eligible": False, "mode": "unavailable"},
            }
            await self._save_result(ZHABAN_CACHE_KEY, result)
            return result

        if job_id:
            update_job("zhaban", job_id, phase="event_pool", progress=34, message="正在核验涨停与炸板事件池")
        (failed_rows, pool_meta), (sector_map, sector_available) = await asyncio.gather(
            self._pool_events(effective), self._sector_map(),
        )
        if job_id:
            update_job("zhaban", job_id, phase="event_pool", progress=48, message="事件池已返回，正在生成板块与个股事件")
        events = []
        for code, history in grouped.items():
            event = self._event_from_history(
                code, history, effective,
                sector=sector_map.get(code, ""),
                failed_pool_row=failed_rows.get(code),
            )
            if event and event["event_type"] != "none":
                if config["exclude_st"] and "ST" in event["name"].upper():
                    continue
                if not self._board_allowed(event["board"], config):
                    continue
                events.append(event)

        if job_id:
            update_job("zhaban", job_id, phase="market_filter", progress=58, message="正在核验炸板率、上证MA20与板块联动")
        market = await self._market_audit(effective, events, pool_meta, config)
        if job_id:
            update_job("zhaban", job_id, phase="candidate_scoring", progress=78, message=f"已识别{len(events)}个事件，正在应用筛选因子")
        sector_counts: dict[str, int] = defaultdict(int)
        for event in events:
            if event["event_type"] in {"true_zhaban", "resealed"}:
                sector_counts[event["sector"]] += 1

        candidates = [
            self._evaluate(event, config, market, sector_counts[event["sector"]])
            for event in events if event["event_type"] == "true_zhaban"
        ]
        candidates.sort(key=lambda item: (item["qualified"], item["score"], item["code"]), reverse=True)
        candidates = candidates[: int(config["max_candidates"])]
        if not pool_meta.get("available"):
            warnings.append("涨停/炸板事件池不可用，事件识别仅来自复权日线近似。")
        if not sector_available:
            warnings.append("历史日期缺少点时行业分类，板块联动使用未分类或当前缓存，存在分类偏差。")
        if any(item.get("tail_touch") is None for item in candidates):
            warnings.append("部分事件缺少首次触板时间，无法严格排除14:30后尾盘炸板。")
        if config["board_scope"] == "all":
            warnings.append("当前选择全部板块，仅用于分组观察；10%/20%/30%样本不能合并宣称同一模型有效。")
        warnings.append("炸板后成交占比没有历史逐笔/分钟数据；吸收强度使用日线量能代理，不可据此直接下单。")

        summary = {
            "true_zhaban": sum(item["event_type"] == "true_zhaban" for item in events),
            "resealed": sum(item["event_type"] == "resealed" for item in events),
            "near_miss": sum(item["event_type"] == "near_miss" for item in events),
            "qualified": sum(item["qualified"] for item in candidates),
            "execution_ready": sum(item["execution_ready"] for item in candidates),
        }
        result = {
            "generated_at": shanghai_now().isoformat(),
            "data_date": effective.isoformat(),
            "status": "research_only",
            "source": "stock_daily_bars+eastmoney_limit_pools",
            "is_realtime": False,
            "cache_used": True,
            "strategy": config,
            "market_environment": market,
            "summary": summary,
            "candidates": candidates,
            "events": sorted(events, key=lambda item: (item["event_type"], item["code"]))[:300],
            "warnings": list(dict.fromkeys(warnings)),
            "data_quality": {
                "audit_eligible": False,
                "mode": "daily_approximation",
                "board_scope": config["board_scope"],
                "board_label": BOARD_LABELS.get(config["board_scope"], "全部（分组对比）"),
                "universe_count": len(grouped),
                "pool_confirmed_count": sum(item.get("pool_confirmed") for item in events),
                "intraday_verified_count": 0,
                "missing_fields": ["炸板后最低价", "炸板后成交量", "历史竞价量比"],
                "missing_policy": "缺失盘中字段明确标记，不使用日线结果生成自动交易指令。",
                "formula_note": "收复率按(收盘-炸板后低点)/(涨停价-炸板后低点)定义；日线模式以全日最低价近似。",
            },
            "disclaimer": "本模块为未经样本外验证的事件研究，不构成投资建议或收益承诺。",
        }
        await self._save_result(ZHABAN_CACHE_KEY, result)
        return result

    @staticmethod
    def _drawdown_pct(values: list[float]) -> float:
        peak = values[0] if values else 0.0
        maximum = 0.0
        for value in values:
            peak = max(peak, value)
            if peak > 0:
                maximum = max(maximum, (peak - value) / peak * 100)
        return round(maximum, 2)

    @staticmethod
    def _reprice_plan(
        plan: dict[str, Any],
        *,
        commission_rate: float,
        stamp_tax_rate: float,
        slippage_rate: float,
    ) -> dict[str, Any]:
        shares = int(plan.get("shares") or 0)
        entry_raw = float(plan["entry_raw"])
        entry_price = entry_raw * (1 + slippage_rate)
        buy_amount = shares * entry_price
        raw_legs = plan.get("exit_legs") or [{
            "date": plan["exit_date"],
            "raw_price": plan["exit_raw"],
            "ratio": 1.0,
            "reason": plan["reason"],
        }]
        remaining_shares = shares
        exit_legs: list[dict[str, Any]] = []
        sell_amount = 0.0
        weighted_exit_raw = 0.0
        weighted_exit_price = 0.0
        for index, leg in enumerate(raw_legs):
            if index == len(raw_legs) - 1:
                leg_shares = remaining_shares
            else:
                ratio = _clamp(float(leg.get("ratio") or 0))
                leg_shares = min(remaining_shares, int(shares * ratio / 100) * 100)
            if leg_shares <= 0:
                continue
            raw_price = float(leg["raw_price"])
            execution_price = raw_price * (1 - slippage_rate)
            leg_amount = leg_shares * execution_price
            sell_amount += leg_amount
            weighted_exit_raw += raw_price * leg_shares
            weighted_exit_price += execution_price * leg_shares
            remaining_shares -= leg_shares
            exit_legs.append({
                "date": leg.get("date") or plan["exit_date"],
                "shares": leg_shares,
                "ratio": round(leg_shares / shares, 4) if shares else 0,
                "raw_price": round(raw_price, 4),
                "execution_price": round(execution_price, 4),
                "reason": leg.get("reason") or plan["reason"],
            })
        exit_raw = weighted_exit_raw / shares if shares else float(plan["exit_raw"])
        exit_price = weighted_exit_price / shares if shares else exit_raw * (1 - slippage_rate)
        commission_buy = buy_amount * commission_rate
        commission_sell = sell_amount * commission_rate
        stamp_tax = sell_amount * stamp_tax_rate
        total_cost = buy_amount + commission_buy
        net_proceeds = sell_amount - commission_sell - stamp_tax
        pnl = net_proceeds - total_cost
        return {
            "code": plan["code"],
            "name": plan["name"],
            "signal_date": plan["signal_date"],
            "entry_date": plan["entry_date"],
            "exit_date": plan["exit_date"],
            "entry_raw": round(entry_raw, 4),
            "exit_raw": round(exit_raw, 4),
            "entry_price": round(entry_price, 4),
            "exit_price": round(exit_price, 4),
            "shares": shares,
            "buy_amount": round(buy_amount, 2),
            "sell_amount": round(sell_amount, 2),
            "commission_buy": round(commission_buy, 2),
            "commission_sell": round(commission_sell, 2),
            "stamp_tax": round(stamp_tax, 2),
            "total_cost": round(total_cost, 2),
            "net_proceeds": round(net_proceeds, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / total_cost * 100, 4) if total_cost else None,
            "reason": plan["reason"],
            "exit_legs": exit_legs,
            "score": plan.get("score"),
            "board": plan.get("board"),
            "event_source": plan.get("event_source"),
            "approximation": True,
        }

    @staticmethod
    def _period_metrics(
        trades: list[dict[str, Any]],
        daily_values: list[dict[str, Any]],
        initial_value: float,
    ) -> dict[str, Any]:
        returns = [float(item["pnl"]) for item in trades if item.get("pnl") is not None]
        total_pnl = sum(returns)
        end_value = float(daily_values[-1]["value"]) if daily_values else initial_value
        total_return = (end_value / initial_value - 1) * 100 if initial_value else 0.0
        winners = [value for value in returns if value > 0]
        losers = [value for value in returns if value < 0]
        loss_streak = 0
        max_loss_streak = 0
        for value in returns:
            if value < 0:
                loss_streak += 1
                max_loss_streak = max(max_loss_streak, loss_streak)
            else:
                loss_streak = 0
        equity_values = [initial_value, *[float(item["value"]) for item in daily_values]]
        average_win = sum(winners) / len(winners) if winners else None
        average_loss = sum(losers) / len(losers) if losers else None
        annualized = (
            ((end_value / initial_value) ** (252 / len(daily_values)) - 1) * 100
            if initial_value > 0 and end_value > 0 and daily_values else 0.0
        )
        return {
            "trade_count": len(returns),
            "completed_trade_count": len(returns),
            "total_pnl": round(total_pnl, 2),
            "total_return_pct": round(total_return, 2),
            "annualized_return_pct": round(annualized, 2),
            "win_rate_pct": round(len(winners) / len(returns) * 100, 2) if returns else None,
            "profit_loss_ratio": round(average_win / abs(average_loss), 3) if average_win is not None and average_loss not in (None, 0) else None,
            "average_trade_pnl": round(total_pnl / len(returns), 2) if returns else None,
            "average_win_pct": round(sum(item.get("pnl_pct", 0) for item in trades if (item.get("pnl") or 0) > 0) / len(winners), 3) if winners else None,
            "average_loss_pct": round(sum(item.get("pnl_pct", 0) for item in trades if (item.get("pnl") or 0) < 0) / len(losers), 3) if losers else None,
            "max_consecutive_losses": max_loss_streak,
            "max_drawdown_pct": ZhabanStrategyService._drawdown_pct(equity_values),
            "from": daily_values[0]["date"] if daily_values else None,
            "to": daily_values[-1]["date"] if daily_values else None,
        }

    @classmethod
    def _group_periods(
        cls,
        daily_values: list[dict[str, Any]],
        trades: list[dict[str, Any]],
        initial_capital: float,
        *,
        granularity: str,
    ) -> list[dict[str, Any]]:
        grouped_values: dict[str, list[dict[str, Any]]] = defaultdict(list)
        grouped_trades: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in daily_values:
            key = str(item["date"])[:4] if granularity == "year" else str(item["date"])[:7]
            grouped_values[key].append(item)
        for item in trades:
            raw_date = str(item.get("exit_date") or item.get("entry_date") or "")
            key = raw_date[:4] if granularity == "year" else raw_date[:7]
            if key:
                grouped_trades[key].append(item)

        output: list[dict[str, Any]] = []
        previous_value = initial_capital
        for key in sorted(grouped_values):
            values = grouped_values[key]
            metrics = cls._period_metrics(grouped_trades.get(key, []), values, previous_value)
            metrics.update({"period": key, "start_value": round(previous_value, 2), "end_value": round(values[-1]["value"], 2)})
            output.append(metrics)
            previous_value = float(values[-1]["value"])
        return output

    @staticmethod
    def _board_performance(
        trades: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        output = []
        for board in BOARD_LABELS.values():
            board_trades = [item for item in trades if item.get("board") == board]
            candidate_count = sum(item.get("board") == board for item in candidates)
            if not board_trades and not candidate_count:
                continue
            pnl_values = [float(item.get("pnl") or 0) for item in board_trades]
            winners = [value for value in pnl_values if value > 0]
            losers = [value for value in pnl_values if value < 0]
            average_win = _average(winners)
            average_loss = _average(losers)
            output.append({
                "key": BOARD_IDS[board],
                "label": board,
                "candidate_count": candidate_count,
                "trade_count": len(board_trades),
                "total_pnl": round(sum(pnl_values), 2),
                "win_rate_pct": round(len(winners) / len(board_trades) * 100, 2) if board_trades else None,
                "profit_loss_ratio": round(average_win / abs(average_loss), 3) if average_win is not None and average_loss not in (None, 0) else None,
            })
        return output

    @staticmethod
    def _validation_report(
        summary: dict[str, Any],
        annual: list[dict[str, Any]],
        cost_sensitivity: list[dict[str, Any]],
    ) -> dict[str, Any]:
        active_years = [item for item in annual if int(item.get("trade_count") or 0) > 0]
        yearly_status = "unavailable" if len(active_years) < 2 else (
            "passed" if all(float(item.get("total_return_pct") or 0) > 0 for item in active_years) else "failed"
        )
        oos = summary.get("out_of_sample") or {}
        oos_trades = int(oos.get("trade_count") or 0)
        oos_status = "unavailable" if oos_trades < 10 else (
            "passed" if float(oos.get("total_return_pct") or 0) > 0 else "failed"
        )
        harsh_cost = next(
            (item for item in cost_sensitivity if item.get("key") == "cost_and_slippage_x2"),
            None,
        )
        cost_status = "unavailable" if not harsh_cost or not harsh_cost.get("available") else (
            "passed" if float(harsh_cost.get("total_return_pct") or 0) > 0 else "failed"
        )
        checks = [
            {
                "key": "factor_monotonicity",
                "label": "分组收益单调递增",
                "status": "unavailable",
                "detail": "当前未运行因子分位数组合实验，不能宣称单调性。",
            },
            {
                "key": "year_consistency",
                "label": "不同年份均有效",
                "status": yearly_status,
                "detail": f"有交易年份{len(active_years)}个；至少需要2个年份才能判断。",
            },
            {
                "key": "parameter_plateau",
                "label": "参数附近稳定平台",
                "status": "unavailable",
                "detail": "当前只回测单组参数，需继续运行邻域参数网格。",
            },
            {
                "key": "transaction_cost",
                "label": "加倍滑点与提高费用后仍有效",
                "status": cost_status,
                "detail": "采用佣金和税费+50%、滑点x2的压力情景。",
            },
            {
                "key": "out_of_sample",
                "label": "样本外没有断崖失效",
                "status": oos_status,
                "detail": f"样本外完成{oos_trades}笔；少于10笔不作判断。",
            },
            {
                "key": "risk_report",
                "label": "收益与风险指标完整",
                "status": "passed",
                "detail": "已输出年度、月度、回撤、胜率、盈亏比、成本敏感性和连续亏损。",
            },
        ]
        statuses = {item["status"] for item in checks}
        overall = "failed" if "failed" in statuses else "passed" if statuses == {"passed"} else "unverified"
        return {
            "overall_status": overall,
            "checks": checks,
            "note": "只有六项全部通过后，策略才可进入下一阶段模拟验证；当前结果不代表实盘有效。",
        }

    @classmethod
    def _cost_sensitivity(
        cls,
        plans: list[dict[str, Any]],
        initial_capital: float,
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        scenarios = [
            ("base", "基准成本", 1.0, 1.0),
            ("cost_plus_50pct", "佣金与税费+50%", 1.5, 1.0),
            ("slippage_x2", "滑点×2", 1.0, 2.0),
            ("cost_and_slippage_x2", "成本+50%且滑点×2", 1.5, 2.0),
        ]
        result = []
        for key, label, cost_multiplier, slippage_multiplier in scenarios:
            values = []
            equity = float(initial_capital)
            for plan in plans:
                repriced = cls._reprice_plan(
                    plan,
                    commission_rate=float(config["commission_rate"]) * cost_multiplier,
                    stamp_tax_rate=float(config["stamp_tax_rate"]) * cost_multiplier,
                    slippage_rate=float(config["slippage_rate"]) * slippage_multiplier,
                )
                equity += float(repriced["pnl"])
                values.append(equity)
            pnl = equity - initial_capital
            result.append({
                "key": key,
                "label": label,
                "trade_count": len(plans),
                "total_pnl": round(pnl, 2),
                "total_return_pct": round(pnl / initial_capital * 100, 2) if initial_capital else 0.0,
                "max_drawdown_pct": cls._drawdown_pct([initial_capital, *values]),
                "available": bool(plans),
                "note": "固定基准股数重算费用；不改变候选、成交数量或成交顺序。",
            })
        return result

    async def _backtest_inputs(
        self,
        start_date: date,
        end_date: date,
        job_id: str | None = None,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[str]]:
        warnings: list[str] = []
        if job_id:
            update_job("zhaban_backtest", job_id, phase="loading_data", progress=8, message="正在读取回测区间及前置日线缓存")
        async with async_session() as session:
            rows = list((await session.execute(
                select(StockDailyBar)
                .where(
                    StockDailyBar.trade_date >= start_date - timedelta(days=100),
                    StockDailyBar.trade_date <= end_date + timedelta(days=30),
                )
                .order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
            )).scalars().all())
            sentiments = list((await session.execute(
                select(MarketSentimentDaily)
                .where(MarketSentimentDaily.trade_date >= start_date, MarketSentimentDaily.trade_date <= end_date)
                .order_by(MarketSentimentDaily.trade_date)
            )).scalars().all())
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row.stock_code].append(_bar_dict(row))
        if not grouped:
            return {}, [], ["回测区间没有可用的A股日线缓存。"]
        index_history: list[dict[str, Any]] = []
        try:
            index_history = await collector.fetch_shanghai_index_history(days=800)
        except Exception:
            warnings.append("上证指数历史不可用，要求MA20时将无法通过市场环境过滤。")
        if not index_history:
            warnings.append("没有可核验的上证指数历史序列。")
        return dict(grouped), [
            {
                "date": row.trade_date,
                "failed_limit_rate": _number(row.failed_limit_rate),
                "failed_limit_count": row.failed_limit_count,
                "limit_up_count": row.limit_up_count,
                "source": row.source,
            }
            for row in sentiments
        ] + [{"date": item.get("date"), "close": item.get("close"), "_index": True} for item in index_history], warnings

    async def backtest(
        self,
        *,
        start_date: date,
        end_date: date,
        initial_capital: float,
        config_payload: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        config = self._normalize_config(config_payload)
        if start_date >= end_date:
            raise ValueError("回测结束日期必须晚于开始日期")
        if end_date > shanghai_now().date():
            raise ValueError("回测结束日期不能晚于今天")
        grouped, market_rows, warnings = await self._backtest_inputs(start_date, end_date, job_id)
        if not grouped:
            result = {
                "generated_at": shanghai_now().isoformat(), "status": "unavailable",
                "strategy": config, "summary": {"trade_count": 0}, "trades": [],
                "annual": [], "monthly": [], "cost_sensitivity": [], "warnings": warnings,
                "data_quality": {"audit_eligible": False, "mode": "unavailable"},
            }
            await self._save_result(ZHABAN_BACKTEST_CACHE_KEY, result)
            return result

        if job_id:
            update_job("zhaban_backtest", job_id, phase="event_detection", progress=24, message="正在按板块涨停制度识别历史炸板事件")
        all_dates = sorted({bar["date"] for history in grouped.values() for bar in history})
        effective_dates = [item for item in all_dates if start_date <= item <= end_date]
        if len(effective_dates) < 30:
            warnings.append(f"回测区间只有{len(effective_dates)}个交易日，低于可解释研究门槛30日。")
        sector_map, sector_available = await self._sector_map()
        if not sector_available:
            warnings.append("历史板块点时目录不可用，板块联动使用未分类，结果只能作观察。")

        events_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for code, history in grouped.items():
            for index in range(1, len(history)):
                current = history[index]
                target = current["date"]
                if target < start_date or target > end_date:
                    continue
                previous_close = _number(history[index - 1].get("close"))
                high = _number(current.get("high"))
                if previous_close in (None, 0) or high is None:
                    continue
                _, limit_pct = _limit_spec(code)
                if high < _limit_price(previous_close, limit_pct) - 0.0051:
                    continue
                event = self._event_from_history(
                    code, history[: index + 1], target,
                    sector=sector_map.get(code, ""), failed_pool_row=None,
                )
                if event and event["event_type"] != "none":
                    if config["exclude_st"] and "ST" in event["name"].upper():
                        continue
                    if not self._board_allowed(event["board"], config):
                        continue
                    events_by_date[target].append(event)
        event_count = sum(len(value) for value in events_by_date.values())
        if job_id:
            update_job("zhaban_backtest", job_id, phase="market_audit", progress=42, message=f"已识别{event_count}个日线事件，正在应用市场环境过滤")

        sentiment_by_date = {
            item["date"]: item for item in market_rows if not item.get("_index")
        }
        index_rows = [item for item in market_rows if item.get("_index")]
        index_rows.sort(key=lambda item: str(item.get("date") or ""))
        index_by_date: dict[date, tuple[float, float]] = {}
        index_closes: list[float] = []
        for item in index_rows:
            item_date = item.get("date")
            close = _number(item.get("close"))
            if not isinstance(item_date, date) or close is None:
                try:
                    item_date = date.fromisoformat(str(item_date)[:10])
                except (TypeError, ValueError):
                    continue
            index_closes.append(close)
            if len(index_closes) >= 20:
                index_by_date[item_date] = (close, sum(index_closes[-20:]) / 20)

        market_by_date: dict[date, dict[str, Any]] = {}
        for target in effective_dates:
            events = events_by_date.get(target, [])
            touched = sum(item["event_type"] in {"true_zhaban", "resealed"} for item in events)
            failed = sum(item["event_type"] == "true_zhaban" for item in events)
            sentiment = sentiment_by_date.get(target)
            if sentiment and sentiment.get("failed_limit_rate") is not None:
                failed_rate = float(sentiment["failed_limit_rate"])
                market_source = "market_sentiment_daily"
            elif touched:
                failed_rate = failed / touched * 100
                market_source = "daily_bar_approximation"
            else:
                failed_rate = None
                market_source = "unavailable"
            index_value, ma20 = index_by_date.get(target, (None, None))
            rate_ok = failed_rate is not None and failed_rate <= float(config["failed_limit_rate_max_pct"])
            above_ma20 = index_value is not None and ma20 is not None and index_value > ma20
            market_failed = (failed_rate is not None and not rate_ok) or (
                config["require_market_ma20"] and index_value is not None and not above_ma20
            )
            missing = failed_rate is None or (config["require_market_ma20"] and index_value is None)
            status = "failed" if market_failed else "unavailable" if missing else "passed"
            market_by_date[target] = {
                "status": status,
                "failed_limit_rate_pct": round(failed_rate, 2) if failed_rate is not None else None,
                "failed_limit_count": failed,
                "closed_limit_count": max(touched - failed, 0),
                "touched_limit_count": touched,
                "shanghai_close": round(index_value, 2) if index_value is not None else None,
                "shanghai_ma20": round(ma20, 2) if ma20 is not None else None,
                "above_ma20": above_ma20 if index_value is not None else None,
                "source": market_source,
                "conditions": [
                    _condition(
                        "failed_limit_rate", "全市场炸板率",
                        "passed" if rate_ok else "failed" if failed_rate is not None else "unavailable",
                        round(failed_rate, 2) if failed_rate is not None else None,
                        f"<={config['failed_limit_rate_max_pct']:g}%", market_source,
                    ),
                    _condition(
                        "market_ma20", "上证MA20环境",
                        "passed" if not config["require_market_ma20"] or above_ma20 else "failed" if index_value is not None else "unavailable",
                        round(index_value, 2) if index_value is not None else None,
                        "上证收盘高于MA20" if config["require_market_ma20"] else "未启用",
                        "tencent_index_daily" if index_value is not None else "unavailable",
                        f"MA20={ma20:.2f}" if ma20 is not None else "历史指数日期或覆盖不足",
                    ),
                ],
            }

        sector_counts_by_date: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for target, events in events_by_date.items():
            for event in events:
                if event["event_type"] in {"true_zhaban", "resealed"}:
                    sector_counts_by_date[target][event["sector"]] += 1

        evaluated: list[dict[str, Any]] = []
        for target, events in events_by_date.items():
            market = market_by_date.get(target, {"status": "unavailable", "conditions": []})
            for event in events:
                if event["event_type"] != "true_zhaban":
                    continue
                candidate = self._evaluate(
                    event, config, market,
                    sector_counts_by_date[target].get(event["sector"], 0),
                )
                if config["require_auction_confirmation"]:
                    auction_condition = _condition(
                        "auction_confirmation", "次日竞价确认", "unavailable", None,
                        f"量比>={config['auction_volume_ratio_min']:g}且高开{config['auction_high_open_pct_min']:g}%~{config['auction_high_open_pct_max']:g}%",
                        "历史竞价分钟数据缺失",
                        "当前缓存未保存历史竞价量比，不能假设通过",
                    )
                    candidate["conditions"].append(auction_condition)
                    candidate["unavailable_reasons"].append("次日竞价确认")
                    candidate["qualified"] = False
                    candidate["qualification_label"] = "等待历史竞价数据"
                if candidate["qualified"]:
                    evaluated.append(candidate)
        evaluated.sort(key=lambda item: (item["trade_date"], item["score"], item["code"]), reverse=True)
        if not config["allow_daily_approximation"]:
            warnings.append("当前关闭了日线近似，历史回测不会把缺少盘中字段的事件当作可交易候选。")
        warnings.extend([
            "本回测使用日线OHLC识别炸板，无法确认首次触板时间、炸板后低点和成交占比。",
            "历史竞价量比未进入缓存；竞价确认不是本次日线回测的已验证条件。",
            "板块分类来自当前缓存目录，不等同于历史点时行业分类。",
            "缓存未包含完整上市日制度事件，IPO初期无涨跌幅或特殊涨跌幅样本可能被误识别，需在点时股票主表补齐后复核。",
            "分批止盈的现金按最终退出日统一回笼，持仓占用采用保守口径。",
        ])
        if config["board_scope"] == "all":
            warnings.append("全部板块模式只用于查看独立分组结果；汇总组合不作为单一炸板模型的有效性证据。")

        if job_id:
            update_job("zhaban_backtest", job_id, phase="execution_simulation", progress=62, message=f"正在模拟{len(evaluated)}个合格事件的次日开盘成交")
        global_index = {item: position for position, item in enumerate(effective_dates)}
        bars_by_code_date = {
            code: {bar["date"]: bar for bar in history}
            for code, history in grouped.items()
        }
        plans: list[dict[str, Any]] = []
        skipped = {"no_next_day_bar": 0, "no_exit_bar": 0, "period_boundary": 0}
        for candidate in evaluated:
            signal_date = date.fromisoformat(str(candidate["trade_date"])[:10])
            signal_position = global_index.get(signal_date)
            if signal_position is None or signal_position + int(config["holding_days"]) >= len(effective_dates):
                skipped["period_boundary"] += 1
                continue
            entry_date = effective_dates[signal_position + 1]
            code = candidate["code"]
            by_date = bars_by_code_date.get(code, {})
            entry_bar = by_date.get(entry_date)
            if not entry_bar or _number(entry_bar.get("open")) in (None, 0):
                skipped["no_next_day_bar"] += 1
                continue
            exit_date = None
            exit_raw = None
            exit_reason = "持有期结束收盘退出"
            entry_position = global_index.get(entry_date)
            if entry_position is None:
                skipped["no_next_day_bar"] += 1
                continue
            entry_raw = _number(entry_bar.get("open"))
            entry_exec = entry_raw * (1 + float(config["slippage_rate"]))
            stop_level = entry_exec * (1 - float(config["stop_loss_pct"]) / 100)
            take_level = entry_exec * (1 + float(config["take_profit_pct"]) / 100)
            partial_ratio = float(config["take_profit_partial_pct"]) / 100
            remaining_ratio = 1.0
            partial_taken = False
            exit_legs: list[dict[str, Any]] = []
            for offset in range(int(config["holding_days"])):
                day_position = entry_position + offset
                if day_position >= len(effective_dates):
                    break
                day = effective_dates[day_position]
                bar = by_date.get(day)
                if not bar:
                    break
                open_price = _number(bar.get("open"))
                high = _number(bar.get("high"))
                low = _number(bar.get("low"))
                close = _number(bar.get("close"))
                if None in (open_price, high, low, close):
                    break
                active_stop = entry_exec if partial_taken else stop_level
                stop_hit = low <= active_stop
                take_hit = high >= take_level
                last_holding_day = offset == int(config["holding_days"]) - 1
                if open_price <= active_stop:
                    exit_legs.append({"date": day.isoformat(), "raw_price": open_price, "ratio": remaining_ratio, "reason": "止损跳空/开盘触发" if not partial_taken else "余仓低开保本退出"})
                    remaining_ratio = 0.0
                elif partial_taken and stop_hit:
                    exit_legs.append({"date": day.isoformat(), "raw_price": entry_exec, "ratio": remaining_ratio, "reason": "止盈后余仓保本退出"})
                    remaining_ratio = 0.0
                elif not partial_taken and open_price >= take_level:
                    exit_legs.append({"date": day.isoformat(), "raw_price": open_price, "ratio": partial_ratio, "reason": "止盈跳空，分批卖出"})
                    remaining_ratio -= partial_ratio
                    partial_taken = True
                    if low <= entry_exec:
                        exit_legs.append({"date": day.isoformat(), "raw_price": entry_exec, "ratio": remaining_ratio, "reason": "余仓回落至成本线"})
                        remaining_ratio = 0.0
                elif not partial_taken and stop_hit and take_hit:
                    exit_legs.append({"date": day.isoformat(), "raw_price": stop_level, "ratio": remaining_ratio, "reason": "同一日止盈止损均触发，按保守口径先止损"})
                    remaining_ratio = 0.0
                elif not partial_taken and stop_hit:
                    exit_legs.append({"date": day.isoformat(), "raw_price": stop_level, "ratio": remaining_ratio, "reason": "硬止损"})
                    remaining_ratio = 0.0
                elif not partial_taken and take_hit:
                    exit_legs.append({"date": day.isoformat(), "raw_price": take_level, "ratio": partial_ratio, "reason": "达到止盈线，分批卖出"})
                    remaining_ratio -= partial_ratio
                    partial_taken = True
                    if low <= entry_exec:
                        exit_legs.append({"date": day.isoformat(), "raw_price": entry_exec, "ratio": remaining_ratio, "reason": "同日路径不明，余仓按保本退出"})
                        remaining_ratio = 0.0
                if last_holding_day and remaining_ratio > 0:
                    exit_legs.append({"date": day.isoformat(), "raw_price": close, "ratio": remaining_ratio, "reason": "持有期结束收盘退出"})
                    remaining_ratio = 0.0
                if remaining_ratio <= 1e-9:
                    exit_date = day
                    break
            if exit_date is None or not exit_legs:
                skipped["no_exit_bar"] += 1
                continue
            exit_raw = sum(float(leg["raw_price"]) * float(leg["ratio"]) for leg in exit_legs)
            exit_reason = "；".join(dict.fromkeys(str(leg["reason"]) for leg in exit_legs))
            plans.append({
                "code": candidate["code"], "name": candidate["name"],
                "signal_date": signal_date.isoformat(), "entry_date": entry_date.isoformat(),
                "exit_date": exit_date.isoformat(),
                "signal_index": signal_position, "entry_index": entry_position,
                "exit_index": global_index.get(exit_date),
                "entry_raw": entry_raw, "exit_raw": exit_raw, "exit_legs": exit_legs, "reason": exit_reason,
                "score": candidate.get("score"), "board": candidate.get("board"),
                "event_source": candidate.get("event_source"),
            })

        plans.sort(key=lambda item: (item["entry_date"], -float(item.get("score") or 0), item["code"]))
        base_plans: list[dict[str, Any]] = []
        active: list[dict[str, Any]] = []
        active_codes: set[str] = set()
        plans_by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for plan in plans:
            plans_by_entry[plan["entry_date"]].append(plan)
        cash = float(initial_capital)
        trades: list[dict[str, Any]] = []
        daily_values: list[dict[str, Any]] = []
        max_positions = int(config["max_positions"])
        max_allocation = float(initial_capital) * float(config["max_position_pct"]) / 100
        for day in effective_dates:
            day_key = day.isoformat()
            still_active: list[dict[str, Any]] = []
            for position in active:
                if position["plan"].get("exit_date") == day_key:
                    result = position["result"]
                    cash += float(result["net_proceeds"])
                    trades.append(result)
                    base_plans.append(position["plan"])
                    active_codes.discard(result["code"])
                else:
                    still_active.append(position)
            active = still_active
            for plan in plans_by_entry.get(day_key, []):
                if len(active) >= max_positions or plan["code"] in active_codes:
                    continue
                entry_base = float(plan["entry_raw"]) * (1 + float(config["slippage_rate"]))
                slots = max(1, max_positions - len(active))
                allocation = min(max_allocation, cash / slots)
                shares = int(allocation / entry_base / 100) * 100
                if shares < 100:
                    continue
                plan = {**plan, "shares": shares}
                result = self._reprice_plan(
                    plan,
                    commission_rate=float(config["commission_rate"]),
                    stamp_tax_rate=float(config["stamp_tax_rate"]),
                    slippage_rate=float(config["slippage_rate"]),
                )
                cash -= float(result["total_cost"])
                active.append({"plan": plan, "result": result})
                active_codes.add(plan["code"])
            # A one-day holding period enters at today's open and exits at
            # today's close. Close newly entered positions after all opening
            # orders have been sized so the daily cash sequence stays valid.
            still_active = []
            for position in active:
                if position["plan"].get("exit_date") == day_key:
                    result = position["result"]
                    cash += float(result["net_proceeds"])
                    trades.append(result)
                    base_plans.append(position["plan"])
                    active_codes.discard(result["code"])
                else:
                    still_active.append(position)
            active = still_active
            marked_value = cash
            for position in active:
                mark_bar = bars_by_code_date.get(position["plan"]["code"], {}).get(day)
                mark = _number(mark_bar.get("close")) if mark_bar else None
                marked_value += (mark or position["result"]["entry_price"]) * position["result"]["shares"]
            daily_values.append({
                "date": day_key, "value": round(marked_value, 2),
                "cash": round(cash, 2), "holding_count": len(active),
            })
        for position in active:
            skipped["no_exit_bar"] += 1

        summary = self._period_metrics(trades, daily_values, initial_capital)
        split_index = max(1, int(len(daily_values) * 0.7)) if daily_values else 0
        split_date = daily_values[split_index]["date"] if daily_values and split_index < len(daily_values) else None
        train_values = daily_values[:split_index] if split_index else []
        oos_values = daily_values[split_index:] if split_index else []
        train_trades = [item for item in trades if not split_date or item.get("exit_date", "") < split_date]
        oos_trades = [item for item in trades if split_date and item.get("exit_date", "") >= split_date]
        train_start = initial_capital
        if split_index > 0 and split_index - 1 < len(daily_values):
            train_start = initial_capital
        oos_start = float(daily_values[split_index - 1]["value"]) if split_index and split_index - 1 < len(daily_values) else initial_capital
        summary["train"] = self._period_metrics(train_trades, train_values, train_start)
        summary["out_of_sample"] = self._period_metrics(oos_trades, oos_values, oos_start)
        summary["sample_split_date"] = split_date
        summary["skipped"] = skipped
        summary["initial_capital"] = round(initial_capital, 2)
        summary["final_value"] = round(daily_values[-1]["value"] if daily_values else initial_capital, 2)

        annual = self._group_periods(daily_values, trades, initial_capital, granularity="year")
        monthly = self._group_periods(daily_values, trades, initial_capital, granularity="month")
        cost_sensitivity = self._cost_sensitivity(base_plans, initial_capital, config)
        active_months = [item for item in monthly if int(item.get("trade_count") or 0) > 0]
        summary["worst_month"] = min(
            active_months,
            key=lambda item: float(item.get("total_return_pct") or 0),
            default=None,
        )
        validation = self._validation_report(summary, annual, cost_sensitivity)

        if job_id:
            update_job("zhaban_backtest", job_id, phase="reporting", progress=88, message="正在生成年度、月度、样本外和成本敏感性报告")
        result = {
            "generated_at": shanghai_now().isoformat(),
            "status": "research_only" if trades else "insufficient_data",
            "source": "stock_daily_bars+daily_bar_approximation",
            "is_realtime": False,
            "cache_used": True,
            "strategy": config,
            "period": {"requested_from": start_date.isoformat(), "requested_to": end_date.isoformat(), "from": effective_dates[0].isoformat() if effective_dates else None, "to": effective_dates[-1].isoformat() if effective_dates else None},
            "summary": summary,
            "annual": annual,
            "monthly": monthly,
            "board_performance": self._board_performance(trades, evaluated),
            "cost_sensitivity": cost_sensitivity,
            "validation": validation,
            "trades": trades[-500:],
            "daily_values": daily_values[-800:],
            "candidate_count": len(evaluated),
            "event_count": event_count,
            "data_quality": {
                "audit_eligible": False,
                "mode": "daily_bar_event_study",
                "board_scope": config["board_scope"],
                "board_label": BOARD_LABELS.get(config["board_scope"], "全部（分组对比）"),
                "universe_count": len(grouped),
                "trading_days": len(effective_dates),
                "event_count": event_count,
                "qualified_event_count": len(evaluated),
                "executed_trade_count": len(trades),
                "intraday_verified_count": 0,
                "auction_verified_count": 0,
                "missing_fields": ["首次触板时间", "炸板后最低价", "炸板后成交量", "历史竞价量比", "历史点时板块"],
                "missing_policy": "缺失盘中或点时字段不补零；结果只用于研究与模拟，不生成自动交易指令。",
                "execution_method": "事件日收盘形成候选，下一交易日开盘成交；+8%分批止盈后余仓保本，最长3日口径可调。",
                "warnings": list(dict.fromkeys(warnings)),
            },
            "warnings": list(dict.fromkeys(warnings)),
            "disclaimer": "本回测是日线事件研究近似，不代表真实可成交结果，也不构成投资建议。未验证竞价确认条件，不能据此宣称胜率或收益承诺。",
        }
        await self._save_result(ZHABAN_BACKTEST_CACHE_KEY, result)
        return result

    async def _save_result(self, key: str, result: dict[str, Any]) -> None:
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, key)
                if row is None:
                    session.add(MarketDataCache(key=key, payload=result))
                else:
                    row.payload = result
                    row.updated_at = datetime.utcnow()
                await session.commit()
        except Exception:
            if key == ZHABAN_CACHE_KEY:
                quant_store.write("zhaban_latest", {"version": 1, "result": result})
            elif key == ZHABAN_BACKTEST_CACHE_KEY:
                quant_store.write("zhaban_backtest_latest", {"version": 1, "result": result})

    async def latest(self) -> dict[str, Any] | None:
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, ZHABAN_CACHE_KEY)
            if row and isinstance(row.payload, dict):
                return dict(row.payload)
        except Exception:
            pass
        value = quant_store.read("zhaban_latest").get("result")
        return dict(value) if isinstance(value, dict) else None

    async def latest_backtest(self) -> dict[str, Any] | None:
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, ZHABAN_BACKTEST_CACHE_KEY)
            if row and isinstance(row.payload, dict):
                return dict(row.payload)
        except Exception:
            pass
        value = quant_store.read("zhaban_backtest_latest").get("result")
        return dict(value) if isinstance(value, dict) else None

    async def start_scan(self, request: dict[str, Any]) -> dict[str, Any]:
        force = bool(request.get("force"))
        running = latest_running_job("zhaban")
        if running and not force:
            return running
        target = request.get("target_date")
        if isinstance(target, str):
            target = date.fromisoformat(target[:10])
        self._normalize_config(request.get("config") or {})
        job = create_job("zhaban", "zhaban_scan", {"task": "scan", "target_date": target.isoformat() if target else None})
        spawn(self._run_scan_job(job["job_id"], target, request.get("config") or {}))
        return job

    async def _run_scan_job(self, job_id: str, target: date | None, config: dict[str, Any]) -> None:
        async with self._lock:
            update_job(
                "zhaban", job_id, status="running", phase="starting", progress=3,
                message="正在启动炸板事件研究", started_at=shanghai_now().isoformat(),
            )
            try:
                result = await self.scan(target_date=target, config_payload=config, job_id=job_id)
                update_job(
                    "zhaban", job_id, status="completed", phase="completed", progress=100,
                    message=f"研究完成，识别{result.get('summary', {}).get('true_zhaban', 0)}个真炸板事件",
                    completed_at=shanghai_now().isoformat(),
                    result={
                        "data_date": result.get("data_date"),
                        "summary": result.get("summary") or {},
                        "status": result.get("status"),
                    },
                )
            except Exception as exc:
                update_job(
                    "zhaban", job_id, status="failed", phase="failed", progress=100,
                    message="炸板事件研究失败", error=str(exc)[:300],
                    completed_at=shanghai_now().isoformat(),
                )

    async def start_backtest(self, request: dict[str, Any]) -> dict[str, Any]:
        force = bool(request.get("force"))
        running = latest_running_job("zhaban_backtest")
        if running and not force:
            return running
        start_value = request.get("start_date")
        end_value = request.get("end_date")
        try:
            start_date = date.fromisoformat(str(start_value)[:10])
            end_date = date.fromisoformat(str(end_value)[:10])
        except (TypeError, ValueError) as exc:
            raise ValueError("回测日期必须使用YYYY-MM-DD格式") from exc
        if start_date >= end_date:
            raise ValueError("回测结束日期必须晚于开始日期")
        if end_date > shanghai_now().date():
            raise ValueError("回测结束日期不能晚于今天")
        config = self._normalize_config(request.get("config") or {})
        initial_capital = _number(request.get("initial_capital"))
        if initial_capital is None or not 10000 <= initial_capital <= 100000000:
            raise ValueError("初始资金必须在1万到1亿元之间")
        job = create_job(
            "zhaban_backtest", "zhaban_bt",
            {
                "task": "backtest", "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(), "initial_capital": initial_capital,
            },
        )
        spawn(self._run_backtest_job(job["job_id"], start_date, end_date, initial_capital, config))
        return job

    async def _run_backtest_job(
        self,
        job_id: str,
        start_date: date,
        end_date: date,
        initial_capital: float,
        config: dict[str, Any],
    ) -> None:
        async with self._lock:
            update_job(
                "zhaban_backtest", job_id, status="running", phase="starting", progress=3,
                message="正在启动炸板策略日线事件回测", started_at=shanghai_now().isoformat(),
            )
            try:
                result = await self.backtest(
                    start_date=start_date, end_date=end_date,
                    initial_capital=initial_capital, config_payload=config, job_id=job_id,
                )
                update_job(
                    "zhaban_backtest", job_id, status="completed", phase="completed", progress=100,
                    message=f"回测完成，执行{result.get('summary', {}).get('trade_count', 0)}笔",
                    completed_at=shanghai_now().isoformat(),
                    result={
                        "period": result.get("period"),
                        "summary": result.get("summary") or {},
                        "status": result.get("status"),
                    },
                )
            except Exception as exc:
                update_job(
                    "zhaban_backtest", job_id, status="failed", phase="failed", progress=100,
                    message="炸板策略回测失败", error=str(exc)[:300],
                    completed_at=shanghai_now().isoformat(),
                )

    @staticmethod
    def job(job_id: str) -> dict[str, Any] | None:
        return get_job("zhaban", job_id)

    @staticmethod
    def backtest_job(job_id: str) -> dict[str, Any] | None:
        return get_job("zhaban_backtest", job_id)

    @staticmethod
    def running_scan_job() -> dict[str, Any] | None:
        return latest_running_job("zhaban")

    @staticmethod
    def running_backtest_job() -> dict[str, Any] | None:
        return latest_running_job("zhaban_backtest")


zhaban_strategy_service = ZhabanStrategyService()
