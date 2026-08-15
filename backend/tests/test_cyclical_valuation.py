import unittest

from services.cyclical_valuation import (
    build_cyclical_valuation,
    classify_cyclical_sector,
)
from services.fqe_engine import FundamentalQuantEngine
from services.stock_essence_decision import StockEssenceDecisionService
from services.stock_selection_agents import StockSelectionAgentService


def _fundamentals(*, current_profit=300.0, growth=8.0) -> dict:
    return {
        "earnings_state": "改善" if growth >= 0 else "恶化",
        "earnings_quality_score": 80,
        "metrics": {
            "net_profit_ttm": current_profit,
            "net_profit_growth_pct": growth,
            "gross_margin_pct": 32,
            "roe_pct": 18,
            "operating_cashflow_to_profit": 1.0,
        },
        "cycle_history": [
            {"period": "2023-12-31", "net_profit_ttm": 100, "gross_margin_pct": 16, "roe_pct": 7},
            {"period": "2024-12-31", "net_profit_ttm": 120, "gross_margin_pct": 20, "roe_pct": 9},
            {"period": "2025-12-31", "net_profit_ttm": 180, "gross_margin_pct": 25, "roe_pct": 13},
            {"period": "2026-06-30", "net_profit_ttm": current_profit, "gross_margin_pct": 32, "roe_pct": 18},
        ],
    }


class CyclicalValuationTests(unittest.TestCase):
    def test_classification_keeps_consumer_company_out_of_cycle_rules(self):
        self.assertTrue(classify_cyclical_sector("贵金属")["is_cyclical"])
        self.assertFalse(classify_cyclical_sector("食品饮料-白酒")["is_cyclical"])

    def test_low_pe_at_profit_peak_triggers_inversion_risk(self):
        result = build_cyclical_valuation(
            sector_names=["贵金属"],
            current_pe=8,
            current_pb=2.5,
            market_cap=2_400,
            fundamentals=_fundamentals(),
        )

        self.assertEqual(result["cycle_phase"], "peak")
        self.assertTrue(result["pe_inversion_risk"])
        self.assertGreater(result["normalized_pe"], 8)
        self.assertLessEqual(result["long_term_value_score"], 30)
        self.assertTrue(any("不按普通低估加分" in item for item in result["cycle_warnings"]))

    def test_negative_pe_at_cycle_bottom_remains_observable(self):
        fundamentals = _fundamentals(current_profit=-20, growth=12)
        fundamentals["cycle_history"] = [
            {"period": "2023-12-31", "net_profit_ttm": 120, "gross_margin_pct": 30, "roe_pct": 12},
            {"period": "2024-12-31", "net_profit_ttm": 50, "gross_margin_pct": 20, "roe_pct": 5},
            {"period": "2025-12-31", "net_profit_ttm": -40, "gross_margin_pct": 8, "roe_pct": -3},
            {"period": "2026-06-30", "net_profit_ttm": -20, "gross_margin_pct": 10, "roe_pct": -1},
        ]
        result = build_cyclical_valuation(
            sector_names=["养殖"],
            current_pe=-30,
            current_pb=1.1,
            market_cap=600,
            fundamentals=fundamentals,
        )

        self.assertIn(result["cycle_phase"], {"recovery", "trough"})
        self.assertFalse(result["pe_inversion_risk"])
        self.assertIn("周期", result["valuation_method"])
        self.assertTrue(any("PE失真" in item for item in result["cycle_warnings"]))

    def test_stock_profile_does_not_label_peak_cycle_stock_as_ordinary_undervalued(self):
        valuation = StockEssenceDecisionService._build_valuation(
            {"pe": 8, "pb": 2.5, "market_cap": 2_400},
            {
                "history": [
                    {"date": "2026-08-12", "pe_ttm": 15},
                    {"date": "2026-08-13", "pe_ttm": 12},
                    {"date": "2026-08-14", "pe_ttm": 8},
                ],
                "history_end": "2026-08-14",
                "source": "test",
            },
            [{"pe": 10}, {"pe": 20}],
            _fundamentals(),
            {"_coverage_status": "no_analyst_coverage"},
            ("贵金属",),
        )

        self.assertTrue(valuation["pe_inversion_risk"])
        self.assertNotIn("低估 + 盈利改善", valuation["state"])
        risk = StockEssenceDecisionService._build_risk_reward(
            [], {"price": 10}, valuation, {"level": "中性"},
        )
        self.assertEqual(risk["valuation_risk"], "高")

    def test_fqe_does_not_reward_unverified_low_pe_cycle_stock(self):
        stock = {
            "code": "601069", "name": "西部黄金", "industry": "贵金属",
            "market_cap": 100, "pe": 8, "roe": 18,
            "ocf_to_profit_ttm": 1.0, "debt_ratio": 35,
            "deducted_profit_growth": 30, "list_days": 2000,
            "pe_percentile_3y": 10,
        }
        pragmatic = FundamentalQuantEngine.run_retail([stock], 10, "pragmatic")
        strict = FundamentalQuantEngine.run_retail([stock], 10, "strict")

        self.assertEqual(strict["count"], 0)
        self.assertEqual(pragmatic["count"], 1)
        self.assertLessEqual(pragmatic["holdings"][0]["score"], 0.7)
        self.assertTrue(any("周期股缺少标准化盈利" in item for item in pragmatic["warnings"]))

    def test_selection_agent_uses_normalized_pe_for_cycle_stock(self):
        result = StockSelectionAgentService()._fundamental_agent({
            "code": "601069", "name": "西部黄金", "sector": "贵金属",
            "pe": 8, "pb": 2.5, "roe": 18, "gross_margin": 30,
            "revenue_growth": 15, "deducted_profit_growth": 25,
            "ocf_to_profit": 1.0, "debt_ratio": 35, "receivable_to_revenue": 10,
            "cycle_phase": "peak", "normalized_pe": 24, "pe_inversion_risk": True,
            "_feature_meta": {"financial": {"status": "available"}},
        })

        self.assertTrue(result["metrics"]["is_cyclical"])
        self.assertTrue(result["metrics"]["pe_inversion_risk"])
        self.assertTrue(any("PE反向风险" in item for item in result["risks"]))


if __name__ == "__main__":
    unittest.main()
