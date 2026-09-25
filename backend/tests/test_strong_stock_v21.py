import unittest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import IndustryFundFlowDaily, MainForceState, MarketBoard, MarketDataCache, StockDailyBar, StockUniverseSnapshot, ThemeState, ThreeBooksConsensus, TradingZoneGeometry
from services.strong_stock_v21 import StrongStockV21Service

from strong_stock_decision.v21_engine import (
    EvolutionEngine,
    MarketRegimeEngine,
    SectorLifecycleEngine,
    SectorMigrationEngine,
    SectorTrajectoryEngine,
    ZoneOpportunityFusionEngine,
)
from strong_stock_decision.book_evidence import summarize_hunter_evidence
from strong_stock_decision.candidate_review import summarize_candidate_review
from strong_stock_decision.v2_engine import build_v2


def sector_history(count=6, *, improving=True):
    rows = []
    for index in range(count):
        rows.append({
            "trade_date": date(2026, 8, 1) + timedelta(days=index),
            "rank": 20 - index * 2 if improving else 5 + index * 2,
            "pct_change": 1.2 if improving else -1.2,
            "relative_return_vs_market": 0.8 if improving else -0.8,
            "main_force_inflow_ratio": 0.02 if improving else -0.02,
            "breadth": 0.62 if improving else 0.28,
            "turnover_share": 0.12,
        })
    return rows


