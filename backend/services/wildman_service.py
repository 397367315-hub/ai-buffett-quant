"""Data adapter and persistence boundary for the strict Wildman rule core."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import desc, func, select

from database import async_session
from models import (
    MarketDataCache,
    MarketSentimentDaily,
    StockAuctionSnapshot,
    StockDailyBar,
    WildmanCandidate,
    WildmanMainline,
    WildmanMarketCycle,
    WildmanStockRole,
    WildmanTradeReview,
)
from services.data_collector import collector, shanghai_now
from services.level2_service import level2_service
from wildman.rules import RULE_VERSION, WildmanRuleCore, review_metrics


CACHE_KEY = "wildman_dashboard_v2"


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value) if value not in (None, "") else None


def _normalize_code(value: Any) -> str:
    return str(value or "").split(".", 1)[0].strip()


class WildmanService:
    def __init__(self) -> None:
        self.rules = WildmanRuleCore()
        self._lock = asyncio.Lock()

    async def _target_date(self, requested: date | None = None) -> date:
        if requested:
            return requested
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
        async with async_session() as session:
            return list((await session.execute(
                select(MarketSentimentDaily)
                .where(MarketSentimentDaily.trade_date <= target)
                .order_by(desc(MarketSentimentDaily.trade_date))
                .limit(5)
            )).scalars().all())

    async def _bars(self, symbols: list[str], target: date) -> dict[str, list[StockDailyBar]]:
        if not symbols:
            return {}
        start = target - timedelta(days=180)
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
        return grouped

    async def _auctions(self, symbols: list[str], target: date) -> dict[str, StockAuctionSnapshot]:
        if not symbols:
            return {}
        async with async_session() as session:
            rows = list((await session.execute(select(StockAuctionSnapshot).where(
                StockAuctionSnapshot.trade_date == target,
                StockAuctionSnapshot.stock_code.in_(symbols),
            ))).scalars().all())
        return {row.stock_code: row for row in rows}

    @staticmethod
    def _ma(rows: list[StockDailyBar], period: int) -> float | None:
        values = [_num(row.close_price) for row in rows[-period:]]
        values = [value for value in values if value is not None]
        return sum(values) / period if len(values) == period else None

    def _stock_facts(self, item: dict[str, Any], rows: list[StockDailyBar], auction: StockAuctionSnapshot | None, theme: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
        latest = rows[-1] if rows else None
        close = _num(getattr(latest, "close_price", None)) or _num(item.get("price"))
        low = _num(getattr(latest, "low_price", None)) or close
        high = _num(getattr(latest, "high_price", None)) or close
        ma5, ma10, ma20, ma75 = (self._ma(rows, period) for period in (5, 10, 20, 75))
        previous_ma20 = self._ma(rows[:-1], 20) if len(rows) > 20 else None
        volume_values = [_num(row.volume) for row in rows[-6:-1]]
        volume_values = [value for value in volume_values if value is not None]
        latest_volume = _num(getattr(latest, "volume", None))
        volume_contract = latest_volume is not None and volume_values and latest_volume < sum(volume_values) / len(volume_values) * .8
        recent_highs = [_num(row.high_price) for row in rows[-21:-1]]
        pressure = max((value for value in recent_highs if value is not None), default=None)
        recent_peak = max((value for value in [_num(row.high_price) for row in rows[-30:]] if value is not None), default=None)
        drawdown = (close / recent_peak - 1) * 100 if close and recent_peak else None
        height = int(_num(item.get("continuous_days")) or 1)
        failed = int(_num(item.get("failed_attempts")) or 0)
        market_cap = _num(item.get("market_cap"))
        auction_pct = _num(getattr(auction, "high_open_pct", None))
        auction_ratio = _num(getattr(auction, "auction_volume_ratio", None))
        latest_change_pct = _num(getattr(latest, "change_pct", None)) or 0
        theme_count = int(theme.get("limit_up_count") or 0)
        max_height = int(theme.get("max_limit_height") or 0)
        return {
            "symbol": _normalize_code(item.get("code")), "name": item.get("name"),
            "close_price": close, "low_price": low, "high_price": high,
            "market_cap": market_cap, "consecutive_limit_days": height,
            "theme_linkage": theme_count >= 3, "emotion_benchmark": height >= max_height and theme_count >= 3,
            "first_limit_pioneer": height == 1 and item.get("first_limit_time") is not None,
            "trend_intact": ma20 is not None and close is not None and close >= ma20,
            "supplement": max_height >= 4 and 1 <= height <= max(2, int(max_height * .7)),
            "old_dragon": False, "cross_cycle": False,
            "first_volume_divergence": height >= 4 and failed > 0,
            "divergence_low": low, "yesterday_divergence": height >= 2 and failed > 0,
            "yesterday_strong": height >= 2 and failed == 0,
            "auction_pct": auction_pct,
            "auction_volume_strength": auction_ratio is not None and auction_ratio >= 1,
            "open_volume_attack": auction_pct is not None and auction_pct >= 2,
            "intraday_anchor": _num(getattr(auction, "auction_price", None)) or low,
            "weekly_monthly_bottom": len(rows) >= 75 and close is not None and min((_num(row.low_price) or close) for row in rows[-75:]) >= close * .7,
            "ma20": ma20, "ma20_turning_up": ma20 is not None and previous_ma20 is not None and ma20 >= previous_ma20,
            "ma20_flat": ma20 is not None and previous_ma20 is not None and abs(ma20 / previous_ma20 - 1) <= .003,
            "ma5_cross_ma10": ma5 is not None and ma10 is not None and ma5 >= ma10,
            "ma5_near_cross": ma5 is not None and ma10 is not None and ma5 >= ma10 * .985,
            "ma5_cross_ma20": ma5 is not None and ma20 is not None and ma5 >= ma20,
            "cross_volume_expand": not volume_contract,
            "pullback_ma10_ma20": close is not None and any(value is not None and abs(close / value - 1) <= .03 for value in (ma10, ma20)),
            "volume_contract": bool(volume_contract),
            "stabilizing_candle": latest is not None and _num(latest.close_price) is not None and _num(latest.open_price) is not None and latest.close_price >= latest.open_price * .99,
            "bottom_volume_breakout": latest is not None and _num(latest.change_pct) is not None and latest.change_pct >= 5 and not volume_contract,
            "drawdown_pct": drawdown, "extreme_low_volume": bool(volume_contract),
            "support_recovered": latest is not None and close is not None and low is not None and close > low * 1.02,
            "reversal_confirmed": latest is not None and _num(latest.change_pct) is not None and latest.change_pct > 0,
            "recent_solid_limit": height >= 1 and failed == 0,
            "holds_limit_candle_half": True if height >= 1 else None,
            "limit_candle_half": round(((low or 0) + (high or 0)) / 2, 3) if low and high else None,
            "renewed_volume_breakout": latest is not None and _num(latest.change_pct) is not None and latest.change_pct >= 3 and not volume_contract,
            "pressure_price": pressure,
            "fake_breakout": bool(close and pressure and close < pressure and latest_change_pct > 5 and volume_contract),
            "high_volume_stall": bool(latest_volume and volume_values and latest_volume > sum(volume_values) / len(volume_values) * 1.8 and latest_change_pct < 1),
            "anchor_broken": False, "open_low": auction_pct is not None and auction_pct < 0,
            "no_support": False, "decline_days": sum(1 for row in rows[-5:] if (_num(row.change_pct) or 0) < 0),
            "arc_days": len(rows), "ma75_turning_up": ma75 is not None and close is not None and close >= ma75,
            "break_neckline": bool(close and pressure and close >= pressure),
        }

    async def _snapshot(self, target: date, refresh: bool) -> dict[str, Any]:
        sentiments_task = self._sentiments(target)
        up_task = self._safe(collector.fetch_limit_up_pool(page_size=500, target_date=target), {"stocks": [], "total": 0, "trade_date": None})
        down_task = self._safe(collector.fetch_limit_down_pool(page_size=500, target_date=target), {"stocks": [], "total": 0, "trade_date": None})
        failed_task = self._safe(collector.fetch_failed_limit_pool(page_size=500, target_date=target), {"stocks": [], "total": 0, "trade_date": None})
        sentiments, up_pool, down_pool, failed_pool = await asyncio.gather(sentiments_task, up_task, down_task, failed_task)
        up_rows = list(up_pool.get("stocks") or [])
        down_rows = list(down_pool.get("stocks") or [])
        failed_rows = list(failed_pool.get("stocks") or [])
        latest_sentiment = sentiments[0] if sentiments else None
        previous_sentiment = sentiments[1] if len(sentiments) > 1 else None

        by_theme: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in up_rows:
            by_theme[str(item.get("sector") or "未归类")].append(item)
        theme_facts: list[dict[str, Any]] = []
        for theme_name, rows in by_theme.items():
            heights = [int(_num(item.get("continuous_days")) or 1) for item in rows]
            max_height = max(heights, default=0)
            core_rows = [item for item in rows if (_num(item.get("market_cap")) or 0) >= 10_000_000_000]
            non_first = sum(height >= 2 for height in heights)
            theme_facts.append({
                "theme_id": theme_name, "theme_name": theme_name,
                "limit_up_count": len(rows), "max_limit_height": max_height,
                "has_pioneer": any(height == 1 for height in heights),
                "has_core_midcap": bool(core_rows), "old_dragon_active": None,
                # A successful promotion to the second board or above is the
                # observable price fact used here; no actor intent is inferred.
                "leader_premium": max_height >= 2, "support_promotion": non_first >= 2,
                "core_midcap_stable": any((_num(item.get("change_pct")) or 0) >= 0 for item in core_rows) if core_rows else None,
                "fund_return": None, "reversal": None, "supplement_started": len(rows) >= 3,
                "resists_market_drop": None, "rows": rows,
            })
        theme_facts.sort(key=lambda row: (row["max_limit_height"], row["limit_up_count"]), reverse=True)
        all_heights = [int(_num(item.get("continuous_days")) or 1) for item in up_rows]
        max_height = max(all_heights, default=int(_num(getattr(latest_sentiment, "max_streak_height", None)) or 0))
        ladder_complete = max_height >= 4 and any(height in {2, 3} for height in all_heights) and sum(height == 1 for height in all_heights) >= 3
        core_support = any(theme.get("has_core_midcap") for theme in theme_facts[:5])
        one_word = sum(1 for item in up_rows if str(item.get("first_limit_time") or "")[:4] in {"0925", "09:25"} and int(_num(item.get("failed_attempts")) or 0) == 0)
        limit_down_count = int(_num(getattr(latest_sentiment, "limit_down_count", None)) or down_pool.get("total") or len(down_rows))
        previous_down = _num(getattr(previous_sentiment, "limit_down_count", None))
        market = {
            "max_limit_height": max_height, "limit_up_count": int(up_pool.get("total") or len(up_rows)),
            "limit_down_count": limit_down_count, "nuclear_button_count": None,
            "yesterday_limit_loss_count": None, "new_theme_first_boards": max((sum(int(_num(item.get("continuous_days")) or 1) == 1 for item in rows) for rows in by_theme.values()), default=0),
            "limit_down_decreasing": previous_down is not None and limit_down_count < previous_down,
            "ladder_complete": ladder_complete, "core_midcap_support": core_support,
            "leader_nuked": bool(previous_sentiment and _num(previous_sentiment.max_streak_height) is not None and previous_sentiment.max_streak_height >= 4 and max_height <= 2 and limit_down_count >= 10),
            "mid_level_limit_down_spread": limit_down_count >= 10,
            "first_divergence": bool(max_height >= 4 and failed_rows),
            "one_word_limit_count": one_word, "leader_volume_acceleration": max_height >= 4 and one_word > 0,
            "rear_all_red": len(up_rows) >= 50, "leader_broken": max_height >= 4 and bool(failed_rows),
            "cold_rear_supplement": len(up_rows) >= 60,
        }
        return {"market": market, "themes": theme_facts, "up_rows": up_rows, "down_rows": down_rows, "failed_rows": failed_rows, "source": {"limit_up": up_pool.get("source"), "limit_down": down_pool.get("source"), "failed": failed_pool.get("source"), "data_date": up_pool.get("trade_date") or target.isoformat()}}

    async def dashboard(self, requested: date | None = None, *, refresh: bool = False, exclude_star_market: bool = True, exclude_gem: bool = True) -> dict[str, Any]:
        target = await self._target_date(requested)
        cache_key = self._cache_key(exclude_star_market, exclude_gem)
        cached = await self._cached(cache_key)
        if not refresh and requested is None and cached and cached.get("trade_date") == target.isoformat():
            return {**cached, "cache_hit": True}
        async with self._lock:
            snapshot = await self._snapshot(target, refresh)
            cycle = self.rules.market_cycle.detect(snapshot["market"])
            theme_results = []
            theme_map: dict[str, dict[str, Any]] = {}
            for theme in snapshot["themes"][:20]:
                result = self.rules.mainline.evaluate(theme)
                item = {key: value for key, value in theme.items() if key != "rows"} | result
                theme_results.append(item)
                theme_map[theme["theme_id"]] = item
            raw_candidates = [item for theme in snapshot["themes"][:12] for item in theme.get("rows", [])]
            unique: dict[str, dict[str, Any]] = {}
            for item in raw_candidates:
                code = _normalize_code(item.get("code"))
                if len(code) != 6 or not code.isdigit():
                    continue
                if exclude_star_market and code.startswith(("688", "689")): continue
                if exclude_gem and code.startswith(("300", "301", "302")): continue
                unique.setdefault(code, item)
            symbols = list(unique)[:80]
            bars_by_symbol, auctions = await asyncio.gather(self._bars(symbols, target), self._auctions(symbols, target))
            candidates = []
            for code in symbols:
                raw = unique[code]
                theme_name = str(raw.get("sector") or "未归类")
                theme = theme_map.get(theme_name) or {"theme_id": theme_name, "theme_name": theme_name, "state": "观察", "limit_up_count": 1, "max_limit_height": 1}
                facts = self._stock_facts(raw, bars_by_symbol.get(code, []), auctions.get(code), theme, snapshot["market"])
                result = self.rules.evaluate(snapshot["market"], theme, facts, {}, None)
                candidates.append({
                    "symbol": code, "name": raw.get("name") or code, "theme_id": theme_name, "theme_name": theme_name,
                    "price": facts.get("close_price"), "change_pct": _num(raw.get("change_pct")),
                    "continuous_days": facts.get("consecutive_limit_days"),
                    "stock_facts": facts,
                    "theme_facts": {key: value for key, value in theme.items() if key != "rows"},
                    **result,
                })
            order = {"模式条件成立": 0, "等待确认": 1, "观察": 2, "风险否决": 3}
            candidates.sort(key=lambda row: (order.get(row["candidate_status"], 9), -(row.get("continuous_days") or 0)))
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in candidates:
                grouped[row["role"]["role"]].append(row)
            payload = {
                "module_id": "WILDMAN_DECISION_V1", "rule_version": RULE_VERSION,
                "mode": "DECISION_SUPPORT", "trade_date": target.isoformat(), "updated_at": shanghai_now().isoformat(),
                "cycle": cycle, "market_facts": snapshot["market"], "mainlines": theme_results,
                "candidates": candidates, "candidate_groups": dict(grouped),
                "reject_pool": [row for row in candidates if row["candidate_status"] == "风险否决"],
                "classic_toolbox": [
                    {"id": "WM_CLASSIC_520", "name": "520战法", "status": "可用"},
                    {"id": "WM_CLASSIC_T", "name": "老太太扶楼梯", "status": "主动启用"},
                    {"id": "WM_CLASSIC_75A", "name": "圆弧底75A", "status": "可用"},
                ],
                "source_status": snapshot["source"],
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

    async def candidate(self, symbol: str, requested: date | None = None, *, refresh: bool = False) -> dict[str, Any]:
        code = _normalize_code(symbol)
        payload = await self.dashboard(requested, refresh=refresh, exclude_star_market=False, exclude_gem=False)
        candidate = next((row for row in payload["candidates"] if row["symbol"] == code), None)
        if candidate is None:
            raise ValueError("该股票不在当日野人哥核心/排除池中")
        target = date.fromisoformat(payload["trade_date"])
        l2 = await level2_service.summary(code, trade_date=target, refresh=refresh)
        intraday = {}
        timeline = (l2.get("summary") or {}).get("timeline") or []
        if timeline:
            closes = [_num(row.get("close_price")) for row in timeline]
            closes = [value for value in closes if value is not None]
            intraday = {"lows_rising": len(closes) >= 3 and closes[-1] >= min(closes[-3:]), "two_pullbacks_hold": len(closes) >= 3 and closes[-1] >= closes[-3], "vertical_drop": False, "down_volume_expands": False}
        stock_facts = candidate.get("stock_facts") or {
            "symbol": code,
            "name": candidate["name"],
            "close_price": candidate.get("price"),
            "consecutive_limit_days": candidate.get("continuous_days"),
        }
        theme_facts = candidate.get("theme_facts") or {
            "theme_id": candidate["theme_id"],
            "theme_name": candidate["theme_name"],
        }
        refined = self.rules.evaluate(payload["market_facts"], theme_facts, stock_facts, intraday, l2)
        level2_payload = {
            key: l2.get(key)
            for key in ("available", "pending", "provider", "data_quality", "summary", "sync", "capabilities")
        }
        return {**candidate, **refined, "level2": level2_payload, "trade_date": payload["trade_date"]}

    async def review(self, period: str = "daily", requested: date | None = None) -> dict[str, Any]:
        target = await self._target_date(requested)
        days = {"daily": 1, "weekly": 7, "monthly": 31}.get(period, 1)
        start = target - timedelta(days=days - 1)
        async with async_session() as session:
            rows = list((await session.execute(select(WildmanTradeReview).where(WildmanTradeReview.entry_date >= start, WildmanTradeReview.entry_date <= target).order_by(desc(WildmanTradeReview.entry_date)))).scalars().all())
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
