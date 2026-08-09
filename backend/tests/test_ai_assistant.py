import unittest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import AIChatHistory, StockDailyBar, StockFundFlowDaily
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

    async def test_quote_failure_falls_back_to_labelled_daily_cache(self):
        async with self.session_factory() as session:
            session.add(StockDailyBar(
                stock_code="600519", stock_name="贵州茅台", market="SH",
                trade_date=date(2026, 8, 7), close_price=1488.0,
                change_pct=-0.4, volume=9000, amount=13_000_000,
                source="test_cache",
            ))
            await session.commit()

        with patch(
            "services.ai_assistant.quote_snapshot_service.fetch",
            new_callable=AsyncMock,
            side_effect=TimeoutError,
        ):
            context = await self.service.build_context("分析600519")

        stock_context = context["stocks"]
        self.assertEqual(stock_context["quotes"][0]["price"], 1488.0)
        self.assertEqual(stock_context["quotes"][0]["name"], "贵州茅台")
        self.assertEqual(stock_context["quote_metadata"]["source"], "database_cache")
        self.assertEqual(stock_context["quote_metadata"]["data_date"], "2026-08-07")
        self.assertFalse(stock_context["quote_metadata"]["is_realtime"])
        self.assertTrue(stock_context["quote_metadata"]["cache_used"])

    async def test_strategy_context_backfills_missing_daily_history_on_demand(self):
        start = date(2026, 6, 1)
        history = [{
            "trade_date": (start + timedelta(days=index)).isoformat(),
            "open": 100 + index, "close": 101 + index,
            "high": 102 + index, "low": 99 + index,
            "volume": 1_000_000, "amount": 100_000_000,
            "amplitude": 3.0, "change_pct": 1.0,
            "change_amount": 1.0, "turnover": 4.0,
        } for index in range(30)]
        quote = {
            "stocks": [{"code": "600519", "name": "贵州茅台", "price": 130.0}],
            "source": "cache", "data_date": "2026-08-07", "is_realtime": False,
            "cache_used": True, "complete": True,
        }
        price_history = {
            "code": "600519", "name": "贵州茅台", "source": "tencent", "history": history,
        }

        with (
            patch("services.ai_assistant.quote_snapshot_service.fetch", new=AsyncMock(return_value=quote)),
            patch("services.ai_assistant.collector.fetch_stock_price_history", new=AsyncMock(return_value=price_history)) as fetch,
            patch("services.history_cache.async_session", self.session_factory),
        ):
            context = await self.service._stock_context(
                ["600519"], daily_limit=120, ensure_minimum=30,
            )

        fetch.assert_awaited_once()
        self.assertEqual(len(context["daily_bars"]["600519"]), 30)
        self.assertTrue(context["history_coverage"]["600519"]["sufficient"])
        self.assertTrue(context["history_coverage"]["600519"]["refresh_attempted"])

    async def test_non_trading_context_backfills_incomplete_fund_flow_history(self):
        async with self.session_factory() as session:
            session.add(StockFundFlowDaily(
                stock_code="600519", stock_name="贵州茅台",
                trade_date=date(2026, 8, 7), main_net_inflow=100.0,
            ))
            await session.commit()
        history = [{
            "date": (date(2026, 7, 27) + timedelta(days=index)).isoformat(),
            "main_net_inflow": float(index + 1),
        } for index in range(12)]

        with (
            patch(
                "services.ai_assistant.shanghai_now",
                return_value=datetime(2026, 8, 9, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
            ),
            patch(
                "services.ai_assistant.collector.fetch_stock_fund_flow",
                new=AsyncMock(return_value=history),
            ) as fetch,
        ):
            context = await self.service._stock_flow_context(["600519"])

        fetch.assert_awaited_once_with("600519")
        self.assertGreaterEqual(len(context["series"]["600519"]), 10)
        self.assertEqual(context["source"], "eastmoney")

    async def test_non_trading_context_uses_ftshare_when_eastmoney_has_only_latest_day(self):
        start = date(2026, 7, 27)
        async with self.session_factory() as session:
            session.add_all([
                StockDailyBar(
                    stock_code="600519", stock_name="贵州茅台", market="SH",
                    trade_date=start + timedelta(days=index), close_price=1400 + index,
                    change_pct=0.1, volume=1000, amount=1_000_000, source="test_cache",
                )
                for index in range(10)
            ])
            session.add(StockFundFlowDaily(
                stock_code="600519", stock_name="贵州茅台",
                trade_date=start + timedelta(days=9), main_net_inflow=100.0,
            ))
            await session.commit()

        eastmoney_latest = [{
            "date": (start + timedelta(days=9)).isoformat(),
            "main_net_inflow": 100,
        }]

        async def ftshare_day(code: str, trade_date: str):
            return {
                "date": trade_date,
                "main_net_inflow": 200,
                "super_large_net_inflow": 80,
                "large_net_inflow": 120,
                "medium_net_inflow": -40,
                "small_net_inflow": -160,
            }

        with (
            patch(
                "services.ai_assistant.shanghai_now",
                return_value=datetime(2026, 8, 9, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
            ),
            patch(
                "services.ai_assistant.collector.fetch_stock_fund_flow",
                new=AsyncMock(return_value=eastmoney_latest),
            ),
            patch(
                "services.ai_assistant.ftshare_mcp_client.get_stock_capital_flow",
                new=AsyncMock(side_effect=ftshare_day),
            ) as ftshare,
        ):
            context = await self.service._stock_flow_context(["600519"])

        self.assertEqual(ftshare.await_count, 9)
        self.assertEqual(len(context["series"]["600519"]), 10)
        self.assertEqual(context["source"], "eastmoney+ftshare_mcp")
        self.assertNotIn("600519", context["errors"])

    async def test_non_trading_context_reuses_complete_fund_flow_history(self):
        async with self.session_factory() as session:
            session.add_all([
                StockFundFlowDaily(
                    stock_code="600519", stock_name="贵州茅台",
                    trade_date=date(2026, 7, 27) + timedelta(days=index),
                    main_net_inflow=float(index + 1),
                )
                for index in range(12)
            ])
            await session.commit()

        with (
            patch(
                "services.ai_assistant.shanghai_now",
                return_value=datetime(2026, 8, 9, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
            ),
            patch(
                "services.ai_assistant.collector.fetch_stock_fund_flow",
                new_callable=AsyncMock,
            ) as fetch,
        ):
            context = await self.service._stock_flow_context(["600519"])

        fetch.assert_not_awaited()
        self.assertEqual(len(context["series"]["600519"]), 12)
        self.assertEqual(context["source"], "database_cache")


if __name__ == "__main__":
    unittest.main()
