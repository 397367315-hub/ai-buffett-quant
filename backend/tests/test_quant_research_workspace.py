import unittest

from services.quant_research_workspace import (
    FACTOR_CATALOG,
    quant_research_workspace,
)


def _dsl() -> dict:
    return {
        "strategy_id": "research_demo",
        "name": "研究演示策略",
        "family": "weekly",
        "version": "1.0.0",
        "universe": {"market": "A_SHARE"},
        "entry": {"all": [{"factor": "momentum_20d", "operator": ">", "value": 0.0}]},
        "exit": {"stop_loss_pct": 5.0, "force_exit_time": "14:50"},
        "portfolio": {"max_single_weight": 0.2},
        "cost_model": {"commission": "research_protocol"},
    }


class QuantResearchWorkspaceTests(unittest.TestCase):
    def test_factor_catalog_is_complete_and_registered(self):
        self.assertEqual(len(FACTOR_CATALOG), 49)
        self.assertEqual(len({item["id"] for item in FACTOR_CATALOG}), 49)
        self.assertTrue(all(item["registered"] for item in FACTOR_CATALOG))
        factor_ids = {item["id"] for item in FACTOR_CATALOG}
        self.assertTrue({
            "market_regime_score", "sector_leadership_score", "crowd_extreme_score",
            "supply_exhaustion_score", "breakout_confirmation_score",
        }.issubset(factor_ids))

    def test_dsl_accepts_registered_factor_and_rejects_unknown_or_code(self):
        valid = quant_research_workspace.validate_dsl(_dsl())
        self.assertTrue(valid["valid"])
        self.assertEqual(valid["factor_ids"], ["momentum_20d"])

        unknown = _dsl()
        unknown["entry"]["all"][0]["factor"] = "not_registered"
        unknown_result = quant_research_workspace.validate_dsl(unknown)
        self.assertFalse(unknown_result["valid"])
        self.assertTrue(any("未注册因子" in message for message in unknown_result["errors"]))

        unsafe = _dsl()
        unsafe["entry"]["python"] = "__import__('os').system('whoami')"
        unsafe_result = quant_research_workspace.validate_dsl(unsafe)
        self.assertFalse(unsafe_result["valid"])
        self.assertTrue(any("禁止" in message for message in unsafe_result["errors"]))

    def test_dsl_hash_is_stable_for_same_definition(self):
        first = quant_research_workspace.validate_dsl(_dsl())
        second = quant_research_workspace.validate_dsl(_dsl())
        self.assertEqual(first["dsl_hash"], second["dsl_hash"])

    def test_partitions_are_chronological_and_drawdown_is_calculated(self):
        values = [5.0, -3.0, -2.0, 4.0, -1.0, 2.0]
        rows = [
            {
                "date": f"2026-01-{index:02d}",
                "exit_date": f"2026-01-{index + 1:02d}",
                "avg_net_return_pct": value,
            }
            for index, value in enumerate(values, 1)
        ]
        partitions = quant_research_workspace._partition_metrics({"_daily_results_internal": rows})
        self.assertEqual(
            sum(item["trading_periods"] for item in partitions.values()),
            len(rows),
        )
        self.assertLessEqual(partitions["train"]["to"], partitions["validation"]["from"])
        self.assertLessEqual(partitions["validation"]["to"], partitions["out_of_sample"]["from"])
        self.assertGreater(partitions["train"]["max_drawdown"], 0)

    def test_cost_stress_uses_recorded_cost_components(self):
        rows = [{
            "avg_gross_return_pct": 3.0,
            "commission_cost_pct": 0.05,
            "stamp_tax_cost_pct": 0.10,
            "slippage_cost_pct": 0.40,
            "impact_cost_pct": 0.10,
        }]
        base = quant_research_workspace._stress_result(rows)
        stressed = quant_research_workspace._stress_result(rows, cost_multiplier=1.5, slippage_multiplier=1.5)
        self.assertTrue(base["available"])
        self.assertTrue(stressed["available"])
        self.assertLess(stressed["total_return"], base["total_return"])


if __name__ == "__main__":
    unittest.main()
