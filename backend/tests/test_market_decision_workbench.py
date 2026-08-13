import unittest
from datetime import date, timedelta

from services.market_decision_workbench import (
    MarketDecisionWorkbenchService,
    WORKBENCH_CONTRACT_VERSION,
    SCORE_VERSION,
    _adaptive_strategy_weights,
    assemble_workbench,
    calculate_market_state,
)


def index_history(days: int = 80) -> list[dict]:
    start = date(2026, 5, 25)
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "close": 3000 + index * 4,
        }
        for index in range(days)
    ]


def sentiment_history() -> list[dict]:
    return [
        {
            "trade_date": (date(2026, 7, 20) + timedelta(days=index)).isoformat(),
            "market_amount": 1_800_000_000_000 + index * 10_000_000_000,
        }
        for index in range(15)
    ]


def topic_snapshot() -> dict:
    return {
        "available": True,
        "data_date": "2026-08-12",
        "updated_at": "2026-08-12T15:10:00+08:00",
        "is_realtime": False,
        "source": "database_cache",
        "cache_hit": True,
        "market": {
            "sentiment": {
                "up": 4000,
                "down": 1300,
                "flat": 100,
                "up_ratio": 75.5,
                "breadth": "普涨",
            },
            "liquidity": {
                "market_amount": 2_200_000_000_000,
                "amount_complete": True,
            },
            "emotion": {
                "zt_count": 88,
                "dt_count": 3,
                "zb_count": 12,
                "break_rate": 12.0,
            },
            "top_sectors": [
                {"name": "通信", "main_net_inflow": 12_000_000_000},
                {"name": "电子", "main_net_inflow": 9_000_000_000},
                {"name": "半导体", "main_net_inflow": 6_000_000_000},
            ],
        },
        "topics": [
            {
                "name": "通信",
                "status": "强",
                "novelty": "延续",
                "strength_score": 92,
                "breadth": 78,
                "member_count": 6,
                "sector_change_pct": 3.2,
                "sector_main_net_inflow": 12_000_000_000,
                "evidence": "涨停联动6只，板块上涨宽度78%",
                "audit": {"gaps": []},
                "leader": {
                    "code": "600001",
                    "name": "测试龙头",
                    "price": 15.2,
                    "pct": 9.9,
                    "turnover": 8.0,
                    "return_5d_pct": 12.0,
                    "main_net_inflow": 300_000_000,
                    "overheated": False,
                    "heat_status": "可观察",
                    "boards": 2,
                    "data_gaps": [],
                    "intraday": {"above_vwap": True, "active_direction": "buy"},
                },
            },
            {
                "name": "电子",
                "status": "强",
                "novelty": "新出现",
                "strength_score": 84,
                "breadth": 68,
                "member_count": 4,
                "sector_change_pct": 2.2,
                "sector_main_net_inflow": 9_000_000_000,
                "evidence": "涨停联动4只，板块上涨宽度68%",
                "audit": {"gaps": []},
                "leader": {
                    "code": "000002",
                    "name": "测试次龙",
                    "price": 20.0,
                    "pct": 7.0,
                    "turnover": 11.0,
                    "return_5d_pct": 9.0,
                    "main_net_inflow": 100_000_000,
                    "overheated": False,
                    "heat_status": "可观察",
                    "boards": 1,
                    "data_gaps": [],
                    "intraday": {"above_vwap": True, "active_direction": "buy"},
                },
            },
        ],
        "data_quality": {
            "complete_market_snapshot": True,
            "missing_fields": [],
        },
    }


