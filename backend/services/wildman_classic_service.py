"""Whole-market backend scanner for the Wildman classic toolbox.

The scanner is intentionally independent from ``wildman_service`` and its
qualitative rule core.  It reads one bounded batch at a time, prefers NumCat
for the complete universe and QFQ daily history, and reports every fallback
and coverage gap instead of turning missing data into a negative signal.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import isfinite
from typing import Any

from sqlalchemy import desc, func, select

from database import async_session
from models import FinancialPITSnapshot, PersonalPoolItem, StockDailyBar, StockUniverseSnapshot
from services.data_collector import collector, shanghai_now
from services.history_cache import history_cache
from wildman.classic import STRATEGIES, STRATEGY_IDS, evaluate_classic, indicator_bars, strategy_card

try:
    from market_data.numcat.gateway import NumCatGatewayError, numcat_gateway
    from market_data.numcat.market_provider import DAILY_FIELDS, STK_FACTOR_FIELDS, _market, _rows, numcat_market_provider
except ImportError:  # pragma: no cover - only for stripped-down tooling environments
    NumCatGatewayError = RuntimeError
    numcat_gateway = None
    numcat_market_provider = None
    STK_FACTOR_FIELDS = ""
    DAILY_FIELDS = ""
    _market = lambda code: None


SCAN_CACHE_TTL_SECONDS = 300
HISTORY_BATCH_SIZE = 16
HISTORY_CONCURRENCY = 2
HISTORY_LOOKBACK_DAYS = 400
HISTORY_RECENT_DAYS = 260
HISTORY_TOPUP_LIMIT = 64
DB_BATCH_SIZE = 160
NUMCAT_BATCH_TIMEOUT_SECONDS = 16
MAX_FINISHED_CACHE_ENTRIES = 8
MAX_GLOBAL_SCAN_JOBS = 2
MAX_TOTAL_SCAN_JOBS = 8


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _code(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text.split(".", 1)[0].zfill(6) if text else ""


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _iso(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value) if value not in (None, "") else None


def _date_text(value: Any) -> str:
    parsed = _date(value)
    return parsed.isoformat() if parsed else str(value or "")[:10]


def _chunks(items: list[str], size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _is_special(name: Any) -> bool:
    text = str(name or "").upper()
    return "ST" in text or "退" in text


def _market_exclusion(code: str, meta: dict[str, Any], *, exclude_star_market: bool, exclude_gem: bool) -> str | None:
    if exclude_star_market and code.startswith(("688", "689")):
        return "科创板过滤"
    if exclude_gem and code.startswith(("300", "301", "302")):
        return "创业板过滤"
    if _is_special(meta.get("name")) or str(meta.get("status") or "").lower() in {"delisted", "inactive", "退市"}:
        return "ST/退市过滤"
    if meta.get("is_suspended") is True:
        return "停牌过滤"
    return None


def _provider_row_to_bar(row: dict[str, Any], *, adjustment_basis: str, source_updated_at: str | None) -> dict[str, Any] | None:
    trade_date = _date_text(row.get("tradedate") or row.get("trade_date"))
    if not trade_date:
        return None
    # The factor endpoint exposes both raw and QFQ columns.  A batch chooses
    # one basis for every stock; never mix raw open with adjusted close.
    suffix = "_qfq" if adjustment_basis == "qfq" else ""
    close = row.get(f"close{suffix}")
    open_price = row.get(f"open{suffix}")
    high = row.get(f"high{suffix}")
    low = row.get(f"low{suffix}")
    if any(_number(value) is None for value in (open_price, high, low, close)):
        return None
    return {
        "date": trade_date,
        "open": _number(open_price),
        "high": _number(high),
        "low": _number(low),
        "close": _number(close),
        # NumCat documents ``vol`` in hands; StockDailyBar uses shares.
        "volume": (_number(row.get("vol")) * 100) if _number(row.get("vol")) is not None else None,
        "amount": _number(row.get("amount")),
        "change_pct": _number(row.get("pct_chg")),
        "source": "numcat_stk_factor_pro",
        "adjustment_basis": adjustment_basis,
        "source_updated_at": source_updated_at,
    }


async def fetch_numcat_history_batch(symbols: list[str], *, days: int = HISTORY_RECENT_DAYS, end_date: date | None = None, _market_hint: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Fetch QFQ histories for a symbol batch through one NumCat request.

    This is public on purpose: detail views and future bounded toolbox jobs can
    share the same batch path.  The provider's per-symbol ``daily`` helper is
    not used here because it would turn a whole-market scan into one request
    per stock.  ``end_date`` is applied locally as a final PIT guard because
    the gateway contract differs between deployed NumCat routes.
    """
    if not symbols or numcat_gateway is None or numcat_market_provider is None or not numcat_market_provider.configured:
        return {}
    codes = list(dict.fromkeys(_code(item) for item in symbols if _code(item)))
    if not codes:
        return {}
    # Respect the upstream row cap while retaining enough history for MA75.
    batch_size = 32 if days <= 60 else HISTORY_BATCH_SIZE
    if len(codes) > batch_size:
        combined = {}
        for batch in _chunks(codes, batch_size):
            combined.update(await fetch_numcat_history_batch(batch, days=days, end_date=end_date, _market_hint=_market_hint))
        return combined
    if _market_hint is None:
        grouped: dict[str | None, list[str]] = defaultdict(list)
        for code in codes:
            grouped[_market(code)].append(code)
        if len(grouped) > 1:
            parts = await asyncio.gather(*(
                fetch_numcat_history_batch(group, days=days, end_date=end_date, _market_hint=market)
                for market, group in grouped.items()
            ))
            merged: dict[str, list[dict[str, Any]]] = {}
            for part in parts:
                merged.update(part)
            return merged
    params: dict[str, Any] = {
        "symbols": ",".join(codes),
        "recentdays": min(max(int(days), 1), 800),
    }
    market_hint = _market_hint if _market_hint is not None else _market(codes[0])
    factor_error = None
    try:
        payload = await numcat_gateway.query(
            "stk_factor_pro",
            fields=STK_FACTOR_FIELDS,
            params=params,
            market=market_hint,
            cache_ttl=900,
            affinity_key=f"wildman-classic-history:{','.join(codes)}:{end_date or 'latest'}",
        )
    except NumCatGatewayError as exc:
        factor_error = exc
        payload = {}
    rows = _rows(payload)
    by_code_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        stock_code = _code(row.get("symbol") or row.get("code"))
        if stock_code in codes:
            by_code_rows[stock_code].append(row)
    delayed = [code for code in codes if not by_code_rows[code] or (end_date is not None and max((_date(row.get("tradedate")) or date.min for row in by_code_rows[code]), default=date.min) < end_date)]
    daily_codes: set[str] = set()
    if delayed:
        # Adjusted factors can lag daily quotes. Replace the whole price series
        # with raw daily bars instead of appending unadjusted prices to QFQ.
        try:
            daily_payload = await numcat_gateway.query(
                "daily", fields=DAILY_FIELDS,
                params={"symbols": ",".join(delayed), "recentdays": params["recentdays"]},
                market=market_hint, cache_ttl=900,
                affinity_key=f"wildman-daily:{','.join(delayed)}:{end_date or 'latest'}",
            )
            daily_rows: dict[str, list[dict]] = defaultdict(list)
            for row in _rows(daily_payload):
                code = _code(row.get("symbol"))
                if code in delayed:
                    daily_rows[code].append(row)
            for code, history in daily_rows.items():
                if end_date is None or any(_date(row.get("tradedate")) == end_date for row in history):
                    by_code_rows[code] = history
                    daily_codes.add(code)
        except NumCatGatewayError:
            pass
    if factor_error and not any(by_code_rows.values()):
        raise factor_error
    data_payload = (payload.get("data") or {}) if isinstance(payload, dict) else {}
    updated_at = _iso(data_payload.get("updated_at") or data_payload.get("source_updated_at"))
    result: dict[str, list[dict[str, Any]]] = {}
    for stock_code, raw_rows in by_code_rows.items():
        future_rows_filtered = bool(
            end_date
            and any((_date(row.get("tradedate") or row.get("trade_date")) or end_date) > end_date for row in raw_rows)
        )
        by_date: dict[str, dict[str, Any]] = {}
        for row in raw_rows:
            normalized_date = _date_text(row.get("tradedate") or row.get("trade_date"))
            if normalized_date:
                by_date[normalized_date] = row
        raw_rows = [by_date[key] for key in sorted(by_date)]
        qfq_complete = all(
            all(_number(row.get(f"{field}_qfq")) is not None for field in ("open", "high", "low", "close"))
            for row in raw_rows
        )
        basis = "qfq" if qfq_complete and not future_rows_filtered else "raw"
        bars = [
            {**bar, "source": "numcat_daily" if stock_code in daily_codes else "numcat_stk_factor_pro", "pit_status": "future_rows_filtered" if future_rows_filtered else "exact"}
            for row in raw_rows
            if (bar := _provider_row_to_bar(row, adjustment_basis=basis, source_updated_at=updated_at)) is not None
            and (end_date is None or _date(bar["date"]) is None or _date(bar["date"]) <= end_date)
        ]
        if bars:
            result[stock_code] = bars
    return result


