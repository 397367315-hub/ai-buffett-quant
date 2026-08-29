import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import (
    MarginMarketDaily, MarginSectorDaily, MarginStockDaily, StockDailyBar, StockLeverageMetric,
)
from services.margin_leverage import (
    MarginLeverageService, _relation, financing_ratio_level, margin_leverage_service,
)


class MarginLeverageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.service = MarginLeverageService()
        self.session_patch = patch("services.margin_leverage.async_session", self.session_factory)
        self.session_patch.start()

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()

    def test_financing_ratio_thresholds_keep_five_percent_as_reference_not_absolute_rule(self):
        self.assertEqual(financing_ratio_level(4.9)["level"], "正常")
        self.assertEqual(financing_ratio_level(5.0)["level"], "偏高")
        self.assertEqual(financing_ratio_level(8.0)["level"], "高杠杆")
        self.assertEqual(financing_ratio_level(12.0)["level"], "高风险")
        self.assertEqual(financing_ratio_level(20.0)["level"], "监管风险观察区")
        self.assertEqual(financing_ratio_level(25.0)["level"], "极端/监管阈值")
        self.assertIsNone(financing_ratio_level(None)["score"])

    def test_price_financing_relation_separates_crowding_and_healthy_deleveraging(self):
        self.assertEqual(_relation(4, 6, 92)[0], "杠杆追涨")
        self.assertEqual(_relation(-4, 3, 75)[0], "越跌越补")
        self.assertEqual(_relation(-3, -5, 60)[0], "踩踏去杠杆")
        self.assertEqual(_relation(2, -3, 55)[0], "健康去杠杆")

    def test_snapshot_audit_rejects_partial_exchange_and_half_balance_discontinuity(self):
        target = date(2026, 8, 28)
        rows = [{
            "DATE": "2026-08-28 00:00:00",
            "MARKET": "融资融券_沪证",
            "RZYE": 1_330_000_000_000,
        }]
        audit = self.service._audit_stock_snapshot(
            rows,
            target,
            {"financing_balance": 2_630_000_000_000},
        )
        self.assertFalse(audit["passed"])
        self.assertIn("融资融券_深证", audit["missing_markets"])
        self.assertGreater(audit["balance_deviation_pct"], 40)

    def test_snapshot_audit_accepts_complete_markets_with_small_scope_difference(self):
        target = date(2026, 8, 27)
        rows = [
            {"DATE": "2026-08-27", "MARKET": "融资融券_沪证", "RZYE": 1_330_000_000_000},
            {"DATE": "2026-08-27", "MARKET": "融资融券_深证", "RZYE": 1_280_000_000_000},
            {"DATE": "2026-08-27", "MARKET": "融资融券_北证", "RZYE": 8_000_000_000},
        ]
        audit = self.service._audit_stock_snapshot(
            rows,
            target,
            {"financing_balance": 2_636_000_000_000},
        )
        self.assertTrue(audit["passed"])
        self.assertLess(audit["balance_deviation_pct"], 2)

    async def test_own_history_metric_uses_stock_history_and_same_date_turnover(self):
        start = date(2025, 8, 1)
        margin_rows = []
        price_rows = []
        for index in range(250):
            trade_date = start + timedelta(days=index)
            balance = 1_000_000_000 + index * 1_000_000
            margin_rows.append(MarginStockDaily(
                stock_code="600519", stock_name="贵州茅台", trade_date=trade_date,
                financing_balance=balance, financing_buy=10_000_000, financing_repay=8_000_000,
                financing_net_buy=2_000_000, financing_ratio=6.5, financing_buy_ratio=8.0,
                price_change_5d=3.0, close_price=100 + index * 0.1, turnover_rate=1.5,
            ))
            price_rows.append(StockDailyBar(
                stock_code="600519", stock_name="贵州茅台", trade_date=trade_date,
                close_price=100 + index * 0.1, change_pct=0.2, turnover=1.5,
            ))
        metric = self.service._metric_from_rows("600519", margin_rows, price_rows)
        self.assertIsNotNone(metric)
        self.assertGreater(metric["percentile_250"], 95)
        self.assertIsNotNone(metric["lri_score"])
        self.assertEqual(metric["coverage_pct"], 100)
        self.assertEqual(
            metric["components"]["own_history_percentile"]["explanation"],
            "只使用该股票自身历史，不用全市场横截面排名代替。",
        )

    def test_own_history_metric_uses_margin_price_series_when_daily_bars_are_not_warmed(self):
        start = date(2025, 8, 1)
        rows = [
            MarginStockDaily(
                stock_code="600519", stock_name="贵州茅台",
                trade_date=start + timedelta(days=index),
                financing_balance=1_000_000_000 + index * 1_000_000,
                financing_ratio=4.0,
                close_price=100 + index * 0.1,
                pct_change=0.1 + (index % 5) * 0.05,
                price_change_5d=0.5,
            )
            for index in range(250)
        ]
        metric = self.service._metric_from_rows("600519", rows, [])
        self.assertIsNotNone(metric)
        self.assertIsNotNone(metric["volatility_20d"])
        self.assertIsNotNone(metric["lri_score"])
        self.assertGreaterEqual(metric["coverage_pct"], 80)

    async def test_non_margin_stock_never_returns_zero_risk(self):
        with patch(
            "services.margin_leverage.collector.fetch_margin_stock_history",
            new_callable=AsyncMock, return_value=[],
        ):
            result = await self.service.stock_detail("600519")
        self.assertFalse(result["available"])
        self.assertFalse(result["eligible"])
        self.assertEqual(result["message"], "当前股票不是融资融券标的")
        self.assertEqual(result["risk_message"], "暂无两融风险评分")

    async def test_market_payload_is_explicitly_non_realtime(self):
        async with self.session_factory() as session:
            session.add(MarginMarketDaily(
                trade_date=date(2026, 8, 28), margin_balance=2_000, financing_balance=1_900,
                securities_balance=100, financing_buy=200, financing_repay=150,
                financing_net_buy=50, lmi_score=62, lmi_level="升温",
                components={"score": 62, "level": "升温"}, source="test",
            ))
            await session.commit()
        result = await self.service.market(250)
        self.assertTrue(result["available"])
        self.assertFalse(result["meta"]["is_realtime"])
        self.assertIn("T+1", result["meta"]["disclosure_note"])

    async def test_rankings_join_latest_stock_and_lri_rows(self):
        target = date(2026, 8, 28)
        async with self.session_factory() as session:
            for code, name, lri in (("600001", "甲公司", 88), ("000001", "乙公司", 55)):
                session.add(MarginStockDaily(
                    stock_code=code, stock_name=name, trade_date=target,
                    financing_balance=1_000_000, financing_ratio=6,
                ))
                session.add(StockLeverageMetric(
                    stock_code=code, trade_date=target, lri_score=lri,
                    lri_level="高风险" if lri > 80 else "关注", coverage_pct=100,
                    components={}, risk_reasons=[], validation_conditions=[], invalidation_conditions=[],
                ))
            await session.commit()
        result = await self.service.stock_rankings(metric="lri", limit=100)
        self.assertEqual(result["rankings"][0]["stock_name"], "甲公司")
        self.assertEqual(result["rankings"][0]["metric"]["lri_score"], 88)

    async def test_rankings_ignore_newer_partial_exchange_rows(self):
        complete_date = date(2026, 8, 27)
        partial_date = date(2026, 8, 28)
        async with self.session_factory() as session:
            session.add(MarginMarketDaily(
                trade_date=complete_date,
                financing_balance=2_600_000_000_000,
                components={},
            ))
            session.add_all([
                MarginStockDaily(
                    stock_code="600001", stock_name="完整甲", trade_date=complete_date,
                    financing_balance=2_000_000,
                ),
                MarginStockDaily(
                    stock_code="000001", stock_name="完整乙", trade_date=complete_date,
                    financing_balance=1_000_000,
                ),
                MarginStockDaily(
                    stock_code="600519", stock_name="次日单股", trade_date=partial_date,
                    financing_balance=9_000_000,
                ),
            ])
            await session.commit()
        result = await self.service.stock_rankings(metric="balance", limit=100)
        self.assertEqual(result["meta"]["data_date"], complete_date.isoformat())
        self.assertEqual([row["stock_name"] for row in result["rankings"]], ["完整甲", "完整乙"])

    async def test_calculate_lmi_persists_explainable_components(self):
        start = date(2025, 8, 1)
        async with self.session_factory() as session:
            for index in range(250):
                session.add(MarginMarketDaily(
                    trade_date=start + timedelta(days=index),
                    financing_balance=10_000_000_000 + index * 20_000_000,
                    financing_net_buy=30_000_000, market_turnover_amount=100_000_000_000,
                    market_index_close=3000 + index, components={},
                ))
            target = start + timedelta(days=249)
            for index in range(20):
                session.add(MarginStockDaily(
                    stock_code=f"600{index:03d}", stock_name=str(index), trade_date=target,
                    financing_balance=100_000_000, financing_ratio=9 if index < 4 else 3,
                    sector_name=f"行业{index % 5}",
                ))
            for index in range(10):
                session.add(MarginSectorDaily(
                    trade_date=target, sector_type="industry", sector_code=str(index),
                    sector_name=str(index), financing_balance=1_000_000_000 - index * 10_000_000,
                ))
            await session.commit()
        result = await self.service._calculate_lmi(target)
        self.assertIsNotNone(result["score"])
        self.assertIn("market_percentile_250", result["components"])
        self.assertEqual(result["sector_mapping_coverage_pct"], 100)
        async with self.session_factory() as session:
            row = await session.get(MarginMarketDaily, target)
        self.assertEqual(row.lmi_score, result["score"])


if __name__ == "__main__":
    unittest.main()
