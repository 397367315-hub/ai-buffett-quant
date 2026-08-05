import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from quant.market_cache import load_quant_market_snapshot, save_quant_market_snapshot
from quant.persistence import (
    create_strategy_persisted,
    delete_strategy_persisted,
    hydrate_strategy_store,
    list_strategies_persisted,
    update_strategy_persisted,
)
from quant.schemas import StrategyCreate
from quant.signals import QuantSignalService
from quant.storage import QuantJsonStore
from quant.templates import BUILTIN_STRATEGIES
from api.quant_routes import get_sectors


def _strategy_payload(name: str) -> dict:
    return {
        "name": name,
        "active": True,
        "scan_schedule": "manual",
        "filter": {"logic": "AND", "rules": []},
        "entry": {"logic": "AND", "rules": [
            {"type": "change_pct", "operator": "gte", "value": 1},
        ]},
        "exit": {
            "stop_loss_pct": 5,
            "take_profit_pct": 15,
            "max_holding_days": 20,
            "rules": [],
        },
        "position": {
            "method": "equal_weight",
            "max_holdings": 5,
            "max_position_pct": 20,
            "fixed_amount": None,
        },
    }


class QuantPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        database_path = Path(self.tempdir.name) / "quant.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.store = QuantJsonStore(Path(self.tempdir.name) / "json")
        self.patches = [
            patch("quant.persistence.async_session", self.session_factory),
            patch("quant.persistence.quant_store", self.store),
            patch("quant.market_cache.async_session", self.session_factory),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.engine.dispose()
        self.tempdir.cleanup()

    async def test_legacy_strategy_and_builtins_survive_an_empty_process_cache(self):
        legacy = {
            "id": "strat_legacy",
            "created_at": "2026-08-01T10:00:00+08:00",
            "updated_at": "2026-08-01T10:00:00+08:00",
            **_strategy_payload("旧版文件策略"),
        }
        self.store.write("strategies", {"version": 1, "strategies": [legacy]})

        hydrated = await hydrate_strategy_store()

        self.assertEqual(
            {item["id"] for item in hydrated},
            {"strat_legacy", *(item["id"] for item in BUILTIN_STRATEGIES)},
        )
        self.assertTrue(all(item["builtin"] for item in hydrated if item["id"].startswith("strat_builtin")))

        self.store.write("strategies", {"version": 2, "strategies": []})
        restarted = await list_strategies_persisted()

        self.assertEqual({item["id"] for item in restarted}, {item["id"] for item in hydrated})
        self.assertEqual(len(self.store.read("strategies")["strategies"]), len(hydrated))

    async def test_database_crud_updates_cache_and_protects_builtins(self):
        await hydrate_strategy_store()
        created = await create_strategy_persisted(_strategy_payload("持久化测试策略"))
        updated = await update_strategy_persisted(created["id"], {"active": False})

        self.assertFalse(updated["active"])
        self.assertFalse(updated["builtin"])
        self.assertIn(created["id"], {item["id"] for item in self.store.read("strategies")["strategies"]})

        with self.assertRaisesRegex(ValueError, "内置策略不能删除"):
            await delete_strategy_persisted(BUILTIN_STRATEGIES[0]["id"])
        self.assertTrue(await delete_strategy_persisted(created["id"]))
        self.assertNotIn(created["id"], {item["id"] for item in await list_strategies_persisted()})

    async def test_complete_market_snapshot_round_trips_through_database(self):
        snapshot = {
            "stocks": [{"code": "600519", "name": "贵州茅台", "price": 1500.0}],
            "total": 1,
            "source": "eastmoney",
            "data_date": "2026-08-05",
            "is_realtime": True,
            "fetched_at": "2026-08-05T10:00:00+08:00",
            "complete": True,
        }

        self.assertTrue(await save_quant_market_snapshot(snapshot))
        cached = await load_quant_market_snapshot()

        self.assertEqual(cached["data_date"], "2026-08-05")
        self.assertEqual(cached["source"], "cache")
        self.assertFalse(cached["is_realtime"])
        self.assertEqual(cached["stocks"][0]["code"], "600519")

    async def test_older_complete_snapshot_cannot_replace_latest_trading_day(self):
        latest = {
            "stocks": [{"code": "600519", "name": "贵州茅台", "price": 1500.0}],
            "source": "eastmoney",
            "data_date": "2026-08-05",
            "complete": True,
        }
        older = {
            "stocks": [{"code": "600519", "name": "贵州茅台", "price": 1400.0}],
            "source": "eastmoney",
            "data_date": "2026-08-04",
            "complete": True,
        }

        self.assertTrue(await save_quant_market_snapshot(latest))
        self.assertFalse(await save_quant_market_snapshot(older))

        cached = await load_quant_market_snapshot()
        self.assertEqual(cached["data_date"], "2026-08-05")
        self.assertEqual(cached["stocks"][0]["price"], 1500.0)


class QuantSnapshotFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_signal_scan_uses_database_snapshot_when_live_and_file_cache_fail(self):
        service = QuantSignalService()
        persistent = {
            "stocks": [{"code": "600519", "name": "贵州茅台", "price": 1500.0}],
            "total": 1,
            "source": "cache",
            "data_date": "2026-08-05",
            "is_realtime": False,
            "fetched_at": "2026-08-05T15:00:00+08:00",
            "complete": True,
        }
        with tempfile.TemporaryDirectory() as directory:
            store = QuantJsonStore(Path(directory))
            with (
                patch("quant.signals.quant_store", store),
                patch(
                    "quant.signals.collector.fetch_quant_market_snapshot",
                    new=AsyncMock(side_effect=RuntimeError("upstream unavailable")),
                ),
                patch(
                    "quant.signals.load_quant_market_snapshot",
                    new=AsyncMock(return_value=persistent),
                ),
            ):
                snapshot, stale, warning = await service._market_snapshot(force=True)

        self.assertTrue(stale)
        self.assertEqual(snapshot["data_date"], "2026-08-05")
        self.assertIn("最近交易日缓存", warning)

    async def test_sector_options_use_database_snapshot_when_live_source_fails(self):
        persistent = {
            "stocks": [
                {"code": "600519", "sector": "白酒"},
                {"code": "000001", "sector": "银行"},
            ],
            "source": "cache",
            "data_date": "2026-08-05",
        }
        with tempfile.TemporaryDirectory() as directory:
            store = QuantJsonStore(Path(directory))
            with (
                patch("api.quant_routes.quant_store", store),
                patch(
                    "api.quant_routes.collector.fetch_quant_market_snapshot",
                    new=AsyncMock(side_effect=RuntimeError("upstream unavailable")),
                ),
                patch(
                    "api.quant_routes.load_quant_market_snapshot",
                    new=AsyncMock(return_value=persistent),
                ),
            ):
                result = await get_sectors(limit=300)

        self.assertEqual(
            result["data"]["sectors"],
            [
                {"code": "白酒", "name": "白酒", "type": "industry"},
                {"code": "银行", "name": "银行", "type": "industry"},
            ],
        )
        self.assertEqual(result["data"]["source"], "cache")


class BuiltinStrategyValidationTests(unittest.TestCase):
    def test_builtin_short_and_long_strategies_are_valid_and_disclose_target(self):
        self.assertEqual({item["horizon"] for item in BUILTIN_STRATEGIES}, {"short", "long"})
        for strategy in BUILTIN_STRATEGIES:
            StrategyCreate.model_validate(strategy)
            self.assertEqual(strategy["target_win_rate"], [70, 90])
            self.assertIn("不承诺收益", strategy["validation_note"])

        short = next(item for item in BUILTIN_STRATEGIES if item["horizon"] == "short")
        short_types = {rule["type"] for rule in short["filter"]["rules"]}
        self.assertTrue({"ocf_to_profit", "debt_ratio"}.issubset(short_types))

        long = next(item for item in BUILTIN_STRATEGIES if item["horizon"] == "long")
        long_entry_types = [rule["type"] for rule in long["entry"]["rules"]]
        self.assertNotIn("sector_strength", long_entry_types)


if __name__ == "__main__":
    unittest.main()
