import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main as proxy  # noqa: E402


class ProxyRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_historical_eastmoney_request_falls_back_to_delay_host(self):
        requested_urls = []

        async def fake_get(url, params, headers):
            requested_urls.append(url)
            if urlparse(url).hostname == proxy.PUSH2_HISTORY_HOST:
                raise proxy.httpx.ConnectError("history host unavailable")
            return {"data": {"klines": ["2026-07-30,1"]}}

        with patch.object(proxy, "_get_upstream_json", new=fake_get):
            await proxy._fetch_market_request(
                "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
                {},
                {},
            )

        self.assertEqual(
            [urlparse(url).hostname for url in requested_urls],
            [proxy.PUSH2_HISTORY_HOST, proxy.PUSH2_DELAY_HOST],
        )

    async def test_ftshare_proxy_relays_only_allowlisted_tool_and_session_header(self):
        received = {}

        class FakeResponse:
            status_code = 200
            content = b'{"jsonrpc":"2.0","id":2,"result":{"data":[]}}'
            headers = {
                "content-type": "application/json",
                "Mcp-Session-Id": "upstream-session",
            }

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def post(self, url, json, headers):
                received.update({"url": url, "json": json, "headers": headers})
                return FakeResponse()

        request = proxy.FTShareMCPRequest(
            payload={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "ft_stock_filter", "arguments": {"page": 1}},
            },
            headers={"Mcp-Session-Id": "caller-session"},
        )
        with patch.object(proxy.httpx, "AsyncClient", return_value=FakeClient()):
            response = await proxy.fetch_ftshare_mcp(request)

        self.assertEqual(received["url"], proxy.FTSHARE_MCP_URL)
        self.assertEqual(received["headers"]["Mcp-Session-Id"], "caller-session")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["mcp-session-id"], "upstream-session")
        self.assertEqual(response.body, FakeResponse.content)


if __name__ == "__main__":
    unittest.main()
