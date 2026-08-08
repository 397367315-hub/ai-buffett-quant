import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from services.ftshare_mcp import FTShareMCPClient, FTShareMCPError


class FTShareMCPClientTests(unittest.TestCase):
    def test_sse_response_parser_extracts_json_rpc_payload(self):
        message = FTShareMCPClient._parse_rpc_response(
            "data: \n"
            "id: 0\n\n"
            "data: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"ok\":true}}\n"
        )

        self.assertEqual(message["result"], {"ok": True})

    def test_a_share_codes_map_to_correct_ftshare_symbols(self):
        self.assertEqual(FTShareMCPClient.stock_symbol("600519"), "600519.SH")
        self.assertEqual(FTShareMCPClient.stock_symbol("000001"), "000001.SZ")
        self.assertEqual(FTShareMCPClient.stock_symbol("920065"), "920065.BJ")
        with self.assertRaises(FTShareMCPError):
            FTShareMCPClient.stock_symbol("invalid")

    def test_document_url_only_accepts_hashes_returned_by_ftshare(self):
        self.assertEqual(
            FTShareMCPClient.announcement_document_url("a" * 64),
            "https://market.ft.tech/api/v1/market/data/announcements/stock-announcements/" + "a" * 64,
        )
        self.assertIsNone(FTShareMCPClient.announcement_document_url("../../not-a-document"))

    def test_mcp_requests_use_configured_data_proxy(self):
        received = {}

        def handler(request: httpx.Request) -> httpx.Response:
            received["url"] = str(request.url)
            received["headers"] = dict(request.headers)
            received["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}},
                headers={"Mcp-Session-Id": "proxy-session"},
            )

        async def run() -> tuple[dict, httpx.Headers]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await FTShareMCPClient()._post(
                    client,
                    "https://market.ft.tech/gateway/mcp",
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                    {"Accept": "application/json", "Mcp-Session-Id": "upstream-session"},
                )

        with (
            patch("services.ftshare_mcp.settings.data_proxy_base_url", "https://proxy.example"),
            patch("services.ftshare_mcp.settings.data_proxy_token", "proxy-token"),
        ):
            message, headers = asyncio.run(run())

        self.assertEqual(received["url"], "https://proxy.example/ftshare-mcp")
        self.assertEqual(received["headers"]["x-data-proxy-token"], "proxy-token")
        self.assertEqual(received["body"]["payload"]["method"], "initialize")
        self.assertEqual(received["body"]["headers"]["Mcp-Session-Id"], "upstream-session")
        self.assertEqual(message["result"], {"ok": True})
        self.assertEqual(headers.get("Mcp-Session-Id"), "proxy-session")

    def test_paginated_tool_reads_every_non_truncated_page(self):
        requested_pages = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            method = payload.get("method")
            if method == "initialize":
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {"protocolVersion": "2025-03-26"},
                    },
                    headers={"Mcp-Session-Id": "history-session"},
                )
            if method == "notifications/initialized":
                return httpx.Response(202)

            page = payload["params"]["arguments"]["page"]
            requested_pages.append(page)
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": page + 1,
                    "result": {
                        "structuredContent": {
                            "data": [{"page": page}],
                            "metadata": {
                                "pagination": {"pages": 3},
                                "truncated": False,
                            },
                        },
                    },
                },
            )

        original_async_client = httpx.AsyncClient

        def client_factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return original_async_client(*args, **kwargs)

        async def run():
            return await FTShareMCPClient().call_paginated_tool(
                "ft_get_eastmoney_sector_flow",
                {"sector_type": "industry"},
                page_size=2,
                concurrency=2,
            )

        with (
            patch("services.ftshare_mcp.settings.ftshare_mcp_enabled", True),
            patch("services.ftshare_mcp.settings.data_proxy_base_url", ""),
            patch("services.ftshare_mcp.httpx.AsyncClient", side_effect=client_factory),
        ):
            result = asyncio.run(run())

        self.assertEqual(sorted(requested_pages), [1, 2, 3])
        self.assertEqual(sorted(item["page"] for item in result["data"]), [1, 2, 3])
        self.assertEqual(result["metadata"]["returned"], 3)

    def test_sector_history_uses_verified_iso_date_contract(self):
        client = FTShareMCPClient()
        client.call_paginated_tool = AsyncMock(return_value={
            "data": [{"sector_code": "BK0475"}],
            "metadata": {},
        })

        rows = asyncio.run(client.get_sector_flow_history(
            "industry",
            "2025-08-08",
            "2026-08-08",
        ))

        self.assertEqual(rows, [{"sector_code": "BK0475"}])
        client.call_paginated_tool.assert_awaited_once_with(
            "ft_get_eastmoney_sector_flow",
            {
                "sector_type": "industry",
                "start_date": "2025-08-08",
                "end_date": "2026-08-08",
            },
        )
