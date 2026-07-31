import unittest
from datetime import date, timedelta

from services.a_stock_data import calculate_indicators


def _history(days: int = 80) -> list[dict]:
    start = date(2026, 1, 2)
    rows = []
    for index in range(days):
        close = 10 + index * 0.12
        rows.append({
            "date": (start + timedelta(days=index)).isoformat(),
            "close": close,
            "high": close + 0.25,
            "low": close - 0.25,
            "volume": 1000 + index * 12,
        })
    return rows


class AStockDataSkillTests(unittest.TestCase):
    def test_indicator_contract_returns_skill_fields_without_talib(self):
        result = calculate_indicators(_history())

        self.assertEqual(result["contract"]["slug"], "a-stock-data")
        self.assertEqual(result["contract"]["history_adjustment"], "qfq")
        self.assertIsNotNone(result["ma20"])
        self.assertIsNotNone(result["macd"]["hist"])
        self.assertIsNotNone(result["rsi"]["rsi14"])
        self.assertIsNotNone(result["kdj"]["j"])
        self.assertIsNotNone(result["boll"]["upper"])
        self.assertIsNotNone(result["volume"]["ratio"])

    def test_missing_fields_are_not_converted_to_zero(self):
        rows = _history(25)
        for row in rows:
            row.pop("volume")
        result = calculate_indicators(rows)

        self.assertIsNone(result["volume"]["ma5"])
        self.assertIsNone(result["volume"]["ratio"])
        self.assertIsNotNone(result["ma20"])

    def test_latest_missing_volume_does_not_reuse_an_older_bar(self):
        rows = _history(25)
        rows[-1].pop("volume")

        result = calculate_indicators(rows)

        self.assertIsNone(result["volume"]["ma5"])
        self.assertIsNone(result["volume"]["ratio"])

    def test_incomplete_high_low_does_not_create_a_kdj_value(self):
        rows = _history(25)
        rows[-1].pop("high")
        rows[-1].pop("low")

        result = calculate_indicators(rows)

        self.assertIsNone(result["kdj"]["k"])
        self.assertIsNone(result["kdj"]["d"])
        self.assertIsNone(result["kdj"]["j"])


if __name__ == "__main__":
    unittest.main()
