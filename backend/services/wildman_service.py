"""Data adapter and persistence boundary for the strict Wildman rule core."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta
from math import isfinite
from typing import Any
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import desc, func, select

from database import async_session
from models import (
    MarketDataCache,
    MarketSentimentDaily,
    StockAuctionSnapshot,
    StockDailyBar,
    StockUniverseSnapshot,
    WildmanCandidate,
    WildmanMainline,
    WildmanMarketCycle,
    WildmanStockRole,
    WildmanTradeReview,
)
from services.data_collector import collector, shanghai_now
from services.level2_service import level2_service
from market_data.numcat.market_provider import numcat_market_provider
from wildman.rules import RULE_VERSION, WildmanRuleCore, review_metrics
from wildman.facts import daily_facts, intraday_facts
from services.wildman_risk_service import risk_facts
from services.wildman_history_service import build_history_context


CACHE_KEY = "wildman_dashboard_v5"


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value) if value not in (None, "") else None


def _normalize_code(value: Any) -> str:
    return str(value or "").split(".", 1)[0].strip()


def candidate_card(row: dict[str, Any]) -> dict[str, Any]:
    """Keep evidence in the detail record, not in every dashboard grouping."""
    card = {key: row.get(key) for key in (
        "symbol", "name", "theme_id", "theme_name", "price", "change_pct",
        "continuous_days", "candidate_status", "risk_reward", "setup",
    )}
    card["role"] = {"role": (row.get("role") or {}).get("role")}
    return card


def dashboard_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "candidates": [candidate_card(row) for row in payload.get("candidates", [])],
        "candidate_groups": {key: [candidate_card(row) for row in rows] for key, rows in payload.get("candidate_groups", {}).items()},
        "reject_pool": [candidate_card(row) for row in payload.get("reject_pool", [])],
    }


class WildmanService:
    def __init__(self) -> None:
        self.rules = WildmanRuleCore()
        self._lock = asyncio.Lock()

    async def _target_date(self, requested: date | None = None) -> date:
        if requested:
            return requested
        if numcat_market_provider.configured:
            rows = await self._safe(numcat_market_provider.market_emotion(recentdays=5), [])
            observed = [date.fromisoformat(row["trade_date"]) for row in rows if row.get("trade_date") and row["trade_date"] <= shanghai_now().date().isoformat()]
            if observed:
                return max(observed)
        async with async_session() as session:
            values = [
                (await session.execute(select(func.max(MarketSentimentDaily.trade_date)))).scalar_one_or_none(),
                (await session.execute(select(func.max(StockDailyBar.trade_date)))).scalar_one_or_none(),
            ]
        known = [item for item in values if isinstance(item, date)]
        return max(known) if known else shanghai_now().date()

    @staticmethod
    async def _safe(awaitable: Any, fallback: Any) -> Any:
        try:
            return await asyncio.wait_for(awaitable, timeout=16)
        except Exception:
            return fallback

    @staticmethod
    def _cache_key(exclude_star_market: bool, exclude_gem: bool) -> str:
        return f"{CACHE_KEY}:s{int(exclude_star_market)}:g{int(exclude_gem)}"

    async def _cached(self, key: str) -> dict[str, Any] | None:
        async with async_session() as session:
            row = await session.get(MarketDataCache, key)
        return dict(row.payload) if row and isinstance(row.payload, dict) else None

    @staticmethod
    def _cache_fresh(payload: dict | None, target: date) -> bool:
        if not payload or payload.get("trade_date") != target.isoformat() or payload.get("rule_version") != RULE_VERSION:
            return False
        try:
            return 0 <= (shanghai_now() - datetime.fromisoformat(payload["updated_at"])).total_seconds() < 300
        except (KeyError, TypeError, ValueError):
            return False

    async def _write_cache(self, key: str, payload: dict[str, Any]) -> None:
        async with async_session() as session:
            row = await session.get(MarketDataCache, key)
            if row is None:
                session.add(MarketDataCache(key=key, payload=payload))
            else:
                row.payload = payload
                row.updated_at = datetime.utcnow()
            await session.commit()

    async def _sentiments(self, target: date) -> list[MarketSentimentDaily]:
        if numcat_market_provider.configured:
            rows = await self._safe(numcat_market_provider.market_emotion(recentdays=30), [])
            matched = [row for row in rows if str(row.get("trade_date") or "") <= target.isoformat()]
            if matched and any(row.get("trade_date") == target.isoformat() for row in matched):
                return [SimpleNamespace(**{**row, "trade_date": date.fromisoformat(row["trade_date"])}) for row in sorted(matched, key=lambda row: row["trade_date"], reverse=True)[:30]]
        async with async_session() as session:
            return list((await session.execute(
                select(MarketSentimentDaily)
                .where(MarketSentimentDaily.trade_date <= target)
                .order_by(desc(MarketSentimentDaily.trade_date))
                .limit(30)
            )).scalars().all())

    async def _bars(self, symbols: list[str], target: date) -> dict[str, list[StockDailyBar]]:
        if not symbols:
            return {}
        start = target - timedelta(days=420)
        async with async_session() as session:
            rows = list((await session.execute(
                select(StockDailyBar).where(
                    StockDailyBar.stock_code.in_(symbols),
                    StockDailyBar.trade_date >= start,
                    StockDailyBar.trade_date <= target,
                ).order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
            )).scalars().all())
        grouped: dict[str, list[StockDailyBar]] = defaultdict(list)
        for row in rows:
            grouped[row.stock_code].append(row)
        if numcat_market_provider.configured:
            from services.wildman_classic_service import fetch_numcat_history_batch
            semaphore = asyncio.Semaphore(3)
            async def primary_batch(codes: list[str]) -> dict:
                async with semaphore:
                    return await self._safe(fetch_numcat_history_batch(codes, days=min(800, 260 + max(0, (shanghai_now().date() - target).days)), end_date=target), {})
            primary = await asyncio.gather(*(primary_batch(symbols[start:start + 16]) for start in range(0, len(symbols), 16)))
            for batch in primary:
                for code, history in batch.items():
                    if not history or str(history[-1].get("date") or "") != target.isoformat():
                        continue
                    grouped[code] = [SimpleNamespace(
                        stock_code=code, trade_date=date.fromisoformat(row["date"]),
                        open_price=row.get("open"), close_price=row.get("close"), high_price=row.get("high"), low_price=row.get("low"),
                        volume=row.get("volume"), change_pct=row.get("change_pct"), source=row.get("source") or "numcat",
                    ) for row in history]
        return grouped

    async def _auctions(self, symbols: list[str], target: date) -> dict[str, StockAuctionSnapshot]:
        if not symbols:
            return {}
        async with async_session() as session:
            rows = list((await session.execute(select(StockAuctionSnapshot).where(
                StockAuctionSnapshot.trade_date == target,
                StockAuctionSnapshot.stock_code.in_(symbols),
            ))).scalars().all())
        result = {row.stock_code: row for row in rows}
        if numcat_market_provider.configured:
            primary = await self._safe(numcat_market_provider.auction(symbols, tradedate=target), [])
            for row in primary:
                if str(row.get("tradedate") or "")[:10] != target.isoformat():
                    continue
                code = _normalize_code(row.get("symbol"))
                result[code] = SimpleNamespace(stock_code=code, trade_date=target, high_open_pct=_num(row.get("auc_pct_chg")), auction_volume_ratio=_num(row.get("auc_vol_ratio")), auction_price=_num(row.get("m_price")), source="numcat_daily_auc")
        return result

    async def _universe_metadata(self, symbols: list[str], target: date) -> dict[str, dict[str, Any]]:
        """Load the latest PIT industry and market cap known by the target date."""
        if not symbols:
            return {}
        latest_dates = (
            select(
                StockUniverseSnapshot.stock_code.label("stock_code"),
                func.max(StockUniverseSnapshot.trade_date).label("trade_date"),
            )
            .where(
                StockUniverseSnapshot.stock_code.in_(symbols),
                StockUniverseSnapshot.trade_date <= target,
            )
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
        return {
            row.stock_code: {
                "name": row.stock_name,
                "sector": str(row.industry or "").strip(),
                "market_cap": _num(row.market_cap),
                "trade_date": row.trade_date.isoformat(),
                "source": row.source,
            }
            for row in rows
        }

    @staticmethod
    def _ma(rows: list[StockDailyBar], period: int) -> float | None:
        values = [_num(row.close_price) for row in rows[-period:]]
        values = [value for value in values if value is not None]
        return sum(values) / period if len(values) == period else None

    def _stock_facts(self, item: dict[str, Any], rows: list[StockDailyBar], auction: StockAuctionSnapshot | None, theme: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
        return daily_facts(item, rows, auction, theme, market)

    async def _risk_facts(self, symbols: list[str], target: date, refresh: bool = False) -> dict:
        try:
            return await asyncio.wait_for(risk_facts(symbols, target, refresh=refresh), timeout=40)
        except Exception as exc:
            return {code: {"major_risk": None, "fundamentals_clear": None, "coverage": {"status": "failed", "checked_to": target.isoformat(), "error": type(exc).__name__}, "risk_evidence": []} for code in symbols}

    async def _snapshot(self, target: date, refresh: bool) -> dict[str, Any]:
        sentiments_task = self._sentiments(target)
        up_task = self._safe(collector.fetch_limit_up_pool(page_size=500, target_date=target), {"stocks": [], "total": 0, "trade_date": None})
        down_task = self._safe(collector.fetch_limit_down_pool(page_size=500, target_date=target), {"stocks": [], "total": 0, "trade_date": None})
        failed_task = self._safe(collector.fetch_failed_limit_pool(page_size=500, target_date=target), {"stocks": [], "total": 0, "trade_date": None})
        sentiments, up_pool, down_pool, failed_pool = await asyncio.gather(sentiments_task, up_task, down_task, failed_task)
        async with async_session() as session:
            previous_date = (await session.execute(select(func.max(StockDailyBar.trade_date)).where(StockDailyBar.trade_date < target))).scalar_one_or_none()
        observed_prior = [row.trade_date for row in sentiments if row.trade_date < target]
        if observed_prior:
            previous_date = max(observed_prior + ([previous_date] if previous_date else []))
        previous_pool = await self._safe(collector.fetch_limit_up_pool(page_size=500, target_date=previous_date), {}) if previous_date else {}
        def dated(pool: dict, day: date | None) -> bool:
            return day is not None and str(pool.get("trade_date") or "").replace("-", "")[:8] == day.strftime("%Y%m%d")
        up_valid, down_valid = dated(up_pool, target), dated(down_pool, target)
        failed_valid, previous_valid = dated(failed_pool, target), dated(previous_pool, previous_date)
        if not up_valid:
            up_pool = {"stocks": [], "total": None}
        if not down_valid:
            down_pool = {"stocks": [], "total": None}
        if not failed_valid:
            failed_pool = {"stocks": [], "total": None}
        up_rows = list(up_pool.get("stocks") or [])
        down_rows = list(down_pool.get("stocks") or [])
        failed_rows = list(failed_pool.get("stocks") or [])
        previous_rows = list(previous_pool.get("stocks") or []) if previous_valid else []
        previous_by_code = {_normalize_code(row.get("code")): {**row, "trade_date": previous_date.isoformat()} for row in previous_rows}
        all_rows = [*up_rows, *down_rows, *failed_rows, *previous_rows]
        metadata = await self._universe_metadata(
            list({_normalize_code(item.get("code")) for item in all_rows}),
            target,
        )
        if all_rows and numcat_market_provider.configured and 0 <= (shanghai_now().date() - target).days <= 3:
            codes = list({_normalize_code(item.get("code")) for item in all_rows})
            basics, quotes = await asyncio.gather(
                self._safe(numcat_market_provider.stock_basic(codes), []),
                self._safe(numcat_market_provider.screening(symbols=codes, tradedate=target, enrichment_limit=0), []),
            )
            for row in basics:
                code = _normalize_code(row.get("code"))
                primary = {"sector": row.get("industry"), "name": row.get("name"), "source": "numcat_stockbasic"}
                metadata.setdefault(code, {}).update({key: value for key, value in primary.items() if value})
            for row in quotes:
                if str(row.get("trade_date") or "")[:10] == target.isoformat():
                    metadata.setdefault(_normalize_code(row.get("code")), {}).update({"market_cap": row.get("market_cap")})
        metadata_hits = 0
        for item in all_rows:
            row = metadata.get(_normalize_code(item.get("code"))) or {}
            if not str(item.get("sector") or "").strip() and row.get("sector"):
                item["sector"] = row["sector"]
                metadata_hits += 1
            if _num(item.get("market_cap")) is None and row.get("market_cap") is not None:
                item["market_cap"] = row["market_cap"]
            if not str(item.get("name") or "").strip() and row.get("name"):
                item["name"] = row["name"]
        latest_sentiment = next((row for row in sentiments if row.trade_date == target), None)
        previous_sentiment = next((row for row in sentiments if row.trade_date == previous_date), None)
        symbols = list(dict.fromkeys(_normalize_code(item.get("code")) for item in all_rows if _normalize_code(item.get("code"))))
        bars, risks = await asyncio.gather(self._bars(symbols, target), self._risk_facts(symbols[:160], target, refresh))
        history_rows = {}
        for item in all_rows:
            history_rows.setdefault(_normalize_code(item.get("code")), item)
        try:
            history = await asyncio.wait_for(build_history_context(target, list(history_rows.values()), bars, sentiments), timeout=35) if numcat_market_provider.configured else {}
        except Exception as exc:
            history = {"source": {"status": "failed", "error": type(exc).__name__, "observed_dates": []}}
        overrides = history.get("stock_overrides") or {}
        for item in all_rows:
            historical = overrides.get(_normalize_code(item.get("code"))) or {}
            if historical.get("primary_theme_name"):
                item["industry"] = item.get("sector")
                item["sector"] = historical["primary_theme_name"]
        for item in [*up_rows, *failed_rows]:
            item["previous_limit"] = previous_by_code.get(_normalize_code(item.get("code")))
        def current_bar(code: str):
            rows = bars.get(code) or []
            return rows[-1] if rows and rows[-1].trade_date == target else None
        def healthy_core(item: dict) -> bool | None:
            rows = bars.get(_normalize_code(item.get("code"))) or []
            latest = current_bar(_normalize_code(item.get("code")))
            if latest is None or len(rows) < 21:
                return None
            avg_volume = sum(_num(row.volume) or 0 for row in rows[-6:-1]) / 5
            return bool(latest.close_price is not None and latest.close_price >= (self._ma(rows, 20) or float("inf")) and avg_volume > 0 and (_num(latest.volume) or 0) >= avg_volume * 1.2)

        def board_date(item: dict, offset: int = 0) -> str | None:
            history = bars.get(_normalize_code(item.get("code"))) or []
            height = int(_num(item.get("continuous_days")) or 0)
            if height > offset and len(history) >= height and history[-1].trade_date == target:
                return history[len(history) - height + offset].trade_date.isoformat()
            return None

        by_theme: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in up_rows:
            by_theme[str(item.get("sector") or "未归类")].append(item)
        theme_facts: list[dict[str, Any]] = []
        for theme_name, rows in by_theme.items():
            heights = [int(_num(item.get("continuous_days")) or 1) for item in rows]
            max_height = max(heights, default=0)
            core_rows = [item for item in rows if (_num(item.get("market_cap")) or 0) >= 10_000_000_000]
            prior_theme = [item for item in previous_rows if item.get("sector") == theme_name]
            prior_height = max((int(_num(item.get("continuous_days")) or 1) for item in prior_theme), default=0)
            prior_leaders = [item for item in prior_theme if int(_num(item.get("continuous_days")) or 1) == prior_height]
            prior_cores = [item for item in prior_theme if (_num(item.get("market_cap")) or 0) >= 10_000_000_000]
            leader_bars = [current_bar(_normalize_code(item.get("code"))) for item in prior_leaders]
            core_bars = [current_bar(_normalize_code(item.get("code"))) for item in prior_cores]
            theme_facts.append({
                "theme_id": theme_name, "theme_name": theme_name,
                "limit_up_count": len(rows), "max_limit_height": max_height,
                "leader_established_date": min((day for item in rows if int(_num(item.get("continuous_days")) or 0) == max_height and (day := board_date(item, 3))), default=None),
                "earliest_start_date": min((day for item in rows if (day := board_date(item))), default=None),
                "leader_first_limit_time": min((str(item.get("first_limit_time") or "").replace(":", "")[:4] for item in rows if int(_num(item.get("continuous_days")) or 1) == max_height and item.get("first_limit_time")), default=None),
                "earliest_first_limit_time": min((str(item.get("first_limit_time") or "").replace(":", "")[:4] for item in rows if item.get("first_limit_time")), default=None),
                "has_pioneer": any(height == 1 for height in heights),
                "has_core_midcap": bool(core_rows), "old_dragon_active": None,
                "leader_premium": all(_num(bar.change_pct) is not None and bar.change_pct > 0 for bar in leader_bars) if leader_bars and all(bar is not None for bar in leader_bars) else None,
                "support_promotion": any(item.get("previous_limit") and int(_num(item.get("continuous_days")) or 0) > int(_num(item["previous_limit"].get("continuous_days")) or 1) and _normalize_code(item.get("code")) not in {_normalize_code(p.get("code")) for p in prior_leaders} for item in rows) if previous_valid and prior_theme else None,
                "core_midcap_stable": all(_num(bar.change_pct) is not None and bar.change_pct >= 0 for bar in core_bars) if core_bars and all(bar is not None for bar in core_bars) else None,
                "core_volume_support": any(healthy_core(item) is True for item in core_rows) if core_rows else None,
                "fund_return": None, "reversal": None, "supplement_started": prior_height >= 4 and any(int(_num(item.get("continuous_days")) or 1) == 1 for item in rows) if previous_valid else None,
                "resists_market_drop": None, "rows": rows,
            })
        for theme in theme_facts:
            theme.update({key: value for key, value in (history.get("theme_overrides", {}).get(theme["theme_name"]) or {}).items() if value is not None})
        theme_facts.sort(key=lambda row: (row["max_limit_height"], row["limit_up_count"]), reverse=True)
        all_heights = [int(_num(item.get("continuous_days")) or 1) for item in up_rows]
        max_height = max(all_heights, default=int(_num(getattr(latest_sentiment, "max_streak_height", None)) or 0))
        ladder_complete = any(theme["max_limit_height"] >= 4 and any(int(_num(item.get("continuous_days")) or 1) in {2, 3} for item in theme["rows"]) and any(int(_num(item.get("continuous_days")) or 1) == 1 for item in theme["rows"]) and theme["has_core_midcap"] for theme in theme_facts) if up_valid else None
        core_support = any(theme.get("core_volume_support") is True for theme in theme_facts[:5]) if up_valid and bars else None
        one_word = sum(1 for item in up_rows if str(item.get("first_limit_time") or "").replace(":", "")[:4] == "0925" and _num(item.get("failed_attempts")) == 0)
        limit_down_count = _num(getattr(latest_sentiment, "limit_down_count", None))
        if limit_down_count is None and down_valid:
            limit_down_count = int(down_pool.get("total") if down_pool.get("total") is not None else len(down_rows))
        previous_down = _num(getattr(previous_sentiment, "limit_down_count", None))
        prior_max = max((int(_num(item.get("continuous_days")) or 1) for item in previous_rows), default=0)
        prior_leader_codes = {_normalize_code(item.get("code")) for item in previous_rows if int(_num(item.get("continuous_days")) or 1) == prior_max}
        down_codes = {_normalize_code(item.get("code")) for item in down_rows}
        failed_codes = {_normalize_code(item.get("code")) for item in failed_rows}
        market = {
            "trade_date": target.isoformat(),
            "max_limit_height": max_height if up_valid else None, "limit_up_count": int(up_pool.get("total") or len(up_rows)) if up_valid else None,
            "limit_down_count": limit_down_count, "nuclear_button_count": None,
            "yesterday_limit_loss_count": len(set(previous_by_code) & down_codes) if previous_valid and down_valid else None, "new_theme_first_boards": max((sum(int(_num(item.get("continuous_days")) or 1) == 1 for item in rows) for name, rows in by_theme.items() if name not in {item.get("sector") for item in previous_rows}), default=0) if previous_valid and up_valid else None,
            "limit_down_decreasing": limit_down_count < previous_down if previous_down is not None and limit_down_count is not None else None,
            "ladder_complete": ladder_complete, "core_midcap_support": core_support,
            "leader_nuked": bool(prior_max >= 4 and prior_leader_codes & down_codes) if previous_valid and down_valid else None,
            "mid_level_limit_down_spread": sum(1 for item in previous_rows if 2 <= int(_num(item.get("continuous_days")) or 1) < prior_max and _normalize_code(item.get("code")) in down_codes) >= 2 if previous_valid and down_valid else None,
            "first_divergence": None,
            "one_word_limit_count": one_word if up_valid else None, "leader_volume_acceleration": None,
            "rear_all_red": None, "leader_broken": bool(prior_max >= 4 and prior_leader_codes & (failed_codes | down_codes)) if previous_valid and failed_valid and down_valid else None,
            "cold_rear_supplement": None,
        }
        market.update({key: value for key, value in (history.get("market_overrides") or {}).items() if value is not None})
        current_codes = {_normalize_code(item.get("code")) for item in up_rows}
        prior_candidates = [{**item, "continuous_days": 0, "failed_attempts": None, "previous_limit": previous_by_code.get(_normalize_code(item.get("code")))} for item in previous_rows if _normalize_code(item.get("code")) not in current_codes]
        mapped_count = sum(bool(item.get("primary_theme_name")) and item.get("industry_theme_proxy") is False for item in overrides.values())
        return {"market": market, "themes": theme_facts, "up_rows": up_rows, "previous_rows": prior_candidates, "down_rows": down_rows, "failed_rows": failed_rows, "bars": bars, "risk_facts": risks, "history_facts": overrides, "source": {"limit_up": up_pool.get("source"), "limit_down": down_pool.get("source"), "failed": failed_pool.get("source"), "universe_metadata": sorted({str(row.get("source") or "unknown") for row in metadata.values()}), "universe_metadata_hits": metadata_hits, "data_date": up_pool.get("trade_date"), "previous_date": previous_date.isoformat() if previous_valid else None, "same_day_pools": {"limit_up": up_valid, "limit_down": down_valid, "failed": failed_valid}, "history": history.get("source") or {}, "concept_mapped_count": mapped_count, "theme_basis": f"猫爪当日题材成分匹配{mapped_count}只；其余明确使用行业代理，多日关联不代表因果"}}

    async def dashboard(self, requested: date | None = None, *, refresh: bool = False, exclude_star_market: bool = True, exclude_gem: bool = True) -> dict[str, Any]:
        target = await self._target_date(requested)
        cache_key = self._cache_key(exclude_star_market, exclude_gem)
        cached = await self._cached(cache_key)
        if not refresh and self._cache_fresh(cached, target):
            return {**cached, "cache_hit": True}
        async with self._lock:
            cached = await self._cached(cache_key)
            if not refresh and self._cache_fresh(cached, target):
                return {**cached, "cache_hit": True}
            snapshot = await self._snapshot(target, refresh)
            cycle = self.rules.market_cycle.detect(snapshot["market"])
            theme_results = []
            theme_map: dict[str, dict[str, Any]] = {}
            for theme in snapshot["themes"][:20]:
                result = self.rules.mainline.evaluate(theme)
                item = {key: value for key, value in theme.items() if key != "rows"} | result
                theme_results.append(item)
                theme_map[theme["theme_id"]] = item
            raw_candidates = [item for theme in snapshot["themes"][:20] for item in theme.get("rows", [])]
            raw_candidates.extend(snapshot.get("failed_rows") or [])
            raw_candidates.extend(snapshot.get("previous_rows") or [])
            unique: dict[str, dict[str, Any]] = {}
            for item in raw_candidates:
                code = _normalize_code(item.get("code"))
                if len(code) != 6 or not code.isdigit():
                    continue
                if exclude_star_market and code.startswith(("688", "689")): continue
                if exclude_gem and code.startswith(("300", "301", "302")): continue
                unique.setdefault(code, item)
            symbols = list(unique)[:160]
            bars_by_symbol, auctions = await asyncio.gather(self._bars(symbols, target) if not snapshot.get("bars") else asyncio.sleep(0, result=snapshot["bars"]), self._auctions(symbols, target))
            candidates = []
            for code in symbols:
                raw = unique[code]
                theme_name = str(raw.get("sector") or "未归类")
                theme = theme_map.get(theme_name) or {"theme_id": theme_name, "theme_name": theme_name, "state": "观察", "limit_up_count": 1, "max_limit_height": 1}
                facts = self._stock_facts(raw, bars_by_symbol.get(code, []), auctions.get(code), theme, snapshot["market"])
                historical = (snapshot.get("history_facts") or {}).get(code) or {}
                original_basis = facts.get("fact_basis") or {}
                facts.update({key: value for key, value in historical.items() if value is not None})
                facts["fact_basis"] = {**original_basis, **(historical.get("fact_basis") or {})}
                facts.update((snapshot.get("risk_facts") or {}).get(code) or {})
                result = self.rules.evaluate(snapshot["market"], theme, facts, {}, None)
                candidates.append({
                    "symbol": code, "name": raw.get("name") or code, "theme_id": theme_name, "theme_name": theme_name,
                    "price": facts.get("close_price"), "change_pct": _num(getattr((bars_by_symbol.get(code) or [None])[-1], "change_pct", None)),
                    "continuous_days": facts.get("consecutive_limit_days"),
                    "stock_facts": facts,
                    "theme_facts": {key: value for key, value in theme.items() if key != "rows"},
                    **result,
                })
            order = {"模式条件成立": 0, "等待确认": 1, "观察": 2, "风险否决": 3}
            candidates.sort(key=lambda row: (order.get(row["candidate_status"], 9), -(row.get("continuous_days") or 0)))
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in candidates:
                grouped[row["role"]["role"]].append(candidate_card(row))
            payload = {
                "module_id": "WILDMAN_DECISION_V1", "rule_version": RULE_VERSION,
                "mode": "DECISION_SUPPORT", "trade_date": target.isoformat(), "updated_at": shanghai_now().isoformat(),
                "cycle": cycle, "market_facts": snapshot["market"], "mainlines": theme_results,
                "candidates": candidates, "candidate_groups": dict(grouped),
                "reject_pool": [candidate_card(row) for row in candidates if row["candidate_status"] == "风险否决"],
                "classic_toolbox": [
                    {"id": "WM_CLASSIC_520", "name": "520战法", "status": "可用"},
                    {"id": "WM_CLASSIC_T", "name": "老太太扶楼梯", "status": "主动启用"},
                    {"id": "WM_CLASSIC_75A", "name": "圆弧底75A", "status": "可用"},
                ],
                "source_status": {**snapshot["source"], "preferred_provider": "numcat", "daily_sources": sorted({str(getattr(row, "source", "unknown")) for code in symbols for row in bars_by_symbol.get(code, [])}), "candidate_scope": "当日涨停、炸板及前一交易日涨停观察池", "candidate_total": len(unique), "candidate_evaluated": len(symbols)},
                "filters": {"exclude_star_market": exclude_star_market, "exclude_gem": exclude_gem},
                "constraints": {"ai_can_override": False, "automatic_trade": False, "raw_level2_persisted_here": False},
                "cache_hit": False,
            }
            await self._persist(payload)
            await self._write_cache(cache_key, payload)
            return payload

    async def _persist(self, payload: dict[str, Any]) -> None:
        target = date.fromisoformat(payload["trade_date"])
        cycle = payload["cycle"]
        facts = payload["market_facts"]
        async with async_session() as session:
            cycle_row = (await session.execute(select(WildmanMarketCycle).where(WildmanMarketCycle.trade_date == target, WildmanMarketCycle.rule_version == RULE_VERSION))).scalar_one_or_none()
            cycle_values = {"cycle": cycle["cycle"], "cycle_node": cycle["cycle_node"], "max_limit_height": facts.get("max_limit_height"), "limit_up_count": facts.get("limit_up_count"), "limit_down_count": facts.get("limit_down_count"), "nuclear_button_count": facts.get("nuclear_button_count"), "yesterday_limit_loss_count": facts.get("yesterday_limit_loss_count"), "evidence_json": cycle["evidence"], "allowed_actions_json": cycle["allowed_actions"], "forbidden_actions_json": cycle["forbidden_actions"], "data_quality_json": cycle["data_quality"]}
            if cycle_row:
                for key, value in cycle_values.items(): setattr(cycle_row, key, value)
            else:
                session.add(WildmanMarketCycle(trade_date=target, rule_version=RULE_VERSION, **cycle_values))
            for theme in payload["mainlines"]:
                row = (await session.execute(select(WildmanMainline).where(WildmanMainline.trade_date == target, WildmanMainline.theme_id == theme["theme_id"], WildmanMainline.rule_version == RULE_VERSION))).scalar_one_or_none()
                values = {"theme_name": theme["theme_name"], "state": theme["state"], **{f"step{index}_pass": theme["steps"].get(f"step{index}") for index in range(1, 6)}, "evidence_json": theme["evidence"], "missing_json": theme["missing"]}
                if row:
                    for key, value in values.items(): setattr(row, key, value)
                else: session.add(WildmanMainline(trade_date=target, theme_id=theme["theme_id"], rule_version=RULE_VERSION, **values))
            for candidate in payload["candidates"]:
                role = candidate["role"]
                role_row = (await session.execute(select(WildmanStockRole).where(WildmanStockRole.trade_date == target, WildmanStockRole.symbol == candidate["symbol"], WildmanStockRole.theme_id == candidate["theme_id"], WildmanStockRole.rule_version == RULE_VERSION))).scalar_one_or_none()
                role_values = {"stock_name": candidate["name"], "role": role["role"], "role_evidence_json": role["evidence"], "confidence_source": role["confidence_source"]}
                if role_row:
                    for key, value in role_values.items(): setattr(role_row, key, value)
                else: session.add(WildmanStockRole(trade_date=target, symbol=candidate["symbol"], theme_id=candidate["theme_id"], rule_version=RULE_VERSION, **role_values))
                setup = candidate["setup"]
                candidate_row = (await session.execute(select(WildmanCandidate).where(WildmanCandidate.trade_date == target, WildmanCandidate.symbol == candidate["symbol"], WildmanCandidate.setup_type == setup["type"], WildmanCandidate.rule_version == RULE_VERSION))).scalar_one_or_none()
                candidate_values = {"stock_name": candidate["name"], "theme_id": candidate["theme_id"], "theme_name": candidate["theme_name"], "market_cycle": candidate["cycle"]["cycle"], "cycle_node": candidate["cycle"]["cycle_node"], "role": role["role"], "candidate_status": candidate["candidate_status"], "anchor_type": setup.get("anchor_type"), "anchor_price": setup.get("anchor_price"), "risk_reward": candidate.get("risk_reward"), "position_json": candidate["position"], "exit_plan_json": candidate["exit_plan"], "risk_flags_json": candidate["risk"]["flags"], "passed_rules_json": candidate["passed_rules"], "failed_rules_json": candidate["failed_rules"], "waiting_rules_json": candidate["waiting_rules"], "explain_chain_json": candidate["explain_chain"], "source_snapshot_json": {"price": candidate.get("price"), "change_pct": candidate.get("change_pct"), "source": payload["source_status"]}}
                if candidate_row:
                    for key, value in candidate_values.items(): setattr(candidate_row, key, value)
                else: session.add(WildmanCandidate(trade_date=target, symbol=candidate["symbol"], setup_type=setup["type"], rule_version=RULE_VERSION, **candidate_values))
            await session.commit()

    async def _account_rules(self, account: dict | None, target: date) -> dict:
        if not account:
            return {}
        today = shanghai_now().date()
        nav = account.get("nav_metrics") or {}
        current = str(account.get("as_of") or "") == today.isoformat() and target == today
        nav_current = current and str(nav.get("as_of") or "") == today.isoformat() and nav.get("quality") == "complete"
        async with async_session() as session:
            rows = list((await session.execute(select(WildmanTradeReview).where(
                WildmanTradeReview.exit_date.is_not(None), WildmanTradeReview.exit_date <= target,
            ).order_by(desc(WildmanTradeReview.exit_date), desc(WildmanTradeReview.id)).limit(30))).scalars().all())
        stops = 0
        for row in rows:
            pnl = _num(row.pnl_pct)
            if pnl is None or pnl >= 0:
                break
            stops += 1
        return {
            "consecutive_stops": stops,
            "verified_drawdown_pct": nav.get("current_drawdown_pct") if nav_current else None,
            "source": "用户手工核对账户与已结算复盘；非券商同步",
            "as_of": account.get("as_of"), "current": current, "scan_blocked": False,
        }

    async def candidate(self, symbol: str, requested: date | None = None, *, refresh: bool = False, account: dict | None = None) -> dict[str, Any]:
        code = _normalize_code(symbol)
        payload = await self.dashboard(requested, refresh=refresh, exclude_star_market=False, exclude_gem=False)
        candidate = next((row for row in payload["candidates"] if row["symbol"] == code), None)
        if candidate is None:
            raise ValueError("该股票不在当日野人哥核心/排除池中")
        target = date.fromisoformat(payload["trade_date"])
        l2, minutes = await asyncio.gather(
            level2_service.summary(code, trade_date=target, refresh=refresh),
            self._safe(numcat_market_provider.minute(code, tradedate=target), []) if numcat_market_provider.configured else asyncio.sleep(0, result=[]),
        )
        minutes = [row for row in minutes if str(row.get("tradedate") or "")[:10] == target.isoformat()]
        timeline = [{**row, "volume": row.get("vol")} for row in minutes] or (l2.get("summary") or {}).get("timeline") or []
        intraday = intraday_facts(timeline)
        stock_facts = dict(candidate.get("stock_facts") or {
            "symbol": code,
            "name": candidate["name"],
            "close_price": candidate.get("price"),
            "consecutive_limit_days": candidate.get("continuous_days"),
        })
        if minutes:
            from wildman.facts import minute_key
            minutes = [row for row in minutes if minute_key(row.get("trademin") or row.get("time"))]
            minutes.sort(key=lambda row: minute_key(row.get("trademin") or row.get("time")) or "")
            intraday = intraday_facts([{**row, "volume": row.get("vol")} for row in minutes])
            early = [row for row in minutes if "0930" <= (minute_key(row.get("trademin") or row.get("time")) or "") <= "0940"]
            if len(early) >= 5:
                opening, last = _num(early[0].get("open")), _num(early[-1].get("close"))
                earlier_vol = sum(_num(row.get("vol")) or 0 for row in early[:2]) / 2
                later_vol = sum(_num(row.get("vol")) or 0 for row in early[-2:]) / 2
                stock_facts["open_volume_attack"] = last > opening and later_vol > earlier_vol if opening and last and earlier_vol > 0 else None
                stock_facts["intraday_anchor"] = min((_num(row.get("low")) for row in early if _num(row.get("low")) is not None), default=None)
            if minutes and intraday.get("two_pullbacks_hold") is True:
                stock_facts["support_recovered"] = True
                last, vwap = _num(minutes[-1].get("close")), _num(minutes[-1].get("vwap"))
                stock_facts["reversal_confirmed"] = last > vwap if last and vwap else None
        theme_facts = candidate.get("theme_facts") or {
            "theme_id": candidate["theme_id"],
            "theme_name": candidate["theme_name"],
        }
        account_rules = await self._account_rules(account, target)
        refined = self.rules.evaluate(payload["market_facts"], theme_facts, stock_facts, intraday, l2, account=account_rules)
        level2_payload = {
            key: l2.get(key)
            for key in ("available", "pending", "provider", "data_quality", "summary", "sync", "capabilities")
        }
        return {**candidate, **refined, "stock_facts": stock_facts, "account_context": account_rules, "intraday_source": "numcat_minute" if minutes else f"{l2.get('provider')}_level2_features" if timeline and l2.get("provider") else None, "level2": level2_payload, "trade_date": payload["trade_date"]}

    async def review(self, period: str = "daily", requested: date | None = None) -> dict[str, Any]:
        target = await self._target_date(requested)
        days = {"daily": 1, "weekly": 7, "monthly": 31}.get(period, 1)
        start = target - timedelta(days=days - 1)
        async with async_session() as session:
            review_date = func.coalesce(WildmanTradeReview.exit_date, WildmanTradeReview.entry_date)
            rows = list((await session.execute(select(WildmanTradeReview).where(review_date >= start, review_date <= target).order_by(desc(review_date)))).scalars().all())
        items = [{"trade_id": row.trade_id, "symbol": row.symbol, "stock_name": row.stock_name, "entry_date": row.entry_date.isoformat(), "exit_date": _iso(row.exit_date), "setup_type": row.setup_type, "cycle_at_entry": row.cycle_at_entry, "role_at_entry": row.role_at_entry, "mode_inside": row.mode_inside, "entry_price": row.entry_price, "exit_price": row.exit_price, "pnl_pct": row.pnl_pct, "anchor_broken": row.anchor_broken, "stop_executed": row.stop_executed, "expectation_state": row.expectation_state, "violation_tags": row.violation_tags_json or [], "review_text": row.review_text} for row in rows]
        dashboard = await self.dashboard(target)
        return {"period": period, "start_date": start.isoformat(), "end_date": target.isoformat(), "metrics": review_metrics(items), "trades": items, "cycle": dashboard["cycle"], "tomorrow_plan": {"allowed": dashboard["cycle"]["allowed_actions"], "forbidden": dashboard["cycle"]["forbidden_actions"], "focus_mainlines": [row["theme_name"] for row in dashboard["mainlines"] if row["state"] in {"核心主线确认", "高质量候选"}][:5]}}

    async def save_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        trade_id = str(payload.get("trade_id") or uuid4().hex)
        entry_date = date.fromisoformat(str(payload.get("entry_date") or shanghai_now().date().isoformat())[:10])
        exit_date = date.fromisoformat(str(payload["exit_date"])[:10]) if payload.get("exit_date") else None
        values = {"symbol": _normalize_code(payload.get("symbol")), "stock_name": payload.get("stock_name"), "entry_date": entry_date, "exit_date": exit_date, "setup_type": str(payload.get("setup_type") or "NONE"), "cycle_at_entry": payload.get("cycle_at_entry"), "role_at_entry": payload.get("role_at_entry"), "mode_inside": bool(payload.get("mode_inside", True)), "entry_price": _num(payload.get("entry_price")), "exit_price": _num(payload.get("exit_price")), "pnl_pct": _num(payload.get("pnl_pct")), "max_favorable_excursion": _num(payload.get("max_favorable_excursion")), "max_adverse_excursion": _num(payload.get("max_adverse_excursion")), "anchor_price": _num(payload.get("anchor_price")), "anchor_broken": payload.get("anchor_broken"), "stop_executed": payload.get("stop_executed"), "expectation_state": payload.get("expectation_state"), "violation_tags_json": list(payload.get("violation_tags") or []), "review_text": payload.get("review_text")}
        if not values["symbol"]:
            raise ValueError("symbol不能为空")
        if exit_date and exit_date < entry_date:
            raise ValueError("卖出日期不能早于买入日期")
        for field in ("entry_price", "exit_price"):
            if payload.get(field) not in (None, "") and (values[field] is None or values[field] <= 0):
                raise ValueError("成交价格必须是正数")
        async with async_session() as session:
            row = (await session.execute(select(WildmanTradeReview).where(WildmanTradeReview.trade_id == trade_id))).scalar_one_or_none()
            if row:
                for key, value in values.items(): setattr(row, key, value)
            else: session.add(WildmanTradeReview(trade_id=trade_id, **values))
            await session.commit()
        return {"trade_id": trade_id, "saved": True}

    async def rule_registry(self) -> dict[str, Any]:
        return {"rule_version": RULE_VERSION, "immutable": True, "ai_can_override": False, "rule_groups": ["WM_CYCLE_*", "WM_MAINLINE_*", "WM_ROLE_*", "WM_SUPPORT_*", "WM_SETUP_*", "WM_RISK_*", "WM_POSITION_*", "WM_EXIT_*", "WM_CLASSIC_*"]}


wildman_service = WildmanService()

__all__ = ["WildmanService", "wildman_service"]
