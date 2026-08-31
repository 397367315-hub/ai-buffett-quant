import unittest
from datetime import date, timedelta

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

    def test_migration_is_explicitly_inferred(self):
        result = SectorMigrationEngine().infer([{ "sector_id": "a", "sector_name": "旧主线", "rank": 20, "relative_return_vs_market": -1, "main_force_inflow_ratio": -.01 }, {"sector_id": "b", "sector_name": "新方向", "rank": 2, "relative_return_vs_market": 1, "main_force_inflow_ratio": .01, "breadth": .6}])
        self.assertTrue(result["paths"])
        self.assertIn("账户迁移", result["description"])

    def test_evolution_requires_minimum_samples(self):
        result = EvolutionEngine().propose([{"result_state": "SUCCESS"}] * 10)
        self.assertEqual(result["status"], "INSUFFICIENT_SAMPLE")


if __name__ == "__main__":
    unittest.main()
