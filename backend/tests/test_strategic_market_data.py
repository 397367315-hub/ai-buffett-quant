import unittest
from datetime import date, timedelta
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import MarketSentimentDaily
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


if __name__ == "__main__":
    unittest.main()
