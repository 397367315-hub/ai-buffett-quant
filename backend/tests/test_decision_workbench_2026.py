import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from services.decision_workbench_2026 import (
    DecisionWorkbench2026Service,
    build_decision_2026,
)


NOW = datetime(2026, 8, 18, 10, 40, tzinfo=ZoneInfo("Asia/Shanghai"))


def payload(*, permission_action="execute", sector_lifecycle="强化", alpha_change=5.0):
    result = {
        "available": True,
        "meta": {
            "contract_version": "market-workbench-v4.0.0",
            "decision_date": "2026-08-18",
            "calculated_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
            "is_realtime": True,
            "coverage_pct": 100,
        },
        "market_state": {
            "state_code": "S2", "state_label": "趋势启动", "score": 76,
            "coverage_pct": 100, "confidence_pct": 100,
            "dimensions": [
                {"id": "capital", "score": 72, "observed": True},
                {"id": "risk", "score": 74, "observed": True},
            ],
        },
        "structure_health": {"score": 72, "evidence": ["板块宽度改善"]},
        "volume_price_alignment": {"score": 68, "evidence": ["成交支持"]},
        "crowding_risk": {"score": 35, "evidence": ["拥挤风险低"]},
        "market_cognition": {"final_action": permission_action},
        "main_lines": [{
            "name": "机器人", "lifecycle": sector_lifecycle, "strength_score": 82,
            "breadth": 72, "change_pct": 2.0, "risk_flags": [],
        }],
        "daily_short_term_recommendations": {"candidates": [{
            "code": "600001", "name": "测试股份", "sector": "机器人",
            "price": 12.0, "change_pct": alpha_change, "volume_ratio": 1.8,
            "main_net_inflow": 100_000_000,
            "score_breakdown": {
                "sector_strength": 82, "capital": 76, "profitability": 72,
                "risk_safety": 78, "trend": 75,
            },
            "profitability": {"status": "financial_pit_cache", "pe": 24, "roe": 14},
            "risk": "未触发主要风险", "reasons": ["量价资金共振"],
            "invalidation_conditions": ["跌破结构支撑"],
        }]},
        "candidates": [{
            "code": "600001", "name": "测试股份", "sector": "机器人",
            "price": 12.0, "change_pct": alpha_change, "score": 80,
            "score_breakdown": {"sector_strength": 82, "trend": 75, "capital": 76},
            "execution_eligible": False, "stale": False, "data_date": "2026-08-18",
            "why_selected": ["个股跑赢板块"], "why_not_full": ["尚未经过策略触发"],
            "abandon_conditions": ["Alpha消失"], "source": "test",
        }],
        "adaptive_strategy_weights": {"weights": []},
        "strategy_health": [],
        "risk": {"market": [], "strategy": [], "stock": []},
    }
    result["decision_2026"] = build_decision_2026(result, now=NOW)
    return result


class DecisionWorkbench2026Tests(unittest.TestCase):
    def test_builds_permission_density_alpha_and_conditions(self):
        decision = payload()["decision_2026"]

        self.assertIn(decision["trading_permission"]["code"], {"ALLOW", "CAUTION"})
        self.assertIsNotNone(decision["opportunity_density"]["score"])
        self.assertEqual(decision["sector_map"][0]["permission"], "允许研究")
        candidate = decision["candidate_decisions"][0]
        self.assertEqual(candidate["beta_alpha"]["individual_alpha_pct"], 3.0)
        self.assertIsNone(candidate["beta_alpha"]["market_beta_pct"])
        self.assertEqual(candidate["emotion"]["boundary"], "情绪只描述热度，不直接产生买入信号。")
        self.assertNotEqual(candidate["execution"]["level"], "EXECUTE")

    def test_low_coverage_blocks_permission_without_default_scores(self):
        source = payload()
        source["market_state"]["coverage_pct"] = 35
        decision = build_decision_2026(source, now=NOW)

        self.assertEqual(decision["trading_permission"]["code"], "BLOCK")
        self.assertEqual(decision["trading_permission"]["max_total_position_pct"], 0)

    def test_trend_dynamic_weights_are_percentages_and_raise_trend_factors(self):
        source = payload()
        source["market_state"]["state_code"] = "S1"
        weights = build_decision_2026(source, now=NOW)["dynamic_weights"]["weights"]

        self.assertEqual(sum(weights.values()), 100)
        self.assertGreater(weights["sector"], 15)
        self.assertGreater(weights["alpha"], 15)
        self.assertGreater(weights["funding"], 10)
        self.assertGreater(weights["technical"], 10)


class DecisionWorkbenchSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_patch = patch("services.decision_workbench_2026.async_session", self.factory)
        self.session_patch.start()
        self.service = DecisionWorkbench2026Service()

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()

    async def test_window_snapshot_is_immutable_but_user_judgment_can_update(self):
        first_payload = payload()
        changed_payload = payload(alpha_change=-1.0)
        getter = AsyncMock(side_effect=[first_payload, changed_payload])
        with (
            patch("services.market_decision_workbench.market_decision_workbench_service.get", getter),
            patch("services.decision_workbench_2026.shanghai_now", return_value=NOW),
        ):
            first = await self.service.capture("morning_1040")
            second = await self.service.capture("morning_1040", user_judgment="人工保持谨慎")

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["snapshot_hash"], second["snapshot_hash"])
        self.assertEqual(second["user_judgment"], "人工保持谨慎")
        self.assertEqual(second["payload"]["decision_2026"]["candidate_decisions"][0]["change_pct"], 5.0)

    async def test_validation_records_market_downgrade_error(self):
        morning_payload = payload(permission_action="execute")
        late_payload = payload(permission_action="no_trade", sector_lifecycle="退潮", alpha_change=-1.0)
        getter = AsyncMock(side_effect=[morning_payload, late_payload])
        with (
            patch("services.market_decision_workbench.market_decision_workbench_service.get", getter),
            patch("services.decision_workbench_2026.shanghai_now", return_value=NOW),
        ):
            await self.service.capture("morning_1040")
            await self.service.capture("tail_1455")
            result = await self.service.validate()

        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["outcome"], "ERROR")
        self.assertIn("市场判断错误", {item["type"] for item in result["errors"]})

    async def test_close_review_accepts_verified_same_day_end_of_day_cache(self):
        close_payload = payload()
        close_payload["meta"]["is_realtime"] = False
        close_now = datetime(2026, 8, 18, 15, 55, tzinfo=ZoneInfo("Asia/Shanghai"))
        with (
            patch("services.market_decision_workbench.market_decision_workbench_service.get", AsyncMock(return_value=close_payload)),
            patch("services.decision_workbench_2026.shanghai_now", return_value=close_now),
        ):
            result = await self.service.capture("close_review")

        self.assertTrue(result["created"])
        self.assertFalse(result["is_realtime"])
        self.assertEqual(result["decision_date"], "2026-08-18")


if __name__ == "__main__":
    unittest.main()
