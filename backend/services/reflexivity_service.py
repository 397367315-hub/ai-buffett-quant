"""Runtime data access for the V5 behaviour/reflexivity Skill.

This service is deliberately separate from the existing nine-skill funnel so
the new six-dimensional diagnosis can be queried, persisted and replayed
without changing the semantics of the older scanners.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select

from database import async_session
from models import BehaviorReflexivitySnapshot, StockAuctionSnapshot, StockDailyBar
from quant.market_cache import load_quant_market_snapshot
from quant.reflexivity_skill import (
    CANDIDATE_LABELS,
    MODEL_VERSION,
    OPPORTUNITY_CANDIDATES,
    SKILL_VERSION,
    build_reflexivity_diagnosis,
)
from services.data_collector import collector, is_a_share_market_session, normalize_stock_code, shanghai_now


MAX_SCAN_STOCKS = 240
SCAN_CACHE_SECONDS = 45


def _num(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def _pct_field_to_decimal(value: Any) -> float | None:
    """Convert a quote ``change_pct`` field to a decimal return.

    The market snapshot stores ``change_pct`` in percentage points (``1.2``
    means +1.2%), while the quant feature engine consumes decimals
    (``0.012``).  Keeping this conversion explicit avoids the ambiguous
    ``0.8`` case being interpreted as an 80% return.
    """
    parsed = _num(value)
    return parsed / 100 if parsed is not None else None


def _segment(code: str) -> str:
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301", "302")):
        return "创业板"
    if code.startswith(("4", "8", "92")):
        return "北交所"
    if code.startswith("6"):
        return "沪市主板"
    if code.startswith(("000", "001", "002", "003")):
        return "深市主板/中小板"
    return "其他A股"


def _bar(row: StockDailyBar) -> dict[str, Any]:
    return {
        "trade_date": row.trade_date,
        "open_price": row.open_price,
        "close_price": row.close_price,
        "high_price": row.high_price,
        "low_price": row.low_price,
        "volume": row.volume,
        "amount": row.amount,
        "turnover": row.turnover,
        "available_time": row.updated_at.isoformat() if row.updated_at else row.trade_date.isoformat(),
    }


def _context_for_stock(stock: dict[str, Any], universe: list[dict[str, Any]], market_return: float | None) -> dict[str, Any]:
    sector = str(stock.get("sector") or "").strip()
    members = [item for item in universe if sector and str(item.get("sector") or "").strip() == sector]
    changes = [_num(item.get("change_pct")) for item in members]
    changes = [value for value in changes if value is not None]
    avg_change_pct = sum(changes) / len(changes) if changes else None
    avg_change = _pct_field_to_decimal(avg_change_pct)
    breadth = sum(value > 0 for value in changes) / len(changes) * 100 if changes else None
    flows = [_num(item.get("main_net_inflow")) for item in members]
    flows = [value for value in flows if value is not None]
    flow = sum(flows) if flows else None
    stock_change_pct = _num(stock.get("change_pct"))
    stock_change = _pct_field_to_decimal(stock_change_pct)
    relative = stock_change - avg_change if stock_change is not None and avg_change is not None else None
    sector_strength = _clamp(50 + (avg_change_pct or 0) * 8 + ((breadth or 50) - 50) * 0.35 + _clamp((flow or 0) / 1e8, -15, 15))
    sector_state = "强化" if sector_strength >= 72 else "启势" if sector_strength >= 58 else "退潮" if sector_strength <= 34 else "分歧" if sector_strength <= 46 else "震荡"
    alpha_density = sum(value > (avg_change_pct or 0) for value in changes) / len(changes) * 100 if changes else None
    return {
        "market_return_1d": _pct_field_to_decimal(market_return),
        "sector_return_1d": avg_change,
        "sector_breadth": breadth,
        "sector_strength": sector_strength if members else None,
        "sector_flow": flow,
        "sector_state": sector_state if members else "未验证",
        "alpha_density": alpha_density,
        # This is explicitly a relative-strength proxy, not a full Alpha
        # model.  The UI labels it as a proxy until a PIT factor is available.
        "stock_alpha_score": _clamp(50 + (relative or 0) * 1200) if relative is not None else None,
        "relative_sector_1d": relative,
    }


class ReflexivityService:
    def __init__(self) -> None:
        self._scan_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def _load_bars(self, code: str, as_of: date | None = None) -> tuple[list[dict[str, Any]], str]:
        cutoff = as_of or shanghai_now().date()
        database_bars: list[dict[str, Any]] = []
        try:
            async with async_session() as session:
                rows = (await session.execute(
                    select(StockDailyBar).where(
                        StockDailyBar.stock_code == code,
                        StockDailyBar.trade_date <= cutoff,
                    ).order_by(StockDailyBar.trade_date.desc()).limit(450)
                )).scalars().all()
            database_bars = list(reversed([_bar(row) for row in rows]))
            # A short database tail is not enough for a six-dimensional
            # diagnosis.  Try the verified provider/cache path below and
            # merge it with the local rows instead of silently returning an
            # incomplete result.
            if len(database_bars) >= 21:
                return database_bars, "database_stock_daily_bars"
        except Exception:
            database_bars = []
        try:
            payload = await collector.fetch_stock_price_history(code, 420)
            provider_bars = [
                dict(row) for row in (payload.get("history") or [])
                if (_date(row.get("trade_date")) or date.max) <= cutoff
            ]
            if provider_bars:
                # Prefer the database observation for an already-known date,
                # but fill missing fields from the provider.  This preserves
                # the locally audited row while recovering short/partial
                # histories after a cold start.
                by_date = {_date(row.get("trade_date")): dict(row) for row in provider_bars if _date(row.get("trade_date"))}
                for row in database_bars:
                    day = _date(row.get("trade_date"))
                    if day is None:
                        continue
                    merged = dict(by_date.get(day) or {})
                    merged.update({key: value for key, value in row.items() if value not in (None, "")})
                    by_date[day] = merged
                merged_bars = [by_date[day] for day in sorted(by_date)]
                source = str(payload.get("source") or "tencent")
                return merged_bars, f"database+{source}" if database_bars else source
            if database_bars:
                return database_bars, "database_stock_daily_bars_partial"
        except Exception:
            if database_bars:
                return database_bars, "database_stock_daily_bars_partial"
        return [], "unavailable"

    async def _load_snapshot(self) -> dict[str, Any]:
        try:
            return await load_quant_market_snapshot()
        except Exception:
            return {"stocks": [], "source": "unavailable"}

    async def _quote_for(self, code: str, snapshot: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        for item in snapshot.get("stocks") or []:
            if str(item.get("code") or "") == code:
                return dict(item)
        if force or is_a_share_market_session():
            try:
                payload = await collector.fetch_stock_quotes([code])
                rows = payload.get("stocks") or []
                if rows:
                    return dict(rows[0])
            except Exception:
                pass
        return {}

    async def _auction_for(self, code: str, target: date) -> dict[str, Any] | None:
        try:
            async with async_session() as session:
                row = (await session.execute(
                    select(StockAuctionSnapshot).where(
                        StockAuctionSnapshot.stock_code == code,
                        StockAuctionSnapshot.trade_date <= target,
                    ).order_by(desc(StockAuctionSnapshot.trade_date), desc(StockAuctionSnapshot.quote_at)).limit(1)
                )).scalar_one_or_none()
            if row is None:
                return None
            return {
                "trade_date": row.trade_date,
                "quote_at": row.quote_at,
                "auction_price": row.auction_price,
                "previous_close": row.previous_close,
                "auction_volume": row.auction_volume,
                "auction_amount": row.auction_amount,
                "auction_volume_ratio": row.auction_volume_ratio,
            }
        except Exception:
            return None

    @staticmethod
    def _append_live_bar(bars: list[dict[str, Any]], quote: dict[str, Any], target: date, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        price = _num(quote.get("price"))
        if price is None:
            return bars
        latest = _date(bars[-1].get("trade_date")) if bars else None
        if latest == target:
            return bars
        return [*bars, {
            "trade_date": target,
            "open_price": _num(quote.get("open")),
            "close_price": price,
            "high_price": _num(quote.get("high")),
            "low_price": _num(quote.get("low")),
            "volume": _num(quote.get("volume")),
            "amount": _num(quote.get("amount")),
            "turnover": _num(quote.get("turnover")),
            "available_time": snapshot.get("fetched_at") or snapshot.get("cached_at") or shanghai_now().isoformat(),
        }]

    async def _persist(self, diagnosis: dict[str, Any], *, snapshot_time: datetime | None = None) -> None:
        await self._persist_many([diagnosis], snapshot_time=snapshot_time)

    async def _persist_many(self, diagnoses: list[dict[str, Any]], *, snapshot_time: datetime | None = None) -> None:
        valid = [item for item in diagnoses if item.get("data_date") and item.get("symbol")]
        if not valid:
            return
        try:
            when = snapshot_time or shanghai_now().replace(tzinfo=None)
            async with async_session() as session:
                for diagnosis in valid:
                    stock_code = str(diagnosis.get("symbol"))
                    trade_date = _date(diagnosis.get("data_date")) or when.date()
                    cutoff = self._parse_datetime(diagnosis.get("data_cutoff_time"))
                    model_version = str(diagnosis.get("model_version") or MODEL_VERSION)
                    # A refresh may ask for the same daily diagnosis many
                    # times.  Keep one row per stock/cutoff/model, while
                    # still allowing a new intraday cutoff to be recorded.
                    existing = (await session.execute(
                        select(BehaviorReflexivitySnapshot.id).where(
                            BehaviorReflexivitySnapshot.stock_code == stock_code,
                            BehaviorReflexivitySnapshot.trade_date == trade_date,
                            BehaviorReflexivitySnapshot.data_cutoff_time == cutoff,
                            BehaviorReflexivitySnapshot.model_version == model_version,
                        ).limit(1)
                    )).scalar_one_or_none()
                    if existing is not None:
                        continue
                    session.add(BehaviorReflexivitySnapshot(
                        stock_code=stock_code,
                        stock_name=diagnosis.get("name"),
                        trade_date=trade_date,
                        snapshot_time=when,
                        forced_buy_pressure=_num((diagnosis.get("forced_trading") or {}).get("forced_buy_pressure")),
                        forced_sell_pressure=_num((diagnosis.get("forced_trading") or {}).get("forced_sell_pressure")),
                        nearest_up_liquidity=(diagnosis.get("liquidity_map") or {}).get("nearest_up_liquidity_zone"),
                        nearest_down_liquidity=(diagnosis.get("liquidity_map") or {}).get("nearest_down_liquidity_zone"),
                        liquidity_asymmetry=_num((diagnosis.get("liquidity_map") or {}).get("liquidity_asymmetry_score")),
                        capital_price_efficiency=_num((diagnosis.get("capital_price_efficiency") or {}).get("score")),
                        capital_price_efficiency_delta=_num((diagnosis.get("capital_price_efficiency") or {}).get("efficiency_delta_1d")),
                        absorption_score=_num((diagnosis.get("absorption_pressure") or {}).get("absorption_score")),
                        pressure_score=_num((diagnosis.get("absorption_pressure") or {}).get("pressure_score")),
                        psychology_state=(diagnosis.get("psychology") or {}).get("psychology_state"),
                        psychology_transition={
                            "previous_state": (diagnosis.get("psychology") or {}).get("previous_state"),
                            "transition": (diagnosis.get("psychology") or {}).get("transition"),
                        },
                        reflexivity_state=(diagnosis.get("reflexivity") or {}).get("reflexivity_state"),
                        reflexivity_score=_num((diagnosis.get("reflexivity") or {}).get("reflexivity_score")),
                        crowding_score=_num((diagnosis.get("crowding") or {}).get("score")),
                        selection_score=_num(diagnosis.get("selection_score")),
                        diagnosis_level=diagnosis.get("diagnosis_level"),
                        candidate_type=diagnosis.get("candidate_type"),
                        data_cutoff_time=cutoff,
                        model_version=model_version,
                        skill_version=str(diagnosis.get("skill_version") or SKILL_VERSION),
                        payload=diagnosis,
                    ))
                await session.commit()
        except Exception as exc:
            # A data diagnosis remains useful when an optional audit write is
            # temporarily unavailable; the response exposes no false storage
            # success and the error is visible in server logs.
            print(f"Reflexivity snapshot persistence failed: {type(exc).__name__}")

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
        except (TypeError, ValueError):
            return None

    async def diagnose(self, symbol: str, *, as_of: date | None = None, force: bool = False) -> dict[str, Any]:
        code = normalize_stock_code(symbol)
        key = f"{code}:{as_of.isoformat() if as_of else 'latest'}"
        async with self._locks[code]:
            cached = self._scan_cache.get(("stock", key))
            if cached and not force and time.monotonic() - cached[0] <= SCAN_CACHE_SECONDS:
                result = deepcopy(cached[1])
                result.setdefault("data_quality", {})["cache_used"] = True
                return result
            snapshot = await self._load_snapshot()
            target = as_of or _date(snapshot.get("data_date")) or shanghai_now().date()
            bars, source = await self._load_bars(code, target)
            quote = await self._quote_for(code, snapshot, force=force)
            bars = self._append_live_bar(bars, quote, target, snapshot) if not as_of else bars
            auction = await self._auction_for(code, target)
            universe = snapshot.get("stocks") or []
            stock = next((dict(item) for item in universe if str(item.get("code") or "") == code), {"code": code})
            stock.update({key: value for key, value in quote.items() if value not in (None, "")})
            market_changes = [_num(item.get("change_pct")) for item in universe]
            market_changes = [value for value in market_changes if value is not None]
            market_return = sum(market_changes) / len(market_changes) if market_changes else None
            context = _context_for_stock(stock, universe, market_return)
            context["market_state"] = "实时快照" if snapshot.get("is_realtime") else "最近完整交易日"
            result = build_reflexivity_diagnosis(bars, as_of=target, context=context, auction=auction, symbol=code, name=stock.get("name") or code, horizon="3d")
            result["source"] = source
            result["market_segment"] = _segment(code)
            result["is_realtime"] = bool(snapshot.get("is_realtime") and target == shanghai_now().date() and is_a_share_market_session())
            result["auction_observed"] = bool(auction)
            result.setdefault("data_quality", {})["cache_used"] = False
            await self._persist(result)
            self._scan_cache[("stock", key)] = (time.monotonic(), result)
            return result

    async def scan(
        self,
        *,
        horizon: str = "3d",
        sector: str | None = None,
        min_score: float = 0,
        force: bool = False,
        exclude_star_market: bool = True,
        exclude_gem: bool = True,
        limit: int = 50,
    ) -> dict[str, Any]:
        cache_key = ("scan", horizon, sector or "all", float(min_score), exclude_star_market, exclude_gem, min(limit, 100))
        cached = self._scan_cache.get(cache_key)
        if cached and not force and time.monotonic() - cached[0] <= SCAN_CACHE_SECONDS:
            result = deepcopy(cached[1])
            result["cache_used"] = True
            return result
        snapshot = await self._load_snapshot()
        source_stocks = [dict(item) for item in (snapshot.get("stocks") or [])]
        filtered: list[dict[str, Any]] = []
        excluded = {"科创板": 0, "创业板": 0, "ST/退市/停牌": 0}
        for stock in source_stocks:
            raw_code = str(stock.get("code") or "")
            try:
                code = normalize_stock_code(raw_code)
            except ValueError:
                continue
            stock["code"] = code
            name = str(stock.get("name") or "")
            segment = _segment(code)
            if exclude_star_market and segment == "科创板":
                excluded[segment] += 1
                continue
            if exclude_gem and segment == "创业板":
                excluded[segment] += 1
                continue
            if "ST" in name.upper() or "退" in name or bool(stock.get("is_suspended")):
                excluded["ST/退市/停牌"] += 1
                continue
            if sector and sector.strip() and sector.strip() not in name and sector.strip() != str(stock.get("sector") or "").strip():
                continue
            if code:
                filtered.append(stock)
        def rank(item: dict[str, Any]) -> float:
            return abs(_num(item.get("change_pct")) or 0) * 2 + (_num(item.get("volume_ratio")) or 0) * 4 + max(0, (_num(item.get("main_net_inflow")) or 0) / 1e8) * 2
        filtered = sorted(filtered, key=rank, reverse=True)[:MAX_SCAN_STOCKS]
        target = _date(snapshot.get("data_date")) or shanghai_now().date()
        market_changes = [_num(item.get("change_pct")) for item in filtered]
        market_changes = [value for value in market_changes if value is not None]
        market_return = sum(market_changes) / len(market_changes) if market_changes else None
        codes = [str(item.get("code")) for item in filtered]
        bars_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if codes:
            try:
                async with async_session() as session:
                    rows = (await session.execute(
                        select(StockDailyBar).where(
                            StockDailyBar.stock_code.in_(codes),
                            StockDailyBar.trade_date <= target,
                            StockDailyBar.trade_date >= target - timedelta(days=730),
                        ).order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
                    )).scalars().all()
                for row in rows:
                    bars_by_code[row.stock_code].append(_bar(row))
            except Exception:
                pass
        diagnoses: list[dict[str, Any]] = []
        for stock in filtered:
            code = str(stock.get("code"))
            bars = bars_by_code.get(code) or []
            if not bars:
                continue
            bars = self._append_live_bar(bars, stock, target, snapshot)
            context = _context_for_stock(stock, filtered, market_return)
            context["market_state"] = "实时快照" if snapshot.get("is_realtime") else "最近完整交易日"
            diagnosis = build_reflexivity_diagnosis(bars, as_of=target, context=context, symbol=code, name=stock.get("name") or code, horizon=horizon)
            diagnosis["price"] = _num(stock.get("price"))
            diagnosis["change_pct"] = _num(stock.get("change_pct"))
            diagnosis["sector"] = stock.get("sector") or "未分类"
            diagnosis["market_segment"] = _segment(code)
            diagnosis["source"] = "database_stock_daily_bars"
            if diagnosis.get("selection_score") is not None and diagnosis.get("selection_score") >= min_score:
                diagnoses.append(diagnosis)
        diagnoses.sort(key=lambda item: (item.get("selection_score") is not None, item.get("selection_score") or -1), reverse=True)
        opportunity = [item for item in diagnoses if item.get("candidate_type") in OPPORTUNITY_CANDIDATES]
        risks = [item for item in diagnoses if item.get("candidate_type") not in OPPORTUNITY_CANDIDATES]
        result = {
            "version": "v5-reflexivity-runtime-1",
            "model_version": MODEL_VERSION,
            "skill_version": SKILL_VERSION,
            "generated_at": shanghai_now().isoformat(),
            "data_cutoff_time": snapshot.get("fetched_at") or snapshot.get("cached_at") or target.isoformat(),
            "trade_date": target.isoformat(),
            "horizon": horizon,
            "filters": {
                "sector": sector or "all", "min_score": min_score,
                "exclude_star_market": exclude_star_market, "exclude_gem": exclude_gem,
                "excluded_counts": excluded,
            },
            "candidates": diagnoses[:max(1, min(limit, 100))],
            "opportunity_candidates": opportunity[:max(1, min(limit, 100))],
            "risk_candidates": risks[:max(1, min(limit, 100))],
            "scanned_count": len(filtered), "observed_count": len(diagnoses),
            "universe_count": len(source_stocks),
            "data_quality": {
                "history_coverage": len(bars_by_code),
                "minimum_history_sessions": 21,
                "is_realtime": bool(snapshot.get("is_realtime")),
                "warnings": [
                    "缺少日线的股票不会用当前报价硬造反身性结构。",
                    "SHORT_COVER关闭；L2/逐笔方向不从日线推断。",
                    "候选排序权重未经过样本外校准，Skill 10保持EXPERIMENTAL。",
                ],
            },
            "audit": {"no_future_data": True, "available_time_rule": "trade_date <= data_cutoff_time"},
            "cache_used": False,
        }
        # Persist the bounded top set.  Full stock histories are available via
        # the single-stock endpoint and do not require a row for every refresh.
        # Keep writes bounded: a refresh can observe hundreds of symbols, but
        # opening one database transaction per symbol in parallel would make a
        # small personal Postgres/SQLite pool less responsive.
        await self._persist_many(diagnoses[:20])
        self._scan_cache[cache_key] = (time.monotonic(), result)
        return result

    async def history(self, symbol: str, limit: int = 30) -> list[dict[str, Any]]:
        code = normalize_stock_code(symbol)
        async with async_session() as session:
            rows = (await session.execute(
                select(BehaviorReflexivitySnapshot).where(
                    BehaviorReflexivitySnapshot.stock_code == code,
                ).order_by(desc(BehaviorReflexivitySnapshot.snapshot_time)).limit(min(max(limit, 1), 120))
            )).scalars().all()
        return [{
            "id": row.id, "stock_code": row.stock_code, "stock_name": row.stock_name,
            "trade_date": row.trade_date.isoformat(), "snapshot_time": row.snapshot_time.isoformat() if row.snapshot_time else None,
            "forced_buy_pressure": row.forced_buy_pressure, "forced_sell_pressure": row.forced_sell_pressure,
            "absorption_score": row.absorption_score, "pressure_score": row.pressure_score,
            "psychology_state": row.psychology_state, "psychology_transition": row.psychology_transition,
            "reflexivity_state": row.reflexivity_state, "reflexivity_score": row.reflexivity_score,
            "selection_score": row.selection_score, "diagnosis_level": row.diagnosis_level,
            "candidate_type": row.candidate_type, "candidate_label": CANDIDATE_LABELS.get(row.candidate_type or "", row.candidate_type),
            "data_cutoff_time": row.data_cutoff_time.isoformat() if row.data_cutoff_time else None,
            "model_version": row.model_version, "skill_version": row.skill_version,
        } for row in rows]

    async def explain(self, symbol: str, *, as_of: date | None = None, force: bool = False) -> dict[str, Any]:
        diagnosis = await self.diagnose(symbol, as_of=as_of, force=force)
        forced = diagnosis.get("forced_trading") or {}
        efficiency = diagnosis.get("capital_price_efficiency") or {}
        psychology = diagnosis.get("psychology") or {}
        reflexivity = diagnosis.get("reflexivity") or {}
        liquidity = diagnosis.get("liquidity_map") or {}
        narrative = "\n".join([
            f"{diagnosis.get('name') or diagnosis.get('symbol') or symbol}的行为反身性诊断结论：{diagnosis.get('candidate_label') or '暂无明确候选'}（{diagnosis.get('diagnosis_level') or 'S0'}）。",
            f"当前心理阶段为{psychology.get('transition') or psychology.get('psychology_state') or '未形成可观测状态'}，状态置信度{psychology.get('state_confidence') if psychology.get('state_confidence') is not None else '未计算'}。",
            f"潜在被迫买盘{forced.get('forced_buy_pressure') if forced.get('forced_buy_pressure') is not None else '未充分覆盖'}，潜在被迫卖盘{forced.get('forced_sell_pressure') if forced.get('forced_sell_pressure') is not None else '未充分覆盖'}；这只是价格、成交和位置代理，不代表已识别交易者意图。",
            f"资金价格效率状态为{efficiency.get('state') or '未形成'}，承接/抛压分别为{(diagnosis.get('absorption_pressure') or {}).get('absorption_score') or '未计算'}/{(diagnosis.get('absorption_pressure') or {}).get('pressure_score') or '未计算'}。",
            f"反身性判断为{reflexivity.get('reflexivity_label') or '未形成'}；最近上方流动性为{(liquidity.get('nearest_up_liquidity_zone') or {}).get('label') or '未发现已观测区域'}，下方为{(liquidity.get('nearest_down_liquidity_zone') or {}).get('label') or '未发现已观测区域'}。",
            f"结论只在以下条件继续成立时有效：{'；'.join(diagnosis.get('validation_conditions') or ['等待更多成交和板块数据'])}。",
            f"需要推翻当前判断的条件：{'；'.join(diagnosis.get('invalidation_conditions') or ['关键数据发生反向变化'])}。",
        ])
        return {
            "symbol": diagnosis.get("symbol"), "data_cutoff_time": diagnosis.get("data_cutoff_time"),
            "model_version": diagnosis.get("model_version"), "sources": ["PIT日线", "板块/市场快照"],
            "narrative": narrative, "diagnosis": diagnosis,
            "ai_boundary": "解释由结构化计算结果生成；AI不得修改分数、状态或数据来源。",
        }


reflexivity_service = ReflexivityService()
