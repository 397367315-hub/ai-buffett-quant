import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import AIChatHistory, StockDailyBar
from services.ai_assistant import AIAssistantService, MAX_HISTORY_MESSAGES


class AIAssistantTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_patch = patch("services.ai_assistant.async_session", self.session_factory)
        self.session_patch.start()
        self.service = AIAssistantService()

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()

    async def test_history_keeps_latest_forty_rounds_and_can_be_cleared(self):
        async with self.session_factory() as session:
            session.add_all([
                AIChatHistory(user_id="web_user", role="user" if index % 2 == 0 else "assistant", content=f"message-{index}")
                for index in range(MAX_HISTORY_MESSAGES + 4)
            ])
            await session.commit()

        await self.service.save_message("web_user", "user", "latest-message", "beginner")
        history = await self.service.history("web_user")

        self.assertEqual(len(history), MAX_HISTORY_MESSAGES)
        self.assertEqual(history[-1]["content"], "latest-message")
        self.assertNotEqual(history[0]["content"], "message-0")

        deleted = await self.service.clear_history("web_user")
        self.assertEqual(deleted, MAX_HISTORY_MESSAGES)
        self.assertEqual(await self.service.history("web_user"), [])

    async def test_stock_question_loads_verified_quote_and_recent_daily_bars(self):
        start = date(2026, 7, 1)
        async with self.session_factory() as session:
            session.add_all([
                StockDailyBar(
                    stock_code="600519", stock_name="贵州茅台", market="SH",
                    trade_date=start + timedelta(days=index), close_price=1400 + index,
                    change_pct=0.5, volume=1000 + index, amount=2_000_000,
                    source="test_cache",
                )
                for index in range(35)
            ])
            await session.commit()
        quote = {
            "stocks": [{"code": "600519", "name": "贵州茅台", "price": 1434.0}],
            "source": "cache", "data_date": "2026-08-04", "is_realtime": False,
            "cache_used": True, "complete": True,
        }

        with patch(
            "services.ai_assistant.quote_snapshot_service.fetch",
            new_callable=AsyncMock,
            return_value=quote,
        ) as fetch:
            context = await self.service.build_context("600519近一个月走势怎样？")

        fetch.assert_awaited_once_with(["600519"], self.session_factory)
        self.assertTrue(context["available"])
        self.assertIn("股票行情与近30条日线", context["sources"])
        self.assertEqual(context["stocks"]["quote_metadata"]["data_date"], "2026-08-04")
        self.assertFalse(context["stocks"]["quote_metadata"]["is_realtime"])
        self.assertEqual(len(context["stocks"]["daily_bars"]["600519"]), 30)
        self.assertEqual(context["stocks"]["daily_bars"]["600519"][-1]["close"], 1434)


if __name__ == "__main__":
    unittest.main()
