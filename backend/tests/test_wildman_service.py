import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import (
    MarketDataCache,
    StockUniverseSnapshot,
    WildmanCandidate,
    WildmanMainline,
    WildmanMarketCycle,
    WildmanStockRole,
    WildmanTradeReview,
)
from services.wildman_service import WildmanService


class WildmanServicePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        tables = [
            MarketDataCache.__table__,
            StockUniverseSnapshot.__table__,
            WildmanMarketCycle.__table__,
            WildmanMainline.__table__,
            WildmanStockRole.__table__,
            WildmanCandidate.__table__,
            WildmanTradeReview.__table__,
        ]
        async with self.engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        self.session_patch = patch("services.wildman_service.async_session", self.session_factory)
        self.session_patch.start()

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()

    @staticmethod
    def _bar(index: int):
        return SimpleNamespace(
            close_price=10 + index * 0.02,
            open_price=10 + index * 0.01,
            high_price=10.3 + index * 0.02,
            low_price=9.8 + index * 0.02,
            volume=1_000_000 + index * 1_000,
            change_pct=None if index == 29 else 0.8,
        )

    async def test_dashboard_adapts_and_persists_compact_rule_snapshots(self):
        service = WildmanService()
        target = date(2026, 9, 19)
        raw_stock = {
            "code": "600001",
            "name": "测试股份",
            "sector": "机器人",
            "continuous_days": 2,
            "failed_attempts": 0,
            "first_limit_time": "09:35",
            "market_cap": 12_000_000_000,
            "change_pct": 10.0,
        }
        snapshot = {
            "market": {
                "max_limit_height": 2,
                "limit_up_count": 12,
                "limit_down_count": 1,
                "new_theme_first_boards": 3,
                "limit_down_decreasing": True,
                "ladder_complete": False,
                "core_midcap_support": True,
                "leader_nuked": False,
                "mid_level_limit_down_spread": False,
                "first_divergence": False,
                "one_word_limit_count": 0,
                "leader_volume_acceleration": False,
                "rear_all_red": False,
                "leader_broken": False,
                "cold_rear_supplement": False,
            },
            "themes": [{
                "theme_id": "机器人",
                "theme_name": "机器人",
                "limit_up_count": 3,
                "max_limit_height": 2,
                "has_pioneer": True,
                "has_core_midcap": True,
                "old_dragon_active": None,
                "leader_premium": True,
                "support_promotion": True,
                "core_midcap_stable": True,
                "fund_return": None,
                "reversal": None,
                "supplement_started": True,
                "resists_market_drop": None,
                "rows": [raw_stock],
            }],
            "up_rows": [raw_stock],
            "down_rows": [],
            "failed_rows": [],
            "source": {"limit_up": "test", "limit_down": "test", "failed": "test", "data_date": target.isoformat()},
        }

        with (
            patch.object(service, "_target_date", new=AsyncMock(return_value=target)),
            patch.object(service, "_snapshot", new=AsyncMock(return_value=snapshot)),
            patch.object(service, "_bars", new=AsyncMock(return_value={"600001": [self._bar(index) for index in range(30)]})),
            patch.object(service, "_auctions", new=AsyncMock(return_value={})),
        ):
            payload = await service.dashboard(refresh=True, exclude_star_market=False, exclude_gem=False)

        self.assertEqual(payload["candidates"][0]["symbol"], "600001")
        self.assertTrue(payload["mainlines"][0]["steps"]["step4"])
        async with self.session_factory() as session:
            counts = {
                model.__tablename__: (await session.execute(select(func.count()).select_from(model))).scalar_one()
                for model in (WildmanMarketCycle, WildmanMainline, WildmanStockRole, WildmanCandidate)
            }
            candidate = (await session.execute(select(WildmanCandidate))).scalar_one()

        self.assertEqual(counts, {
            "wildman_market_cycle": 1,
            "wildman_mainline": 1,
            "wildman_stock_role": 1,
            "wildman_candidate": 1,
        })
        compact_snapshot = json.dumps(candidate.source_snapshot_json, ensure_ascii=False)
        self.assertNotIn("timeline", compact_snapshot)
        self.assertNotIn("raw_trades", compact_snapshot)

    async def test_universe_metadata_uses_latest_pit_row_on_or_before_target(self):
        service = WildmanService()
        async with self.session_factory() as session:
            session.add_all([
                StockUniverseSnapshot(stock_code="001216", stock_name="华瓷股份", exchange="SZ", trade_date=date(2026, 9, 10), industry="家居用品", market_cap=4_200_000_000, source="numcat"),
                StockUniverseSnapshot(stock_code="001216", stock_name="华瓷股份", exchange="SZ", trade_date=date(2026, 9, 20), industry="未来行业", market_cap=9_900_000_000, source="future"),
            ])
            await session.commit()

        metadata = await service._universe_metadata(["001216"], date(2026, 9, 18))

        self.assertEqual(metadata["001216"]["sector"], "家居用品")
        self.assertEqual(metadata["001216"]["market_cap"], 4_200_000_000)
        self.assertEqual(metadata["001216"]["trade_date"], "2026-09-10")

    def test_cleared_resistance_is_not_reused_as_forward_target(self):
        service = WildmanService()
        rows = [self._bar(index) for index in range(30)]
        for row in rows[:-1]:
            row.high_price = 9.8
        rows[-1].close_price = 10.5
        rows[-1].high_price = 10.6
        rows[-1].change_pct = 6.0

        facts = service._stock_facts(
            {"code": "001216", "name": "华瓷股份", "continuous_days": 1},
            rows,
            None,
            {"limit_up_count": 3, "max_limit_height": 1},
            {},
        )

        self.assertTrue(facts["break_neckline"])
        self.assertIsNone(facts["pressure_price"])


if __name__ == "__main__":
    unittest.main()
