import unittest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import MarketBoard, StockDailyBar, StockUniverseSnapshot, ThemeState, TradingZoneGeometry
from services.strong_stock_v21 import StrongStockV21Service

from strong_stock_decision.v21_engine import (
    EvolutionEngine,
    MarketRegimeEngine,
    SectorLifecycleEngine,
    SectorMigrationEngine,
    SectorTrajectoryEngine,
    ZoneOpportunityFusionEngine,
)


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
                    stock_code="600188", stock_name="兖矿能源", trade_date=target,
                    close_price=12.3, change_pct=2.1,
                ),
                TradingZoneGeometry(
                    symbol="600188", trade_time=datetime.combine(target, datetime.min.time()),
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
        self.assertEqual(rows[0]["theme_name"], "高股息")
        self.assertEqual([row["symbol"] for row in rows], ["600188"])
        self.assertEqual(metadata["total_scanned"], 5200)
        self.assertEqual(metadata["source_label"], "全市场系统扫描")


if __name__ == "__main__":
    unittest.main()
