import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import StockDailyBar
from services.openclaw_gateway import openclaw_gateway


class OpenClawGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_list_and_initialize_are_mcp_compatible(self):
        initialized = await openclaw_gateway.handle_rpc({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        })
        self.assertEqual(initialized["result"]["protocolVersion"], "2024-11-05")
        listed = await openclaw_gateway.handle_rpc({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })
        names = {item["name"] for item in listed["result"]["tools"]}
        self.assertIn("get_stock_quote", names)
        self.assertIn("run_overnight_strategy", names)
        self.assertIn("add_to_personal_pool", names)
        self.assertIn("analyze_mao_strategy", names)

    async def test_tool_call_is_allowlisted_and_returns_structured_content(self):
        with patch(
            "services.openclaw_gateway.HANDLERS",
            {"test_tool": AsyncMock(return_value={"ok": True, "data_date": "2026-08-08"})},
        ):
            result = await openclaw_gateway.handle_rpc({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "test_tool", "arguments": {}},
            })
        self.assertFalse(result["result"]["isError"])
        self.assertEqual(result["result"]["structuredContent"]["ok"], True)

        rejected = await openclaw_gateway.handle_rpc({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "os_shell", "arguments": {"command": "id"}},
        })
        self.assertTrue(rejected["result"]["isError"])
        self.assertIn("未知工具", rejected["result"]["content"][0]["text"])

    async def test_notification_has_no_response(self):
        self.assertIsNone(await openclaw_gateway.handle_rpc({
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }))


class OpenClawDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_patch = patch("services.openclaw_database.async_session", self.session_factory)
        self.session_patch.start()

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()

    async def test_database_tool_reads_allowlisted_rows_with_audit_metadata(self):
        async with self.session_factory() as session:
            session.add_all([
                StockDailyBar(
                    stock_code="600519", stock_name="贵州茅台", market="SH",
                    trade_date=date(2026, 8, 7), close_price=1500.0,
                    change_pct=1.2, volume=100, amount=150000, source="eastmoney",
                ),
                StockDailyBar(
                    stock_code="600519", stock_name="贵州茅台", market="SH",
                    trade_date=date(2026, 8, 6), close_price=1482.0,
                    change_pct=-0.3, volume=90, amount=133380, source="eastmoney",
                ),
            ])
            await session.commit()

        response = await openclaw_gateway.handle_rpc({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "query_system_database",
                "arguments": {
                    "dataset": "stock_daily_bars",
                    "stock_code": "600519.SH",
                    "start_date": "2026-08-06",
                    "end_date": "2026-08-07",
                    "fields": ["stock_code", "trade_date", "close_price", "source"],
                    "limit": 1,
                },
            },
        })
        result = response["result"]
        self.assertFalse(result["isError"])
        payload = result["structuredContent"]
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["pagination"]["total"], 2)
        self.assertEqual(payload["pagination"]["returned"], 1)
        self.assertTrue(payload["pagination"]["has_more"])
        self.assertEqual(payload["data_date"], "2026-08-07")
        self.assertEqual(payload["records"][0]["stock_code"], "600519")
        self.assertEqual(set(payload["records"][0]), {"stock_code", "trade_date", "close_price", "source"})

    async def test_database_tool_rejects_arbitrary_sql_and_unknown_fields(self):
        for arguments, message in [
            ({"dataset": "stock_daily_bars", "sql": "SELECT * FROM stock_daily_bars"}, "不支持的数据库查询参数"),
            ({"dataset": "stock_daily_bars", "fields": ["password"]}, "不允许读取字段"),
        ]:
            response = await openclaw_gateway.handle_rpc({
                "jsonrpc": "2.0", "id": 6, "method": "tools/call",
                "params": {"name": "query_system_database", "arguments": arguments},
            })
            self.assertTrue(response["result"]["isError"])
            self.assertIn(message, response["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
