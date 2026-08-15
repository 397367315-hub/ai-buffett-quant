import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api import routes
from database import Base
from models import PersonalSystemConfig
from services.macro_dashboard import (
    MacroDashboardService,
    collector as macro_collector,
    macro_policy_news_collector,
)
from services.personal_analytics import PersonalAnalyticsService
from services.report_calendar import ReportCalendarService


class MacroDashboardCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_sources_reuse_labeled_cache_without_advancing_snapshot_time(self):
        cached_at = "2026-08-03T15:20:00+08:00"
        cached = {
            "updated_at": "2026-08-04T08:00:00+08:00",
            "snapshot_updated_at": cached_at,
            "global_markets": [{
                "key": "sp500", "label": "标普500", "value": 6300,
                "change_pct": 0.5, "available": True, "source": "新浪财经",
            }],
            "economic_calendar": [{
                "title": "非农就业", "country": "美国", "country_code": "USD",
                "impact": "高", "event_at": "2026-08-07T20:30:00+08:00",
                "forecast": "", "previous": "", "source": "calendar",
            }],
            "domestic_liquidity": {
                "northbound": {
                    "available": True, "date": "2026-08-03",
                    "net_inflow": 1_200_000_000, "consecutive_inflow_days": 2,
                    "source": "eastmoney",
                },
                "turnover": {
                    "available": True, "date": "2026-08-03",
                    "sh_amount": 500_000_000_000, "sh_index": 3500,
                    "sh_change_pct": 0.3, "source": "东方财富",
                },
            },
            "policy": {
                "available": True,
                "summary": "缓存政策摘要",
                "international_items": [],
                "policy_items": [],
            },
        }
        service = MacroDashboardService()
        save = AsyncMock()
        with (
            patch.object(service, "_load_cache", new_callable=AsyncMock, return_value=cached),
            patch.object(service, "_save_cache", save),
            patch.object(service, "_global_markets", new_callable=AsyncMock, side_effect=RuntimeError),
            patch.object(service, "_economic_calendar", new_callable=AsyncMock, side_effect=RuntimeError),
            patch.object(macro_collector, "fetch_north_bound_daily", new_callable=AsyncMock, side_effect=RuntimeError),
            patch.object(macro_collector, "fetch_market_turnover", new_callable=AsyncMock, side_effect=RuntimeError),
            patch.object(macro_policy_news_collector, "get_context", new_callable=AsyncMock, side_effect=RuntimeError),
        ):
            result = await service.dashboard()

        self.assertTrue(result["cache_used"])
        self.assertEqual(result["snapshot_updated_at"], cached_at)
        self.assertEqual(result["source_status"]["新浪财经"], "cache")
        self.assertEqual(result["source_status"]["经济日历"], "cache")
        self.assertEqual(result["source_status"]["东方财富资金"], "cache")
        self.assertEqual(result["source_status"]["宏观政策快照"], "cache")
        self.assertEqual(result["domestic_liquidity"]["northbound"]["date"], "2026-08-03")
        self.assertIn(result["a_share_outlook"]["stance"], {"bullish", "neutral", "cautious"})
        self.assertIn("A股综合方向", result["a_share_outlook"]["headline"])
        self.assertGreater(result["a_share_outlook"]["data_points"], 0)
        self.assertEqual(save.await_args.args[0]["snapshot_updated_at"], cached_at)


class PersonalWorkspaceDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_report_calendar_falls_back_to_persisted_snapshot(self):
        cached_at = "2026-08-03T08:20:00+08:00"
        upcoming = [{
            "code": "600519", "name": "贵州茅台", "pool": "watchlist",
            "holding": False, "relation": "观察池", "report_type": "半年报",
            "publish_date": "2026-08-10", "actual_publish_date": None,
            "days_until": 6, "changed": False, "source": "缓存",
        }]
        published = [{
            "code": "600519", "name": "贵州茅台", "pool": "watchlist",
            "holding": False, "relation": "观察池", "report_type": "一季报",
            "notice_date": "2026-04-30", "report_date": "2026-03-31",
            "metrics": {}, "comparison": {}, "anomalies": [], "source": "缓存",
        }]
        async with self.session_factory() as session:
            session.add(PersonalSystemConfig(
                key="report_snapshot",
                payload={
                    "updated_at": "2026-08-04T08:20:00+08:00",
                    "snapshot_updated_at": cached_at,
                    "upcoming": upcoming,
                    "published": published,
                },
            ))
            await session.commit()

        service = ReportCalendarService()
        now = datetime(2026, 8, 4, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        relation = {
            "600519": {
                "name": "贵州茅台", "pool": "watchlist",
                "holding": False, "relation": "观察池",
            },
        }
        with (
            patch("services.report_calendar.async_session", self.session_factory),
            patch("services.report_calendar.shanghai_now", return_value=now),
            patch.object(service, "_personal_universe", new_callable=AsyncMock, return_value=(["600519"], relation)),
            patch.object(service, "_fetch_appointments", new_callable=AsyncMock, side_effect=RuntimeError),
            patch.object(service, "_fetch_published", new_callable=AsyncMock, side_effect=RuntimeError),
        ):
            result = await service.dashboard()

        self.assertTrue(result["cache_used"])
        self.assertEqual(result["snapshot_updated_at"], cached_at)
        self.assertEqual(result["source_status"], {"appointments": "cache", "financials": "cache"})
        self.assertEqual(result["upcoming"], upcoming)
        self.assertEqual(result["published"], published)

    async def test_loss_add_threshold_accepts_only_non_positive_percentages(self):
        service = PersonalAnalyticsService()
        with patch("services.personal_analytics.async_session", self.session_factory):
            config = await service.update_account_config({"loss_add_block_pct": -8})
            self.assertEqual(config["loss_add_block_pct"], -8)
            with self.assertRaises(ValueError):
                await service.update_account_config({"loss_add_block_pct": 1})

    async def test_stock_selection_keeps_verified_sector_directory_after_24_hours(self):
        sectors = [{
            "code": "BK0475",
            "name": "银行",
            "candidate_count": 42,
            "stock_count": 42,
            "count_source": "stock_universe",
            "change_pct": 0,
            "main_net_inflow": 0,
        }]
        async with self.session_factory() as session:
            session.add(PersonalSystemConfig(
                key="stock_selection_sector_directory_v1",
                payload={
                    "classification_refreshed_at": "2026-07-01T15:20:00",
                    "sectors": sectors,
                },
            ))
            await session.commit()

        with (
            patch("api.routes.async_session", self.session_factory),
            patch(
                "api.routes.load_quant_market_snapshot",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch.object(
                routes.collector,
                "fetch_intelligent_selection_sectors",
                new_callable=AsyncMock,
                return_value=sectors,
            ) as refresh,
        ):
            result = await routes.get_stock_selection_sectors()

        refresh.assert_awaited_once_with(seed_sectors=sectors)
        self.assertEqual(result["data"]["sectors"], sectors)
        self.assertTrue(result["data"]["directory_cache_used"])
        self.assertTrue(result["data"]["directory_stale"])


if __name__ == "__main__":
    unittest.main()
