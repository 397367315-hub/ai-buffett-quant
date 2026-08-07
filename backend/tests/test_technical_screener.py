import unittest
from unittest.mock import AsyncMock, patch

from services.technical_screener import TechnicalScreenerService, normalize_screener_criteria


def stock(**overrides):
    row = {
        "code": "600000",
        "name": "浦发银行",
        "price": 10.0,
        "change_pct": 2.0,
        "turnover": 5.0,
        "volume_ratio": 2.0,
        "pe": 20.0,
        "pb": 2.0,
        "roe": 12.0,
        "market_cap": 10_000_000_000,
        "main_net_inflow": 100_000_000,
        "main_net_inflow_pct": 2.0,
        "amount": 1_000_000_000,
        "amplitude": 5.0,
    }
    row.update(overrides)
    return row


class TechnicalScreenerTests(unittest.IsolatedAsyncioTestCase):
    def test_short_and_long_presets_have_distinct_auditable_thresholds(self):
        short = normalize_screener_criteria({"preset": "short"})
        long = normalize_screener_criteria({"preset": "long"})

        self.assertEqual(short["volume_ratio"], [1.2, 6.0])
        self.assertEqual(short["main_net_inflow_yi_min"], 0.0)
        self.assertEqual(long["roe_min"], 8.0)
        self.assertEqual(long["pe_ttm"], [0.01, 50.0])
        self.assertTrue(long["require_profitable"])

    def test_filter_rejects_failed_and_missing_factors_with_reason_counts(self):
        service = TechnicalScreenerService()
        config = normalize_screener_criteria({"preset": "short", "exclude_star_market": True})

        selected, rejected = service._filter([
            stock(),
            stock(code="688001"),
            stock(code="600001", volume_ratio=1.19),
            stock(code="600002", volume_ratio=1.2),
            stock(code="600003", roe=None),
        ], config)

        self.assertEqual([item["code"] for item in selected], ["600000"])
        self.assertEqual(rejected["star_market"], 1)
        self.assertEqual(rejected["volume_ratio"], 2)
        self.assertEqual(rejected["roe_missing"], 1)

    async def test_closed_market_uses_complete_persistent_snapshot_without_live_wait(self):
        service = TechnicalScreenerService()
        cached = {
            "stocks": [stock()],
            "complete": True,
            "source": "cache",
            "data_date": "2026-08-06",
            "source_updated_at": "2026-08-06T15:00:00+08:00",
            "is_realtime": False,
        }
        live = AsyncMock()
        with (
            patch("services.technical_screener.is_a_share_market_session", return_value=False),
            patch("services.technical_screener.load_quant_market_snapshot", new=AsyncMock(return_value=cached)),
            patch("services.technical_screener.collector.fetch_technical_screener", new=live),
        ):
            result = await service.run({"preset": "short"})

        live.assert_not_awaited()
        self.assertTrue(result["cache_used"])
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["data_date"], "2026-08-06")
        self.assertEqual(result["total"], 1)

    def test_invalid_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "下限不能高于上限"):
            normalize_screener_criteria({"preset": "custom", "change_pct": [5, 1]})


if __name__ == "__main__":
    unittest.main()
