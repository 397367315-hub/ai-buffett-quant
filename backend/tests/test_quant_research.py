import unittest
from datetime import date, timedelta

from services.quant_research import QuantResearchEngine


def _bars(stock_count: int = 12, days: int = 130) -> list[dict]:
    start = date(2025, 12, 1)
    rows = []
    for stock_index in range(stock_count):
        for day_index in range(days):
            trade_date = start + timedelta(days=day_index)
            close = 10 + stock_index + day_index * (0.08 + stock_index * 0.003)
            rows.append({
                "code": f"600{stock_index:03d}",
                "date": trade_date,
                "open": close * 0.999,
                "close": close,
                "amount": 5_000_000,
                "source": "test",
            })
    return rows


class QuantResearchTests(unittest.TestCase):
    def test_point_in_time_simulation_uses_t_plus_one_and_non_overlapping_holds(self):
        grouped = QuantResearchEngine._normalise_bars(_bars(), date(2026, 4, 10))
        result = QuantResearchEngine._simulate(
            grouped,
            evaluation_start=date(2026, 3, 1),
            evaluation_end=date(2026, 4, 10),
            lookback_days=20,
            holding_days=5,
            top_n=5,
            capital=400_000,
        )

        rows = result["daily_results"]
        self.assertGreaterEqual(len(rows), 5)
        self.assertEqual(rows[0]["entry_date"], "2026-03-02")
        self.assertEqual(rows[0]["exit_date"], "2026-03-06")
        self.assertTrue(all(rows[index]["date"] >= rows[index - 1]["exit_date"] for index in range(1, len(rows))))
        self.assertTrue(all(row["average_cost_pct"] > 0 for row in rows))
        self.assertTrue(all(row["selected_count"] <= 5 for row in rows))

    def test_metrics_include_cost_adjusted_benchmark_alpha_beta_and_ic(self):
        grouped = QuantResearchEngine._normalise_bars(_bars(), date(2026, 4, 10))
        simulation = QuantResearchEngine._simulate(
            grouped,
            evaluation_start=date(2026, 3, 1),
            evaluation_end=date(2026, 4, 10),
            lookback_days=20,
            holding_days=5,
            top_n=5,
            capital=400_000,
        )
        metrics = QuantResearchEngine._metrics(simulation, 5)

        for key in (
            "total_return", "benchmark_return", "max_drawdown", "sharpe_ratio",
            "alpha_annualized", "beta", "information_coefficient",
        ):
            self.assertIn(key, metrics)
        self.assertGreater(metrics["trading_periods"], 0)
        self.assertEqual(
            simulation["_daily_results_internal"],
            simulation["daily_results"],
        )


if __name__ == "__main__":
    unittest.main()
