import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import PersonalPoolItem
from seed_data import seed
from services.data_collector import collector
from services.personal_portfolio import PersonalPortfolioService, _health, normalize_pool


class PersonalPortfolioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.seed_session_patch = patch("seed_data.async_session", self.session_factory)
        self.personal_session_patch = patch("services.personal_portfolio.async_session", self.session_factory)
        self.seed_init_patch = patch("seed_data.init_db", new=AsyncMock())
        self.positions_patch = patch(
            "seed_data.settings.personal_positions_json",
            json.dumps([
                {"code": "601611", "name": "中国核建", "cost": 10, "stop_loss": 9, "targets": [12, 14], "position_pct": 10, "status": "持有"},
                {"code": "000539", "name": "粤电力A", "cost": 6, "stop_loss": 5, "targets": [7, 8], "position_pct": 20, "status": "持有"},
                {"code": "000725", "name": "京东方A", "cost": 4, "stop_loss": 3.5, "targets": [], "position_pct": 15, "status": "建议减仓换电力"},
            ], ensure_ascii=False),
        )
        self.seed_session_patch.start()
        self.personal_session_patch.start()
        self.seed_init_patch.start()
        self.positions_patch.start()

    async def asyncTearDown(self):
        self.seed_session_patch.stop()
        self.personal_session_patch.stop()
        self.seed_init_patch.stop()
        self.positions_patch.stop()
        await self.engine.dispose()

    async def test_seed_imports_five_layer_pool_without_duplicate_position_rows(self):
        await seed()
        await seed()
        async with self.session_factory() as session:
            count = (await session.execute(select(func.count()).select_from(PersonalPoolItem))).scalar_one()
            core_positions = (await session.execute(
                select(PersonalPoolItem).where(
                    PersonalPoolItem.pool_key == "core",
                    PersonalPoolItem.position_pct.is_not(None),
                )
            )).scalars().all()
            leader_positions = (await session.execute(
                select(PersonalPoolItem).where(
                    PersonalPoolItem.pool_key == "leaders",
                    PersonalPoolItem.position_pct.is_not(None),
                )
            )).scalars().all()

        self.assertEqual(count, 41)
        self.assertEqual({item.code for item in core_positions}, {"601611", "000539"})
        self.assertEqual(leader_positions, [])

    async def test_overview_enriches_verified_quotes_and_calculates_health(self):
        await seed()

        async def fake_quotes(codes):
            return {
                "stocks": [
                    {"code": code, "name": f"核验{code}", "price": 10.0, "change_pct": 1.5, "quote_timestamp": 0}
                    for code in codes
                ],
                "source": "test",
                "complete": True,
                "data_date": None,
                "is_realtime": False,
                "fetched_at": "2026-08-04T10:00:00",
            }

        service = PersonalPortfolioService()
        with patch.object(collector, "fetch_stock_quotes", new=fake_quotes):
            overview = await service.overview()

        self.assertEqual(overview["quote"]["source"], "test")
        self.assertTrue(overview["quote"]["complete"])
        self.assertEqual(overview["summary"]["holding_count"], 3)
        self.assertEqual(sum(pool["count"] for pool in overview["pools"]), 41)
        self.assertTrue(all(item["name_verified"] for item in overview["items"]))

    async def test_pool_alias_and_health_flag_missing_risk_controls(self):
        self.assertEqual(normalize_pool("长期观察池"), "watchlist")
        with self.assertRaises(ValueError):
            normalize_pool("不存在的股票池")
        items = [{
            "pool": "core",
            "position_pct": 85,
            "status": "holding",
            "stop_loss": None,
            "targets": [],
            "industry": "测试行业",
            "sector": "",
            "display_name": "测试股",
            "quote_available": True,
        }]
        health = _health(items, {"complete": True})
        self.assertEqual(health["holding_count"], 1)
        self.assertTrue(any(issue["title"] == "持仓风控参数不完整" for issue in health["issues"]))
        self.assertTrue(any(issue["title"] == "现金安全垫不足" for issue in health["issues"]))

    async def test_partial_update_preserves_unmentioned_risk_controls(self):
        service = PersonalPortfolioService()
        created = await service.create_item({
            "pool": "watchlist",
            "code": "600519",
            "name": "贵州茅台",
            "cost": 1500,
            "position_pct": 10,
            "stop_loss": 1380,
            "targets": [1680, 1800],
            "max_position": 15,
            "thesis": "原始研究依据",
        })

        updated = await service.update_item(created["item"]["id"], {"thesis": "复核后的研究依据"})

        self.assertEqual(updated["thesis"], "复核后的研究依据")
        self.assertEqual(updated["cost"], 1500)
        self.assertEqual(updated["position_pct"], 10)
        self.assertEqual(updated["stop_loss"], 1380)
        self.assertEqual(updated["targets"], [1680, 1800])
        self.assertEqual(updated["max_position"], 15)

    async def test_analysis_add_is_idempotent_and_preserves_manual_controls(self):
        service = PersonalPortfolioService()
        created = await service.create_item({
            "pool": "watchlist",
            "code": "000001",
            "name": "平安银行",
            "stop_loss": 9.2,
            "targets": [12.5],
            "thesis": "手工研究依据",
            "source": "user",
        })

        repeated = await service.create_item({
            "pool": "watchlist",
            "code": "SZ000001",
            "name": "平安银行",
            "industry": "银行",
            "thesis": "量化策略命中",
            "source": "quant_signal",
        })

        self.assertTrue(created["created"])
        self.assertFalse(repeated["created"])
        self.assertEqual(repeated["item"]["id"], created["item"]["id"])
        self.assertEqual(repeated["item"]["stop_loss"], 9.2)
        self.assertEqual(repeated["item"]["targets"], [12.5])
        self.assertEqual(repeated["item"]["thesis"], "量化策略命中")
        async with self.session_factory() as session:
            count = (await session.execute(select(func.count()).select_from(PersonalPoolItem))).scalar_one()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
