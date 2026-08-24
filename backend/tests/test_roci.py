import asyncio
import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

from fastapi.routing import APIRoute
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from main import app
from models import (
    RociAction,
    RociBattlefieldSnapshot,
    RociForce,
    RociPatternHit,
    RociPrimaryContradiction,
    RociSkill,
    RociSkillRun,
    StockDailyBar,
)
from roci.adapters import (
    _load_workbench_context,
    cache_freshness,
    load_daily_context,
    roci_cache_has_usable_recommendations,
)
from roci.engines import UNKNOWN, action, asymmetry, battlefield, risk_adapted_recommendations, risk_pricing, stress_test
from roci.registry import all_skill_definitions
from roci.service import RociService


def _context() -> dict:
    return {
        "workbench": {
            "market_state": {"score": 62, "label": "偏强"},
            "headline_metrics": {"up_count": 3200, "down_count": 1400, "limit_up": 60, "limit_down": 8},
            "structure_health": {"score": 66, "coverage_pct": 90},
            "crowding_risk": {"score": 42, "coverage_pct": 80},
            "main_lines": [{"name": "测试主线", "strength_score": 70}],
            "candidates": [{"code": "600519", "name": "测试股票", "sector": "测试", "score": 72}],
        },
        "forecast": {
            "timeline": [{"probability": 58}],
            "data_cutoff_time": "2026-08-24T10:00:00",
        },
        "microstructure": {},
        "reflexivity": {},
        "daily": {
            "data_date": "2026-08-24",
            "source": "test",
            "bars": [
                {"trade_date": "2026-08-20", "close": 10, "change_pct": 1, "volume": 100},
                {"trade_date": "2026-08-21", "close": 10.2, "change_pct": 2, "volume": 110},
                {"trade_date": "2026-08-24", "close": 10.1, "change_pct": -1, "volume": 120},
            ],
        },
        "quant_snapshot": {},
        "cache_used": False,
        "collected_at": "2026-08-24T10:00:00",
        "source_status": {"workbench": "available", "forecast_v5": "available", "daily_bars": "available"},
    }


