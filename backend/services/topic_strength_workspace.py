"""Unified topic-strength research workspace.

This service is the aggregation boundary for the topic page.  It combines
the existing daily fund-flow warehouse with the official NumCat snapshots,
and persists one small JSON envelope per view so a closed market still has a
usable, date-labelled page.  Missing values stay missing and every section
reports its own source and freshness.
"""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import desc, func, select

from database import async_session
from models import ConceptFundFlowDaily, IndustryFundFlowDaily, MarketBoard, MarketDataCache, StockFundFlowDaily
from market_data.numcat.market_provider import NumCatGatewayError, numcat_market_provider
from services.data_collector import collector, is_a_share_market_session, shanghai_now


PERIOD_SESSIONS = {"week": 5, "month": 20, "quarter": 60, "half_year": 120}
BOARD_TYPES = {"all", "industry", "concept", "selected"}
WORKSPACE_CACHE_PREFIX = "topic_strength_workspace_v1:"
WORKSPACE_LIVE_CACHE_SECONDS = 45


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    parsed = _number(value)
    return int(parsed) if parsed is not None else None


def _date_value(value: Any) -> date | None:
    text = str(value or "").strip()
    if len(text) >= 8 and text[:8].isdigit():
        try:
            return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:8]}")
        except ValueError:
            return None
    try:
        return date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        return None


def _date_text(value: Any) -> str | None:
    parsed = _date_value(value)
    return parsed.isoformat() if parsed else None


def _first_present(*values: Any) -> Any:
    """Return the first value that is actually present, keeping numeric zero."""
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _sum_known(values: Iterable[Any]) -> float | None:
    numbers = [_number(value) for value in values]
    numbers = [value for value in numbers if value is not None]
    return sum(numbers) if numbers else None


def _minmax_scores(values: list[float | None]) -> list[float | None]:
    known = [value for value in values if value is not None]
    if not known:
        return [None for _ in values]
    low, high = min(known), max(known)
    if high == low:
        return [50.0 if value is not None else None for value in values]
    return [round((value - low) / (high - low) * 100, 1) if value is not None else None for value in values]


def _section(
    rows: list[dict[str, Any]],
    *,
    source: str,
    data_date: str | None,
    realtime: bool,
    error: str | None = None,
    cache_hit: bool = False,
) -> dict[str, Any]:
    return {
        "available": bool(rows),
        "rows": rows,
        "count": len(rows),
        "source": source if rows else "unavailable",
        "data_date": data_date,
        "updated_at": shanghai_now().isoformat(),
        "is_realtime": bool(rows and realtime),
        "cache_hit": cache_hit,
        "error": error,
    }


