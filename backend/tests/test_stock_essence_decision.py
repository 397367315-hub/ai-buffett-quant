import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

from api import routes
from services.stock_essence_decision import (
    DECISION_STATES,
    StockEssenceDecisionService,
)


class StockEssenceDecisionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = StockEssenceDecisionService()

    def test_financial_quality_uses_only_disclosed_pit_rows_and_builds_ttm(self):
        financial = [
            {
                "REPORT_DATE": "2026-03-31", "NOTICE_DATE": "2026-04-25",
                "TOTALOPERATEREVE": 120, "PARENTNETPROFIT": 12,
                "KCFJCXSYJLR": 11, "NETCASH_OPERATE_PK": 10,
                "TOTALOPERATEREVETZ": 20, "PARENTNETPROFITTZ": 20,
                "KCFJCXSYJLRTZ": 22, "ROEJQ": 12, "XSMLL": 40, "ZCFZL": 35,
            },
            {
                "REPORT_DATE": "2025-12-31", "NOTICE_DATE": "2026-03-20",
                "TOTALOPERATEREVE": 450, "PARENTNETPROFIT": 45,
                "KCFJCXSYJLR": 42, "NETCASH_OPERATE_PK": 40,
            },
            {
                "REPORT_DATE": "2025-03-31", "NOTICE_DATE": "2025-04-25",
                "TOTALOPERATEREVE": 100, "PARENTNETPROFIT": 10,
                "KCFJCXSYJLR": 9, "NETCASH_OPERATE_PK": 8,
            },
            {
                "REPORT_DATE": "2026-06-30", "NOTICE_DATE": "2026-08-30",
                "TOTALOPERATEREVE": 9999, "PARENTNETPROFIT": 9999,
            },
        ]
        balances = [{
            "REPORT_DATE": "2026-03-31", "NOTICE_DATE": "2026-04-25",
            "INVENTORY": 20, "INVENTORY_YOY": 5,
            "ACCOUNTS_RECE": 10, "ACCOUNTS_RECE_YOY": 4,
            "TOTAL_ASSETS": 200, "TOTAL_LIABILITIES": 70,
        }]

        result = self.service._build_financials(financial, balances, date(2026, 8, 14))

        self.assertEqual(result["report_date"], "2026-03-31")
        self.assertEqual(result["metrics"]["revenue_ttm"], 470)
        self.assertEqual(result["metrics"]["net_profit_ttm"], 47)
        self.assertEqual(result["earnings_quality"], "高")

    def test_execute_requires_realtime_tail_and_auction_confirmation(self):
        market = {"market_cognition": {"final_action": "execute"}}
        fundamentals = {"earnings_quality": "高"}
        valuation = {"state": "合理 + 盈利改善/稳定"}
        relative = {"individual_alpha_score": 80}
        emotion = {"level": "正常"}
        risk = {"risk_reward_ratio": 2, "support": 10}
        strategy = {
            "tail_1455": {"all_conditions_passed": True},
            "auction_confirmation": {"execution_ready": False},
        }

        guarded = self.service._build_decision(
            market, fundamentals, valuation, relative, emotion, {}, {}, risk,
            strategy, True,
        )
        self.assertEqual(guarded["state"], "CAUTION")

        strategy["auction_confirmation"]["execution_ready"] = True
        executable = self.service._build_decision(
            market, fundamentals, valuation, relative, emotion, {}, {}, risk,
            strategy, True,
        )
        self.assertEqual(executable["state"], "EXECUTE")
        self.assertIn(executable["state"], DECISION_STATES)

        historical = self.service._build_decision(
            market, fundamentals, valuation, relative, emotion, {}, {}, risk,
            strategy, False,
        )
        self.assertNotEqual(historical["state"], "EXECUTE")

    async def test_component_endpoint_keeps_structured_decision_attached(self):
        profile = {
            "meta": {"data_date": "2026-08-14"},
            "valuation": {"current_pe_ttm": 20.2},
            "decision": {"state": "OBSERVE", "label": "观察"},
        }
        with patch.object(
            routes.stock_essence_decision_service,
            "get",
            new=AsyncMock(return_value=profile),
        ):
            response = await routes.get_stock_valuation("600519", None, False)

        self.assertEqual(response["data"]["current_pe_ttm"], 20.2)
        self.assertEqual(response["decision"]["state"], "OBSERVE")

    async def test_ai_explanation_cannot_replace_structured_state(self):
        profile = {
            "meta": {"contract_version": "stock-essence-decision-v2.3.0", "data_date": "2026-08-14"},
            "company": {"stock_code": "600519", "stock_name": "贵州茅台"},
            "fundamentals": {"earnings_quality": "高"},
            "valuation": {}, "attribution": {}, "sector_role": {},
            "sector_dependency": {}, "emotion": {}, "expectation_gap": {},
            "catalysts": {}, "risk_reward": {}, "strategy_fit": {}, "evidence": [],
            "decision": {"state": "OBSERVE", "label": "观察", "reasons": ["等待确认"]},
        }
        with (
            patch.object(
                routes.stock_essence_decision_service,
                "get",
                new=AsyncMock(return_value=profile),
            ),
            patch.object(routes.ai_service, "generate", new=AsyncMock(return_value="解释文本")),
        ):
            response = await routes.explain_stock_decision("600519", {})

        self.assertEqual(response["data"]["decision"]["state"], "OBSERVE")
        self.assertEqual(response["data"]["analysis"]["decision"]["state"], "OBSERVE")
        self.assertEqual(response["data"]["narrative"], "解释文本")

    def test_failed_refresh_keeps_complete_verified_snapshot(self):
        previous = {
            "available": True,
            "meta": {
                "data_date": "2026-08-14",
                "source_updated_at": "2026-08-14T15:00:00+08:00",
                "cache_used": False,
            },
            "company": {"stock_code": "600519", "stock_name": "贵州茅台", "current_price": 1341.99},
            "data_audit": {
                "public_source_coverage_pct": 100.0,
                "resolved_sources": 2,
                "required_sources": 2,
                "sources": [
                    {"key": "company", "status": "observed", "detail": "公司资料已核验"},
                    {"key": "quote", "status": "observed", "detail": "行情已核验"},
                ],
            },
        }
        candidate = {
            "available": False,
            "meta": {"data_date": "2026-08-15", "cache_used": False},
            "company": {"stock_code": "600519", "stock_name": "", "current_price": None},
            "data_audit": {
                "public_source_coverage_pct": 50.0,
                "resolved_sources": 1,
                "required_sources": 2,
                "sources": [
                    {"key": "company", "status": "observed", "detail": "公司资料已核验"},
                    {"key": "quote", "status": "source_retry_required", "detail": "代理暂时失败"},
                ],
            },
        }

        result = self.service._prefer_verified_snapshot(
            previous,
            candidate,
            datetime.fromisoformat("2026-08-15T16:00:00+08:00"),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["company"]["stock_name"], "贵州茅台")
        self.assertEqual(result["company"]["current_price"], 1341.99)
        self.assertEqual(result["meta"]["data_date"], "2026-08-14")
        self.assertTrue(result["meta"]["cache_used"])
        self.assertEqual(result["data_audit"]["refresh_failed_sources"], ["quote"])
        self.assertTrue(all(
            item["status"] == "cached_fallback"
            for item in result["data_audit"]["sources"]
        ))
        self.assertEqual(previous["data_audit"]["sources"][0]["status"], "observed")

    def test_refresh_with_same_verified_sources_replaces_cache(self):
        previous = {
            "available": True,
            "data_audit": {"sources": [{"key": "quote", "status": "observed"}]},
        }
        candidate = {
            "available": True,
            "data_audit": {"sources": [{"key": "quote", "status": "observed"}]},
        }

        result = self.service._prefer_verified_snapshot(
            previous,
            candidate,
            datetime.fromisoformat("2026-08-15T16:00:00+08:00"),
        )

        self.assertIsNone(result)

    def test_best_previous_snapshot_prefers_verified_coverage_before_date(self):
        failed_today = {
            "available": False,
            "meta": {"data_date": "2026-08-15"},
            "data_audit": {"sources": [
                {"key": "market_context", "status": "observed"},
            ]},
        }
        complete_previous_day = {
            "available": True,
            "meta": {"data_date": "2026-08-14"},
            "data_audit": {"sources": [
                {"key": "company", "status": "observed"},
                {"key": "quote", "status": "observed"},
            ]},
        }

        result = self.service._best_previous_payload([
            failed_today,
            complete_previous_day,
        ])

        self.assertIs(result, complete_previous_day)

    async def test_empty_consensus_is_an_observed_zero_not_a_source_failure(self):
        with patch(
            "services.stock_essence_decision.collector.fetch_json",
            new=AsyncMock(return_value={"result": {"data": []}}),
        ):
            consensus = await self.service._consensus("600234")

        self.assertEqual(consensus["_coverage_status"], "no_analyst_coverage")
        self.assertEqual(consensus["RATING_ORG_NUM"], 0)
        expectation = self.service._build_expectation(
            consensus,
            {"metrics": {"net_profit_growth_pct": -12.5}},
            "2026-08-15T16:00:00+08:00",
        )
        self.assertEqual(expectation["analyst_count"], 0)
        self.assertEqual(expectation["state"], "无机构一致预期覆盖")
        self.assertEqual(expectation["latest_actual_profit_growth_pct"], -12.5)

    def test_negative_current_pe_does_not_produce_misleading_percentiles(self):
        valuation = self.service._build_valuation(
            {"pe": -20, "pb": 3.2},
            {"history": [
                {"date": "2026-08-12", "pe_ttm": 18},
                {"date": "2026-08-13", "pe_ttm": 20},
                {"date": "2026-08-14", "pe_ttm": -20},
            ], "history_end": "2026-08-14", "source": "test"},
            [{"pe": 15}, {"pe": 25}],
            {"earnings_state": "恶化", "metrics": {"net_profit_growth_pct": -5}},
            {"_coverage_status": "no_analyst_coverage"},
        )

        self.assertEqual(valuation["current_pe_ttm"], -20)
        self.assertFalse(valuation["pe_applicable"])
        self.assertIsNone(valuation["pe_percentile_3y"])
        self.assertIsNone(valuation["industry_pe_percentile"])
        self.assertIn("亏损期", valuation["state"])

    async def test_historical_valuation_cache_is_cut_off_at_requested_date(self):
        cached = {
            "history": [
                {"date": "2026-08-08", "pe_ttm": 20},
                {"date": "2026-08-10", "pe_ttm": 21},
                {"date": "2026-08-14", "pe_ttm": 22},
            ],
            "history_start": "2026-08-08",
            "history_end": "2026-08-14",
            "source": "database_cache",
        }
        with (
            patch(
                "services.stock_essence_decision.fqe_reference_data.get_history",
                new=AsyncMock(return_value=cached),
            ),
            patch(
                "services.stock_essence_decision.fqe_reference_data._fetch_valuation",
                new=AsyncMock(),
            ) as fetch,
        ):
            result = await self.service._valuation_history(
                "600519", "贵州茅台", date(2026, 8, 10),
            )

        self.assertEqual(result["history_end"], "2026-08-10")
        self.assertEqual(len(result["history"]), 2)
        fetch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
