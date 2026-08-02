import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from services.stock_selection_agents import StockSelectionAgentService


def _history(start_price: float, days: int = 80) -> list[dict]:
    start = datetime(2026, 4, 1)
    return [
        {
            "date": (start + timedelta(days=index)).date().isoformat(),
            "close": round(start_price * (1 + index * 0.002), 2),
            "high": round(start_price * (1 + index * 0.002 + 0.01), 2),
            "low": round(start_price * (1 + index * 0.002 - 0.01), 2),
        }
        for index in range(days)
    ]


def _macro_context() -> dict:
    return {
        "available": True,
        "updated_at": "2026-07-30T12:00:00+08:00",
        "summary": "已核验国际宏观数据源 1 个，国内政策与发展信息 1 条。",
        "international_items": [{
            "source": "IMF WEO DataMapper",
            "scope": "international_macro",
            "title": "IMF 2026 年实际 GDP 增长预测：中国 4.4%",
            "published_at": "2026-07-30",
            "url": "https://www.imf.org/external/datamapper/NGDP_RPCH",
            "impact": "neutral",
        }],
        "policy_items": [{
            "source": "中国政府网",
            "scope": "domestic_policy",
            "title": "关于促进白酒产业高质量发展的行动计划",
            "published_at": "2026-07-29",
            "url": "https://www.gov.cn/example-policy",
            "impact": "neutral",
        }],
        "source_status": {"中国政府网": "available", "IMF WEO DataMapper": "available"},
        "macro_adjustment": 2.0,
    }


class StockSelectionAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_ranks_source_backed_candidate_and_exposes_agent_trace(self):
        service = StockSelectionAgentService()
        candidates = {
            "total": 2,
            "stocks": [
                {
                    "code": "600519", "name": "贵州茅台", "price": 120.0,
                    "change_pct": 2.5, "turnover": 4.0, "pe": 18.0, "pb": 5.0,
                    "roe": 21.0, "volume_ratio": 2.0, "market_cap": 100_000_000_000,
                    "main_net_inflow": 800_000_000, "main_net_inflow_pct": 6.0,
                    "sector": "白酒",
                    "selection_sources": ["fund_flow", "volume", "momentum"],
                },
                {
                    "code": "000001", "name": "平安银行", "price": 10.0,
                    "change_pct": -1.0, "turnover": 2.0, "pe": -8.0, "pb": 0.6,
                    "roe": -1.0, "volume_ratio": 0.8, "market_cap": 100_000_000_000,
                    "main_net_inflow": -500_000_000, "main_net_inflow_pct": -6.0,
                    "sector": "银行",
                    "selection_sources": ["fund_flow"],
                },
                {
                    "code": "000002", "name": "*ST测试", "price": 8.0,
                    "change_pct": 5.0, "turnover": 9.0, "pe": 10.0, "pb": 1.0,
                    "roe": 10.0, "volume_ratio": 3.0, "market_cap": 10_000_000_000,
                    "main_net_inflow": 900_000_000, "main_net_inflow_pct": 8.0,
                    "sector": "白酒",
                    "selection_sources": ["fund_flow"],
                },
            ],
        }
        service._load_histories = AsyncMock(return_value={
            "600519": _history(100),
            "000001": _history(10),
        })
        regime = {"regime": "震荡市", "confidence": 0.7, "bias": "neutral"}

        announcements = {
            "600519": [{
                "source": "东方财富公告聚合",
                "scope": "company_announcement",
                "title": "贵州茅台2025年年度权益分派实施公告",
                "published_at": "2026-07-29",
                "url": "https://data.eastmoney.com/notices/detail/600519/example.html",
                "impact": "neutral",
            }],
        }
        with (
            patch(
                "services.stock_selection_agents.collector.fetch_intelligent_selection_candidates",
                new=AsyncMock(return_value=candidates),
            ),
            patch(
                "services.stock_selection_agents.MarketRegime.detect",
                new=AsyncMock(return_value=regime),
            ),
            patch(
                "services.stock_selection_agents.macro_policy_news_collector.get_context",
                new=AsyncMock(return_value=_macro_context()),
            ),
            patch(
                "services.stock_selection_agents.macro_policy_news_collector.get_stock_announcements",
                new=AsyncMock(return_value=announcements),
            ),
        ):
            result = await service.run(mode="full", risk_profile="balanced", top_n=3)

        self.assertTrue(result["available"])
        self.assertEqual(result["candidate_summary"]["live_candidates"], 2)
        self.assertEqual(result["candidate_summary"]["analyzed"], 2)
        self.assertEqual(result["recommendations"][0]["code"], "600519")
        self.assertEqual(result["recommendations"][0]["sector"], "白酒")
        self.assertIn("technical", result["recommendations"][0]["agents"])
        self.assertIn("risk", result["recommendations"][0]["agents"])
        self.assertIn("news", result["recommendations"][0]["agents"])
        self.assertIn("research", result["recommendations"][0])
        self.assertIn("data_quality", result["recommendations"][0]["research"])
        self.assertIn("strategy_audit", result["recommendations"][0]["research"])
        self.assertEqual(result["data_contract"]["slug"], "a-stock-data")
        self.assertTrue(result["recommendations"][0]["agents"]["news"]["sources"])
        self.assertEqual(
            next(agent for agent in result["agent_pipeline"] if agent["id"] == "news")["status"],
            "completed",
        )

    async def test_pipeline_refuses_to_create_recommendations_without_tradable_quotes(self):
        service = StockSelectionAgentService()
        with (
            patch(
                "services.stock_selection_agents.collector.fetch_intelligent_selection_candidates",
                new=AsyncMock(return_value={"total": 0, "stocks": []}),
            ),
            patch(
                "services.stock_selection_agents.MarketRegime.detect",
                new=AsyncMock(return_value={"regime": "震荡市", "confidence": 0.5, "bias": "neutral"}),
            ),
            patch(
                "services.stock_selection_agents.macro_policy_news_collector.get_context",
                new=AsyncMock(return_value={"available": False, "international_items": [], "policy_items": []}),
            ),
            patch(
                "services.stock_selection_agents.macro_policy_news_collector.get_stock_announcements",
                new=AsyncMock(return_value={}),
            ),
        ):
            result = await service.run()

        self.assertFalse(result["available"])
        self.assertEqual(result["recommendations"], [])
        self.assertIn("不会以零价或退市记录", result["message"])

    async def test_invalid_profile_is_rejected_before_market_requests(self):
        service = StockSelectionAgentService()
        with self.assertRaisesRegex(ValueError, "risk_profile"):
            await service.run(risk_profile="unknown")

    async def test_sector_filter_excludes_nonmatching_candidates_before_ranking(self):
        service = StockSelectionAgentService()
        candidates = {
            "total": 2,
            "stocks": [
                {
                    "code": "600519", "name": "贵州茅台", "price": 120.0,
                    "change_pct": 2.5, "turnover": 4.0, "pe": 18.0, "pb": 5.0,
                    "roe": 21.0, "volume_ratio": 2.0, "market_cap": 100_000_000_000,
                    "main_net_inflow": 800_000_000, "main_net_inflow_pct": 6.0,
                    "sector": "白酒", "selection_sources": ["fund_flow"],
                },
                {
                    "code": "000001", "name": "平安银行", "price": 10.0,
                    "change_pct": 5.0, "turnover": 5.0, "pe": 10.0, "pb": 0.6,
                    "roe": 10.0, "volume_ratio": 3.0, "market_cap": 100_000_000_000,
                    "main_net_inflow": 900_000_000, "main_net_inflow_pct": 8.0,
                    "sector": "银行", "selection_sources": ["fund_flow", "volume"],
                },
            ],
        }
        service._load_histories = AsyncMock(return_value={"600519": _history(100)})
        with (
            patch(
                "services.stock_selection_agents.collector.fetch_intelligent_selection_candidates",
                new=AsyncMock(return_value=candidates),
            ),
            patch(
                "services.stock_selection_agents.MarketRegime.detect",
                new=AsyncMock(return_value={"regime": "震荡市", "confidence": 0.5, "bias": "neutral"}),
            ),
            patch(
                "services.stock_selection_agents.macro_policy_news_collector.get_context",
                new=AsyncMock(return_value={"available": False, "international_items": [], "policy_items": []}),
            ),
            patch(
                "services.stock_selection_agents.macro_policy_news_collector.get_stock_announcements",
                new=AsyncMock(return_value={}),
            ),
        ):
            result = await service.run(sector="白酒", top_n=3)

        self.assertTrue(result["available"])
        self.assertEqual(result["sector_filter"]["value"], "白酒")
        self.assertEqual(result["sector_filter"]["matched_candidates"], 1)
        self.assertEqual(result["candidate_summary"]["market_candidates"], 2)
        self.assertEqual([item["code"] for item in result["recommendations"]], ["600519"])

    async def test_sector_code_scans_complete_board_instead_of_leader_pool(self):
        service = StockSelectionAgentService()
        board_snapshot = {
            "source": "eastmoney",
            "total": 120,
            "complete": True,
            "stocks": [{
                "code": "600519", "name": "贵州茅台", "price": 120.0,
                "change_pct": 2.5, "turnover": 4.0, "pe": 18.0, "pb": 5.0,
                "roe": 21.0, "volume_ratio": 2.0, "market_cap": 100_000_000_000,
                "main_net_inflow": 800_000_000, "main_net_inflow_pct": 6.0,
                "sector": "酿酒行业", "selection_sources": ["industry_constituent"],
            }],
        }
        service._load_histories = AsyncMock(return_value={"600519": _history(100)})
        leader_pool = AsyncMock()
        with (
            patch(
                "services.stock_selection_agents.collector.fetch_all_board_stocks",
                new=AsyncMock(return_value=board_snapshot),
            ) as board_fetch,
            patch(
                "services.stock_selection_agents.collector.fetch_intelligent_selection_candidates",
                new=leader_pool,
            ),
            patch(
                "services.stock_selection_agents.MarketRegime.detect",
                new=AsyncMock(return_value={"regime": "震荡市", "confidence": 0.5, "bias": "neutral"}),
            ),
            patch(
                "services.stock_selection_agents.macro_policy_news_collector.get_context",
                new=AsyncMock(return_value={"available": False, "international_items": [], "policy_items": []}),
            ),
            patch(
                "services.stock_selection_agents.macro_policy_news_collector.get_stock_announcements",
                new=AsyncMock(return_value={}),
            ),
        ):
            result = await service.run(
                sector="酿酒行业",
                sector_code="BK0475",
                top_n=3,
            )

        board_fetch.assert_awaited_once_with("BK0475", sector_name="酿酒行业")
        leader_pool.assert_not_awaited()
        self.assertTrue(result["available"])
        self.assertEqual(result["sector_filter"]["code"], "BK0475")
        self.assertEqual(result["sector_filter"]["market_candidates"], 120)
        self.assertEqual(result["sector_filter"]["matched_candidates"], 1)
        self.assertEqual(result["recommendations"][0]["selection_sources"], ["industry_constituent"])

    def test_unavailable_news_source_is_not_marked_as_an_active_signal(self):
        service = StockSelectionAgentService()
        report = service._news_policy_agent(
            {"sector": "银行"},
            {"available": False, "international_items": [], "policy_items": []},
            [],
        )

        self.assertFalse(report["available"])
        self.assertEqual(report["score"], 50.0)
        self.assertIn("未计分", report["summary"])

    async def test_ftshare_candidate_snapshot_is_not_presented_as_verified_live_data(self):
        service = StockSelectionAgentService()
        candidates = {
            "source": "ftshare_mcp",
            "total": 1,
            "stocks": [{
                "code": "600519", "name": "贵州茅台", "price": 120.0,
                "change_pct": 2.5, "turnover": 4.0, "pe": 18.0, "pb": "", "roe": "",
                "volume_ratio": None, "market_cap": 100_000_000_000,
                "main_net_inflow": None, "main_net_inflow_pct": None,
                "sector": "", "selection_sources": ["ftshare_market"],
            }],
        }
        service._load_histories = AsyncMock(return_value={})
        with (
            patch(
                "services.stock_selection_agents.collector.fetch_intelligent_selection_candidates",
                new=AsyncMock(return_value=candidates),
            ),
            patch(
                "services.stock_selection_agents.MarketRegime.detect",
                new=AsyncMock(return_value={"regime": "震荡市", "confidence": 0.5, "bias": "neutral"}),
            ),
            patch(
                "services.stock_selection_agents.macro_policy_news_collector.get_context",
                new=AsyncMock(return_value={"available": False, "international_items": [], "policy_items": []}),
            ),
            patch(
                "services.stock_selection_agents.macro_policy_news_collector.get_stock_announcements",
                new=AsyncMock(return_value={}),
            ),
            patch.object(service, "_is_market_session", return_value=True),
        ):
            result = await service.run(top_n=3)

        self.assertEqual(result["source"], "ftshare_mcp")
        self.assertFalse(result["is_realtime"])
        self.assertIsNone(result["data_date"])
        self.assertIn("未提供主力资金流", result["recommendations"][0]["agents"]["capital"]["summary"])
        self.assertEqual(
            next(agent for agent in result["agent_pipeline"] if agent["id"] == "data")["skill"],
            "FTShare 行情快照候选池",
        )

    async def test_incomplete_daily_evidence_cannot_be_marked_priority_research(self):
        service = StockSelectionAgentService()
        candidates = {
            "total": 1,
            "stocks": [{
                "code": "600519", "name": "贵州茅台", "price": 120.0,
                "change_pct": 2.5, "turnover": 4.0, "pe": "", "pb": "", "roe": "",
                "volume_ratio": None, "market_cap": 100_000_000_000,
                "main_net_inflow": None, "main_net_inflow_pct": None,
                "sector": "白酒", "selection_sources": ["ftshare_market"],
            }],
        }
        service._load_histories = AsyncMock(return_value={"600519": []})
        with (
            patch(
                "services.stock_selection_agents.collector.fetch_intelligent_selection_candidates",
                new=AsyncMock(return_value=candidates),
            ),
            patch(
                "services.stock_selection_agents.MarketRegime.detect",
                new=AsyncMock(return_value={"regime": "震荡市", "confidence": 0.5, "bias": "neutral"}),
            ),
            patch(
                "services.stock_selection_agents.macro_policy_news_collector.get_context",
                new=AsyncMock(return_value={"available": False, "international_items": [], "policy_items": []}),
            ),
            patch(
                "services.stock_selection_agents.macro_policy_news_collector.get_stock_announcements",
                new=AsyncMock(return_value={}),
            ),
        ):
            result = await service.run(top_n=3)

        recommendation = result["recommendations"][0]
        self.assertEqual(recommendation["research"]["data_quality"]["grade"], "不足")
        self.assertEqual(recommendation["verdict"], "证据不足")
        self.assertNotEqual(recommendation["verdict"], "优先研究")
