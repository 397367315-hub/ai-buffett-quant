import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from quant.backtest import StrategyBacktestService, _history_is_sufficient
from quant.engine import create_strategy, match_stock, update_strategy
from quant.portfolio import paper_portfolio
from quant.rules import evaluate_rules, evaluate_rules_detailed
from quant.schemas import PaperBuyRequest, PaperSellRequest, StrategyCreate
from quant.signals import QuantSignalService
from quant.storage import QuantJsonStore


class QuantModuleTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = QuantJsonStore(Path(self.tempdir.name))
        self.patches = [
            patch("quant.engine.quant_store", self.store),
            patch("quant.portfolio.quant_store", self.store),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tempdir.cleanup()

    def test_rule_operator_is_applied_instead_of_using_rule_defaults(self):
        stock = {"turnover": 3.0, "sectors": ["电力"], "price": 10, "ma20": 9}
        matched, _, failed = evaluate_rules(
            [{"type": "turnover", "operator": "gt", "value": 2.5}], stock
        )
        self.assertTrue(matched)
        self.assertEqual(failed, [])
        matched, _, failed = evaluate_rules(
            [{"type": "turnover", "operator": "lte", "value": 2.5}], stock
        )
        self.assertFalse(matched)
        self.assertEqual(len(failed), 1)
        matched, _, _ = evaluate_rules(
            [{"type": "sector", "operator": "in", "value": ["电力"]}], stock
        )
        self.assertTrue(matched)
        matched, _, _ = evaluate_rules(
            [{"type": "sector", "operator": "not_in", "value": ["电力"]}], {"sectors": []}
        )
        self.assertFalse(matched)

    def test_missing_rule_field_is_unavailable_instead_of_zero_or_passed(self):
        result = evaluate_rules_detailed(
            [{"type": "gross_margin", "operator": "gte", "value": 20}],
            {"code": "600000", "gross_margin": None},
        )

        self.assertFalse(result["matched"])
        self.assertEqual(result["failed"], [])
        self.assertEqual(len(result["unavailable"]), 1)
        self.assertEqual(result["details"][0]["status"], "unavailable")

    def test_non_compensating_risk_block_suppresses_quant_signal(self):
        strategy = {
            "id": "risk_block", "name": "风险否决测试",
            "filter": {"logic": "AND", "rules": []},
            "entry": {"logic": "AND", "rules": [
                {"type": "change_pct", "operator": "gte", "value": 1},
            ]},
        }

        signal = match_stock(strategy, {
            "code": "600001", "name": "测试股份", "price": 10,
            "change_pct": 5, "net_profit": -1,
        })

        self.assertIsNone(signal)

    def test_history_coverage_checks_requested_period_not_only_row_count(self):
        start = date(2025, 1, 1)
        end = date(2025, 12, 31)
        recent_only = [{"date": (date(2025, 10, 1) + timedelta(days=index)).isoformat()} for index in range(90)]
        covered = [{"date": (date(2024, 12, 1) + timedelta(days=index * 4)).isoformat()} for index in range(100)]
        self.assertFalse(_history_is_sufficient(recent_only, start, end))
        self.assertTrue(_history_is_sufficient(covered, start, end))

    def test_strategy_crud_preserves_validated_rule_groups(self):
        payload = StrategyCreate.model_validate({
            "name": "测试策略", "filter": {"logic": "AND", "rules": [
                {"type": "pe_ttm", "operator": "lte", "value": 20},
            ]}, "entry": {"logic": "AND", "rules": [
                {"type": "vol_ratio", "operator": "gte", "value": 1.2},
            ]},
        })
        created = create_strategy(payload)
        updated = update_strategy(created["id"], {"active": False})
        self.assertFalse(updated["active"])
        signal = match_stock(updated | {"active": True}, {
            "code": "600000", "name": "浦发银行", "price": 10, "pe_ttm": 10,
            "vol_ratio": 2, "change_pct": 0, "turnover": 1, "sectors": ["银行"],
        })
        self.assertIsNotNone(signal)

    def test_paper_account_uses_lot_sizes_and_realistic_sell_fees(self):
        bought = paper_portfolio.buy(PaperBuyRequest(
            stock_code="600000", stock_name="测试股份", price=10, shares=1000, strategy_id="strat_a"
        ))
        self.assertLess(bought["account"]["available_cash"], 90000)
        sold = paper_portfolio.sell(PaperSellRequest(
            stock_code="600000", price=11, shares=1000, reason="止盈"
        ))
        self.assertEqual(sold["holdings"], [])
        sell = sold["history"][-1]
        self.assertGreater(sell["commission"], 0)
        self.assertGreater(sell["tax"], 0)
        with self.assertRaises(ValueError):
            paper_portfolio.buy(PaperBuyRequest(stock_code="600000", price=10, shares=101))

    def test_backtest_executes_at_next_open_not_signal_close(self):
        service = StrategyBacktestService()
        start = date(2025, 1, 1)
        bars = []
        for index in range(90):
            current = start + timedelta(days=index)
            close = 10 + index * 0.08
            bars.append({
                "date": current.isoformat(), "open": close - 0.03, "close": close,
                "high": close + 0.1, "low": close - 0.15, "volume": 1_000_000 + index * 1_000,
                "turnover": 3,
            })
        strategy = {
            "id": "strat_tplusone", "name": "T+1测试", "filter": {"logic": "AND", "rules": []},
            "entry": {"logic": "AND", "rules": [
                {"type": "above_ma", "operator": "eq", "value": "MA5"},
            ]},
            "exit": {"stop_loss_pct": 50, "take_profit_pct": 100, "max_holding_days": 3, "rules": []},
            "position": {"method": "equal_weight", "max_holdings": 1, "max_position_pct": 100},
        }
        result = service._simulate(
            strategy, [{"code": "600000", "name": "测试股份", "price": 10, "sectors": []}],
            {"600000": bars}, {}, start, start + timedelta(days=89), 100000, None,
        )
        buys = [item for item in result["trades"] if item["action"] == "buy"]
        self.assertTrue(buys)
        self.assertGreater(buys[0]["date"], buys[0]["signal_date"])
        self.assertEqual(buys[0]["execution"], "T+1 开盘")
        self.assertGreater(result["completed_trade_count"], 0)


class QuantSignalSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduled_scan_excludes_manual_only_strategies(self):
        strategies = [
            {
                "id": "daily", "name": "定时策略", "active": True, "scan_schedule": "daily",
                "filter": {"logic": "AND", "rules": []},
                "entry": {"logic": "AND", "rules": [{"type": "change_pct", "operator": "gte", "value": 1}]},
            },
            {
                "id": "manual", "name": "手动策略", "active": True, "scan_schedule": "manual",
                "filter": {"logic": "AND", "rules": []},
                "entry": {"logic": "AND", "rules": [{"type": "change_pct", "operator": "gte", "value": 1}]},
            },
        ]
        service = QuantSignalService()
        snapshot = {
            "stocks": [{"code": "600000", "name": "测试股份", "price": 10, "change_pct": 2}],
            "source": "test", "data_date": "2026-08-03", "is_realtime": True,
        }
        with patch("quant.signals.list_strategies", return_value=strategies), patch.object(
            service, "_market_snapshot", new=AsyncMock(return_value=(snapshot, False, None))
        ), patch(
            "quant.signals.stock_feature_service.enrich",
            new=AsyncMock(return_value={
                "stocks": snapshot["stocks"],
                "coverage": {"total": 1},
                "warnings": [],
                "source_updated_at": None,
            }),
        ):
            result = await service.scan(scheduled_only=True, persist=False)

        self.assertEqual(result["strategy_count"], 1)
        self.assertEqual(result["signals"][0]["strategy_ids"], ["daily"])


if __name__ == "__main__":
    unittest.main()