class StrongStockV21EngineTests(unittest.TestCase):
    def test_book_risk_review_overrides_attack_pool(self):
        result = ZoneOpportunityFusionEngine().fuse([{"symbol": "000001", "zone": "强势A区", "zone_stage": "A_ACTIVE", "sector_id": "s1", "book_review": {"status": "RISK", "risk_priority": True}}], "TREND_ATTACK", {"s1": {"state": "ACCELERATING"}})
        self.assertEqual(result[0]["opportunity_pool"], "RISK_EXCLUDE")

    def test_unverified_book_review_cannot_reach_p1_or_p2(self):
        result = ZoneOpportunityFusionEngine().fuse([{"symbol": "000001", "zone": "强势A区", "zone_stage": "A_ACTIVE", "sector_id": "s1", "book_review": {"status": "UNVERIFIED", "risk_priority": False}}], "TREND_ATTACK", {"s1": {"state": "ACCELERATING"}})
        self.assertEqual(result[0]["priority"], "WATCH")

    def test_peak_observation_is_not_positive_pattern(self):
        v2 = {"trade_date": "2026-08-28", "zones": {"zone": "强势A区"}, "data_quality": {"price_basis": "OHLC"}, "sell": {}, "signals": [{"skill_id": "BXDT_PEAK_001", "status": "CONFIRMED", "name": "历史高点"}]}
        evidence = {"data_quality": {"point_in_time": True}}
        review = summarize_candidate_review(v2, evidence, decision_date="2026-08-28", selection_date="2026-08-28", bar_count=60)
        self.assertEqual(review["big_pattern"]["signals"], [])
        self.assertTrue(review["big_pattern"]["risk"])

    def test_not_found_peak_and_top_signals_do_not_create_book_risk(self):
        v2 = {"trade_date": "2026-08-28", "zones": {"zone": "强势A区"}, "data_quality": {"price_basis": "OHLC"}, "sell": {}, "signals": [{"skill_id": "BXDT_PEAK_001", "status": "NOT_FOUND"}, {"skill_id": "BXZX_CLASSIC_TOP_001", "status": "NOT_FOUND"}]}
        review = summarize_candidate_review(v2, {"data_quality": {"point_in_time": True}}, decision_date="2026-08-28", selection_date="2026-08-28", bar_count=60)
        self.assertFalse(review["risk_priority"])
        self.assertNotEqual(review["status"], "RISK")

    def test_candidate_review_needs_point_in_time_ohlc(self):
        v2 = {"trade_date": "2026-08-28", "zones": {"zone": "强势A区"}, "data_quality": {"price_basis": "CLOSE_PROXY"}, "sell": {}, "signals": []}
        review = summarize_candidate_review(v2, {"data_quality": {"point_in_time": False}}, decision_date="2026-08-28", selection_date="2026-08-28", bar_count=60)
        self.assertEqual(review["status"], "UNVERIFIED")

    def test_same_day_book_review_is_research_only(self):
        bars = [{"trade_date": date(2026, 8, 28), "open": 9.9, "close": 10, "high": 10.1, "low": 9.8, "volume": 100, "change_pct": 1}] * 60
        v2 = build_v2({"symbol": "000001", "bars": bars})
        review = summarize_candidate_review(v2, summarize_hunter_evidence(context={"bars": bars, "as_of": "2026-08-28"}), decision_date="2026-08-28", selection_date="2026-08-28", bar_count=60)
        self.assertIn(review["status"], {"STRUCTURE_CANDIDATE", "INITIAL_WATCH", "RISK"})
        self.assertNotEqual(review["three_books"], "BOOK_CONFIRMED")
    def test_regime_has_four_state_contract_and_evidence(self):
        result = MarketRegimeEngine().evaluate({"up_count": 3800, "down_count": 1000, "turnover_activity": 1.12, "index_trend_5d": 2.0, "index_above_ma20": True, "failed_limit_rate": .1, "limit_down_count": 3, "top10_overlap_1d": .7, "core_strength": 75})
        self.assertEqual(result["regime"], "TREND_ATTACK")
        self.assertTrue(result["evidence"])
        self.assertIn("counter_evidence", result)

    def test_insufficient_market_facts_do_not_force_classification(self):
        self.assertEqual(MarketRegimeEngine().evaluate({})["regime"], "TRANSITION")

    def test_single_day_spike_does_not_start_lifecycle(self):
        rows = sector_history(3)
        rows[0]["rank"], rows[1]["rank"], rows[2]["rank"] = 20, 19, 18
        rows[0]["main_force_inflow_ratio"] = rows[1]["main_force_inflow_ratio"] = None
        self.assertNotEqual(SectorLifecycleEngine().evaluate(rows)["state"], "STARTING")

    def test_trajectory_contains_multi_window_baselines(self):
        result = SectorTrajectoryEngine().build(sector_history())
        self.assertEqual([item["window"] for item in result["windows"]], ["1D", "3D", "5D", "10D", "20D"])

    def test_risk_c_zone_overrides_attack(self):
        result = ZoneOpportunityFusionEngine().fuse([{"symbol": "000001", "zone": "风险C区", "zone_stage": "C_DEEPENING", "sector_id": "s1"}], "TREND_ATTACK", {"s1": {"state": "ACCELERATING"}})
        self.assertEqual(result[0]["priority"], "EXCLUDE")
        self.assertEqual(result[0]["opportunity_pool"], "RISK_EXCLUDE")

    def test_starting_a_forming_is_primary_in_attack_market(self):
        result = ZoneOpportunityFusionEngine().fuse(
            [{"symbol": "000001", "zone": "强势A区", "zone_stage": "A_FORMING", "sector_id": "s1"}],
            "TREND_ATTACK",
            {"s1": {"state": "STARTING"}},
        )
        self.assertEqual(result[0]["opportunity_pool"], "A_DISCOVERY")
        self.assertEqual(result[0]["priority"], "P1")
        self.assertTrue(result[0]["next_confirmation"])

    def test_defensive_market_and_weak_main_force_downgrade_confirmation(self):
        result = ZoneOpportunityFusionEngine().fuse(
            [{"symbol": "000001", "zone": "强势A区", "zone_stage": "A_ACTIVE", "sector_id": "s1", "main_force_state": "持续流出"}],
            "DEFENSIVE_FADE",
            {"s1": {"state": "ACCELERATING"}},
        )
        self.assertEqual(result[0]["opportunity_pool"], "A_CONFIRM")
        self.assertEqual(result[0]["priority"], "P2")
        self.assertIn("主力状态转弱", "".join(result[0]["counter_evidence"]))

    def test_invalid_zone_is_always_excluded(self):
        result = ZoneOpportunityFusionEngine().fuse(
            [{"symbol": "000001", "zone": "强势A区", "zone_stage": "A_INVALID", "sector_id": "s1"}],
            "TREND_ATTACK",
            {"s1": {"state": "STARTING"}},
        )
        self.assertEqual(result[0]["priority"], "EXCLUDE")

    def test_migration_is_explicitly_inferred(self):
        result = SectorMigrationEngine().infer([{ "sector_id": "a", "sector_name": "旧主线", "rank": 20, "relative_return_vs_market": -1, "main_force_inflow_ratio": -.01 }, {"sector_id": "b", "sector_name": "新方向", "rank": 2, "relative_return_vs_market": 1, "main_force_inflow_ratio": .01, "breadth": .6}])
        self.assertTrue(result["paths"])
        self.assertIn("账户迁移", result["description"])

    def test_evolution_requires_minimum_samples(self):
        result = EvolutionEngine().propose([{"result_state": "SUCCESS"}] * 10)
        self.assertEqual(result["status"], "INSUFFICIENT_SAMPLE")


