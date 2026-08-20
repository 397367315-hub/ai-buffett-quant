import unittest
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from services.ai_service import clean_ai_text
from services.macro_dashboard import (
    parse_eastmoney_market_payload,
    parse_fred_macro_zip,
    parse_mofcom_credit_payload,
    _parse_eastmoney_indicator,
)


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

    def test_parses_fred_daily_rates_and_vix_from_public_zip(self):
        content = BytesIO()
        with ZipFile(content, "w", ZIP_DEFLATED) as archive:
            archive.writestr("daily.csv", "observation_date,DGS10,DGS2\n2026-08-18,4.20,3.60\n2026-08-19,4.25,3.65\n")
            archive.writestr("daily,_close.csv", "observation_date,VIXCLS\n2026-08-18,18.0\n2026-08-19,19.8\n")
        rows = {item["key"]: item for item in parse_fred_macro_zip(content.getvalue())}
        self.assertEqual(rows["us10y"]["value"], 4.25)
        self.assertEqual(rows["us10y"]["change_pct"], 0.05)
        self.assertEqual(rows["us2y"]["value"], 3.65)
        self.assertEqual(rows["vix"]["change_pct"], 10.0)
        self.assertFalse(rows["us10y"]["is_realtime"])

    def test_builds_credit_pulse_proxy_from_social_finance_flow(self):
        rows = [{"date": f"{2025 + index // 12:04d}{index % 12 + 1:02d}", "tiosfs": 100 + index} for index in range(13)]
        result = parse_mofcom_credit_payload(rows)
        self.assertEqual(result["key"], "credit_pulse")
        self.assertIn("滚动总额同比变化", result["method"])
        self.assertEqual(result["latest_monthly_increment"], 112)

    def test_parses_eastmoney_macro_indicator_fields(self):
        result = _parse_eastmoney_indicator(
            {"result": {"data": [{"REPORT_DATE": "2026-07-01 00:00:00", "BASE": 103.0, "BASE_SAME": 6.4, "BASE_SEQUENTIAL": -0.5}]}},
            key="industry_price", label="企业商品价格指数", value_field="BASE", yoy_field="BASE_SAME", mom_field="BASE_SEQUENTIAL", source="东方财富",
        )
        self.assertEqual(result["value"], 103.0)
        self.assertEqual(result["yoy_pct"], 6.4)
        self.assertEqual(result["mom_pct"], -0.5)


if __name__ == "__main__":
    unittest.main()
