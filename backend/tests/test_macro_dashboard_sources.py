import unittest

from services.macro_dashboard import parse_cboe_vix_csv, parse_treasury_yield_csv


class MacroDashboardSourceTests(unittest.TestCase):
    def test_treasury_fallback_parses_latest_yields_and_point_changes(self):
        payload = """Date,\"2 Yr\",\"10 Yr\"\n08/18/2026,4.19,4.71\n08/19/2026,4.20,4.65\n"""

        rows = parse_treasury_yield_csv(payload)

        self.assertEqual({row["key"] for row in rows}, {"us2y", "us10y"})
        by_key = {row["key"]: row for row in rows}
        self.assertEqual(by_key["us10y"]["value"], 4.65)
        self.assertEqual(by_key["us10y"]["change_pct"], -0.06)
        self.assertEqual(by_key["us10y"]["source"], "美国财政部日收益率")
        self.assertFalse(by_key["us10y"]["is_realtime"])

    def test_cboe_fallback_parses_latest_close_and_relative_change(self):
        payload = """DATE,OPEN,HIGH,LOW,CLOSE\n08/18/2026,15.81,16.09,15.60,15.84\n08/19/2026,15.92,15.95,14.77,14.89\n"""

        rows = parse_cboe_vix_csv(payload)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["key"], "vix")
        self.assertEqual(rows[0]["value"], 14.89)
        self.assertAlmostEqual(rows[0]["change_pct"], -6.0, places=1)
        self.assertEqual(rows[0]["source"], "Cboe公开VIX历史")
        self.assertFalse(rows[0]["is_realtime"])


if __name__ == "__main__":
    unittest.main()
