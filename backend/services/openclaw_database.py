"""Allowlisted, read-only access to the application's persisted datasets.

OpenClaw needs the same historical evidence that the web application uses, but
it must not receive an arbitrary SQL executor.  This module therefore builds
ORM queries only from the dataset, filter, field, and sort allowlists below.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import asc, desc, func, select

from database import async_session
from models import (
    MarketSentimentDaily,
    MarketFundFlowDaily,
    OvernightPosition,
    OvernightStrategyRun,
    QuantStrategy,
    SecurityMaster,
    SecurityStatusEvent,
    StockDailyBar,
    StockSelectionRun,
    StockValuationHistory,
)
from services.data_collector import normalize_stock_code


COMMON_ARGUMENTS = {
    "dataset",
    "fields",
    "limit",
    "offset",
    "sort_by",
    "sort_order",
    "stock_code",
    "start_date",
    "end_date",
    "market",
    "source",
    "status",
    "stage",
    "mode",
    "risk_profile",
    "is_realtime",
    "is_builtin",
    "strategy_id",
    "sync_status",
}


DATASET_DEFINITIONS: dict[str, dict[str, Any]] = {
    "stock_daily_bars": {
        "label": "A股日线缓存",
        "model": StockDailyBar,
        "fields": (
            "id", "stock_code", "stock_name", "market", "trade_date",
            "open_price", "close_price", "high_price", "low_price", "volume",
            "amount", "amplitude", "change_pct", "change_amount", "turnover",
            "source", "updated_at",
        ),
        "filters": {"stock_code", "market", "source", "start_date", "end_date"},
        "date_field": "trade_date",
        "date_kind": "date",
        "sort_fields": {"id", "stock_code", "trade_date", "change_pct", "amount", "updated_at"},
        "default_sort": "trade_date",
    },
    "overnight_runs": {
        "label": "一夜持股运行记录",
        "model": OvernightStrategyRun,
        "fields": (
            "id", "stage", "trigger", "status", "progress", "message", "data_date",
            "is_realtime", "scanned_count", "prefiltered_count", "qualified_count",
            "candidates", "data_quality", "error", "started_at", "finished_at", "created_at",
        ),
        "filters": {"stage", "status", "start_date", "end_date", "is_realtime"},
        "date_field": "data_date",
        "date_kind": "date",
        "sort_fields": {"id", "stage", "status", "progress", "data_date", "created_at"},
        "default_sort": "created_at",
    },
    "overnight_positions": {
        "label": "一夜持股模拟持仓",
        "model": OvernightPosition,
        "fields": (
            "id", "entry_run_id", "stock_code", "stock_name", "sector", "status", "shares",
            "signal_at", "entry_at", "entry_price", "previous_close", "reference_capital",
            "allocated_pct", "exit_at", "exit_price", "exit_reason", "pnl", "pnl_pct",
            "audit", "created_at", "updated_at",
        ),
        "filters": {"stock_code", "status", "start_date", "end_date"},
        "date_field": "entry_at",
        "date_kind": "datetime",
        "sort_fields": {"id", "stock_code", "status", "entry_at", "exit_at", "pnl", "pnl_pct"},
        "default_sort": "entry_at",
    },
    "stock_selection_runs": {
        "label": "智能选股运行记录",
        "model": StockSelectionRun,
        "fields": (
            "id", "mode", "risk_profile", "candidate_count", "selected_count", "source",
            "data_date", "is_realtime", "result", "created_at",
        ),
        "filters": {"mode", "risk_profile", "source", "start_date", "end_date", "is_realtime"},
        "date_field": "data_date",
        "date_kind": "date",
        "sort_fields": {"id", "mode", "risk_profile", "candidate_count", "selected_count", "data_date", "created_at"},
        "default_sort": "created_at",
    },
    "quant_strategies": {
        "label": "量化策略配置",
        "model": QuantStrategy,
        "fields": ("id", "name", "is_builtin", "payload", "created_at", "updated_at"),
        "filters": {"strategy_id", "is_builtin"},
        "date_field": "updated_at",
        "date_kind": "datetime_text",
        "sort_fields": {"id", "name", "is_builtin", "created_at", "updated_at"},
        "default_sort": "updated_at",
    },
    "market_flow": {
        "label": "A股大盘资金流缓存",
        "model": MarketFundFlowDaily,
        "fields": (
            "id", "trade_date", "market", "main_net_inflow", "super_large_net_inflow",
            "large_net_inflow", "medium_net_inflow", "small_net_inflow", "north_net_inflow",
            "created_at",
        ),
        "filters": {"market", "start_date", "end_date"},
        "date_field": "trade_date",
        "date_kind": "date",
        "sort_fields": {"id", "trade_date", "market", "main_net_inflow", "north_net_inflow", "created_at"},
        "default_sort": "trade_date",
    },
    "market_sentiment_daily": {
        "label": "A股市场宽度与涨停情绪历史",
        "model": MarketSentimentDaily,
        "fields": (
            "trade_date", "up_count", "down_count", "flat_count", "stock_count",
            "market_amount", "amount_count", "average_turnover", "turnover_count", "limit_up_count", "limit_down_count",
            "failed_limit_count", "failed_limit_rate", "max_streak_height", "source", "updated_at",
        ),
        "filters": {"source", "start_date", "end_date"},
        "date_field": "trade_date", "date_kind": "date",
        "sort_fields": {"trade_date", "market_amount", "failed_limit_rate", "max_streak_height", "updated_at"},
        "default_sort": "trade_date",
    },
    "security_master": {
        "label": "A股证券主表（含历史非活跃证券）",
        "model": SecurityMaster,
        "fields": (
            "stock_code", "stock_name", "exchange", "list_date", "delist_date", "status",
            "is_currently_listed", "date_quality", "source", "source_updated_at", "updated_at",
        ),
        "filters": {"stock_code", "status", "source", "start_date", "end_date"},
        "date_field": "list_date", "date_kind": "date",
        "sort_fields": {"stock_code", "list_date", "delist_date", "status", "updated_at"},
        "default_sort": "stock_code",
    },
    "security_status_events": {
        "label": "A股上市、停牌、退市状态事件",
        "model": SecurityStatusEvent,
        "fields": (
            "id", "stock_code", "stock_name", "change_date", "change_type", "details", "source", "updated_at",
        ),
        "filters": {"stock_code", "source", "start_date", "end_date"},
        "date_field": "change_date", "date_kind": "date",
        "sort_fields": {"id", "stock_code", "change_date", "change_type", "updated_at"},
        "default_sort": "change_date",
    },
    "stock_valuation_history": {
        "label": "个股三年PE历史与分位",
        "model": StockValuationHistory,
        "fields": (
            "stock_code", "stock_name", "history", "requested_start", "history_start", "history_end",
            "sample_count", "positive_sample_count", "latest_pe_ttm", "pe_percentile_3y",
            "sync_status", "source", "updated_at",
        ),
        "filters": {"stock_code", "sync_status", "source", "start_date", "end_date"},
        "date_field": "history_end", "date_kind": "date",
        "sort_fields": {"stock_code", "history_end", "sample_count", "pe_percentile_3y", "updated_at"},
        "default_sort": "history_end",
        "default_fields": (
            "stock_code", "stock_name", "requested_start", "history_start", "history_end",
            "sample_count", "positive_sample_count", "latest_pe_ttm", "pe_percentile_3y",
            "sync_status", "source", "updated_at",
        ),
    },
}


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe(item) for item in value]
    return str(value)


def _bounded_int(arguments: dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(arguments.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} 必须是整数") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} 必须在 {minimum} 到 {maximum} 之间")
    return value


def _parse_date(value: Any, key: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} 必须是YYYY-MM-DD日期") from exc


def _parse_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise ValueError(f"{key} 必须是布尔值")


def _date_condition(column: Any, kind: str, value: date, *, end: bool = False) -> Any:
    if kind == "date":
        return column <= value if end else column >= value
    if kind == "datetime":
        boundary = datetime.combine(value + (timedelta(days=1) if end else timedelta(0)), time.min)
        return column < boundary if end else column >= boundary
    # QuantStrategy stores ISO timestamps in text columns for compatibility.
    boundary = (value + timedelta(days=1) if end else value).isoformat()
    return column < boundary if end else column >= boundary


def _date_bounds(value: Any) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    serialized = _safe(value)
    if not isinstance(serialized, str):
        return None, None
    return serialized[:10], serialized


async def query_system_database(arguments: dict[str, Any]) -> dict[str, Any]:
    """Query one persisted dataset without accepting SQL or ORM expressions."""
    if not isinstance(arguments, dict):
        raise ValueError("arguments 必须是对象")
    unknown = sorted(set(arguments) - COMMON_ARGUMENTS)
    if unknown:
        raise ValueError(f"不支持的数据库查询参数: {','.join(unknown)}")

    dataset = str(arguments.get("dataset") or "").strip().lower()
    definition = DATASET_DEFINITIONS.get(dataset)
    if definition is None:
        raise ValueError(f"dataset 必须是: {','.join(DATASET_DEFINITIONS)}")

    invalid_filters = sorted(
        key for key in arguments
        if key in {"stock_code", "start_date", "end_date", "market", "source", "status", "stage", "mode", "risk_profile", "sync_status", "is_realtime", "is_builtin", "strategy_id"}
        and key not in definition["filters"]
    )
    if invalid_filters:
        raise ValueError(f"{dataset} 不支持过滤条件: {','.join(invalid_filters)}")

    requested_fields = arguments.get("fields")
    if requested_fields is None:
        fields = list(definition.get("default_fields") or definition["fields"])
    else:
        if not isinstance(requested_fields, list) or not requested_fields:
            raise ValueError("fields 必须是非空字段数组")
        if len(requested_fields) > 40 or any(not isinstance(item, str) for item in requested_fields):
            raise ValueError("fields 最多包含40个字符串字段")
        fields = list(dict.fromkeys(requested_fields))
        invalid_fields = sorted(set(fields) - set(definition["fields"]))
        if invalid_fields:
            raise ValueError(f"{dataset} 不允许读取字段: {','.join(invalid_fields)}")

    limit = _bounded_int(arguments, "limit", 100, 1, 200)
    offset = _bounded_int(arguments, "offset", 0, 0, 10_000)
    sort_by = str(arguments.get("sort_by") or definition["default_sort"]).strip()
    if sort_by not in definition["sort_fields"]:
        raise ValueError(f"{dataset} 不允许按 {sort_by} 排序")
    sort_order = str(arguments.get("sort_order") or "desc").strip().lower()
    if sort_order not in {"asc", "desc"}:
        raise ValueError("sort_order 仅支持 asc 或 desc")

    model = definition["model"]
    conditions: list[Any] = []
    if "stock_code" in definition["filters"] and arguments.get("stock_code") not in (None, ""):
        conditions.append(model.stock_code == normalize_stock_code(arguments["stock_code"]))
    for key in ("market", "source", "status", "stage", "mode", "risk_profile", "sync_status"):
        if key in definition["filters"] and arguments.get(key) not in (None, ""):
            value = str(arguments[key]).strip()
            if len(value) > 100:
                raise ValueError(f"{key} 不能超过100个字符")
            conditions.append(getattr(model, key) == value)
    for key in ("is_realtime", "is_builtin"):
        if key in definition["filters"] and arguments.get(key) is not None:
            conditions.append(getattr(model, key) == _parse_bool(arguments[key], key))
    if "strategy_id" in definition["filters"] and arguments.get("strategy_id") not in (None, ""):
        strategy_id = str(arguments["strategy_id"]).strip()
        if not 1 <= len(strategy_id) <= 40:
            raise ValueError("strategy_id 长度必须在1到40之间")
        conditions.append(model.id == strategy_id)

    date_field = definition["date_field"]
    date_column = getattr(model, date_field)
    start_date = _parse_date(arguments["start_date"], "start_date") if arguments.get("start_date") is not None else None
    end_date = _parse_date(arguments["end_date"], "end_date") if arguments.get("end_date") is not None else None
    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date 不能晚于 end_date")
    if start_date:
        conditions.append(_date_condition(date_column, definition["date_kind"], start_date))
    if end_date:
        conditions.append(_date_condition(date_column, definition["date_kind"], end_date, end=True))

    sort_column = getattr(model, sort_by)
    order_clauses = [asc(sort_column) if sort_order == "asc" else desc(sort_column)]
    # Add a stable secondary key so pagination does not reshuffle equal dates.
    if sort_by != "id" and hasattr(model, "id"):
        order_clauses.append(desc(model.id))

    async with async_session() as session:
        count = int((await session.execute(
            select(func.count()).select_from(model).where(*conditions)
        )).scalar_one())
        rows = list((await session.execute(
            select(model).where(*conditions).order_by(*order_clauses).offset(offset).limit(limit)
        )).scalars().all())
        bounds = (await session.execute(
            select(func.min(date_column), func.max(date_column)).select_from(model).where(*conditions)
        )).one()

    records = [
        {field: _safe(getattr(row, field, None)) for field in fields}
        for row in rows
    ]
    bound_start, bound_end = _date_bounds(bounds[0]), _date_bounds(bounds[1])
    sources = sorted({str(getattr(row, "source")) for row in rows if getattr(row, "source", None)})
    realtime_values = {bool(getattr(row, "is_realtime")) for row in rows if hasattr(row, "is_realtime")}
    latest_data_date = bound_end[0] if bound_end[0] else None
    return {
        "read_only": True,
        "database": "application_database",
        "dataset": dataset,
        "dataset_label": definition["label"],
        "filters": {
            key: _safe(arguments[key])
            for key in definition["filters"]
            if key in arguments
        },
        "fields": fields,
        "sort": {"by": sort_by, "order": sort_order},
        "pagination": {
            "limit": limit,
            "offset": offset,
            "returned": len(records),
            "total": count,
            "has_more": offset + len(records) < count,
        },
        "data_range": {"start": bound_start[0], "end": bound_end[0]},
        "data_date": latest_data_date,
        "source": sources or "application_database",
        "is_realtime": bool(realtime_values) and realtime_values == {True},
        "mixed_realtime": len(realtime_values) > 1,
        "cache_used": True,
        "records": records,
    }
