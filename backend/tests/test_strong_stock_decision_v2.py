import json
import unittest
from datetime import date, timedelta

from strong_stock_decision.registry import V2_BOOK_SKILL_DEFINITIONS
from strong_stock_decision.v2_engine import build_v2


def bars_for(count: int = 120, *, sparse: bool = False, falling: bool = False):
    rows = []
    previous = 20.0
    for index in range(count):
        close = 20.0 + index * 0.08 if not falling else 32.0 - index * 0.08
        change = (close / previous - 1) * 100 if index else 0
        rows.append({
            "trade_date": date(2025, 1, 1) + timedelta(days=index),
            "open": None if sparse else close - 0.04,
            "close": close,
            "high": None if sparse else close + 0.12,
            "low": None if sparse else close - 0.12,
            "volume": 100000 + index * 700,
            "amount": 1000000 + index * 8000,
            "change_pct": change,
        })
        previous = close
    return rows


class StrongStockV2EngineTests(unittest.TestCase):
    def build(self, bars=None, **extra):
        return build_v2({"symbol": "000001", "name": "测试", "bars": bars or bars_for(), "sector": None, "sector_flow": [], "source_status": {}, **extra})

    def test_registry_is_canonical_and_complete(self):
        ids = {item["skill_id"] for item in V2_BOOK_SKILL_DEFINITIONS}
        for skill_id in ("HQS_RISK_001", "HQS_MAIN_005", "HQS_WASH_003", "HQS_GAP_004", "BXDT_MA_005", "BXDT_BOTTOM_009", "BXDT_3D_003", "BXZX_010", "BXZX_CLASSIC_TOP_004"):
            self.assertIn(skill_id, ids)

    def test_every_registered_skill_is_present_in_output(self):
        result = self.build()
        returned = {item["skill_id"] for item in result["signals"]}
        self.assertEqual(returned, {item["skill_id"] for item in V2_BOOK_SKILL_DEFINITIONS})
        self.assertEqual(result["mode"], "SHADOW")
        self.assertEqual(result["empirical_layer"]["action_impact"], "DISABLED_UNTIL_VALIDATED")

    def test_sparse_prices_disclose_close_proxy(self):
        result = self.build(bars_for(sparse=True))
        self.assertEqual(result["data_quality"]["price_basis"], "CLOSE_PROXY")
        self.assertTrue(any(item.get("feature") == "price_basis" for item in result["risk"]["signals"][0]["evidence"]))

    def test_risk_conflict_is_explicit_and_risk_dominant(self):
        result = self.build(bars_for(falling=True))
        self.assertIn(result["zones"]["zone"], {"风险C区", "未形成明确交易区"})
        if result["zones"]["zone"] == "风险C区":
            self.assertEqual(result["consensus"]["dominant_side"], "RISK")

    def test_output_is_json_serializable(self):
        result = self.build(sector={"name": "测试行业", "change_pct": 1.5})
        json.dumps(result, ensure_ascii=False)

    def test_explanation_includes_structured_alternatives(self):
        result = self.build()
        why_not = result["explanation"].get("why_not")
        self.assertIsInstance(why_not, list)
        self.assertGreaterEqual(len(why_not), 3)
        self.assertTrue(any("攻击星线" in item for item in why_not))
        self.assertTrue(any("经典顶部" in item for item in why_not))

    def test_rule_config_is_a_read_only_shadow_catalog(self):
        from strong_stock_decision.service import strong_stock_decision_service

        result = strong_stock_decision_service.rule_config()
        self.assertEqual(result["status"], "SHADOW_ONLY")
        self.assertFalse(result["runtime_applied"])
        self.assertFalse(result["editable"])
        self.assertTrue(result["configs"])
        required = {
            "feature_name", "default_value", "min_value", "max_value",
            "market_regime", "market_cap_bucket", "timeframe", "source", "version",
        }
        self.assertTrue(required.issubset(result["configs"][0]))

    def test_v2_flag_is_independent_and_has_explicit_disabled_envelope(self):
        from config import settings
        from strong_stock_decision.service import strong_stock_decision_service

        original = getattr(settings, "feature_strong_stock_decision_v2", True)
        try:
            settings.feature_strong_stock_decision_v2 = False
            self.assertFalse(strong_stock_decision_service._v2_enabled())
            self.assertEqual(strong_stock_decision_service._v2_disabled_envelope()["status"], "DISABLED")
        finally:
            settings.feature_strong_stock_decision_v2 = original


if __name__ == "__main__":
    unittest.main()
