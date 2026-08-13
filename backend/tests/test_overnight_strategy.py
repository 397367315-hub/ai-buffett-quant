import unittest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import MarketDataCache, OvernightPosition, OvernightStrategyRun, StockDailyBar
from services.data_collector import EastMoneyDataCollector
from services.market_decision_contract import WORKBENCH_CACHE_PREFIX, WORKBENCH_CONTRACT_VERSION
from services.overnight_strategy import AUCTION_STRATEGY_CONFIG, OvernightStrategyService


def daily_bars(count: int = 80) -> list[dict]:
    start = date(2026, 4, 1)
    rows = []
    for index in range(count):
        close = 10 + index * 0.1
        rows.append({
            "date": (start + timedelta(days=index)).isoformat(),
            "open": close - 0.05,
            "close": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "volume": 1_000_000 + index * 1_000,
            "change_pct": 0.6,
        })
    return rows


def minute_payload(*, surge: bool = False, pulse: bool = False, trade_date: date = date(2026, 8, 4)) -> dict:
    bars = []
    start = datetime.combine(trade_date, datetime.min.time()).replace(hour=14, minute=15)
    for index in range(41):
        price = 18 + index * 0.002
        if surge and index >= 31:
            price = 18.1 * (1 + (index - 30) * 0.006)
        volume = 6_000 if pulse and index >= 31 else 1_000
        moment = start + timedelta(minutes=index)
        bars.append({
            "stock_code": "600000",
            "stock_name": "浦发银行",
            "bar_time": moment.isoformat(timespec="minutes"),
            "interval_minutes": 1,
            "open": price - 0.01,
            "close": price,
            "high": price + 0.01,
            "low": price - 0.02,
            "volume": volume,
            "amount": int(price * volume),
            "average": price,
        })
    return {
        "stock_code": "600000",
        "stock_name": "浦发银行",
        "pre_close": 17.5,
        "bars": bars,
        "source": "eastmoney",
        "data_date": trade_date.isoformat(),
        "is_realtime": True,
    }


