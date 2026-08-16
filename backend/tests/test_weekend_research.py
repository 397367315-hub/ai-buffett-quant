import json
import unittest
from unittest.mock import AsyncMock, patch

from api import research_routes
from services.weekend_research import (
    WeekendResearchService,
    _ai_report_synthesis,
    _candidate_seeds,
    build_research_report,
)


def workbench_fixture() -> dict:
    return {
        "available": True,
        "meta": {
            "decision_date": "2026-08-14",
            "coverage_pct": 88,
            "source": "database_cache",
            "is_realtime": False,
        },
        "market_state": {
            "state_code": "S2",
            "state_label": "趋势启动",
            "score": 72,
            "dimensions": [
                {"id": "breadth", "label": "市场宽度", "score": 68, "observed": True, "evidence": ["上涨3100家，下跌1900家"]},
                {"id": "risk", "label": "风险状态", "score": 63, "observed": True, "evidence": ["炸板率处于中位"]},
                {"id": "liquidity", "label": "成交活跃", "score": 51, "observed": True, "evidence": ["成交额环比收缩"]},
            ],
        },
        "structure_health": {"score": 66, "evidence": ["板块宽度尚可"]},
        "volume_price_alignment": {"status": "divergent", "evidence": ["指数上涨但成交收缩"]},
        "crowding_risk": {"score": 58},
        "market_cognition": {
            "facts": ["成交额环比下降8%"],
            "principal_contradiction": {
                "statement": "指数趋势与增量成交不足之间的矛盾",
                "evidence": ["指数在MA20上方", "成交额下降"],
                "confidence_pct": 82,
            },
            "dominant_aspect": {"statement": "多方趋势占优但承接不足", "direction": "mixed"},
            "stage": {"code": "S2", "label": "趋势启动"},
            "quantitative_changes": [{"label": "成交收缩", "evidence": "连续2个样本下降"}],
            "practice_hypothesis": {
                "statement": "若成交与宽度同步修复，则趋势延续可信度提高",
                "validation_window": "T+1/T+3/T+5",
                "falsification": ["成交继续收缩", "宽度跌破50%"],
            },
            "action_label": "谨慎",
            "final_action": "caution",
        },
        "headline_metrics": {"up_down_ratio": 1.63, "market_amount": 1_600_000_000_000},
        "ai_judgement": {"market_summary": "指数趋势向上，但成交承接不足"},
        "main_lines": [
            {
                "rank": 1, "name": "机器人", "classification": "核心主线", "lifecycle": "强化",
                "strength_score": 86, "breadth": 72, "change_pct": 2.8,
                "main_net_inflow": 8_000_000_000, "evidence": "板块宽度72%，主力净流入80亿",
                "leader": {"code": "600001", "name": "测试龙头", "price": 18.2, "change_pct": 6.1},
                "risk_flags": [],
            }
        ],
        "daily_short_term_recommendations": {
            "candidates": [
                {
                    "code": "600001", "name": "测试龙头", "sector": "机器人", "score": 82,
                    "why_selected": ["板块强化", "资金流入"], "why_not_full": ["等待日内确认"],
                },
                {
                    "code": "000002", "name": "测试潜力", "sector": "电子", "score": 76,
                    "why_selected": ["Alpha改善"], "why_not_full": ["板块强度稍弱"],
                },
            ]
        },
        "candidates": [],
        "audit": {"missing_fields": ["北向净买入已停止公开"], "stale_components": []},
    }


def stock_profile() -> dict:
    return {
        "meta": {"data_date": "2026-08-14"},
        "company": {
            "stock_code": "600001", "stock_name": "测试龙头", "industry": "机器人",
            "main_business": "工业机器人研发与销售", "current_price": 18.2,
            "total_market_cap": 12_000_000_000,
        },
        "fundamentals": {
            "earnings_quality": "高", "earnings_quality_score": 86,
            "earnings_state": "改善", "earnings_sustainability": "高", "metrics": {},
        },
        "valuation": {"state": "合理 + 盈利改善/稳定", "current_pe_ttm": 24, "pe_percentile_3y": 38},
        "capital_impact": {"windows": []},
        "attribution": {"individual_alpha_score": 73},
        "alpha": {"score": 73, "windows": [{"days": 5, "alpha_pct": 4.2}]},
        "sector_role": {"role": "核心龙头"},
        "sector_dependency": {"dependency_level": "中", "independence_level": "高"},
        "catalysts": {"net_direction": "neutral"},
        "expectation_gap": {"state": "基本匹配"},
        "emotion": {"level": "正常", "trend": "温度平稳"},
        "risk_reward": {"risk_reward_ratio": 2.4, "potential_upside_pct": 12, "potential_downside_pct": 5},
        "strategy_fit": {},
        "decision": {"state": "CAUTION", "label": "谨慎", "reasons": [], "invalidation_conditions": ["跌破关键支撑"]},
        "data_audit": {"public_source_coverage_pct": 91},
        "evidence": [{"nature": "fact", "category": "company", "statement": "主营业务已核验"}],
    }


