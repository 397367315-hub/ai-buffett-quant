import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from services.market_way_v4 import (
    MarketWayV4Service,
    build_market_way_v4,
    build_momentum_state,
    build_truth_layer,
)
from services.truth_layer import PointInTimeGuard, detect_data_conflicts


NOW = datetime(2026, 8, 18, 14, 40, tzinfo=ZoneInfo("Asia/Shanghai"))


def payload():
    return {
        "available": True,
        "meta": {
            "contract_version": "market-workbench-v4.0.0",
            "decision_date": "2026-08-18",
            "calculated_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
            "is_realtime": True,
            "coverage_pct": 85,
            "source": "eastmoney",
        },
        "market_state": {
            "state_code": "S2", "state_label": "趋势启动", "score": 72,
            "coverage_pct": 85, "confidence_pct": 78,
            "dimensions": [
                {"id": "breadth", "score": 68},
                {"id": "capital", "score": 74},
            ],
        },
        "structure_health": {"score": 70, "coverage_pct": 80, "evidence": ["核心与中军同步"]},
        "crowding_risk": {"score": 35, "evidence": ["拥挤尚低"]},
        "volume_price_alignment": {"score": 70, "status": "aligned", "coverage_pct": 80},
        "contradiction_evolution": {"qualitative_shift": "not_confirmed"},
        "headline_metrics": {"limit_up": 42, "failed_limit_rate": 14},
        "market_cognition": {
            "principal_contradiction": {"statement": "增量资金能否继续支持主线", "evidence": []},
            "facts": ["成交与宽度同步改善"],
            "practice_hypothesis": {"falsification": ["宽度明显收缩"]},
        },
        "main_lines": [{
            "name": "人工智能", "strength_score": 78, "breadth": 72,
            "change_pct": 2.4, "main_net_inflow": 120000000,
            "lifecycle": "强化", "leader": {"boards": 2},
        }],
        "daily_short_term_recommendations": {"candidates": [{
            "code": "600001", "name": "测试股份", "sector": "人工智能",
            "market_cap": 50000000000,
        }]},
        "candidates": [],
        "decision_2026": {
            "market_regime": {"label": "趋势初期"},
            "trading_permission": {"code": "ALLOW", "label": "允许进攻", "allows_new_position": True, "max_total_position_pct": 60, "reasons": []},
            "decision_windows": [{"id": "tail_1440", "label": "尾盘决策窗口", "time": "14:40", "status": "进行中"}],
            "conditional_orders": {"execute": [], "prepare": [], "alert": []},
            "why_not_buy": {"reasons": []},
            "exit_engine": {"logic_failure": [], "market_deterioration": []},
        },
        "audit": {
            "component_dates": {"topic_strength": "2026-08-18", "index_history": "2026-08-18"},
            "data_sources": ["eastmoney"], "stale_components": [],
        },
    }


class TruthLayerTests(unittest.TestCase):
    def test_point_in_time_guard_rejects_future_available_evidence(self):
        result = PointInTimeGuard.evaluate({
            "event_time": "2026-08-18T10:00:00+08:00",
            "publish_time": "2026-08-18T10:00:00+08:00",
            "available_time": "2026-08-18T11:00:01+08:00",
            "snapshot_time": "2026-08-18T10:30:00+08:00",
        }, "2026-08-18T10:30:00+08:00")
        self.assertFalse(result["allowed"])
        self.assertIn("FUTURE_AVAILABLE_DATA", result["violations"])

    def test_conflict_prefers_higher_grade_and_records_values(self):
        records = [
            {"status": "ACCEPTED", "fact_key": "price", "source_key": "eastmoney", "source_grade": "A", "value": 10},
            {"status": "ACCEPTED", "fact_key": "price", "source_key": "research_media", "source_grade": "B", "value": 11},
        ]
        conflicts = detect_data_conflicts(records, "2026-08-18")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["preferred_source"], "eastmoney")

    def test_truth_layer_exposes_four_times_and_downgrades_mixed_dates(self):
        source = payload()
        source["audit"]["component_dates"]["stock_selection"] = "2026-08-17"
        truth = build_truth_layer(source, {"available": False, "policy_items": []}, generated_at=NOW)
        self.assertIn(truth["status"], {"LIMITED", "PASS"})
        self.assertTrue(all(item.get("event_time") for item in truth["records"]))
        self.assertTrue(any("stock_selection" in warning for warning in truth["warnings"]))


class MarketWayTests(unittest.TestCase):
    def test_builds_chain_momentum_and_no_policy_buy_signal(self):
        result = build_market_way_v4(payload(), {"available": False, "policy_items": []}, generated_at=NOW)
        self.assertEqual(len(result["chain"]), 10)
        self.assertIn(result["momentum"]["state"], {"启势", "顺势", "蓄势", "分势", "无序"})
        self.assertEqual(result["national_direction_radar"]["policy_source_available"], False)
        self.assertTrue(any("政策" in item for item in result["boundaries"]))

    def test_momentum_downgrades_after_confirmed_structural_shift(self):
        source = payload()
        source["contradiction_evolution"]["qualitative_shift"] = "confirmed"
        result = build_momentum_state(source, [])
        self.assertEqual(result["order_state"], "结构瓦解")
        self.assertEqual(result["state"], "退势")


class MarketWayStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_data_status_reconciles_a_completed_backfill_warning(self):
        service = MarketWayV4Service()
        service._last_pipeline = {"market_history": {"status": "cache_incomplete"}}
        service._refresh_status = {
            "status": "completed_with_gaps",
            "stage": "completed",
            "progress": 100,
            "message": "V4已更新，仍有数据源在后台重试",
            "warnings": ["市场情绪历史正在补采，完成后自动重建"],
            "history_backfill": {"run_id": 17, "status": "running"},
        }
        service._market_history_status = AsyncMock(return_value={
            "status": "available",
            "history_count": 30,
            "amount_history_count": 30,
            "turnover_history_count": 30,
        })

        with patch(
            "services.history_cache.history_cache.latest_backfill_status",
            new=AsyncMock(return_value={"run_id": 17, "status": "completed"}),
        ):
            result = await service.data_status()

        self.assertEqual(result["refresh_job"]["status"], "completed")
        self.assertEqual(result["refresh_job"]["warnings"], [])
        self.assertEqual(result["refresh_job"]["history_backfill"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
