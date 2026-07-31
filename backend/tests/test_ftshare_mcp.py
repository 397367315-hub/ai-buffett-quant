import unittest

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