class OvernightRuleTests(unittest.TestCase):
    def setUp(self):
        self.service = OvernightStrategyService()
        self.now = datetime(2026, 8, 4, 14, 55, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.stock = {
            "code": "600000",
            "name": "浦发银行",
            "sector": "银行",
            "price": 18.2,
            "change_pct": 4.0,
            "vol_ratio": 1.8,
            "turnover": 5.0,
            "market_cap": 180.0,
            "volume": 1_200_000,
            "high": 18.3,
            "low": 17.7,
        }

    def test_prefilter_requires_volume_ratio_strictly_above_1_2(self):
        snapshot_stock = {
            **self.stock,
            "market_cap": 18_000_000_000,
        }
        at_threshold = {**snapshot_stock, "volume_ratio": 1.2}
        above_threshold = {**snapshot_stock, "code": "600001", "volume_ratio": 1.21}

        selected = self.service._prefilter([at_threshold, above_threshold])

        self.assertEqual([stock["code"] for stock in selected], ["600001"])

    def test_daily_rules_pass_only_with_complete_blacklist_evidence(self):
        audit = self.service._daily_audit(
            self.stock,
            daily_bars(),
            today=self.now.date(),
            announcements=[],
            announcement_available=True,
            report_dates=[],
            report_available=True,
        )

        self.assertTrue(audit["daily_passed"])
        self.assertGreater(audit["ma"]["ma10"], audit["ma"]["ma20"])
        self.assertGreater(audit["ma"]["ma20"], audit["ma"]["ma30"])

        unavailable = self.service._daily_audit(
            self.stock,
            daily_bars(),
            today=self.now.date(),
            announcements=[],
            announcement_available=False,
            report_dates=[],
            report_available=True,
        )
        self.assertFalse(unavailable["daily_passed"])
        self.assertIn("当日无重大利空公告", unavailable["unavailable_reasons"])

    def test_last_five_minute_surge_and_pulse_volume_are_hard_blocks(self):
        benchmark = minute_payload()
        normal = self.service._minute_audit(minute_payload(), self.now, benchmark)
        surge = self.service._minute_audit(minute_payload(surge=True), self.now, benchmark)
        pulse = self.service._minute_audit(minute_payload(pulse=True), self.now, benchmark)

        self.assertTrue(normal["minute_passed"])
        self.assertFalse(surge["minute_passed"])
        self.assertIn("排除尾盘5分钟急拉", surge["failed_reasons"])
        self.assertFalse(pulse["minute_passed"])
        self.assertIn("排除脉冲爆量", pulse["failed_reasons"])

    def test_exit_enforces_t_plus_one_and_ten_oclock_deadline(self):
        same_day = OvernightPosition(
            id=1,
            entry_run_id=1,
            stock_code="600000",
            stock_name="浦发银行",
            status="open",
            shares=100,
            signal_at=datetime(2026, 8, 4, 14, 50),
            entry_at=datetime(2026, 8, 4, 14, 50),
            entry_price=18.2,
            previous_close=18.18,
        )
        blocked = self.service._exit_decision(same_day, minute_payload(), self.now, force=True)
        self.assertFalse(blocked["ready"])
        self.assertEqual(blocked["data_status"], "t_plus_one")

        next_day = datetime(2026, 8, 5, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        payload = {
            "pre_close": 18.0,
            "bars": [
                {"bar_time": "2026-08-05T09:30", "open": 18.09, "close": 18.05, "high": 18.12, "low": 18.0},
                {"bar_time": "2026-08-05T10:00", "open": 18.0, "close": 17.95, "high": 18.03, "low": 17.9},
            ],
        }
        decision = self.service._exit_decision(same_day, payload, next_day, force=True)
        self.assertTrue(decision["ready"])
        self.assertEqual(decision["exit_price"], round(17.95 * 0.999, 4))
        self.assertIn("10:00", decision["reason"])
        self.assertLess(decision["pnl"], 0)

    def test_exit_treats_minus_one_to_one_as_flat_and_honors_stop_loss(self):
        position = OvernightPosition(
            id=1,
            entry_run_id=1,
            stock_code="600000",
            stock_name="浦发银行",
            status="open",
            shares=100,
            signal_at=datetime(2026, 8, 4, 14, 50),
            entry_at=datetime(2026, 8, 4, 14, 50),
            entry_price=18.2,
            previous_close=18.18,
        )
        now = datetime(2026, 8, 5, 9, 40, tzinfo=ZoneInfo("Asia/Shanghai"))
        flat_payload = {
            "pre_close": 18.2,
            "bars": [
                {"bar_time": "2026-08-05T09:30", "open": 18.1, "close": 18.12, "high": 18.15, "low": 18.05},
                {"bar_time": "2026-08-05T09:35", "open": 18.12, "close": 18.2, "high": 18.22, "low": 18.1},
            ],
        }
        flat = self.service._exit_decision(position, flat_payload, now, force=False)
        self.assertTrue(flat["ready"])
        self.assertIn("成本线", flat["reason"])
        self.assertEqual(flat["market_price"], 18.2)

        stop_payload = {
            "pre_close": 18.2,
            "bars": [
                {"bar_time": "2026-08-05T09:30", "open": 18.25, "close": 18.1, "high": 18.3, "low": 17.5},
            ],
        }
        stopped = self.service._exit_decision(position, stop_payload, now, force=False)
        self.assertTrue(stopped["ready"])
        self.assertIn("-3%", stopped["reason"])
        self.assertEqual(stopped["market_price"], position.entry_price * 0.97)

    def test_force_exit_cannot_run_before_ten_oclock(self):
        position = OvernightPosition(
            id=1,
            entry_run_id=1,
            stock_code="600000",
            stock_name="浦发银行",
            status="open",
            shares=100,
            signal_at=datetime(2026, 8, 4, 14, 50),
            entry_at=datetime(2026, 8, 4, 14, 50),
            entry_price=18.2,
        )
        now = datetime(2026, 8, 5, 9, 40, tzinfo=ZoneInfo("Asia/Shanghai"))
        decision = self.service._exit_decision(position, minute_payload(trade_date=now.date()), now, force=True)
        self.assertFalse(decision["ready"])
        self.assertEqual(decision["data_status"], "outside_window")

    def test_auction_requires_strict_volume_ratio_and_inclusive_high_open_range(self):
        candidate = {"code": "600000", "previous_close": 100.0}
        quote = {
            "auction_price": 102.0,
            "previous_close": 100.0,
            "auction_volume_ratio": 3.01,
            "quote_at": "2026-08-05T09:25:00+08:00",
            "source": "eastmoney",
            "is_realtime": True,
        }
        now = datetime(2026, 8, 5, 9, 25, tzinfo=ZoneInfo("Asia/Shanghai"))
        passed = self.service._auction_audit(candidate, quote, now, AUCTION_STRATEGY_CONFIG)
        self.assertTrue(passed["auction_passed"])

        at_ratio = self.service._auction_audit(
            candidate, {**quote, "auction_volume_ratio": 3.0}, now, AUCTION_STRATEGY_CONFIG,
        )
        self.assertFalse(at_ratio["auction_passed"])
        self.assertIn("竞价量比", at_ratio["failed_reasons"])

        at_upper_bound = self.service._auction_audit(
            candidate, {**quote, "auction_price": 105.0, "high_open_pct": 5.0}, now, AUCTION_STRATEGY_CONFIG,
        )
        self.assertTrue(at_upper_bound["auction_passed"])

        stale = self.service._auction_audit(
            candidate, {**quote, "quote_at": "2026-08-05T09:18:00+08:00"}, now, AUCTION_STRATEGY_CONFIG,
        )
        self.assertFalse(stale["auction_passed"])
        self.assertIn("竞价实时数据", stale["unavailable_reasons"])


class OvernightCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_minute_trends_preserve_timestamp_and_convert_lots_to_shares(self):
        collector = EastMoneyDataCollector()
        collector.fetch_json = AsyncMock(return_value={
            "data": {
                "name": "浦发银行",
                "preClose": 12.0,
                "trends": [
                    "2026-08-04 14:49,12.10,12.12,12.13,12.09,35,42420.00,12.110",
                    "2026-08-04 14:50,12.12,12.15,12.16,12.11,48,58320.00,12.120",
                ],
            }
        })
        fixed = datetime(2026, 8, 4, 14, 50, tzinfo=ZoneInfo("Asia/Shanghai"))
        with patch("services.data_collector.shanghai_now", return_value=fixed):
            result = await collector.fetch_stock_minute_trends("600000")

        self.assertEqual(result["data_date"], "2026-08-04")
        self.assertTrue(result["is_realtime"])
        self.assertEqual(result["bars"][0]["volume"], 3_500)
        self.assertEqual(result["bars"][-1]["bar_time"], "2026-08-04T14:50")


class OvernightWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_patch = patch("services.overnight_strategy.async_session", self.session_factory)
        self.session_patch.start()
        self.service = OvernightStrategyService()
        async with self.session_factory() as session:
            for item in daily_bars():
                session.add(StockDailyBar(
                    stock_code="600000",
                    stock_name="浦发银行",
                    market="SH",
                    trade_date=date.fromisoformat(item["date"]),
                    open_price=item["open"],
                    close_price=item["close"],
                    high_price=item["high"],
                    low_price=item["low"],
                    volume=item["volume"],
                    change_pct=item["change_pct"],
                ))
            session.add(MarketDataCache(
                key=f"{WORKBENCH_CACHE_PREFIX}2026-08-04",
                payload={
                    "available": True,
                    "meta": {
                        "contract_version": WORKBENCH_CONTRACT_VERSION,
                        "decision_date": "2026-08-04",
                    },
                    "market_cognition": {"final_action": "execute"},
                    "adaptive_strategy_weights": {
                        "weights": [
                            {"strategy_id": "tail_1455", "weight_pct": 50},
                            {"strategy_id": "auction_confirmation", "weight_pct": 60},
                        ],
                    },
                },
            ))
            await session.commit()

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()

    async def test_entry_run_creates_only_verified_hundred_share_position(self):
        now = datetime(2026, 8, 4, 14, 55, tzinfo=ZoneInfo("Asia/Shanghai"))
        snapshot = {
            "stocks": [{
                "code": "600000", "name": "浦发银行", "sector": "银行", "price": 18.2,
                "change_pct": 4.0, "volume_ratio": 1.8, "turnover": 5.0,
                "market_cap": 18_000_000_000, "high": 18.3, "low": 17.7,
                "previous_close": 17.5, "volume": 1_200_000,
            }],
            "complete": True,
            "is_realtime": True,
            "data_date": "2026-08-04",
            "source": "eastmoney",
        }
        with (
            patch("services.overnight_strategy.shanghai_now", return_value=now),
            patch("services.overnight_strategy.collector.fetch_quant_market_snapshot", new_callable=AsyncMock, return_value=snapshot),
            patch("services.overnight_strategy.collector.fetch_market_turnover", new_callable=AsyncMock, return_value={"sh_change_pct": 0.2}),
            patch("services.overnight_strategy.macro_policy_news_collector.get_stock_announcements_audit", new_callable=AsyncMock, return_value={
                "announcements": {"600000": []},
                "status": {"600000": {"available": True, "source": "eastmoney", "error": None}},
            }),
            patch.object(self.service, "_appointment_map", new_callable=AsyncMock, return_value=({"600000": []}, True)),
            patch("services.overnight_strategy.collector.fetch_stock_minute_trends", new_callable=AsyncMock, return_value=minute_payload()),
            patch("services.overnight_strategy.collector.fetch_shanghai_index_minute_trends", new_callable=AsyncMock, return_value=minute_payload()),
            patch.object(self.service, "_persist_minute_bars", new_callable=AsyncMock, return_value=36),
        ):
            result = await self.service.start("entry", background=False)

        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(result["run"]["qualified_count"], 1)
        async with self.session_factory() as session:
            positions = (await session.execute(select(OvernightPosition))).scalars().all()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].shares, 100)
        self.assertEqual(positions[0].status, "open")
        self.assertEqual(positions[0].previous_close, 17.5)
        self.assertLessEqual(positions[0].allocated_pct, 10)

    async def test_three_losses_warn_without_blocking_future_scans(self):
        run = OvernightStrategyRun(
            stage="entry", trigger="manual", status="completed", progress=100,
            message="test", data_date=date(2026, 8, 1),
        )
        async with self.session_factory() as session:
            session.add(run)
            await session.flush()
            for index, pnl in enumerate((100.0, -10.0, -20.0, -30.0)):
                moment = datetime(2026, 8, 1, 10, 0) + timedelta(days=index)
                session.add(OvernightPosition(
                    entry_run_id=run.id, stock_code=f"60000{index}", stock_name=f"测试{index}",
                    status="closed", shares=100, signal_at=moment - timedelta(days=1),
                    entry_at=moment - timedelta(days=1), entry_price=10.0,
                    exit_at=moment, exit_price=10.0 + pnl / 100,
                    pnl=pnl, pnl_pct=pnl / 1000 * 100,
                ))
            await session.commit()

        result = await self.service._loss_circuit(datetime(2026, 8, 5, 9, 25))

        self.assertTrue(result["warning"])
        self.assertFalse(result["blocked"])
        self.assertEqual(result["consecutive_losses"], 3)
        self.assertNotIn("pause_until", result)

    async def test_market_no_trade_gate_keeps_candidate_but_blocks_simulated_position(self):
        async with self.session_factory() as session:
            gate_row = await session.get(MarketDataCache, f"{WORKBENCH_CACHE_PREFIX}2026-08-04")
            gate_row.payload = {
                **gate_row.payload,
                "market_cognition": {"final_action": "no_trade"},
                "adaptive_strategy_weights": {
                    "weights": [{"strategy_id": "tail_1455", "weight_pct": 0}],
                },
            }
            run = OvernightStrategyRun(
                stage="entry",
                trigger="manual",
                status="running",
                progress=80,
                message="testing",
                data_quality={"strategy_id": "overnight_review_v2"},
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)

        candidate = {
            "code": "600000",
            "name": "浦发银行",
            "sector": "银行",
            "qualified": True,
            "selected_for_entry": False,
            "score": 88,
            "previous_close": 17.5,
            "signal_at": "2026-08-04T14:55",
            "failed_reasons": [],
            "unavailable_reasons": [],
            "conditions": [],
            "minute": {
                "market_price": 18.0,
                "entry_price": 18.018,
                "latest_bar_at": "2026-08-04T14:55",
            },
        }
        selected = await self.service._create_positions(
            run.id,
            [candidate],
            datetime(2026, 8, 4, 14, 55, tzinfo=ZoneInfo("Asia/Shanghai")),
            {
                **AUCTION_STRATEGY_CONFIG,
                "id": "overnight_review_v2",
                "requires_auction_confirmation": False,
            },
        )

        self.assertEqual(selected, 0)
        self.assertFalse(candidate["qualified"])
        self.assertTrue(candidate["market_execution_gate"]["blocked"])
        self.assertIn("不交易", candidate["failed_reasons"][-1])
        async with self.session_factory() as session:
            positions = (await session.execute(select(OvernightPosition))).scalars().all()
            stored_run = await session.get(OvernightStrategyRun, run.id)
        self.assertEqual(positions, [])
        self.assertTrue(stored_run.data_quality["market_execution_gate"]["blocked"])

    async def test_missing_candidate_signal_date_fails_closed(self):
        async with self.session_factory() as session:
            run = OvernightStrategyRun(
                stage="entry",
                trigger="manual",
                status="running",
                progress=80,
                message="testing",
                data_quality={"strategy_id": "overnight_review_v2"},
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)

        candidate = {
            "code": "600000",
            "name": "浦发银行",
            "qualified": True,
            "score": 88,
            "failed_reasons": [],
            "minute": {
                "market_price": 18.0,
                "entry_price": 18.018,
                "latest_bar_at": "2026-08-04T14:55",
            },
        }
        selected = await self.service._create_positions(
            run.id,
            [candidate],
            datetime(2026, 8, 4, 14, 55, tzinfo=ZoneInfo("Asia/Shanghai")),
            AUCTION_STRATEGY_CONFIG | {
                "id": "overnight_review_v2",
                "requires_auction_confirmation": False,
            },
        )

        self.assertEqual(selected, 0)
        self.assertFalse(candidate["qualified"])
        self.assertIn("信号日缺失或不一致", candidate["failed_reasons"][-1])

    async def test_scheduled_completed_run_is_deduplicated_for_five_minutes(self):
        first, created = await self.service._create_run("entry", "schedule")
        self.assertTrue(created)
        async with self.session_factory() as session:
            row = await session.get(OvernightStrategyRun, first.id)
            row.status = "completed"
            row.finished_at = datetime.utcnow()
            await session.commit()

        duplicate, duplicate_created = await self.service._create_run("entry", "github_schedule")
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate.id, first.id)

        manual, manual_created = await self.service._create_run("entry", "manual")
        self.assertTrue(manual_created)
        self.assertNotEqual(manual.id, first.id)

    async def test_auction_run_uses_previous_tail_candidate_and_buys_only_after_confirmation(self):
        previous_run = OvernightStrategyRun(
            stage="entry",
            trigger="schedule",
            status="completed",
            progress=100,
            message="尾盘候选完成",
            data_date=date(2026, 8, 4),
            is_realtime=True,
            scanned_count=1,
            prefiltered_count=1,
            qualified_count=1,
            candidates=[{
                "code": "600000", "name": "浦发银行", "sector": "银行", "score": 88.0,
                "previous_close": 17.5, "qualified": True, "tail_qualified": True,
                "awaiting_auction": True, "auction_passed": None, "selected_for_entry": False,
                "failed_reasons": [], "unavailable_reasons": [], "conditions": [],
                "minute": {"latest_bar_at": "2026-08-04T14:55", "market_price": 18.0, "entry_price": 18.018},
                "signal_at": "2026-08-04T14:55",
            }],
            data_quality={
                "strategy_id": AUCTION_STRATEGY_CONFIG["id"],
                "strategy": AUCTION_STRATEGY_CONFIG,
                "cash_day": False,
            },
        )
        async with self.session_factory() as session:
            session.add(previous_run)
            await session.commit()

        now = datetime(2026, 8, 5, 9, 25, tzinfo=ZoneInfo("Asia/Shanghai"))
        auction_payload = {
            "stocks": [{
                "code": "600000", "name": "浦发银行", "auction_price": 18.0,
                "auction_volume": 320_000, "auction_volume_ratio": 3.2,
                "high_open_pct": 2.857, "previous_close": 17.5,
                "quote_at": "2026-08-05T09:25:00+08:00", "source": "eastmoney",
                "is_realtime": True,
            }],
            "complete": True, "is_realtime": True, "data_date": "2026-08-05",
            "source": "eastmoney", "field_coverage": {"auction_volume_ratio": 1},
        }
        with (
            patch("services.overnight_strategy.shanghai_now", return_value=now),
            patch("services.overnight_strategy.collector.fetch_stock_auction_quotes", new_callable=AsyncMock, return_value=auction_payload),
        ):
            result = await self.service.start(
                "auction", strategy_id=AUCTION_STRATEGY_CONFIG["id"], background=False,
            )

        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(result["run"]["qualified_count"], 1)
        self.assertIn("AI竞价盯盘Agent", result["run"]["message"])
        async with self.session_factory() as session:
            positions = (await session.execute(select(OvernightPosition))).scalars().all()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].shares, 100)
        self.assertEqual(positions[0].audit["auction_confirmed"], True)
        self.assertEqual(positions[0].audit["entry_source"], "call_auction")
        self.assertEqual(positions[0].entry_at, datetime(2026, 8, 5, 9, 25))
        self.assertEqual(positions[0].entry_price, 18.018)


if __name__ == "__main__":
    unittest.main()
