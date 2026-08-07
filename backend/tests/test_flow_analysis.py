import unittest
from datetime import date, timedelta
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import IndustryFundFlowDaily, MarketBoard
from services.flow_analysis import FlowAnalysisService


class FlowAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.session_factory() as session:
            session.add_all([
                MarketBoard(board_type="industry", code="BK001", name="持续主线"),
                MarketBoard(board_type="industry", code="BK002", name="刚转强"),
            ])
            start = date(2026, 7, 27)
            first = [100, 120, 130, 140, 150]
            second = [-100, -90, -80, -70, 50]
            for index in range(5):
                trade_date = start + timedelta(days=index)
                session.add(IndustryFundFlowDaily(
                    board_code="BK001", trade_date=trade_date,
                    main_net_inflow=first[index] * 1_000_000, change_pct=1.0,
                ))
                session.add(IndustryFundFlowDaily(
                    board_code="BK002", trade_date=trade_date,
                    main_net_inflow=second[index] * 1_000_000, change_pct=-0.3,
                ))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_week_analysis_identifies_sustained_and_turning_flows(self):
        service = FlowAnalysisService()
        with (
            patch("services.flow_analysis.async_session", self.session_factory),
            patch("services.flow_analysis.ai_service.client", None),
        ):
            result = await service.analyze("industry", "week")

        self.assertTrue(result["available"])
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["actual_sessions"], 5)
        self.assertEqual(result["analysis"]["sustained_inflows"][0]["name"], "持续主线")
        self.assertEqual(result["analysis"]["turning_positive"][0]["name"], "刚转强")
        self.assertFalse(result["ai_generated"])
        self.assertIn("近一周", result["analysis"]["headline"])

    async def test_month_analysis_marks_short_cache_coverage(self):
        service = FlowAnalysisService()
        with (
            patch("services.flow_analysis.async_session", self.session_factory),
            patch("services.flow_analysis.ai_service.client", None),
        ):
            result = await service.analyze("industry", "month")

        self.assertFalse(result["coverage"]["complete"])
        self.assertTrue(result["analysis"]["risks"])


if __name__ == "__main__":
    unittest.main()
