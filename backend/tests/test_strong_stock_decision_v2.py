import json
import unittest
from datetime import date, timedelta

from strong_stock_decision.registry import V2_BOOK_SKILL_DEFINITIONS
from strong_stock_decision.v2_engine import (
    _buy_point,
    _normalise_bars,
    _pattern_data,
    _sell,
    _series_features,
    build_v2,
)


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

    def test_risk_c_without_active_shape_blocks_general_buy_point(self):
        result = _buy_point(
            {},
            {"zone": "风险C区"},
            {"direction": "偏多"},
            [],
            [],
            {"overall_score": 72},
        )
        self.assertEqual(result["legacy_level"], "一般买点")
        self.assertEqual(result["level"], "仅研究观察")
        self.assertEqual(result["effective_buy_permission"], "BLOCK")
        self.assertIn("未形成明确主动形态", result["reason"])
        self.assertFalse(any(item["status"] == "CONFIRMED" for item in result["levels"]))

    def test_risk_c_with_active_shape_still_blocks_positive_buy_point(self):
        result = _buy_point(
            {},
            {"zone": "风险C区"},
            {"direction": "偏多"},
            [{"skill_id": "BXZX_009", "status": "CONFIRMED"}],
            [],
            {"overall_score": 80},
        )
        self.assertEqual(result["legacy_level"], "臆想买点")
        self.assertEqual(result["level"], "仅研究观察")
        self.assertEqual(result["effective_buy_permission"], "BLOCK")
        self.assertIn("存在主动形态研究信号", result["reason"])
        self.assertEqual(result["matched_skills"], ["BXZX_009"])

    def test_v1_zone_is_the_single_canonical_conclusion(self):
        result = build_v2(
            {"symbol": "000001", "name": "测试", "bars": bars_for(falling=True)},
            legacy={
                "module_id": "STRONG_STOCK_DECISION_V1",
                "decision": {"action": "WATCH"},
                "best_trading_zone": {"zone": "强势B区", "reasons": ["V1状态机结论"]},
            },
        )
        self.assertEqual(result["zones"]["zone"], "强势B区")
        self.assertEqual(result["zone_comparison"]["canonical_source"], "V1统一交易区状态机")
        self.assertTrue(result["zone_comparison"]["different"])
        zone_signals = {item["skill_id"]: item for item in result["zones"]["signals"]}
        self.assertEqual(zone_signals["HQS_009"]["status"], "CONFIRMED")
        self.assertEqual(zone_signals["HQS_010"]["status"], "NOT_FOUND")

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

    def test_book_top_star_stays_observing_and_risk_side(self):
        result = _sell(
            {"signals": [], "overall_score": 20},
            {"zone": "强势B区", "reasons": []},
            [{"skill_id": "BXZX_CLASSIC_TOP_001", "name": "射击之星", "status": "FORMING"}],
            {"upper_wick": 50, "returns5": 0},
        )
        self.assertEqual(result["classic_top"]["state"], "OBSERVING")
        self.assertEqual(result["risk_priority"], "RISK")

    def test_star_output_discloses_book_follow_through_boundary(self):
        result = self.build()
        star_signals = [item for item in result["signals"] if item["skill_id"].startswith("BXZX_") and item["skill_id"] != "BXZX_013"]
        self.assertTrue(star_signals)
        self.assertTrue(all(any(e.get("feature") == "book_verification" for e in item["evidence"]) for item in star_signals))
        self.assertFalse(any(item["skill_id"].startswith("BXZX_CLASSIC_TOP") and item["status"] == "CONFIRMED" for item in star_signals))

    def _pattern_fixture(self, closes, volumes=None):
        volumes = volumes or [100.0] * len(closes)
        bars = []
        for index, close in enumerate(closes):
            bars.append({
                "trade_date": date(2025, 1, 1) + timedelta(days=index),
                "open": close - 0.1,
                "close": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "volume": volumes[index],
                "amount": 1000.0,
            })
        features = _series_features(_normalise_bars(bars))
        return features, _pattern_data(features, {"continuity": "持续", "direction": "偏多"}, {}, {"stage": "均线密集"})[0]

    def test_neckline_support_requires_later_retest(self):
        base = [9.0] * 10 + [10.0] * 20
        _, forming = self._pattern_fixture(base + [10.2])
        signal = next(item for item in forming if item["skill_id"] == "BXDT_NECK_004")
        self.assertEqual(signal["lifecycle"], "FORMING")
        self.assertEqual(signal["metrics"]["support_state"], "RETEST_PENDING")
        self.assertNotEqual(signal["status"], "CONFIRMED")

        _, confirmed = self._pattern_fixture(base + [10.2, 10.0, 10.3])
        signal = next(item for item in confirmed if item["skill_id"] == "BXDT_NECK_004")
        self.assertEqual(signal["lifecycle"], "CONFIRMED")
        self.assertEqual(signal["metrics"]["support_state"], "CONFIRMED")
        self.assertTrue(signal["metrics"]["retest_index"] > signal["metrics"]["first_breakout_index"])

        _, invalidated = self._pattern_fixture(base + [10.2, 10.0, 10.3, 9.8])
        signal = next(item for item in invalidated if item["skill_id"] == "BXDT_NECK_004")
        self.assertEqual(signal["status"], "INVALID")
        self.assertEqual(signal["lifecycle"], "FAILED")
        self.assertEqual(signal["metrics"]["support_state"], "INVALIDATED")

    def test_capital_bottom_requires_positive_volume_dominance(self):
        closes = [20.0 + (index * 0.05 if index % 2 else 0.0) for index in range(40)]
        volumes = [200.0 if index % 2 else 100.0 for index in range(40)]
        features, patterns = self._pattern_fixture(closes, volumes)
        features["position120"] = 20.0
        patterns = _pattern_data(features, {"continuity": "持续", "direction": "偏多"}, {}, {"stage": "均线密集"})[0]
        signal = next(item for item in patterns if item["skill_id"] == "BXDT_CAPITAL_001")
        self.assertTrue(signal["metrics"]["dominance"])
        self.assertEqual(signal["metrics"]["window"], "ENGINE_FEATURE recent 20 bars")
        self.assertEqual(signal["status"], "FORMING")

        features["recent_up_volume"], features["recent_down_volume"] = 100.0, 300.0
        features["positive_count"], features["negative_count"] = 8, 12
        patterns = _pattern_data(features, {"continuity": "持续", "direction": "偏多"}, {}, {"stage": "均线密集"})[0]
        signal = next(item for item in patterns if item["skill_id"] == "BXDT_CAPITAL_001")
        self.assertFalse(signal["metrics"]["dominance"])
        self.assertEqual(signal["status"], "NOT_FOUND")

    def test_big_pattern_geometry_discloses_book_verification_and_peak_is_not_positive(self):
        result = self.build()
        by_id = {item["skill_id"]: item for item in result["signals"]}
        for skill_id in ("BXDT_TRI_001", "BXDT_TRI_002", "BXDT_TRI_003", "BXDT_BOX_001", "BXDT_UP_001", "BXDT_PEAK_001"):
            signal = by_id[skill_id]
            self.assertTrue(any(item.get("feature") == "book_verification" for item in signal["evidence"]))
        self.assertNotEqual(by_id["BXDT_PEAK_001"]["status"], "CONFIRMED")
        self.assertEqual(by_id["BXDT_PEAK_001"]["metrics"]["direction"], "RISK_OBSERVATION_UNTIL_HOLD")

    def test_signal_book_provenance_is_explicit_and_unmapped_is_null(self):
        result = self.build()
        by_id = {item["skill_id"]: item for item in result["signals"]}
        for skill_id, book, basis, page_range in (
            ("HQS_RISK_001", "猎取强势股", "book_printed_page", "022-024"),
            ("HQS_008", "猎取强势股", "book_printed_page", "067"),
            ("HQS_009", "猎取强势股", "book_printed_page", "076-077"),
            ("HQS_010", "猎取强势股", "book_printed_page", "083-084"),
            ("BXDT_TRI_001", "暴涨大形态", "book_printed_page", "030-047"),
            ("BXDT_BOX_001", "暴涨大形态", "book_printed_page", "048-061"),
            ("BXDT_NECK_004", "暴涨大形态", "book_printed_page", "062-083"),
            ("BXDT_UP_001", "暴涨大形态", "book_printed_page", "084-096"),
            ("BXDT_BOTTOM_001", "暴涨大形态", "book_printed_page", "097-123"),
            ("BXDT_CAPITAL_001", "暴涨大形态", "book_printed_page", "124-136"),
            ("BXDT_PEAK_001", "暴涨大形态", "book_printed_page", "169-205"),
            ("BXZX_001", "暴涨之星", "pdf_physical_page", "018,021-025"),
            ("BXZX_CLASSIC_TOP_001", "暴涨之星", "pdf_physical_page", "121-133"),
        ):
            signal = by_id[skill_id]
            self.assertEqual(signal["detection_basis"], "ENGINE_FEATURE")
            self.assertEqual(signal["book_source"], {"book": book, "chapter": signal["book_source"]["chapter"], "page_basis": basis, "page_range": page_range})
        self.assertEqual(by_id["BXDT_3D_001"]["detection_basis"], "ENGINE_FEATURE")
        self.assertIsNone(by_id["BXDT_3D_001"]["book_source"])
        self.assertIsNone(by_id["BXZX_007"]["book_source"])

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
