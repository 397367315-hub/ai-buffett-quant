import unittest
from unittest.mock import AsyncMock, patch

from services.block_trade_analysis import BlockTradeAnalysisService


class BlockTradeAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_analysis_keeps_premium_as_evidence_not_a_price_promise(self):
        service = BlockTradeAnalysisService()
        trades = [{
            "code": "600001", "name": "测试股份", "date": "2026-08-10",
            "price": 10.5, "amount": 30_000_000, "premium": 2.0,
            "buyer": "机构专用", "seller": "某证券营业部",
        }]
        service._quotes = AsyncMock(return_value={
            "600001": {"price": 10.0, "cache_trade_date": "2026-08-10", "quote_source": "cache"},
        })
        with patch("services.block_trade_analysis.ai_service.client", None):
            result = await service.analyze(trades)

        stock = result["stocks"][0]
        self.assertIn("平均溢价", stock["evidence"][0])
        self.assertIn("当前可验证价低于最近大宗成交价", stock["risks"][0])
        self.assertIn("不能单独证明后续涨跌", result["summary"])
        self.assertEqual(stock["buyer_types"], ["机构专用席位"])

    async def test_missing_quote_is_reported_instead_of_filled(self):
        service = BlockTradeAnalysisService()
        service._quotes = AsyncMock(return_value={})
        with patch("services.block_trade_analysis.ai_service.client", None):
            result = await service.analyze([{
                "code": "600002", "name": "无行情股份", "date": "2026-08-10",
                "price": 8.0, "amount": 5_000_000, "premium": -3.0,
                "buyer": "", "seller": "",
            }])

        stock = result["stocks"][0]
        self.assertIsNone(stock["latest_price"])
        self.assertTrue(any("最新行情未返回" in item for item in stock["risks"]))
        self.assertTrue(any("平均折价" in item for item in stock["risks"]))


if __name__ == "__main__":
    unittest.main()
