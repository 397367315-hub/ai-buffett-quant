import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import StockDailyBar
from services.data_collector import collector
from services.zhaban_strategy import DEFAULT_ZHABAN_CONFIG, ZhabanStrategyService, _limit_spec


class ZhabanStrategyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_patch = patch("services.zhaban_strategy.async_session", self.session_factory)
        self.session_patch.start()
        self.service = ZhabanStrategyService()

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()

    def test_board_limit_rules_are_not_mixed(self):
        self.assertEqual(_limit_spec("600000"), ("主板", 10.0))
        self.assertEqual(_limit_spec("300001"), ("创业板", 20.0))
        self.assertEqual(_limit_spec("688001"), ("科创板", 20.0))
        self.assertEqual(_limit_spec("830001"), ("北交所", 30.0))

    def test_default_scope_keeps_main_board_as_an_independent_sample(self):
        self.assertEqual(DEFAULT_ZHABAN_CONFIG["board_scope"], "main")
        self.assertEqual(DEFAULT_ZHABAN_CONFIG["holding_days"], 3)
        self.assertEqual(DEFAULT_ZHABAN_CONFIG["limit_touch_count_10d_max"], 2)
        self.assertTrue(self.service._board_allowed("主板", DEFAULT_ZHABAN_CONFIG))
        self.assertFalse(self.service._board_allowed("创业板", DEFAULT_ZHABAN_CONFIG))

    def test_partial_take_profit_repricing_preserves_exit_legs(self):
        plan = {
            "code": "600001", "name": "测试股份", "signal_date": "2026-01-01",
            "entry_date": "2026-01-02", "exit_date": "2026-01-03",
            "entry_raw": 10.0, "exit_raw": 10.4, "shares": 1000,
            "reason": "分批止盈后保本退出", "board": "主板",
            "exit_legs": [
                {"date": "2026-01-02", "raw_price": 10.8, "ratio": 0.5, "reason": "达到止盈线，分批卖出"},
                {"date": "2026-01-03", "raw_price": 10.0, "ratio": 0.5, "reason": "止盈后余仓保本退出"},
            ],
        }

        result = self.service._reprice_plan(
            plan, commission_rate=0, stamp_tax_rate=0, slippage_rate=0,
        )

        self.assertEqual([item["shares"] for item in result["exit_legs"]], [500, 500])
        self.assertEqual(result["exit_price"], 10.4)
        self.assertEqual(result["pnl"], 400.0)

    def test_daily_event_is_marked_as_approximation(self):
        target = date(2026, 1, 8)
        history = [
            {"date": target - timedelta(days=1), "close": 10.0, "high": 10.2, "low": 9.8, "open": 10.0, "volume": 1_000_000, "turnover": 3, "name": "测试股份", "source": "test"},
            {"date": target, "close": 10.9, "high": 11.0, "low": 10.4, "open": 10.3, "volume": 2_000_000, "turnover": 6, "name": "测试股份", "source": "test"},
        ]

        event = self.service._event_from_history("600001", history, target)

        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "true_zhaban")
        self.assertEqual(event["event_source"], "daily_bar_approximation")
        self.assertFalse(event["intraday_verified"])
        self.assertAlmostEqual(event["recovery_rate"], 0.8333, places=4)

    async def test_pool_rows_are_rejected_when_provider_returns_wrong_date(self):
        with (
            patch.object(collector, "fetch_limit_up_pool", new_callable=AsyncMock, return_value={"stocks": [], "total": 20, "trade_date": "2026-01-09"}),
            patch.object(collector, "fetch_failed_limit_pool", new_callable=AsyncMock, return_value={"stocks": [{"code": "600001"}], "total": 3, "trade_date": "2026-01-09"}),
        ):
            rows, meta = await self.service._pool_events(date(2026, 1, 8))

        self.assertEqual(rows, {})
        self.assertFalse(meta["available"])
        self.assertIsNone(meta["trade_date"])
        self.assertEqual(meta["returned_dates"], ["2026-01-09"])

    async def test_backtest_enters_next_open_and_exits_same_day_close(self):
        first = date(2026, 1, 1)
        rows = []
        for index in range(45):
            current = first + timedelta(days=index)
            open_price = 10.0
            close_price = 10.0
            high_price = 10.2
            low_price = 9.8
            volume = 1_000_000
            if index == 30:
                open_price, close_price, high_price, low_price, volume = 10.3, 10.9, 11.0, 10.4, 2_000_000
            elif index == 31:
                open_price, close_price, high_price, low_price, volume = 10.95, 11.0, 11.1, 10.8, 1_500_000
            rows.append(StockDailyBar(
                stock_code="600001", stock_name="测试股份", market="SH",
                trade_date=current, open_price=open_price, close_price=close_price,
                high_price=high_price, low_price=low_price, volume=volume,
                turnover=5.0, source="test",
            ))
        async with self.session_factory() as session:
            session.add_all(rows)
            await session.commit()

        config = {
            "require_market_ma20": False,
            "require_sector_linkage": False,
            "failed_limit_rate_max_pct": 100,
            "depth_pct_max": 5,
            "recovery_rate_min": 0,
            "absorption_strength_min": 0,
            "close_position_min": 0,
            "prior_5d_return_max_pct": 200,
            "turnover_3d_avg_max_pct": 100,
            "limit_touch_count_10d_max": 10,
            "holding_days": 1,
            "stop_loss_pct": 30,
            "take_profit_pct": 100,
        }
        with (
            patch.object(collector, "fetch_shanghai_index_history", new_callable=AsyncMock, return_value=[]),
            patch.object(self.service, "_sector_map", new_callable=AsyncMock, return_value=({}, False)),
        ):
            result = await self.service.backtest(
                start_date=first,
                end_date=first + timedelta(days=44),
                initial_capital=100000,
                config_payload=config,
            )

        self.assertEqual(result["summary"]["trade_count"], 1)
        trade = result["trades"][0]
        self.assertEqual(trade["signal_date"], (first + timedelta(days=30)).isoformat())
        self.assertEqual(trade["entry_date"], (first + timedelta(days=31)).isoformat())
        self.assertEqual(trade["exit_date"], trade["entry_date"])
        self.assertEqual(trade["reason"], "持有期结束收盘退出")
        self.assertGreater(trade["commission_buy"], 0)
        self.assertGreater(trade["commission_sell"], 0)
        self.assertGreater(trade["stamp_tax"], 0)
        self.assertEqual(result["data_quality"]["intraday_verified_count"], 0)
        self.assertFalse(result["data_quality"]["audit_eligible"])


if __name__ == "__main__":
    unittest.main()