class RociContractTests(unittest.TestCase):
    def test_registry_has_84_unique_skills_and_eighteen_shadow_skills(self):
        skills = all_skill_definitions()
        self.assertEqual(len(skills), 84)
        self.assertEqual(len({item["skill_id"] for item in skills}), 84)
        self.assertEqual(
            {item["skill_id"] for item in skills if 67 <= int(item["skill_id"].split("S")[-1]) <= 76},
            {f"ROCI-S{number:03d}" for number in range(67, 77)},
        )
        self.assertEqual(
            {item["skill_id"] for item in skills if 90 <= int(item["skill_id"].split("S")[-1]) <= 97},
            {f"ROCI-S{number:03d}" for number in range(90, 98)},
        )
        self.assertTrue(all(item["status"] == "SHADOW" for item in skills if 67 <= int(item["skill_id"].split("S")[-1]) <= 76 or 90 <= int(item["skill_id"].split("S")[-1]) <= 97))

    def test_missing_inputs_remain_unknown(self):
        empty = {}
        self.assertEqual(battlefield(empty)["regime"], UNKNOWN)
        self.assertEqual(risk_pricing(empty, battlefield(empty), {})["status"], UNKNOWN)
        self.assertEqual(stress_test(empty)["state"], UNKNOWN)
        self.assertEqual(asymmetry(empty, battlefield(empty), {})["status"], UNKNOWN)
        self.assertEqual(action(battlefield(empty), {"candidate_key": UNKNOWN}, {}, {}, {}, 0)["action"], "NO_TRADE")

    def test_shadow_patterns_cannot_become_active_confirmation(self):
        result = action(
            {"regime": "NORMAL_OFFENSE"},
            {"candidate_key": "板块扩散能否继续", "confidence": 80, "what_would_resolve": [], "what_would_worsen": []},
            {"status": "MOSTLY_PRICED"},
            {"state": "RESILIENT"},
            {"status": "FAVORABLE"},
            90,
            has_active_confirmation=False,
        )
        self.assertNotEqual(result["action"], "ATTACK")
        self.assertIn("ROCI-S067至ROCI-S076", result["shadow_excluded"])

    def test_cache_uses_short_session_ttl_and_longer_off_hours_ttl(self):
        self.assertFalse(cache_freshness(
            {"generated_at": "2026-08-24T09:58:00"},
            datetime(2026, 8, 24, 10, 0),
        )[0])
        self.assertTrue(cache_freshness(
            {"generated_at": "2026-08-24T15:58:00"},
            datetime(2026, 8, 24, 16, 0),
        )[0])

    def test_empty_recommendation_snapshot_is_not_a_valid_roci_cache(self):
        self.assertFalse(roci_cache_has_usable_recommendations({
            "opportunities": {
                "risk_adapted": {
                    "status": UNKNOWN,
                    "sectors": [],
                    "stocks": [],
                },
            },
        }))
        self.assertTrue(roci_cache_has_usable_recommendations({
            "opportunities": {
                "risk_adapted": {
                    "status": "AVAILABLE",
                    "sectors": [{"name": "主线"}],
                    "stocks": [],
                },
            },
        }))
    def test_risk_pricing_keeps_unpriced_risk_as_highest_priority(self):
        context = _context()
        context["workbench"]["crowding_risk"] = {"score": 82}
        context["workbench"]["structure_health"] = {"score": 74}
        result = risk_pricing(context, battlefield(context), {})
        self.assertEqual(result["status"], "NOT_PRICED")
        self.assertEqual(result["risks"][0]["state"], "NOT_PRICED")

    def test_market_stress_uses_equal_weight_series_when_stock_bars_are_absent(self):
        context = _context()
        context["daily"] = {
            "data_date": "2026-08-24",
            "bars": [],
            "bars_by_code": {
                "600001": [
                    {"trade_date": "2026-08-18", "change_pct": -2.0, "close": 98},
                    {"trade_date": "2026-08-19", "change_pct": 1.0, "close": 99},
                    {"trade_date": "2026-08-20", "change_pct": -2.5, "close": 96.5},
                    {"trade_date": "2026-08-21", "change_pct": 0.5, "close": 97},
                    {"trade_date": "2026-08-24", "change_pct": 1.2, "close": 98.2},
                ],
                "600002": [
                    {"trade_date": "2026-08-18", "change_pct": -1.0, "close": 99},
                    {"trade_date": "2026-08-19", "change_pct": 2.0, "close": 101},
                    {"trade_date": "2026-08-20", "change_pct": -2.0, "close": 99},
                    {"trade_date": "2026-08-21", "change_pct": 0.0, "close": 99},
                    {"trade_date": "2026-08-24", "change_pct": 1.0, "close": 100},
                ],
            },
        }
        result = stress_test(context)
        self.assertEqual(result["scope"], "market")
        self.assertTrue(all(item["evidence"][0]["source"] == "stock_daily_bars_equal_weight" for item in result["events"]))

    def test_unknown_risk_pricing_does_not_promote_direction_only_context(self):
        context = _context()
        context["workbench"].pop("crowding_risk")
        context["workbench"].pop("structure_health")
        context["workbench"]["market_state"] = {"score": 80}
        result = risk_pricing(context, battlefield(context), {})
        self.assertEqual(result["status"], UNKNOWN)

    def test_risk_adapted_recommendations_explain_sectors_and_quality_stocks(self):
        context = _context()
        context["workbench"]["main_lines"] = [
            {
                "name": "健康主线", "classification": "核心主线", "lifecycle": "观察",
                "strength_score": 82, "breadth": 71, "main_net_inflow": 800_000_000,
                "risk_flags": [], "leader": {"code": "600001", "name": "主线龙头"},
            },
            {
                "name": "退潮热点", "classification": "短期热点", "lifecycle": "退潮",
                "strength_score": 75, "breadth": 18, "main_net_inflow": -500_000_000,
                "risk_flags": ["板块宽度不足50%"], "leader": {"code": "600002", "name": "退潮龙头"},
            },
        ]
        context["workbench"]["daily_short_term_recommendations"] = {
            "data_date": "2026-08-24",
            "source": "test_pit",
            "candidates": [{
                "code": "600001", "name": "主线龙头", "sector": "健康主线", "score": 78,
                "confidence_pct": 95, "volume_ratio": 1.8, "risk": "未触发主要风险阈值",
                "score_breakdown": {
                    "market_fit": 65, "sector_strength": 82, "capital": 80,
                    "profitability": 78, "risk_safety": 84, "volume_ratio": 90, "trend": 72,
                },
                "profitability": {"status": "financial_pit_cache", "roe": 16, "pe": 22, "disclosed_at": "2026-08-20"},
                "reasons": ["板块资金持续净流入", "盈利质量已核验"],
                "invalidation_conditions": ["板块资金转为持续流出"],
                "data_date": "2026-08-24", "is_realtime": False, "source": "test_pit",
            }],
        }
        battle = battlefield(context)
        pricing = risk_pricing(context, battle, {})
        result = risk_adapted_recommendations(context, battle, pricing, {"state": "RESILIENT"}, {"action": "PROBE"})
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["sectors"][0]["name"], "健康主线")
        self.assertEqual(result["stocks"][0]["code"], "600001")
        self.assertEqual(result["stocks"][0]["role"], "板块领导股")
        self.assertTrue(result["stocks"][0]["reasons"])
        self.assertEqual(result["avoided_sectors"][0]["name"], "退潮热点")
        self.assertIn("生命周期为退潮", result["avoided_sectors"][0]["risk_flags"])

    def test_risk_adapted_recommendations_do_not_invent_missing_sector_data(self):
        result = risk_adapted_recommendations({}, {"regime": UNKNOWN}, {"status": UNKNOWN}, {"state": UNKNOWN})
        self.assertEqual(result["status"], UNKNOWN)
        self.assertEqual(result["sectors"], [])
        self.assertEqual(result["stocks"], [])
        self.assertIn("main_lines", result["missing_inputs"])

    def test_both_documented_api_prefixes_are_registered(self):
        def collect_paths(routes):
            paths = set()
            for route in routes:
                if isinstance(route, APIRoute):
                    paths.add(route.path)
                original = getattr(route, "original_router", None)
                if original is not None:
                    paths.update(item.path for item in original.routes if isinstance(item, APIRoute))
                nested = getattr(route, "routes", None)
                if nested:
                    paths.update(collect_paths(nested))
            return paths

        paths = collect_paths(app.routes)
        self.assertIn("/api/v1/roci/dashboard", paths)
        self.assertIn("/api/roci/dashboard", paths)
        self.assertIn("/api/v1/roci/lab/skills/{skill_id}/promote", paths)
        self.assertIn("/api/roci/lab/skills/{skill_id}/promote", paths)
        self.assertIn("/api/v1/roci/recommendations", paths)
        self.assertIn("/api/roci/recommendations", paths)


class RociPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_patch = patch("roci.service.async_session", self.session_factory)
        self.context_patch = patch("roci.service.load_existing_context", new=AsyncMock(return_value=_context()))
        self.session_patch.start()
        self.context_patch.start()

    async def asyncTearDown(self):
        self.context_patch.stop()
        self.session_patch.stop()
        await self.engine.dispose()

    async def test_disabled_skill_survives_registry_reseed_and_promotion_is_rejected(self):
        service = RociService()
        await service.ensure_initialized()
        disabled = await service.disable_skill("ROCI-S067")
        self.assertEqual(disabled["status"], "DISABLED")
        await service.ensure_initialized()
        async with self.session_factory() as session:
            row = await session.get(RociSkill, "ROCI-S067")
            self.assertEqual(row.status, "DISABLED")
            self.assertFalse(row.enabled)
        promotion = await service.promote_skill("ROCI-S067", {"metrics": {"hit_rate": 99}})
        self.assertEqual(promotion["status"], "REJECTED")
        self.assertIsNone(promotion.get("metrics"))

    async def test_recomputing_same_snapshot_is_idempotent(self):
        service = RociService()
        first = await service.build(force=True, persist=True)
        second = await service.build(force=True, persist=True)
        self.assertEqual(first["snapshot_key"], second["snapshot_key"])
        async with self.session_factory() as session:
            snapshot_key = first["snapshot_key"]
            counts = {
                "snapshots": await session.scalar(select(func.count()).select_from(RociBattlefieldSnapshot).where(RociBattlefieldSnapshot.snapshot_key == snapshot_key)),
                "skills": await session.scalar(select(func.count()).select_from(RociSkillRun).where(RociSkillRun.snapshot_key == snapshot_key)),
                "forces": await session.scalar(select(func.count()).select_from(RociForce).where(RociForce.snapshot_key == snapshot_key)),
                "contradictions": await session.scalar(select(func.count()).select_from(RociPrimaryContradiction).where(RociPrimaryContradiction.snapshot_key == snapshot_key)),
                "actions": await session.scalar(select(func.count()).select_from(RociAction).where(RociAction.snapshot_key == snapshot_key)),
                "hits": await session.scalar(select(func.count()).select_from(RociPatternHit).where(RociPatternHit.snapshot_key == snapshot_key)),
            }
        self.assertEqual(counts["snapshots"], 1)
        self.assertEqual(counts["skills"], 84)
        self.assertEqual(counts["forces"], 4)
        self.assertEqual(counts["contradictions"], 1)
        self.assertEqual(counts["actions"], 1)
        self.assertGreater(counts["hits"], 0)

    async def test_concurrent_same_snapshot_persistence_is_serialized(self):
        service = RociService()
        payloads = await asyncio.gather(
            service.build(force=True, persist=True),
            service.build(force=True, persist=True),
            service.build(force=True, persist=True),
        )
        snapshot_key = payloads[0]["snapshot_key"]
        self.assertTrue(all(item["snapshot_key"] == snapshot_key for item in payloads))
        async with self.session_factory() as session:
            self.assertEqual(
                await session.scalar(select(func.count()).select_from(RociBattlefieldSnapshot).where(RociBattlefieldSnapshot.snapshot_key == snapshot_key)),
                1,
            )
            self.assertEqual(
                await session.scalar(select(func.count()).select_from(RociSkillRun).where(RociSkillRun.snapshot_key == snapshot_key)),
                84,
            )

    async def test_disabled_skill_is_not_triggered_or_reported_as_contribution(self):
        service = RociService()
        await service.ensure_initialized()
        await service.disable_skill("ROCI-S027")
        payload = await service.build(force=True, persist=False)
        disabled = next(item for item in payload["skills"]["items"] if item["skill_id"] == "ROCI-S027")
        self.assertEqual(disabled["status"], "DISABLED")
        self.assertFalse(disabled["enabled"])
        self.assertFalse(disabled["triggered"])
        self.assertIsNone(disabled["contribution"])
        self.assertNotIn("ROCI-S027", payload["agent_report"]["skills_used"])

    async def test_non_active_skills_never_have_contribution(self):
        service = RociService()
        payload = await service.build(force=True, persist=False)
        self.assertTrue(payload["skills"]["items"])
        self.assertTrue(all(item["contribution"] is None for item in payload["skills"]["items"] if item["status"] != "ACTIVE"))

    async def test_battlefield_history_is_limited_to_one_entry_per_trade_date(self):
        service = RociService()
        async with self.session_factory() as session:
            for index, day in enumerate(("2026-08-18", "2026-08-19", "2026-08-19", "2026-08-20")):
                session.add(RociBattlefieldSnapshot(
                    snapshot_key=f"history-{index}",
                    trade_date=datetime.fromisoformat(day).date(),
                    data_cutoff_time=datetime(2026, 8, 20, 10 + index),
                    regime="MIXED",
                    market_reward="观察",
                    market_penalty="风险",
                    payload={},
                ))
            await session.commit()
        history = await service._battlefield_history(symbol=None, current_key="current", limit=10)
        self.assertEqual([item["trade_date"] for item in history], ["2026-08-18", "2026-08-19", "2026-08-20"])

    async def test_market_replay_does_not_select_a_stock_snapshot(self):
        service = RociService()
        async with self.session_factory() as session:
            session.add_all([
                RociBattlefieldSnapshot(
                    snapshot_key="stock-replay",
                    symbol="600001",
                    trade_date=date(2026, 8, 20),
                    data_cutoff_time=datetime(2026, 8, 20, 15),
                    regime="STOCK_ONLY",
                    payload={"scope": "stock"},
                ),
                RociBattlefieldSnapshot(
                    snapshot_key="market-replay",
                    symbol=None,
                    trade_date=date(2026, 8, 20),
                    data_cutoff_time=datetime(2026, 8, 20, 15),
                    regime="MARKET_ONLY",
                    payload={"scope": "market"},
                ),
            ])
            await session.commit()
        replay = await service.replay(symbol=None, trade_date=date(2026, 8, 20))
        self.assertEqual(replay["scope"], "market")
        self.assertTrue(replay["replay"]["no_future_data"])

    async def test_market_daily_context_uses_sql_equal_weight_aggregation(self):
        rows = []
        for stock_code, changes in {
            "600001": (1.0, -2.0, 3.0),
            "600002": (-1.0, 2.0, 1.0),
        }.items():
            for offset, change_pct in enumerate(changes):
                rows.append(StockDailyBar(
                    stock_code=stock_code,
                    stock_name=f"测试{stock_code}",
                    market="SH",
                    trade_date=date(2026, 8, 20 + offset),
                    open_price=10,
                    close_price=10 + change_pct,
                    high_price=11,
                    low_price=9,
                    volume=100 + offset,
                    amount=100000 + offset,
                    change_pct=change_pct,
                    source="test",
                ))
        async with self.session_factory() as session:
            session.add_all(rows)
            await session.commit()
        with patch("roci.adapters.async_session", self.session_factory):
            context = await load_daily_context(symbol=None, limit=90)
        self.assertEqual(context["source"], "stock_daily_bars_equal_weight")
        self.assertEqual(context["data_date"], "2026-08-22")
        self.assertEqual(len(context["market_bars"]), 3)
        self.assertLessEqual(len(context["market_bars"]), 90)
        self.assertEqual(context["market_bars"][0]["sample_size"], 2)
        self.assertAlmostEqual(context["market_bars"][0]["change_pct"], 0.0)


class RociAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_workbench_cache_is_used_when_live_refresh_fails(self):
        cached_workbench = {
            "available": True,
            "main_lines": [{"name": "缓存主线", "strength_score": 72}],
            "meta": {"decision_date": "2026-08-24"},
        }
        with patch("roci.adapters._cached", new=AsyncMock(return_value=cached_workbench)), patch(
            "roci.adapters.market_decision_workbench_service.get",
            new=AsyncMock(return_value={"__adapter_error__": "workbench:TimeoutError"}),
        ):
            payload, cache_used = await _load_workbench_context(force=True)
        self.assertEqual(payload["main_lines"][0]["name"], "缓存主线")
        self.assertTrue(cache_used)


if __name__ == "__main__":
    unittest.main()