class StrongStockV21ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_patch = patch("services.strong_stock_v21.async_session", self.session_factory)
        self.session_patch.start()

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()

    async def test_candidate_uses_point_in_time_industry_as_primary_sector(self):
        target = date(2026, 8, 28)
        async with self.session_factory() as session:
            session.add_all([
                MarketBoard(board_type="industry", code="BK0475", name="煤炭行业"),
                StockUniverseSnapshot(
                    stock_code="600188", stock_name="兖矿能源", exchange="SH",
                    trade_date=target, industry="煤炭行业",
                ),
                StockDailyBar(
                    stock_code="600188", stock_name="兖矿能源", trade_date=target - timedelta(days=1),
                    close_price=12.3, change_pct=2.1,
                ),
                MainForceState(symbol="600188", trade_time=datetime.combine(target - timedelta(days=1), datetime.min.time()), main_force_direction="流出"),
                ThreeBooksConsensus(symbol="600188", trade_time=datetime.combine(target - timedelta(days=1), datetime.min.time()), consensus_level="严重冲突"),
                ThemeState(symbol="600188", trade_time=datetime.combine(target - timedelta(days=1), datetime.min.time()), theme_name="旧主题", theme_type="旧"),
                TradingZoneGeometry(
                    symbol="600188", trade_time=datetime.combine(target - timedelta(days=1), datetime.min.time()),
                    zone="强势A区", zone_stage="A_FORMING",
                ),
                TradingZoneGeometry(
                    symbol="000001", trade_time=datetime.combine(target, datetime.min.time()),
                    zone="强势B区", zone_stage="B_ACTIVE",
                ),
                ThemeState(
                    symbol="600188", trade_time=datetime.combine(target, datetime.min.time()),
                    theme_name="高股息", theme_type="事件题材",
                ),
            ])
            await session.commit()

        with patch(
            "services.strong_stock_v21.collector.fetch_intelligent_selection_candidates",
            new=AsyncMock(return_value={
                "scan_total": 5200,
                "total": 2,
                "stocks": [
                    {"code": "600188", "name": "兖矿能源", "sector": "煤炭行业", "price": 12.3},
                ],
                "source": "numcat",
                "data_date": target.isoformat(),
                "is_realtime": False,
            }),
        ):
            rows, metadata = await StrongStockV21Service()._candidate_rows(
                target,
                current_scan=True,
                exclude_star_market=True,
                exclude_gem=True,
                refresh=True,
            )

        self.assertEqual(rows[0]["sector_id"], "BK0475")
        self.assertEqual(rows[0]["sector_name"], "煤炭行业")
        self.assertEqual(rows[0]["zone"], "UNKNOWN")
        self.assertEqual(rows[0]["zone_source_date"], (target - timedelta(days=1)).isoformat())
        self.assertIsNone(rows[0]["close_price"])
        self.assertIsNone(rows[0]["main_force_state"])
        self.assertIsNone(rows[0]["three_books_consensus"])
        self.assertEqual(rows[0]["theme_name"], "高股息")
        self.assertEqual(rows[0]["theme_name"], "高股息")
        self.assertEqual([row["symbol"] for row in rows], ["600188"])
        self.assertEqual(metadata["total_scanned"], 5200)
        self.assertEqual(metadata["source_label"], "全市场系统扫描")

    async def test_book_review_is_bounded_and_marks_stale_selection(self):
        target = date(2026, 8, 28)
        async with self.session_factory() as session:
            session.add_all([StockDailyBar(stock_code="600188", trade_date=target - timedelta(days=day), open_price=10, close_price=10.1, high_price=10.2, low_price=9.9, volume=100, change_pct=1) for day in range(60)])
            await session.commit()
        rows = [{"symbol": "600188", "stock_name": "测试", "zone": "UNKNOWN", "zone_stage": "UNKNOWN", "system_selection": {"run_date": "2026-08-27"}}]
        metadata = {"data_date": "2026-08-27"}
        reviewed = await StrongStockV21Service()._attach_book_reviews(rows, target, metadata)
        self.assertEqual(reviewed, 0)
        self.assertEqual(rows[0]["book_review"]["status"], "UNVERIFIED")
        self.assertEqual(metadata["data_quality"]["status"], "STALE")

    async def test_overview_snapshot_cache_hit_refresh_filter_and_expiry(self):
        target = date(2026, 8, 28)
        payload = {"trade_date": target.isoformat(), "market": {}, "opportunities": [], "data_quality": {"status": "PARTIAL"}}
        service = StrongStockV21Service()
        with patch.object(service, "build", new=AsyncMock(return_value=payload)) as build_mock:
            first = await service.overview(target)
            second = await service.overview(target)
            refreshed = await service.overview(target, refresh=True)
            filtered = await service.overview(target, exclude_gem=False)
        self.assertFalse(first["cache_used"])
        self.assertTrue(second["cache_used"])
        self.assertFalse(refreshed["cache_used"])
        self.assertFalse(filtered["cache_used"])
        self.assertEqual(build_mock.await_count, 3)
        async with self.session_factory() as session:
            row = await session.get(MarketDataCache, service._overview_cache_key(target, True, True))
            row.updated_at = datetime.utcnow() - timedelta(hours=2)
            await session.commit()
        with patch.object(service, "build", new=AsyncMock(return_value=payload)) as build_again:
            expired = await service.overview(target)
        self.assertFalse(expired["cache_used"])
        self.assertEqual(build_again.await_count, 1)

    async def test_complete_snapshot_keeps_normal_ttl(self):
        target = date(2026, 8, 28)
        payload = {"trade_date": target.isoformat(), "market": {}, "opportunities": [], "data_quality": {"status": "COMPLETE", "candidate_scan": {"status": "COMPLETE"}}}
        service = StrongStockV21Service()
        with patch.object(service, "build", new=AsyncMock(return_value=payload)) as build_mock:
            await service.overview(target)
            await service.overview(target)
        self.assertEqual(build_mock.await_count, 1)

    async def test_overview_preserves_none_vs_historical_build_semantics(self):
        target = date(2026, 8, 28)
        payload = {"trade_date": target.isoformat(), "market": {}, "opportunities": [], "data_quality": {"status": "COMPLETE", "candidate_scan": {"status": "COMPLETE"}}}
        service = StrongStockV21Service()
        with patch.object(service, "_target_date", new=AsyncMock(return_value=target)), patch.object(service, "build", new=AsyncMock(return_value=payload)) as build_mock:
            await service.overview(None, refresh=True)
            await service.overview(target, refresh=True)
        self.assertIsNone(build_mock.await_args_list[0].args[0])
        self.assertEqual(build_mock.await_args_list[1].args[0], target)

    async def test_overview_cache_separates_current_and_historical_modes(self):
        target = date(2026, 8, 28)
        payload = {"trade_date": target.isoformat(), "market": {}, "opportunities": [], "data_quality": {"status": "COMPLETE", "candidate_scan": {"status": "COMPLETE"}}}
        service = StrongStockV21Service()
        with patch.object(service, "_target_date", new=AsyncMock(return_value=target)), patch.object(service, "build", new=AsyncMock(return_value=payload)) as build_mock:
            await service.overview(None, refresh=True)
            await service.overview(target, refresh=False)
        self.assertEqual(build_mock.await_count, 2)

    async def test_build_failure_returns_expired_same_day_snapshot_as_stale(self):
        target = date(2026, 8, 28)
        payload = {"trade_date": target.isoformat(), "market": {}, "opportunities": [], "data_quality": {"status": "COMPLETE", "candidate_scan": {"status": "COMPLETE"}}}
        service = StrongStockV21Service()
        with patch.object(service, "build", new=AsyncMock(return_value=payload)):
            await service.overview(target)
        async with self.session_factory() as session:
            row = await session.get(MarketDataCache, service._overview_cache_key(target, True, True))
            row.updated_at = datetime.utcnow() - timedelta(days=2)
            await session.commit()
        with patch.object(service, "build", new=AsyncMock(side_effect=RuntimeError("upstream down"))):
            stale = await service.overview(target, refresh=True)
        self.assertTrue(stale["cache_used"])
        self.assertTrue(stale["cache_stale"])
        self.assertEqual(stale["data_quality"]["status"], "STALE")
        self.assertEqual(stale["data_quality"]["cache_fallback_error"], "RuntimeError")

    async def test_sector_rows_keeps_latest_window_and_returns_ascending_history(self):
        target = date(2026, 8, 28)
        async with self.session_factory() as session:
            session.add_all([IndustryFundFlowDaily(board_code="BK1", trade_date=target - timedelta(days=2), main_net_inflow=1), IndustryFundFlowDaily(board_code="BK1", trade_date=target, main_net_inflow=2)])
            session.add(MarketBoard(board_type="industry", code="BK1", name="测试行业"))
            await session.commit()
        history, latest = await StrongStockV21Service()._sector_rows(target, "industry")
        self.assertEqual([row["trade_date"] for row in history["BK1"]], [(target - timedelta(days=2)).isoformat(), target.isoformat()])
        self.assertEqual(latest[0]["trade_date"], target.isoformat())

    async def test_historical_scan_metadata_never_fabricates_target_date(self):
        target = date(2026, 8, 28)
        service = StrongStockV21Service()
        with patch.object(service, "_load_system_selection_candidates", new=AsyncMock(return_value=[])):
            rows, metadata = await service._candidate_rows(target, current_scan=False, exclude_star_market=True, exclude_gem=True, refresh=False)
        self.assertEqual(rows, [])
        self.assertIsNone(metadata["data_date"])

    async def test_historical_scan_preserves_run_date_when_all_rows_filtered(self):
        target = date(2026, 8, 28)
        service = StrongStockV21Service()
        source = [{"symbol": "688001", "system_selection": {"run_date": "2026-08-27"}}]
        with patch.object(service, "_load_system_selection_candidates", new=AsyncMock(return_value=source)):
            rows, metadata = await service._candidate_rows(target, current_scan=False, exclude_star_market=True, exclude_gem=True, refresh=False)
        self.assertEqual(rows, [])
        self.assertEqual(metadata["data_date"], "2026-08-27")


if __name__ == "__main__":
    unittest.main()
