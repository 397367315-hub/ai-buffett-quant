"""Bounded NumCat collection for Wildman historical facts.

The parent Wildman service owns integration. This module exposes a small
adapter contract so it can merge historical overrides into current facts
without changing rule evaluation or persistence schemas.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from datetime import date
from typing import Any

from sqlalchemy import desc, select

from database import async_session
from market_data.numcat.market_provider import numcat_market_provider
from models import WildmanMarketCycle
from wildman.history import (
    MAX_HISTORY_TRADING_DAYS,
    bounded_trade_dates,
    derive_historical_overrides,
    normalize_code,
    normalize_trade_date,
)


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in ("stocks", "rows", "items", "data"):
            if isinstance(value.get(key), list):
                return [row for row in value[key] if isinstance(row, dict)]
        return [value]
    return [row for row in value if isinstance(row, dict)] if isinstance(value, (list, tuple)) else []


def _current_rows(value: Any) -> list[dict[str, Any]]:
    return _rows(value)


def _bar_dates(value: Any) -> list[Any]:
    dates: list[Any] = []
    groups = value.values() if isinstance(value, dict) else [value]
    for group in groups:
        if isinstance(group, dict):
            group = group.get("bars") or group.get("rows") or []
        elif not isinstance(group, (list, tuple)):
            group = [group]
        for row in group or []:
            raw = row if isinstance(row, dict) else vars(row)
            dates.append(raw.get("trade_date") or raw.get("tradedate") or raw.get("date"))
    return dates


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _sentiment_proxy_rows(value: Any, target: date) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("rows") or value.get("items") or value.get("data") or []
    output: list[dict[str, Any]] = []
    for row in value or []:
        def get(key: str) -> Any:
            return row.get(key) if isinstance(row, dict) else getattr(row, key, None)

        day = get("trade_date")
        if not day or str(day)[:10] > target.isoformat():
            continue
        up = _number(get("limit_up_count"))
        down = _number(get("limit_down_count"))
        height = _number(get("max_streak_height"))
        failed = _number(get("failed_limit_rate"))
        if None in (up, down, height, failed):
            continue
        stage = None
        if height <= 1 and down >= up:
            stage = "ice_proxy"
        elif height >= 3 and up > down and failed <= 0.3:
            stage = "main_rise_proxy"
        elif height <= 2 and down > up:
            stage = "retreat_proxy"
        if stage:
            output.append({"trade_date": day, "cycle": stage, "source": "sentiment_numeric_proxy"})
    return output


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class WildmanHistoryService:
    """Collect at most one compact twenty-trading-day evidence window."""

    def __init__(self, provider: Any = None) -> None:
        self.provider = provider or numcat_market_provider
        self._cache: dict[str, tuple[float, Any]] = {}
        self._semaphore: asyncio.Semaphore | None = None

    def _request_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(4)
        return self._semaphore

    async def _provider_call(
        self,
        method: str,
        *args: Any,
        timeout: float = 8.0,
        cache_ttl: float = 45.0,
        **kwargs: Any,
    ) -> Any:
        function = getattr(self.provider, method, None)
        if function is None:
            return None
        cache_key = repr((method, args, sorted(kwargs.items(), key=lambda item: item[0])))
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] <= cache_ttl:
            return cached[1]
        try:
            async with self._request_semaphore():
                result = await asyncio.wait_for(
                    _maybe_await(function(*args, **kwargs)),
                    timeout=timeout,
                )
        except (asyncio.TimeoutError, TypeError, ValueError, OSError):
            return None
        except Exception:
            return None
        if result is not None and cache_ttl > 0:
            if len(self._cache) >= 64:
                self._cache.pop(next(iter(self._cache)))
            self._cache[cache_key] = (time.monotonic(), result)
        return result

    async def _limit_history(
        self,
        target: date,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[date], list[str]]:
        missing: list[str] = []
        up_payload = await self._provider_call(
            "limit_pool", "u", recentdays=MAX_HISTORY_TRADING_DAYS
        )
        failed_payload = await self._provider_call(
            "limit_pool", "ub", recentdays=MAX_HISTORY_TRADING_DAYS
        )
        up_rows = _rows(up_payload)
        failed_rows = _rows(failed_payload)
        observed = [
            row.get("trade_date") or row.get("tradedate")
            for row in (*up_rows, *failed_rows)
        ]
        dates = bounded_trade_dates(target, observed)
        if not up_rows:
            missing.append("missing_dated_limit_up_history")
        if not failed_rows:
            missing.append("missing_dated_failed_limit_history")
        return up_rows, failed_rows, dates, missing

    async def _stored_cycle_rows(self, target: date) -> list[dict[str, Any]]:
        try:
            async with async_session() as session:
                result = await asyncio.wait_for(
                    session.execute(
                        select(WildmanMarketCycle)
                        .where(WildmanMarketCycle.trade_date <= target)
                        .order_by(desc(WildmanMarketCycle.trade_date), desc(WildmanMarketCycle.id))
                        .limit(MAX_HISTORY_TRADING_DAYS * 3)
                    ),
                    timeout=4.0,
                )
                rows = result.scalars().all()
        except Exception:
            return []
        latest: dict[date, dict[str, Any]] = {}
        for row in rows:
            if row.trade_date in latest:
                continue
            latest[row.trade_date] = {
                "trade_date": row.trade_date,
                "cycle": row.cycle,
                "cycle_node": row.cycle_node,
                "rule_version": row.rule_version,
                "source": "wildman_market_cycle",
            }
        return [latest[day] for day in sorted(latest)][-MAX_HISTORY_TRADING_DAYS:]

    async def build_history_context(
        self,
        target: Any,
        current_rows: Any,
        bars: Any,
        sentiments: Any,
    ) -> dict[str, Any]:
        target_date = normalize_trade_date(target)
        current = _current_rows(current_rows)
        if target_date is None:
            return {
                "stock_overrides": {},
                "theme_overrides": {},
                "market_overrides": {},
                "source": {
                    "provider": "numcat",
                    "bounded_to": MAX_HISTORY_TRADING_DAYS,
                    "observed_dates": [],
                    "missing_reasons": ["invalid_target_date"],
                },
            }

        up_rows, failed_rows, pool_dates, missing = await self._limit_history(target_date)
        input_dates = [
            row.get("trade_date") or row.get("tradedate")
            for row in current
        ]
        if isinstance(sentiments, dict):
            sentiments = sentiments.get("rows") or sentiments.get("items") or sentiments.get("data") or []
        elif sentiments is not None and not isinstance(sentiments, (list, tuple)):
            sentiments = [sentiments]
        stored_cycles = await self._stored_cycle_rows(target_date)
        proxy_cycles = _sentiment_proxy_rows(sentiments, target_date)
        for row in sentiments or []:
            input_dates.append(
                getattr(row, "trade_date", None)
                if not isinstance(row, dict)
                else row.get("trade_date") or row.get("tradedate")
            )
        input_dates.extend(
            row.get("trade_date")
            for row in (*proxy_cycles, *stored_cycles)
        )
        input_dates.extend(_bar_dates(bars))
        dates = bounded_trade_dates(
            target_date,
            [*pool_dates, *input_dates],
            limit=MAX_HISTORY_TRADING_DAYS,
        )
        cycle_sentiments = [*(sentiments or []), *proxy_cycles, *stored_cycles]

        # If an older gateway returned no recentdays rows, ask only for dates
        # already evidenced by bars/sentiments. Calendar weekdays are not
        # invented as trading sessions.
        if dates and (not up_rows or not failed_rows):
            requests = []
            for day in dates:
                requests.append((
                    day,
                    self._provider_call("limit_pool", "u", tradedate=day)
                    if not up_rows else None,
                    self._provider_call("limit_pool", "ub", tradedate=day)
                    if not failed_rows else None,
                ))
            fetched = await asyncio.gather(*(
                asyncio.gather(
                    up_request or asyncio.sleep(0, result=None),
                    failed_request or asyncio.sleep(0, result=None),
                )
                for _, up_request, failed_request in requests
                if up_request is not None or failed_request is not None
            ))
            index = 0
            dated_up = list(up_rows)
            dated_failed = list(failed_rows)
            for _, up_request, failed_request in requests:
                if up_request is None and failed_request is None:
                    continue
                up_result, failed_result = fetched[index]
                index += 1
                if up_request is not None:
                    dated_up.extend(_rows(up_result))
                if failed_request is not None:
                    dated_failed.extend(_rows(failed_result))
            up_rows, failed_rows = dated_up, dated_failed

        theme_daily_rows = await self._provider_call(
            "theme_daily",
            level="parent",
            recentdays=MAX_HISTORY_TRADING_DAYS,
            cache_ttl=120.0,
        )
        symbol_to_name_by_day: dict[str, tuple[date, str]] = {}
        for row in _rows(theme_daily_rows):
            symbol = str(row.get("theme_symbol") or "").strip()
            name = str(row.get("theme_name") or "").strip()
            observed_day = normalize_trade_date(row.get("trade_date") or row.get("tradedate"))
            if not symbol or not name or observed_day is None or observed_day > target_date:
                continue
            previous = symbol_to_name_by_day.get(symbol)
            if previous is None or observed_day >= previous[0]:
                symbol_to_name_by_day[symbol] = (observed_day, name)
        symbol_to_name = {
            symbol: name
            for symbol, (_, name) in symbol_to_name_by_day.items()
        }
        name_to_symbol = {name: symbol for symbol, name in symbol_to_name.items()}
        candidate_symbols: list[set[str]] = []
        for row in current:
            symbols: set[str] = set()
            for key in ("theme_symbol", "theme_id", "theme_symbols", "theme_ids"):
                raw = row.get(key)
                values = raw if isinstance(raw, (list, tuple, set)) else str(raw or "").replace(";", ",").split(",")
                symbols.update(str(value).strip() for value in values if str(value).strip())
            for key in (
                "primary_theme_name", "theme_name", "theme_names_xgb",
                "theme_names_kpl", "theme_names_jygs",
            ):
                raw = row.get(key)
                values = raw if isinstance(raw, (list, tuple, set)) else str(raw or "").replace(";", ",").split(",")
                symbols.update(
                    name_to_symbol[name]
                    for name in (str(value).strip() for value in values)
                    if name in name_to_symbol
                )
            candidate_symbols.append(symbols)
        # Partial symbol coverage is unsafe: it would silently discard
        # cross-industry concepts. An unfiltered dated membership response is
        # bounded and preserves the complete topic universe.
        query_symbols = (
            sorted(set.union(*candidate_symbols))
            if candidate_symbols and all(candidate_symbols)
            else []
        )

        member_rows: list[dict[str, Any]] = []
        member_dates = dates or [target_date]
        relevant_codes = {
            normalize_code(row.get("code") or row.get("symbol"))
            for row in [*current, *up_rows, *failed_rows]
        }
        async def fetch_members(day: date) -> list[dict[str, Any]]:
            kwargs: dict[str, Any] = {"level": "parent", "tradedate": day}
            # Never send an industry/display label as theme_symbols. Without
            # a verified symbol, an unfiltered dated response is safer.
            if query_symbols:
                kwargs["theme_symbols"] = query_symbols
            rows = await self._provider_call("theme_members", cache_ttl=0, cache_result=False, **kwargs)
            output = []
            for row in _rows(rows):
                # Membership outside the current and historical limit pools
                # cannot participate in any leadership calculation below.
                row = {**row, "symbols": [code for code in row.get("symbols", []) if normalize_code(code) in relevant_codes]}
                if row.get("trade_date") or row.get("tradedate"):
                    output.append({
                        **row,
                        "theme_name": row.get("theme_name")
                        or symbol_to_name.get(str(row.get("theme_symbol") or "").strip()),
                    })
                else:
                    # thememembers_jx does not include tradedate in its
                    # response fields; the exact request date is the only
                    # available scope and is disclosed as such downstream.
                    output.append({
                        **row,
                        "trade_date": day.isoformat(),
                        "_date_quality": "request_scoped_date",
                        "theme_name": row.get("theme_name")
                        or symbol_to_name.get(str(row.get("theme_symbol") or "").strip()),
                    })
            return output

        member_batches = await asyncio.gather(*(fetch_members(day) for day in member_dates))
        for batch in member_batches:
            member_rows.extend(batch)

        result = derive_historical_overrides(
            target_date,
            current,
            up_rows,
            member_rows,
            failed_limit_rows=failed_rows,
            bars=bars,
            sentiments=cycle_sentiments,
            requested_dates=dates,
        )
        observed_dates = result["market_overrides"].get("history_dates") or []
        source = {
            "provider": getattr(self.provider, "name", "numcat"),
            "limit_up_source": "numcat_limit_pool",
            "failed_limit_source": "numcat_limit_pool",
            "theme_members_source": "numcat_thememembers_jx",
            "theme_daily_source": "numcat_themedaily_jx" if symbol_to_name else None,
            "theme_symbol_mapping_count": len(symbol_to_name),
            "theme_query_mode": "explicit_symbols" if query_symbols else "all_dated_memberships",
            "observed_dates": observed_dates,
            "coverage": {
                "requested_trading_days": MAX_HISTORY_TRADING_DAYS,
                "observed_trading_days": len(observed_dates),
                "bounded": True,
            },
            "missing_reasons": sorted(set(missing))
            + (["insufficient_theme_history"] if len(observed_dates) < 2 else []),
            "fact_basis": (
                "dated membership/limit-up overlap and multi-day association "
                "proxy; correlation_only_no_causal_claim"
            ),
            "cycle_evidence": {
                "stored_wildman_market_cycle": bool(stored_cycles),
                "numeric_sentiment_proxy": bool(proxy_cycles),
                "missing_reasons": []
                if stored_cycles or proxy_cycles
                else ["missing_cycle_transition_evidence"],
            },
        }
        if not symbol_to_name:
            source["missing_reasons"].append("missing_theme_symbol_name_mapping")
        result["source"] = source
        # Shared only with the in-flight five-step collector; these inputs are
        # never copied into dashboard snapshots or persisted as raw history.
        result["mainline_inputs"] = {
            "limit_history": up_rows,
            "member_history": member_rows,
            "theme_symbols": name_to_symbol,
        }
        return result


_default_history_service = WildmanHistoryService()
wildman_history_service = _default_history_service


async def build_history_context(
    target: Any,
    current_rows: Any,
    bars: Any,
    sentiments: Any,
) -> dict[str, Any]:
    """Public integration contract for the parent Wildman service."""
    return await _default_history_service.build_history_context(
        target, current_rows, bars, sentiments
    )


__all__ = [
    "WildmanHistoryService",
    "build_history_context",
    "wildman_history_service",
]
