import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

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

    async def test_closed_market_returns_verified_overview_cache_before_live_calls(self):
        cached = {
            "market_index": {"sh_index": 3804.69, "sh_change_pct": 0.42},
            "north_bound": {"latest_deal_amount": 12_000_000_000},
            "fund_flow": {"top_inflow": [{"name": "缓存板块", "inflow": 100}]},
            "limit_board": {"limit_up": 10, "limit_down": 3},
            "hot_sectors": [],
            "data_date": "2026-08-07",
        }

        with (
            patch.object(routes, "shanghai_now", return_value=datetime(2026, 8, 8, 10, 0)),
            patch.object(routes, "_read_json_snapshot", new=AsyncMock(return_value=cached)) as read_snapshot,
            patch.object(routes.collector, "fetch_tencent_index_quotes", new=AsyncMock(return_value={})),
            patch.object(routes.collector, "fetch_market_turnover", new=AsyncMock(side_effect=AssertionError("live call should not run"))),
        ):
            response = await routes.get_market_overview()

        self.assertEqual(response["data"]["source"], "cache")
        self.assertFalse(response["data"]["is_realtime"])
        self.assertEqual(response["data"]["data_date"], "2026-08-07")
        read_snapshot.assert_awaited_once_with("market_overview_v1")

    async def test_closed_market_enriches_legacy_cache_with_tencent_indices(self):
        cached = {
            "market_index": {"sh_index": None, "sh_change_pct": None},
            "market_breadth": {"全市场": {"up": 100, "down": 200}},
            "data_date": "2026-08-19",
        }
        tencent = {
            "indices": {
                "shanghai": {"value": 3894.42, "change": -95.88, "change_pct": -2.40, "amount": 1_218_107_974_056, "volume": 572_191_213},
                "chinext": {"value": 3473.49, "change_pct": -6.26},
                "hs300": {"value": 4588.70, "change_pct": -2.90},
            },
            "data_date": "2026-08-19",
            "source_updated_at": "2026-08-19T16:14:27+08:00",
            "source": "tencent",
            "is_realtime": False,
        }
        history = {
            "index_series": {
                "shanghai": [3990.30, 3894.42],
                "chinext": [3705.56, 3473.49],
                "hs300": [4725.81, 4588.70],
            }
        }

        with (
            patch.object(routes, "shanghai_now", return_value=datetime(2026, 8, 20, 8, 30)),
            patch.object(routes, "_read_json_snapshot", new=AsyncMock(return_value=cached)),
            patch.object(routes, "_write_json_snapshot", new=AsyncMock(return_value="2026-08-20T08:30:00+08:00")),
            patch.object(routes.collector, "fetch_tencent_index_quotes", new=AsyncMock(return_value=tencent)),
            patch.object(routes.collector, "fetch_tencent_index_history", new=AsyncMock(return_value=history)),
        ):
            response = await routes.get_market_overview()

        index = response["data"]["market_index"]
        self.assertEqual(index["sh_index"], 3894.42)
        self.assertEqual(index["indices"]["chinext"]["value"], 3473.49)
        self.assertEqual(index["index_series"]["hs300"], [4725.81, 4588.70])
        self.assertTrue(response["data"]["cache_used"])

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
            patch.object(routes, "_read_json_snapshot", new=AsyncMock(return_value=None)),
            patch.object(routes, "_write_json_snapshot", new=AsyncMock()),
        ):
            response = await routes.get_market_overview()

        self.assertEqual(response["data"]["fund_flow"]["top_inflow"][0]["name"], "高净额")
        self.assertEqual(response["data"]["fund_flow"]["top_outflow"][0]["name"], "低净额")
        self.assertEqual(response["data"]["limit_board"], {"limit_up": 0, "limit_down": 2})

    async def test_auction_index_does_not_replace_complete_turnover_cache(self):
        cached = {
            "market_index": {
                "sh_index": 3894.42,
                "sh_change_pct": -2.40,
                "sh_volume": 572_191_213,
                "sh_amount": 1_218_107_974_056,
            },
            "fund_flow": {"top_inflow": [], "top_outflow": []},
            "data_date": "2026-08-19",
        }
        auction_turnover = {
            "sh_index": 3894.28,
            "sh_change": -0.14,
            "sh_change_pct": 0.0,
            "sh_volume": 0,
            "sh_amount": 0,
            "data_date": "2026-08-20",
            "is_realtime": True,
            "source": "tencent",
            "indices": {
                "shanghai": {"value": 3894.28, "change_pct": 0.0},
                "chinext": {"value": 3473.49, "change_pct": 0.0},
                "hs300": {"value": 4588.61, "change_pct": 0.0},
            },
        }

        with (
            patch.object(routes, "shanghai_now", return_value=datetime(2026, 8, 20, 9, 21)),
            patch.object(routes.collector, "fetch_north_bound_daily", new=AsyncMock(return_value=[])),
            patch.object(routes.collector, "fetch_concept_flow", new=AsyncMock(return_value=[{"code": "BK0001", "name": "测试板块", "main_net_inflow": 1}])),
            patch.object(routes.collector, "fetch_limit_up_pool", new=AsyncMock(return_value={"stocks": [], "total": 0, "trade_date": "2026-08-20"})),
            patch.object(routes.collector, "fetch_limit_down_pool", new=AsyncMock(return_value={"stocks": [], "total": 0, "trade_date": "2026-08-20"})),
            patch.object(routes.collector, "fetch_market_breadth", new=AsyncMock(return_value={})),
            patch.object(routes.collector, "fetch_market_turnover", new=AsyncMock(return_value=auction_turnover)),
            patch.object(routes.collector, "fetch_tencent_index_history", new=AsyncMock(return_value={})),
            patch.object(routes, "_read_json_snapshot", new=AsyncMock(return_value=cached)),
            patch.object(routes, "_write_json_snapshot", new=AsyncMock()) as write_snapshot,
        ):
            response = await routes.get_market_overview()

        index = response["data"]["market_index"]
        self.assertEqual(index["sh_index"], 3894.28)
        self.assertEqual(index["sh_amount"], 1_218_107_974_056)
        self.assertEqual(index["indices"]["hs300"]["value"], 4588.61)
        self.assertEqual(response["data"]["refresh_status"], "partial_live_index")
        write_snapshot.assert_not_awaited()

    async def test_concept_summary_combines_true_inflow_and_outflow_rankings(self):
        inflow_rows = [
            {"code": "BK0001", "name": "净流入", "main_net_inflow": 30},
            {"code": "BK0003", "name": "小流入", "main_net_inflow": 10},
        ]
        outflow_rows = [{"code": "BK0002", "name": "净流出", "main_net_inflow": -40}]
        requested_orders = []

        async def fetch_concept_flow(*, sort_order=0, **kwargs):
            del kwargs
            requested_orders.append(sort_order)
            return inflow_rows if sort_order == 0 else outflow_rows

        with (
            patch.object(routes.collector, "fetch_concept_flow", new=fetch_concept_flow),
            patch.object(routes, "shanghai_now", return_value=datetime(2026, 7, 30, 10, 0)),
        ):
            response = await routes.get_concept_summary(range="today", board_code=None)

        rankings = response["data"]["rankings"]
        self.assertEqual([row["name"] for row in rankings], ["净流入", "小流入", "净流出"])
        self.assertEqual(response["data"]["summary"]["total_main_inflow"], 0)
        self.assertEqual(response["data"]["summary"]["outflow_board_count"], 1)
        self.assertFalse(response["data"]["summary"]["rankings_are_complete"])
        self.assertEqual(set(requested_orders), {0, 1})

    async def test_flow_observer_keeps_bidirectional_rows_in_one_snapshot(self):
        inflow_rows = [
            {"code": "BK0001", "name": "半导体", "main_net_inflow": 300_000_000},
            {"code": "BK0002", "name": "通信设备", "main_net_inflow": 100_000_000},
        ]
        outflow_rows = [
            {"code": "BK0003", "name": "房地产", "main_net_inflow": -250_000_000},
            {"code": "BK0004", "name": "银行", "main_net_inflow": -80_000_000},
        ]

        async def fetch_industry_flow(*, sort_order=0, **kwargs):
            del kwargs
            return inflow_rows if sort_order == 0 else outflow_rows

        with (
            patch.object(routes, "shanghai_now", return_value=datetime(2026, 8, 19, 10, 0)),
            patch.object(routes.collector, "fetch_industry_flow", new=fetch_industry_flow),
            patch.object(routes.collector, "fetch_market_turnover", new=AsyncMock(return_value={"sh_amount": 1_000_000_000})),
        ):
            response = await routes.get_flow_observer(board_type="industry", limit=2)

        data = response["data"]
        self.assertEqual([row["name"] for row in data["inflows"]], ["半导体", "通信设备"])
        self.assertEqual([row["name"] for row in data["outflows"]], ["房地产", "银行"])
        self.assertEqual(data["summary"]["shown_net_flow"], 70_000_000)
        self.assertEqual(data["board_label"], "行业板块")
        self.assertEqual(data["flow_inference"]["method"], "net_flow_balance")
        self.assertTrue(data["transfers"])
        self.assertTrue(all(link["inferred"] for link in data["transfers"]))

    async def test_flow_observer_rejects_unknown_board_type(self):
        with self.assertRaises(HTTPException):
            await routes.get_flow_observer(board_type="unknown", limit=4)

    async def test_flow_observer_retries_components_missing_during_proxy_wakeup(self):
        calls = {0: 0, 1: 0, "market": 0}

        async def fetch_industry_flow(*, sort_order=0, **kwargs):
            del kwargs
            calls[sort_order] += 1
            if calls[sort_order] == 1:
                return []
            if sort_order == 0:
                return [{"code": "BK0001", "name": "电子", "main_net_inflow": 300_000_000}]
            return [{"code": "BK0002", "name": "银行", "main_net_inflow": -200_000_000}]

        async def fetch_market_turnover():
            calls["market"] += 1
            return {} if calls["market"] == 1 else {"sh_amount": 1_000_000_000}

        with (
            patch.object(routes, "shanghai_now", return_value=datetime(2026, 8, 19, 10, 0)),
            patch.object(routes.collector, "fetch_industry_flow", new=fetch_industry_flow),
            patch.object(routes.collector, "fetch_market_turnover", new=fetch_market_turnover),
        ):
            response = await routes.get_flow_observer(board_type="industry", limit=2)

        self.assertEqual(calls, {0: 2, 1: 2, "market": 2})
        self.assertTrue(response["data"]["available"])
        self.assertEqual(response["data"]["source_status"], {
            "inflows": True,
            "outflows": True,
            "market": True,
        })

    async def test_stock_flow_marks_today_source_data_as_realtime(self):
        flow_data = [{"date": "2026-07-30", "main_net_inflow": 1}]

        with (
            patch.object(routes.collector, "fetch_stock_fund_flow", new=AsyncMock(return_value=flow_data)),
            patch.object(routes, "shanghai_now", return_value=datetime(2026, 7, 30, 10, 0)),
        ):
            response = await routes.get_stock_flow("600519.SH")

        self.assertTrue(response["data"]["is_realtime"])
        self.assertEqual(response["data"]["stock_code"], "600519")

    def test_undated_quote_is_not_claimed_as_realtime_before_market_open(self):
        with patch.object(routes, "shanghai_now", return_value=datetime(2026, 8, 3, 2, 0)):
            metadata = routes._quote_metadata(available=True)

        self.assertFalse(metadata["is_realtime"])
        self.assertIsNone(metadata["data_date"])


if __name__ == "__main__":
    unittest.main()
