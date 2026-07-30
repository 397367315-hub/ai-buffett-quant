import unittest
from unittest.mock import AsyncMock, patch

from services.data_collector import collector
from services.trading_engine import AITradingEngine


class TradingEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_stock_quote_uses_validated_stock_endpoint(self):
        engine = AITradingEngine()
        fetch_json = AsyncMock(return_value={
            "data": {
                "f43": 136176,
                "f44": 136200,
                "f45": 132200,
                "f47": 71873,
                "f48": 9712135434.0,
                "f169": 4076,
                "f170": 309,
            }
        })
        with patch.object(collector, "fetch_json", new=fetch_json):
            quote = await engine._fetch_stock_price("600519.SH")

        self.assertEqual(quote["price"], 1361.76)
        self.assertEqual(quote["change_pct"], 3.09)
        self.assertEqual(quote["high"], 1362.0)
        self.assertEqual(quote["low"], 1322.0)
        self.assertEqual(quote["amount"], 9712135434)
        self.assertEqual(fetch_json.await_args.args[1]["secid"], "1.600519")


if __name__ == "__main__":
    unittest.main()