@dataclass
class _CacheEntry:
    expires_at: float
    payload: dict[str, Any]


@dataclass
class _ScanJob:
    task: asyncio.Task
    progress: dict[str, int]
    started_at: str


class WildmanClassicService:
    def __init__(self) -> None:
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._jobs: dict[str, _ScanJob] = {}
        self._date_cache: dict[str, tuple[float, date]] = {}
        self._lock = asyncio.Lock()
        self._job_slots = asyncio.Semaphore(MAX_GLOBAL_SCAN_JOBS)

    @staticmethod
    def _validate_strategy(strategy_id: str) -> str:
        normalized = str(strategy_id or "").strip().upper()
        if normalized not in STRATEGY_IDS:
            raise ValueError("strategy_id只能是WM_CLASSIC_520、WM_CLASSIC_T或WM_CLASSIC_75A")
        return normalized

    @staticmethod
    def _cache_key(strategy_id: str, target: date, exclude_star_market: bool, exclude_gem: bool) -> str:
        return f"{strategy_id}:{target.isoformat()}:{int(exclude_star_market)}:{int(exclude_gem)}"

    async def _resolve_trade_date(self, requested: date | None) -> date:
        cache_key = requested.isoformat() if requested else "latest"
        cached_date = self._date_cache.get(cache_key)
        if cached_date and cached_date[0] > time.monotonic():
            return cached_date[1]
        if numcat_market_provider is not None and numcat_market_provider.configured:
            try:
                sessions = await numcat_market_provider.market_emotion(recentdays=30)
                candidates = sorted(
                    {
                        parsed for item in sessions
                        if (parsed := _date(item.get("trade_date"))) is not None
                        and (requested is None or parsed <= requested)
                    }
                )
                if candidates:
                    resolved = candidates[-1]
                    self._date_cache[cache_key] = (time.monotonic() + 60, resolved)
                    return resolved
            except Exception:
                pass
        async with async_session() as session:
            statement = select(func.max(StockDailyBar.trade_date))
            if requested:
                statement = statement.where(StockDailyBar.trade_date <= requested)
            known = (await session.execute(statement)).scalar_one_or_none()
            if known:
                return known
            universe_statement = select(func.max(StockUniverseSnapshot.trade_date))
            if requested:
                universe_statement = universe_statement.where(StockUniverseSnapshot.trade_date <= requested)
            known = (await session.execute(universe_statement)).scalar_one_or_none()
        resolved = known or requested or shanghai_now().date()
        self._date_cache[cache_key] = (time.monotonic() + 60, resolved)
        return resolved

    async def _load_numcat_universe(self, target: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if numcat_market_provider is None or not numcat_market_provider.configured:
            return [], {}
        try:
            rows, listed = await asyncio.gather(
                numcat_market_provider.screening(tradedate=target, enrichment_limit=0),
                numcat_market_provider.stock_basic(list_status="L"),
            )
        except Exception as exc:
            return [], {"provider": "numcat", "status": "failed", "error": type(exc).__name__}
        quote_by_code: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = _code(row.get("code"))
            if not code or _date(row.get("trade_date")) != target:
                continue
            quote_by_code[code] = row
        listing_by_code = {_code(row.get("code")): row for row in listed if _code(row.get("code"))}
        if not listing_by_code:
            return [], {"provider": "numcat_stockbasic", "status": "empty", "quote_count": len(quote_by_code)}
        universe = []
        for code, listing in listing_by_code.items():
            quote = quote_by_code.get(code) or {}
            universe.append({
                "code": code,
                # stockbasic is the directory authority; screening is only
                # allowed to enrich live quote fields and never replaces it.
                "name": str(listing.get("name") or quote.get("name") or ""),
                "sector": str(listing.get("industry") or ""),
                "market": str(listing.get("market") or quote.get("market") or ""),
                "exchange": str(listing.get("exchange") or quote.get("exchange") or ""),
                "price": _number(quote.get("price")),
                "change_pct": _number(quote.get("change_pct")),
                "is_st": quote.get("is_st"),
                "trade_date": target.isoformat(),
                "source": "numcat_stockbasic+numcat_screening",
                "source_updated_at": quote.get("quote_timestamp") or target.isoformat(),
            })
        return universe, {
            "provider": "numcat_stockbasic+numcat_screening",
            "status": "complete_universe_quote_partial" if len(quote_by_code) != len(listing_by_code) else "complete",
            "trade_date": target.isoformat(),
            "count": len(universe),
            "listed_count": len(listing_by_code),
            "quote_count": len(quote_by_code),
            "exact_quote_date_count": len(quote_by_code),
            "quote_coverage": round(len(quote_by_code) / len(listing_by_code), 4) if listing_by_code else 0,
        }

    async def _load_db_universe(self, target: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        latest_dates = (
            select(
                StockUniverseSnapshot.stock_code.label("stock_code"),
                func.max(StockUniverseSnapshot.trade_date).label("trade_date"),
            )
            .where(StockUniverseSnapshot.trade_date <= target)
            .group_by(StockUniverseSnapshot.stock_code)
            .subquery()
        )
        async with async_session() as session:
            rows = list((await session.execute(
                select(StockUniverseSnapshot).join(
                    latest_dates,
                    (StockUniverseSnapshot.stock_code == latest_dates.c.stock_code)
                    & (StockUniverseSnapshot.trade_date == latest_dates.c.trade_date),
                )
            )).scalars().all())
        universe = [{
            "code": _code(row.stock_code),
            "name": str(row.stock_name or ""),
            "sector": str(row.industry or ""),
            "market": str(row.exchange or ""),
            "exchange": str(row.exchange or ""),
            "price": _number(row.close_price),
            "change_pct": None,
            "is_suspended": row.is_suspended,
            "status": row.status_quality,
            "trade_date": row.trade_date.isoformat(),
            "source": row.source,
            "source_updated_at": _iso(row.updated_at) or row.trade_date.isoformat(),
        } for row in rows if row.stock_code]
        return universe, {
            "provider": "database_stock_universe_snapshot",
            "status": "complete" if universe else "empty",
            "trade_date": target.isoformat(),
            "count": len(universe),
        }

    async def _load_collector_universe(self, target: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        snapshot = await collector.fetch_quant_market_snapshot(include_special=True)
        source_date = _date(snapshot.get("data_date"))
        if source_date and source_date > target:
            raise RuntimeError("全市场行情快照晚于请求日期，拒绝未来数据")
        universe = []
        for item in snapshot.get("stocks") or []:
            code = _code(item.get("code"))
            if code:
                universe.append({**item, "code": code, "source": snapshot.get("source") or "collector", "source_updated_at": snapshot.get("source_updated_at") or snapshot.get("fetched_at")})
        if not universe:
            raise RuntimeError("全市场行情快照为空")
        return universe, {
            "provider": str(snapshot.get("source") or "collector"),
            "status": "complete" if snapshot.get("complete") else "partial",
            "trade_date": snapshot.get("data_date") or target.isoformat(),
            "count": len(universe),
        }

    async def _universe(self, target: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        universe, source = await self._load_numcat_universe(target)
        if universe:
            return universe, source
        universe, source = await self._load_db_universe(target)
        if universe:
            return universe, source
        return await self._load_collector_universe(target)

    async def _load_db_bars(self, codes: list[str], target: date) -> dict[str, list[dict[str, Any]]]:
        if not codes:
            return {}
        cutoff = target - timedelta(days=HISTORY_LOOKBACK_DAYS)
        async with async_session() as session:
            rows = list((await session.execute(
                select(StockDailyBar).where(
                    StockDailyBar.stock_code.in_(codes),
                    StockDailyBar.trade_date >= cutoff,
                    StockDailyBar.trade_date <= target,
                ).order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
            )).scalars().all())
        grouped: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            grouped[_code(row.stock_code)].append({
                "date": row.trade_date,
                "open": row.open_price,
                "high": row.high_price,
                "low": row.low_price,
                "close": row.close_price,
                "volume": row.volume,
                "amount": row.amount,
                "change_pct": row.change_pct,
                "source": row.source or "database",
                "source_updated_at": _iso(row.updated_at),
            })
        return dict(grouped)

    async def _load_fundamentals(self, codes: list[str], target: date) -> dict[str, dict[str, Any]]:
        if not codes:
            return {}
        async with async_session() as session:
            statement = select(FinancialPITSnapshot).where(
                FinancialPITSnapshot.stock_code.in_(codes),
                FinancialPITSnapshot.disclosed_at <= target,
            ).order_by(
                FinancialPITSnapshot.stock_code,
                desc(FinancialPITSnapshot.disclosed_at),
                desc(FinancialPITSnapshot.report_date),
            )
            rows = list((await session.execute(statement)).scalars().all())
        result = {}
        for row in rows:
            code = _code(row.stock_code)
            if code in result:
                continue
            known_risk = (row.net_profit is not None and row.net_profit <= 0) or (row.operating_cf is not None and row.operating_cf < 0) or (row.debt_ratio is not None and row.debt_ratio > 80)
            result[code] = {
                "fundamental_safe": False if known_risk else None,
                "financial_screen": {"net_profit": row.net_profit, "operating_cf": row.operating_cf, "debt_ratio": row.debt_ratio},
                "fundamental_source": "已披露财务快照：亏损、经营现金流为负或负债率>80%为风险代理；其余不等于全面基本面安全认证",
            }
        return result

    @staticmethod
    def _history_source(bars: list[dict[str, Any]]) -> tuple[str, str | None, str | None]:
        sources = Counter(str(item.get("source") or "unknown") for item in bars)
        source = sources.most_common(1)[0][0] if sources else "unknown"
        basis = str(next((item.get("adjustment_basis") for item in bars if item.get("adjustment_basis")), "stored_source"))
        updated = max((str(item.get("source_updated_at")) for item in bars if item.get("source_updated_at")), default=None)
        return source, basis, updated

    async def _numcat_history_batches(self, codes: list[str], target: date, diagnostics: list[dict[str, Any]] | None = None, *, days: int = HISTORY_RECENT_DAYS) -> dict[str, list[dict[str, Any]]]:
        """Fetch at most 32 same-market symbols per request with per-batch deadlines."""
        if not codes:
            return {}
        batches: list[list[str]] = []
        for batch in _chunks(codes, 32 if days <= 60 else HISTORY_BATCH_SIZE):
            grouped: dict[str | None, list[str]] = defaultdict(list)
            for code in batch:
                grouped[_market(code)].append(code)
            batches.extend(grouped.values())
        semaphore = asyncio.Semaphore(HISTORY_CONCURRENCY)

        async def fetch(batch: list[str]) -> dict[str, list[dict[str, Any]]]:
            async with semaphore:
                from services.wildman_classic_service import fetch_numcat_history_batch

                return await asyncio.wait_for(
                    fetch_numcat_history_batch(
                        batch,
                        days=days,
                        end_date=target,
                        _market_hint=_market(batch[0]) if batch else None,
                    ),
                    timeout=NUMCAT_BATCH_TIMEOUT_SECONDS,
                )

        results = await asyncio.gather(
            *(fetch(batch) for batch in batches),
            return_exceptions=True,
        )
        merged: dict[str, list[dict[str, Any]]] = {}
        for batch, result in zip(batches, results):
            if diagnostics is not None:
                if isinstance(result, Exception):
                    diagnostics.append({"requested": len(batch), "returned": 0, "missing": len(batch), "symbols": batch, "error": type(result).__name__})
                else:
                    diagnostics.append({
                        "requested": len(batch),
                        "returned": len(result),
                        "missing": len(batch) - len(result),
                        "symbols": batch,
                        "details": {
                            code: {
                                "bars": len(result.get(code) or []),
                                "latest": max((item.get("date") for item in result.get(code) or []), default=None),
                                "pit_status": sorted({item.get("pit_status") for item in result.get(code) or []}),
                            }
                            for code in batch if code in result
                        },
                    })
            if isinstance(result, dict):
                merged.update(result)
        return merged

    async def _history_batch(self, codes: list[str], target: date, *, refresh: bool, minimum_bars: int, topup_budget: list[int] | None = None, diagnostics: list[dict[str, Any]] | None = None, history_days: int = HISTORY_RECENT_DAYS) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
        result: dict[str, list[dict[str, Any]]] = {}
        source_counts: Counter[str] = Counter()
        history_updated: list[str] = []
        missing = list(codes)
        topup_budget = topup_budget if topup_budget is not None else [HISTORY_TOPUP_LIMIT]
        stale_codes: set[str] = set()
        numcat_attempted = False
        if numcat_market_provider is not None and numcat_market_provider.configured:
            numcat_attempted = True
            try:
                numcat = await self._numcat_history_batches(codes, target, diagnostics=diagnostics, days=history_days)
                for code, bars in numcat.items():
                    latest_numcat_date = max((_date(item.get("date")) for item in bars), default=None)
                    if bars and latest_numcat_date != target:
                        stale_codes.add(code)
                    if (
                        len(bars) >= minimum_bars
                        and latest_numcat_date == target
                        and all(item.get("pit_status") in {"exact", "future_rows_filtered"} for item in bars)
                    ):
                        result[code] = bars
                        source, _, updated = self._history_source(bars)
                        source_counts[source] += 1
                        if updated:
                            history_updated.append(updated)
                missing = [code for code in codes if code not in result]
            except Exception as exc:
                # Keep the fallback explicit so an upstream 422 cannot be
                # mistaken for a market-wide absence of historical data.
                numcat_error = type(exc).__name__
                missing = list(codes)
            else:
                numcat_error = None
        else:
            numcat_error = None
        db_rows = await self._load_db_bars(missing, target)
        for code, bars in db_rows.items():
            latest_db_date = max((_date(item["date"]) for item in bars), default=None)
            if bars and latest_db_date != target:
                stale_codes.add(code)
            if len(bars) >= minimum_bars and max((_date(item["date"]) for item in bars), default=None) == target:
                result[code] = bars
                source, _, updated = self._history_source(bars)
                source_counts[source] += 1
                if updated:
                    history_updated.append(updated)
        missing = [code for code in codes if code not in result]
        topup = {"status": "skipped", "requested": 0}
        if refresh and missing and topup_budget[0] > 0:
            topup_codes = missing[:topup_budget[0]]
            topup_budget[0] -= len(topup_codes)
            metadata = [{"code": code} for code in topup_codes]
            topup = await history_cache.refresh_recent_stock_histories(metadata, target.isoformat(), days=90)
            refreshed_rows = await self._load_db_bars(topup_codes, target)
            for code, bars in refreshed_rows.items():
                if len(bars) >= minimum_bars and max((_date(item["date"]) for item in bars), default=None) == target:
                    result[code] = bars
                    source, _, updated = self._history_source(bars)
                    source_counts[source] += 1
                    if updated:
                        history_updated.append(updated)
        return result, {
            "sources": dict(source_counts),
            "updated_at": max(history_updated, default=None),
            "numcat_attempted": numcat_attempted,
            "numcat_error": numcat_error,
            "numcat_batches": diagnostics or [],
            "stale_codes": sorted(stale_codes),
            "topup": topup,
        }

    async def _held_symbols(self) -> set[str]:
        async with async_session() as session:
            rows = list((await session.execute(select(PersonalPoolItem).where(
                PersonalPoolItem.status.in_(["holding", "reduce"]),
                PersonalPoolItem.position_pct > 0,
                PersonalPoolItem.asset_type == "stock",
            ))).scalars().all())
        return {_code(row.code) for row in rows if _code(row.code)}

    @staticmethod
    def _minimum_bars(strategy_id: str) -> int:
        return 21 if strategy_id == "WM_CLASSIC_520" else 6 if strategy_id == "WM_CLASSIC_T" else 75

    async def _scan_uncached(self, strategy_id: str, target: date, *, refresh: bool, exclude_star_market: bool, exclude_gem: bool, progress: dict[str, int] | None = None, cache_hit: bool = False) -> dict[str, Any]:
        definition = strategy_card(strategy_id)
        universe, universe_source = await self._universe(target)
        held = await self._held_symbols() if strategy_id == "WM_CLASSIC_T" else set()
        if progress is None:
            progress = {"total": 0, "scanned": 0, "eligible": 0, "excluded": 0, "missing_history": 0, "stale_history": 0}
        progress.update({"total": len(universe), "scanned": 0, "eligible": 0, "excluded": 0, "missing_history": 0, "stale_history": 0})
        eligible: list[dict[str, Any]] = []
        excluded_reasons: Counter[str] = Counter()
        for item in universe:
            code = _code(item.get("code"))
            reason = _market_exclusion(code, item, exclude_star_market=exclude_star_market, exclude_gem=exclude_gem)
            if reason:
                progress["excluded"] += 1
                excluded_reasons[reason] += 1
                continue
            item = dict(item)
            item["code"] = code
            item["holding"] = code in held
            eligible.append(item)
        progress["eligible"] = len(eligible)
        rows: list[dict[str, Any]] = []
        history_sources: Counter[str] = Counter()
        history_errors: Counter[str] = Counter()
        history_updated: list[str] = []
        numcat_batches: list[dict[str, Any]] = []
        minimum = self._minimum_bars(strategy_id)
        lookback = {"WM_CLASSIC_520": 45, "WM_CLASSIC_T": 30, "WM_CLASSIC_75A": HISTORY_RECENT_DAYS}[strategy_id]
        history_days = min(800, lookback + max(0, (shanghai_now().date() - target).days))
        topup_budget = [HISTORY_TOPUP_LIMIT]
        fundamentals_by_code: dict[str, dict[str, Any]] = {}
        for batch in _chunks([_code(item["code"]) for item in eligible], DB_BATCH_SIZE):
            if strategy_id == "WM_CLASSIC_T":
                fundamentals_by_code.update(await self._load_fundamentals(batch, target))
            histories, history_meta = await self._history_batch(batch, target, refresh=refresh, minimum_bars=minimum, topup_budget=topup_budget, diagnostics=numcat_batches, history_days=history_days)
            history_sources.update(history_meta["sources"])
            if history_meta.get("numcat_error"):
                history_errors[history_meta["numcat_error"]] += 1
            if history_meta.get("updated_at"):
                history_updated.append(history_meta["updated_at"])
            for item in (item for item in eligible if _code(item["code"]) in batch):
                code = _code(item["code"])
                progress["scanned"] += 1
                bars = histories.get(code)
                if not bars:
                    if code in history_meta.get("stale_codes", []):
                        progress["stale_history"] += 1
                    else:
                        progress["missing_history"] += 1
                    continue
                latest = max((_date(bar.get("date")) for bar in bars), default=None)
                if latest != target:
                    progress["stale_history"] += 1
                    continue
                meta = {**item, **fundamentals_by_code.get(code, {}), "symbol": code, "price": item.get("price"), "change_pct": item.get("change_pct")}
                evaluated = evaluate_classic(strategy_id, bars, meta=meta)
                if evaluated["status"] in {"已确认", "等待确认", "风险排除"}:
                    source, basis, updated = self._history_source(bars)
                    if basis == "raw":
                        evaluated["risk_notes"].append("使用完整未复权日线；除权除息可能影响均线与形态，需结合价格调整核对。")
                    evaluated["evidence"].append({
                        "rule_id": "DATA_SOURCE",
                        "rule_name": "实际行情来源与复权口径",
                        "required": True,
                        "actual": {"provider": source, "adjustment_basis": basis, "updated_at": updated},
                        "passed": True,
                        "source": source,
                    })
                    evaluated["theme_name"] = str(item.get("sector") or "")
                    evaluated["price"] = evaluated["price"] if evaluated["price"] is not None else bars[-1].get("close")
                    rows.append(evaluated)
        history_errors.update(item["error"] for item in numcat_batches if item.get("error"))
        coverage_note = (
            f"全市场 universe={progress['total']}，排除={progress['excluded']}，可评估={progress['eligible']}；"
            f"缺历史={progress['missing_history']}，过期={progress['stale_history']}。"
            f"实际历史来源={dict(history_sources) or {'none': 0}}；NumCat错误={dict(history_errors) or {'none': 0}}；无历史不计为无匹配。"
        )
        if strategy_id == "WM_CLASSIC_T" and not held:
            coverage_note += " 当前没有PersonalPoolItem可卖底仓，因此T不生成新买入建议。"
        return {
            "strategy": definition,
            "status": "completed",
            "trade_date": target.isoformat(),
            "updated_at": shanghai_now().isoformat(),
            "progress": progress,
            "rows": rows,
            "coverage_note": coverage_note,
            "cache_hit": cache_hit,
            "data_sources": {
                "universe": universe_source,
                "history": {"providers": dict(history_sources), "numcat_errors": dict(history_errors), "latest_source_updated_at": max(history_updated, default=None), "adjustment_basis": "猫爪复权因子优先；更新滞后时整段使用猫爪未复权日线，不混接价格口径"},
                "excluded_reasons": dict(excluded_reasons),
            },
        }

    def _store_cache(self, key: str, payload: dict[str, Any]) -> None:
        self._cache[key] = _CacheEntry(time.monotonic() + SCAN_CACHE_TTL_SECONDS, payload)
        self._cache.move_to_end(key)
        while len(self._cache) > MAX_FINISHED_CACHE_ENTRIES:
            self._cache.popitem(last=False)

    @staticmethod
    def _running_payload(strategy_id: str, target: date, progress: dict[str, int], started_at: str) -> dict[str, Any]:
        return {
            "strategy": strategy_card(strategy_id),
            "status": "running",
            "trade_date": target.isoformat(),
            "updated_at": started_at,
            "progress": dict(progress),
            "rows": [],
            "coverage_note": "全市场扫描进行中；缺失或过期历史不会被解释为无匹配。",
            "cache_hit": False,
        }

    async def _run_job(self, key: str, strategy_id: str, target: date, *, refresh: bool, exclude_star_market: bool, exclude_gem: bool, progress: dict[str, int]) -> dict[str, Any]:
        async with self._job_slots:
            try:
                return await self._scan_uncached(
                    strategy_id,
                    target,
                    refresh=refresh,
                    exclude_star_market=exclude_star_market,
                    exclude_gem=exclude_gem,
                    progress=progress,
                )
            except Exception as exc:
                return {
                    "strategy": strategy_card(strategy_id),
                    "status": "failed",
                    "trade_date": target.isoformat(),
                    "updated_at": shanghai_now().isoformat(),
                    "progress": dict(progress),
                    "rows": [],
                    "error": type(exc).__name__,
                    "coverage_note": "扫描失败，未将失败数据解释为无匹配。",
                    "cache_hit": False,
                }

    async def scan(self, strategy_id: str, requested_date: date | None = None, *, refresh: bool = False, exclude_star_market: bool = True, exclude_gem: bool = True) -> dict[str, Any]:
        strategy_id = self._validate_strategy(strategy_id)
        target = await self._resolve_trade_date(requested_date)
        key = self._cache_key(strategy_id, target, exclude_star_market, exclude_gem)
        now = time.monotonic()
        if not refresh:
            cached = self._cache.get(key)
            if cached and cached.expires_at > now:
                self._cache.move_to_end(key)
                payload = dict(cached.payload)
                payload["cache_hit"] = True
                return payload
        async with self._lock:
            job = self._jobs.get(key)
            if job is not None and job.task.done():
                payload = job.task.result()
                self._jobs.pop(key, None)
                self._store_cache(key, payload)
                return payload
            if job is not None:
                return self._running_payload(strategy_id, target, job.progress, job.started_at)
            if len(self._jobs) >= MAX_TOTAL_SCAN_JOBS:
                return {
                    "strategy": strategy_card(strategy_id),
                    "status": "failed",
                    "trade_date": target.isoformat(),
                    "updated_at": shanghai_now().isoformat(),
                    "progress": {"total": 0, "scanned": 0, "eligible": 0, "excluded": 0, "missing_history": 0, "stale_history": 0},
                    "rows": [],
                    "error": "scan_queue_full",
                    "coverage_note": "扫描队列已达到8个任务上限，请稍后轮询已提交任务。",
                    "cache_hit": False,
                }
            progress = {"total": 0, "scanned": 0, "eligible": 0, "excluded": 0, "missing_history": 0, "stale_history": 0}
            started_at = shanghai_now().isoformat()
            task = asyncio.create_task(self._run_job(
                key,
                strategy_id,
                target,
                refresh=refresh,
                exclude_star_market=exclude_star_market,
                exclude_gem=exclude_gem,
                progress=progress,
            ))
            self._jobs[key] = _ScanJob(task=task, progress=progress, started_at=started_at)

            def finalize(done: asyncio.Task) -> None:
                try:
                    payload = done.result()
                except Exception:
                    return
                self._store_cache(key, payload)
                current = self._jobs.get(key)
                if current is not None and current.task is done:
                    self._jobs.pop(key, None)

            task.add_done_callback(finalize)
            return self._running_payload(strategy_id, target, progress, started_at)

    async def detail(self, strategy_id: str, symbol: str, requested_date: date | None = None, *, refresh: bool = False) -> dict[str, Any]:
        strategy_id = self._validate_strategy(strategy_id)
        code = _code(symbol)
        if not code or len(code) != 6 or not code.isdigit():
            raise ValueError("symbol必须是6位股票代码")
        target = await self._resolve_trade_date(requested_date)
        histories, history_meta = await self._history_batch([code], target, refresh=refresh, minimum_bars=self._minimum_bars(strategy_id))
        bars = histories.get(code, [])
        async with async_session() as session:
            universe_row = (await session.execute(select(StockUniverseSnapshot).where(
                StockUniverseSnapshot.stock_code == code,
                StockUniverseSnapshot.trade_date <= target,
            ).order_by(StockUniverseSnapshot.trade_date.desc()).limit(1))).scalar_one_or_none()
        meta = {"symbol": code, "name": universe_row.stock_name if universe_row else "", "sector": universe_row.industry if universe_row else ""}
        if strategy_id == "WM_CLASSIC_T":
            meta["holding"] = code in await self._held_symbols()
            meta.update((await self._load_fundamentals([code], target)).get(code, {}))
        row = evaluate_classic(strategy_id, bars, meta=meta)
        if row["status"] == "NO_MATCH":
            row["status"] = "风险排除"
        source, basis, updated = self._history_source(bars)
        if basis == "raw":
            row["risk_notes"].append("使用完整未复权日线；除权除息可能影响均线与形态，需结合价格调整核对。")
        row["evidence"].append({"rule_id": "DATA_SOURCE", "rule_name": "实际行情来源与复权口径", "required": True, "actual": {"provider": source, "adjustment_basis": basis, "updated_at": updated}, "passed": bool(bars), "source": source})
        return {
            **row,
            "strategy": strategy_card(strategy_id),
            "trade_date": target.isoformat(),
            "bars": indicator_bars(bars),
            "data_source": {"provider": source, "adjustment_basis": basis, "source_updated_at": updated, "batch_meta": history_meta},
        }


wildman_classic_service = WildmanClassicService()


__all__ = ["WildmanClassicService", "fetch_numcat_history_batch", "wildman_classic_service"]
