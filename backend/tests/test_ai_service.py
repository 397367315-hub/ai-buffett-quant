import unittest

from services.ai_service import clean_ai_text
from services.macro_dashboard import parse_eastmoney_market_payload


class AITextCleaningTests(unittest.TestCase):
    def test_removes_markdown_decoration_without_removing_financial_facts(self):
        raw = "### 当前判断\n**量能不足**，关注 600519。\n```text\n风险中性\n```"
        self.assertEqual(clean_ai_text(raw), "当前判断\n量能不足，关注 600519。\n风险中性")


class GlobalMarketSourceTests(unittest.TestCase):
    def test_parses_eastmoney_global_quote_rows_with_source_time(self):
        payload = {"data": {"diff": [{
            "f2": 7700.0, "f3": 0.5, "f12": "SPX", "f124": 1787169585,
        }]}}
        rows = parse_eastmoney_market_payload(payload)
        self.assertEqual(rows[0]["key"], "sp500")
        self.assertEqual(rows[0]["source"], "东方财富全球行情")
        self.assertEqual(rows[0]["change_pct"], 0.5)
        self.assertTrue(rows[0]["source_time"])


if __name__ == "__main__":
    unittest.main()
