import asyncio
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from strong_stock_decision.registry import BOOK_SKILL_DEFINITIONS
from strong_stock_decision.service import StrongStockDecisionService, _merge_flow_rows


def make_bars(count: int = 90, *, close_start: float = 10.0, sparse: bool = False) -> list[dict]:
    rows = []
    previous = close_start
    for index in range(count):
        close = close_start + index * 0.04 + (0.12 if index % 7 == 0 else 0)
        change = (close / previous - 1) * 100 if index else 0
        row = {
            "trade_date": date(2026, 1, 1) + timedelta(days=index),
            "open": close - 0.03,
            "close": close,
            "high": close + 0.08,
            "low": close - 0.08,
            "volume": 100000 + index * 1200,
            "amount": 1000000 + index * 15000,
            "turnover": 4.0,
            "change_pct": change,
            "source": "test",
        }
        if sparse:
            row["high"] = None
            row["low"] = None
        rows.append(row)
        previous = close
    return rows


class StrongStockRegistryTests(unittest.TestCase):
    def test_registry_has_all_three_books_and_unique_skill_ids(self):
        ids = [item["skill_id"] for item in BOOK_SKILL_DEFINITIONS]
        self.assertEqual(len(ids), 43)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({item["book"] for item in BOOK_SKILL_DEFINITIONS}, {"猎取强势股", "暴涨大形态", "暴涨之星"})

    def test_empty_build_keeps_complete_signal_contract(self):
        service = StrongStockDecisionService()
        result = service._build({
            "symbol": "000001", "name": "测试", "bars": [], "flow": [],
            "sector_flow": [], "sector": None, "quote": None,
            "quote_is_realtime": False, "source_status": {},
        })
        self.assertEqual(len(result["signals"]), 43)
        self.assertEqual({item["status"] for item in result["signals"]}, {"NOT_FOUND"})
        self.assertEqual(result["decision"]["action"], "NO_TRADE")

    def test_sparse_ohlcv_is_explicitly_proxy_based(self):
        service = StrongStockDecisionService()
        bars = make_bars(sparse=True)
        result = service._build({
            "symbol": "000001", "name": "测试", "bars": bars, "flow": [],
            "sector_flow": [], "sector": None, "quote": None,
            "quote_is_realtime": False, "source_status": {"daily_bars": "available"},
        })
        triangle = next(item for item in result["big_patterns"] if item["skill_id"] == "BXDT_003")
        self.assertTrue(any(e.get("value") == "CLOSE_PROXY" for e in triangle["evidence"]))
        self.assertLessEqual(triangle["confidence"] or 0, 48)

    def test_composite_score_matches_exposed_components(self):
        service = StrongStockDecisionService()
        result = service._build({
            "symbol": "000001", "name": "测试", "bars": make_bars(), "flow": [],
            "sector_flow": [], "sector": None, "quote": None,
            "quote_is_realtime": False, "source_status": {},
        })
        score = result["composite_score"]
        components = [item for item in score["components"] if item["available"]]
        expected = sum(item["value"] * item["weight"] for item in components) / sum(item["weight"] for item in components)
        self.assertEqual(score["component_count"], 6)
        self.assertEqual(score["available_count"], len(components))
        self.assertAlmostEqual(score["value"], expected, places=1)

    def test_risk_c_zone_has_priority_over_attack(self):
        service = StrongStockDecisionService()
        bars = make_bars(90, close_start=30)
        for index, row in enumerate(bars[-5:]):
            row["close"] = 20 - index * 1.2
            row["open"] = row["close"] + 0.4
            row["high"] = row["open"] + 0.1
            row["low"] = row["close"] - 0.3
            row["change_pct"] = -5.0
        result = service._build({
            "symbol": "000001", "name": "测试", "bars": bars, "flow": [],
            "sector_flow": [], "sector": None, "quote": None,
            "quote_is_realtime": False, "source_status": {},
        })
        self.assertIn(result["best_trading_zone"]["zone"], {"风险C区", "未形成明确交易区"})
        if result["best_trading_zone"]["zone"] == "风险C区":
            self.assertIn(result["decision"]["action"], {"RISK", "EXIT"})

    def test_replay_does_not_use_future_bars(self):
        service = StrongStockDecisionService()
        bars = make_bars(90)
        context = {
            "symbol": "000001", "name": "测试", "bars": bars, "flow": [],
            "sector_flow": [], "sector": None, "quote": None,
            "quote_is_realtime": False, "source_status": {},
        }

        async def load_context(_symbol, _as_of):
            return context

        with patch.object(service, "_load_context", new=load_context):
            result = asyncio.run(service.backtest("000001", skill_id="HQS_001"))
        dates = [item["trade_date"] for item in result["observations"]]
        self.assertTrue(all(dates[index] < dates[index + 1] for index in range(len(dates) - 1)))
        self.assertTrue(result["method"].startswith("只使用每个截面之前"))

    def test_flow_merge_prefers_remote_and_respects_cutoff(self):
        cached = [{"date": "2026-03-28", "main_net_inflow": 10}, {"date": "2026-03-29", "main_net_inflow": 20}]
        remote = [{"date": "2026-03-29", "main_net_inflow": 99}, {"date": "2026-03-30", "main_net_inflow": 30}]
        merged = _merge_flow_rows(cached, remote, latest=date(2026, 3, 29))
        self.assertEqual([row["main_net_inflow"] for row in merged], [10, 99])

    def test_feature_flag_returns_disabled_without_loading_data(self):
        service = StrongStockDecisionService()
        with patch("strong_stock_decision.service.settings.feature_strong_stock_decision", False):
            result = asyncio.run(service.evaluate("000001"))
        self.assertFalse(result["enabled"])
        self.assertEqual(result["status"], "DISABLED")
