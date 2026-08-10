import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import StockDailyBar
from services.quant_research import QuantResearchEngine
from services.quant_research_workspace import QuantResearchWorkspaceService


class QuantResearchJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_bar_loader_selects_only_the_research_fields(self):
        async with self.session_factory() as session:
            session.add(StockDailyBar(
                stock_code="600001", trade_date=date.today(), open_price=10,
                close_price=10.2, amount=5_000_000, source="test",
            ))
            await session.commit()

        with patch("services.quant_research.async_session", self.session_factory):
            rows = await QuantResearchEngine._load_bars(30, 20, 5)

        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0].keys()), {"code", "date", "open", "close", "amount", "source"})
        self.assertEqual(rows[0]["code"], "600001")

    async def test_streaming_loader_builds_compact_series(self):
        async with self.session_factory() as session:
            session.add_all([
                StockDailyBar(
                    stock_code="600001", trade_date=date(2026, 8, day), open_price=10 + day,
                    close_price=10.2 + day, amount=5_000_000, source="test",
                )
                for day in (1, 2)
            ])
            await session.commit()

        with patch("services.quant_research.async_session", self.session_factory):
            grouped, row_count, sources = await QuantResearchEngine._load_grouped_bars(30, 20, 5)

        self.assertEqual(row_count, 2)
        self.assertEqual(sources, ["test"])
        self.assertEqual(list(grouped["600001"].dates), [date(2026, 8, 1).toordinal(), date(2026, 8, 2).toordinal()])
        self.assertEqual(list(grouped["600001"].closes), [11.2, 12.2])

    async def test_engine_keeps_internal_periods_for_report_partitions(self):
        today = date.today()
        rows = []
        for stock_index in range(2):
            for offset in range(70):
                trade_date = today - timedelta(days=69 - offset)
                close = 10 + stock_index + offset * 0.03
                rows.append({
                    "code": f"60000{stock_index}", "date": trade_date,
                    "open": close * 0.999, "close": close,
                    "amount": 5_000_000, "source": "test",
                })
        grouped = QuantResearchEngine._normalise_bars(rows, today)

        with patch.object(
            QuantResearchEngine,
            "_load_grouped_bars",
            new_callable=AsyncMock,
            return_value=(grouped, len(rows), ["test"]),
        ):
            result = await QuantResearchEngine.run(days=30, lookback_days=10, holding_days=2)

        self.assertTrue(result["available"])
        self.assertGreater(len(result["_daily_results_internal"]), 0)

    async def test_background_job_reports_real_stages_and_result(self):
        service = QuantResearchWorkspaceService()
        updates = []

        async def fake_run(request, progress_callback=None):
            self.assertEqual(request["experiment_id"], "weekly_momentum_baseline_v1")
            await progress_callback(36, "normalising", "已读取日线")
            await progress_callback(88, "report", "正在生成报告")
            return {"report_version": "test", "status": "RESEARCH_ONLY"}

        def record_update(kind, job_id, **values):
            updates.append({"kind": kind, "job_id": job_id, **values})
            return values

        with (
            patch.object(service, "run_experiment", side_effect=fake_run),
            patch("services.quant_research_workspace.update_job", side_effect=record_update),
        ):
            await service._run_job("research_test", {"experiment_id": "weekly_momentum_baseline_v1"})

        self.assertEqual(updates[0]["status"], "running")
        self.assertTrue(any(item.get("phase") == "normalising" and item.get("progress") == 36 for item in updates))
        self.assertEqual(updates[-1]["status"], "completed")
        self.assertEqual(updates[-1]["result"]["report_version"], "test")


if __name__ == "__main__":
    unittest.main()
