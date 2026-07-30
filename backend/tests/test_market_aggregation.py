import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from api import routes


class MarketAggregationTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_component_uses_its_explicit_fallback(self):
        with patch.object(routes.settings, "market_aggregate_timeout", 0.01):
            result = await routes._fetch_market_component(
                "slow-source",
                asyncio.sleep(0.1, result={"unexpected": True}),
                {"available": False},
            )

        self.assertEqual(result, {"available": False})

    async def test_overview_uses_the_actual_lowest_flow_ranking(self):
        async def fetch_concept_flow(*, sort_order=0, **kwargs):
            del kwargs
            return ([
                {"code": "BK0001", "name": "高净额", "main_net_inflow": 30},
                {"code": "BK0002", "name": "次高净额", "main_net_inflow": 20},
            ] if sort_order == 0 else [
                {"code": "BK0003", "name": "低净额", "main_net_inflow": -40},
                {"code": "BK0004", "name": "次低净额", "main_net_inflow": -20},
            ])

        with (
            patch.object(routes.collector, "fetch_north_bound_daily", new=AsyncMock(return_value=[])),
            patch.object(routes.collector, "fetch_concept_flow", new=fetch_concept_flow),
            patch.object(routes.collector, "fetch_limit_up_pool", new=AsyncMock(return_value={"stocks": [], "total": 0, "trade_date": "2026-07-30"})),
            patch.object(routes.collector, "fetch_limit_down_pool", new=AsyncMock(return_value={"stocks": [], "total": 2, "trade_date": "2026-07-30"})),
            patch.object(routes.collector, "fetch_market_breadth", new=AsyncMock(return_value={})),
            patch.object(routes.collector, "fetch_market_turnover", new=AsyncMock(return_value={"sh_index": 3804.69, "data_date": "2026-07-30"})),
        ):
            response = await routes.get_market_overview()

        self.assertEqual(response["data"]["fund_flow"]["top_inflow"][0]["name"], "高净额")
        self.assertEqual(response["data"]["fund_flow"]["top_outflow"][0]["name"], "低净额")
        self.assertEqual(response["data"]["limit_board"], {"limit_up": 0, "limit_down": 2})


if __name__ == "__main__":
    unittest.main()
