import unittest
from datetime import datetime
from unittest.mock import patch

from services.data_collector import EastMoneyDataCollector, normalize_stock_code, stock_secid


class DataCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_stock_universe_fetches_every_page_and_new_prefixes(self):
        collector = EastMoneyDataCollector()
        rows = [
            {"f12": f"60{index:04d}", "f13": 1, "f14": f"测试{index}"}
            for index in range(199)
        ] + [
            {"f12": "302132", "f13": 0, "f14": "中航成飞"},
            {"f12": "920065", "f13": 0, "f14": "千岸科技"},
        ]
        calls = []

        async def fake_fetch_json(url, params, headers=None):
            del url, headers
            page = int(params["pn"])
            page_size = int(params["pz"])
            calls.append((page, page_size))
            start = (page - 1) * page_size
            return {"data": {"total": len(rows), "diff": rows[start:start + page_size]}}

        collector.fetch_json = fake_fetch_json
        universe = await collector.fetch_stock_universe()

        self.assertEqual(len(universe), len(rows))
        self.assertEqual({page for page, _ in calls}, {1, 2, 3})
        self.assertTrue(all(page_size == 100 for _, page_size in calls))
        self.assertIn("302132", {item["code"] for item in universe})
        self.assertIn("920065", {item["code"] for item in universe})
        self.assertEqual(normalize_stock_code("920065"), "920065")

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

        async def fake_fetch_json(url, params, headers=None):
            del url, headers
            self.assertEqual(params["fields"], "f43,f47,f48,f57,f58,f169,f170")
            return {
                "data": {
                    "f43": 380469,
                    "f47": 592298923,
                    "f48": 1106477266461.8,
                    "f169": -2378,
                    "f170": -62,
                }
            }

        collector.fetch_json = fake_fetch_json
        result = await collector.fetch_market_turnover()

        self.assertEqual(result["sh_index"], 3804.69)
        self.assertEqual(result["sh_change"], -23.78)
        self.assertEqual(result["sh_change_pct"], -0.62)

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


if __name__ == "__main__":
    unittest.main()
