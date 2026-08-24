"""Auditable intraday implementation of the upgraded overnight strategy."""

from __future__ import annotations

import asyncio
import math
import uuid
from copy import deepcopy
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import desc, func, select

from database import async_session
from models import (
    MarketDataCache,
    OvernightPosition,
    OvernightStrategyRun,
    PersonalSystemConfig,
    StockDailyBar,
    StockMinuteBar,
)
from quant.market_cache import load_quant_market_snapshot
from quant.indicators import normalize_snapshot_stock
from quant.risk import CRITICAL_ANNOUNCEMENT_TERMS
from services.data_collector import collector, shanghai_now
from services.history_cache import history_cache
from services.macro_policy_news import macro_policy_news_collector
from services.market_decision_contract import (
    WORKBENCH_CACHE_PREFIX,
    evaluate_market_execution_gate,
)
from services.quote_cache import quote_snapshot_service
from services.report_calendar import report_calendar_service


STRATEGY_CONFIG: dict[str, Any] = {
    "id": "overnight_review_v2",
    "name": "一夜持股·复盘增强版",
    "version": "2.0",
    "is_builtin": True,
    "preliminary_scan": "14:30",
    "entry_window": "14:52-14:59",
    "exit_window": "次一交易日 09:30-10:00",
    "change_pct": [3.0, 5.0],
    "volume_ratio_min": 1.2,
    "turnover_pct": [5.0, 10.0],
    "market_cap_yi": [50.0, 200.0],
    "exclude_star_market": True,
    "require_volume_staircase": True,
    "require_relative_strength": True,
    "relative_strength_min_pct": 0.0,
    "require_vwap_hold": True,
    "require_late_high_retest": True,
    "minimum_listing_sessions": 60,
    "minimum_price": 0.0,
    "exclude_chinext": False,
    "require_market_ma20": False,
    "require_price_above_ma10": True,
    "requires_auction_confirmation": False,
    "auction_window": "09:24-09:27",
    "auction_volume_ratio_min": 3.0,
    "auction_high_open_pct": [2.0, 5.0],
    "take_profit_pct": 2.0,
    "stop_loss_pct": 3.0,
    "ai_auction_monitor": False,
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

AUCTION_STRATEGY_CONFIG: dict[str, Any] = {
    **STRATEGY_CONFIG,
    "id": "overnight_auction_confirm_v1",
    "name": "一夜持股·竞价确认版",
    "version": "3.0",
    "volume_ratio_min": 1.0,
    "exclude_chinext": True,
    "minimum_price": 2.0,
    "minimum_listing_sessions": 365,
    "require_market_ma20": True,
    "require_price_above_ma10": False,
    "requires_auction_confirmation": True,
    "auction_volume_ratio_min": 3.0,
    "auction_high_open_pct": [2.0, 5.0],
    "max_positions": 2,
    "max_position_pct": 25.0,
    "take_profit_pct": 4.0,
    "ai_auction_monitor": True,
}

OVERNIGHT_STRATEGY_CONFIG_KEY = "overnight_strategy_configs_v2"
EDITABLE_FACTORS = {
    "change_pct", "volume_ratio_min", "turnover_pct", "market_cap_yi",
    "exclude_star_market", "require_volume_staircase", "require_relative_strength",
    "relative_strength_min_pct", "require_vwap_hold", "require_late_high_retest",
    "last_five_minute_change_max", "max_positions", "minimum_price", "exclude_chinext",
    "minimum_listing_sessions", "require_market_ma20", "require_price_above_ma10",
    "requires_auction_confirmation", "auction_volume_ratio_min", "auction_high_open_pct",
    "take_profit_pct", "stop_loss_pct", "max_position_pct", "max_total_position_pct",
}
FACTOR_SCHEMA = [
    {"key": "change_pct", "label": "当日涨幅", "type": "range", "min": -5, "max": 15, "step": 0.5, "unit": "%"},
    {"key": "volume_ratio_min", "label": "最低量比", "type": "number", "min": 0.5, "max": 5, "step": 0.1},
    {"key": "turnover_pct", "label": "换手率", "type": "range", "min": 0, "max": 30, "step": 0.5, "unit": "%"},
    {"key": "market_cap_yi", "label": "总市值", "type": "range", "min": 10, "max": 1000, "step": 10, "unit": "亿元"},
    {"key": "exclude_star_market", "label": "排除科创板(688/689)", "type": "boolean"},
    {"key": "exclude_chinext", "label": "排除创业板(300/301/302)", "type": "boolean"},
    {"key": "minimum_price", "label": "最低股价", "type": "number", "min": 0, "max": 100, "step": 0.1, "unit": "元"},
    {"key": "minimum_listing_sessions", "label": "最低上市交易日", "type": "number", "min": 60, "max": 5000, "step": 1, "unit": "日"},
    {"key": "require_volume_staircase", "label": "近3日台阶放量", "type": "boolean"},
    {"key": "require_relative_strength", "label": "分时强于上证指数", "type": "boolean"},
    {"key": "require_market_ma20", "label": "上证站上MA20", "type": "boolean"},
    {"key": "require_price_above_ma10", "label": "股价高于MA10", "type": "boolean"},
    {"key": "relative_strength_min_pct", "label": "最低相对强度", "type": "number", "min": -2, "max": 5, "step": 0.1, "unit": "%"},
    {"key": "require_vwap_hold", "label": "尾盘守住VWAP", "type": "boolean"},
    {"key": "require_late_high_retest", "label": "14:55新高回踩确认", "type": "boolean"},
    {"key": "last_five_minute_change_max", "label": "尾盘5分钟最大涨幅", "type": "number", "min": 0.5, "max": 5, "step": 0.1, "unit": "%"},
    {"key": "requires_auction_confirmation", "label": "次日竞价双条件确认", "type": "boolean"},
    {"key": "auction_volume_ratio_min", "label": "竞价量比主阈值", "type": "number", "min": 0.1, "max": 20, "step": 0.1},
    {"key": "auction_high_open_pct", "label": "竞价高开幅度", "type": "range", "min": -10, "max": 20, "step": 0.5, "unit": "%"},
    {"key": "take_profit_pct", "label": "止盈幅度", "type": "number", "min": 0.5, "max": 20, "step": 0.5, "unit": "%"},
    {"key": "stop_loss_pct", "label": "止损幅度", "type": "number", "min": 0.5, "max": 20, "step": 0.5, "unit": "%"},
    {"key": "max_positions", "label": "最多持股数", "type": "number", "min": 1, "max": 10, "step": 1, "unit": "只"},
]

MAJOR_NEGATIVE_TERMS = tuple(dict.fromkeys((
    *CRITICAL_ANNOUNCEMENT_TERMS,
    "业绩预亏", "业绩大幅下降", "大额减持", "违规担保", "重大诉讼",
    "行政处罚", "债务违约", "终止重组", "下修业绩", "审计保留意见",
)))

RUN_STAGES = {"preliminary", "entry", "auction", "exit", "force_exit"}


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
        "strategy_id": str((row.data_quality or {}).get("strategy_id") or STRATEGY_CONFIG["id"]),
        "strategy_name": str((row.data_quality or {}).get("strategy", {}).get("name") or STRATEGY_CONFIG["name"]),
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
        "research_only": bool((row.data_quality or {}).get("research_only")),
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

    @staticmethod
    def _validate_strategy(payload: dict[str, Any], *, strategy_id: str | None = None) -> dict[str, Any]:
        config = {**STRATEGY_CONFIG}
        for key in EDITABLE_FACTORS:
            if key in payload:
                config[key] = payload[key]
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("策略名称不能为空")
        if len(name) > 80:
            raise ValueError("策略名称不能超过80个字符")

        for key in ("change_pct", "turnover_pct", "market_cap_yi"):
            values = config.get(key)
            if not isinstance(values, (list, tuple)) or len(values) != 2:
                raise ValueError(f"{key} 必须是包含最小值和最大值的数组")
            lower, upper = (_number(values[0]), _number(values[1]))
            if lower is None or upper is None or lower >= upper:
                raise ValueError(f"{key} 的最小值必须小于最大值")
            config[key] = [round(lower, 4), round(upper, 4)]
        values = config.get("auction_high_open_pct")
        if not isinstance(values, (list, tuple)) or len(values) != 2:
            raise ValueError("auction_high_open_pct 必须是包含最小值和最大值的数组")
        lower, upper = (_number(values[0]), _number(values[1]))
        if lower is None or upper is None or lower >= upper:
            raise ValueError("auction_high_open_pct 的最小值必须小于最大值")
        config["auction_high_open_pct"] = [round(lower, 4), round(upper, 4)]
        numeric_limits = {
            "volume_ratio_min": (0.1, 10.0),
            "auction_volume_ratio_min": (0.1, 20.0),
            "relative_strength_min_pct": (-5.0, 10.0),
            "last_five_minute_change_max": (0.1, 10.0),
            "max_positions": (1, 10),
            "minimum_price": (0.0, 1000.0),
            "minimum_listing_sessions": (60, 5000),
            "take_profit_pct": (0.5, 20.0),
            "stop_loss_pct": (0.5, 20.0),
            "max_position_pct": (1.0, 100.0),
            "max_total_position_pct": (1.0, 100.0),
        }
        for key, (minimum, maximum) in numeric_limits.items():
            value = _number(config.get(key))
            if value is None or not minimum <= value <= maximum:
                raise ValueError(f"{key} 必须在 {minimum} 到 {maximum} 之间")
            config[key] = int(value) if key in {"max_positions", "minimum_listing_sessions"} else round(value, 4)
        for key in (
            "exclude_star_market", "require_volume_staircase", "require_relative_strength",
            "require_vwap_hold", "require_late_high_retest", "exclude_chinext",
            "require_market_ma20", "require_price_above_ma10", "requires_auction_confirmation",
            "ai_auction_monitor",
        ):
            config[key] = bool(config.get(key))
        config.update({
            "id": strategy_id or f"overnight_{uuid.uuid4().hex[:12]}",
            "name": name,
            "version": "2.0",
            "is_builtin": False,
            "updated_at": datetime.utcnow().isoformat(),
        })
        return config

    async def _strategy_store(self) -> tuple[str, list[dict[str, Any]]]:
        payload: dict[str, Any] = {}
        try:
            async with async_session() as session:
                row = await session.get(PersonalSystemConfig, OVERNIGHT_STRATEGY_CONFIG_KEY)
            if row and isinstance(row.payload, dict):
                payload = dict(row.payload)
        except Exception:
            payload = {}
        strategies = [{**STRATEGY_CONFIG}, {**AUCTION_STRATEGY_CONFIG}]
        seen = {STRATEGY_CONFIG["id"], AUCTION_STRATEGY_CONFIG["id"]}
        for item in payload.get("strategies") or []:
            if not isinstance(item, dict):
                continue
            strategy_id = str(item.get("id") or "")
            if not strategy_id or strategy_id in seen:
                continue
            try:
                strategies.append(self._validate_strategy(item, strategy_id=strategy_id))
                seen.add(strategy_id)
            except ValueError:
                continue
        active_id = str(payload.get("active_id") or STRATEGY_CONFIG["id"])
        if active_id not in seen:
            active_id = STRATEGY_CONFIG["id"]
        return active_id, strategies

    async def _save_strategy_store(self, active_id: str, strategies: list[dict[str, Any]]) -> None:
        custom = [item for item in strategies if not item.get("is_builtin")]
        payload = {
            "active_id": active_id,
            "strategies": custom,
            "updated_at": datetime.utcnow().isoformat(),
        }
        async with async_session() as session:
            row = await session.get(PersonalSystemConfig, OVERNIGHT_STRATEGY_CONFIG_KEY)
            if row is None:
                session.add(PersonalSystemConfig(key=OVERNIGHT_STRATEGY_CONFIG_KEY, payload=payload))
            else:
                row.payload = payload
            await session.commit()

    async def list_strategies(self) -> dict[str, Any]:
        active_id, strategies = await self._strategy_store()
        return {
            "active_id": active_id,
            "strategies": strategies,
            "factor_schema": FACTOR_SCHEMA,
            "validation_note": "参数可调不等于胜率承诺；历史点时分钟样本不足时只展示真实前向运行对比。",
        }

    async def save_strategy(self, payload: dict[str, Any], strategy_id: str | None = None) -> dict[str, Any]:
        active_id, strategies = await self._strategy_store()
        if strategy_id == STRATEGY_CONFIG["id"]:
            raise ValueError("内置复盘策略不可覆盖，请另存为新策略")
        existing = next((item for item in strategies if item["id"] == strategy_id), None)
        merged = {**(existing or {}), **(payload or {})}
        config = self._validate_strategy(merged, strategy_id=strategy_id)
        if any(item["name"] == config["name"] and item["id"] != config["id"] for item in strategies):
            raise ValueError("策略名称已存在")
        strategies = [item for item in strategies if item["id"] != config["id"]]
        strategies.append(config)
        await self._save_strategy_store(active_id, strategies)
        return config

    async def activate_strategy(self, strategy_id: str) -> dict[str, Any]:
        _, strategies = await self._strategy_store()
        strategy = next((item for item in strategies if item["id"] == strategy_id), None)
        if strategy is None:
            raise LookupError("一夜持股策略不存在")
        await self._save_strategy_store(strategy_id, strategies)
        return strategy

    async def delete_strategy(self, strategy_id: str) -> None:
        active_id, strategies = await self._strategy_store()
        strategy = next((item for item in strategies if item["id"] == strategy_id), None)
        if strategy is None:
            raise LookupError("一夜持股策略不存在")
        if strategy.get("is_builtin"):
            raise ValueError("内置复盘策略不可删除")
        remaining = [item for item in strategies if item["id"] != strategy_id]
        await self._save_strategy_store(
            STRATEGY_CONFIG["id"] if active_id == strategy_id else active_id,
            remaining,
        )

    async def _resolve_strategy(self, strategy_id: str | None = None) -> dict[str, Any]:
        active_id, strategies = await self._strategy_store()
        target = strategy_id or active_id
        strategy = next((item for item in strategies if item["id"] == target), None)
        if strategy is None:
            raise ValueError("所选一夜持股策略不存在")
        return {**strategy}

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
            existing_quality = row.data_quality if isinstance(row.data_quality, dict) else {}
            row.data_quality = {**existing_quality, **(data_quality or {})}
            row.error = error
            row.finished_at = datetime.utcnow()
            await session.commit()

    async def _create_run(
        self,
        stage: str,
        trigger: str,
        strategy: dict[str, Any] | None = None,
        *,
        research_only: bool = False,
    ) -> tuple[OvernightStrategyRun, bool]:
        strategy = strategy or {**STRATEGY_CONFIG}
        normalized = str(stage or "").strip().lower()
        normalized_trigger = str(trigger or "manual").strip().lower()[:20]
        if normalized not in RUN_STAGES:
            raise ValueError("stage 必须是 preliminary、entry、auction、exit 或 force_exit")
        async with async_session() as session:
            active_rows = (await session.execute(
                select(OvernightStrategyRun)
                .where(
                    OvernightStrategyRun.stage == normalized,
                    OvernightStrategyRun.status.in_(["queued", "running"]),
                )
                .order_by(desc(OvernightStrategyRun.id))
                .limit(20)
            )).scalars().all()
            active = next(
                (
                    item for item in active_rows
                    if str((item.data_quality or {}).get("strategy_id") or STRATEGY_CONFIG["id"]) == strategy["id"]
                    and bool((item.data_quality or {}).get("research_only")) == bool(research_only)
                ),
                None,
            )
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
                recent_rows = (await session.execute(
                    select(OvernightStrategyRun)
                    .where(
                        OvernightStrategyRun.stage == normalized,
                        OvernightStrategyRun.status == "completed",
                    )
                    .order_by(desc(OvernightStrategyRun.id))
                    .limit(20)
                )).scalars().all()
                recent = next(
                    (
                        item for item in recent_rows
                        if str((item.data_quality or {}).get("strategy_id") or STRATEGY_CONFIG["id"]) == strategy["id"]
                        and bool((item.data_quality or {}).get("research_only")) == bool(research_only)
                    ),
                    None,
                )
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
                data_quality={
                    "strategy_id": strategy["id"],
                    "strategy": strategy,
                    "research_only": bool(research_only),
                },
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
        strategy_id: str | None = None,
        research_only: bool = False,
    ) -> dict[str, Any]:
        strategy = await self._resolve_strategy(strategy_id)
        # Manual scans outside their execution window become explicitly
        # labelled cache research. Scheduled jobs and real-time entry remain
        # fail-closed and keep the original window/data requirements.
        if not research_only and trigger == "manual" and stage in {"preliminary", "entry"}:
            now = shanghai_now()
            window_open, _ = self._stage_window_status(stage, now)
            research_only = now.weekday() >= 5 or not window_open
        row, created = await self._create_run(
            stage, trigger, strategy, research_only=research_only,
        )
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
                    stored = row.data_quality or {}
                    strategy = stored.get("strategy") if isinstance(stored, dict) else None
                    research_only = bool(stored.get("research_only")) if isinstance(stored, dict) else False
                    if not isinstance(strategy, dict):
                        strategy = await self._resolve_strategy()
                if stage in {"preliminary", "entry"}:
                    await self._scan(run_id, stage, strategy, research_only=research_only)
                elif stage == "auction":
                    await self._auction(run_id, strategy)
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
        if stage == "auction":
            return 9 * 60 + 24 <= minute <= 9 * 60 + 27, "AI竞价盯盘只在交易日09:24-09:27执行"
        return 14 * 60 + 52 <= minute <= 14 * 60 + 59, "模拟入场只在交易日14:52-14:59执行"

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
    def _prefilter(stocks: list[dict], config: dict[str, Any] | None = None) -> list[dict]:
        selected, _ = OvernightStrategyService._prefilter_diagnostics(stocks, config)
        return selected

    @staticmethod
    def _prefilter_diagnostics(
        stocks: list[dict], config: dict[str, Any] | None = None,
    ) -> tuple[list[dict], dict[str, int]]:
        config = config or STRATEGY_CONFIG
        change_min, change_max = config["change_pct"]
        turnover_min, turnover_max = config["turnover_pct"]
        cap_min, cap_max = config["market_cap_yi"]
        selected: list[dict] = []
        rejected: dict[str, int] = {}

        def reject(reason: str) -> None:
            rejected[reason] = rejected.get(reason, 0) + 1

        for raw in stocks:
            stock = normalize_snapshot_stock(raw)
            code = str(stock.get("code") or "")
            name = str(stock.get("name") or "")
            change = _number(stock.get("change_pct"))
            ratio = _number(stock.get("vol_ratio"))
            turnover = _number(stock.get("turnover"))
            market_cap = _number(stock.get("market_cap"))
            price = _number(stock.get("price"))
            minimum_price = float(config.get("minimum_price") or 0)
            if price is None or price <= minimum_price or "ST" in name.upper() or "退" in name:
                reject("价格/名称排除（ST、退市或低于最低价）")
                continue
            if config.get("exclude_star_market") and code.startswith(("688", "689")):
                reject("科创板权限过滤")
                continue
            if config.get("exclude_chinext") and code.startswith(("300", "301", "302")):
                reject("创业板权限过滤")
                continue
            if change is None or not change_min <= change <= change_max:
                reject("涨幅不在策略区间")
                continue
            if ratio is None or ratio <= float(config["volume_ratio_min"]):
                reject("量比未超过主阈值")
                continue
            if turnover is None or not turnover_min <= turnover <= turnover_max:
                reject("换手率不在策略区间")
                continue
            if market_cap is None or not cap_min <= market_cap <= cap_max:
                reject("市值不在策略区间")
                continue
            selected.append(stock)
        return selected, rejected

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
        market_audit: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        allow_unavailable: bool = False,
    ) -> dict[str, Any]:
        config = config or STRATEGY_CONFIG
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
            _condition("change_pct", "当日涨幅", "passed", round(float(stock["change_pct"]), 2), f"{config['change_pct'][0]:g}%-{config['change_pct'][1]:g}%", source="全市场实时行情"),
            _condition("volume_ratio", "量比", "passed", round(float(stock["vol_ratio"]), 2), f">{config['volume_ratio_min']:g}", source="全市场实时行情"),
            _condition("turnover", "换手率", "passed", round(float(stock["turnover"]), 2), f"{config['turnover_pct'][0]:g}%-{config['turnover_pct'][1]:g}%", source="全市场实时行情"),
            _condition("market_cap", "总市值", "passed", round(float(stock["market_cap"]), 2), f"{config['market_cap_yi'][0]:g}-{config['market_cap_yi'][1]:g}亿元", source="全市场实时行情"),
            _condition(
                "star_market_permission", "科创板权限过滤", "passed",
                "已排除688/689" if config.get("exclude_star_market") else "允许科创板",
                "按策略开关过滤", source="股票代码与策略配置",
            ),
            _condition(
                "chinext_market_permission", "创业板过滤", "passed",
                "已排除300/301/302" if config.get("exclude_chinext") else "允许创业板",
                "按策略开关过滤", source="股票代码与策略配置",
            ),
        ]

        recent_volumes = [
            value for item in bars[-2:]
            for value in [_number(item.get("volume"))]
            if value is not None and value > 0
        ]
        current_volume = _number(stock.get("volume"))
        staircase_available = len(recent_volumes) == 2 and current_volume is not None and current_volume > 0
        staircase_passed = bool(
            staircase_available
            and recent_volumes[0] < recent_volumes[1] < current_volume
        )
        if config.get("require_volume_staircase"):
            conditions.append(_condition(
                "volume_staircase", "近3日台阶放量",
                "passed" if staircase_passed else "failed" if staircase_available else "unavailable",
                [*recent_volumes, current_volume], "前两日成交量 < 昨日成交量 < 当日累计成交量",
                source="本地日线缓存+全市场实时行情",
            ))
        else:
            conditions.append(_condition(
                "volume_staircase", "近3日台阶放量", "passed", "策略未启用", "可选增强因子",
                source="策略配置",
            ))

        listing_ok = len(bars) >= int(config["minimum_listing_sessions"])
        conditions.append(_condition(
            "listing_sessions", "上市交易日", "passed" if listing_ok else "unavailable",
            len(bars), ">=60个已缓存交易日", source="本地日线缓存",
            detail="缓存不足时不会把股票当作非次新股",
        ))

        ma_available = all(value is not None for value in ma.values())
        ma_order = bool(ma_available and ma[10] > ma[20] > ma[30])
        price_above = bool(
            ma_available
            and price is not None
            and price > ma[5]
            and (not config.get("require_price_above_ma10") or price > ma[10])
        )
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
                "价格 > MA5" + (" 且价格 > MA10" if config.get("require_price_above_ma10") else ""),
                source="缓存日线+当前实时价",
            ),
        ])

        market_audit = market_audit or {}
        market_above_ma20 = market_audit.get("above_ma20")
        if config.get("require_market_ma20"):
            conditions.append(_condition(
                "market_ma20", "上证站上MA20",
                "passed" if market_above_ma20 is True else "failed" if market_above_ma20 is False else "unavailable",
                {
                    "index": market_audit.get("index"),
                    "ma20": market_audit.get("ma20"),
                    "above_ma20": market_above_ma20,
                },
                "上证收盘/最新价 > MA20", source="上证指数日线+实时行情",
                detail=str(market_audit.get("detail") or ""),
            ))
        else:
            conditions.append(_condition(
                "market_ma20", "上证站上MA20", "passed", "策略未启用", "可选大盘过滤",
                source="策略配置",
            ))

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
        # Research mode may rank a cached candidate while explicitly keeping
        # missing evidence as "待核验". Execution mode remains fail-closed.
        passed = not failed and (allow_unavailable or not unavailable)
        trend_spread = (
            (ma[10] - ma[30]) / ma[30] * 100
            if ma.get(10) is not None and ma.get(30) not in (None, 0) else 0.0
        )
        score = 55.0
        change_midpoint = sum(config["change_pct"]) / 2
        score += max(0.0, 12.0 - abs(float(stock.get("change_pct") or 0) - change_midpoint) * 6.0)
        score += min(10.0, max(0.0, (float(stock.get("vol_ratio") or 0) - float(config["volume_ratio_min"])) * 12.0))
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
    def _minute_audit(
        payload: dict,
        now: datetime,
        benchmark_payload: dict | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = config or STRATEGY_CONFIG
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
            and 14 * 60 + 52 <= latest_at.hour * 60 + latest_at.minute <= 14 * 60 + 59
            and 0 <= (_local_naive(now) - latest_at).total_seconds() <= 10 * 60
        )
        conditions.append(_condition(
            "minute_freshness", "入场窗口分钟行情",
            "passed" if fresh else "unavailable",
            latest_at.isoformat(timespec="minutes") if latest_at else None,
            "当日14:52-14:59且延迟不超过10分钟", source="东方财富1分钟分时",
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
            "passed" if last_five_change is not None and last_five_change <= float(config["last_five_minute_change_max"]) else "failed" if last_five_change is not None else "unavailable",
            round(last_five_change, 3) if last_five_change is not None else None,
            f"最近5分钟涨幅<={config['last_five_minute_change_max']:g}%", source="东方财富1分钟分时",
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

        market_price = _number((latest or {}).get("close"))
        vwap = _number((latest or {}).get("average"))
        if vwap is None:
            total_volume = sum(_number(item.get("volume")) or 0 for item in bars)
            total_amount = sum(_number(item.get("amount")) or 0 for item in bars)
            vwap = total_amount / total_volume if total_volume > 0 and total_amount > 0 else None
        vwap_passed = bool(market_price is not None and vwap is not None and market_price >= vwap)
        if config.get("require_vwap_hold"):
            conditions.append(_condition(
                "vwap_hold", "尾盘守住VWAP",
                "passed" if vwap_passed else "failed" if market_price is not None and vwap is not None else "unavailable",
                {"price": round(market_price, 4) if market_price is not None else None, "vwap": round(vwap, 4) if vwap is not None else None},
                "最新价>=当日成交均价", source="东方财富1分钟分时",
            ))
        else:
            conditions.append(_condition("vwap_hold", "尾盘守住VWAP", "passed", "策略未启用", "可选确认因子", source="策略配置"))

        stock_return = None
        benchmark_return = None
        relative_strength = None
        stock_pre_close = _number(payload.get("pre_close"))
        if market_price is not None and stock_pre_close not in (None, 0):
            stock_return = (market_price / stock_pre_close - 1) * 100
        benchmark_bars = [
            item for item in (benchmark_payload or {}).get("bars") or []
            if str(item.get("bar_time") or "").startswith(today_text)
            and (latest_at is None or (_datetime(item.get("bar_time")) or datetime.max) <= latest_at)
        ]
        benchmark_close = _number((benchmark_bars[-1] if benchmark_bars else {}).get("close"))
        benchmark_pre_close = _number((benchmark_payload or {}).get("pre_close"))
        if benchmark_close is not None and benchmark_pre_close not in (None, 0):
            benchmark_return = (benchmark_close / benchmark_pre_close - 1) * 100
        if stock_return is not None and benchmark_return is not None:
            relative_strength = stock_return - benchmark_return
        relative_passed = bool(
            relative_strength is not None
            and relative_strength >= float(config["relative_strength_min_pct"])
        )
        if config.get("require_relative_strength"):
            conditions.append(_condition(
                "relative_strength", "分时强于上证指数",
                "passed" if relative_passed else "failed" if relative_strength is not None else "unavailable",
                {
                    "stock_return_pct": round(stock_return, 3) if stock_return is not None else None,
                    "benchmark_return_pct": round(benchmark_return, 3) if benchmark_return is not None else None,
                    "excess_pct": round(relative_strength, 3) if relative_strength is not None else None,
                },
                f"相对强度>={config['relative_strength_min_pct']:g}%", source="个股与上证指数1分钟分时",
            ))
        else:
            conditions.append(_condition("relative_strength", "分时强于上证指数", "passed", "策略未启用", "可选确认因子", source="策略配置"))

        day_highs = [value for item in bars for value in [_number(item.get("high"))] if value is not None]
        late_bars = [
            item for item in bars
            if ((_datetime(item.get("bar_time")) or datetime.min).hour * 60 + (_datetime(item.get("bar_time")) or datetime.min).minute) >= 14 * 60 + 50
        ]
        late_highs = [value for item in late_bars for value in [_number(item.get("high"))] if value is not None]
        day_high = max(day_highs, default=None)
        late_high = max(late_highs, default=None)
        pullback_pct = (
            (late_high - market_price) / late_high * 100
            if late_high not in (None, 0) and market_price is not None else None
        )
        late_confirmation = bool(
            day_high is not None and late_high is not None and market_price is not None
            and late_high >= day_high * 0.999
            and pullback_pct is not None and pullback_pct <= 1.0
            and (vwap is None or market_price >= vwap)
        )
        if config.get("require_late_high_retest"):
            conditions.append(_condition(
                "late_high_retest", "14:55新高回踩确认",
                "passed" if late_confirmation else "failed" if day_high is not None and late_high is not None and market_price is not None else "unavailable",
                {
                    "day_high": round(day_high, 4) if day_high is not None else None,
                    "late_high": round(late_high, 4) if late_high is not None else None,
                    "pullback_pct": round(pullback_pct, 3) if pullback_pct is not None else None,
                },
                "14:50后触及当日新高，回踩<=1%且不破VWAP", source="东方财富1分钟分时",
            ))
        else:
            conditions.append(_condition("late_high_retest", "14:55新高回踩确认", "passed", "策略未启用", "可选确认因子", source="策略配置"))

        failed = [item["label"] for item in conditions if item["status"] == "failed"]
        unavailable = [item["label"] for item in conditions if item["status"] == "unavailable"]
        return {
            "conditions": conditions,
            "minute_passed": not failed and not unavailable,
            "failed_reasons": failed,
            "unavailable_reasons": unavailable,
            "latest_bar_at": latest_at.isoformat(timespec="minutes") if latest_at else None,
            "market_price": market_price,
            "entry_price": (
                round(market_price * (1 + float(config["slippage_rate"])), 4)
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

    @staticmethod
    async def _loss_circuit(now: datetime) -> dict[str, Any]:
        async with async_session() as session:
            rows = (await session.execute(
                select(OvernightPosition)
                .where(OvernightPosition.status == "closed", OvernightPosition.pnl.is_not(None))
                .order_by(desc(OvernightPosition.exit_at), desc(OvernightPosition.id))
                .limit(3)
            )).scalars().all()
        consecutive_losses = 0
        for row in rows:
            if float(row.pnl or 0) >= 0:
                break
            consecutive_losses += 1
        warning = consecutive_losses >= 3
        return {
            # The system observes and warns; the user remains the final decision
            # maker. A losing streak must never hide current market candidates.
            "blocked": False,
            "warning": warning,
            "level": "high" if warning else "normal",
            "consecutive_losses": consecutive_losses,
            "reason": (
                "最近连续3笔模拟交易亏损，请降低仓位并复核策略；扫描和行情观察继续运行。"
                if warning else "未触发连续3笔亏损提醒"
            ),
        }

    @staticmethod
    async def _market_audit(
        market: dict[str, Any],
        now: datetime,
        config: dict[str, Any],
        *,
        data_date: date | None = None,
    ) -> dict[str, Any]:
        """Verify the optional broad-market MA20 gate from dated index data."""
        if not config.get("require_market_ma20"):
            return {"required": False, "above_ma20": None, "regime": "unrestricted"}
        current_index = _number(market.get("sh_index"))
        expected_date = data_date or now.date()
        if current_index is None or _date(market.get("data_date")) != expected_date:
            return {
                "required": True,
                "above_ma20": None,
                "regime": "unknown",
                "detail": "上证最新价或当日数据日期缺失",
            }
        try:
            history = await collector.fetch_shanghai_index_history(days=45)
        except Exception as exc:
            return {
                "required": True,
                "above_ma20": None,
                "regime": "unknown",
                "detail": f"上证指数历史数据不可用：{type(exc).__name__}",
            }
        closes = [
            _number(item.get("close"))
            for item in history or []
            if _number(item.get("close")) is not None
        ]
        if len(closes) < 19:
            return {
                "required": True,
                "above_ma20": None,
                "regime": "unknown",
                "detail": f"上证指数MA20历史覆盖不足（{len(closes)}/19）",
            }
        if abs(closes[-1] - current_index) > max(current_index * 0.03, 1.0):
            closes.append(current_index)
        else:
            closes[-1] = current_index
        ma20 = _average(closes[-20:])
        above = bool(ma20 is not None and current_index > ma20)
        recent_trend = []
        for index in range(max(19, len(closes) - 5), len(closes)):
            rolling = _average(closes[index - 19:index + 1])
            recent_trend.append(bool(rolling is not None and closes[index] > rolling))
        trend = bool(above and len(recent_trend) >= 5 and all(recent_trend))
        return {
            "required": True,
            "index": round(current_index, 2),
            "ma20": round(ma20, 2) if ma20 is not None else None,
            "above_ma20": above,
            "recent_5_above_ma20": recent_trend,
            "regime": "trend" if trend else "range_or_weak",
            "data_date": market.get("data_date"),
            "detail": "连续5个有效点高于各自MA20视为趋势市；否则按震荡/偏弱市处理",
        }

    async def _scan(
        self,
        run_id: int,
        stage: str,
        config: dict[str, Any],
        *,
        research_only: bool = False,
    ) -> None:
        now = shanghai_now()
        window_open, window_message = self._stage_window_status(stage, now)
        if not research_only and (now.weekday() >= 5 or not window_open):
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

        circuit = await self._loss_circuit(now)
        await self._set_progress(
            run_id,
            5,
            "正在读取最近完整缓存快照（研究观察模式）" if research_only else "正在获取完整A股实时横截面",
        )
        if research_only:
            snapshot_result, market_result = await asyncio.gather(
                load_quant_market_snapshot(),
                collector.fetch_market_turnover(),
                return_exceptions=True,
            )
        else:
            snapshot_result, market_result = await asyncio.gather(
                collector.fetch_quant_market_snapshot(),
                collector.fetch_market_turnover(),
                return_exceptions=True,
            )
        if isinstance(snapshot_result, Exception):
            await self._finish(
                run_id,
                status="unavailable",
                message="完整实时行情不可用，本轮不产生一夜持股信号",
                data_quality={"quote": "unavailable", "exception": type(snapshot_result).__name__},
                error=type(snapshot_result).__name__,
            )
            return
        snapshot = snapshot_result
        market = {} if isinstance(market_result, Exception) else market_result

        data_date = _date(snapshot.get("data_date"))
        realtime = bool(snapshot.get("is_realtime")) and data_date == now.date()
        stocks = list(snapshot.get("stocks") or [])
        if not stocks or (not research_only and (not snapshot.get("complete") or not realtime)):
            await self._finish(
                run_id,
                status="unavailable",
                message=(
                    "没有可用的完整市场缓存，本轮无法建立研究观察"
                    if research_only else "行情不是当日完整实时快照，本轮不产生一夜持股信号"
                ),
                data_date=data_date,
                is_realtime=realtime,
                data_quality={
                    "quote": "stale_or_incomplete",
                    "research_only": bool(research_only),
                    "complete": bool(snapshot.get("complete")),
                    "is_realtime": bool(snapshot.get("is_realtime")),
                    "data_date": snapshot.get("data_date"),
                    "missing_policy": "缓存行情只供查看，不用于尾盘模拟买入",
                },
                error="CacheSnapshotUnavailable" if research_only else "RealtimeSnapshotRequired",
            )
            return

        scan_date = data_date or now.date()
        market_audit = await self._market_audit(market, now, config, data_date=scan_date)
        prefiltered, prefilter_rejections = self._prefilter_diagnostics(stocks, config)
        market_change = _number(market.get("sh_change_pct")) if _date(market.get("data_date")) == scan_date else None
        market_circuit = {
            "triggered": bool(market_change is not None and market_change <= -2.0),
            "sh_change_pct": market_change,
            "resilient_candidates": len(prefiltered),
        }
        if market_circuit["triggered"] and not prefiltered:
            await self._finish(
                run_id,
                status="completed",
                message="空仓日：上证跌幅超过2%且没有通过条件的抗跌候选",
                data_date=data_date,
                is_realtime=True,
                scanned_count=len(stocks),
                data_quality={
                    "strategy_id": config["id"], "strategy": config,
                    "cash_day": True, "loss_circuit": circuit,
                    "market_circuit": market_circuit,
                },
            )
            return
        await self._set_progress(run_id, 24, f"静态条件通过 {len(prefiltered)} 只，正在核验日线与黑名单")
        codes = [str(item["code"]) for item in prefiltered]
        if research_only:
            # A cache observation should be fast and deterministic. External
            # same-day disclosure endpoints are reserved for executable scans;
            # their absence is recorded as pending evidence below.
            bars_result, announcements_result, appointments_result = await asyncio.gather(
                self._daily_bars(codes, scan_date),
                asyncio.sleep(0, result={"announcements": {}, "status": {}}),
                asyncio.sleep(0, result=({}, False)),
            )
        else:
            bars_result, announcements_result, appointments_result = await asyncio.gather(
                self._daily_bars(codes, scan_date),
                macro_policy_news_collector.get_stock_announcements_audit(codes, max_stocks=min(len(codes), 64)),
                self._appointment_map(codes, scan_date),
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
                today=scan_date,
                announcements=announcements.get(code),
                announcement_available=bool((announcement_status.get(code) or {}).get("available")),
                report_dates=appointment_map.get(code, []),
                report_available=report_available,
                market_audit=market_audit,
                config=config,
                allow_unavailable=research_only,
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
                "tail_qualified": False,
                "awaiting_auction": False,
                "auction_passed": None,
                "signal_at": (
                    f"{scan_date.isoformat()}T14:55"
                    if research_only else _local_naive(now).isoformat(timespec="minutes")
                ),
                "failed_reasons": audit["failed_reasons"],
                "unavailable_reasons": audit["unavailable_reasons"],
                "conditions": audit["conditions"],
                "ma": audit["ma"],
                "fib": audit["fib"],
                "minute": None,
            })

        if research_only:
            # Keep the research view useful even when the newly added hard
            # filters leave no executable signal. These are ranked near-misses,
            # never buy candidates; every failed rule remains visible.
            ranked_codes = {
                str(item.get("code"))
                for item in sorted(candidates, key=lambda item: item.get("score") or 0, reverse=True)[:12]
            }
            for candidate in candidates:
                candidate["research_qualified"] = str(candidate.get("code")) in ranked_codes
                candidate["research_status"] = (
                    "通过日线审计，等待实时分钟复核"
                    if candidate.get("daily_passed") else "近似候选，存在硬约束未通过"
                )

        daily_passed = [item for item in candidates if item["daily_passed"]]
        minute_payloads: list[dict] = []
        minute_covered = 0
        benchmark_payload: dict[str, Any] = {}
        if stage == "entry" and daily_passed and not research_only:
            await self._set_progress(run_id, 62, f"日线与黑名单通过 {len(daily_passed)} 只，正在复核1分钟分时")
            semaphore = asyncio.Semaphore(8)

            async def fetch_minutes(candidate: dict) -> tuple[str, dict | Exception]:
                async with semaphore:
                    try:
                        return candidate["code"], await collector.fetch_stock_minute_trends(candidate["code"], days=1)
                    except Exception as exc:
                        return candidate["code"], exc

            minute_results_raw, benchmark_result = await asyncio.gather(
                asyncio.gather(*(fetch_minutes(item) for item in daily_passed)),
                collector.fetch_shanghai_index_minute_trends(days=1),
                return_exceptions=True,
            )
            minute_results = [] if isinstance(minute_results_raw, Exception) else minute_results_raw
            benchmark_payload = {} if isinstance(benchmark_result, Exception) else benchmark_result
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
                minute_audit = self._minute_audit(payload, now, benchmark_payload, config)
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
        elif stage == "entry" and research_only:
            for candidate in daily_passed:
                candidate["research_only"] = True
                candidate["research_qualified"] = True
                candidate["unavailable_reasons"].append("等待交易日14:52-14:59实时分钟复核")
                candidate["conditions"].append(_condition(
                    "research_minute_boundary",
                    "实时分钟复核边界",
                    "unavailable",
                    None,
                    "仅在交易日14:52-14:59使用实时1分钟行情",
                    source="研究观察模式",
                    detail="当前结果来自最近完整缓存，只用于候选观察，不建立模拟持仓",
                ))
        elif stage == "preliminary":
            for candidate in daily_passed:
                candidate["unavailable_reasons"] = ["等待14:45-14:55最终分钟复核"]

        candidates.sort(key=lambda item: (bool(item.get("qualified")), item.get("score") or 0), reverse=True)
        failed_counts: dict[str, int] = {}
        unavailable_counts: dict[str, int] = {}
        for item in candidates:
            for reason in item.get("failed_reasons") or []:
                failed_counts[reason] = failed_counts.get(reason, 0) + 1
            for reason in item.get("unavailable_reasons") or []:
                unavailable_counts[reason] = unavailable_counts.get(reason, 0) + 1
        selected_count = 0
        tail_count = 0
        market_execution_gate: dict[str, Any] | None = None
        if stage == "entry":
            tail_count = sum(bool(item.get("qualified")) for item in candidates)
            for candidate in candidates:
                candidate["tail_qualified"] = bool(candidate.get("qualified"))
                if config.get("requires_auction_confirmation"):
                    candidate["awaiting_auction"] = bool(candidate.get("qualified"))
                    candidate["auction_passed"] = None
            if config.get("requires_auction_confirmation"):
                await self._set_progress(run_id, 88, f"尾盘条件核验完成，{tail_count}只候选等待次日09:25 AI竞价盯盘")
            else:
                await self._set_progress(run_id, 88, "分钟条件核验完成，正在执行仓位上限并建立100股模拟持仓")
                selected_count = await self._create_positions(run_id, candidates, now, config)
                market_execution_gate = next(
                    (
                        item.get("market_execution_gate")
                        for item in candidates
                        if isinstance(item.get("market_execution_gate"), dict)
                        and item["market_execution_gate"].get("blocked")
                    ),
                    None,
                )

        data_quality = {
            "strategy_id": config["id"],
            "strategy": config,
            "research_only": bool(research_only),
            "execution_allowed": not research_only,
            "research_candidate_count": sum(bool(item.get("research_qualified")) for item in candidates),
            "rejection_reasons": {
                "prefilter": prefilter_rejections,
                "daily_failed": failed_counts,
                "evidence_pending": unavailable_counts,
            },
            "cash_day": not any(item.get("qualified") for item in candidates),
            "research_cash_day": not any(item.get("research_qualified") for item in candidates),
            "loss_circuit": circuit,
            "market_circuit": market_circuit,
            "market_audit": market_audit,
            "quote": {
                "source": snapshot.get("source", "eastmoney"),
                "data_date": snapshot.get("data_date"),
                "is_realtime": realtime,
                "complete": bool(snapshot.get("complete")),
                "stocks": len(stocks),
            },
            "daily_history": {
                "covered": sum(
                    len(bars_by_code.get(code, [])) >= int(config["minimum_listing_sessions"])
                    for code in codes
                ),
                "requested": len(codes),
                "minimum_listing_sessions": int(config["minimum_listing_sessions"]),
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
                "benchmark_available": bool(benchmark_payload.get("bars")),
                "persisted_forward_only": True,
            },
            "auction": {
                "required": bool(config.get("requires_auction_confirmation")),
                "status": "pending_next_session" if config.get("requires_auction_confirmation") and stage == "entry" else "not_required",
                "agent": "AI竞价盯盘Agent" if config.get("ai_auction_monitor") else None,
            },
            "missing_policy": (
                "研究观察可展示待核验候选；真实执行仍要求所有强制字段和点时数据完整"
                if research_only else "任一强制字段缺失即不入选；不会以日线推断尾盘分钟条件"
            ),
            "backtest_limitation": "现有分钟缓存不是历史全市场点时样本，不能据此宣称十年精确回测或固定胜率",
        }
        if stage == "entry":
            data_quality["execution"] = {
                "rule_qualified_count": tail_count,
                "simulated_entry_count": selected_count,
                "blocked_by_market_gate": bool(market_execution_gate),
            }
        if market_execution_gate:
            data_quality["market_execution_gate"] = market_execution_gate
        if research_only:
            message = (
                f"缓存研究完成：扫描{len(stocks)}只，静态预筛{len(prefiltered)}只，"
                f"{sum(bool(item.get('research_qualified')) for item in candidates)}只进入观察候选；"
                "仅供研究，不能作为实时买入或模拟建仓信号"
            )
        elif stage == "preliminary":
            message = f"14:30预扫描完成：{len(daily_passed)}只等待14:55最终分钟复核"
        elif config.get("requires_auction_confirmation"):
            message = (
                f"尾盘候选完成：{sum(item.get('tail_qualified') for item in candidates)}只等待次日09:25 AI竞价盯盘"
                if any(item.get("tail_qualified") for item in candidates)
                else "空仓日：没有满足尾盘全部因子的标的，次日无需竞价确认"
            )
        else:
            if market_execution_gate:
                message = (
                    f"尾盘复核完成：{tail_count}只通过策略条件；"
                    f"{market_execution_gate['reason']}"
                )
            elif selected_count:
                message = f"尾盘复核完成：{tail_count}只通过策略条件，模拟买入{selected_count}只"
            elif tail_count:
                message = f"尾盘复核完成：{tail_count}只通过策略条件，仓位或持仓约束后未新增模拟仓位"
            else:
                message = "空仓日：尾盘复核后没有满足全部因子的标的"
        await self._finish(
            run_id,
            status="completed",
            message=message,
            data_date=scan_date,
            is_realtime=realtime,
            scanned_count=len(stocks),
            prefiltered_count=len(prefiltered),
            candidates=candidates[:120],
            data_quality=data_quality,
        )

    @staticmethod
    def _auction_audit(
        candidate: dict[str, Any],
        quote: dict[str, Any],
        now: datetime,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Audit the next-session call auction without substituting a cache quote."""
        quote_at = _datetime(quote.get("quote_at"))
        now_local = _local_naive(now)
        quote_local = _local_naive(quote_at) if quote_at else None
        quote_minute = quote_local.hour * 60 + quote_local.minute if quote_local else None
        quote_fresh = bool(
            quote.get("is_realtime")
            and quote_local
            and quote_local.date() == now_local.date()
            and quote_minute is not None
            and 9 * 60 + 24 <= quote_minute <= 9 * 60 + 27
            and 0 <= (now_local - quote_local).total_seconds() <= 5 * 60
        )
        auction_price = _number(quote.get("auction_price"))
        previous_close = _number(quote.get("previous_close")) or _number(candidate.get("previous_close"))
        high_open_pct = _number(quote.get("high_open_pct"))
        if high_open_pct is None and auction_price is not None and previous_close not in (None, 0):
            high_open_pct = (auction_price / previous_close - 1) * 100
        auction_ratio = _number(quote.get("auction_volume_ratio"))
        ratio_min = float(config.get("auction_volume_ratio_min") or 3.0)
        open_min, open_max = config.get("auction_high_open_pct") or [2.0, 5.0]
        source = str(quote.get("source") or "unavailable")
        conditions = [
            _condition(
                "auction_quote_fresh", "竞价实时数据", "passed" if quote_fresh else "unavailable",
                quote.get("quote_at"), "当日09:24-09:27且延迟不超过5分钟",
                source="东方财富/腾讯实时竞价报价",
                detail="缓存行情只供查看，不能替代竞价成交观察" if not quote_fresh else "报价时间和实时标记均通过",
            ),
            _condition(
                "auction_volume_ratio", "竞价量比", "passed" if auction_ratio is not None and auction_ratio > ratio_min else "failed" if auction_ratio is not None else "unavailable",
                round(auction_ratio, 3) if auction_ratio is not None else None,
                f">{ratio_min:g}（严格大于）", source=source,
            ),
            _condition(
                "auction_high_open_pct", "高开幅度", "passed" if high_open_pct is not None and open_min <= high_open_pct <= open_max else "failed" if high_open_pct is not None else "unavailable",
                round(high_open_pct, 3) if high_open_pct is not None else None,
                f"{open_min:g}%-{open_max:g}%（含边界）", source=source,
            ),
        ]
        failed = [item["label"] for item in conditions if item["status"] == "failed"]
        unavailable = [item["label"] for item in conditions if item["status"] == "unavailable"]
        passed = not failed and not unavailable
        if passed:
            decision = "通过：竞价量比和高开幅度同时满足，允许模拟买入100股"
        elif unavailable:
            decision = "放弃：竞价实时字段不完整或已过期，不使用缓存猜测"
        else:
            decision = f"放弃：{'、'.join(failed)}未同时满足"
        return {
            "conditions": conditions,
            "auction_passed": passed,
            "failed_reasons": failed,
            "unavailable_reasons": unavailable,
            "auction_price": auction_price,
            "previous_close": previous_close,
            "high_open_pct": round(high_open_pct, 3) if high_open_pct is not None else None,
            "auction_volume_ratio": round(auction_ratio, 3) if auction_ratio is not None else None,
            "quote_at": quote_local.isoformat(timespec="minutes") if quote_local else None,
            "source": source,
            "agent_decision": {
                "agent": "AI竞价盯盘Agent",
                "decision": "pass" if passed else "reject",
                "reason": decision,
                "checked_at": now_local.isoformat(timespec="seconds"),
                "rules": [f"竞价量比>{ratio_min:g}", f"高开{open_min:g}%-{open_max:g}%", "两个条件必须同时满足"],
            },
        }

    async def _auction(self, run_id: int, config: dict[str, Any]) -> None:
        now = shanghai_now()
        window_open, window_message = self._stage_window_status("auction", now)
        if now.weekday() >= 5 or not window_open:
            await self._finish(
                run_id,
                status="unavailable",
                message=window_message,
                data_date=now.date(),
                data_quality={
                    "strategy_id": config["id"], "strategy": config,
                    "auction": {"status": "outside_window", "agent": "AI竞价盯盘Agent"},
                    "missing_policy": "非竞价窗口不使用缓存行情建立仓位",
                },
                error="OutsideExecutionWindow",
            )
            return
        if not config.get("requires_auction_confirmation"):
            await self._finish(
                run_id,
                status="unavailable",
                message="当前策略未启用次日竞价确认，请切换到竞价确认版",
                data_date=now.date(),
                data_quality={
                    "strategy_id": config["id"], "strategy": config,
                    "auction": {"status": "not_required", "agent": "AI竞价盯盘Agent"},
                },
                error="AuctionConfirmationDisabled",
            )
            return

        circuit = await self._loss_circuit(now)
        async with async_session() as session:
            previous_runs = (await session.execute(
                select(OvernightStrategyRun)
                .where(
                    OvernightStrategyRun.stage == "entry",
                    OvernightStrategyRun.status.in_(["completed", "partial"]),
                    OvernightStrategyRun.data_date < now.date(),
                )
                .order_by(desc(OvernightStrategyRun.id))
                .limit(100)
            )).scalars().all()
        # The auction agent is independent from the active tail strategy. It
        # prefers its own prior-day candidates, then falls back to the latest
        # eligible candidates produced by any overnight tail strategy.
        dated_runs = [item for item in previous_runs if item.data_date is not None]
        latest_tail_date = max((item.data_date for item in dated_runs), default=None)
        eligible_runs = [item for item in dated_runs if item.data_date == latest_tail_date]
        eligible_runs.sort(
            key=lambda item: (
                str((item.data_quality or {}).get("strategy_id") or STRATEGY_CONFIG["id"]) != config["id"],
                -int(item.id),
            )
        )
        tail_candidates: list[dict[str, Any]] = []
        source_run_ids: list[int] = []
        seen_codes: set[str] = set()
        for source_run in eligible_runs:
            source_quality = source_run.data_quality if isinstance(source_run.data_quality, dict) else {}
            source_strategy_id = str(source_quality.get("strategy_id") or STRATEGY_CONFIG["id"])
            source_strategy = source_quality.get("strategy") if isinstance(source_quality.get("strategy"), dict) else {}
            source_candidates = [
                item for item in (source_run.candidates or [])
                if isinstance(item, dict) and bool(item.get("tail_qualified") or item.get("awaiting_auction"))
            ]
            if not source_candidates:
                continue
            source_run_ids.append(source_run.id)
            for item in source_candidates:
                code = str(item.get("code") or "")
                if not code or code in seen_codes:
                    continue
                candidate = deepcopy(item)
                candidate["source_strategy_id"] = source_strategy_id
                candidate["source_strategy_name"] = str(source_strategy.get("name") or source_strategy_id)
                candidate["source_entry_run_id"] = source_run.id
                tail_candidates.append(candidate)
                seen_codes.add(code)
        previous_run = eligible_runs[0] if eligible_runs else None
        pending_id = source_run_ids[0] if source_run_ids else None
        if not tail_candidates:
            await self._finish(
                run_id,
                status="completed",
                message="今日没有可供竞价确认的前一交易日尾盘候选",
                data_date=now.date(),
                data_quality={
                    "strategy_id": config["id"], "strategy": config,
                    "cash_day": True,
                    "auction": {"status": "no_pending_candidates", "agent": "AI竞价盯盘Agent"},
                    "pending_entry_run_id": pending_id,
                    "pending_entry_run_ids": source_run_ids,
                    "candidate_source_policy": "优先竞价版尾盘候选，否则读取其他一夜策略最新尾盘候选",
                },
            )
            return

        await self._set_progress(
            run_id,
            20,
            f"AI竞价盯盘Agent正在核验{len(tail_candidates)}只尾盘候选（来源策略可独立）",
        )
        codes = [str(item.get("code") or "") for item in tail_candidates if item.get("code")]
        try:
            quote_payload = await collector.fetch_stock_auction_quotes(codes)
        except Exception as exc:
            await self._finish(
                run_id,
                status="unavailable",
                message="竞价实时数据不可用，未使用缓存建立模拟仓位",
                data_date=now.date(),
                scanned_count=len(tail_candidates),
                prefiltered_count=len(tail_candidates),
                candidates=[
                    {
                        **deepcopy(item),
                        "qualified": False,
                        "auction_passed": False,
                        "awaiting_auction": False,
                        "unavailable_reasons": ["竞价实时行情源不可用"],
                    }
                    for item in tail_candidates
                ],
                data_quality={
                    "strategy_id": config["id"], "strategy": config,
                    "auction": {"status": "unavailable", "agent": "AI竞价盯盘Agent"},
                    "pending_entry_run_id": pending_id,
                    "pending_entry_run_ids": source_run_ids,
                    "candidate_source_policy": "优先竞价版尾盘候选，否则读取其他一夜策略最新尾盘候选",
                    "missing_policy": "竞价数据不可用时不以缓存推断买入",
                },
                error=type(exc).__name__,
            )
            return

        quotes = {str(item.get("code")): item for item in quote_payload.get("stocks") or []}
        candidates: list[dict[str, Any]] = []
        for item in tail_candidates:
            candidate = deepcopy(item)
            candidate["selected_for_entry"] = False
            candidate["awaiting_auction"] = False
            candidate["auction_passed"] = False
            candidate["qualified"] = False
            quote = quotes.get(str(candidate.get("code"))) or {}
            audit = self._auction_audit(candidate, quote, now, config)
            candidate["auction"] = {
                "auction_price": audit["auction_price"],
                "auction_volume": quote.get("auction_volume"),
                "auction_volume_ratio": audit["auction_volume_ratio"],
                "high_open_pct": audit["high_open_pct"],
                "previous_close": audit["previous_close"],
                "quote_at": audit["quote_at"],
                "source": audit["source"],
                "is_realtime": bool(quote.get("is_realtime")),
                "agent_decision": audit["agent_decision"],
            }
            candidate["conditions"] = [*(candidate.get("conditions") or []), *audit["conditions"]]
            candidate["failed_reasons"] = [
                *(candidate.get("failed_reasons") or []), *audit["failed_reasons"]
            ]
            candidate["unavailable_reasons"] = [
                *(candidate.get("unavailable_reasons") or []), *audit["unavailable_reasons"]
            ]
            candidate["auction_passed"] = bool(audit["auction_passed"])
            candidate["qualified"] = bool(candidate.get("tail_qualified") and audit["auction_passed"])
            candidates.append(candidate)

        candidates.sort(key=lambda item: (bool(item.get("qualified")), item.get("score") or 0), reverse=True)
        await self._set_progress(run_id, 82, "竞价双条件审计完成，正在按仓位上限模拟买入100股")
        selected_count = await self._create_positions(run_id, candidates, now, config)
        market_execution_gate = next(
            (
                item.get("market_execution_gate")
                for item in candidates
                if isinstance(item.get("market_execution_gate"), dict)
                and item["market_execution_gate"].get("blocked")
            ),
            None,
        )
        fresh_count = sum(bool((item.get("auction") or {}).get("is_realtime")) for item in candidates)
        passed_count = sum(bool(item.get("auction_passed")) for item in candidates)
        available = bool(quote_payload.get("stocks"))
        complete_realtime = bool(quote_payload.get("complete") and quote_payload.get("is_realtime"))
        status = "completed" if complete_realtime else "partial" if available else "unavailable"
        if not available:
            message = "竞价实时数据不可用，未使用缓存建立模拟仓位"
        elif market_execution_gate:
            message = (
                f"AI竞价盯盘Agent完成：{len(candidates)}只，双条件通过{passed_count}只；"
                f"{market_execution_gate['reason']}"
            )
        else:
            message = f"AI竞价盯盘Agent完成：{len(candidates)}只，双条件通过{passed_count}只，模拟买入{selected_count}只"
        execution_quality = {
            "rule_qualified_count": passed_count,
            "simulated_entry_count": selected_count,
            "blocked_by_market_gate": bool(market_execution_gate),
        }
        await self._finish(
            run_id,
            status=status,
            message=message,
            data_date=now.date(),
            is_realtime=complete_realtime,
            scanned_count=len(tail_candidates),
            prefiltered_count=len(tail_candidates),
            candidates=candidates[:120],
            data_quality={
                "strategy_id": config["id"], "strategy": config,
                "cash_day": not any(item.get("qualified") for item in candidates),
                "loss_circuit": circuit,
                "pending_entry_run_id": pending_id,
                "pending_entry_run_ids": source_run_ids,
                "candidate_source_policy": "优先竞价版尾盘候选，否则读取其他一夜策略最新尾盘候选",
                "execution": execution_quality,
                **({"market_execution_gate": market_execution_gate} if market_execution_gate else {}),
                "auction": {
                    "status": "completed" if complete_realtime else "partial" if available else "unavailable",
                    "agent": "AI竞价盯盘Agent",
                    "requested": len(codes),
                    "covered": fresh_count,
                    "passed": passed_count,
                    "rejected": sum(bool(item.get("auction_passed") is False) for item in candidates),
                    "source": quote_payload.get("source") or "unavailable",
                    "source_updated_at": quote_payload.get("source_updated_at"),
                    "field_coverage": quote_payload.get("field_coverage") or {},
                },
                "missing_policy": "竞价任何强制字段缺失即放弃该股；不使用缓存行情推断竞价通过",
                "backtest_limitation": "竞价过滤的样本必须按实时点时数据持续累积，小样本不代表未来胜率",
            },
            error=None if status != "unavailable" else "AuctionQuoteUnavailable",
        )

    async def _create_positions(
        self,
        run_id: int,
        candidates: list[dict],
        now: datetime,
        config: dict[str, Any],
    ) -> int:
        qualified = [item for item in candidates if item.get("qualified")]
        qualified.sort(key=lambda item: (item.get("score") or 0, item.get("code") or ""), reverse=True)
        if not qualified:
            return 0
        parsed_signal_dates = [_date(item.get("signal_at")) for item in qualified]
        signal_dates = {signal_date for signal_date in parsed_signal_dates if signal_date is not None}
        decision_date = next(iter(signal_dates)) if len(signal_dates) == 1 else now.date()
        cache_key = f"{WORKBENCH_CACHE_PREFIX}{decision_date.isoformat()}"
        async with async_session() as session:
            cache_row = await session.get(MarketDataCache, cache_key)
        gate = evaluate_market_execution_gate(
            dict(cache_row.payload) if cache_row and isinstance(cache_row.payload, dict) else None,
            decision_date=decision_date,
            strategy_id=str(config.get("id") or ""),
            requires_auction_confirmation=bool(config.get("requires_auction_confirmation")),
        )
        if len(signal_dates) != 1 or any(signal_date is None for signal_date in parsed_signal_dates):
            gate = {
                **gate,
                "available": False,
                "blocked": True,
                "reason": "候选信号日缺失或不一致，按失败关闭规则不建立新模拟仓位",
            }
        for candidate in qualified:
            candidate["market_execution_gate"] = gate
        if gate["blocked"]:
            for candidate in qualified:
                candidate["qualified"] = False
                candidate.setdefault("failed_reasons", []).append(str(gate["reason"]))
                candidate["selected_for_entry"] = False
            async with async_session() as session:
                run = await session.get(OvernightStrategyRun, run_id)
                if run is not None:
                    quality = run.data_quality if isinstance(run.data_quality, dict) else {}
                    run.data_quality = {**quality, "market_execution_gate": gate}
                    await session.commit()
            return 0
        async with async_session() as session:
            run = await session.get(OvernightStrategyRun, run_id)
            if run is not None:
                quality = run.data_quality if isinstance(run.data_quality, dict) else {}
                run.data_quality = {**quality, "market_execution_gate": gate}
            open_rows = (await session.execute(
                select(OvernightPosition).where(OvernightPosition.status == "open")
            )).scalars().all()
            open_codes = {row.stock_code for row in open_rows}
            occupied_pct = sum(float(row.allocated_pct or 0) for row in open_rows)
            remaining_slots = max(0, int(config["max_positions"]) - len(open_rows))
            selected = 0
            for candidate in qualified:
                if selected >= remaining_slots:
                    break
                if candidate["code"] in open_codes:
                    candidate["failed_reasons"].append("已有未平仓一夜持股模拟仓位")
                    candidate["qualified"] = False
                    continue
                auction = candidate.get("auction") or {}
                minute = candidate.get("minute") or {}
                if config.get("requires_auction_confirmation"):
                    raw_entry_price = _number(auction.get("auction_price"))
                    entry_at = _datetime(auction.get("quote_at"))
                    entry_price = (
                        raw_entry_price * (1 + float(config["slippage_rate"]))
                        if raw_entry_price is not None else None
                    )
                    entry_source = "call_auction"
                else:
                    raw_entry_price = _number(minute.get("market_price"))
                    entry_price = _number(minute.get("entry_price"))
                    entry_at = _datetime(minute.get("latest_bar_at"))
                    entry_source = "tail_minute"
                if entry_price is None or entry_at is None or (
                    config.get("requires_auction_confirmation") and not candidate.get("auction_passed")
                ):
                    candidate["qualified"] = False
                    candidate["unavailable_reasons"].append("有效模拟成交价")
                    continue
                entry_at = _local_naive(entry_at)
                entry_price = round(entry_price, 4)
                cost = entry_price * int(config["shares_per_position"])
                allocated_pct = cost / float(config["reference_capital"]) * 100
                if allocated_pct > float(config["max_position_pct"]):
                    candidate["qualified"] = False
                    candidate["failed_reasons"].append(
                        f"100股成本超过参考资金{float(config['max_position_pct']):g}%单股上限"
                    )
                    continue
                if occupied_pct + allocated_pct > float(config["max_total_position_pct"]):
                    candidate["qualified"] = False
                    candidate["failed_reasons"].append(
                        f"总仓位将超过{float(config['max_total_position_pct']):g}%"
                    )
                    continue
                session.add(OvernightPosition(
                    entry_run_id=run_id,
                    stock_code=candidate["code"],
                    stock_name=candidate["name"],
                    sector=candidate.get("sector"),
                    status="open",
                    shares=int(config["shares_per_position"]),
                    signal_at=_local_naive(_datetime(candidate.get("signal_at")) or now),
                    entry_at=entry_at,
                    entry_price=entry_price,
                    previous_close=_number(candidate.get("previous_close")),
                    reference_capital=float(config["reference_capital"]),
                    allocated_pct=allocated_pct,
                    audit={
                        "strategy_id": config["id"],
                        "strategy": config["name"],
                        "strategy_config": config,
                        "score": candidate.get("score"),
                        "conditions": candidate.get("conditions") or [],
                        "entry_market_price": raw_entry_price,
                        "entry_source": entry_source,
                        "auction_confirmed": bool(config.get("requires_auction_confirmation")),
                        "auction": auction if config.get("requires_auction_confirmation") else None,
                        "entry_slippage_rate": config["slippage_rate"],
                        "market_execution_gate": gate,
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
        audit = position.audit if isinstance(position.audit, dict) else {}
        strategy_config = audit.get("strategy_config") if isinstance(audit.get("strategy_config"), dict) else STRATEGY_CONFIG
        commission_rate = float(strategy_config.get("commission_rate", STRATEGY_CONFIG["commission_rate"]))
        slippage_rate = float(strategy_config.get("slippage_rate", STRATEGY_CONFIG["slippage_rate"]))
        stamp_tax_rate = float(strategy_config.get("stamp_tax_rate", STRATEGY_CONFIG["stamp_tax_rate"]))
        take_profit_pct = float(strategy_config.get("take_profit_pct", STRATEGY_CONFIG["take_profit_pct"]))
        stop_loss_pct = float(strategy_config.get("stop_loss_pct", STRATEGY_CONFIG["stop_loss_pct"]))
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
            target = open_price * (1 + take_profit_pct / 100) if gap >= 1 else position.entry_price
            target_reason = (
                f"高开1%-3%后冲高{take_profit_pct:g}%，按策略止盈"
                if gap >= 1 else "平开后回到成本线，按计划离场"
            )
            stop_price = position.entry_price * (1 - stop_loss_pct / 100)
            for item in bars:
                item_time = _datetime(item.get("bar_time"))
                if (_number(item.get("low")) or math.inf) <= stop_price:
                    market_price, exit_time, reason = stop_price, item_time, f"次日跌至入场价-{stop_loss_pct:g}%，按止损纪律清仓"
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
        execution_price = market_price * (1 - slippage_rate)
        shares = int(position.shares or 100)
        buy_amount = position.entry_price * shares
        sell_amount = execution_price * shares
        fees = (
            buy_amount * commission_rate
            + sell_amount * (commission_rate + stamp_tax_rate)
        )
        pnl = sell_amount - buy_amount - fees
        return {
            "ready": True,
            "exit_at": exit_time,
            "market_price": market_price,
            "exit_price": round(execution_price, 4),
            "reason": reason,
            "opening_gap_pct": round(gap, 3),
            "take_profit_pct": take_profit_pct,
            "stop_loss_pct": stop_loss_pct,
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
        audit = row.audit if isinstance(row.audit, dict) else {}
        strategy_config = audit.get("strategy_config") if isinstance(audit.get("strategy_config"), dict) else STRATEGY_CONFIG
        commission_rate = float(strategy_config.get("commission_rate", STRATEGY_CONFIG["commission_rate"]))
        slippage_rate = float(strategy_config.get("slippage_rate", STRATEGY_CONFIG["slippage_rate"]))
        stamp_tax_rate = float(strategy_config.get("stamp_tax_rate", STRATEGY_CONFIG["stamp_tax_rate"]))
        current_price = row.exit_price if row.status == "closed" else _number(quote.get("price"))
        shares = int(row.shares or 100)
        buy_amount = row.entry_price * shares
        if row.status == "closed":
            pnl = row.pnl
            pnl_pct = row.pnl_pct
        elif current_price is not None:
            sell_amount = current_price * (1 - slippage_rate) * shares
            fees = (
                buy_amount * commission_rate
                + sell_amount * (commission_rate + stamp_tax_rate)
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
            "strategy_tag": str((row.audit or {}).get("strategy") or STRATEGY_CONFIG["name"]),
            "strategy_id": str((row.audit or {}).get("strategy_id") or STRATEGY_CONFIG["id"]),
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
            "audit": audit,
        }

    async def get_run(self, run_id: int) -> dict[str, Any]:
        async with async_session() as session:
            row = await session.get(OvernightStrategyRun, run_id)
        if row is None:
            raise LookupError("一夜持股运行记录不存在")
        return _run_view(row) or {}

    async def compare_strategies(self, strategy_ids: list[str]) -> dict[str, Any]:
        requested = list(dict.fromkeys(str(item) for item in strategy_ids if item))
        if not 2 <= len(requested) <= 5:
            raise ValueError("请选择2到5个一夜持股策略进行对比")
        _, strategies = await self._strategy_store()
        by_id = {item["id"]: item for item in strategies}
        missing = [item for item in requested if item not in by_id]
        if missing:
            raise ValueError(f"策略不存在: {','.join(missing)}")
        async with async_session() as session:
            runs = (await session.execute(
                select(OvernightStrategyRun)
                .where(
                    OvernightStrategyRun.stage.in_(["entry", "auction"]),
                    OvernightStrategyRun.status.in_(["completed", "partial"]),
                )
                .order_by(OvernightStrategyRun.id)
            )).scalars().all()
            positions = (await session.execute(
                select(OvernightPosition).order_by(OvernightPosition.exit_at, OvernightPosition.id)
            )).scalars().all()
        positions_by_run: dict[int, list[OvernightPosition]] = {}
        for position in positions:
            positions_by_run.setdefault(position.entry_run_id, []).append(position)

        comparisons = []
        for strategy_id in requested:
            strategy_runs = [
                row for row in runs
                if str((row.data_quality or {}).get("strategy_id") or STRATEGY_CONFIG["id"]) == strategy_id
            ]
            samples = [item for row in strategy_runs for item in positions_by_run.get(row.id, [])]
            closed = [item for item in samples if item.status == "closed" and item.pnl is not None]
            cumulative = 0.0
            peak = 0.0
            max_drawdown = 0.0
            for item in closed:
                cumulative += float(item.pnl or 0)
                peak = max(peak, cumulative)
                max_drawdown = min(max_drawdown, cumulative - peak)
            total_cost = sum(float(item.entry_price) * int(item.shares or 100) for item in closed)
            total_pnl = sum(float(item.pnl or 0) for item in closed)
            comparisons.append({
                "strategy_id": strategy_id,
                "name": by_id[strategy_id]["name"],
                "config": by_id[strategy_id],
                "run_count": len(strategy_runs),
                "entry_run_count": sum(row.stage == "entry" for row in strategy_runs),
                "auction_run_count": sum(row.stage == "auction" for row in strategy_runs),
                "cash_days": len({
                    row.data_date.isoformat() for row in strategy_runs
                    if row.data_date and bool((row.data_quality or {}).get("cash_day"))
                }),
                "positions": len(samples),
                "closed_positions": len(closed),
                "wins": sum(float(item.pnl or 0) > 0 for item in closed),
                "losses": sum(float(item.pnl or 0) < 0 for item in closed),
                "win_rate": round(sum(float(item.pnl or 0) > 0 for item in closed) / len(closed) * 100, 2) if closed else None,
                "total_pnl": round(total_pnl, 2) if closed else None,
                "return_pct": round(total_pnl / total_cost * 100, 3) if total_cost else None,
                "average_pnl": round(total_pnl / len(closed), 2) if closed else None,
                "auction_confirmed_positions": sum(
                    bool((item.audit or {}).get("auction_confirmed")) for item in samples
                ),
                "max_drawdown_amount": round(max_drawdown, 2) if closed else None,
                "sample_from": strategy_runs[0].data_date.isoformat() if strategy_runs and strategy_runs[0].data_date else None,
                "sample_to": strategy_runs[-1].data_date.isoformat() if strategy_runs and strategy_runs[-1].data_date else None,
            })
        return {
            "method": "forward_point_in_time_comparison",
            "comparisons": comparisons,
            "sample_complete": all(item["closed_positions"] >= 30 for item in comparisons),
            "limitation": "这是上线后真实点时信号的前向对比，不是伪造的多年历史回测；每个策略至少完成30笔后再比较胜率。",
        }

    async def dashboard(self) -> dict[str, Any]:
        strategy_store = await self.list_strategies()
        active_strategy = next(
            item for item in strategy_store["strategies"]
            if item["id"] == strategy_store["active_id"]
        )
        auction_strategy = next(
            (item for item in strategy_store["strategies"] if item["id"] == AUCTION_STRATEGY_CONFIG["id"]),
            {**AUCTION_STRATEGY_CONFIG},
        )
        async with async_session() as session:
            runs = (await session.execute(
                select(OvernightStrategyRun).order_by(desc(OvernightStrategyRun.id)).limit(100)
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
        def strategy_id_for(row: OvernightStrategyRun) -> str:
            return str((row.data_quality or {}).get("strategy_id") or STRATEGY_CONFIG["id"])

        def belongs_to_active(row: OvernightStrategyRun) -> bool:
            return strategy_id_for(row) == active_strategy["id"]

        latest_entry = next((row for row in runs if belongs_to_active(row) and row.stage == "entry" and row.status not in {"queued", "running"}), None)
        latest_auction = next((row for row in runs if strategy_id_for(row) == AUCTION_STRATEGY_CONFIG["id"] and row.stage == "auction" and row.status not in {"queued", "running"}), None)
        latest_preliminary = next((row for row in runs if belongs_to_active(row) and row.stage == "preliminary" and row.status not in {"queued", "running"}), None)
        latest_research = next((row for row in runs if bool((row.data_quality or {}).get("research_only")) and row.stage in {"preliminary", "entry"} and row.status not in {"queued", "running"}), None)
        completed = [item for item in position_views if item["status"] == "closed" and item["pnl"] is not None]
        all_priced = [item for item in position_views if item["pnl"] is not None]
        total_cost = sum(item["cost_value"] for item in all_priced)
        total_pnl = sum(item["pnl"] for item in all_priced)
        loss_alert = await self._loss_circuit(shanghai_now())
        return {
            "updated_at": shanghai_now().isoformat(),
            "strategy": {
                **active_strategy,
                "enabled": True,
                "execution": "研究用100股模拟成交，不连接券商",
                "selection_limit": "按可审计综合分取前5只",
            },
            "strategy_store": strategy_store,
            "auction_strategy": {
                **auction_strategy,
                "enabled": True,
                "independent": True,
                "agent": "AI竞价盯盘Agent",
            },
            "active_run": _run_view(active),
            "latest_entry_run": _run_view(latest_entry),
            "latest_auction_run": _run_view(latest_auction),
            "latest_preliminary_run": _run_view(latest_preliminary),
            "latest_research_run": _run_view(latest_research),
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
            "loss_alert": loss_alert,
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
                "available": True,
                "grade": "真实前向样本",
                "reason": "可比较不同参数上线后的真实点时运行；尚无全市场历史点时分钟快照，因此不宣称多年精确回测。",
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
        latest = dashboard.get("latest_auction_run") or dashboard.get("latest_entry_run") or {}
        strategy = dashboard.get("strategy") or {}
        auction_strategy = dashboard.get("auction_strategy") or AUCTION_STRATEGY_CONFIG
        return {
            "tag": str(strategy.get("name") or STRATEGY_CONFIG["name"]),
            "auction_agent": {
                "name": "AI竞价盯盘Agent",
                "strategy_id": auction_strategy.get("id"),
                "strategy_name": auction_strategy.get("name"),
                "independent": True,
            },
            "schedule": "交易日14:30预扫，14:55尾盘复核；竞价确认版次日09:24-09:27由AI竞价盯盘Agent核验，10:00前退出",
            "run": latest,
            "tail_run": dashboard.get("latest_entry_run") or {},
            "auction_run": dashboard.get("latest_auction_run") or {},
            "positions": dashboard.get("open_positions") or [],
            "recent_closed": (dashboard.get("closed_positions") or [])[:10],
            "performance": dashboard.get("performance") or {},
            "data_quality": (latest.get("data_quality") or {}) if isinstance(latest, dict) else {},
        }


overnight_strategy_service = OvernightStrategyService()