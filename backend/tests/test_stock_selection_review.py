import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import StockDailyBar, StockSelectionRun
from services.stock_selection_review import stock_selection_review_service


PARAMETERS = {
    "mode": "quick",
    "risk_profile": "balanced",
    "horizon": "week",
    "top_n": 5,
    "sector": None,
    "sector_code": None,
    "selection_style": "default",
    "sector_limit": None,
    "factor_filters": {"preset": "off", "enabled": False},
}


def _result(items, *, parameters=None, watchlist=None):
    return {
        "selection_parameters": parameters if parameters is not None else dict(PARAMETERS),
        "recommendations": items,
        "watchlist": watchlist or [],
    }


class StockSelectionReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_patch = patch(
            "services.stock_selection_review.async_session", self.factory
        )
        self.session_patch.start()

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()

    async def _run(self, *, data_date, result, created_at):
        async with self.factory() as session:
            row = StockSelectionRun(
                mode="quick", risk_profile="balanced", data_date=data_date,
                result=result, created_at=created_at,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.id

    async def test_compatible_review_reports_added_retained_removed(self):
        prior = await self._run(
            data_date=date(2026, 8, 3),
            result=_result([{"code": "600001", "name": "甲"}, {"code": "600003", "name": "丙"}], watchlist=[{"code": "600004", "name": "丁"}]),
            created_at=datetime(2026, 8, 3, 7),
        )
        current = await self._run(
            data_date=date(2026, 8, 4),
            result=_result([{"code": "600001", "name": "甲"}, {"code": "600002", "name": "乙"}], watchlist=[{"code": "600004", "name": "丁"}]),
            created_at=datetime(2026, 8, 4, 7),
        )
        review = await stock_selection_review_service.review(current)
        self.assertEqual(review["previous_run_id"], prior)
        self.assertTrue(review["comparison"]["comparable"])
        self.assertEqual([item["code"] for item in review["comparison"]["added"]], ["600002"])
        self.assertEqual([item["code"] for item in review["comparison"]["retained"]], ["600001", "600004"])
        self.assertEqual([item["code"] for item in review["comparison"]["removed"]], ["600003"])

    async def test_missing_parameters_are_not_claimed_compatible(self):
        prior = await self._run(
            data_date=date(2026, 8, 3),
            result={"recommendations": [{"code": "600001", "name": "甲"}]},
            created_at=datetime(2026, 8, 3, 7),
        )
        current = await self._run(
            data_date=date(2026, 8, 4),
            result=_result([{ "code": "600001", "name": "甲"}]),
            created_at=datetime(2026, 8, 4, 7),
        )
        review = await stock_selection_review_service.review(current)
        self.assertIsNone(review["previous_run_id"])
        self.assertFalse(review["comparison"]["comparable"])
        self.assertIn("完整筛选条件", review["comparison"]["reason"])

    async def test_five_sessions_complete_and_longer_windows_pending(self):
        current = await self._run(
            data_date=date(2026, 8, 3),
            result=_result([{ "code": "600001", "name": "甲"}]),
            created_at=datetime(2026, 8, 3, 7),
        )
        dates = [date(2026, 8, day) for day in (4, 5, 6, 7, 10, 11)]
        async with self.factory() as session:
            session.add_all([
                StockDailyBar(
                    stock_code="600001", stock_name="甲", trade_date=trade_date,
                    close_price=10 + index, source="tencent_qfq",
                )
                for index, trade_date in enumerate(dates)
            ])
            await session.commit()
        review = await stock_selection_review_service.review(current)
        stock = review["performance"]["stocks"][0]
        self.assertEqual(stock["observed_date"], "2026-08-04")
        self.assertEqual(stock["windows"][0]["status"], "complete")
        self.assertEqual(stock["windows"][1]["status"], "pending")
        self.assertEqual(stock["windows"][2]["status"], "pending")
        self.assertEqual(review["performance"]["windows"][0]["sample_count"], 1)

    async def test_unadjusted_or_missing_bars_are_missing_and_snapshot_date_is_not_used(self):
        current = await self._run(
            data_date=date(2026, 8, 3),
            result=_result([
                {"code": "600001", "name": "甲"},
                {"code": "600002", "name": "乙"},
            ]),
            created_at=datetime(2026, 8, 3, 7),
        )
        async with self.factory() as session:
            session.add_all([
                # The snapshot-date bar cannot be used as the observation baseline.
                StockDailyBar(stock_code="600001", trade_date=date(2026, 8, 3), close_price=10, source="tencent_qfq"),
                # Plain close has no auditable adjustment contract.
                StockDailyBar(stock_code="600002", trade_date=date(2026, 8, 4), close_price=20, source="eastmoney"),
            ])
            await session.commit()
        review = await stock_selection_review_service.review(current)
        stocks = {item["code"]: item for item in review["performance"]["stocks"]}
        self.assertIsNone(stocks["600001"]["observed_date"])
        self.assertEqual(stocks["600001"]["windows"][0]["status"], "missing")
        self.assertEqual(stocks["600002"]["windows"][0]["status"], "missing")


    async def test_cached_quote_date_does_not_backdate_selection_and_watch_is_not_performance(self):
        current = await self._run(
            data_date=date(2026, 8, 3),
            result=_result([{"code": "600001", "name": "甲"}], watchlist=[{"code": "600002", "name": "乙"}]),
            created_at=datetime(2026, 8, 6, 7),
        )
        dates = [date(2026, 8, d) for d in (4, 5, 6, 7, 10, 11, 12, 13, 14)]
        async with self.factory() as session:
            session.add_all([StockDailyBar(stock_code="600001", trade_date=d, close_price=10+i, source="tencent_qfq") for i,d in enumerate(dates)])
            await session.commit()
        review = await stock_selection_review_service.review(current)
        stocks = review["performance"]["stocks"]
        self.assertEqual(len(stocks), 1)
        self.assertEqual(stocks[0]["observed_date"], "2026-08-07")
        self.assertEqual(stocks[0]["windows"][0]["status"], "complete")

    async def test_missing_baseline_does_not_shift_to_a_later_price(self):
        current = await self._run(data_date=date(2026,8,3), result=_result([{"code":"600001"}]), created_at=datetime(2026,8,3,7))
        async with self.factory() as session:
            session.add(StockDailyBar(stock_code="600999", trade_date=date(2026,8,4), close_price=10, source="tencent_qfq"))
            session.add(StockDailyBar(stock_code="600001", trade_date=date(2026,8,5), close_price=20, source="tencent_qfq"))
            await session.commit()
        review = await stock_selection_review_service.review(current)
        self.assertIsNone(review["performance"]["stocks"][0]["observed_date"])
        self.assertEqual(review["performance"]["stocks"][0]["windows"][0]["status"], "missing")

    def test_future_or_unclosed_bars_cannot_mature_a_window(self):
        from services.stock_selection_review import _performance, SHANGHAI
        from types import SimpleNamespace
        run = SimpleNamespace(data_date=date(2026,8,3), created_at=datetime(2026,8,3,7))
        dates = [date(2026,8,d) for d in (4,5,6,7,10,11)]
        bars = [SimpleNamespace(stock_code="600001", stock_name="甲", trade_date=d,close_price=10+i,source="tencent_qfq") for i,d in enumerate(dates)]
        result = _performance(run,[{"code":"600001","name":"甲"}],bars,dates,now=datetime(2026,8,11,10,tzinfo=SHANGHAI))
        self.assertEqual(result["stocks"][0]["windows"][0]["status"], "pending")

    def test_qualification_downgrade_is_explained_in_retained_list(self):
        from services.stock_selection_review import _comparison
        prior = _result([{"code":"600001","qualification":{"status":"qualified"}}])
        current = _result([],watchlist=[{"code":"600001","qualification":{"status":"watch","reasons":["公告证据不足"]}}])
        result = _comparison(current,prior)
        self.assertIn("合格候选 → 待验证观察",result["retained"][0]["reason"])
        self.assertIn("公告证据不足",result["retained"][0]["reason"])

if __name__ == "__main__":
    unittest.main()
