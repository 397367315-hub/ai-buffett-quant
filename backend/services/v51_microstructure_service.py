"""V5.1 evidence service.

This service is deliberately additive.  It reads the existing PIT/daily/minute
tables and the existing public collectors, then persists bounded snapshots for
replay.  Missing auction history stays missing and is exposed in ``quality``.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import date, datetime, time as clock_time, timedelta
from typing import Any

from sqlalchemy import desc, func, select

from database import async_session
from models import (
    AuctionSnapshotV51,
    ConceptFundFlowDaily,
    IndustryFundFlowDaily,
    MarketBoard,
    MarketDataCache,
    StockAuctionSnapshot,
    StockDailyBar,
    StockMinuteBar,
    StockUniverseSnapshot,
    V51EngineSnapshot,
)
from quant.v51_microstructure import (
    AUCTION_MODEL_VERSION,
    ENGINE_VERSION,
    MODEL_VERSION,
    candlestick_semantics,
    disagreement_features,
    expectation_deviation,
    intraday_relative_strength,
    leadership_features,
    liquidity_map_features,
    market_reward_punishment,
    normalize_auction_snapshots,
    regulatory_risk_snapshot,
    supply_test_features,
)
from services.data_collector import (
    collector,
    is_a_share_market_session,
    normalize_stock_code,
    shanghai_now,
)
from services.macro_policy_news import macro_policy_news_collector
from services.pit_market_data import pit_market_data_service


def _value(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _bar_payload(row: StockDailyBar) -> dict[str, Any]:
    return {
        "stock_code": row.stock_code,
        "stock_name": row.stock_name,
        "trade_date": row.trade_date.isoformat() if row.trade_date else None,
        "open_price": row.open_price,
        "close_price": row.close_price,
        "high_price": row.high_price,
        "low_price": row.low_price,
        "volume": row.volume,
        "amount": row.amount,
        "change_pct": row.change_pct,
        "turnover": row.turnover,
        "source": row.source,
    }


def _minute_payload(row: StockMinuteBar) -> dict[str, Any]:
    return {
        "stock_code": row.stock_code,
        "bar_time": row.bar_time.isoformat() if row.bar_time else None,
        "interval_minutes": row.interval_minutes,
        "open_price": row.open_price,
        "close_price": row.close_price,
        "high_price": row.high_price,
        "low_price": row.low_price,
        "volume": row.volume,
        "amount": row.amount,
        "average_price": row.average_price,
        "source": row.source,
    }


class V51MicrostructureService:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def _latest_trade_date(self) -> date | None:
        async with async_session() as session:
            return (await session.execute(select(func.max(StockDailyBar.trade_date)))).scalar_one_or_none()

    async def _load_daily(self, code: str | None = None, target: date | None = None, lookback: int = 180) -> list[StockDailyBar]:
        target = target or await self._latest_trade_date()
        if target is None:
            return []
        start = target - timedelta(days=max(lookback, 20) * 2)
        async with async_session() as session:
            statement = select(StockDailyBar).where(
                StockDailyBar.trade_date <= target,
                StockDailyBar.trade_date >= start,
            )
            if code:
                statement = statement.where(StockDailyBar.stock_code == code)
            statement = statement.order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
            return list((await session.execute(statement)).scalars().all())

    async def _load_minutes(self, code: str, target: date | None = None, limit: int = 640) -> list[dict[str, Any]]:
        target = target or await self._latest_trade_date()
        if target is None:
            return []
        async with async_session() as session:
            rows = list((await session.execute(
                select(StockMinuteBar).where(
                    StockMinuteBar.stock_code == code,
                    StockMinuteBar.bar_time >= datetime.combine(target, datetime.min.time()),
                    StockMinuteBar.bar_time < datetime.combine(target + timedelta(days=1), datetime.min.time()),
                ).order_by(StockMinuteBar.bar_time.desc()).limit(limit)
            )).scalars().all())
        cached = [_minute_payload(row) for row in reversed(rows)]
        if cached:
            return cached
        # A minute bar may not have been persisted yet for a newly observed
        # session. Fetch the provider window once, then persist only bars whose
        # provider returned an explicit timestamp. This keeps 5/15/30 minute
        # validation available without fabricating bars from daily OHLC.
        try:
            live = await asyncio.wait_for(
                collector.fetch_stock_minute_history(code, interval_minutes=1, limit=min(limit, 1536)),
                timeout=8.0,
            )
        except Exception:
            return []
        bars = list(live.get("bars") or [])
        rows_to_write = []
        for item in bars:
            moment = _parse_datetime(item.get("bar_time"))
            if moment is None or moment.date() != target:
                continue
            rows_to_write.append({
                "stock_code": code,
                "stock_name": item.get("stock_name") or None,
                "bar_time": moment,
                "interval_minutes": int(item.get("interval_minutes") or 1),
                "open_price": item.get("open"),
                "close_price": item.get("close"),
                "high_price": item.get("high"),
                "low_price": item.get("low"),
                "volume": item.get("volume"),
                "amount": item.get("amount"),
                "average_price": item.get("average"),
                "source": live.get("source") or "eastmoney",
            })
        if rows_to_write:
            try:
                from sqlalchemy.dialects.postgresql import insert as postgresql_insert
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                async with async_session() as session:
                    dialect = session.get_bind().dialect.name
                    insert = postgresql_insert if dialect == "postgresql" else sqlite_insert
                    statement = insert(StockMinuteBar).values(rows_to_write)
                    updates = {column.name: getattr(statement.excluded, column.name) for column in StockMinuteBar.__table__.columns if column.name != "id"}
                    await session.execute(statement.on_conflict_do_update(index_elements=["stock_code", "bar_time", "interval_minutes"], set_=updates))
                    await session.commit()
            except Exception:
                pass
        return [{
            "stock_code": code,
            "bar_time": item.get("bar_time"),
            "interval_minutes": item.get("interval_minutes") or 1,
            "open_price": item.get("open"),
            "close_price": item.get("close"),
            "high_price": item.get("high"),
            "low_price": item.get("low"),
            "volume": item.get("volume"),
            "amount": item.get("amount"),
            "average_price": item.get("average"),
            "source": live.get("source") or "eastmoney",
        } for item in bars if _parse_datetime(item.get("bar_time")) and _parse_datetime(item.get("bar_time")).date() == target]

    async def _load_auction(self, code: str, target: date) -> list[dict[str, Any]]:
        async with async_session() as session:
            old = (await session.execute(
                select(StockAuctionSnapshot).where(
                    StockAuctionSnapshot.stock_code == code,
                    StockAuctionSnapshot.trade_date <= target,
                ).order_by(desc(StockAuctionSnapshot.trade_date), desc(StockAuctionSnapshot.quote_at)).limit(1)
            )).scalar_one_or_none()
            timeline = list((await session.execute(
                select(AuctionSnapshotV51).where(
                    AuctionSnapshotV51.stock_code == code,
                    AuctionSnapshotV51.trade_date == target,
                ).order_by(AuctionSnapshotV51.snapshot_time)
            )).scalars().all())
        rows = [{
            "snapshot_time": row.snapshot_time,
            "indicative_price": row.indicative_price,
            "previous_close": row.previous_close,
            "matched_volume": row.matched_volume,
            "matched_amount": row.matched_amount,
            "source": row.source,
        } for row in timeline]
        if not rows and old is not None:
            rows.append({
                "snapshot_time": old.quote_at,
                "auction_price": old.auction_price,
                "previous_close": old.previous_close,
                "auction_volume": old.auction_volume,
                "auction_amount": old.auction_amount,
                "source": old.source,
            })
        return rows

    async def _load_quote(self, code: str, target: date | None = None, force: bool = False) -> dict[str, Any]:
        target = target or await self._latest_trade_date()
        async with async_session() as session:
            row = (await session.execute(
                select(StockDailyBar).where(
                    StockDailyBar.stock_code == code,
                    StockDailyBar.trade_date <= target if target else True,
                ).order_by(StockDailyBar.trade_date.desc()).limit(1)
            )).scalar_one_or_none()
        cached = _bar_payload(row) if row else {"code": code}
        cached["code"] = code
        cached["price"] = cached.get("close_price")
        cached["name"] = cached.get("stock_name") or code
        cached["quote_source"] = cached.get("source") or "cache"
        if force and is_a_share_market_session():
            try:
                live = await asyncio.wait_for(collector.fetch_stock_quotes([code]), timeout=5.0)
                item = (live.get("stocks") or [None])[0]
                if item:
                    return {**cached, **item, "quote_source": live.get("source") or item.get("quote_source")}
            except Exception as exc:
                cached["live_error"] = type(exc).__name__
        return cached

    async def _persist_engine(self, *, code: str | None, trade_date: date, engine_id: str, payload: dict[str, Any]) -> None:
        quality = payload.get("quality") or payload.get("data_quality") or {}
        try:
            async with async_session() as session:
                session.add(V51EngineSnapshot(
                    stock_code=code,
                    trade_date=trade_date,
                    engine_id=engine_id,
                    status=str(quality.get("status") or payload.get("state") or "OBSERVED"),
                    model_version=str(payload.get("model_version") or ENGINE_VERSION),
                    data_cutoff_time=_parse_datetime(payload.get("data_cutoff_time")) or datetime.utcnow(),
                    coverage_pct=float(quality.get("coverage_pct") or 0),
                    payload=payload,
                ))
                await session.commit()
        except Exception as exc:
            # Research output remains usable when an optional audit write is
            # blocked by a transient database issue.
            print(f"V5.1 snapshot persistence failed: {engine_id}: {type(exc).__name__}")

    async def _sector_context(self, sector: str | None = None) -> dict[str, Any]:
        try:
            rotation = await asyncio.wait_for(collector.fetch_sector_rotation(5), timeout=6.0)
            sectors = rotation.get("sectors") or []
            if sector:
                found = next((item for item in sectors if str(item.get("name") or "") == sector or str(item.get("code") or "") == sector), None)
                return found or {}
            return {"sectors": sectors}
        except Exception:
            return {"sectors": []}

    async def diagnose(self, symbol: str, *, refresh: bool = False, as_of: date | None = None) -> dict[str, Any]:
        code = normalize_stock_code(symbol)
        key = f"diagnose:{code}:{as_of or 'latest'}"
        cached = self._cache.get(key)
        if cached and not refresh and time.monotonic() - cached[0] < 45:
            result = dict(cached[1])
            result["cache_used"] = True
            return result
        async with self._locks[code]:
            cached = self._cache.get(key)
            if cached and not refresh and time.monotonic() - cached[0] < 45:
                result = dict(cached[1])
                result["cache_used"] = True
                return result
            target = as_of or await self._latest_trade_date() or shanghai_now().date()
            daily_rows = await self._load_daily(code, target, 180)
            bars = [_bar_payload(row) for row in daily_rows]
            quote = await self._load_quote(code, target, force=refresh)
            # Historical diagnoses must be frozen at the requested session.
            # A live quote can only advance the cutoff while the market is open;
            # otherwise the latest completed session is the honest boundary.
            cutoff_time = shanghai_now() if quote.get("quote_timestamp") and is_a_share_market_session() else datetime.combine(target, datetime.max.time()).replace(microsecond=0)
            previous_close = None
            if len(bars) >= 2:
                previous_close = _value(bars[-2].get("close_price"))
            minutes = await self._load_minutes(code, target)
            auction_rows = await self._load_auction(code, target)
            auction = normalize_auction_snapshots(auction_rows, previous_close=previous_close, data_cutoff_time=cutoff_time)
            sector = str(quote.get("sector") or "") or None
            sector_ctx = await self._sector_context(sector)
            sector_return = _value(sector_ctx.get("change_pct"))
            disagreement = disagreement_features(bars, sector_return=sector_return, data_cutoff_time=cutoff_time)
            supply = supply_test_features(bars, data_cutoff_time=cutoff_time)
            candle = candlestick_semantics(bars, data_cutoff_time=cutoff_time)
            liquidity = liquidity_map_features(bars, data_cutoff_time=cutoff_time)
            expectation = expectation_deviation(auction, minutes, previous_close=previous_close, sector_return=sector_return, data_cutoff_time=cutoff_time)
            intraday = {"state": "NO_BENCHMARK_CONFIRMATION", "quality": {"status": "NO_BENCHMARK_CONFIRMATION", "coverage_pct": 0, "source": "unavailable", "model_version": MODEL_VERSION}}
            regulatory = {"state": "NOT_REQUESTED", "risk_score": None, "quality": {"status": "NOT_REQUESTED", "coverage_pct": 0, "source": "unavailable", "model_version": MODEL_VERSION}}
            try:
                announcement_audit = await asyncio.wait_for(macro_policy_news_collector.get_stock_announcements_audit([code], 1), timeout=5.0)
                announcements = (announcement_audit.get("announcements") or {}).get(code, [])
                regulatory = regulatory_risk_snapshot(announcements, data_cutoff_time=cutoff_time)
            except Exception:
                pass
            result = {
                "symbol": code,
                "name": quote.get("name") or quote.get("stock_name") or code,
                "sector": sector,
                "price": quote.get("price") or quote.get("close_price"),
                "change_pct": quote.get("change_pct"),
                "data_date": target.isoformat(),
                "data_cutoff_time": cutoff_time.isoformat(),
                "is_realtime": bool(quote.get("quote_timestamp") and is_a_share_market_session()),
                "model_version": ENGINE_VERSION,
                "engines": {
                    "supply_test": supply,
                    "auction_microstructure": auction,
                    "expectation_deviation": expectation,
                    "disagreement_absorption": disagreement,
                    "candlestick_semantic": candle,
                    "liquidity_map": liquidity,
                    "intraday_relative_strength": intraday,
                    "regulatory_risk": regulatory,
                },
                "integration": {
                    "skill_10_reflexivity": "可由现有Skill 10继续读取同一日线与流动性证据",
                    "overnight_auction": "仅提供竞价观察，不替代原有竞价确认策略",
                    "action": "NO_TRADE" if (supply.get("state") == "SUPPLY_NOT_REDUCED" or regulatory.get("state") == "HIGH") else "RESEARCH_ONLY",
                },
                "quality": {
                    "daily_sessions": len(bars),
                    "minute_bars": len(minutes),
                    "auction_snapshots": len(auction_rows),
                    "auction_model_status": (auction.get("quality") or {}).get("status"),
                    "warnings": [
                        "竞价序列不足时不输出竞价预测。" if len(auction_rows) < 2 else None,
                        "K线语义只提供原子特征，必须等待后续确认。",
                        "公开分钟源只代表可获取窗口，不等同于完整历史。" if minutes else "分钟验证数据不可用。",
                    ],
                },
                "cache_used": False,
            }
            result["quality"]["warnings"] = [item for item in result["quality"]["warnings"] if item]
            for engine_id, payload in result["engines"].items():
                await self._persist_engine(code=code, trade_date=target, engine_id=engine_id, payload=payload)
            self._cache[key] = (time.monotonic(), result)
            return result

    async def scan(self, *, engine: str = "all", limit: int = 30, refresh: bool = False) -> dict[str, Any]:
        target = await self._latest_trade_date()
        if target is None:
            return {"engine": engine, "candidates": [], "quality": {"status": "NO_DAILY_DATA", "coverage_pct": 0, "model_version": ENGINE_VERSION}}
        async with async_session() as session:
            universe_rows = list((await session.execute(
                select(StockUniverseSnapshot).where(StockUniverseSnapshot.trade_date == target).order_by(StockUniverseSnapshot.market_cap.desc().nullslast()).limit(300)
            )).scalars().all())
            if not universe_rows:
                daily_rows = list((await session.execute(
                    select(StockDailyBar).where(StockDailyBar.trade_date == target).order_by(StockDailyBar.amount.desc().nullslast()).limit(300)
                )).scalars().all())
                codes = [row.stock_code for row in daily_rows]
            else:
                codes = [row.stock_code for row in universe_rows]
        candidates = []
        # Bound the expensive per-symbol evidence calculation for a personal
        # deployment while reporting the sampled universe explicitly.
        for code in codes[:max(1, min(limit, 80))]:
            try:
                item = await self.diagnose(code, refresh=refresh, as_of=target)
            except Exception:
                continue
            engines = item.get("engines") or {}
            supply = engines.get("supply_test") or {}
            disagreement = engines.get("disagreement_absorption") or {}
            candle = engines.get("candlestick_semantic") or {}
            score = 50.0
            if supply.get("state") == "BREAKOUT_CONFIRMED":
                score += 18
            if disagreement.get("state") == "ABSORBED":
                score += 14
            if candle.get("state") == "DIRECTIONAL_BODY_ATOM":
                score += 6
            if supply.get("state") == "SUPPLY_NOT_REDUCED":
                score -= 18
            if disagreement.get("state") == "ACTIVE_DISAGREEMENT":
                score -= 8
            item["selection_score"] = round(max(0, min(100, score)), 2)
            item["engine_match"] = engine
            candidates.append(item)
        candidates.sort(key=lambda item: item.get("selection_score") or 0, reverse=True)
        sampled_count = len(codes[:max(1, min(limit, 80))])
        return {
            "engine": engine,
            "trade_date": target.isoformat(),
            "model_version": ENGINE_VERSION,
            "data_cutoff_time": target.isoformat(),
            "candidates": candidates[:max(1, min(limit, 100))],
            "scanned_count": len(codes[:max(1, min(limit, 80))]),
            "quality": {
                "status": "SAMPLED_OBSERVED" if candidates else "NO_OBSERVATIONS",
                "coverage_pct": round(len(candidates) / max(1, sampled_count) * 100, 1),
                "universe_count": len(codes),
                "sampled_count": sampled_count,
                "warning": "扫描为有界样本；没有日线或竞价证据的标的不进入模型结论。",
            },
        }

    async def auction_dashboard(self, *, refresh: bool = False) -> dict[str, Any]:
        target = await self._latest_trade_date()
        if target is None:
            return {"status": "NO_DATA", "model_version": AUCTION_MODEL_VERSION, "quality": {"coverage_pct": 0}}
        async with async_session() as session:
            total = (await session.execute(
                select(func.count(func.distinct(StockUniverseSnapshot.stock_code))).where(
                    StockUniverseSnapshot.trade_date == target,
                )
            )).scalar_one() or 0
            observed = (await session.execute(
                select(func.count(func.distinct(StockAuctionSnapshot.stock_code))).where(
                    StockAuctionSnapshot.trade_date == target,
                )
            )).scalar_one() or 0
            timeline_count = (await session.execute(
                select(func.count(func.distinct(AuctionSnapshotV51.stock_code))).where(
                    AuctionSnapshotV51.trade_date == target,
                )
            )).scalar_one() or 0
            latest = list((await session.execute(select(StockAuctionSnapshot).where(StockAuctionSnapshot.trade_date == target).order_by(StockAuctionSnapshot.high_open_pct.desc().nullslast()).limit(12))).scalars().all())
        rows = [{
            "snapshot_time": item.quote_at,
            "auction_price": item.auction_price,
            "previous_close": item.previous_close,
            "auction_volume": item.auction_volume,
            "auction_amount": item.auction_amount,
            "source": item.source,
        } for item in latest]
        # A historical dashboard must be bounded by that session's close. A
        # current wall-clock cutoff would make replay results depend on when
        # the endpoint was opened after the market closed.
        cutoff_time = datetime.combine(target, clock_time(15, 0))
        features = normalize_auction_snapshots(rows, data_cutoff_time=cutoff_time)
        return {
            "trade_date": target.isoformat(),
            "model_version": AUCTION_MODEL_VERSION,
            "observed_stocks": observed,
            "time_series_snapshots": timeline_count,
            "universe_count": total,
            "coverage_pct": round(observed / max(1, total) * 100, 2),
            "timeline_coverage_pct": round(timeline_count / max(1, total) * 100, 2),
            "latest_observations": [{"code": item.stock_code, "name": item.stock_name, "high_open_pct": item.high_open_pct, "source": item.source, "is_realtime": item.is_realtime} for item in latest],
            "sample_features": features,
            "quality": {
                "status": "SINGLE_SNAPSHOT_ONLY" if observed and timeline_count == 0 else "OBSERVED",
                "warning": "当前部署已保存09:24-09:27单点竞价；完整09:15-09:25序列从接入后逐步累积。" if timeline_count == 0 else None,
                "no_fake_backtest": True,
            },
        }

    async def leadership_sectors(self, *, refresh: bool = False) -> dict[str, Any]:
        target = await self._latest_trade_date()
        rotation = await self._sector_context()
        sectors = rotation.get("sectors") or []
        ranked = []
        for item in sectors[:100]:
            change = _value(item.get("change_pct"))
            flow = _value(item.get("main_net_inflow"))
            score = 50 + (change or 0) * 6 + (flow or 0) / 1e8 * 2
            ranked.append({**item, "leadership_score": round(max(0, min(100, score)), 2), "beneficiary_purity_status": "待成分股确认"})
        ranked.sort(key=lambda item: item.get("leadership_score") or 0, reverse=True)
        return {"trade_date": target.isoformat() if target else None, "model_version": MODEL_VERSION, "sectors": ranked[:50], "quality": {"status": "OBSERVED" if ranked else "NO_DATA", "coverage_pct": 100 if ranked else 0, "source": "EastMoney/缓存" if ranked else "unavailable"}}

    async def leadership_sector(self, sector: str, *, refresh: bool = False) -> dict[str, Any]:
        target = await self._latest_trade_date()
        if target is None:
            return {"sector": sector, "quality": {"status": "NO_DATA", "coverage_pct": 0}}
        async with async_session() as session:
            universe = list((await session.execute(select(StockUniverseSnapshot).where(StockUniverseSnapshot.trade_date == target, StockUniverseSnapshot.industry == sector).limit(120))).scalars().all())
            codes = [item.stock_code for item in universe]
            daily = list((await session.execute(select(StockDailyBar).where(StockDailyBar.trade_date == target, StockDailyBar.stock_code.in_(codes)))).scalars().all()) if codes else []
        by_code = {item.stock_code: item for item in universe}
        stocks = []
        for row in daily:
            item = by_code.get(row.stock_code)
            stocks.append({"code": row.stock_code, "name": row.stock_name, "sector": sector, "change_pct": row.change_pct, "main_net_inflow": None, "market_cap": item.market_cap if item else None})
        result = leadership_features(stocks, sector_name=sector, data_cutoff_time=target)
        return {"sector": sector, "trade_date": target.isoformat(), **result}

    async def catalyst(self, value: str, *, refresh: bool = False) -> dict[str, Any]:
        code = None
        try:
            code = normalize_stock_code(value)
        except ValueError:
            pass
        if code:
            diagnosis = await self.diagnose(code, refresh=refresh)
            return {"symbol": code, "sector": diagnosis.get("sector"), "purity": {"status": "BUSINESS_EVIDENCE_REQUIRED", "score": None}, "diagnosis": diagnosis}
        sector = value.strip()
        result = await self.leadership_sector(sector, refresh=refresh)
        return {"sector": sector, "purity": {"score": result.get("beneficiary_purity_pct"), "status": result.get("state")}, "leadership": result}

    async def reward_punishment(self, *, refresh: bool = False) -> dict[str, Any]:
        target = await self._latest_trade_date()
        if target is None:
            return {"quality": {"status": "NO_DATA", "coverage_pct": 0}}
        async with async_session() as session:
            rows = list((await session.execute(select(StockDailyBar).where(StockDailyBar.trade_date == target).limit(1000))).scalars().all())
        payload = [{"change_pct": row.change_pct, "amount": row.amount} for row in rows]
        return {"trade_date": target.isoformat(), **market_reward_punishment(payload, data_cutoff_time=target)}

    async def regulatory(self, symbol: str, *, refresh: bool = False) -> dict[str, Any]:
        code = normalize_stock_code(symbol)
        target = await self._latest_trade_date() or shanghai_now().date()
        announcements: list[dict[str, Any]] = []
        try:
            audit = await asyncio.wait_for(macro_policy_news_collector.get_stock_announcements_audit([code], 1), timeout=6.0)
            announcements = (audit.get("announcements") or {}).get(code, [])
        except Exception:
            pass
        return {"symbol": code, "trade_date": target.isoformat(), **regulatory_risk_snapshot(announcements, data_cutoff_time=target)}


v51_microstructure_service = V51MicrostructureService()
