"""Unified, date-labelled market replay workspace.

The service combines compact user-imported history, existing daily database
tables and on-demand NumCat responses.  Vendor payloads are normalized and
bounded before use; raw responses are never written to PostgreSQL.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import Integer, desc, func, select

from config import settings
from database import async_session
from market_data.numcat.extended_provider import numcat_extended_provider
from market_data.numcat.market_provider import numcat_market_provider
from models import (
    ConceptFundFlowDaily,
    IndustryFundFlowDaily,
    MarketBoard,
    MarketEmotionReplaySnapshot,
    MarketSentimentDaily,
    StockDailyBar,
)
from services.ai_service import ai_service, clean_ai_text
from services.data_collector import collector, is_a_share_market_session, shanghai_now


REPLAY_VERSION = "MARKET_REPLAY_V1_0"
PROVIDER_CACHE_DAYS = 80
SECTION_ROW_LIMIT = 80

EMOTION_FIELDS = (
    "up_count", "down_count", "flat_count", "stock_count",
    "up_7pct_count", "up_3_to_7pct_count", "up_0_to_3pct_count",
    "down_0_to_3pct_count", "down_3_to_7pct_count", "down_7pct_count",
    "deep_retrace_count", "market_amount", "market_amount_change",
    "market_amount_forecast", "market_amount_forecast_change_pct",
    "market_amount_forecast_change", "main_net_inflow",
    "auction_main_net_inflow", "touched_limit_up_count", "limit_up_count",
    "yesterday_limit_up_count", "promotion_candidate_count",
    "one_price_limit_up_count", "failed_limit_count", "failed_limit_rate",
    "limit_down_count", "max_streak_height", "first_board_count",
    "second_board_count", "third_board_count", "second_board_or_higher_count",
    "third_board_or_higher_count", "fourth_board_or_higher_count",
    "promotion_rate_1_to_2", "promotion_rate", "promotion_rate_2_plus",
    "limit_up_order_amount", "limit_up_order_amount_0920",
    "limit_up_order_amount_after_0920", "limit_up_order_count",
    "overnight_order_amount", "order_amount_0920", "order_amount_0925",
    "order_count_0925", "first_board_amount", "second_board_amount",
    "third_board_amount", "second_board_or_higher_amount",
    "third_board_or_higher_amount", "fourth_board_or_higher_amount",
)


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "--") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: Any) -> int | None:
    parsed = _number(value)
    return int(parsed) if parsed is not None else None


def _date_value(value: Any) -> date | None:
    text = str(value or "").strip()
    if len(text) >= 8 and text[:8].isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        return None


def _date_text(value: Any) -> str | None:
    parsed = _date_value(value)
    return parsed.isoformat() if parsed else None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _source_join(values: Iterable[str | None]) -> str:
    output: list[str] = []
    for value in values:
        # Sources are persisted as a compact ``+``-joined value. Split an
        # existing value before merging so repeated refreshes cannot grow the
        # provenance string indefinitely.
        parts = str(value or "").split("+")
        for part in parts:
            text = part.strip()
            if text == "derived_from_verified_counts":
                continue
            if text and text != "unavailable" and text not in output:
                output.append(text)
    return "+".join(output) if output else "unavailable"


def _sanitize_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)[:600]


def _sanitize_row(row: dict[str, Any], *, max_fields: int = 48) -> dict[str, Any]:
    """Bound one normalized row before it enters a response or compact cache."""
    output: dict[str, Any] = {}
    for raw_key, value in list(row.items())[:max_fields]:
        key = str(raw_key)[:80]
        if isinstance(value, (list, tuple)):
            output[key] = [_sanitize_scalar(item) for item in list(value)[:20] if not isinstance(item, (dict, list, tuple))]
        elif isinstance(value, dict):
            output[key] = {
                str(child_key)[:60]: _sanitize_scalar(child_value)
                for child_key, child_value in list(value.items())[:16]
                if not isinstance(child_value, (dict, list, tuple))
            }
        else:
            output[key] = _sanitize_scalar(value)
    return output


def _section(
    rows: list[dict[str, Any]],
    *,
    source: str,
    data_date: str | None,
    summary: dict[str, Any] | None = None,
    realtime: bool = False,
    cache_hit: bool = False,
    quality: str = "verified",
    error: str | None = None,
) -> dict[str, Any]:
    bounded = [_sanitize_row(row) for row in rows[:SECTION_ROW_LIMIT] if isinstance(row, dict)]
    clean_summary = _sanitize_row(summary or {})
    available = bool(bounded or any(value is not None for value in clean_summary.values()))
    return {
        "available": available,
        "rows": bounded,
        "count": len(bounded),
        "summary": clean_summary,
        "source": source if available else "unavailable",
        "data_date": data_date,
        "updated_at": shanghai_now().isoformat(),
        "is_realtime": bool(available and realtime),
        "cache_hit": bool(available and cache_hit),
        "quality": quality if available else "unavailable",
        "error": error,
    }


def _row_date(row: dict[str, Any]) -> str | None:
    return _date_text(_first_present(
        row.get("trade_date"), row.get("tradedate"), row.get("date"),
        row.get("source_day"), row.get("event_date"),
    ))


def _symbol(row: dict[str, Any]) -> str:
    value = str(_first_present(row.get("code"), row.get("symbol"), row.get("s")) or "").strip().upper()
    value = value.split(".", 1)[0]
    return value.zfill(6) if value.isdigit() and len(value) <= 6 else value[:16]


def _stock_row(row: dict[str, Any], *, source: str) -> dict[str, Any]:
    return {
        "code": _symbol(row),
        "name": str(_first_present(row.get("name"), row.get("n")) or "")[:80],
        "trade_date": _row_date(row),
        "price": _number(_first_present(row.get("price"), row.get("close"), row.get("m_price"))),
        "change_pct": _number(_first_present(row.get("change_pct"), row.get("pct_chg"), row.get("auc_pct_chg"), row.get("pc"))),
        "amount": _number(_first_present(row.get("amount"), row.get("auc_amt"))),
        "seal_amount": _number(_first_present(row.get("seal_amount"), row.get("fd_amount"), row.get("ztwme"))),
        "auction_amount": _number(_first_present(row.get("auc_amt"), row.get("auction_amount"))),
        "auction_volume": _number(_first_present(row.get("auc_vol"), row.get("auction_volume"))),
        "unmatched_volume": _number(_first_present(row.get("um_vol"), row.get("unmatched_volume"))),
        "unmatched_side": _first_present(row.get("um_side"), row.get("side")),
        "continuous_days": _integer(_first_present(row.get("continuous_days"), row.get("limit_times"), row.get("lbc"))),
        "previous_continuous_days": _integer(_first_present(row.get("previous_continuous_days"), row.get("pre_limit_times"))),
        "failed_attempts": _integer(_first_present(row.get("failed_attempts"), row.get("open_times"))),
        "first_time": _first_present(row.get("first_limit_time"), row.get("first_time"), row.get("time")),
        "last_time": _first_present(row.get("last_limit_time"), row.get("last_time")),
        "sector": str(_first_present(row.get("sector"), row.get("theme_name"), row.get("industry")) or "")[:120],
        "reason": str(_first_present(
            row.get("reason"), row.get("limit_detail"), row.get("reason_main_kpl"),
            row.get("reason_main_jygs"), row.get("reason_xgb"),
        ) or "")[:600],
        "rank": _integer(row.get("rank")),
        "source": source,
    }


def _generic_event_row(row: dict[str, Any], *, source: str) -> dict[str, Any]:
    normalized = _stock_row(row, source=source)
    normalized.update({
        "event_type": str(_first_present(row.get("event_type"), row.get("type"), row.get("category"), row.get("event")) or "")[:100],
        "status": str(_first_present(row.get("status"), row.get("state"), row.get("level")) or "")[:100],
        "event_time": str(_first_present(row.get("event_time"), row.get("time"), row.get("trademin"), row.get("datetime")) or "")[:80],
        "description": str(_first_present(row.get("description"), row.get("message"), row.get("title"), row.get("reason"), row.get("remark")) or "")[:600],
        "value": _number(_first_present(row.get("value"), row.get("score"), row.get("ratio"), row.get("pct_chg"))),
    })
    return normalized


def _merge_emotion(*sources: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    output: dict[str, Any] = {}
    field_sources: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_name = str(source.get("source") or "unknown")
        for field in EMOTION_FIELDS:
            value = source.get(field)
            if value is not None:
                output[field] = value
                field_sources[field] = source_name
    up = _integer(output.get("up_count"))
    down = _integer(output.get("down_count"))
    flat = _integer(output.get("flat_count"))
    stock_count = _integer(output.get("stock_count"))
    if flat is None and stock_count is not None and up is not None and down is not None:
        output["flat_count"] = max(0, stock_count - up - down)
        field_sources["flat_count"] = "derived_from_verified_counts"
    elif output.get("stock_count") is None and up is not None and down is not None:
        output["stock_count"] = up + down + (flat or 0)
        field_sources["stock_count"] = "derived_from_verified_counts"
    touched = _number(output.get("touched_limit_up_count"))
    failed = _number(output.get("failed_limit_count"))
    if output.get("failed_limit_rate") is None and touched:
        output["failed_limit_rate"] = round((failed or 0) / touched * 100, 2)
        field_sources["failed_limit_rate"] = "derived_from_verified_counts"
    total_order = _number(output.get("limit_up_order_amount"))
    order_0920 = _number(output.get("limit_up_order_amount_0920"))
    if output.get("limit_up_order_amount_after_0920") is None and total_order is not None and order_0920 is not None:
        output["limit_up_order_amount_after_0920"] = max(0, total_order - order_0920)
        field_sources["limit_up_order_amount_after_0920"] = "derived_from_verified_counts"
    if output.get("promotion_candidate_count") is None and output.get("yesterday_limit_up_count") is not None:
        output["promotion_candidate_count"] = output.get("yesterday_limit_up_count")
        field_sources["promotion_candidate_count"] = "derived_from_verified_counts"
    return output, field_sources


def _emotion_scores(row: dict[str, Any]) -> dict[str, Any]:
    up = _number(row.get("up_count")) or 0
    down = _number(row.get("down_count")) or 0
    limit_up = _number(row.get("limit_up_count")) or 0
    limit_down = _number(row.get("limit_down_count")) or 0
    failed_rate = _number(row.get("failed_limit_rate"))
    promotion = _number(row.get("promotion_rate"))
    breadth_pct = up / max(up + down, 1) * 100
    money_effect = _clip(
        breadth_pct * 0.50
        + min(limit_up, 120) / 120 * 22
        + (100 - (failed_rate if failed_rate is not None else 35)) * 0.18
        + (promotion if promotion is not None else 15) * 0.10
    )
    risk_release = _clip(
        (100 - breadth_pct) * 0.42
        + min(limit_down, 160) / 160 * 30
        + (failed_rate if failed_rate is not None else 25) * 0.20
        + min(_number(row.get("down_7pct_count")) or 0, 1200) / 1200 * 18
    )
    relay_rate = _first_present(row.get("promotion_rate_1_to_2"), row.get("promotion_rate"))
    if breadth_pct >= 65 and limit_up >= 70 and (failed_rate or 0) <= 25:
        environment = "进攻扩散"
    elif breadth_pct <= 25 and (limit_down >= 25 or risk_release >= 65):
        environment = "风险释放"
    elif (failed_rate or 0) >= 40:
        environment = "高分歧退潮"
    elif breadth_pct >= 52:
        environment = "结构修复"
    elif breadth_pct <= 42:
        environment = "防御分化"
    else:
        environment = "震荡混合"
    return {
        "breadth_pct": round(breadth_pct, 2),
        "money_effect_score": round(money_effect, 1),
        "risk_release_score": round(risk_release, 1),
        "relay_success_rate": _number(relay_rate),
        "market_environment": environment,
    }


class ReplayWorkspaceService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @staticmethod
    async def _safe(name: str, awaitable: Any, fallback: Any, timeout: float = 10.0) -> tuple[Any, str | None]:
        try:
            return await asyncio.wait_for(awaitable, timeout=timeout), None
        except Exception as exc:
            return fallback, f"{name}:{type(exc).__name__}"

    async def _available_dates(self, limit: int = 180) -> list[dict[str, Any]]:
        bounded = min(max(int(limit), 1), 500)
        flags: dict[date, dict[str, Any]] = defaultdict(lambda: {
            "emotion_snapshot": False, "market_sentiment": False,
            "fund_flow": False, "daily_bars": False,
        })
        async with async_session() as session:
            snapshots = (await session.execute(
                select(MarketEmotionReplaySnapshot.trade_date, MarketEmotionReplaySnapshot.source)
                .order_by(desc(MarketEmotionReplaySnapshot.trade_date)).limit(bounded)
            )).all()
            sentiments = (await session.execute(
                select(MarketSentimentDaily.trade_date).order_by(desc(MarketSentimentDaily.trade_date)).limit(bounded)
            )).scalars().all()
            concept_dates = (await session.execute(
                select(ConceptFundFlowDaily.trade_date).distinct()
                .order_by(desc(ConceptFundFlowDaily.trade_date)).limit(bounded)
            )).scalars().all()
            bar_dates = (await session.execute(
                select(StockDailyBar.trade_date).group_by(StockDailyBar.trade_date)
                .having(func.count(StockDailyBar.id) >= 1000)
                .order_by(desc(StockDailyBar.trade_date)).limit(bounded)
            )).scalars().all()
        source_by_date = {item[0]: item[1] for item in snapshots}
        for item in source_by_date:
            flags[item]["emotion_snapshot"] = True
        for item in sentiments:
            flags[item]["market_sentiment"] = True
        for item in concept_dates:
            flags[item]["fund_flow"] = True
        for item in bar_dates:
            flags[item]["daily_bars"] = True
        return [
            {
                "date": trade_date.isoformat(),
                **values,
                "source": source_by_date.get(trade_date) or "database_cache",
                "coverage": sum(bool(value) for value in values.values()),
            }
            for trade_date, values in sorted(flags.items(), reverse=True)[:bounded]
        ]

    async def dates(self, limit: int = 180) -> dict[str, Any]:
        rows = await self._available_dates(limit)
        return {
            "dates": rows,
            "count": len(rows),
            "latest": rows[0]["date"] if rows else None,
            "source": "compact_replay_snapshot+database_cache",
        }

    async def _resolve_date(self, requested: date | None) -> tuple[date, bool, list[dict[str, Any]]]:
        today = shanghai_now().date()
        if requested and requested > today:
            raise ValueError("不能查询未来日期")
        dates = await self._available_dates(240)
        available = [_date_value(item.get("date")) for item in dates]
        available = [item for item in available if item is not None]
        if requested is None:
            if available:
                return max(available), False, dates
            cursor = today
            while cursor.weekday() >= 5:
                cursor -= timedelta(days=1)
            return cursor, False, dates
        if requested in available:
            return requested, False, dates
        earlier = [item for item in available if item <= requested]
        # A weekday without local cache may still be queryable from NumCat.
        if requested.weekday() < 5 and numcat_market_provider.configured:
            return requested, False, dates
        return (max(earlier), True, dates) if earlier else (requested, False, dates)

    async def _database_context(self, target: date, history_days: int) -> dict[str, Any]:
        bounded = min(max(int(history_days), 10), 120)
        start = target - timedelta(days=220)
        async with async_session() as session:
            snapshot = await session.get(MarketEmotionReplaySnapshot, target)
            sentiment = await session.get(MarketSentimentDaily, target)
            snapshot_rows = list((await session.execute(
                select(MarketEmotionReplaySnapshot)
                .where(MarketEmotionReplaySnapshot.trade_date <= target)
                .order_by(desc(MarketEmotionReplaySnapshot.trade_date)).limit(bounded)
            )).scalars().all())
            sentiment_rows = list((await session.execute(
                select(MarketSentimentDaily)
                .where(MarketSentimentDaily.trade_date <= target)
                .order_by(desc(MarketSentimentDaily.trade_date)).limit(bounded)
            )).scalars().all())
            concept = list((await session.execute(
                select(ConceptFundFlowDaily).where(ConceptFundFlowDaily.trade_date == target)
                .order_by(desc(ConceptFundFlowDaily.main_net_inflow)).limit(120)
            )).scalars().all())
            industry = list((await session.execute(
                select(IndustryFundFlowDaily).where(IndustryFundFlowDaily.trade_date == target)
                .order_by(desc(IndustryFundFlowDaily.main_net_inflow)).limit(120)
            )).scalars().all())
            recent_concept = list((await session.execute(
                select(ConceptFundFlowDaily)
                .where(ConceptFundFlowDaily.trade_date >= start, ConceptFundFlowDaily.trade_date <= target)
                .order_by(desc(ConceptFundFlowDaily.trade_date))
            )).scalars().all())
            codes = {row.board_code for row in [*concept, *industry, *recent_concept]}
            board_names = {
                row.code: row.name
                for row in (await session.execute(select(MarketBoard).where(MarketBoard.code.in_(codes)))).scalars().all()
            } if codes else {}
            bar_stats = (await session.execute(select(
                func.count(StockDailyBar.id),
                func.sum(StockDailyBar.amount),
                func.sum((StockDailyBar.change_pct > 0).cast(Integer)),
                func.sum((StockDailyBar.change_pct < 0).cast(Integer)),
            ).where(StockDailyBar.trade_date == target))).one()

        snapshot_payload = dict(snapshot.payload or {}) if snapshot else {}
        compact_cache = snapshot_payload.pop("workspace_cache", {}) if snapshot_payload else {}
        csv_emotion = {
            **snapshot_payload,
            "source": snapshot.source,
            "trade_date": target.isoformat(),
        } if snapshot else {}
        db_emotion = {}
        if sentiment:
            db_emotion = {
                "trade_date": sentiment.trade_date.isoformat(),
                **{field: getattr(sentiment, field, None) for field in (
                    "up_count", "down_count", "flat_count", "stock_count", "market_amount",
                    "limit_up_count", "limit_down_count", "failed_limit_count",
                    "failed_limit_rate", "max_streak_height",
                )},
                "source": sentiment.source or "market_sentiment_daily",
            }
        bar_count = int(bar_stats[0] or 0)
        bar_emotion = {
            "trade_date": target.isoformat(),
            "stock_count": bar_count,
            "market_amount": int(bar_stats[1]) if bar_stats[1] is not None else None,
            "up_count": int(bar_stats[2] or 0),
            "down_count": int(bar_stats[3] or 0),
            "flat_count": max(0, bar_count - int(bar_stats[2] or 0) - int(bar_stats[3] or 0)),
            "source": "stock_daily_bars_derived",
        } if bar_count >= 1000 else {}

        history: dict[str, dict[str, Any]] = {}
        for row in snapshot_rows:
            payload = dict(row.payload or {})
            payload.pop("workspace_cache", None)
            history[row.trade_date.isoformat()] = {
                **payload,
                "date": row.trade_date.isoformat(),
                "week": row.week,
                "month": row.month,
                "source": row.source,
            }
        for row in sentiment_rows:
            key = row.trade_date.isoformat()
            current = history.setdefault(key, {"date": key, "source": row.source})
            for field in (
                "up_count", "down_count", "flat_count", "stock_count", "market_amount",
                "limit_up_count", "limit_down_count", "failed_limit_count",
                "failed_limit_rate", "max_streak_height",
            ):
                if current.get(field) is None and getattr(row, field, None) is not None:
                    current[field] = getattr(row, field)
            current["source"] = _source_join((current.get("source"), row.source))

        def board_row(row: Any, board_type: str) -> dict[str, Any]:
            return {
                "code": row.board_code,
                "name": board_names.get(row.board_code, row.board_code),
                "board_type": board_type,
                "trade_date": row.trade_date.isoformat(),
                "change_pct": _number(row.change_pct),
                "main_net_inflow": _number(row.main_net_inflow),
                "main_net_inflow_pct": _number(row.main_net_inflow_pct),
                "super_large_net_inflow": _number(row.super_large_net_inflow),
                "large_net_inflow": _number(row.large_net_inflow),
                "up_count": _integer(row.up_count),
                "down_count": _integer(row.down_count),
                "leading_stock": str(getattr(row, "leading_stock", "") or ""),
                "source": "database_fund_flow",
            }

        recent_dates = sorted({row.trade_date for row in recent_concept}, reverse=True)[:5]
        rotation_rows = [
            board_row(row, "concept") for row in recent_concept if row.trade_date in set(recent_dates)
        ]
        return {
            "snapshot": snapshot,
            "csv_emotion": csv_emotion,
            "db_emotion": db_emotion,
            "bar_emotion": bar_emotion,
            "compact_cache": compact_cache if isinstance(compact_cache, dict) else {},
            "history": sorted(history.values(), key=lambda item: item["date"])[-bounded:],
            "concept": [board_row(row, "concept") for row in concept],
            "industry": [board_row(row, "industry") for row in industry],
            "rotation": rotation_rows,
        }

    @staticmethod
    def _provider_rows_for_date(rows: list[dict[str, Any]], target: date, *, scoped: bool = False) -> list[dict[str, Any]]:
        target_text = target.isoformat()
        output = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            row_date = _row_date(row)
            if row_date and row_date != target_text:
                continue
            if row_date or scoped:
                output.append(row)
        return output

    @staticmethod
    def _ladder(limit_rows: list[dict[str, Any]], emotion: dict[str, Any]) -> list[dict[str, Any]]:
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in limit_rows:
            height = max(_integer(row.get("continuous_days")) or 1, 1)
            grouped[height].append(row)
        if grouped:
            ladder_source = _source_join(item.get("source") for item in limit_rows)
            return [
                {
                    "height": height,
                    "count": len(rows),
                    "stocks": [f"{item.get('name') or item.get('code')}({item.get('code')})" for item in rows[:16]],
                    "total_amount": sum(_number(item.get("amount")) or 0 for item in rows),
                    "total_seal_amount": sum(_number(item.get("seal_amount")) or 0 for item in rows),
                    "source": ladder_source or "limit_pool",
                }
                for height, rows in sorted(grouped.items(), reverse=True)
            ]
        counts = {
            1: _integer(emotion.get("first_board_count")),
            2: _integer(emotion.get("second_board_count")),
            3: _integer(emotion.get("third_board_count")),
        }
        above_four = _integer(emotion.get("fourth_board_or_higher_count"))
        output = [
            {"height": height, "count": count, "stocks": [], "source": "daily_emotion_aggregate"}
            for height, count in counts.items() if count is not None
        ]
        if above_four is not None:
            output.append({"height": "4+", "count": above_four, "stocks": [], "source": "daily_emotion_aggregate"})
        return sorted(output, key=lambda item: str(item["height"]), reverse=True)

    @staticmethod
    def _topic_rankings(database_rows: list[dict[str, Any]], provider_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {str(row.get("code")): dict(row) for row in database_rows if row.get("code")}
        for raw in provider_rows:
            code = str(_first_present(raw.get("theme_symbol"), raw.get("symbol")) or "").strip()
            if not code:
                continue
            row = merged.setdefault(code, {"code": code, "board_type": "selected"})
            row.update({
                "name": str(_first_present(raw.get("theme_name"), raw.get("name"), row.get("name"), code)),
                "trade_date": _row_date(raw) or row.get("trade_date"),
                "change_pct": _number(_first_present(raw.get("pct_chg"), row.get("change_pct"))),
                "strength": _number(raw.get("strength")),
                "main_net_inflow": _number(_first_present(raw.get("main_net_amount"), row.get("main_net_inflow"))),
                "source": _source_join((row.get("source"), "numcat_selected_theme")),
            })
        output = list(merged.values())
        changes = [abs(_number(item.get("change_pct")) or 0) for item in output]
        flows = [abs(_number(item.get("main_net_inflow")) or 0) for item in output]
        max_change = max(changes, default=1) or 1
        max_flow = max(flows, default=1) or 1
        for item in output:
            change = _number(item.get("change_pct")) or 0
            flow = _number(item.get("main_net_inflow")) or 0
            raw_strength = _number(item.get("strength"))
            item["strength_score"] = round(
                _clip((change / max_change * 50) + (flow / max_flow * 35) + ((raw_strength or 50) / 100 * 15)), 1
            )
        output.sort(key=lambda item: (
            _number(item.get("strength_score")) or -999,
            _number(item.get("main_net_inflow")) or -math.inf,
        ), reverse=True)
        for index, item in enumerate(output, 1):
            item["rank"] = index
        return output[:80]

    @staticmethod
    def _rotation(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if row.get("code"):
                grouped[str(row["code"])].append(row)
        output = []
        for code, items in grouped.items():
            items.sort(key=lambda item: str(item.get("trade_date") or ""))
            flows = [_number(item.get("main_net_inflow")) for item in items]
            flows = [value for value in flows if value is not None]
            changes = [_number(item.get("change_pct")) for item in items]
            changes = [value for value in changes if value is not None]
            if not flows and not changes:
                continue
            late = sum(flows[-2:]) / max(len(flows[-2:]), 1) if flows else 0
            early = sum(flows[:-2]) / max(len(flows[:-2]), 1) if len(flows) > 2 else 0
            output.append({
                "code": code,
                "name": items[-1].get("name") or code,
                "sessions": len({item.get("trade_date") for item in items}),
                "total_main_net_inflow": sum(flows) if flows else None,
                "latest_main_net_inflow": flows[-1] if flows else None,
                "flow_acceleration": late - early if flows else None,
                "average_change_pct": sum(changes) / len(changes) if changes else None,
                "state": "强化" if flows and late > max(early, 0) else "转弱" if flows and late < min(early, 0) else "分化",
                "source": "database_fund_flow",
            })
        output.sort(key=lambda item: _number(item.get("flow_acceleration")) or -math.inf, reverse=True)
        return output[:30]

    @staticmethod
    def _cached_section(cache: dict[str, Any], key: str) -> dict[str, Any] | None:
        sections = cache.get("sections") if isinstance(cache, dict) else None
        section = sections.get(key) if isinstance(sections, dict) else None
        if not isinstance(section, dict) or not section.get("available"):
            return None
        copied = dict(section)
        copied["is_realtime"] = False
        copied["cache_hit"] = True
        copied["quality"] = "normalized_compact_cache"
        return copied

    @staticmethod
    def _merge_compact_sections(
        existing: dict[str, Any] | None,
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge same-day sections without downgrading a detail cache.

        A background refresh and a normal page read can finish in either
        order.  An aggregate-only response must not replace a previously
        collected stock-level response for the same date.
        """
        merged = dict(existing) if isinstance(existing, dict) else {}
        for key, section in incoming.items():
            if not isinstance(section, dict):
                continue
            previous = merged.get(key)
            if isinstance(previous, dict):
                previous_rows = list(previous.get("rows") or [])
                incoming_rows = list(section.get("rows") or [])
                previous_detail = sum(
                    bool(row.get("code") or row.get("symbol") or row.get("stocks"))
                    for row in previous_rows if isinstance(row, dict)
                )
                incoming_detail = sum(
                    bool(row.get("code") or row.get("symbol") or row.get("stocks"))
                    for row in incoming_rows if isinstance(row, dict)
                )
                if previous_rows and (
                    not incoming_rows
                    or (previous_detail > 0 and incoming_detail == 0)
                ):
                    merged[key] = {
                        **previous,
                        "summary": {
                            **(previous.get("summary") or {}),
                            **(section.get("summary") or {}),
                        },
                    }
                    continue
            merged[key] = section
        return merged

    async def _persist_compact(
        self,
        target: date,
        emotion: dict[str, Any],
        sections: dict[str, dict[str, Any]],
        source: str,
    ) -> None:
        if not emotion and not any(section.get("available") for section in sections.values()):
            return
        cache_sections = {}
        for key in (
            "yesterday_limit", "auction_limit", "auction_grab", "limit_up", "limit_down",
            "failed_limit", "limit_reasons", "streak_ladder", "anomaly", "radar",
            "topics", "strong_sectors", "topic_rotation", "topic_auction",
            "strongest_fengkou", "hot_search", "one_price", "theme_library",
        ):
            section = sections.get(key)
            if not isinstance(section, dict) or not section.get("available"):
                continue
            cache_sections[key] = {
                **{field: section.get(field) for field in (
                    "available", "count", "summary", "source", "data_date", "quality",
                )},
                "rows": [_sanitize_row(row) for row in list(section.get("rows") or [])[:40]],
                "updated_at": section.get("updated_at"),
                "is_realtime": False,
                "cache_hit": True,
                "error": None,
            }
        payload = {
            **{field: emotion.get(field) for field in EMOTION_FIELDS if emotion.get(field) is not None},
            "source_note": "规范化日级快照；不包含猫爪原始响应",
            "workspace_cache": {
                "version": REPLAY_VERSION,
                "updated_at": shanghai_now().isoformat(),
                "sections": cache_sections,
            },
        }
        try:
            async with self._lock:
                async with async_session() as session:
                    row = await session.get(MarketEmotionReplaySnapshot, target)
                    if row is None:
                        row = MarketEmotionReplaySnapshot(
                            trade_date=target,
                            week=target.strftime("%G/W%V"),
                            month=target.strftime("%Y/%m"),
                            source=source[:80],
                            payload=payload,
                        )
                        session.add(row)
                    else:
                        existing_payload = dict(row.payload or {})
                        existing_cache = existing_payload.get("workspace_cache")
                        incoming_cache = payload.get("workspace_cache") or {}
                        merged_cache = {
                            **(existing_cache if isinstance(existing_cache, dict) else {}),
                            **incoming_cache,
                            "sections": self._merge_compact_sections(
                                existing_cache.get("sections") if isinstance(existing_cache, dict) else {},
                                incoming_cache.get("sections") if isinstance(incoming_cache, dict) else {},
                            ),
                        }
                        row.source = _source_join((row.source, source))[:80]
                        row.payload = {
                            **existing_payload,
                            **payload,
                            "workspace_cache": merged_cache,
                        }
                        row.updated_at = datetime.utcnow()
                    cutoff = shanghai_now().date() - timedelta(days=PROVIDER_CACHE_DAYS * 2)
                    stale = list((await session.execute(
                        select(MarketEmotionReplaySnapshot).where(
                            MarketEmotionReplaySnapshot.trade_date < cutoff,
                            MarketEmotionReplaySnapshot.source.not_like("%user_imported_csv%"),
                        )
                    )).scalars().all())
                    for item in stale:
                        await session.delete(item)
                    await session.commit()
        except Exception as exc:
            print(f"Replay compact cache write failed: {type(exc).__name__}")

    async def get(
        self,
        requested_date: date | None = None,
        *,
        refresh: bool = False,
        history_days: int = 40,
    ) -> dict[str, Any]:
        target, date_adjusted, available_dates = await self._resolve_date(requested_date)
        db = await self._database_context(target, history_days)
        compact_cache = db.get("compact_cache") or {}
        target_text = target.isoformat()
        now = shanghai_now()
        realtime = bool(target == now.date() and is_a_share_market_session(now))

        # Historical replay should open from the same-day compact snapshot in
        # one round trip.  Vendor detail is fetched on a cold date, during the
        # live session, or when the user explicitly requests a refresh.
        provider_queried = bool(
            numcat_market_provider.configured
            and (refresh or realtime or db.get("snapshot") is None)
        )
        # Public EastMoney limit pools remain useful when the optional NumCat
        # key is absent. Keep their request gate cache-first as well: only a
        # live session, a cold date, or an explicit refresh should hit network.
        detail_queried = bool(refresh or realtime or db.get("snapshot") is None)
        provider: dict[str, tuple[Any, str | None]] = {}
        if provider_queried:
            date_params = {"tradedate": target.strftime("%Y%m%d"), "limit": 500}
            date_range = {
                "startdate": target.strftime("%Y%m%d"),
                "enddate": target.strftime("%Y%m%d"),
                "limit": 500,
            }
            jobs = {
                "emotion": numcat_market_provider.market_emotion(tradedate=target),
                "yesterday_limit": numcat_extended_provider.limit_pool_yesterday(params=date_params),
                "auction_limit": numcat_extended_provider.rows("auc_kp", params=date_params, refresh=refresh),
                "auction_grab": numcat_extended_provider.rows("daily_auc", params=date_params, refresh=refresh),
                "one_price": numcat_extended_provider.rows("daily_auc_fd", params=date_params, refresh=refresh),
                "anomaly": numcat_extended_provider.anomaly_forecast(params=date_params),
                "radar": numcat_extended_provider.point_monitor(params=date_params),
                "limit_events": numcat_extended_provider.limit_event_history(params=date_range),
                "themes": numcat_market_provider.theme_daily(level="parent", recentdays=8),
                "theme_auction": numcat_market_provider.theme_auction(tradedate=target),
                "fengkou": numcat_market_provider.strongest_fengkou(tradedate=target, limit=30),
                "hot": numcat_market_provider.hot_stock(tradedate=target, limit=30),
                "theme_reason_xgb": numcat_market_provider.theme_reason(source="xgb", tradedate=target, recentdays=None),
                "theme_reason_jygs": numcat_market_provider.theme_reason(source="jygs", tradedate=target, recentdays=None),
                "theme_library": numcat_market_provider.theme_library(),
            }
            names = list(jobs)
            results = await asyncio.gather(*(
                self._safe(name, jobs[name], {} if name in {"limit_up", "limit_down", "failed_limit"} else [], 11.0)
                for name in names
            ))
            provider.update({name: result for name, result in zip(names, results)})
        if detail_queried:
            detail_jobs = {
                "limit_up": collector.fetch_limit_up_pool(page_size=500, target_date=target),
                "limit_down": collector.fetch_limit_down_pool(page_size=500, target_date=target),
                "failed_limit": collector.fetch_failed_limit_pool(page_size=500, target_date=target),
            }
            detail_results = await asyncio.gather(*(
                self._safe(name, detail_jobs[name], {}, 11.0)
                for name in detail_jobs
            ))
            provider.update({name: result for name, result in zip(detail_jobs, detail_results)})

        def result(name: str, fallback: Any) -> tuple[Any, str | None]:
            if name in provider:
                return provider[name]
            if name in {"limit_up", "limit_down", "failed_limit"}:
                if detail_queried:
                    return fallback, "东方财富公开明细未返回有效交易日数据"
                return fallback, "使用同日规范化缓存；点击刷新可补取涨跌停明细"
            if numcat_market_provider.configured:
                return fallback, "使用同日快照；点击刷新可补取猫爪明细"
            return fallback, "NumCat未配置"

        emotion_rows, emotion_error = result("emotion", [])
        exact_emotion_rows = self._provider_rows_for_date(emotion_rows, target, scoped=True)
        numcat_emotion = exact_emotion_rows[-1] if exact_emotion_rows else {}
        emotion, field_sources = _merge_emotion(
            db.get("bar_emotion") or {},
            db.get("csv_emotion") or {},
            db.get("db_emotion") or {},
            numcat_emotion,
        )
        emotion.update(_emotion_scores(emotion))
        emotion["trade_date"] = target_text
        emotion["field_sources"] = field_sources
        emotion_source = _source_join(field_sources.values())

        up_payload, up_error = result("limit_up", {})
        down_payload, down_error = result("limit_down", {})
        failed_payload, failed_error = result("failed_limit", {})

        def pool_rows(payload: Any, source: str) -> list[dict[str, Any]]:
            if not isinstance(payload, dict):
                return []
            if _date_text(payload.get("trade_date")) not in (None, target_text):
                return []
            return [_stock_row(row, source=source) for row in payload.get("stocks") or []]

        limit_up_rows = pool_rows(up_payload, str(up_payload.get("source") or "limit_pool") if isinstance(up_payload, dict) else "limit_pool")
        limit_down_rows = pool_rows(down_payload, str(down_payload.get("source") or "limit_pool") if isinstance(down_payload, dict) else "limit_pool")
        failed_rows = pool_rows(failed_payload, str(failed_payload.get("source") or "limit_pool") if isinstance(failed_payload, dict) else "limit_pool")

        yesterday_raw, yesterday_error = result("yesterday_limit", [])
        auction_limit_raw, auction_limit_error = result("auction_limit", [])
        auction_grab_raw, auction_grab_error = result("auction_grab", [])
        one_price_raw, one_price_error = result("one_price", [])
        anomaly_raw, anomaly_error = result("anomaly", [])
        radar_raw, radar_error = result("radar", [])
        limit_events_raw, limit_events_error = result("limit_events", [])
        theme_raw, theme_error = result("themes", [])
        theme_auction_raw, theme_auction_error = result("theme_auction", [])
        fengkou_raw, fengkou_error = result("fengkou", [])
        hot_raw, hot_error = result("hot", [])
        reason_xgb, reason_xgb_error = result("theme_reason_xgb", [])
        reason_jygs, reason_jygs_error = result("theme_reason_jygs", [])
        library_raw, library_error = result("theme_library", [])

        yesterday_rows = [
            _stock_row(row, source="numcat_limit_pool_yes")
            for row in self._provider_rows_for_date(yesterday_raw, target, scoped=True)
        ]
        auction_limit_rows = [
            _stock_row(row, source="numcat_auc_kp")
            for row in self._provider_rows_for_date(auction_limit_raw, target, scoped=True)
        ]
        auction_grab_rows = [
            _stock_row(row, source="numcat_daily_auc")
            for row in self._provider_rows_for_date(auction_grab_raw, target, scoped=True)
        ]
        one_price_rows = [
            _stock_row(row, source="numcat_daily_auc_fd")
            for row in self._provider_rows_for_date(one_price_raw, target, scoped=True)
        ]
        anomaly_rows = [
            _generic_event_row(row, source="numcat_anomaly_forecast")
            for row in self._provider_rows_for_date(anomaly_raw, target, scoped=True)
        ]
        radar_rows = [
            _generic_event_row(row, source="numcat_point_monitor")
            for row in self._provider_rows_for_date(radar_raw, target, scoped=True)
        ]
        radar_rows.extend(
            _generic_event_row(row, source="numcat_limit_event_history")
            for row in self._provider_rows_for_date(limit_events_raw, target, scoped=True)
        )

        themes_for_date = self._provider_rows_for_date(theme_raw, target, scoped=False)
        topics = self._topic_rankings([*db.get("concept", []), *db.get("industry", [])], themes_for_date)
        strong_sectors = [row for row in topics if (_number(row.get("strength_score")) or 0) > 0][0:20]
        rotation = self._rotation(db.get("rotation") or [])
        auction_topic_rows = [
            {
                "code": str(row.get("theme_symbol") or ""),
                "name": str(row.get("theme_name") or ""),
                "rank": _integer(row.get("group_rank")),
                "bid_volume_burst": _number(row.get("bid_volume_burst")),
                "abnormal_amount": _number(row.get("abnormal_amount")),
                "bid_volume": _number(row.get("bid_volume")),
                "main_net_inflow": _number(row.get("main_net_amount")),
                "source": "numcat_theme_auc_kp",
            }
            for row in self._provider_rows_for_date(theme_auction_raw, target, scoped=True)
        ]
        reason_rows = []
        for row in [*(reason_xgb or []), *(reason_jygs or [])]:
            if _row_date(row) not in (None, target_text):
                continue
            reason_rows.append({
                "code": _symbol(row) or str(row.get("theme_symbol") or ""),
                "name": str(row.get("name") or ""),
                "reason": str(row.get("reason") or "")[:600],
                "reason_source": str(row.get("reason_source") or row.get("source") or ""),
                "trade_date": _row_date(row) or target_text,
                "source": "numcat_theme_reason",
            })
        for row in limit_up_rows:
            if row.get("reason"):
                reason_rows.append({
                    "code": row.get("code"), "name": row.get("name"),
                    "reason": row.get("reason"), "trade_date": target_text,
                    "source": row.get("source"),
                })

        ladder = self._ladder(limit_up_rows, emotion)
        market_summary = {
            **{field: emotion.get(field) for field in EMOTION_FIELDS},
            **{field: emotion.get(field) for field in (
                "breadth_pct", "money_effect_score", "risk_release_score",
                "relay_success_rate", "market_environment",
            )},
        }
        auction_summary = {
            "auction_main_net_inflow": emotion.get("auction_main_net_inflow"),
            "limit_up_order_amount": emotion.get("limit_up_order_amount"),
            "limit_up_order_amount_0920": _first_present(
                emotion.get("limit_up_order_amount_0920"),
                emotion.get("limit_up_order_amount_after_0920"),
            ),
            "limit_up_order_count": emotion.get("limit_up_order_count"),
            "overnight_order_amount": emotion.get("overnight_order_amount"),
            "order_amount_0920": emotion.get("order_amount_0920"),
            "order_amount_0925": emotion.get("order_amount_0925"),
            "order_count_0925": emotion.get("order_count_0925"),
        }

        # A daily CSV/database snapshot can legitimately provide the market
        # aggregates while the vendor-only detail pools are empty.  Keep the
        # provenance of those aggregate fields separate from an unconfigured
        # NumCat endpoint; otherwise the UI would imply that a vendor detail
        # response exists when it does not.
        aggregate_source = emotion_source if emotion_source != "unavailable" else "daily_emotion_aggregate"
        has_numcat_rows = lambda rows: bool(rows)

        sections: dict[str, dict[str, Any]] = {
            "market_summary": _section([market_summary], source=emotion_source, data_date=target_text, summary=market_summary, realtime=realtime, quality="field_level_merged", error=emotion_error),
            "yesterday_limit": _section(yesterday_rows, source="numcat_limit_pool_yes" if has_numcat_rows(yesterday_rows) else aggregate_source, data_date=target_text, summary={"count": emotion.get("yesterday_limit_up_count")}, realtime=realtime, error=yesterday_error),
            "auction_limit": _section(auction_limit_rows, source="numcat_auc_kp" if has_numcat_rows(auction_limit_rows) else aggregate_source, data_date=target_text, summary=auction_summary, realtime=realtime, error=auction_limit_error),
            "auction_grab": _section(auction_grab_rows, source="numcat_daily_auc" if has_numcat_rows(auction_grab_rows) else aggregate_source, data_date=target_text, summary={**auction_summary, "one_price_count": len(one_price_rows)}, realtime=realtime, error=auction_grab_error or one_price_error),
            "one_price": _section(one_price_rows, source="numcat_daily_auc_fd" if has_numcat_rows(one_price_rows) else aggregate_source, data_date=target_text, summary={"count": emotion.get("one_price_limit_up_count")}, realtime=realtime, error=one_price_error),
            "limit_up": _section(limit_up_rows, source=str(up_payload.get("source") or "daily_emotion_aggregate") if isinstance(up_payload, dict) else "daily_emotion_aggregate", data_date=target_text, summary={"count": emotion.get("limit_up_count"), "touched_count": emotion.get("touched_limit_up_count")}, realtime=realtime, error=up_error),
            "limit_down": _section(limit_down_rows, source=str(down_payload.get("source") or "daily_emotion_aggregate") if isinstance(down_payload, dict) else "daily_emotion_aggregate", data_date=target_text, summary={"count": emotion.get("limit_down_count")}, realtime=realtime, error=down_error),
            "failed_limit": _section(failed_rows, source=str(failed_payload.get("source") or "daily_emotion_aggregate") if isinstance(failed_payload, dict) else "daily_emotion_aggregate", data_date=target_text, summary={"count": emotion.get("failed_limit_count"), "rate": emotion.get("failed_limit_rate")}, realtime=realtime, error=failed_error),
            "limit_reasons": _section(reason_rows, source=_source_join(("numcat_theme_reason" if reason_rows else None, up_payload.get("source") if isinstance(up_payload, dict) else None)), data_date=target_text, realtime=realtime, error=reason_xgb_error or reason_jygs_error),
            "streak_ladder": _section(ladder, source=_source_join(item.get("source") for item in ladder) if limit_up_rows else aggregate_source, data_date=target_text, summary={"max_height": emotion.get("max_streak_height")}, realtime=realtime, quality="verified_detail" if limit_up_rows else "verified_aggregate"),
            "anomaly": _section(anomaly_rows, source="numcat_anomaly_forecast", data_date=target_text, realtime=realtime, error=anomaly_error),
            "radar": _section(radar_rows, source=_source_join(("numcat_point_monitor", "numcat_limit_event_history")), data_date=target_text, realtime=realtime, error=radar_error or limit_events_error),
            "topics": _section(topics, source=_source_join(("database_fund_flow" if db.get("concept") or db.get("industry") else None, "numcat_selected_theme" if themes_for_date else None)), data_date=target_text, realtime=realtime, error=theme_error),
            "strong_sectors": _section(strong_sectors, source=_source_join(("database_fund_flow", "numcat_selected_theme" if themes_for_date else None)), data_date=target_text, realtime=realtime, error=theme_error),
            "topic_rotation": _section(rotation, source="database_fund_flow", data_date=target_text, realtime=False, quality="five_session_derived"),
            "topic_auction": _section(auction_topic_rows, source="numcat_theme_auc_kp", data_date=target_text, realtime=realtime, error=theme_auction_error),
            "strongest_fengkou": _section([_stock_row(row, source="numcat_fengk_kp") | {"strength": _number(row.get("strength")), "main_net_inflow": _number(row.get("main_net_amount"))} for row in (fengkou_raw or [])], source="numcat_fengk_kp", data_date=target_text, realtime=realtime, error=fengkou_error),
            "hot_search": _section([_stock_row(row, source="numcat_hotstock") for row in (hot_raw or [])], source="numcat_hotstock", data_date=target_text, realtime=realtime, error=hot_error),
            "theme_library": _section([{"theme_id": row.get("theme_id"), "name": row.get("name"), "source": "numcat_theme_lib_kp"} for row in (library_raw or [])], source="numcat_theme_lib_kp", data_date=None, realtime=False, quality="current_catalog", error=library_error),
        }

        # The three public limit pools are safe to cache as compact normalized
        # rows even when the optional NumCat provider is unavailable.
        # Preserve their verified same-day detail on ordinary historical reads.

        # If a dated provider call is unavailable, use only the compact cache
        # from the same trade date.  Never substitute another date silently.
        for key in list(sections):
            cached = self._cached_section(compact_cache, key)
            current = sections[key]
            # A summary-only section is technically ``available`` because it
            # has a count, but it must not hide a verified stock-level cache
            # collected by an earlier refresh.
            current_rows = list(current.get("rows") or [])
            cached_rows = list(cached.get("rows") or []) if cached else []
            if cached and not current_rows and cached_rows:
                sections[key] = {
                    **cached,
                    "summary": {**(cached.get("summary") or {}), **(current.get("summary") or {})},
                    "error": current.get("error") or cached.get("error"),
                }
                continue
            if not current.get("available") and cached:
                cached["error"] = sections[key].get("error") or "当前接口未返回，使用同日规范化缓存"
                sections[key] = cached

        history_by_date = {str(item.get("date")): dict(item) for item in db.get("history") or [] if item.get("date")}
        if provider_queried:
            recent_rows, recent_error = await self._safe(
                "emotion_history",
                numcat_market_provider.market_emotion(recentdays=min(max(history_days, 10), 120)),
                [],
                11.0,
            )
            for item in recent_rows:
                item_date = _row_date(item)
                if not item_date or item_date > target_text:
                    continue
                existing = history_by_date.get(item_date, {})
                merged, _ = _merge_emotion(existing, item)
                history_by_date[item_date] = {**existing, **merged, "date": item_date, "source": _source_join((existing.get("source"), item.get("source")))}
        elif numcat_market_provider.configured:
            recent_error = "使用同日快照；点击刷新可补取猫爪历史"
        else:
            recent_error = "NumCat未配置"
        history = []
        for item in sorted(history_by_date.values(), key=lambda row: str(row.get("date") or ""))[-min(max(history_days, 10), 120):]:
            item.update(_emotion_scores(item))
            history.append(_sanitize_row(item, max_fields=64))

        sources = sorted({
            section.get("source") for section in sections.values()
            if section.get("source") and section.get("source") != "unavailable"
        } | ({emotion_source} if emotion_source != "unavailable" else set()))
        available_count = sum(bool(section.get("available")) for section in sections.values())
        unavailable = [key for key, section in sections.items() if not section.get("available")]
        errors = sorted({
            str(section.get("error"))
            for section in sections.values()
            if section.get("error")
            and str(section.get("error")) not in {
                "NumCat未配置",
                "使用同日快照；点击刷新可补取猫爪历史",
                "使用同日快照；点击刷新可补取猫爪明细",
                "使用同日规范化缓存；点击刷新可补取涨跌停明细",
            }
        })
        if recent_error and not history:
            errors.append(recent_error)

        payload = {
            "available": bool(emotion or available_count),
            "requested_date": requested_date.isoformat() if requested_date else None,
            "trade_date": target_text,
            "date_adjusted": date_adjusted,
            "date_adjustment_note": "所选日期不是已缓存交易日，已切换到此前最近交易日" if date_adjusted else None,
            "updated_at": shanghai_now().isoformat(),
            "is_realtime": realtime,
            "cache_hit": any(bool(section.get("cache_hit")) for section in sections.values()),
            "source": _source_join(sources),
            "emotion": emotion,
            "sections": sections,
            "history": {
                "rows": history,
                "count": len(history),
                "source": _source_join(item.get("source") for item in history),
                "data_start": history[0].get("date") if history else None,
                "data_end": history[-1].get("date") if history else None,
                "formula_note": "赚钱效应与风险释放分数为透明规则派生值，不是猫爪原始字段。",
            },
            "available_dates": available_dates,
            "quality": {
                "version": REPLAY_VERSION,
                "coverage_pct": round(available_count / max(len(sections), 1) * 100, 1),
                "available_sections": available_count,
                "section_count": len(sections),
                "unavailable_sections": unavailable,
                "errors": errors,
                "provider_configured": numcat_market_provider.configured,
                "provider_queried": provider_queried,
                "detail_queried": detail_queried,
                "persistent_raw_storage": False,
                "storage_policy": "仅保存按交易日规范化的紧凑统计与有限排行；猫爪原始响应只进入有界内存缓存。",
                "csv_policy": "用户CSV仅作历史参考，金额已由亿元换算为元，并保留来源标识。",
            },
            "refresh_requested": refresh,
        }
        await self._persist_compact(target, emotion, sections, _source_join((emotion_source, "numcat" if provider else None)))
        return payload

    @staticmethod
    def _deterministic_analysis(payload: dict[str, Any]) -> str:
        emotion = payload.get("emotion") or {}
        topics = (payload.get("sections") or {}).get("strong_sectors", {}).get("rows") or []
        up = _integer(emotion.get("up_count"))
        down = _integer(emotion.get("down_count"))
        limit_up = _integer(emotion.get("limit_up_count"))
        limit_down = _integer(emotion.get("limit_down_count"))
        failed_rate = _number(emotion.get("failed_limit_rate"))
        main_flow = _number(emotion.get("main_net_inflow"))
        promotion = _number(emotion.get("promotion_rate"))
        environment = str(emotion.get("market_environment") or "数据不足")
        sector_text = "、".join(str(item.get("name")) for item in topics[:3] if item.get("name")) or "暂无完整板块确认"
        breadth_text = f"上涨{up}家、下跌{down}家" if up is not None and down is not None else "涨跌家数未完整返回"
        board_text = f"涨停{limit_up}家、跌停{limit_down}家" if limit_up is not None and limit_down is not None else "涨跌停明细未完整返回"
        risk_parts = []
        if failed_rate is not None:
            risk_parts.append(f"炸板率{failed_rate:.1f}%")
        if promotion is not None:
            risk_parts.append(f"连板晋级率{promotion:.1f}%")
        if main_flow is not None:
            risk_parts.append(f"主力净额{main_flow / 1e8:.1f}亿元")
        return clean_ai_text(
            f"复盘结论\n{payload.get('trade_date')}市场环境判定为{environment}。\n\n"
            f"市场事实\n{breadth_text}；{board_text}；{'，'.join(risk_parts) or '关键资金与接力数据暂不完整'}。\n\n"
            f"主线与结构\n当日强度与资金排名靠前的方向为{sector_text}。排名只说明当日结构，仍需观察次日资金持续性和中军确认。\n\n"
            "执行复盘\n先检查市场宽度、炸板率和晋级率是否同向，再看竞价封单与板块资金是否确认。价格上涨但宽度、资金和接力效率没有同步时，按分歧处理。\n\n"
            "风险边界\n历史结构用于复盘和建立观察条件，不代表未来必然重复，也不单独构成买卖指令。"
        )

    async def analyze(self, target: date | None, *, use_ai: bool = True, history_days: int = 40) -> dict[str, Any]:
        payload = await self.get(target, refresh=False, history_days=history_days)
        deterministic = self._deterministic_analysis(payload)
        interpretation = deterministic
        ai_generated = False
        if use_ai and ai_service.client:
            evidence = {
                "trade_date": payload.get("trade_date"),
                "emotion": payload.get("emotion"),
                "strong_sectors": (payload.get("sections") or {}).get("strong_sectors", {}).get("rows", [])[:8],
                "topic_rotation": (payload.get("sections") or {}).get("topic_rotation", {}).get("rows", [])[:8],
                "auction": (payload.get("sections") or {}).get("auction_limit", {}).get("summary", {}),
                "quality": payload.get("quality"),
            }
            prompt = (
                "你是资深A股交易复盘研究员。只能使用下方可核验数据，用普通投资者能看懂的中文写复盘。"
                "依次写：一句话结论、市场事实、情绪与接力、板块结构、次日观察条件、风险边界。"
                "每段2到4句，不预测必涨，不给强制买卖指令，不补造缺失数据。不要Markdown符号、表格或加粗。\n"
                + json.dumps(evidence, ensure_ascii=False, default=str)[:26000]
            )
            generated = await ai_service.generate(prompt, system_prompt="只输出简洁纯文本中文，不使用 **、__、```、# 或表格。")
            cleaned = clean_ai_text(generated)
            if cleaned and not cleaned.startswith("[AI服务"):
                interpretation = cleaned
                ai_generated = True
        return {
            "trade_date": payload.get("trade_date"),
            "interpretation": interpretation,
            "ai_generated": ai_generated,
            "data_cutoff_time": payload.get("updated_at"),
            "sources": payload.get("source", "").split("+"),
            "quality": payload.get("quality"),
            "policy": "AI只解释结构化事实，不补造数据、不改变原始数值。",
        }


replay_workspace_service = ReplayWorkspaceService()


__all__ = ["ReplayWorkspaceService", "replay_workspace_service", "REPLAY_VERSION"]
