import unittest
from datetime import date
from unittest.mock import AsyncMock, PropertyMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import MarketEmotionReplaySnapshot
from seed_data import _seed_emotion_replay
from services.data_collector import collector
from services.replay_workspace import ReplayWorkspaceService


class ReplayWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.service = ReplayWorkspaceService()
        self.session_patch = patch("services.replay_workspace.async_session", self.session_factory)
        self.session_patch.start()

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()

    async def _insert_snapshot(self, payload=None, source="user_imported_csv"):
        async with self.session_factory() as session:
            session.add(MarketEmotionReplaySnapshot(
                trade_date=date(2026, 9, 4),
                week="2026/W36",
                month="2026/09",
                source=source,
                payload=payload or {
                    "up_count": 2444,
                    "down_count": 2914,
                    "limit_up_count": 39,
                    "limit_down_count": 9,
                    "failed_limit_count": 48,
                    "failed_limit_rate": 55.17,
                    "auction_main_net_inflow": 1_209_414_911,
                },
            ))
            await session.commit()

    async def test_csv_snapshot_keeps_aggregate_provenance_without_numcat(self):
        await self._insert_snapshot()
        provider_type = type(__import__(
            "market_data.numcat.market_provider", fromlist=["numcat_market_provider"],
        ).numcat_market_provider)

        with patch.object(provider_type, "configured", new_callable=PropertyMock, return_value=False):
            payload = await self.service.get(date(2026, 9, 4), history_days=10)

        self.assertEqual(payload["trade_date"], "2026-09-04")
        self.assertEqual(payload["emotion"]["field_sources"]["up_count"], "user_imported_csv")
        self.assertEqual(payload["sections"]["market_summary"]["source"], "user_imported_csv")
        self.assertEqual(payload["sections"]["auction_limit"]["source"], "user_imported_csv")
        self.assertEqual(payload["sections"]["auction_limit"]["rows"], [])
        self.assertEqual(
            payload["sections"]["auction_limit"]["summary"]["auction_main_net_inflow"],
            1_209_414_911,
        )
        self.assertFalse(payload["quality"]["provider_queried"])

    async def test_historical_snapshot_does_not_call_vendor_until_refresh(self):
        await self._insert_snapshot()
        module = __import__("services.replay_workspace", fromlist=["numcat_market_provider"])
        provider_type = type(module.numcat_market_provider)

        with (
            patch.object(provider_type, "configured", new_callable=PropertyMock, return_value=True),
            patch.object(module.numcat_market_provider, "market_emotion", new_callable=AsyncMock) as emotion,
        ):
            payload = await self.service.get(date(2026, 9, 4), refresh=False, history_days=10)

        emotion.assert_not_awaited()
        self.assertFalse(payload["quality"]["provider_queried"])
        self.assertIn("点击刷新", payload["sections"]["anomaly"]["error"])

    async def test_refresh_uses_public_limit_pools_without_numcat(self):
        await self._insert_snapshot()
        provider_type = type(__import__(
            "market_data.numcat.market_provider", fromlist=["numcat_market_provider"],
        ).numcat_market_provider)
        pool = lambda code, name, direction: {
            "stocks": [{
                "code": code, "name": name, "price": 10,
                "change_pct": 10 if direction == "up" else -10,
                "continuous_days": 2 if direction == "up" else 0,
                "amount": 1000000,
            }],
            "total": 1,
            "trade_date": "20260904",
            "source": "eastmoney_limit_pool",
        }
        with (
            patch.object(provider_type, "configured", new_callable=PropertyMock, return_value=False),
            patch.object(collector, "fetch_limit_up_pool", new_callable=AsyncMock, return_value=pool("600001", "上涨测试", "up")),
            patch.object(collector, "fetch_limit_down_pool", new_callable=AsyncMock, return_value=pool("600002", "下跌测试", "down")),
            patch.object(collector, "fetch_failed_limit_pool", new_callable=AsyncMock, return_value=pool("600003", "炸板测试", "failed")),
        ):
            payload = await self.service.get(date(2026, 9, 4), refresh=True, history_days=10)

        self.assertEqual(payload["sections"]["limit_up"]["rows"][0]["code"], "600001")
        self.assertEqual(payload["sections"]["limit_down"]["rows"][0]["code"], "600002")
        self.assertEqual(payload["sections"]["failed_limit"]["rows"][0]["code"], "600003")
        self.assertEqual(payload["sections"]["limit_up"]["source"], "eastmoney_limit_pool")
        self.assertTrue(payload["quality"]["detail_queried"])

    async def test_derived_counts_are_exposed_with_field_provenance(self):
        await self._insert_snapshot({
            "up_count": 2444,
            "down_count": 2914,
            "stock_count": 5358,
            "touched_limit_up_count": 87,
            "failed_limit_count": 48,
            "limit_up_order_amount": 1_714_384_022,
            "limit_up_order_amount_0920": 911_978_328,
            "yesterday_limit_up_count": 44,
        })
        provider_type = type(__import__(
            "market_data.numcat.market_provider", fromlist=["numcat_market_provider"],
        ).numcat_market_provider)
        with patch.object(provider_type, "configured", new_callable=PropertyMock, return_value=False):
            payload = await self.service.get(date(2026, 9, 4), history_days=10)

        emotion = payload["emotion"]
        self.assertEqual(emotion["flat_count"], 0)
        self.assertAlmostEqual(emotion["failed_limit_rate"], 55.17, places=2)
        self.assertEqual(emotion["promotion_candidate_count"], 44)
        self.assertEqual(emotion["limit_up_order_amount_after_0920"], 802405694)
        self.assertEqual(emotion["field_sources"]["flat_count"], "derived_from_verified_counts")
        self.assertEqual(emotion["field_sources"]["limit_up_order_amount_after_0920"], "derived_from_verified_counts")

    async def test_ai_analysis_strips_markdown_decoration(self):
        payload = {
            "trade_date": "2026-09-04",
            "updated_at": "2026-09-04T15:00:00+08:00",
            "source": "user_imported_csv",
            "emotion": {"up_count": 2444, "down_count": 2914},
            "sections": {"strong_sectors": {"rows": []}, "topic_rotation": {"rows": []}, "auction_limit": {"summary": {}}},
            "quality": {},
        }
        self.service.get = AsyncMock(return_value=payload)
        module = __import__("services.replay_workspace", fromlist=["ai_service"])

        with (
            patch.object(module.ai_service, "client", object()),
            patch.object(module.ai_service, "generate", new_callable=AsyncMock, return_value="## **复盘结论**\n__市场偏弱__\n```text\n忽略围栏\n```"),
        ):
            result = await self.service.analyze(date(2026, 9, 4), use_ai=True)

        self.assertTrue(result["ai_generated"])
        self.assertNotIn("**", result["interpretation"])
        self.assertNotIn("__", result["interpretation"])
        self.assertNotIn("```", result["interpretation"])

    async def test_compact_cache_limits_each_ranking_to_forty_rows(self):
        await self.service._persist_compact(
            date(2026, 9, 4),
            {"up_count": 1, "down_count": 2},
            {
                "limit_up": {
                    "available": True,
                    "count": 100,
                    "summary": {"count": 100},
                    "source": "numcat_limit_pool",
                    "data_date": "2026-09-04",
                    "quality": "verified",
                    "rows": [{"code": f"{index:06d}", "name": "测试"} for index in range(100)],
                    "updated_at": "2026-09-04T15:00:00+08:00",
                }
            },
            "numcat",
        )

        async with self.session_factory() as session:
            row = await session.get(MarketEmotionReplaySnapshot, date(2026, 9, 4))
            cached = row.payload["workspace_cache"]["sections"]["limit_up"]["rows"]
        self.assertEqual(len(cached), 40)

    async def test_aggregate_read_cannot_downgrade_cached_stock_detail(self):
        await self.service._persist_compact(
            date(2026, 9, 4),
            {"up_count": 10},
            {"limit_up": {
                "available": True,
                "summary": {"count": 1},
                "source": "eastmoney_limit_pool",
                "data_date": "2026-09-04",
                "quality": "verified",
                "rows": [{"code": "600001", "name": "明细"}],
            }},
            "eastmoney_limit_pool",
        )
        await self.service._persist_compact(
            date(2026, 9, 4),
            {"up_count": 10},
            {"limit_up": {
                "available": True,
                "summary": {"count": 1},
                "source": "daily_emotion_aggregate",
                "data_date": "2026-09-04",
                "quality": "verified_aggregate",
                "rows": [],
            }},
            "daily_emotion_aggregate",
        )

        async with self.session_factory() as session:
            row = await session.get(MarketEmotionReplaySnapshot, date(2026, 9, 4))
            cached = row.payload["workspace_cache"]["sections"]["limit_up"]
        self.assertEqual(cached["rows"][0]["code"], "600001")


class ReplaySeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_seed_refresh_preserves_existing_workspace_cache(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        try:
            async with factory() as session:
                session.add(MarketEmotionReplaySnapshot(
                    trade_date=date(2026, 9, 4),
                    source="numcat",
                    payload={"workspace_cache": {"sections": {"limit_up": {"rows": [{"code": "600001"}]}}}},
                ))
                await session.commit()

                with patch("seed_data._load_list", return_value=[{
                    "trade_date": "2026-09-04",
                    "week": "2026/W36",
                    "month": "2026/09",
                    "source": "user_imported_csv",
                    "up_count": 2444,
                }]):
                    inserted = await _seed_emotion_replay(session)
                await session.commit()

                row = await session.get(MarketEmotionReplaySnapshot, date(2026, 9, 4))
                self.assertEqual(inserted, 0)
                self.assertEqual(row.payload["up_count"], 2444)
                self.assertEqual(
                    row.payload["workspace_cache"]["sections"]["limit_up"]["rows"][0]["code"],
                    "600001",
                )
                self.assertIn("numcat", row.source)
                self.assertIn("user_imported_csv", row.source)
        finally:
            await engine.dispose()
