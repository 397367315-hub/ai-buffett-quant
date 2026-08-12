import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routes import run_stock_selection
from quant.risk import assess_stock_risk
from services.horizon_analysis import HorizonPotentialAnalyzer, combined_agent_weights
from services.research_protocol import ResearchProtocol
from services.stock_features import StockFeatureService


def _agents() -> dict:
    return {
        "technical": {"score": 90, "metrics": {"ma20": 10}},
        "fundamental": {"score": 35},
        "capital": {"score": 82},
        "risk": {"score": 65, "plan": {"stop_loss_price": 9}},
        "news": {"score": 50, "available": False},
        "supervisor": {"confidence": 88},
    }


class HorizonPotentialTests(unittest.TestCase):
    def test_horizon_weights_change_between_week_and_month(self):
        base = {
            "technical": 0.28, "fundamental": 0.20, "capital": 0.26,
            "safety": 0.18, "news": 0.08,
        }
        week = combined_agent_weights(base, "week")
        month = combined_agent_weights(base, "month")

        self.assertGreater(week["technical"], month["technical"])
        self.assertGreater(week["capital"], month["capital"])
        self.assertGreater(month["fundamental"], week["fundamental"])
        self.assertGreater(month["safety"], week["safety"])
        self.assertAlmostEqual(sum(week.values()), 1.0)
        self.assertAlmostEqual(sum(month.values()), 1.0)

    def test_scores_change_with_the_selected_horizon(self):
        analyzer = HorizonPotentialAnalyzer()
        history = [{"close": 10 + index * 0.03} for index in range(35)]
        quality = {"grade": "充分"}
        risk = {"hard_blocked": False, "hard_blocks": [], "warnings": []}
        regime = {"bias": "neutral"}
        base = {
            "technical": 0.28, "fundamental": 0.20, "capital": 0.26,
            "safety": 0.18, "news": 0.08,
        }
        week = analyzer.assess(
            {"price": 11.02}, history, _agents(), regime, quality, risk,
            "week", combined_agent_weights(base, "week"),
        )
        month = analyzer.assess(
            {"price": 11.02}, history, _agents(), regime, quality, risk,
            "month", combined_agent_weights(base, "month"),
        )

        self.assertGreater(week["potential_score"], month["potential_score"])
        self.assertEqual(week["trading_days"], 5)
        self.assertEqual(month["trading_days"], 20)

    def test_insufficient_analogue_samples_reduce_confidence(self):
        analyzer = HorizonPotentialAnalyzer()
        result = analyzer.assess(
            {"price": 11.02},
            [{"close": 10 + index * 0.03} for index in range(35)],
            _agents(),
            {"bias": "neutral"},
            {"grade": "充分"},
            {"hard_blocked": False, "hard_blocks": [], "warnings": []},
            "month",
            {"technical": 0.2, "fundamental": 0.3, "capital": 0.15, "safety": 0.3, "news": 0.05},
        )

        self.assertFalse(result["historical_analogue"]["available"])
        self.assertLess(result["historical_analogue"]["sample_count"], 5)
        self.assertLessEqual(result["confidence"], 52)

    def test_research_audit_uses_selected_holding_period(self):
        audit = ResearchProtocol.strategy_audit(
            {"price": 10, "amount": 1_000_000},
            [{"date": "2026-07-31", "close": 10}],
            timeline={"red_flags": [], "warnings": []},
            quality={"grade": "充分", "score": 90},
            execution={
                "friction_cost": {"total_round_trip_pct": 0.2},
                "reference_net_return_after_cost_pct": 2,
            },
            source="eastmoney",
            is_realtime=True,
            holding_days=20,
        )
        overlap = next(item for item in audit["findings"] if item["category"] == "时间重叠")
        self.assertIn("20日持有", overlap["fix_suggestion"])


class RiskAndFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_financial_mapping_excludes_disclosures_after_research_date(self):
        service = StockFeatureService()
        service._fetch_report = AsyncMock(return_value=[
            {
                "SECURITY_CODE": "600519", "REPORT_DATE": "2026-06-30",
                "NOTICE_DATE": "2026-08-04", "XSMLL": 99,
                "PARENTNETPROFIT": 999, "NCO_NETPROFIT": 9,
            },
            {
                "SECURITY_CODE": "600519", "REPORT_DATE": "2026-06-30",
                "NOTICE_DATE": "2026-08-03", "TOTALOPERATEREVE": 1000,
                "TOTALOPERATEREVETZ": 12.5, "KCFJCXSYJLR": 120,
                "KCFJCXSYJLRTZ": 8.5, "ROEJQ": 16.2, "XSMLL": 42.1,
                "ZCFZL": 35.2, "YSZKYYSR": 18.4, "PARENTNETPROFIT": 150,
                "NETCASH_OPERATE_PK": 180, "NCO_NETPROFIT": 1.2,
            },
        ])

        result = await service._financial_snapshot({"600519"}, date(2026, 8, 3))

        self.assertEqual(result["600519"]["gross_margin"], 42.1)
        self.assertEqual(result["600519"]["ocf_to_profit"], 1.2)
        self.assertEqual(result["600519"]["financial_disclosed_at"], "2026-08-03")

    async def test_partial_ttm_fields_are_not_reported_as_complete(self):
        service = StockFeatureService()
        service._fetch_report = AsyncMock(return_value=[{
            "SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "贵州茅台",
            "REPORT_DATE": "2025-12-31", "NOTICE_DATE": "2026-04-17",
            "TOTALOPERATEREVE": 1000,
        }])
        service._persist_financial_pit = AsyncMock(return_value=1)

        result = await service._financial_snapshot({"600519"}, date(2026, 8, 3))

        self.assertEqual(result["600519"]["revenue_ttm"], 1000)
        self.assertFalse(result["600519"]["ttm_available"])
        self.assertTrue(result["600519"]["ttm_partial"])
        self.assertEqual(result["600519"]["ttm_available_fields"], ["revenue"])

    async def test_cache_from_another_research_date_is_never_used_as_fallback(self):
        service = StockFeatureService()
        service._read_cache = AsyncMock(return_value={
            "as_of_date": "2026-08-03",
            "fetched_at": "2026-08-03T10:00:00+08:00",
            "financial": {"600519": {"gross_margin": 99}},
            "dataset_status": {"financial": "available"},
        })
        service._financial_snapshot = AsyncMock(side_effect=RuntimeError("offline"))

        payload, warnings = await service._datasets(
            {"600519"}, {"gross_margin"}, date(2026, 7, 1),
        )

        self.assertEqual(payload["financial"], {})
        self.assertEqual(payload["dataset_status"]["financial"], "unavailable")
        self.assertTrue(any("数据不足" in item for item in warnings))

    async def test_financial_source_failure_uses_disclosure_dated_pit_cache(self):
        service = StockFeatureService()
        service._read_cache = AsyncMock(return_value={})
        service._financial_snapshot = AsyncMock(side_effect=RuntimeError("offline"))
        service._financial_snapshot_from_pit = AsyncMock(return_value={
            "600519": {
                "roe": 18.5,
                "financial_report_date": "2025-12-31",
                "financial_disclosed_at": "2026-04-17",
                "ttm_available": True,
            },
        })
        service._write_cache = AsyncMock()

        payload, warnings = await service._datasets(
            {"600519"}, {"gross_margin"}, date(2026, 8, 12),
        )

        self.assertEqual(payload["financial"]["600519"]["roe"], 18.5)
        self.assertEqual(payload["dataset_status"]["financial"], "available")
        self.assertTrue(any("PIT缓存" in item for item in warnings))

    def test_lockup_within_seven_days_is_a_non_compensating_block(self):
        result = assess_stock_risk({
            "name": "测试股份", "net_profit": 10, "lockup_days": 7,
            "lockup_ratio_pct": 12.3,
        })
        self.assertTrue(result["hard_blocked"])
        self.assertTrue(any("限售解禁" in item for item in result["hard_blocks"]))

    def test_critical_announcement_is_a_non_compensating_block(self):
        result = assess_stock_risk(
            {"name": "测试股份", "net_profit": 10, "lockup_days": 100},
            [{"title": "关于收到证监会立案调查告知书的公告", "url": "https://example.com"}],
        )
        self.assertTrue(result["hard_blocked"])
        self.assertEqual(len(result["critical_sources"]), 1)

    def test_missing_risk_fields_do_not_produce_a_low_risk_verdict(self):
        result = assess_stock_risk({"name": "测试股份"})
        self.assertEqual(result["risk_level"], "中")
        self.assertGreater(result["missing_data_penalty"], 0)

    def test_financial_sector_does_not_use_industrial_debt_threshold(self):
        result = assess_stock_risk({
            "name": "测试银行", "sector": "银行", "net_profit": 10,
            "debt_ratio": 92, "lockup_days": 100,
        })

        self.assertFalse(any("资产负债率" in item for item in result["warnings"]))
        self.assertTrue(any("金融行业" in item for item in result["evidence"]))

    async def test_flat_sector_ranks_above_declining_sector(self):
        service = StockFeatureService()
        with patch(
            "services.stock_features.collector.fetch_all_industry_flow",
            new=AsyncMock(return_value=[
                {"name": "平盘板块", "change_pct": 0, "main_net_inflow": 0},
                {"name": "下跌板块", "change_pct": -0.1, "main_net_inflow": 1_000_000},
            ]),
        ):
            ranks = await service._live_sector_ranks()

        self.assertEqual(ranks["平盘板块"]["sector_rank"], 1)
        self.assertEqual(ranks["下跌板块"]["sector_rank"], 2)

    async def test_invalid_horizon_is_rejected_before_running_agents(self):
        with self.assertRaises(HTTPException) as context:
            await run_stock_selection({"horizon": "quarter"})
        self.assertEqual(context.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
