import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from quant.storage import QuantJsonStore
from quant.schemas import FQERequest
from services.fqe_engine import FQECompareService, FundamentalQuantEngine, fqe_compare_service
from services.stock_features import StockFeatureService, _a_share_code, _ttm_value


def _stock(index: int, industry: str) -> dict:
    return {
        "code": f"600{index:03d}",
        "name": f"测试{index}",
        "sector": industry,
        "market_cap": 80 + index,
        "pe": 12 + index * 0.5,
        "roe": 14 + index * 0.2,
        "ocf_to_profit_ttm": 1.0 + index * 0.01,
        "debt_ratio": 42,
        "deducted_profit_growth": 24 + index,
        "ttm_available": True,
        "financial_disclosed_at": "2026-08-01",
    }


class FQEEngineTests(unittest.IsolatedAsyncioTestCase):
    def test_fqe_request_rejects_unsafe_ranges(self):
        with self.assertRaises(ValidationError):
            FQERequest(top_n=4)
        with self.assertRaises(ValidationError):
            FQERequest(candidate_pool=19)
        self.assertEqual(FQERequest(mode="strict").mode, "strict")

    async def test_fqe_service_reuses_running_job_without_duplicate_work(self):
        running = {"job_id": "fqe_existing", "status": "running"}
        with patch("services.fqe_engine.latest_running_job", return_value=running), patch(
            "services.fqe_engine.create_job"
        ) as create_job, patch("services.fqe_engine.spawn") as spawn:
            result = await fqe_compare_service.start(force=False)
        self.assertEqual(result, running)
        create_job.assert_not_called()
        spawn.assert_not_called()

    async def test_latest_result_survives_an_empty_process_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            store = QuantJsonStore(Path(directory) / "json")
            service = FQECompareService()
            result = {
                "version": 1,
                "engine_mode": "COMPARE_DUAL_ENGINE",
                "generated_at": "2026-08-09T17:00:00+08:00",
                "retail_portfolio": {"count": 10, "holdings": []},
                "institutional_portfolio": {"count": 10, "holdings": []},
            }

            with patch("services.fqe_engine.async_session", session_factory), patch(
                "services.fqe_engine.quant_store", store
            ):
                self.assertTrue(await service.save_latest(result))
                store.write("jobs", {"version": 1, "scan": {}, "backtest": {}, "fqe": {}})
                self.assertEqual(await service.get_latest(), result)

            await engine.dispose()

    def test_a_share_code_is_normalized_for_financial_joins(self):
        self.assertEqual(_a_share_code("600519"), "600519")
        self.assertEqual(_a_share_code("000333"), "000333")
        self.assertIsNone(_a_share_code("600519.SH"))

    def test_ttm_uses_current_plus_prior_year_full_year_minus_same_period(self):
        rows = {
            date(2025, 3, 31): {"net_profit": 20},
            date(2025, 6, 30): {"net_profit": 45},
            date(2025, 9, 30): {"net_profit": 70},
            date(2025, 12, 31): {"net_profit": 100},
            date(2026, 3, 31): {"net_profit": 30},
        }
        self.assertEqual(_ttm_value(rows, date(2026, 3, 31), "net_profit"), 110)
        self.assertEqual(_ttm_value(rows, date(2025, 12, 31), "net_profit"), 100)

    def test_pit_cache_key_keeps_research_dates_separate(self):
        self.assertEqual(
            StockFeatureService._cache_key(date(2026, 8, 8)),
            "stock_feature_snapshot_v2:2026-08-08",
        )
        self.assertNotEqual(
            StockFeatureService._cache_key(date(2026, 8, 7)),
            StockFeatureService._cache_key(date(2026, 8, 8)),
        )

    def test_retail_engine_does_not_treat_missing_strict_fields_as_passed(self):
        stock = _stock(1, "家用电器")
        strict = FundamentalQuantEngine.run_retail([stock], 10, "strict")
        pragmatic = FundamentalQuantEngine.run_retail([stock], 10, "pragmatic")
        self.assertEqual(strict["count"], 0)
        self.assertEqual(pragmatic["count"], 1)
        self.assertEqual(pragmatic["holdings"][0]["engine_type"], "Retail_Light")
        self.assertTrue(pragmatic["warnings"])

    async def test_institutional_weights_sum_and_respect_industry_cap(self):
        stocks = [_stock(index, ["电子", "医药", "机械", "家电"][index % 4]) for index in range(12)]
        covariance = [[0.0 if left != right else 0.04 ** 2 for right in range(10)] for left in range(10)]
        with patch.object(
            FundamentalQuantEngine,
            "_load_covariance",
            new=AsyncMock(return_value=(covariance, {"available": True, "usable_days": 120, "stock_count": 10})),
        ):
            result = await FundamentalQuantEngine.run_institutional(stocks, top_n=10, candidate_pool=12)

        weights = [item["weight"] for item in result["holdings"]]
        self.assertEqual(len(weights), 10)
        self.assertAlmostEqual(sum(weights), 1.0, places=5)
        self.assertLessEqual(max(weights), 0.15 + 1e-5)
        self.assertGreaterEqual(min(weights), 0.02 - 1e-5)
        industry_totals = {}
        for item in result["holdings"]:
            industry_totals[item["industry"]] = industry_totals.get(item["industry"], 0) + item["weight"]
        self.assertTrue(all(value <= 0.25 + 1e-5 for value in industry_totals.values()))
        self.assertFalse(result["optimizer"]["constraint_audit"]["violations"])


if __name__ == "__main__":
    unittest.main()
