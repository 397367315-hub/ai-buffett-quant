"""V2.1 market/sector bridge service.

This service deliberately sits beside V2.0.  It reads the existing daily
bars, market breadth, board flow and A/B/C snapshots, then stores a separate
Shadow snapshot for replay and verification.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import desc, func, select

from database import async_session
from models import (
    ABOppSnapshot,
    ConceptFundFlowDaily,
    EvolutionProposal,
    IndustryFundFlowDaily,
    MarketBoard,
    MarketFundFlowDaily,
    MarketRegimeState,
    MarketSentimentDaily,
    PersonalPoolItem,
    PredictionOutcome,
    RadarEvent,
    SectorDailySnapshot,
    SectorLifecycleState,
    SectorMigrationInference,
    StockDailyBar,
    StockUniverseSnapshot,
    StockSkillSignal,
    ThemeState,
    ThreeBooksConsensus,
    TradingZoneGeometry,
    MainForceState,
)
from services.data_collector import shanghai_now
from strong_stock_decision.v21_engine import (
    EVOLUTION_VERSION,
    MARKET_SECTOR_BRIDGE_VERSION,
    STRONG_STOCK_V21_VERSION,
    EvolutionEngine,
    MarketRegimeEngine,
    PostMarketDecisionOrchestrator,
)


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _iso(value: Any) -> str | None:
    target = _date(value)
    return target.isoformat() if target else None


def _breadth(up: Any, down: Any) -> float | None:
    up, down = _num(up), _num(down)
    if up is None or down is None or up + down <= 0:
        return None
    return up / (up + down)


def _row_payload(row: Any) -> dict[str, Any]:
    return {
        "trade_date": _iso(getattr(row, "trade_date", None)),
        "sector_id": getattr(row, "sector_id", None),
        "sector_name": getattr(row, "sector_name", None),
        "sector_type": getattr(row, "sector_type", None),
        "rank": getattr(row, "rank", None),
        "pct_change": getattr(row, "pct_change", None),
        "relative_return_vs_market": getattr(row, "relative_return_vs_market", None),
        "turnover": getattr(row, "turnover", None),
        "turnover_share": getattr(row, "turnover_share", None),
        "main_force_net_inflow": getattr(row, "main_force_net_inflow", None),
        "main_force_inflow_ratio": getattr(row, "main_force_inflow_ratio", None),
        "fund_continuity": getattr(row, "fund_continuity", None),
        "breadth": getattr(row, "breadth", None),
        "limit_up_count": getattr(row, "limit_up_count", None),
        "limit_up_linkage": getattr(row, "limit_up_linkage", None),
        "core_strength": getattr(row, "core_strength", None),
        "data_quality": getattr(row, "data_quality_json", None) or {},
    }


class StrongStockV21Service:
    def __init__(self) -> None:
        self.orchestrator = PostMarketDecisionOrchestrator()
        self.evolution_engine = EvolutionEngine()

    async def _target_date(self, requested: date | None = None) -> date:
        if requested:
            return requested
        async with async_session() as session:
            values = [
                (await session.execute(select(func.max(StockDailyBar.trade_date)))).scalar_one_or_none(),
                (await session.execute(select(func.max(MarketSentimentDaily.trade_date)))).scalar_one_or_none(),
                (await session.execute(select(func.max(IndustryFundFlowDaily.trade_date)))).scalar_one_or_none(),
            ]
        dates = [item for item in values if isinstance(item, date)]
        return max(dates) if dates else shanghai_now().date()

    async def _market(self, target: date) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        async with async_session() as session:
            sentiments = list((await session.execute(
                select(MarketSentimentDaily).where(MarketSentimentDaily.trade_date <= target).order_by(desc(MarketSentimentDaily.trade_date)).limit(21)
            )).scalars().all())
            flows = list((await session.execute(
                select(MarketFundFlowDaily).where(MarketFundFlowDaily.trade_date <= target).order_by(desc(MarketFundFlowDaily.trade_date)).limit(21)
            )).scalars().all())
        latest = sentiments[0] if sentiments else None
        latest_flow = flows[0] if flows else None
        market_amounts = [getattr(item, "market_amount", None) for item in sentiments]
        amount = _num(getattr(latest, "market_amount", None)) if latest else None
        baseline_values = [_num(item) for item in market_amounts[1:] if _num(item) is not None]
        baseline = sum(baseline_values) / len(baseline_values) if baseline_values else None
        turnover_activity = amount / baseline if amount is not None and baseline not in (None, 0) else None
        up = getattr(latest, "up_count", None) if latest else None
        down = getattr(latest, "down_count", None) if latest else None
        breadth = _breadth(up, down)
        risk_market = {
            "up_count": up, "down_count": down,
            "flat_count": getattr(latest, "flat_count", None) if latest else None,
            "breadth_ratio": breadth, "market_amount": amount,
            "turnover_activity": turnover_activity,
            "limit_up_count": getattr(latest, "limit_up_count", None) if latest else None,
            "limit_down_count": getattr(latest, "limit_down_count", None) if latest else None,
            "failed_limit_rate": getattr(latest, "failed_limit_rate", None) if latest else None,
            "market_flow": getattr(latest_flow, "main_net_inflow", None) if latest_flow else None,
            "source": getattr(latest, "source", None) if latest else "market_sentiment_daily",
            "data_date": target.isoformat(),
        }
        # A daily stock cache is the auditable fallback outside the session.
        # It is not labelled realtime and it is never used as a synthetic index.
        if latest is None:
            risk_market["data_quality"] = {"status": "DATA_INCOMPLETE", "missing_fields": ["market_sentiment_daily"]}
        return risk_market, [{"trade_date": _iso(item.trade_date), "market_amount": item.market_amount} for item in reversed(sentiments)]

    async def _sector_rows(self, target: date, sector_type: str | None = None) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        models = [("industry", IndustryFundFlowDaily), ("concept", ConceptFundFlowDaily)]
        if sector_type:
            models = [(kind, model) for kind, model in models if kind == sector_type]
        output: dict[str, list[dict[str, Any]]] = defaultdict(list)
        latest_rows: list[dict[str, Any]] = []
        async with async_session() as session:
            board_names: dict[tuple[str, str], str] = {}
            for kind, model in models:
                rows = list((await session.execute(
                    select(model).where(model.trade_date <= target).order_by(model.trade_date.asc()).limit(12000)
                )).scalars().all())
                codes = list({str(row.board_code) for row in rows})
                if codes:
                    board_names.update({
                        (kind, row.code): row.name for row in (await session.execute(
                            select(MarketBoard).where(MarketBoard.board_type == kind, MarketBoard.code.in_(codes))
                        )).scalars().all()
                    })
                by_date: dict[date, list[Any]] = defaultdict(list)
                for row in rows:
                    by_date[row.trade_date].append(row)
                for trade_day, day_rows in by_date.items():
                    ordered = sorted(day_rows, key=lambda item: _num(item.main_net_inflow) if _num(item.main_net_inflow) is not None else -float("inf"), reverse=True)
                    for rank, row in enumerate(ordered, start=1):
                        code = str(row.board_code)
                        name = board_names.get((kind, code), code)
                        breadth = _breadth(row.up_count, row.down_count)
                        item = {
                            "trade_date": trade_day.isoformat(), "sector_id": code, "sector_name": name, "sector_type": kind,
                            "rank": rank, "pct_change": row.change_pct,
                            "relative_return_vs_market": row.change_pct,
                            # Existing fund-flow tables do not retain board turnover.
                            # Keep the normalized ratio unknown rather than using an
                            # incomparable absolute amount as a substitute.
                            "turnover": None, "turnover_share": None,
                            "main_force_net_inflow": row.main_net_inflow,
                            # f184 is the provider's main-flow share of board
                            # turnover.  Preserve its provenance instead of
                            # comparing absolute flows between board sizes.
                            "main_force_inflow_ratio": (
                                _num(row.main_net_inflow_pct) / 100
                                if _num(row.main_net_inflow_pct) is not None and abs(_num(row.main_net_inflow_pct)) > 1
                                else _num(row.main_net_inflow_pct)
                            ),
                            "fund_continuity": None, "breadth": breadth,
                            "limit_up_count": None, "limit_up_linkage": None, "core_strength": None,
                            "raw_source": {"source": "ConceptFundFlowDaily" if kind == "concept" else "IndustryFundFlowDaily", "main_net_inflow_pct": row.main_net_inflow_pct},
                            "data_quality": {"status": "PARTIAL", "missing_fields": ["sector_turnover", "limit_up_linkage", "core_strength"], "normalization": "provider_main_net_inflow_pct"},
                        }
                        output[code].append(item)
                        if trade_day == target:
                            latest_rows.append(item)
            # If the requested date is a non-trading day, use the latest cached
            # complete board date at or before it, but preserve its source date.
            if not latest_rows:
                last_date = max((_date(item.get("trade_date")) for rows in output.values() for item in rows if _date(item.get("trade_date"))), default=None)
                if last_date:
                    latest_rows = [item for rows in output.values() for item in rows if _date(item.get("trade_date")) == last_date]
        # Calculate continuity from the stored multi-day sign, without a new
        # source call or a single-day start/exit shortcut.
        for code, rows in output.items():
            recent = rows[-5:]
            positive = [item for item in recent if _num(item.get("main_force_net_inflow")) is not None and _num(item.get("main_force_net_inflow")) > 0]
            continuity = len(positive) / len(recent) if recent else None
            for item in rows:
                item["fund_continuity"] = round(continuity, 3) if continuity is not None else None
        return output, sorted(latest_rows, key=lambda item: item.get("rank") or 9999)

    async def _sector_context(self, target: date) -> dict[str, Any]:
        histories, latest = await self._sector_rows(target)
        lifecycle: dict[str, dict[str, Any]] = {}
        for sector_id, rows in histories.items():
            state = self.orchestrator.lifecycle.evaluate(rows)
            lifecycle[sector_id] = {"sector_id": sector_id, "sector_name": rows[-1].get("sector_name"), **state}
        return {"histories": histories, "latest": latest, "lifecycle": lifecycle}

    async def _candidate_rows(self, target: date) -> list[dict[str, Any]]:
        async with async_session() as session:
            zones = list((await session.execute(
                select(TradingZoneGeometry).where(TradingZoneGeometry.trade_time <= datetime.combine(target, datetime.max.time())).order_by(desc(TradingZoneGeometry.trade_time)).limit(1500)
            )).scalars().all())
            latest_zone: dict[str, Any] = {}
            for row in zones:
                latest_zone.setdefault(row.symbol, row)
            symbols = list(latest_zone)[:800]
            if not symbols:
                return []
            main_rows = list((await session.execute(select(MainForceState).where(MainForceState.symbol.in_(symbols), MainForceState.trade_time <= datetime.combine(target, datetime.max.time())).order_by(desc(MainForceState.trade_time)).limit(1600))).scalars().all())
            consensus_rows = list((await session.execute(select(ThreeBooksConsensus).where(ThreeBooksConsensus.symbol.in_(symbols), ThreeBooksConsensus.trade_time <= datetime.combine(target, datetime.max.time())).order_by(desc(ThreeBooksConsensus.trade_time)).limit(1600))).scalars().all())
            themes = list((await session.execute(select(ThemeState).where(ThemeState.symbol.in_(symbols), ThemeState.trade_time <= datetime.combine(target, datetime.max.time())).order_by(desc(ThemeState.trade_time)).limit(1600))).scalars().all())
            bars = list((await session.execute(select(StockDailyBar).where(StockDailyBar.stock_code.in_(symbols), StockDailyBar.trade_date <= target).order_by(desc(StockDailyBar.trade_date)).limit(2000))).scalars().all())
            universes = list((await session.execute(
                select(StockUniverseSnapshot).where(
                    StockUniverseSnapshot.stock_code.in_(symbols),
                    StockUniverseSnapshot.trade_date <= target,
                ).order_by(desc(StockUniverseSnapshot.trade_date)).limit(1600)
            )).scalars().all())
            industry_names = {
                str(row.name): str(row.code)
                for row in (await session.execute(
                    select(MarketBoard).where(MarketBoard.board_type == "industry")
                )).scalars().all()
                if row.name and row.code
            }
            latest_main: dict[str, Any] = {}
            latest_consensus: dict[str, Any] = {}
            latest_theme: dict[str, Any] = {}
            latest_bar: dict[str, Any] = {}
            latest_universe: dict[str, Any] = {}
            for row in main_rows: latest_main.setdefault(row.symbol, row)
            for row in consensus_rows: latest_consensus.setdefault(row.symbol, row)
            for row in themes: latest_theme.setdefault(row.symbol, row)
            for row in bars: latest_bar.setdefault(row.stock_code, row)
            for row in universes: latest_universe.setdefault(row.stock_code, row)
        result = []
        for symbol, zone in latest_zone.items():
            main, consensus, theme, bar = latest_main.get(symbol), latest_consensus.get(symbol), latest_theme.get(symbol), latest_bar.get(symbol)
            universe = latest_universe.get(symbol)
            industry_name = str(getattr(universe, "industry", None) or "").strip() or None
            theme_name = getattr(theme, "theme_name", None) if theme else None
            result.append({
                "symbol": symbol, "stock_name": getattr(bar, "stock_name", None) or symbol,
                # Use the point-in-time industry directory as the primary
                # sector link. ThemeState is a useful supporting signal, but
                # its free-form theme name is not a stable sector identifier.
                "sector_id": industry_names.get(industry_name, "UNKNOWN"),
                "sector_name": industry_name or theme_name,
                "sector_type": "industry" if industry_name else None,
                "theme_name": theme_name,
                "zone": zone.zone, "zone_stage": zone.zone_stage,
                "main_force_state": getattr(main, "main_force_direction", None) if main else None,
                "volume_price_state": None, "ma_state": None,
                "big_pattern_state": None, "rising_star_state": None,
                "three_books_consensus": getattr(consensus, "consensus_level", None) if consensus else None,
                "risk_state": "C_RISK" if "C" in str(zone.zone) or "C_" in str(zone.zone_stage) else None,
                "close_price": getattr(bar, "close_price", None) if bar else None,
                "change_pct": getattr(bar, "change_pct", None) if bar else None,
                "invalidation": ["交易区进入C区", "板块生命周期转为FADING"],
            })
        return result

    async def build(self, requested: date | None = None, *, persist: bool = False) -> dict[str, Any]:
        target = await self._target_date(requested)
        market, market_history = await self._market(target)
        sector_context = await self._sector_context(target)
        industry_histories = {
            key: rows for key, rows in sector_context["histories"].items()
            if rows and rows[-1].get("sector_type") == "industry"
        }
        current_top = {key for key, rows in industry_histories.items() if rows[-1].get("rank") is not None and rows[-1].get("rank") <= 10}
        previous_top = {key for key, rows in industry_histories.items() if len(rows) > 1 and rows[-2].get("rank") is not None and rows[-2].get("rank") <= 10}
        if current_top and previous_top:
            market["top10_overlap_1d"] = len(current_top & previous_top) / 10
            market["sector_churn"] = 1 - market["top10_overlap_1d"]
        regime = self.orchestrator.market.evaluate(market, sector_context["latest"])
        lifecycle = sector_context["lifecycle"]
        current = sector_context["latest"]
        previous = [rows[-2] for rows in sector_context["histories"].values() if len(rows) > 1]
        migration = self.orchestrator.migration.infer(current, previous)
        candidates = await self._candidate_rows(target)
        for row in candidates:
            if row.get("sector_name"):
                match = next((item for item in current if item.get("sector_name") == row["sector_name"]), None)
                if match:
                    row["sector_id"] = match["sector_id"]
        opportunities = self.orchestrator.fusion.fuse(candidates, regime["regime"], lifecycle)
        payload = {
            "module_id": STRONG_STOCK_V21_VERSION,
            "bridge_version": MARKET_SECTOR_BRIDGE_VERSION,
            "mode": "SHADOW", "trade_date": target.isoformat(),
            "source_data_date": market.get("data_date"),
            "market": {**market, "regime": regime},
            "market_history": market_history,
            "sectors": [{**row, "lifecycle": lifecycle.get(row.get("sector_id"), {})} for row in current],
            "sector_trajectories": {key: self.orchestrator.trajectory.build(rows) for key, rows in sector_context["histories"].items()},
            "lifecycle": list(lifecycle.values()),
            "migration": migration,
            "opportunities": opportunities,
            "data_quality": {"status": "COMPLETE" if current and market.get("up_count") is not None else "PARTIAL", "source_name": "MarketSentimentDaily + IndustryFundFlowDaily + ConceptFundFlowDaily + V2.0 snapshots", "source_time": target.isoformat(), "missing_fields": sorted({field for row in current for field in row.get("data_quality", {}).get("missing_fields", [])})},
            "constraints": {"c_zone_overrides_attack": True, "automatic_trade": False, "future_data": False, "fund_migration_is_inference": True},
        }
        if persist:
            await self._persist(payload)
        return payload

    async def _persist(self, payload: dict[str, Any]) -> None:
        target = date.fromisoformat(payload["trade_date"])
        async with async_session() as session:
            regime = payload["market"]["regime"]
            old = (await session.execute(select(MarketRegimeState).where(MarketRegimeState.trade_date == target, MarketRegimeState.engine_version == MARKET_SECTOR_BRIDGE_VERSION))).scalar_one_or_none()
            values = {"regime": regime["regime"], "confidence": regime.get("confidence"), "evidence_json": regime.get("evidence") or [], "counter_evidence_json": regime.get("counter_evidence") or [], "strategy_bias_json": regime.get("strategy_bias") or {}, "data_quality_json": regime.get("data_quality") or {}}
            if old:
                for key, value in values.items(): setattr(old, key, value)
            else:
                session.add(MarketRegimeState(trade_date=target, engine_version=MARKET_SECTOR_BRIDGE_VERSION, **values))
            for row in payload.get("sectors") or []:
                existing = (await session.execute(select(SectorDailySnapshot).where(SectorDailySnapshot.trade_date == target, SectorDailySnapshot.sector_id == row.get("sector_id"), SectorDailySnapshot.sector_type == row.get("sector_type")))).scalar_one_or_none()
                fields = {"sector_name": row.get("sector_name") or row.get("sector_id") or "UNKNOWN", "rank": row.get("rank"), "pct_change": row.get("pct_change"), "relative_return_vs_market": row.get("relative_return_vs_market"), "turnover": row.get("turnover"), "turnover_share": row.get("turnover_share"), "main_force_net_inflow": row.get("main_force_net_inflow"), "main_force_inflow_ratio": row.get("main_force_inflow_ratio"), "fund_continuity": row.get("fund_continuity"), "breadth": row.get("breadth"), "limit_up_count": row.get("limit_up_count"), "limit_up_linkage": row.get("limit_up_linkage"), "core_strength": row.get("core_strength"), "raw_source_json": row.get("raw_source") or {}, "data_quality_json": row.get("data_quality") or {}}
                if existing:
                    for key, value in fields.items(): setattr(existing, key, value)
                else:
                    session.add(SectorDailySnapshot(trade_date=target, sector_id=row.get("sector_id") or "UNKNOWN", sector_type=row.get("sector_type") or "industry", **fields))
                life = row.get("lifecycle") or {}
                life_values = {"state": life.get("state") or "INVALID", "previous_state": life.get("previous_state"), "confidence": life.get("confidence"), "evidence_json": life.get("evidence") or [], "counter_evidence_json": life.get("counter_evidence") or [], "transition_reason_json": life.get("transition_reason") or {}, "data_quality_json": life.get("data_quality") or {}}
                life_row = (await session.execute(select(SectorLifecycleState).where(SectorLifecycleState.trade_date == target, SectorLifecycleState.sector_id == row.get("sector_id"), SectorLifecycleState.sector_type == row.get("sector_type"), SectorLifecycleState.engine_version == MARKET_SECTOR_BRIDGE_VERSION))).scalar_one_or_none()
                if life_row:
                    for key, value in life_values.items(): setattr(life_row, key, value)
                else:
                    session.add(SectorLifecycleState(trade_date=target, sector_id=row.get("sector_id") or "UNKNOWN", sector_type=row.get("sector_type") or "industry", engine_version=MARKET_SECTOR_BRIDGE_VERSION, **life_values))
            for path in (payload.get("migration", {}).get("paths") or []):
                source, target_row = path.get("source") or {}, path.get("target") or {}
                existing_path = (await session.execute(select(SectorMigrationInference).where(SectorMigrationInference.trade_date == target, SectorMigrationInference.source_sector_id == str(source.get("id") or "UNKNOWN"), SectorMigrationInference.target_sector_id == str(target_row.get("id") or "UNKNOWN"), SectorMigrationInference.engine_version == MARKET_SECTOR_BRIDGE_VERSION))).scalar_one_or_none()
                path_values = {"confidence": path.get("confidence"), "evidence_json": path.get("evidence") or [], "counter_evidence_json": path.get("counter_evidence") or [], "inference_type": path.get("inference_type") or "RELATIVE_STRENGTH_INFERENCE"}
                if existing_path:
                    for key, value in path_values.items(): setattr(existing_path, key, value)
                else:
                    session.add(SectorMigrationInference(trade_date=target, source_sector_id=str(source.get("id") or "UNKNOWN"), target_sector_id=str(target_row.get("id") or "UNKNOWN"), engine_version=MARKET_SECTOR_BRIDGE_VERSION, **path_values))
            for item in payload.get("opportunities") or []:
                pool = item.get("opportunity_pool") or "WATCH"
                opp_values = {"stock_name": item.get("stock_name"), "sector_id": item.get("sector_id") or "UNKNOWN", "sector_name": item.get("sector_name"), "market_regime": payload["market"]["regime"]["regime"], "sector_lifecycle": item.get("sector_lifecycle") or "INVALID", "zone": item.get("zone") or "UNKNOWN", "zone_stage": item.get("zone_stage") or "UNKNOWN", "priority": item.get("priority") or "WATCH", "main_force_state": item.get("main_force_state"), "volume_price_state": item.get("volume_price_state"), "ma_state": item.get("ma_state"), "big_pattern_state": item.get("big_pattern_state"), "rising_star_state": item.get("rising_star_state"), "three_books_consensus": item.get("three_books_consensus"), "risk_state": item.get("risk_state"), "evidence_json": item.get("evidence") or [], "missing_confirmation_json": item.get("missing_confirmation") or [], "counter_evidence_json": item.get("counter_evidence") or [], "invalidation_json": item.get("invalidation") or [], "snapshot_json": dict(item)}
                opp_row = (await session.execute(select(ABOppSnapshot).where(ABOppSnapshot.trade_date == target, ABOppSnapshot.symbol == item.get("symbol"), ABOppSnapshot.opportunity_pool == pool, ABOppSnapshot.engine_version == STRONG_STOCK_V21_VERSION))).scalar_one_or_none()
                if opp_row:
                    for key, value in opp_values.items(): setattr(opp_row, key, value)
                else:
                    session.add(ABOppSnapshot(trade_date=target, symbol=item.get("symbol") or "UNKNOWN", opportunity_pool=pool, engine_version=STRONG_STOCK_V21_VERSION, **opp_values))
            await session.commit()

    async def overview(self, requested: date | None = None, *, refresh: bool = False) -> dict[str, Any]:
        return await self.build(requested, persist=refresh)

    async def regime(self, requested: date | None = None) -> dict[str, Any]:
        payload = await self.build(requested)
        return {"trade_date": payload["trade_date"], "market": payload["market"], "data_quality": payload["data_quality"], "mode": "SHADOW"}

    async def lifecycle_view(self, requested: date | None = None) -> dict[str, Any]:
        payload = await self.build(requested)
        return {"trade_date": payload["trade_date"], "items": payload["lifecycle"], "sectors": payload["sectors"], "data_quality": payload["data_quality"]}

    async def trajectory(self, sector_id: str, requested: date | None = None, days: int = 20) -> dict[str, Any]:
        payload = await self.build(requested)
        trajectory = payload["sector_trajectories"].get(sector_id)
        if trajectory is None:
            return {"sector_id": sector_id, "status": "DATA_INCOMPLETE", "message": "没有找到该板块在指定日期前的缓存轨迹"}
        return {"sector_id": sector_id, "trade_date": payload["trade_date"], "trajectory": trajectory, "history": payload["sector_trajectories"].get(sector_id, {}).get("windows", [])[-max(1, min(days, 60)):], "data_quality": trajectory.get("data_quality")}

    async def migration_view(self, requested: date | None = None) -> dict[str, Any]:
        payload = await self.build(requested)
        return {"trade_date": payload["trade_date"], **payload["migration"]}

    async def opportunities(self, requested: date | None = None, pool: str | None = None) -> dict[str, Any]:
        payload = await self.build(requested)
        rows = payload["opportunities"]
        if pool:
            rows = [item for item in rows if item.get("opportunity_pool") == pool]
        return {"trade_date": payload["trade_date"], "market_regime": payload["market"]["regime"], "pool": pool or "ALL", "items": rows, "data_quality": payload["data_quality"], "mode": "SHADOW"}

    async def opportunity_detail(self, symbol: str, requested: date | None = None) -> dict[str, Any]:
        payload = await self.opportunities(requested)
        return next((item for item in payload["items"] if str(item.get("symbol")) == str(symbol)), {"symbol": symbol, "opportunity_pool": "DATA_INCOMPLETE", "evidence": [], "counter_evidence": ["没有找到该股票在指定日期前的V2.0 A/B/C快照"]})

    async def daily_review(self, requested: date | None = None) -> dict[str, Any]:
        payload = await self.build(requested, persist=True)
        async with async_session() as session:
            positions = list((await session.execute(select(PersonalPoolItem).where(PersonalPoolItem.asset_type == "stock").order_by(desc(PersonalPoolItem.updated_at)).limit(100))).scalars().all())
            events = list((await session.execute(select(RadarEvent).where(RadarEvent.last_updated_at <= datetime.combine(date.fromisoformat(payload["trade_date"]), datetime.max.time())).order_by(desc(RadarEvent.last_updated_at)).limit(8))).scalars().all())
        hold_review = [{"code": item.code, "name": item.name, "status": "NO_POSITION_DATA", "message": "个人池记录存在，但当前桥接层没有该持仓的成本/买入快照，不能臆测盈亏。"} for item in positions]
        focus = [item for item in payload["opportunities"] if item.get("priority") in {"P1", "WATCH"} and item.get("opportunity_pool") != "RISK_EXCLUDE"][:12]
        return {"module_id": STRONG_STOCK_V21_VERSION, "trade_date": payload["trade_date"], "mode": "SHADOW", "market": payload["market"], "main_contradiction": payload["market"]["regime"].get("strategy_bias", {}).get("text"), "sector_lifecycle": payload["lifecycle"][:30], "migration": payload["migration"], "opportunities": payload["opportunities"], "my_positions": hold_review, "tomorrow_map": {"focus_sectors": [item.get("sector_name") for item in payload["sectors"][:8]], "focus_candidates": focus, "risk_directions": [item for item in payload["opportunities"] if item.get("opportunity_pool") == "RISK_EXCLUDE"][:8], "confirmation": ["板块宽度继续扩大", "核心股和中军跟随", "归一化主力流入占比有可用样本"], "invalidation": ["市场状态转为防守退潮市", "核心股破坏且板块宽度收缩"]}, "events": [{"event_id": row.event_id, "title": row.canonical_title, "event_type": row.event_type, "direction": row.direction, "score": row.event_score, "source_time": row.last_updated_at.isoformat() if row.last_updated_at else None} for row in events], "learning": ["三书规则不自动修改", "今日盘面归因只在后续验证后进入经验层"], "data_quality": payload["data_quality"]}

    async def event_preheat(self, requested: date | None = None) -> dict[str, Any]:
        target = await self._target_date(requested)
        async with async_session() as session:
            rows = list((await session.execute(
                select(RadarEvent).where(RadarEvent.last_updated_at <= datetime.combine(target, datetime.max.time())).order_by(desc(RadarEvent.urgency_score), desc(RadarEvent.last_updated_at)).limit(30)
            )).scalars().all())
        return {"trade_date": target.isoformat(), "items": [{"event_id": row.event_id, "title": row.canonical_title, "event_type": row.event_type, "direction": row.direction, "urgency": row.urgency_score, "impact": row.impact_score, "status": "PREHEAT", "evidence": ["事件已在事件雷达规范化", "尚需板块宽度、资金和核心股确认"], "invalidation": ["事件影响未被市场价格与资金验证"]} for row in rows], "data_quality": {"status": "COMPLETE" if rows else "DATA_INCOMPLETE", "source": "radar_events", "cutoff": target.isoformat()}, "disclaimer": "事件预热只提出待验证方向，不把新闻标题直接当作交易信号。"}

    async def verification(self, requested: date | None = None) -> dict[str, Any]:
        target = await self._target_date(requested)
        async with async_session() as session:
            predictions = list((await session.execute(select(ABOppSnapshot).where(ABOppSnapshot.trade_date < target).order_by(desc(ABOppSnapshot.trade_date)).limit(500))).scalars().all())
            results = []
            for prediction in predictions:
                bars = list((await session.execute(select(StockDailyBar).where(StockDailyBar.stock_code == prediction.symbol, StockDailyBar.trade_date > prediction.trade_date, StockDailyBar.trade_date <= target).order_by(StockDailyBar.trade_date.asc()).limit(20))).scalars().all())
                for horizon in (1, 3, 5, 10, 20):
                    future = bars[horizon - 1] if len(bars) >= horizon else None
                    base = None
                    if future:
                        base_row = (await session.execute(select(StockDailyBar).where(StockDailyBar.stock_code == prediction.symbol, StockDailyBar.trade_date == prediction.trade_date).limit(1))).scalar_one_or_none()
                        base = _num(getattr(base_row, "close_price", None)) if base_row else None
                    value = (future.close_price / base - 1) * 100 if future and base not in (None, 0) else None
                    results.append({"prediction_id": prediction.id, "symbol": prediction.symbol, "trade_date": prediction.trade_date.isoformat(), "horizon": f"T+{horizon}", "return": round(value, 3) if value is not None else None, "result_state": "STILL_FORMING" if value is None else "SUCCESS" if value > 0 else "FAILED", "error_tags": [] if value is None or value > 0 else ["UNKNOWN"]})
        async with async_session() as session:
            for item in results:
                existing = (await session.execute(select(PredictionOutcome).where(PredictionOutcome.prediction_id == item["prediction_id"], PredictionOutcome.horizon == item["horizon"]))).scalar_one_or_none()
                values = {"return_value": item.get("return"), "result_state": item["result_state"], "invalidated": item["result_state"] == "FAILED", "error_tags_json": item.get("error_tags") or [], "evidence_json": [{"text": "验证只使用预测日之后且不晚于查询截止日的日线", "type": "OUT_OF_SAMPLE"}]}
                if existing:
                    for key, value in values.items(): setattr(existing, key, value)
                else:
                    session.add(PredictionOutcome(prediction_id=item["prediction_id"], horizon=item["horizon"], **values))
            await session.commit()
        return {"trade_date": target.isoformat(), "items": results, "data_quality": {"status": "PARTIAL" if results else "DATA_INCOMPLETE", "future_bars_are_capped_at_target": True}}

    async def generate_proposal(self) -> dict[str, Any]:
        async with async_session() as session:
            outcomes = list((await session.execute(select(PredictionOutcome).order_by(desc(PredictionOutcome.created_at)).limit(5000))).scalars().all())
            proposal = self.evolution_engine.propose([{"result_state": row.result_state} for row in outcomes])
            existing = (await session.execute(select(EvolutionProposal).where(EvolutionProposal.proposal_code == proposal["proposal_code"]))).scalar_one_or_none()
            if existing is None:
                existing = EvolutionProposal(proposal_code=proposal["proposal_code"], target_engine=proposal["target_engine"], current_rule_json=proposal["current_rule"], proposed_rule_json=proposal["proposed_rule"], sample_size=proposal["sample_size"], old_metrics_json=proposal["old_metrics"], new_shadow_metrics_json=proposal["new_shadow_metrics"], risk_notes_json=proposal["risk_notes"], status=proposal["status"], version=proposal["version"])
                session.add(existing)
            await session.commit()
            return {**proposal, "id": existing.id}

    async def proposals(self) -> dict[str, Any]:
        async with async_session() as session:
            rows = list((await session.execute(select(EvolutionProposal).order_by(desc(EvolutionProposal.created_at)).limit(100))).scalars().all())
        return {"version": EVOLUTION_VERSION, "status": "SHADOW", "items": [{"id": row.id, "proposal_code": row.proposal_code, "target_engine": row.target_engine, "sample_size": row.sample_size, "status": row.status, "current_rule": row.current_rule_json, "proposed_rule": row.proposed_rule_json, "old_metrics": row.old_metrics_json, "new_shadow_metrics": row.new_shadow_metrics_json, "risk_notes": row.risk_notes_json} for row in rows], "promotion_policy": "BOOK_RULE不可修改；ENGINE_FEATURE需人工审批并先进入SHADOW_NEW_VERSION；EMPIRICAL_LAYER自动积累。"}

    async def proposal_action(self, proposal_id: int, action: str) -> dict[str, Any]:
        allowed = {"approve": "SHADOW_NEW_VERSION", "reject": "REJECTED", "rollback": "ROLLED_BACK"}
        if action not in allowed:
            raise ValueError("只支持approve、reject、rollback")
        async with async_session() as session:
            row = await session.get(EvolutionProposal, proposal_id)
            if row is None:
                raise ValueError("提案不存在")
            if row.status not in {"WAITING_APPROVAL", "SHADOW_NEW_VERSION", "INSUFFICIENT_SAMPLE"}:
                raise ValueError(f"当前提案状态不可操作：{row.status}")
            row.status = allowed[action]
            row.approved_at = datetime.utcnow() if action == "approve" else row.approved_at
            await session.commit()
            return {"id": row.id, "status": row.status, "message": "仅更新提案状态，未修改任何BOOK_RULE或正式交易动作。"}


strong_stock_v21_service = StrongStockV21Service()