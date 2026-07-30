import unittest

from services.history_cache import HistoryCacheService


class _Bind:
    def __init__(self, dialect_name: str):
        self.dialect = type("Dialect", (), {"name": dialect_name})()


class _Session:
    def __init__(self, dialect_name: str):
        self._bind = _Bind(dialect_name)

    def get_bind(self):
        return self._bind


class HistoryCacheTests(unittest.TestCase):
    def test_postgres_stock_bar_batch_stays_below_asyncpg_bind_limit(self):
        row = {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "market": "1",
            "trade_date": "2026-07-30",
            "open_price": 1.0,
            "close_price": 1.0,
            "high_price": 1.0,
            "low_price": 1.0,
            "volume": 1,
            "amount": 1,
            "amplitude": 1.0,
            "change_pct": 1.0,
            "change_amount": 1.0,
            "turnover": 1.0,
            "source": "tencent",
            "updated_at": "2026-07-30T00:00:00",
        }

        batch_size = HistoryCacheService._upsert_batch_size(_Session("postgresql"), [row])

        self.assertLess(batch_size * len(row), 32_767)
        self.assertGreater(batch_size, 1_000)

    def test_sqlite_uses_a_conservative_bind_budget(self):
        batch_size = HistoryCacheService._upsert_batch_size(
            _Session("sqlite"), [{"stock_code": "600519"} for _ in range(10)]
        )

        self.assertEqual(batch_size, 900)


if __name__ == "__main__":
    unittest.main()
