import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import DragonBoardDaily
from services.dragon_board import DragonBoardService


class DragonBoardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_persist_deduplicates_same_stock_and_combines_reasons(self):
        service = DragonBoardService()
        rows = [
            {
                "date": "2026-08-06", "code": "600001", "name": "测试股份",
                "price": 10, "amount": 1000, "net_amount": 200,
                "buy_amount": 600, "sell_amount": 400, "institution_count": 1,
                "reason": "日涨幅偏离值达7%",
            },
            {
                "date": "2026-08-06", "code": "600001", "name": "测试股份",
                "price": 10, "amount": 1000, "net_amount": 200,
                "buy_amount": 600, "sell_amount": 400, "institution_count": 2,
                "reason": "日换手率达20%",
            },
        ]
        with patch("services.dragon_board.async_session", self.session_factory):
            result = await service._persist(rows)
            board = await service.get_board(date(2026, 8, 6))

        self.assertEqual(result["written"], 1)
        self.assertEqual(board["summary"]["total"], 1)
        self.assertEqual(board["stocks"][0]["institution_count"], 2)
        self.assertIn("日涨幅偏离值达7%", board["stocks"][0]["reason"])
        self.assertIn("日换手率达20%", board["stocks"][0]["reason"])

    async def test_refresh_requests_target_date_and_reads_persisted_snapshot(self):
        service = DragonBoardService()
        upstream = [{
            "date": "2026-08-05", "code": "000001", "name": "平安银行",
            "price": 12, "change_pct": 8.0, "turnover": 10,
            "amount": 1000, "net_amount": 300, "buy_amount": 650, "sell_amount": 350,
            "institution_count": 1, "reason": "日涨幅偏离值达7%",
        }]
        fetch = AsyncMock(return_value=upstream)
        with (
            patch("services.dragon_board.async_session", self.session_factory),
            patch("services.dragon_board.collector.fetch_dragon_board", fetch),
        ):
            refreshed = await service.refresh(date(2026, 8, 5))
            board = await service.get_board(date(2026, 8, 5))

        self.assertEqual(refreshed["status"], "success")
        self.assertEqual(board["data_date"], "2026-08-05")
        self.assertEqual(board["summary"]["total_net_amount"], 300)
        fetch.assert_awaited_once_with(page_size=500, target_date=date(2026, 8, 5))

    async def test_period_analysis_identifies_recurring_and_net_buy_leaders(self):
        start = date(2026, 7, 27)
        async with self.session_factory() as session:
            for index in range(5):
                trade_date = start + timedelta(days=index)
                session.add(DragonBoardDaily(
                    trade_date=trade_date, stock_code="600001", stock_name="连续净买",
                    net_amount=100 + index * 10, buy_amount=500, sell_amount=400,
                    institution_count=1,
                ))
                session.add(DragonBoardDaily(
                    trade_date=trade_date, stock_code=f"000{index:03d}", stock_name=f"净卖{index}",
                    net_amount=-50, buy_amount=100, sell_amount=150,
                    institution_count=0,
                ))
            await session.commit()

        service = DragonBoardService()
        with (
            patch("services.dragon_board.async_session", self.session_factory),
            patch("services.dragon_board.ai_service.client", None),
        ):
            result = await service.analyze("week")

        self.assertTrue(result["available"])
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["analysis"]["top_net_buys"][0]["name"], "连续净买")
        self.assertEqual(result["analysis"]["recurring"][0]["appearances"], 5)
        self.assertFalse(result["ai_generated"])


if __name__ == "__main__":
    unittest.main()
