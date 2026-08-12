import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import MarketSentimentDaily, StockDailyBar
from services.strategic_market_data import StrategicMarketDataService


class StrategicMarketDataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_patch = patch("services.strategic_market_data.async_session", self.session_factory)
        self.session_patch.start()
        self.service = StrategicMarketDataService()

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()

    async def test_history_exposes_complete_breadth_amount_and_limit_statistics(self):
        start = date(2026, 7, 20)
        async with self.session_factory() as session:
            session.add_all([
                MarketSentimentDaily(
                    trade_date=start + timedelta(days=index),
                    up_count=3000 + index, down_count=2200 - index, flat_count=100,
                    stock_count=5300, market_amount=800_000_000_000 + index * 10_000_000_000,
                    amount_count=5250, average_turnover=2.0 + index * 0.05,
                    turnover_count=5250, limit_up_count=50 + index,
                    limit_down_count=5, failed_limit_count=10,
                    failed_limit_rate=12.5, max_streak_height=6, source="test",
                )
                for index in range(20)
            ])
            await session.commit()

        result = await self.service.history(limit=120)
        summary = result["summary"]

        self.assertEqual(result["count"], 20)
        self.assertTrue(summary["breadth_complete"])
        self.assertTrue(summary["amount_complete"])
        self.assertTrue(summary["turnover_complete"])
        self.assertTrue(summary["is_current"])
        self.assertEqual(summary["market_amount_percentile"], 100.0)
        self.assertEqual(summary["average_turnover_percentile"], 100.0)
        self.assertGreater(summary["market_amount_vs_ma5_pct"], 0)
        self.assertEqual(summary["failed_limit_rate"], 12.5)
        self.assertEqual(summary["max_streak_height"], 6)

    async def test_ensure_history_repairs_incomplete_strategy_evidence_once(self):
        incomplete = {"available": False, "summary": {}}
        complete = {
            "available": True,
            "summary": {
                "is_current": True,
                "breadth_complete": True,
                "amount_history_count": 5,
                "turnover_history_count": 5,
                "failed_limit_rate": 12.5,
                "max_streak_height": 4,
            },
        }
        self.service.history = AsyncMock(side_effect=[incomplete, incomplete, complete])
        self.service.sync_recent = AsyncMock(return_value={"status": "success", "written": 5})

        result = await self.service.ensure_history(limit=120)

        self.service.sync_recent.assert_awaited_once_with(days=5)
        self.assertTrue(result["refresh_attempted"])
        self.assertTrue(self.service._analysis_ready(result))

    async def test_daily_bar_derived_limit_evidence_keeps_board_thresholds_and_streaks(self):
        dates = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]
        rows = []
        close = 10.0
        for trade_date in dates:
            previous = close
            close = round(previous * 1.10, 2)
            rows.append(StockDailyBar(
                stock_code="600001", stock_name="主板测试", market="SH",
                trade_date=trade_date, open_price=previous, close_price=close,
                high_price=round(close * 1.01, 2), low_price=previous,
                amount=1_000_000, change_pct=10.0, turnover=5.0, source="test",
            ))
        rows.append(StockDailyBar(
            stock_code="300001", stock_name="创业板测试", market="SZ",
            trade_date=dates[-1], open_price=10.0, close_price=12.0,
            high_price=12.1, low_price=10.0, amount=1_000_000,
            change_pct=20.0, turnover=5.0, source="test",
        ))
        async with self.session_factory() as session:
            session.add_all(rows)
            await session.commit()

        result = await self.service._aggregate_many(dates)

        self.assertEqual(result[dates[0]]["limit_up_count"], 1)
        self.assertEqual(result[dates[-1]]["limit_up_count"], 2)
        self.assertEqual(result[dates[-1]]["max_streak_height"], 3)
        self.assertEqual(result[dates[-1]]["failed_limit_count"], 0)


if __name__ == "__main__":
    unittest.main()
