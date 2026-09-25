import unittest
from datetime import date, timedelta

from strong_stock_decision.book_evidence import summarize_hunter_evidence


def bar(change, volume, close=10, day=1):
    return {"trade_date": date(2025, 1, day), "close": close, "change_pct": change, "volume": volume}


class StrongStockBookEvidenceTests(unittest.TestCase):
    def test_trend_positive_observes_three_yang_proxies_and_sequence(self):
        result = summarize_hunter_evidence(bars=[bar(1, 100, day=1), bar(2, 130, day=2), bar(1, 120, day=3), bar(-1, 80, day=4), bar(-0.5, 50, day=5), bar(0.5, 90, day=6), bar(-0.5, 50, day=7), bar(3, 140, day=8)], zone="强势A区")
        three = result["three_yang_control_three_yin"]
        self.assertEqual(three["yang_more_than_yin"]["status"], "OBSERVED_PROXY")
        self.assertEqual(three["yang_expand_yin_contract"]["status"], "OBSERVED_PROXY")
        self.assertEqual(three["yang_cluster_yin_scatter"]["status"], "OBSERVED_PROXY")
        self.assertEqual(result["wash_then_reattack"]["status"], "OBSERVED_PROXY")
        self.assertFalse(three["yang_more_than_yin"]["book_confirmed"])

    def test_negative_volume_risk_is_not_positive_confirmation(self):
        result = summarize_hunter_evidence(bars=[bar(-1, 200, day=1), bar(-2, 180, day=2), bar(1, 40, day=3)], zone="强势A区")
        self.assertEqual(result["three_yang_control_three_yin"]["yang_more_than_yin"]["status"], "NOT_OBSERVED")
        self.assertEqual(result["wash_then_reattack"]["status"], "NOT_OBSERVED")

    def test_b_zone_waits_for_small_a(self):
        result = summarize_hunter_evidence(bars=[bar(1, 100, day=1), bar(-1, 70, day=2), bar(0.5, 80, day=3)], zone="强势B区")
        self.assertEqual(result["zone_gate"]["status"], "B_SMALL_A_WAITING_CONFIRMATION")
        self.assertEqual(result["trade_action"], "NONE")

    def test_c_zone_has_risk_priority(self):
        result = summarize_hunter_evidence(bars=[bar(1, 100, day=1), bar(2, 120, day=2)], zone="风险C区")
        self.assertEqual(result["zone_gate"]["status"], "RISK_PRIORITY_BLOCK")

    def test_missing_fields_are_unknown(self):
        result = summarize_hunter_evidence(bars=[{"close": 10}, {"volume": 100}])
        self.assertEqual(result["three_yang_control_three_yin"]["yang_more_than_yin"]["status"], "UNKNOWN")
        self.assertEqual(result["wash_then_reattack"]["status"], "UNKNOWN")
        self.assertEqual(result["data_quality"]["status"], "UNKNOWN")
        self.assertFalse(result["data_quality"]["point_in_time"])

    def test_as_of_and_date_order_are_validated(self):
        rows = [bar(1, 100, day=2), bar(-1, 80, day=1)]
        result = summarize_hunter_evidence(context={"bars": rows, "as_of": "2025-01-01"}, zone="强势A区")
        self.assertEqual(result["data_quality"]["status"], "UNKNOWN")
        self.assertFalse(result["data_quality"]["point_in_time"])


if __name__ == "__main__":
    unittest.main()