class MarketDecisionWorkbenchTests(unittest.TestCase):
    def test_market_state_uses_all_observed_dimensions(self):
        state = calculate_market_state(
            topic_snapshot(),
            index_history(),
            sentiment_history(),
            {},
        )

        self.assertEqual(state["version"], SCORE_VERSION)
        self.assertEqual(state["coverage_pct"], 100.0)
        self.assertEqual(len(state["dimensions"]), 7)
        self.assertTrue(all(item["observed"] for item in state["dimensions"]))
        self.assertGreaterEqual(state["score"], 65)
        self.assertIn(state["state_code"], {"S1", "S2"})

    def test_missing_dimensions_are_not_filled_with_neutral_score(self):
        snapshot = topic_snapshot()
        snapshot["market"]["liquidity"] = {}
        state = calculate_market_state(snapshot, [], [], {})
        dimensions = {item["id"]: item for item in state["dimensions"]}

        self.assertIsNone(dimensions["trend"]["score"])
        self.assertFalse(dimensions["trend"]["observed"])
        self.assertIsNone(dimensions["liquidity"]["score"])
        self.assertFalse(dimensions["liquidity"]["observed"])
        self.assertEqual(state["coverage_pct"], 65.0)
        self.assertNotEqual(dimensions["trend"]["score"], 50)
        self.assertIn("缺失维度不填50分", state["missing_policy"])

    def test_low_coverage_state_hides_observed_partial_average(self):
        snapshot = {
            "data_date": "2026-08-12",
            "market": {},
            "topics": [],
            "data_quality": {"complete_market_snapshot": False},
        }
        state = calculate_market_state(
            snapshot,
            [],
            [],
            {"warning": False, "consecutive_losses": 0},
        )

        self.assertEqual(state["coverage_pct"], 0.0)
        self.assertIsNone(state["observed_score"])
        self.assertIsNone(state["score"])
        self.assertEqual(state["state_code"], "S0")

        payload = assemble_workbench(snapshot, [], [], None, {})
        self.assertEqual(payload["risk"]["market"], ["市场风险字段待采集，当前不可判定"])
        self.assertEqual(payload["risk"]["stock"], ["个股风险需在候选生成后判定"])
        self.assertTrue(all(item["weight_pct"] == 0 for item in payload["adaptive_strategy_weights"]["weights"]))
        self.assertEqual(payload["market_cognition"]["dominant_aspect"]["direction"], "unknown")
        self.assertEqual(payload["execution_queue"]["phases"][-1]["display_status"], "市场不交易")
        self.assertTrue(all(item["status"] == "forbidden" for item in payload["strategy_selector"]["strategies"]))

    def test_previous_day_index_is_visible_but_excluded_from_score(self):
        history = index_history()[:-1]
        state = calculate_market_state(
            topic_snapshot(),
            history,
            sentiment_history(),
            {},
        )
        trend = next(item for item in state["dimensions"] if item["id"] == "trend")

        self.assertFalse(trend["observed"])
        self.assertIsNone(trend["score"])
        self.assertEqual(trend["metrics"]["data_date"], "2026-08-11")
        self.assertIn("跨日指数", trend["method"])
        self.assertEqual(state["coverage_pct"], 80.0)

    def test_stale_agent_candidates_are_observation_only(self):
        selection = {
            "id": 9,
            "data_date": "2026-08-10",
            "source": "eastmoney",
            "result": {
                "research_horizon": {"label": "未来一周"},
                "recommendations": [{
                    "code": "600519",
                    "name": "贵州茅台",
                    "sector": "白酒",
                    "price": 1500,
                    "change_pct": 1.2,
                    "score": 72,
                    "confidence": 60,
                    "agents": {
                        "technical": {"score": 80},
                        "capital": {"score": 70},
                        "risk": {"score": 75},
                        "supervisor": {
                            "summary": "结构较强",
                            "debate": {
                                "bull_points": ["趋势向上"],
                                "bear_points": ["估值偏高"],
                            },
                        },
                    },
                }],
            },
        }
        payload = assemble_workbench(
            topic_snapshot(),
            index_history(),
            sentiment_history(),
            selection,
            {},
            calculated_at="2026-08-12T15:30:00+08:00",
        )
        stale = next(item for item in payload["candidates"] if item["code"] == "600519")

        self.assertTrue(stale["stale"])
        self.assertFalse(stale["execution_eligible"])
        self.assertEqual(stale["pool"], "历史观察池")
        self.assertIn("stock_selection", payload["audit"]["stale_components"])
        self.assertEqual(payload["candidate_summary"]["execution_ready"], 0)

    def test_same_day_completed_tail_signal_enters_execution_pool(self):
        overnight = {
            "strategy": {"name": "14:55尾盘候选策略"},
            "latest_entry_run": {
                "id": 12,
                "strategy_name": "14:55尾盘候选策略",
                "status": "completed",
                "data_date": "2026-08-12",
                "qualified_count": 1,
                "candidates": [{
                    "code": "600010",
                    "name": "尾盘候选",
                    "sector": "通信",
                    "price": 8.8,
                    "change_pct": 3.5,
                    "score": 82,
                    "conditions": [{"label": "量比", "status": "passed"}],
                }],
                "data_quality": {"strategy": {"version": "3.0"}},
            },
        }
        payload = assemble_workbench(
            topic_snapshot(),
            index_history(),
            sentiment_history(),
            None,
            overnight,
        )
        candidate = next(item for item in payload["candidates"] if item["code"] == "600010")

        self.assertTrue(candidate["execution_eligible"])
        self.assertEqual(candidate["status"], "待竞价确认")
        self.assertEqual(candidate["pool"], "14:55执行池")
        self.assertEqual(payload["candidate_summary"]["execution_ready"], 1)
        self.assertEqual(payload["execution_queue"]["phases"][1]["scheduled_at"], "14:55")

    def test_same_day_agent_pick_still_requires_strategy_trigger(self):
        selection = {
            "id": 11,
            "data_date": "2026-08-12",
            "source": "eastmoney",
            "result": {
                "research_horizon": {"label": "未来一周"},
                "recommendations": [{
                    "code": "600036",
                    "name": "招商银行",
                    "sector": "银行",
                    "score": 70,
                    "agents": {
                        "technical": {"score": 70},
                        "capital": {"score": 65},
                        "risk": {"score": 80},
                        "supervisor": {"summary": "同日研究候选"},
                    },
                }],
            },
        }
        payload = assemble_workbench(
            topic_snapshot(),
            index_history(),
            sentiment_history(),
            selection,
            {},
        )
        candidate = next(item for item in payload["candidates"] if item["code"] == "600036")

        self.assertFalse(candidate["stale"])
        self.assertFalse(candidate["execution_eligible"])
        self.assertEqual(candidate["status"], "待策略触发")
        self.assertEqual(payload["candidate_summary"]["same_day_observation"], 3)

    def test_future_selection_snapshot_is_not_displayed(self):
        selection = {
            "id": 10,
            "data_date": "2026-08-13",
            "source": "eastmoney",
            "result": {
                "recommendations": [{"code": "600888", "name": "未来数据"}],
            },
        }
        payload = assemble_workbench(
            topic_snapshot(),
            index_history(),
            sentiment_history(),
            selection,
            {},
        )

        self.assertNotIn("600888", {item["code"] for item in payload["candidates"]})
        self.assertTrue(payload["audit"]["no_future_data"])

    def test_stale_tail_candidate_does_not_override_current_topic_candidate(self):
        overnight = {
            "latest_entry_run": {
                "id": 13,
                "strategy_name": "旧尾盘策略",
                "status": "completed",
                "data_date": "2026-08-11",
                "qualified_count": 1,
                "candidates": [{
                    "code": "600001",
                    "name": "测试龙头",
                    "sector": "通信",
                    "score": 99,
                }],
            },
        }
        payload = assemble_workbench(
            topic_snapshot(),
            index_history(),
            sentiment_history(),
            None,
            overnight,
        )
        candidate = next(item for item in payload["candidates"] if item["code"] == "600001")

        self.assertFalse(candidate["stale"])
        self.assertEqual(candidate["source"], "topic_strength")
        self.assertEqual(candidate["pool"], "主线观察池")

    def test_verified_cache_wins_over_degraded_refresh(self):
        cached = {
            "available": True,
            "meta": {"contract_version": WORKBENCH_CONTRACT_VERSION, "decision_date": "2026-08-12", "coverage_pct": 100},
            "market_state": {"score": 70, "dimensions": []},
            "audit": {"score_version": SCORE_VERSION},
        }
        candidate = {
            "available": True,
            "meta": {"decision_date": "2026-08-13", "coverage_pct": 10},
        }

        self.assertTrue(MarketDecisionWorkbenchService._prefer_cached(candidate, cached))
        retained = MarketDecisionWorkbenchService._retained_cache(cached, candidate)
        self.assertTrue(retained["meta"]["cache_used"])
        self.assertEqual(retained["meta"]["refresh_status"], "retained_verified_cache")
        self.assertIn("未用不完整结果覆盖", retained["audit"]["refresh_warning"])

    def test_new_complete_trade_date_replaces_older_cache(self):
        cached = {
            "available": True,
            "meta": {"contract_version": WORKBENCH_CONTRACT_VERSION, "decision_date": "2026-08-12", "coverage_pct": 100},
            "market_state": {"score": 70, "dimensions": []},
            "audit": {"score_version": SCORE_VERSION},
        }
        candidate = {
            "available": True,
            "meta": {"decision_date": "2026-08-13", "coverage_pct": 80},
        }

        self.assertFalse(MarketDecisionWorkbenchService._prefer_cached(candidate, cached))

    def test_legacy_low_coverage_score_cache_is_rejected(self):
        cached = {
            "available": True,
            "meta": {"contract_version": WORKBENCH_CONTRACT_VERSION, "decision_date": "2026-08-12", "coverage_pct": 10},
            "market_state": {"score": 100, "dimensions": [{}]},
            "audit": {"score_version": SCORE_VERSION},
        }

        self.assertFalse(MarketDecisionWorkbenchService._cache_contract_valid(cached))

    def test_v1_cache_is_rejected_after_v2_contract_bump(self):
        cached = {
            "available": True,
            "meta": {"contract_version": "market-workbench-v1.0.0", "decision_date": "2026-08-12", "coverage_pct": 100},
            "market_state": {"score": 70, "dimensions": []},
            "audit": {"score_version": "market-state-v1.0.0"},
        }
        self.assertFalse(MarketDecisionWorkbenchService._cache_contract_valid(cached))

    def test_v2_cognition_contract_exposes_no_trade_and_evidence_chain(self):
        snapshot = topic_snapshot()
        snapshot["market"]["sentiment"]["up_ratio"] = 30
        snapshot["market"]["emotion"]["break_rate"] = 42
        snapshot["topics"][0]["breadth"] = 30
        snapshot["topics"][1]["breadth"] = 28
        history = sentiment_history()
        for index, row in enumerate(history[-5:]):
            row["market_amount"] = 2_400_000_000_000 - index * 200_000_000_000
            row["failed_limit_rate"] = 15 + index * 8
        payload = assemble_workbench(snapshot, index_history(), history, None, {})

        self.assertIn(payload["market_cognition"]["final_action"], {"no_trade", "caution"})
        self.assertIn(payload["market_cognition"]["qualitative_shift"]["status"], {"not_confirmed", "warning", "confirmed"})
        self.assertIn("principal_contradiction", payload["market_cognition"])
        self.assertEqual(payload["audit"]["contract_version"], WORKBENCH_CONTRACT_VERSION)

    def test_strategy_health_without_forward_samples_is_recovery_not_failure(self):
        overnight = {
            "strategy_store": {"strategies": [{"id": "tail_1455", "name": "尾盘研究"}]},
            "runs": [],
            "positions": [],
        }
        payload = assemble_workbench(topic_snapshot(), index_history(), sentiment_history(), None, overnight)
        health = next(item for item in payload["strategy_health"] if item["id"] == "tail_1455")
        self.assertEqual(health["state"], "RECOVERY")
        self.assertIsNone(health["metrics"]["win_rate_pct"])

    def test_strategy_health_counts_legacy_records_using_default_strategy_fallback(self):
        overnight = {
            "strategy_store": {"strategies": [{"id": "overnight_review_v2", "name": "一夜持股"}]},
            "runs": [{"status": "completed", "data_quality": {}}],
            "positions": [
                {"pnl": 120.0, "audit": {}},
                {"pnl": -40.0},
            ],
        }
        payload = assemble_workbench(topic_snapshot(), index_history(), sentiment_history(), None, overnight)
        health = next(item for item in payload["strategy_health"] if item["id"] == "overnight_review_v2")
        self.assertEqual(health["metrics"]["sample_count"], 2)
        self.assertEqual(health["metrics"]["run_count"], 1)
        self.assertEqual(health["metrics"]["wins"], 1)
        self.assertEqual(health["metrics"]["losses"], 1)

    def test_adaptive_weights_map_overnight_health_to_real_strategy_id(self):
        payload = _adaptive_strategy_weights(
            {"state_code": "S2", "score": 75},
            {"score": 75},
            {"score": 25},
            [{"id": "overnight_review_v2", "state": "SUSPENDED"}],
            {"strategy": {"id": "overnight_review_v2"}},
        )
        weights = {item["strategy_id"]: item for item in payload["weights"]}
        self.assertEqual(weights["tail_1455"]["weight_pct"], 0)
        self.assertEqual(payload["health_adjustments"]["tail_1455"], "SUSPENDED")

    def test_contradiction_evolution_counts_only_trailing_streak(self):
        history = sentiment_history()
        values = [100, 90, 95, 85, 86]
        for row, amount in zip(history[-5:], values):
            row["market_amount"] = amount
        payload = assemble_workbench(topic_snapshot(), index_history(), history, None, {})
        item = next(
            row for row in payload["contradiction_evolution"]["quantitative_changes"]
            if row["id"] == "liquidity_contraction"
        )
        self.assertEqual(item["streak"], 0)
        self.assertEqual(item["status"], "neutral")

    def test_cross_date_history_is_not_used_as_current_volume_baseline(self):
        snapshot = topic_snapshot()
        history = [{"trade_date": "2026-08-13", "market_amount": 9_999_999_999_999}]
        payload = assemble_workbench(snapshot, index_history(), history, None, {})
        alignment = payload["volume_price_alignment"]
        self.assertIsNone(alignment["metrics"]["market_amount_change_pct"])
        self.assertIn("历史成交额基准", alignment["missing"])


if __name__ == "__main__":
    unittest.main()