class TopicStrengthWorkspaceService:
    """Build the single-page topic/ranking workspace without cross-section failure."""

    @staticmethod
    async def _read_cache(key: str) -> dict[str, Any] | None:
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, key)
            return dict(row.payload) if row and isinstance(row.payload, dict) else None
        except Exception:
            return None

    @staticmethod
    async def _write_cache(key: str, payload: dict[str, Any]) -> None:
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, key)
                if row is None:
                    session.add(MarketDataCache(key=key, payload=payload))
                else:
                    row.payload = payload
                    row.updated_at = datetime.utcnow()
                await session.commit()
        except Exception as exc:
            print(f"Topic workspace cache write failed: {type(exc).__name__}")

    @staticmethod
    def _fresh(payload: dict[str, Any]) -> bool:
        try:
            updated = datetime.fromisoformat(str(payload.get("updated_at") or ""))
        except ValueError:
            return False
        now = shanghai_now()
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=now.tzinfo)
        return 0 <= (now - updated).total_seconds() <= WORKSPACE_LIVE_CACHE_SECONDS

    @staticmethod
    def _serve_cached(payload: dict[str, Any]) -> dict[str, Any]:
        """Mark a persisted snapshot as cached all the way down to each section."""
        served = dict(payload)
        served["cache_hit"] = True
        served["is_realtime"] = False
        served["partial_cache_hit"] = False
        sections = dict(served.get("sections") or {})
        for key, section in sections.items():
            if not isinstance(section, dict):
                continue
            copied = dict(section)
            copied["is_realtime"] = False
            copied["cache_hit"] = True
            sections[key] = copied
        served["sections"] = sections
        return served

    @staticmethod
    def _section_or_cache(
        rows: list[dict[str, Any]],
        *,
        source: str,
        data_date: str | None,
        realtime: bool,
        error: str | None,
        previous: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        if rows:
            return _section(rows, source=source, data_date=data_date, realtime=realtime, error=error), False
        previous_rows = previous.get("rows") if isinstance(previous, dict) else None
        if isinstance(previous_rows, list) and previous_rows:
            fallback_error = error or "当前接口未返回数据，已使用最近成功缓存"
            copied = dict(previous)
            copied.update({
                "available": True,
                "rows": previous_rows,
                "count": len(previous_rows),
                "is_realtime": False,
                "cache_hit": True,
                "error": fallback_error,
            })
            return copied, True
        return _section([], source=source, data_date=data_date, realtime=False, error=error), False

    @staticmethod
    def _market_stats_or_cache(
        style_rows: list[dict[str, Any]],
        stat_rows: list[dict[str, Any]],
        *,
        style_error: str | None,
        stat_error: str | None,
        latest: str | None,
        previous: dict[str, Any] | None,
        realtime: bool,
    ) -> tuple[dict[str, Any], bool]:
        previous = previous if isinstance(previous, dict) else {}
        cached_style = previous.get("style") if isinstance(previous.get("style"), list) else []
        cached_stat = previous.get("statistics") if isinstance(previous.get("statistics"), list) else []
        style_cached = not style_rows and bool(cached_style)
        stat_cached = not stat_rows and bool(cached_stat)
        served_style = style_rows or cached_style
        served_stat = stat_rows or cached_stat
        errors = [item for item in (style_error, stat_error) if item]
        if style_cached or stat_cached:
            errors.extend(
                item for item in (
                    "风格板块接口未返回数据，已使用最近成功缓存" if style_cached else None,
                    "统计指数接口未返回数据，已使用最近成功缓存" if stat_cached else None,
                ) if item
            )
        return {
            "available": bool(served_style or served_stat),
            "style": served_style,
            "statistics": served_stat,
            "count": len(served_style) + len(served_stat),
            "source": "+".join(source for source, rows in (
                ("numcat_theme_style_daily", style_rows),
                ("numcat_theme_stat_daily", stat_rows),
            ) if rows) or (previous.get("source") if previous.get("source") else "unavailable"),
            "data_date": TopicStrengthWorkspaceService._latest_date([*served_style, *served_stat]) or latest,
            "updated_at": shanghai_now().isoformat(),
            "is_realtime": bool(realtime and (style_rows or stat_rows)),
            "cache_hit": bool(style_cached or stat_cached),
            "error": "; ".join(errors) if errors else None,
        }, bool(style_cached or stat_cached)

    @staticmethod
    async def _safe(awaitable: Any, fallback: Any, timeout: float = 12.0) -> Any:
        try:
            return await asyncio.wait_for(awaitable, timeout=timeout), None
        except Exception as exc:
            return fallback, f"{type(exc).__name__}: {str(exc)[:120]}"

    async def _latest_db_date(self) -> date | None:
        candidates: list[date] = []
        try:
            async with async_session() as session:
                for model in (IndustryFundFlowDaily, ConceptFundFlowDaily):
                    value = (await session.execute(select(func.max(model.trade_date)))).scalar_one_or_none()
                    if value:
                        candidates.append(value)
        except Exception:
            return None
        return max(candidates, default=None)

    async def _flow_history(self, board_type: str, end_date: date, sessions: int) -> tuple[list[dict[str, Any]], str]:
        models: list[tuple[str, Any]] = []
        if board_type in {"all", "industry"}:
            models.append(("industry", IndustryFundFlowDaily))
        if board_type in {"all", "concept"}:
            models.append(("concept", ConceptFundFlowDaily))
        if not models:
            return [], "database_fund_flow"
        start_date = end_date - timedelta(days=max(sessions * 2 + 10, 20))
        rows: list[dict[str, Any]] = []
        try:
            async with async_session() as session:
                for kind, model in models:
                    result = await session.execute(
                        select(model).where(
                            model.trade_date >= start_date,
                            model.trade_date <= end_date,
                        )
                    )
                    for item in result.scalars().all():
                        rows.append({
                            "board_type": kind,
                            "code": str(item.board_code),
                            "trade_date": item.trade_date.isoformat(),
                            "close": _number(item.close_price),
                            "change_pct": _number(item.change_pct),
                            "main_net_inflow": _number(item.main_net_inflow),
                            "up_count": _integer(item.up_count),
                            "down_count": _integer(item.down_count),
                            "source": "database_fund_flow",
                        })
        except Exception as exc:
            return [], f"database_fund_flow_error:{type(exc).__name__}"
        # The broad calendar range above avoids missing holidays.  The actual
        # ranking window must still be the latest N distinct trading dates.
        trade_dates = sorted({row["trade_date"] for row in rows if row.get("trade_date")}, reverse=True)
        allowed_dates = set(trade_dates[:sessions])
        return [row for row in rows if row.get("trade_date") in allowed_dates], "database_fund_flow"

    async def _board_names(self, codes: set[str]) -> dict[str, str]:
        if not codes:
            return {}
        try:
            async with async_session() as session:
                result = await session.execute(select(MarketBoard).where(MarketBoard.code.in_(codes)))
                return {str(row.code): str(row.name) for row in result.scalars().all() if row.name}
        except Exception:
            return {}

    @staticmethod
    def _build_rankings(rows: list[dict[str, Any]], limit: int, expected_sessions: int | None = None) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            key = (str(row.get("board_type") or ""), str(row.get("code") or ""))
            if key[1]:
                grouped[key].append(row)
        output: list[dict[str, Any]] = []
        for (board_type, code), items in grouped.items():
            items.sort(key=lambda item: str(item.get("trade_date") or ""))
            item_dates = {str(item.get("trade_date")) for item in items if item.get("trade_date")}
            closes = [item.get("close") for item in items if item.get("close") is not None]
            returns = [item.get("change_pct") for item in items if item.get("change_pct") is not None]
            period_return = ((closes[-1] / closes[0] - 1) * 100) if len(closes) >= 2 and closes[0] else None
            if period_return is None and returns:
                compounded = 1.0
                for value in returns:
                    compounded *= 1 + value / 100
                period_return = (compounded - 1) * 100
            flows = [item.get("main_net_inflow") for item in items if item.get("main_net_inflow") is not None]
            up = sum(item["up_count"] for item in items if item.get("up_count") is not None)
            down = sum(item["down_count"] for item in items if item.get("down_count") is not None)
            output.append({
                "code": code,
                "name": code,
                "board_type": board_type,
                "data_date": sorted(items, key=lambda item: str(item.get("trade_date") or ""))[-1].get("trade_date"),
                "period_return_pct": round(period_return, 2) if period_return is not None else None,
                "main_net_inflow": round(sum(flows), 2) if flows else None,
                "flow_sessions": len(flows),
                "coverage": min(100.0, round(
                    len(item_dates) / max(expected_sessions or len(item_dates) or 1, 1) * 100,
                    1,
                )),
                "positive_flow_ratio": round(sum(value > 0 for value in flows) / len(flows) * 100, 1) if flows else None,
                "breadth_pct": round(up / (up + down) * 100, 1) if up + down else None,
                "session_count": len(item_dates),
                "source": sorted(items, key=lambda item: str(item.get("trade_date") or ""))[-1].get("source") or "database_fund_flow",
            })
        if not output:
            return []
        return_values = _minmax_scores([item["period_return_pct"] for item in output])
        flow_values = _minmax_scores([item["main_net_inflow"] for item in output])
        breadth_values = _minmax_scores([item["breadth_pct"] for item in output])
        continuity_values = _minmax_scores([item["positive_flow_ratio"] for item in output])
        for index, item in enumerate(output):
            components = [
                (return_values[index], 0.35),
                (flow_values[index], 0.30),
                (continuity_values[index], 0.15),
                (breadth_values[index], 0.20),
            ]
            known = [(value, weight) for value, weight in components if value is not None]
            item["strength_score"] = round(sum(value * weight for value, weight in known) / sum(weight for _, weight in known), 1) if known else None
            item["primary_factors"] = [
                label for label, value in (
                    ("周期涨幅", item["period_return_pct"]),
                    ("资金净流入", item["main_net_inflow"]),
                    ("资金持续性", item["positive_flow_ratio"]),
                    ("上涨宽度", item["breadth_pct"]),
                ) if value is not None
            ][:3]
        output.sort(key=lambda item: (
            item.get("strength_score") is None,
            -(item["strength_score"] if item.get("strength_score") is not None else 0),
            item.get("period_return_pct") is None,
            -(item["period_return_pct"] if item.get("period_return_pct") is not None else 0),
            item.get("main_net_inflow") is None,
            -(item["main_net_inflow"] if item.get("main_net_inflow") is not None else 0),
            str(item.get("board_type") or ""),
            str(item.get("code") or ""),
        ))
        for index, item in enumerate(output[:limit], start=1):
            item["rank"] = index
        return output[:limit]

    async def _period_rankings(self, period: str, board_type: str, limit: int, end_date: date) -> list[dict[str, Any]]:
        rows, _ = await self._flow_history(board_type, end_date, PERIOD_SESSIONS[period])
        names = await self._board_names({str(row["code"]) for row in rows})
        rankings = self._build_rankings(rows, limit, PERIOD_SESSIONS[period])
        for item in rankings:
            item["name"] = names.get(item["code"], item["code"])
        return rankings

    @staticmethod
    def _normalise_provider_theme(row: dict[str, Any]) -> dict[str, Any] | None:
        code = str(row.get("theme_symbol") or row.get("symbol") or "").strip()
        if not code:
            return None
        return {
            "code": code,
            "name": str(row.get("theme_name") or row.get("name") or code),
            "change_pct": _number(_first_present(row.get("pct_chg"), row.get("change_pct"))),
            "strength": _number(row.get("strength")),
            "main_net_inflow": _number(_first_present(row.get("main_net_amount"), row.get("main_net_inflow"))),
            "trade_date": _date_text(row.get("tradedate") or row.get("trade_date")),
            "source": row.get("source") or "numcat",
        }

    @staticmethod
    def _provider_rankings(theme_rows: list[dict[str, Any]], flow_rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for raw in [*theme_rows, *flow_rows]:
            row = TopicStrengthWorkspaceService._normalise_provider_theme(raw)
            if row:
                grouped[row["code"]].append(row)
        output = []
        for code, items in grouped.items():
            names = [str(item.get("name") or "") for item in items if item.get("name")]
            changes = [item["change_pct"] for item in items if item.get("change_pct") is not None]
            flows = [item["main_net_inflow"] for item in items if item.get("main_net_inflow") is not None]
            latest = sorted(items, key=lambda item: str(item.get("trade_date") or ""))[-1]
            output.append({
                "code": code,
                "name": names[-1] if names else code,
                "board_type": "selected",
                "data_date": latest.get("trade_date"),
                "period_return_pct": round((math.prod(1 + value / 100 for value in changes) - 1) * 100, 2) if changes else None,
                "main_net_inflow": round(sum(flows), 2) if flows else None,
                "flow_sessions": len(flows),
                "coverage": None,
                "positive_flow_ratio": round(sum(value > 0 for value in flows) / len(flows) * 100, 1) if flows else None,
                "breadth_pct": None,
                "session_count": len(items),
                "source": "numcat_selected_theme",
                "raw_strength": latest.get("strength"),
            })
        scores = _minmax_scores([item["period_return_pct"] for item in output])
        flows = _minmax_scores([item["main_net_inflow"] for item in output])
        for index, item in enumerate(output):
            known = [(value, weight) for value, weight in ((scores[index], 0.55), (flows[index], 0.45)) if value is not None]
            item["strength_score"] = round(sum(value * weight for value, weight in known) / sum(weight for _, weight in known), 1) if known else item.get("raw_strength")
            item["primary_factors"] = [label for label, value in (("周期涨幅", item["period_return_pct"]), ("资金净流入", item["main_net_inflow"]), ("精选板块强度", item.get("raw_strength"))) if value is not None]
        output.sort(key=lambda item: (
            item.get("strength_score") is None,
            -(item["strength_score"] if item.get("strength_score") is not None else 0),
            str(item.get("code") or ""),
        ))
        for index, item in enumerate(output[:limit], start=1):
            item["rank"] = index
        return output[:limit]

    @staticmethod
    def _selected_boards(theme_rows: list[dict[str, Any]], member_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        member_map = {str(row.get("theme_symbol")): row for row in member_rows if row.get("theme_symbol")}
        latest: dict[str, dict[str, Any]] = {}
        for raw in sorted(theme_rows, key=lambda item: str(item.get("tradedate") or item.get("trade_date") or "")):
            row = TopicStrengthWorkspaceService._normalise_provider_theme(raw)
            if row:
                latest[row["code"]] = row
        output = []
        for code, row in latest.items():
            members = member_map.get(code, {}).get("symbols") or []
            output.append({
                "code": code,
                "name": row["name"],
                "change_pct": row.get("change_pct"),
                "strength": row.get("strength"),
                "main_net_inflow": row.get("main_net_inflow"),
                "member_count": len(members),
                "member_codes": members[:100],
                "data_date": row.get("trade_date"),
                "source": "numcat_selected_theme",
            })
        output.sort(key=lambda item: item.get("strength") if item.get("strength") is not None else -math.inf, reverse=True)
        return output[:50]

    @staticmethod
    def _latest_date(rows: list[dict[str, Any]]) -> str | None:
        dates = [row.get("data_date") or row.get("trade_date") or row.get("source_day") for row in rows]
        valid = [item for item in dates if _date_value(item)]
        return max(valid) if valid else None

    async def get(self, *, period: str = "week", board_type: str = "all", refresh: bool = False, limit: int = 30) -> dict[str, Any]:
        if period not in PERIOD_SESSIONS:
            raise ValueError("周期仅支持week、month、quarter、half_year")
        if board_type not in BOARD_TYPES:
            raise ValueError("板块类型仅支持all、industry、concept、selected")
        bounded_limit = min(max(int(limit), 5), 100)
        cache_key = f"{WORKSPACE_CACHE_PREFIX}{period}:{board_type}"
        closed = not is_a_share_market_session(shanghai_now())
        cached = await self._read_cache(cache_key)
        if cached and not refresh and (closed or self._fresh(cached)):
            return self._serve_cached(cached) if closed else {**cached, "cache_hit": True}
        end_date = await self._latest_db_date() or shanghai_now().date()
        sessions = PERIOD_SESSIONS[period]
        rankings_task = (
            self._period_rankings(period, board_type, bounded_limit, end_date)
            if board_type != "selected"
            else asyncio.sleep(0, result=[])
        )
        if not numcat_market_provider.configured:
            rankings = await rankings_task
            if cached:
                served = self._serve_cached(cached)
                served["rankings"] = rankings or served.get("rankings", [])
                served["quality"] = {
                    **(served.get("quality") or {}),
                    "provider_configured": False,
                    "errors": ["NumCat未配置，已使用最近成功工作台缓存"],
                }
                return served
            payload = self._empty_provider_payload(rankings, period, board_type, end_date)
            await self._write_cache(cache_key, payload)
            return payload

        results = await asyncio.gather(
            self._safe(rankings_task, [], 14.0),
            self._safe(numcat_market_provider.theme_daily(level="parent", recentdays=sessions), [], 14.0),
            self._safe(numcat_market_provider.theme_fund_flow(), [], 10.0),
            self._safe(numcat_market_provider.theme_members(level="parent"), [], 12.0),
            self._safe(numcat_market_provider.theme_members(level="parent", tag="authentic"), [], 12.0),
            self._safe(numcat_market_provider.theme_members(level="parent", tag="long", tradedate=end_date), [], 12.0),
            self._safe(numcat_market_provider.theme_auction(), [], 10.0),
            self._safe(numcat_market_provider.hot_stock(ranking_type="xq&resou", limit=30), [], 10.0),
            self._safe(numcat_market_provider.strongest_fengkou(limit=30), [], 10.0),
            self._safe(numcat_market_provider.theme_library(), [], 12.0),
            self._safe(numcat_market_provider.theme_reason(source="xgb", recentdays=5), [], 10.0),
            self._safe(numcat_market_provider.theme_style_daily(recentdays=min(sessions, 20)), [], 10.0),
            self._safe(numcat_market_provider.theme_stat_daily(recentdays=min(sessions, 20)), [], 10.0),
        )
        rankings, rankings_error = results[0]
        theme_rows, theme_error = results[1]
        flow_rows, flow_error = results[2]
        member_rows, member_error = results[3]
        authentic_rows, authentic_error = results[4]
        long_rows, long_error = results[5]
        auction_rows, auction_error = results[6]
        hot_rows, hot_error = results[7]
        fengkou_rows, fengkou_error = results[8]
        library_rows, library_error = results[9]
        reason_rows, reason_error = results[10]
        style_rows, style_error = results[11]
        stat_rows, stat_error = results[12]
        if board_type == "selected":
            rankings = self._provider_rankings(theme_rows, flow_rows, bounded_limit)
        if not rankings and isinstance(cached, dict) and cached.get("rankings"):
            rankings = cached["rankings"]
            rankings_error = rankings_error or "周期排名接口暂未返回数据，已使用最近成功缓存"
        selected_rows = self._selected_boards(theme_rows, member_rows)
        auth_map = {str(row.get("theme_symbol")): row.get("symbols") or [] for row in authentic_rows}
        long_map = {str(row.get("theme_symbol")): row.get("symbols") or [] for row in long_rows}
        for item in selected_rows:
            item["authentic_codes"] = auth_map.get(item["code"], [])[:20]
            item["long_codes"] = long_map.get(item["code"], [])[:20]
        latest = self._latest_date([*rankings, *theme_rows, *auction_rows, *hot_rows, *fengkou_rows]) or (
            cached.get("data_date") if isinstance(cached, dict) else None
        ) or end_date.isoformat()
        previous_sections = cached.get("sections") if isinstance(cached, dict) else {}
        section_specs = (
            ("selected_boards", selected_rows, "numcat_selected_theme", latest, theme_error or member_error),
            ("hot_search", hot_rows[:30], "numcat_hotstock", self._latest_date(hot_rows) or latest, hot_error),
            ("auction", auction_rows[:50], "numcat_theme_auc_kp", self._latest_date(auction_rows) or latest, auction_error),
            ("main_net", flow_rows[:50], "numcat_themefundflow_jx", self._latest_date(flow_rows) or latest, flow_error),
            ("strongest_fengkou", fengkou_rows[:30], "numcat_fengk_kp", self._latest_date(fengkou_rows) or latest, fengkou_error),
            ("theme_library", library_rows[:100], "numcat_theme_lib_kp", latest, library_error),
            ("theme_reasons", reason_rows[:50], "numcat_theme_reason", self._latest_date(reason_rows) or latest, reason_error),
        )
        sections: dict[str, dict[str, Any]] = {}
        partial_cache_hit = False
        for key, rows, source, data_date, section_error in section_specs:
            sections[key], used_cache = self._section_or_cache(
                rows,
                source=source,
                data_date=data_date,
                realtime=not closed,
                error=section_error,
                previous=previous_sections.get(key) if isinstance(previous_sections, dict) else None,
            )
            partial_cache_hit = partial_cache_hit or used_cache
        sections["market_stats"], stats_cache_hit = self._market_stats_or_cache(
            style_rows[:60],
            stat_rows[:60],
            style_error=style_error,
            stat_error=stat_error,
            latest=latest,
            previous=previous_sections.get("market_stats") if isinstance(previous_sections, dict) else None,
            realtime=not closed,
        )
        partial_cache_hit = partial_cache_hit or stats_cache_hit
        payload = {
            "available": bool(rankings or any(item.get("available") for item in sections.values() if isinstance(item, dict))),
            "period": period,
            "period_sessions": sessions,
            "board_type": board_type,
            "updated_at": shanghai_now().isoformat(),
            "data_date": latest,
            "is_realtime": bool(not closed and any(item.get("is_realtime") for item in sections.values() if isinstance(item, dict))),
            "cache_hit": False,
            "partial_cache_hit": partial_cache_hit,
            "source": "database_fund_flow+numcat" if rankings or sections["selected_boards"]["available"] else "numcat",
            "rankings": rankings,
            "sections": sections,
            "quality": {
                "provider_configured": True,
                "closed_market_cache_policy": "闭市读取最近一次持久化工作台快照；实时字段不会冒充实时",
                "period_definition": f"最近{sessions}个有效交易日",
                "coverage": round(sum(item.get("session_count", 0) for item in rankings) / max(len(rankings) * sessions, 1) * 100, 1) if rankings else 0,
                "errors": [item for item in (rankings_error, theme_error, flow_error, member_error, authentic_error, long_error, auction_error, hot_error, fengkou_error, library_error, reason_error, style_error, stat_error) if item],
            },
        }
        await self._write_cache(cache_key, payload)
        return payload

    @staticmethod
    def _empty_provider_payload(rankings: list[dict[str, Any]], period: str, board_type: str, end_date: date) -> dict[str, Any]:
        empty = _section([], source="unavailable", data_date=end_date.isoformat(), realtime=False, error="NumCat未配置，页面仅显示已有历史资金缓存")
        return {
            "available": bool(rankings), "period": period, "period_sessions": PERIOD_SESSIONS[period], "board_type": board_type,
            "updated_at": shanghai_now().isoformat(), "data_date": end_date.isoformat(), "is_realtime": False, "cache_hit": False,
            "source": "database_fund_flow", "rankings": rankings,
            "sections": {
                **{key: empty for key in ("selected_boards", "hot_search", "auction", "main_net", "strongest_fengkou", "theme_library", "theme_reasons")},
                "market_stats": {"available": False, "style": [], "statistics": [], "count": 0, "source": "unavailable", "data_date": end_date.isoformat(), "updated_at": shanghai_now().isoformat(), "is_realtime": False, "cache_hit": False, "error": "NumCat未配置"},
            },
            "quality": {"provider_configured": False, "closed_market_cache_policy": "使用数据库历史资金缓存", "period_definition": f"最近{PERIOD_SESSIONS[period]}个有效交易日", "coverage": 100 if rankings else 0, "errors": ["NumCat未配置"]},
        }

    async def stocks(self, board_code: str, *, period: str = "week", sort: str = "strength", limit: int = 50, refresh: bool = False) -> dict[str, Any]:
        if period not in PERIOD_SESSIONS:
            raise ValueError("周期仅支持week、month、quarter、half_year")
        code = str(board_code or "").strip()
        if not code:
            raise ValueError("板块编码不能为空")
        bounded_limit = min(max(int(limit), 5), 100)
        cache_key = f"{WORKSPACE_CACHE_PREFIX}members:{code}:{period}"
        cached = await self._read_cache(cache_key)
        if cached and not refresh and (not is_a_share_market_session(shanghai_now()) or self._fresh(cached)):
            return {**cached, "cache_hit": True}
        source = "unavailable"
        rows: list[dict[str, Any]] = []
        authentic: set[str] = set()
        dragon: set[str] = set()
        errors: list[str] = []
        if code.upper().startswith("BK"):
            try:
                result = await collector.fetch_board_stocks(code, page=1, page_size=100, sort_field="f62")
                rows = [{**item, "stock_code": item.get("code"), "stock_name": item.get("name"), "main_net_amount": item.get("main_net_inflow"), "change_pct": item.get("change_pct")} for item in result.get("stocks") or []]
                source = "eastmoney_board_constituents"
                if result.get("error"):
                    errors.append(str(result["error"]))
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {str(exc)[:120]}")
        elif numcat_market_provider.configured:
            member_result, authentic_result, long_result = await asyncio.gather(
                self._safe(numcat_market_provider.theme_members(theme_symbols=[code]), [], 12.0),
                self._safe(numcat_market_provider.theme_members(theme_symbols=[code], tag="authentic"), [], 12.0),
                self._safe(numcat_market_provider.theme_members(theme_symbols=[code], tag="long", tradedate=await self._latest_db_date()), [], 12.0),
            )
            member_rows, member_error = member_result
            authentic_rows, authentic_error = authentic_result
            long_rows, long_error = long_result
            errors.extend(item for item in (member_error, authentic_error, long_error) if item)
            codes = list(dict.fromkeys(
                str(symbol)
                for member_row in member_rows
                for symbol in (member_row.get("symbols") or [])
                if symbol
            ))
            authentic = {str(item) for row in authentic_rows for item in (row.get("symbols") or [])}
            dragon = {str(item) for row in long_rows for item in (row.get("symbols") or [])}
            if codes:
                try:
                    quote_result = await collector.fetch_stock_quotes(codes[:100])
                    rows = list(quote_result.get("stocks") or [])
                    source = str(quote_result.get("source") or "tencent/eastmoney")
                except Exception as exc:
                    errors.append(f"quote:{type(exc).__name__}: {str(exc)[:120]}")
            if not rows:
                rows = [{"stock_code": item, "stock_name": item} for item in codes]
                source = "numcat_thememembers_jx"
        else:
            errors.append("NumCat未配置，无法读取精选板块成员")
        flow_by_code: dict[str, dict[str, Any]] = {}
        codes = [str(row.get("stock_code") or row.get("code") or "") for row in rows]
        if codes:
            try:
                async with async_session() as session:
                    result = await session.execute(
                        select(StockFundFlowDaily).where(StockFundFlowDaily.stock_code.in_(codes)).order_by(desc(StockFundFlowDaily.trade_date))
                    )
                    for item in result.scalars().all():
                        flow_by_code.setdefault(item.stock_code, {
                            "trade_date": item.trade_date.isoformat(),
                            "main_net_amount": item.main_net_inflow,
                        })
            except Exception:
                pass
        output = []
        for row in rows:
            stock_code = str(row.get("stock_code") or row.get("code") or "")
            if not stock_code:
                continue
            flow = flow_by_code.get(stock_code, {})
            output.append({
                "rank": 0,
                "code": stock_code,
                "name": str(row.get("stock_name") or row.get("name") or stock_code),
                "change_pct": _number(row.get("change_pct")),
                "price": _number(row.get("price")),
                "main_net_amount": _number(row.get("main_net_inflow")) if row.get("main_net_inflow") is not None else _number(flow.get("main_net_amount")),
                "volume_ratio": _number(row.get("volume_ratio")),
                "turnover": _number(row.get("turnover")),
                "is_authentic": stock_code in authentic,
                "is_dragon_ranked": stock_code in dragon,
                "source": source,
            })
        if sort == "flow":
            output.sort(key=lambda item: item.get("main_net_amount") if item.get("main_net_amount") is not None else -math.inf, reverse=True)
        elif sort == "change":
            output.sort(key=lambda item: item.get("change_pct") if item.get("change_pct") is not None else -math.inf, reverse=True)
        else:
            output.sort(key=lambda item: (item.get("is_authentic"), item.get("is_dragon_ranked"), item.get("main_net_amount") or -math.inf, item.get("change_pct") or -math.inf), reverse=True)
        for index, item in enumerate(output[:bounded_limit], start=1):
            item["rank"] = index
        if not output and isinstance(cached, dict) and cached.get("rows"):
            fallback = dict(cached)
            fallback["cache_hit"] = True
            fallback["is_realtime"] = False
            fallback["errors"] = [*list(fallback.get("errors") or []), *errors, "当前成员/行情接口未返回数据，已使用最近成功缓存"]
            return fallback
        payload = {
            "available": bool(output), "board_code": code, "period": period, "count": len(output), "rows": output[:bounded_limit],
            "updated_at": shanghai_now().isoformat(), "data_date": max((item.get("trade_date") for item in flow_by_code.values()), default=None),
            "is_realtime": bool(output and is_a_share_market_session(shanghai_now()) and source != "numcat_thememembers_jx"), "cache_hit": False,
            "source": source, "errors": errors, "quality": {"member_count": len(codes), "quote_count": len(output), "authentic_count": len(authentic), "dragon_count": len(dragon)},
        }
        await self._write_cache(cache_key, payload)
        return payload

    async def theme_detail(self, theme_id: str) -> dict[str, Any]:
        if not numcat_market_provider.configured:
            return {"available": False, "theme_id": theme_id, "source": "unavailable", "error": "NumCat未配置"}
        try:
            detail = await numcat_market_provider.theme_library_detail(theme_id)
            return {"available": bool(detail), "theme_id": theme_id, "detail": detail, "source": detail.get("source", "numcat_theme_lib_detail_kp"), "cache_hit": False}
        except (NumCatGatewayError, ValueError) as exc:
            return {"available": False, "theme_id": theme_id, "source": "unavailable", "error": str(exc)}


topic_strength_workspace_service = TopicStrengthWorkspaceService()


__all__ = ["TopicStrengthWorkspaceService", "topic_strength_workspace_service", "PERIOD_SESSIONS", "BOARD_TYPES"]