class WeekendResearchBuilderTests(unittest.TestCase):
    def test_report_is_json_serializable_and_keeps_evidence_natures(self):
        workbench = workbench_fixture()
        seeds = _candidate_seeds(workbench)

        report = build_research_report(
            workbench,
            {"600001": stock_profile()},
            seeds[:1],
            seeds[1:],
            mode="deep",
            topic="机器人为什么走强",
        )

        json.dumps(report, ensure_ascii=False)
        self.assertEqual(report["candidates"][0]["code"], "600001")
        self.assertEqual(report["candidates"][0]["research_class_label"], "趋势延续")
        self.assertEqual(report["scenarios"][0]["nature"], "FORECAST")
        self.assertEqual(report["market_autopsy"]["answers"][2]["nature"], "FACT")
        self.assertTrue(report["hypotheses"][0]["due_date"].startswith("2026-08"))

    def test_missing_stock_profile_is_explicit_not_invented(self):
        workbench = workbench_fixture()
        seeds = _candidate_seeds(workbench)

        report = build_research_report(
            workbench, {}, seeds[:1], [], mode="quick", topic=None,
        )

        candidate = report["candidates"][0]
        self.assertIsNone(candidate["company"]["main_business"])
        self.assertEqual(candidate["data_completeness_pct"], 0)
        self.assertEqual(candidate["confidence"], "低")

    def test_topic_match_is_prioritised_and_duplicate_code_is_merged(self):
        workbench = workbench_fixture()
        workbench["candidates"] = [{
            "code": "600001", "name": "测试龙头", "sector": "机器人", "score": 90,
            "why_selected": ["二次证据"],
        }]

        seeds = _candidate_seeds(workbench, "电子")

        self.assertEqual(seeds[0]["code"], "000002")
        robot = next(item for item in seeds if item["code"] == "600001")
        self.assertIn("二次证据", robot["why_selected"])
        self.assertEqual(sum(item["code"] == "600001" for item in seeds), 1)

    def test_continuous_chinese_topic_matches_sector_and_stock(self):
        workbench = workbench_fixture()
        seeds = _candidate_seeds(workbench, "机器人板块最近一个月为何持续走强")

        self.assertEqual(seeds[0]["code"], "600001")
        report = build_research_report(
            workbench,
            {"600001": stock_profile()},
            seeds[:1],
            seeds[1:],
            mode="topic",
            topic="机器人板块最近一个月为何持续走强",
        )

        self.assertEqual(report["topic_research"]["inference"].split("；")[0], "已匹配1个板块、1只研究股票")

    def test_original_ai_judgment_is_loaded_from_server_report(self):
        report = {"candidates": [{"code": "600001", "name": "测试龙头", "main_risk": "估值偏高"}]}

        result = WeekendResearchService._ai_judgment(report, "stock", "600001")

        self.assertEqual(result["name"], "测试龙头")
        self.assertEqual(result["main_risk"], "估值偏高")

    def test_low_earnings_quality_is_a_risk_not_an_advantage(self):
        workbench = workbench_fixture()
        seeds = _candidate_seeds(workbench)
        profile = stock_profile()
        profile["fundamentals"]["earnings_quality"] = "低"
        profile["alpha"]["score"] = 66

        report = build_research_report(
            workbench, {"600001": profile}, seeds[:1], [], mode="quick", topic=None,
        )

        candidate = report["candidates"][0]
        self.assertNotEqual(candidate["main_advantage"], "盈利质量低")
        self.assertIn("盈利质量低", candidate["main_risk"])


class WeekendResearchRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_ai_synthesis_cannot_replace_structured_report(self):
        report = {
            "conclusion": {"market_state": "趋势启动", "action": "谨慎"},
            "market_autopsy": {}, "conflicts": {}, "sectors": [], "candidates": [],
            "scenarios": [], "data_quality": {"completeness_pct": 90},
        }
        with patch(
            "services.weekend_research.ai_service.generate",
            new=AsyncMock(return_value="本周本质：趋势仍在。\n风险边界：候选不等于推荐。"),
        ):
            result = await _ai_report_synthesis(report)

        self.assertTrue(result["available"])
        self.assertEqual(report["conclusion"]["action"], "谨慎")
        self.assertIn("不修改评分", result["guard"])

    async def test_start_endpoint_returns_background_session(self):
        expected = {"id": "wr_test", "status": "DRAFT", "progress": 0}
        with patch.object(
            research_routes.weekend_research_service,
            "start",
            new=AsyncMock(return_value=expected),
        ) as mocked:
            response = await research_routes.start_weekly_research(
                research_routes.WeeklyResearchRequest(mode="quick"),
            )

        self.assertEqual(response["data"]["id"], "wr_test")
        mocked.assert_awaited_once_with(mode="quick", topic=None)

    async def test_component_endpoint_keeps_report_meta(self):
        payload = {
            "id": "wr_test", "status": "COMPLETED",
            "report": {"meta": {"source_data_date": "2026-08-14"}, "scenarios": [{"name": "震荡分歧"}]},
        }
        with patch.object(
            research_routes.weekend_research_service,
            "get",
            new=AsyncMock(return_value=payload),
        ):
            response = await research_routes.get_weekly_scenarios("wr_test")

        self.assertEqual(response["data"][0]["name"], "震荡分歧")
        self.assertEqual(response["meta"]["source_data_date"], "2026-08-14")


if __name__ == "__main__":
    unittest.main()
