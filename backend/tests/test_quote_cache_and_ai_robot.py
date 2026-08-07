import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import AIRobotJournal, AIRobotPick, AIRobotRun, StockDailyBar
from services.ai_robot import AIRobotService, stock_selection_agents
from services.data_collector import is_a_share_market_session
from services.quote_cache import QuoteSnapshotService, collector


class QuoteSnapshotCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_non_trading_session_returns_latest_verified_cached_quote(self):
        async with self.session_factory() as session:
            session.add(StockDailyBar(
                stock_code="600519",
                stock_name="贵州茅台",
                market="SH",
                trade_date=date(2026, 7, 31),
                close_price=1418.5,
                change_pct=1.2,
                change_amount=16.8,
                source="closing_snapshot",
            ))
            await session.commit()

        service = QuoteSnapshotService()
        sunday = datetime(2026, 8, 2, 21, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with (
            patch("services.quote_cache.shanghai_now", return_value=sunday),
            patch.object(collector, "fetch_stock_quotes", new_callable=AsyncMock) as fetch_live,
        ):
            result = await service.fetch(["600519"], self.session_factory)

        fetch_live.assert_not_awaited()
        self.assertEqual(result["data_date"], "2026-07-31")
        self.assertEqual(result["source"], "cache")
        self.assertFalse(result["is_realtime"])
        self.assertTrue(result["cache_used"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["stocks"][0]["price"], 1418.5)
        self.assertEqual(result["stocks"][0]["cache_trade_date"], "2026-07-31")

    async def test_market_close_is_not_labeled_realtime_after_1500(self):
        timezone = ZoneInfo("Asia/Shanghai")

        self.assertTrue(is_a_share_market_session(datetime(2026, 8, 3, 15, 0, tzinfo=timezone)))
        self.assertFalse(is_a_share_market_session(datetime(2026, 8, 3, 15, 1, tzinfo=timezone)))


class AIRobotSimulationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_patch = patch("services.ai_robot.async_session", self.session_factory)
        self.session_patch.start()
        self.service = AIRobotService()
        self.sector = {
            "key": "energy",
            "label": "电力/能源",
            "criteria": [],
            "board": {"code": "BK0428", "name": "电力行业", "board_type": "industry"},
        }

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()

    async def _create_run(self, *, previous_price=None, previous_on=None):
        async with self.session_factory() as session:
            if previous_price is not None:
                previous = AIRobotRun(pool_type="short", status="completed", progress=100)
                session.add(previous)
                await session.flush()
                session.add(AIRobotPick(
                    run_id=previous.id,
                    pool_type="short",
                    sector_key="energy",
                    sector_label="电力/能源",
                    code="600519",
                    name="贵州茅台",
                    selected_price=previous_price,
                    selected_on=previous_on,
                    simulated_shares=100,
                    state="new",
                ))
            current = AIRobotRun(pool_type="short", status="queued", progress=0)
            session.add(current)
            await session.commit()
            await session.refresh(current)
            return current.id

    async def test_sector_resolution_uses_bounded_rankings_when_full_directory_fails(self):
        industry = [{"code": "BK0475", "name": "银行", "main_net_inflow": 1}]
        concept = [{"code": "BK0816", "name": "人工智能", "main_net_inflow": 1}]
        with (
            patch.object(collector, "fetch_all_industry_flow", new_callable=AsyncMock, side_effect=RuntimeError),
            patch.object(collector, "fetch_all_concept_flow", new_callable=AsyncMock, side_effect=RuntimeError),
            patch.object(collector, "fetch_industry_flow", new_callable=AsyncMock, return_value=industry) as fallback_industry,
            patch.object(collector, "fetch_concept_flow", new_callable=AsyncMock, return_value=concept) as fallback_concept,
        ):
            sectors = await self.service._resolve_sectors()

        fallback_industry.assert_awaited_once_with(page_size=100)
        fallback_concept.assert_awaited_once_with(page_size=100)
        by_key = {item["key"]: item for item in sectors}
        self.assertEqual(by_key["finance_dividend"]["board"]["code"], "BK0475")
        self.assertEqual(by_key["ai_compute"]["board"]["code"], "BK0816")

    async def _execute_with_recommendation(self, run_id, *, data_date, price):
        result = {
            "data_date": data_date,
            "is_realtime": False,
            "source": "test",
            "recommendations": [{
                "code": "600519",
                "name": "贵州茅台",
                "price": price,
                "score": 88,
                "confidence": 0.82,
                "verdict": "通过",
                "agents": {},
            }],
        }
        with (
            patch.object(self.service, "_resolve_sectors", new_callable=AsyncMock, return_value=[self.sector]),
            patch.object(self.service, "_core_codes", new_callable=AsyncMock, return_value=set()),
            patch.object(stock_selection_agents, "run", new_callable=AsyncMock, return_value=result),
        ):
            await self.service._execute(run_id)
        async with self.session_factory() as session:
            run = await session.get(AIRobotRun, run_id)
            pick = (await session.execute(
                select(AIRobotPick).where(AIRobotPick.run_id == run_id)
            )).scalar_one()
        return run, pick

    async def test_new_pick_simulates_exactly_100_shares_at_verified_price(self):
        run_id = await self._create_run()

        run, pick = await self._execute_with_recommendation(
            run_id, data_date="2026-08-03", price=12.35,
        )

        self.assertEqual(run.status, "completed")
        self.assertEqual(pick.state, "new")
        self.assertEqual(pick.simulated_shares, 100)
        self.assertEqual(pick.selected_price, 12.35)
        self.assertEqual(pick.selected_on, date(2026, 8, 3))
        async with self.session_factory() as session:
            journal = (await session.execute(
                select(AIRobotJournal).where(AIRobotJournal.run_id == run_id)
            )).scalar_one()
        self.assertIn("新调入 1 只", journal.action_summary)
        self.assertEqual(journal.picks_snapshot[0]["shares"], 100)
        self.assertIn("贵州茅台", journal.decision_reason)

    async def test_daily_journal_records_actual_portfolio_pnl_reflection(self):
        run_id = await self._create_run()
        await self._execute_with_recommendation(run_id, data_date="2026-08-03", price=12.35)
        performance = {
            "positions": 1, "priced_positions": 1, "waiting_positions": 0,
            "quote_unavailable_positions": 0, "simulated_shares": 100,
            "cost_value": 1235.0, "market_value": 1300.0, "pnl": 65.0,
            "pnl_pct": 5.26, "winners": 1, "losers": 0,
        }
        dashboard = {
            "updated_at": "2026-08-04T16:50:00+08:00",
            "quote": {"data_date": "2026-08-04", "source": "cache", "is_realtime": False},
            "pools": {
                "short": {
                    "run": {"id": run_id},
                    "performance": performance,
                    "picks": [{
                        "code": "600519", "latest_price": 13.0, "market_value": 1300.0,
                        "pnl": 65.0, "pnl_pct": 5.26, "price_status": "available",
                    }],
                },
            },
        }

        await self.service._record_performance_journals(dashboard)

        async with self.session_factory() as session:
            journal = (await session.execute(
                select(AIRobotJournal).where(AIRobotJournal.run_id == run_id)
            )).scalar_one()
        self.assertIn("浮盈 65.00 元", journal.pnl_reflection)
        self.assertEqual(journal.metrics["performance"]["simulated_shares"], 100)
        self.assertEqual(journal.picks_snapshot[0]["pnl"], 65.0)

    async def test_retained_pick_keeps_original_simulated_cost(self):
        run_id = await self._create_run(
            previous_price=9.8,
            previous_on=date(2026, 7, 1),
        )

        _, pick = await self._execute_with_recommendation(
            run_id, data_date="2026-08-03", price=12.35,
        )

        self.assertEqual(pick.state, "retained")
        self.assertEqual(pick.simulated_shares, 100)
        self.assertEqual(pick.selected_price, 9.8)
        self.assertEqual(pick.selected_on, date(2026, 7, 1))

    async def test_undated_quote_does_not_create_a_simulated_cost(self):
        run_id = await self._create_run()

        run, pick = await self._execute_with_recommendation(
            run_id, data_date=None, price=12.35,
        )

        self.assertEqual(run.summary["waiting_for_price"], 1)
        self.assertIsNone(pick.selected_price)
        self.assertIsNone(pick.selected_on)
        view = self.service._pick_view(pick, {"price": 12.35})
        self.assertEqual(view["price_status"], "waiting")
        self.assertIsNone(view["cost_value"])
        self.assertIsNone(view["pnl"])


if __name__ == "__main__":
    unittest.main()
