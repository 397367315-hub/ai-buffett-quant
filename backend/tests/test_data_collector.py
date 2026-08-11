import asyncio
import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from services.data_collector import EastMoneyDataCollector, normalize_stock_code, stock_secid


class DataCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_rotation_does_not_call_positive_inflow_an_outflow(self):
        collector = EastMoneyDataCollector()
        collector.fetch_all_concept_flow = AsyncMock(return_value=[
            {"code": "BK001", "name": "强流入", "main_net_inflow": 200, "change_pct": 1},
            {"code": "BK002", "name": "弱流入", "main_net_inflow": 10, "change_pct": 0.2},
        ])

        result = await collector.fetch_sector_rotation()

        self.assertEqual(result["hot_outflow"], [])
        self.assertFalse(result["outflow_data_available"])

    async def test_block_trade_snapshot_is_reused_for_concurrent_page_requests(self):
        collector = EastMoneyDataCollector()
        calls = 0

        async def fake_fetch_json(url, params, headers=None):
            nonlocal calls
            del url, params, headers
            calls += 1
            await asyncio.sleep(0)
            return {"result": {"data": [{
                "SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "贵州茅台",
                "TRADE_DATE": "2026-08-10", "DEAL_AMT": 1000000,
                "DEAL_PRICE": 1400, "PREMIUM_RATIO": 0.01,
                "DEAL_VOLUME": 1000, "BUYER_NAME": "机构专用", "SELLER_NAME": "营业部",
            }]}}

        collector.fetch_json = fake_fetch_json
        first, second = await asyncio.gather(
            collector.fetch_block_trades(),
            collector.fetch_block_trades(),
        )

        self.assertEqual(calls, 1)
        self.assertEqual(first, second)
        self.assertEqual(first[0]["code"], "600519")

    async def test_dragon_board_target_date_is_forwarded_to_eastmoney_filter(self):
        collector = EastMoneyDataCollector()
        calls = []

        async def fake_fetch_json(url, params, headers=None):
            del url, headers
            calls.append(dict(params))
            return {"result": {"data": []}}

        collector.fetch_json = fake_fetch_json
        await collector.fetch_dragon_board(target_date=date(2026, 8, 6))

        self.assertEqual(calls[0]["filter"], "(TRADE_DATE='2026-08-06')")

    async def test_data_source_timeout_is_capped_for_stale_deploy_settings(self):
        collector = EastMoneyDataCollector()

        with patch("services.data_collector.settings.data_proxy_timeout", 90.0):
            self.assertEqual(collector._request_timeout(), 20.0)

    async def test_configured_proxy_does_not_fall_back_to_overseas_request(self):
        collector = EastMoneyDataCollector()
        collector._fetch_via_proxy = AsyncMock(side_effect=RuntimeError("proxy unavailable"))
        collector._fetch_direct = AsyncMock()

        with patch("services.data_collector.settings.data_proxy_base_url", "https://proxy.example"):
            with self.assertRaisesRegex(RuntimeError, "proxy unavailable"):
                await collector.fetch_json("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get", {})

        collector._fetch_direct.assert_not_awaited()

    async def test_stock_universe_fetches_every_page_and_new_prefixes(self):
        collector = EastMoneyDataCollector()
        rows = [
            {"f2": 10.0, "f12": f"60{index:04d}", "f13": 1, "f14": f"测试{index}"}
            for index in range(199)
        ] + [
            {"f2": 10.0, "f12": "302132", "f13": 0, "f14": "中航成飞"},
            {"f2": 10.0, "f12": "920065", "f13": 0, "f14": "千岸科技"},
        ]
        calls = []

        async def fake_fetch_json(url, params, headers=None):
            del url, headers
            page = int(params["pn"])
            page_size = int(params["pz"])
            calls.append(dict(params))
            start = (page - 1) * page_size
            return {"data": {"total": len(rows), "diff": rows[start:start + page_size]}}

        collector.fetch_json = fake_fetch_json
        universe = await collector.fetch_stock_universe()

        self.assertEqual(len(universe), len(rows))
        self.assertEqual({int(call["pn"]) for call in calls}, {1, 2, 3})
        self.assertTrue(all(int(call["pz"]) == 100 for call in calls))
        self.assertTrue(all(call["fid"] == "f12" for call in calls))
        self.assertIn("302132", {item["code"] for item in universe})
        self.assertIn("920065", {item["code"] for item in universe})
        self.assertEqual(normalize_stock_code("920065"), "920065")

    async def test_stock_universe_excludes_delisted_zero_price_symbols(self):
        collector = EastMoneyDataCollector()

        async def fake_fetch_json(url, params, headers=None):
            del url, headers
            self.assertIn("f100", params["fields"])
            return {
                "data": {
                    "total": 3,
                    "diff": [
                        {"f2": 1361.76, "f12": "600519", "f13": 1, "f14": "贵州茅台", "f100": "白酒Ⅱ"},
                        {"f2": 0, "f12": "600001", "f13": 1, "f14": "邯郸钢铁"},
                        {"f2": "-", "f12": "000003", "f13": 0, "f14": "PT金田A"},
                    ],
                }
            }

        collector.fetch_json = fake_fetch_json
        universe = await collector.fetch_stock_universe()

        self.assertEqual(universe, [{"code": "600519", "name": "贵州茅台", "market": 1, "sector": "白酒Ⅱ"}])

    async def test_security_directory_retains_inactive_symbols_for_pit_master(self):
        collector = EastMoneyDataCollector()

        async def fake_fetch_json(url, params, headers=None):
            del url, params, headers
            return {"data": {"total": 2, "diff": [
                {"f2": 1361.76, "f12": "600519", "f13": 1, "f14": "贵州茅台"},
                {"f2": "-", "f12": "000003", "f13": 0, "f14": "PT金田A"},
            ]}}

        collector.fetch_json = fake_fetch_json
        directory = await collector.fetch_security_directory()

        self.assertEqual(len(directory), 2)
        self.assertTrue(next(item for item in directory if item["code"] == "600519")["is_currently_listed"])
        self.assertFalse(next(item for item in directory if item["code"] == "000003")["is_currently_listed"])

    def test_limit_pool_uses_actual_consecutive_board_height(self):
        collector = EastMoneyDataCollector()
        item = collector._pool_item({
            "c": "600000", "n": "测试", "p": 10000, "lbc": 4,
            "zttj": {"days": 8, "ct": 5}, "zbc": 2,
        }, "up")

        self.assertEqual(item["continuous_days"], 4)
        self.assertEqual(item["limit_days_in_window"], 8)
        self.assertEqual(item["limit_count_in_window"], 5)
        self.assertEqual(item["failed_attempts"], 2)

    async def test_failed_limit_pool_forwards_historical_trade_date(self):
        collector = EastMoneyDataCollector()
        captured = {}

        async def fake_fetch_json(url, params, headers=None):
            del headers
            captured.update({"url": url, "date": params["date"]})
            return {"data": {"tc": 3, "qdate": "20260807", "pool": []}}

        collector.fetch_json = fake_fetch_json
        result = await collector.fetch_failed_limit_pool(target_date=date(2026, 8, 7))

        self.assertIn("getTopicZBPool", captured["url"])
        self.assertEqual(captured["date"], "20260807")
        self.assertEqual(result["total"], 3)

    async def test_market_breadth_is_derived_from_complete_cached_snapshot(self):
        snapshot = {
            "data_date": "2026-08-07",
            "stocks": [
                {"code": "600519", "price": 10, "change_pct": 1.0},
                {"code": "000001", "price": 10, "change_pct": -0.5},
                {"code": "920065", "price": 10, "change_pct": 0.0},
            ],
        }
        with patch("quant.market_cache.load_quant_market_snapshot", new=AsyncMock(return_value=snapshot)):
            breadth = await EastMoneyDataCollector().fetch_market_breadth()

        self.assertEqual(breadth["全市场"]["up"], 1)
        self.assertEqual(breadth["全市场"]["down"], 1)
        self.assertEqual(breadth["全市场"]["flat"], 1)
        self.assertEqual(breadth["全市场"]["ratio"], 50.0)

    async def test_quant_market_snapshot_paginates_complete_code_sorted_quotes(self):
        collector = EastMoneyDataCollector()
        rows = [
            {
                "f2": 10 + index / 100, "f3": 1.2, "f8": 3.1, "f9": 18,
                "f10": 1.4, "f12": f"600{index:03d}", "f14": f"测试{index}",
                "f20": 20_000_000_000, "f23": 2, "f37": 10, "f62": 50_000_000,
                "f100": "软件开发", "f184": 2.5,
            }
            for index in range(205)
        ]
        rows[10]["f2"] = 0
        rows[11]["f14"] = "ST测试"
        calls = []

        async def fake_fetch_json(url, params, headers=None):
            del url, headers
            calls.append(dict(params))
            page = int(params["pn"])
            size = int(params["pz"])
            start = (page - 1) * size
            return {"data": {"total": len(rows), "diff": rows[start:start + size]}}

        collector.fetch_json = fake_fetch_json
        snapshot = await collector.fetch_quant_market_snapshot()

        self.assertTrue(snapshot["complete"])
        self.assertEqual(snapshot["upstream_total"], 205)
        self.assertEqual(snapshot["total"], 203)
        self.assertEqual({int(call["pn"]) for call in calls}, {1, 2, 3})
        self.assertTrue(all(call["fid"] == "f12" and call["po"] == "0" for call in calls))
        self.assertEqual(snapshot["stocks"], sorted(snapshot["stocks"], key=lambda item: item["code"]))

    async def test_small_stock_quote_refresh_uses_validated_exchange_codes(self):
        collector = EastMoneyDataCollector()
        calls = []

        async def fake_fetch_json(url, params, headers=None):
            del url, headers
            calls.append(dict(params))
            return {"data": {"diff": [
                {"f2": 12.34, "f12": "600000", "f14": "测试600000", "f124": 0},
                {"f2": 12.34, "f12": "000001", "f14": "测试000001", "f124": 0},
            ]}}

        collector.fetch_json = fake_fetch_json
        with patch("services.data_collector.is_a_share_market_session", return_value=True):
            snapshot = await collector.fetch_stock_quotes(["600000", "000001", "600000"])

        self.assertEqual([item["code"] for item in snapshot["stocks"]], ["600000", "000001"])
        self.assertTrue(all(item["price"] == 12.34 for item in snapshot["stocks"]))
        self.assertEqual(calls[0]["secids"], "1.600000,0.000001")
        self.assertTrue(snapshot["complete"])

    async def test_small_quote_refresh_supports_shanghai_and_shenzhen_etfs(self):
        collector = EastMoneyDataCollector()
        calls = []

        async def fake_fetch_json(url, params, headers=None):
            del url, headers
            calls.append(dict(params))
            return {"data": {"diff": [
                {"f2": 10.0, "f3": 1.25, "f12": "510300", "f14": "ETF510300", "f124": 0},
                {"f2": 10.0, "f3": 1.25, "f12": "159992", "f14": "ETF159992", "f124": 0},
            ]}}

        collector.fetch_json = fake_fetch_json
        with patch("services.data_collector.is_a_share_market_session", return_value=True):
            snapshot = await collector.fetch_stock_quotes(["510300", "159992"])

        self.assertEqual(calls[0]["secids"], "1.510300,0.159992")
        self.assertEqual([item["price"] for item in snapshot["stocks"]], [10.0, 10.0])
        self.assertEqual([item["change_pct"] for item in snapshot["stocks"]], [1.25, 1.25])

    def test_stock_code_exchange_qualifiers_must_match_the_code(self):
        self.assertEqual(normalize_stock_code("SH600519"), "600519")
        self.assertEqual(normalize_stock_code("000001.SZ"), "000001")
        self.assertEqual(normalize_stock_code("BJ.920065"), "920065")
        self.assertEqual(stock_secid("SH600519"), "1.600519")

        with self.assertRaises(ValueError):
            normalize_stock_code("600519.SZ")
        with self.assertRaises(ValueError):
            normalize_stock_code("SH000001")
        with self.assertRaises(ValueError):
            normalize_stock_code("SH600519.SZ")

    async def test_market_turnover_uses_change_amount_and_change_percent(self):
        collector = EastMoneyDataCollector()
        source_at = datetime(2026, 8, 12, 14, 55, tzinfo=ZoneInfo("Asia/Shanghai"))

        async def fake_fetch_json(url, params, headers=None):
            del url, headers
            self.assertEqual(params["fields"], "f43,f47,f48,f57,f58,f124,f169,f170")
            return {
                "data": {
                    "f43": 380469,
                    "f47": 592298923,
                    "f48": 1106477266461.8,
                    "f124": int(source_at.timestamp()),
                    "f169": -2378,
                    "f170": -62,
                }
            }

        collector.fetch_json = fake_fetch_json
        with patch(
            "services.data_collector.shanghai_now",
            return_value=datetime(2026, 8, 12, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        ):
            result = await collector.fetch_market_turnover()

        self.assertEqual(result["sh_index"], 3804.69)
        self.assertEqual(result["sh_change"], -23.78)
        self.assertEqual(result["sh_change_pct"], -0.62)
        self.assertEqual(result["data_date"], "2026-08-12")
        self.assertTrue(result["is_realtime"])

    async def test_security_directory_retries_a_timed_out_first_page(self):
        collector = EastMoneyDataCollector()
        attempts = 0

        async def fake_fetch_json(url, params, headers=None):
            nonlocal attempts
            del url, headers
            self.assertEqual(params["pn"], "1")
            attempts += 1
            if attempts < 3:
                raise TimeoutError("upstream timeout")
            return {"data": {"total": 1, "diff": [
                {"f2": 10.2, "f12": "600000", "f13": 1, "f14": "浦发银行", "f100": "银行"},
            ]}}

        collector.fetch_json = fake_fetch_json
        with patch("services.data_collector.asyncio.sleep", new=AsyncMock()):
            result = await collector.fetch_security_directory_snapshot(allow_partial=False)

        self.assertEqual(attempts, 3)
        self.assertTrue(result["complete"])
        self.assertEqual(result["records"][0]["code"], "600000")

    async def test_security_directory_returns_explicit_partial_metadata(self):
        collector = EastMoneyDataCollector()
        page_two_attempts = 0
        first_page = [
            {"f2": 10, "f12": f"600{index:03d}", "f13": 1, "f14": f"测试{index}", "f100": "测试"}
            for index in range(100)
        ]

        async def fake_fetch_json(url, params, headers=None):
            nonlocal page_two_attempts
            del url, headers
            if params["pn"] == "1":
                return {"data": {"total": 150, "diff": first_page}}
            page_two_attempts += 1
            raise TimeoutError("page two timeout")

        collector.fetch_json = fake_fetch_json
        with patch("services.data_collector.asyncio.sleep", new=AsyncMock()):
            result = await collector.fetch_security_directory_snapshot(allow_partial=True)

        self.assertEqual(page_two_attempts, 3)
        self.assertFalse(result["complete"])
        self.assertEqual(result["failed_pages"], [2])
        self.assertEqual(result["errors"], {"2": "TimeoutError"})
        self.assertEqual(len(result["records"]), 100)

    async def test_board_flow_normalizes_eastmoney_sort_direction(self):
        collector = EastMoneyDataCollector()
        calls = []

        async def fake_fetch_json(url, params, headers=None):
            del url, headers
            calls.append(params["po"])
            return {"data": {"diff": []}}

        collector.fetch_json = fake_fetch_json
        await collector.fetch_concept_flow(sort_order=0)
        await collector.fetch_concept_flow(sort_order=1)

        self.assertEqual(calls, ["1", "0"])

    async def test_complete_board_directory_uses_stable_code_pagination(self):
        collector = EastMoneyDataCollector()
        rows = [
            {"f12": f"BK{index:04d}", "f14": f"板块{index}", "f62": index}
            for index in range(205)
        ]
        calls = []

        async def fake_fetch_json(url, params, headers=None):
            del url, headers
            calls.append(dict(params))
            page = int(params["pn"])
            page_size = int(params["pz"])
            start = (page - 1) * page_size
            return {"data": {"total": len(rows), "diff": rows[start:start + page_size]}}

        collector.fetch_json = fake_fetch_json
        result = await collector.fetch_all_industry_flow()

        self.assertEqual(len(result), len(rows))
        self.assertEqual({int(call["pn"]) for call in calls}, {1, 2, 3})
        self.assertTrue(all(call["fid"] == "f12" for call in calls))
        self.assertEqual({row["code"] for row in result}, {row["f12"] for row in rows})

    async def test_complete_board_constituents_are_paginated_and_annotated(self):
        collector = EastMoneyDataCollector()
        rows = [
            {"code": f"600{index:03d}", "name": f"测试{index}", "price": 10 + index}
            for index in range(205)
        ]

        async def fake_fetch_board_stocks(board_code, page=1, page_size=100, sort_field="f62"):
            self.assertEqual(board_code, "BK0475")
            self.assertEqual(sort_field, "f12")
            start = (page - 1) * page_size
            return {
                "total": len(rows),
                "stocks": rows[start:start + page_size],
                "page": page,
                "page_size": page_size,
                "board_code": board_code,
            }

        collector.fetch_board_stocks = AsyncMock(side_effect=fake_fetch_board_stocks)
        result = await collector.fetch_all_board_stocks("BK0475", sector_name="软件开发")

        self.assertEqual(result["total"], 205)
        self.assertEqual(result["tradable_total"], 205)
        self.assertTrue(result["complete"])
        self.assertEqual({call.kwargs["page"] for call in collector.fetch_board_stocks.await_args_list}, {1, 2, 3})
        self.assertTrue(all(stock["sector"] == "软件开发" for stock in result["stocks"]))
        self.assertTrue(all(stock["selection_sources"] == ["industry_constituent"] for stock in result["stocks"]))

    async def test_stock_selection_sector_directory_uses_all_live_industry_boards(self):
        collector = EastMoneyDataCollector()
        collector.fetch_stock_universe = AsyncMock(return_value=[
            *[
                {"code": f"600{index:03d}", "name": f"软件{index}", "sector": "软件开发"}
                for index in range(240)
            ],
            *[
                {"code": f"000{index:03d}", "name": f"银行{index}", "sector": "银行"}
                for index in range(40)
            ],
        ])
        collector.fetch_all_industry_flow = AsyncMock(return_value=[
            {
                "code": "BK0475", "name": "软件开发", "change_pct": 2.5,
                "main_net_inflow": 800_000_000, "main_net_inflow_pct": 4.2,
                "up_count": 201, "down_count": 35, "flat_count": 4,
                "leading_stock": "测试龙头",
            },
            {
                "code": "BK0477", "name": "银行", "change_pct": -0.2,
                "main_net_inflow": -100_000_000, "main_net_inflow_pct": -0.5,
                "up_count": 8, "down_count": 30, "flat_count": 2,
                "leading_stock": "",
            },
        ])
        collector.fetch_intelligent_selection_candidates = AsyncMock()

        sectors = await collector.fetch_intelligent_selection_sectors()

        self.assertEqual([item["code"] for item in sectors], ["BK0475", "BK0477"])
        self.assertEqual(sectors[0]["stock_count"], 240)
        self.assertEqual(sectors[0]["candidate_count"], 240)
        self.assertEqual(sectors[0]["count_source"], "stock_universe")
        self.assertEqual(sectors[0]["heat_rank"], 1)
        collector.fetch_intelligent_selection_candidates.assert_not_awaited()

    async def test_sector_seed_refreshes_one_live_page_without_full_directory_scan(self):
        collector = EastMoneyDataCollector()
        seed = [
            {
                "code": "BK0475", "name": "软件开发", "stock_count": 133,
                "count_source": "stock_universe", "main_net_inflow": 1,
            },
            {
                "code": "BK0477", "name": "银行", "stock_count": 42,
                "count_source": "stock_universe", "main_net_inflow": 1,
            },
        ]
        collector.fetch_industry_flow = AsyncMock(return_value=[{
            "code": "BK0475", "name": "软件开发", "change_pct": 6.15,
            "main_net_inflow": 4_618_064_640, "main_net_inflow_pct": 5.85,
            "up_count": 133, "down_count": 0, "flat_count": 0,
            "leading_stock": "普联软件",
        }])
        collector.fetch_all_industry_flow = AsyncMock()
        collector.fetch_stock_universe = AsyncMock()

        sectors = await collector.fetch_intelligent_selection_sectors(seed_sectors=seed)

        collector.fetch_industry_flow.assert_awaited_once_with(page_size=100)
        collector.fetch_all_industry_flow.assert_not_awaited()
        collector.fetch_stock_universe.assert_not_awaited()
        self.assertEqual([item["code"] for item in sectors], ["BK0475", "BK0477"])
        self.assertEqual(sectors[0]["main_net_inflow"], 4_618_064_640)
        self.assertEqual(sectors[1]["main_net_inflow"], 1)

    async def test_sector_seed_timeout_returns_verified_cache_within_deadline(self):
        collector = EastMoneyDataCollector()
        seed = [{
            "code": "BK0475", "name": "软件开发", "stock_count": 133,
            "candidate_count": 133, "count_source": "stock_universe",
            "change_pct": 2.5, "main_net_inflow": 800_000_000,
            "main_net_inflow_pct": 4.2, "up_count": 100,
            "down_count": 30, "flat_count": 3, "leading_stock": "测试龙头",
            "heat_rank": 1,
        }]

        async def blocked_refresh(*, page_size):
            self.assertEqual(page_size, 100)
            await asyncio.sleep(1)
            return []

        collector.fetch_industry_flow = AsyncMock(side_effect=blocked_refresh)
        started_at = asyncio.get_running_loop().time()
        with patch("services.data_collector.settings.stock_selection_sector_refresh_timeout", 0.01):
            sectors = await collector.fetch_intelligent_selection_sectors(seed_sectors=seed)
        elapsed = asyncio.get_running_loop().time() - started_at

        self.assertLess(elapsed, 0.1)
        self.assertEqual(len(sectors), 1)
        self.assertEqual(sectors[0]["code"], "BK0475")
        self.assertEqual(sectors[0]["stock_count"], 133)
        self.assertEqual(sectors[0]["main_net_inflow"], 800_000_000)

    async def test_technical_screener_uses_live_descending_quotes_and_skips_zero_price(self):
        collector = EastMoneyDataCollector()
        captured = {}

        async def fake_fetch_json(url, params, headers=None):
            del url, headers
            captured.update(params)
            return {
                "data": {
                    "diff": [
                        {"f2": 0, "f12": "000003", "f14": "PT金田A"},
                        {
                            "f2": 25.6, "f3": 3.2, "f5": 1_000_000, "f6": 20_000_000,
                            "f8": 4.1, "f9": 18.5, "f10": 2.4, "f12": "600519",
                            "f14": "贵州茅台", "f20": 100_000_000, "f23": 6.0,
                            "f37": 22.1, "f62": 300_000_000, "f66": 120_000_000,
                            "f69": 2.1, "f72": 80_000_000, "f75": 1.4,
                            "f100": "白酒", "f184": 5.5,
                        },
                    ]
                }
            }

        collector.fetch_json = fake_fetch_json
        result = await collector.fetch_technical_screener({
            "min_change": 1,
            "max_pe": 100,
            "min_turnover": 1,
        })

        self.assertEqual(captured["po"], "1")
        self.assertEqual(captured["fid"], "f10")
        self.assertEqual(
            captured["fields"],
            "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f23,f37,f62,f66,f69,f72,f75,f100,f124,f184",
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["stocks"][0]["code"], "600519")
        self.assertEqual(result["stocks"][0]["sector"], "白酒")
        self.assertEqual(result["stocks"][0]["volume"], 100_000_000)
        self.assertEqual(result["stocks"][0]["amount"], 20_000_000)
        self.assertEqual(result["stocks"][0]["large_net_inflow"], 80_000_000)
        self.assertEqual(result["stocks"][0]["large_order_inflow_pct"], 1.4)

    async def test_stock_history_uses_ftshare_only_after_primary_source_fails(self):
        collector = EastMoneyDataCollector()
        collector.fetch_json = AsyncMock(side_effect=RuntimeError("proxy unavailable"))
        ftshare_rows = [
            {
                "ts_millis": 1785308400000,
                "open": "1333.83", "close": "1321.00", "high": "1343.48", "low": "1312.06",
                "volume": 6_232_985, "turnover": "8282396818.64",
            },
            {
                "ts_millis": 1785394800000,
                "open": "1323.00", "close": "1361.76", "high": "1362.00", "low": "1322.00",
                "volume": 7_187_261, "turnover": "9712135434.49",
            },
        ]

        with patch(
            "services.data_collector.ftshare_mcp_client.get_daily_ohlc",
            new=AsyncMock(return_value=ftshare_rows),
        ), patch("services.data_collector.shanghai_now", return_value=datetime(2026, 7, 31)):
            result = await collector.fetch_stock_price_history("600519", days=365)

        self.assertEqual(result["source"], "ftshare_mcp")
        self.assertEqual([item["trade_date"] for item in result["history"]], ["2026-07-29", "2026-07-30"])
        self.assertAlmostEqual(result["history"][1]["change_pct"], 3.0855, places=3)

    async def test_intelligent_selection_candidates_merge_multiple_live_rankings(self):
        collector = EastMoneyDataCollector()
        collector.fetch_technical_screener = AsyncMock(side_effect=[
            {"stocks": [{"code": "600519", "main_net_inflow": 300_000_000, "volume_ratio": 1.0, "change_pct": 2.0}]},
            {"stocks": [{"code": "600519", "main_net_inflow": 300_000_000, "volume_ratio": 2.5, "change_pct": 2.0}, {"code": "000001", "main_net_inflow": 0, "volume_ratio": 4.0, "change_pct": 1.0}]},
            {"stocks": [{"code": "000001", "main_net_inflow": 0, "volume_ratio": 4.0, "change_pct": 5.0}]},
        ])

        result = await collector.fetch_intelligent_selection_candidates(page_size=100)

        self.assertEqual(result["total"], 2)
        by_code = {stock["code"]: stock for stock in result["stocks"]}
        self.assertEqual(by_code["600519"]["selection_sources"], ["fund_flow", "volume"])
        self.assertEqual(by_code["000001"]["selection_sources"], ["volume", "momentum"])

    async def test_intelligent_selection_uses_ftshare_when_all_primary_rankings_are_empty(self):
        collector = EastMoneyDataCollector()
        collector.fetch_technical_screener = AsyncMock(return_value={"stocks": []})
        fallback_rows = [{
            "symbol": "600519.XSHG", "name": "贵州茅台", "close": "1361.76",
            "change_rate": 0.032, "volume": 7_187_261, "turnover": "9712135434.49",
            "turnover_rate": 0.014, "pe_ttm": 20.7, "float_a_market_cap": "1700000000000",
        }]
        with patch(
            "services.data_collector.ftshare_mcp_client.get_stock_filter",
            new=AsyncMock(return_value=fallback_rows),
        ):
            result = await collector.fetch_intelligent_selection_candidates(page_size=100)

        self.assertEqual(result["source"], "ftshare_mcp")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["stocks"][0]["code"], "600519")
        self.assertEqual(result["stocks"][0]["change_pct"], 3.2)
        self.assertEqual(result["stocks"][0]["turnover"], 1.4)
        self.assertEqual(result["stocks"][0]["sector"], "")
        self.assertIsNone(result["stocks"][0]["main_net_inflow"])

    async def test_tencent_history_preserves_known_fields_and_nulls_unknown_ones(self):
        collector = EastMoneyDataCollector()

        async def fake_fetch_json(url, params, headers=None):
            self.assertEqual(url, collector.TENCENT_KLINE_URL)
            self.assertEqual(params["param"], "sh600519,day,,,385,qfq")
            self.assertEqual(headers, collector.TENCENT_HEADERS)
            return {
                "code": 0,
                "data": {
                    "sh600519": {
                        "qfqday": [
                            ["2025-07-29", "10", "10", "10.5", "9.5", "100"],
                            ["2025-07-30", "11", "11", "11.5", "10.5", "123"],
                            ["2026-07-30", "12", "12", "13", "11", "200"],
                        ],
                        "qt": {"sh600519": ["1", "贵州茅台", "600519"]},
                    }
                },
            }

        collector.fetch_json = fake_fetch_json
        with patch("services.data_collector.shanghai_now", return_value=datetime(2026, 7, 30)):
            result = await collector.fetch_stock_price_history("600519", 365)

        self.assertEqual(result["source"], "tencent")
        self.assertEqual(result["name"], "贵州茅台")
        self.assertEqual([item["trade_date"] for item in result["history"]], ["2025-07-30", "2026-07-30"])
        first = result["history"][0]
        self.assertEqual(first["volume"], 12300)
        self.assertIsNone(first["amount"])
        self.assertIsNone(first["turnover"])
        self.assertAlmostEqual(first["change_pct"], 10.0)

    async def test_tencent_star_market_volume_is_already_in_shares(self):
        collector = EastMoneyDataCollector()

        async def fake_fetch_json(url, params, headers=None):
            self.assertEqual(url, collector.TENCENT_KLINE_URL)
            self.assertEqual(params["param"], "sh688432,day,,,385,qfq")
            return {
                "data": {
                    "sh688432": {
                        "qfqday": [
                            ["2025-07-30", "10", "10", "10.5", "9.5", "68996467"],
                            ["2026-07-30", "11", "11", "11.5", "10.5", "74872970"],
                        ],
                    }
                },
            }

        collector.fetch_json = fake_fetch_json
        with patch("services.data_collector.shanghai_now", return_value=datetime(2026, 7, 30)):
            result = await collector.fetch_stock_price_history("688432", 365)

        self.assertEqual(result["history"][0]["volume"], 68_996_467)
        self.assertEqual(result["history"][1]["volume"], 74_872_970)

    async def test_shanghai_index_history_uses_tencent_closes(self):
        collector = EastMoneyDataCollector()

        async def fake_fetch_json(url, params, headers=None):
            self.assertEqual(url, collector.TENCENT_KLINE_URL)
            self.assertEqual(params["param"], "sh000001,day,,,385,qfq")
            self.assertEqual(headers, collector.TENCENT_HEADERS)
            return {
                "data": {
                    "sh000001": {
                        "day": [
                            ["2025-07-29", "3800", "3801"],
                            ["2025-07-30", "3801", "3810"],
                            ["2026-07-30", "3810", "3804.69"],
                        ]
                    }
                }
            }

        collector.fetch_json = fake_fetch_json
        with patch("services.data_collector.shanghai_now", return_value=datetime(2026, 7, 30)):
            result = await collector.fetch_shanghai_index_history(365)

        self.assertEqual(result, [
            {"date": "2025-07-30", "close": 3810.0},
            {"date": "2026-07-30", "close": 3804.69},
        ])

    def test_tencent_quote_parser_preserves_code_units_and_timestamp(self):
        values = [""] * 88
        values[1] = "贵州茅台"
        values[2] = "600519"
        values[3] = "1309.22"
        values[4] = "1308.55"
        values[5] = "1308.66"
        values[30] = "20260807145500"
        values[31] = "0.67"
        values[32] = "0.05"
        values[33] = "1315.28"
        values[34] = "1301.00"
        values[36] = "24976"
        values[38] = "0.20"
        values[39] = "19.79"
        values[44] = "16366.32"
        values[46] = "7.03"
        values[49] = "0.63"
        values[57] = "326691.9421"

        stocks = EastMoneyDataCollector._parse_tencent_quote_text(
            f'v_sh600519="{"~".join(values)}";'
        )

        self.assertEqual(len(stocks), 1)
        stock = stocks[0]
        self.assertEqual(stock["code"], "600519")
        self.assertEqual(stock["volume"], 2_497_600)
        self.assertEqual(stock["amount"], 3_266_919_421)
        self.assertEqual(stock["market_cap"], 1_636_632_000_000)
        self.assertEqual(stock["quote_source"], "tencent")
        self.assertIsInstance(stock["quote_timestamp"], int)

    async def test_stock_quotes_use_one_eastmoney_batch_during_market(self):
        collector = EastMoneyDataCollector()
        collector.fetch_json = AsyncMock(return_value={
            "data": {
                "diff": [
                    {"f2": 11.2, "f3": 1.0, "f12": "000001", "f14": "平安银行", "f124": 1786080000},
                    {"f2": 1309.2, "f3": 0.1, "f12": "600519", "f14": "贵州茅台", "f124": 1786080000},
                ]
            }
        })
        with patch("services.data_collector.is_a_share_market_session", return_value=True):
            result = await collector.fetch_stock_quotes(["000001", "600519"])

        collector.fetch_json.assert_awaited_once()
        self.assertEqual(result["source"], "eastmoney")
        self.assertTrue(result["complete"])
        self.assertEqual([item["code"] for item in result["stocks"]], ["000001", "600519"])


if __name__ == "__main__":
    unittest.main()
