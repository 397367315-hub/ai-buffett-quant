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
                    "selection_sources": ["fund_flow", "volume", "momentum"],
                },
                {
                    "code": "000001", "name": "平安银行", "price": 10.0,
                    "change_pct": -1.0, "turnover": 2.0, "pe": -8.0, "pb": 0.6,
                    "roe": -1.0, "volume_ratio": 0.8, "market_cap": 100_000_000_000,
                    "main_net_inflow": -500_000_000, "main_net_inflow_pct": -6.0,
                    "selection_sources": ["fund_flow"],
                },
                {
                    "code": "000002", "name": "*ST测试", "price": 8.0,
                    "change_pct": 5.0, "turnover": 9.0, "pe": 10.0, "pb": 1.0,
                    "roe": 10.0, "volume_ratio": 3.0, "market_cap": 10_000_000_000,
                    "main_net_inflow": 900_000_000, "main_net_inflow_pct": 8.0,
                    "selection_sources": ["fund_flow"],
                },
            ],
        }
        service._load_histories = AsyncMock(return_value={
            "600519": _history(100),
            "000001": _history(10),
        })
        regime = {"regime": "震荡市", "confidence": 0.7, "bias": "neutral"}

        with patch(
            "services.stock_selection_agents.collector.fetch_intelligent_selection_candidates",
            new=AsyncMock(return_value=candidates),
        ), patch(
            "services.stock_selection_agents.MarketRegime.detect",
            new=AsyncMock(return_value=regime),
        ):
            result = await service.run(mode="full", risk_profile="balanced", top_n=3)

        self.assertTrue(result["available"])
        self.assertEqual(result["candidate_summary"]["live_candidates"], 2)
        self.assertEqual(result["candidate_summary"]["analyzed"], 2)
        self.assertEqual(result["recommendations"][0]["code"], "600519")
        self.assertIn("technical", result["recommendations"][0]["agents"])
        self.assertIn("risk", result["recommendations"][0]["agents"])
        self.assertEqual(
            next(agent for agent in result["agent_pipeline"] if agent["id"] == "news")["status"],
            "not_configured",
        )

    async def test_pipeline_refuses_to_create_recommendations_without_tradable_quotes(self):
        service = StockSelectionAgentService()
        with patch(
            "services.stock_selection_agents.collector.fetch_intelligent_selection_candidates",
            new=AsyncMock(return_value={"total": 0, "stocks": []}),
        ), patch(
            "services.stock_selection_agents.MarketRegime.detect",
            new=AsyncMock(return_value={"regime": "震荡市", "confidence": 0.5, "bias": "neutral"}),
        ):
            result = await service.run()

        self.assertFalse(result["available"])
        self.assertEqual(result["recommendations"], [])
        self.assertIn("不会以零价或退市记录", result["message"])

    async def test_invalid_profile_is_rejected_before_market_requests(self):
        service = StockSelectionAgentService()
        with self.assertRaisesRegex(ValueError, "risk_profile"):
            await service.run(risk_profile="unknown")
