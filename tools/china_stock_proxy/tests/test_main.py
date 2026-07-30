import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main as proxy  # noqa: E402


class ProxyRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_historical_eastmoney_request_tries_delay_host_first(self):
        requested_urls = []

        async def fake_get(url, params, headers):
            requested_urls.append(url)
            return {"data": {"klines": ["2026-07-30,1"]}}

        with patch.object(proxy, "_get_upstream_json", new=fake_get):
            await proxy._fetch_market_request(
                "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
                {},
                {},
            )

        self.assertEqual(urlparse(requested_urls[0]).hostname, proxy.PUSH2_DELAY_HOST)


if __name__ == "__main__":
    unittest.main()
