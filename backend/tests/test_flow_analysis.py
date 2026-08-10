import unittest
from datetime import date, timedelta
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import IndustryFundFlowDaily, MarketBoard
from services.flow_analysis import FLOW_WINDOWS, FlowAnalysisService


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

    async def test_quarter_and_year_windows_are_available_without_fabricating_coverage(self):
        service = FlowAnalysisService()
        with (
            patch("services.flow_analysis.async_session", self.session_factory),
            patch("services.flow_analysis.ai_service.client", None),
        ):
            quarter = await service.analyze("industry", "quarter")
            year = await service.analyze("industry", "year")

        self.assertEqual(FLOW_WINDOWS["quarter"]["sessions"], 60)
        self.assertEqual(FLOW_WINDOWS["year"]["sessions"], 250)
        self.assertEqual(quarter["coverage"]["actual_sessions"], 5)
        self.assertEqual(year["coverage"]["actual_sessions"], 5)
        self.assertFalse(year["coverage"]["complete"])

    def test_positive_boards_are_never_labeled_as_outflows(self):
        analysis = FlowAnalysisService._deterministic_summary(
            {"label": "近一周", "sessions": 5},
            "industry",
            [{
                "code": "BK001", "name": "仍在流入", "days": 5,
                "total_inflow": 100, "average_inflow": 20, "latest_inflow": 10,
                "positive_days": 5, "negative_days": 0, "positive_ratio_pct": 100,
                "average_change_pct": 1.0, "trend_delta": 0, "previous_average": 20,
            }],
            [{"date": "2026-08-10", "net_inflow": 100, "inflow_boards": 1, "outflow_boards": 0, "board_count": 1}],
        )
        self.assertEqual(analysis["top_outflows"], [])
        self.assertFalse(analysis["outflow_data_available"])


if __name__ == "__main__":
    unittest.main()
