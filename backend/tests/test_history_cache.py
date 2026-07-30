import asyncio
import socket
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy.exc import OperationalError

from services.history_cache import HistoryCacheService, engine


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


class HistoryCacheAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_backfill_request_has_a_hard_deadline(self):
        service = HistoryCacheService()
        service._BACKFILL_REQUEST_TIMEOUT_SECONDS = 0.001

        with self.assertRaises(asyncio.TimeoutError):
            await service._request_with_deadline(asyncio.sleep(0.1))

    async def test_backfill_database_operation_retries_a_connection_failure(self):
        service = HistoryCacheService()
        attempts = 0

        async def flaky_operation():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OperationalError("SELECT 1", {}, ConnectionRefusedError())
            return "written"

        with patch.object(type(engine), "dispose", new_callable=AsyncMock) as dispose:
            with patch("services.history_cache.asyncio.sleep", new_callable=AsyncMock):
                result = await service._with_database_retry(flaky_operation)

        self.assertEqual(result, "written")
        self.assertEqual(attempts, 2)
        dispose.assert_awaited_once()

    async def test_backfill_database_operation_retries_a_dns_failure(self):
        service = HistoryCacheService()
        attempts = 0

        async def flaky_operation():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise socket.gaierror(-2, "Name or service not known")
            return "written"

        with patch.object(type(engine), "dispose", new_callable=AsyncMock) as dispose:
            with patch("services.history_cache.asyncio.sleep", new_callable=AsyncMock):
                result = await service._with_database_retry(flaky_operation)

        self.assertEqual(result, "written")
        self.assertEqual(attempts, 2)
        dispose.assert_awaited_once()

    async def test_backfill_database_operation_survives_an_extended_dns_outage(self):
        service = HistoryCacheService()
        attempts = 0

        async def flaky_operation():
            nonlocal attempts
            attempts += 1
            if attempts <= 5:
                raise socket.gaierror(-2, "Name or service not known")
            return "written"

        with patch.object(type(engine), "dispose", new_callable=AsyncMock) as dispose:
            with patch("services.history_cache.asyncio.sleep", new_callable=AsyncMock):
                result = await service._with_database_retry(flaky_operation)

        self.assertEqual(result, "written")
        self.assertEqual(attempts, 6)
        self.assertEqual(dispose.await_count, 5)

    async def test_backfill_database_operation_retries_database_recovery(self):
        service = HistoryCacheService()
        attempts = 0

        class DatabaseRecoveryError(Exception):
            pass

        async def recovering_operation():
            nonlocal attempts
            attempts += 1
            if attempts < 4:
                raise DatabaseRecoveryError()
            return "written"

        with patch("services.history_cache.PostgresConnectionError", DatabaseRecoveryError):
            with patch.object(type(engine), "dispose", new_callable=AsyncMock) as dispose:
                with patch("services.history_cache.asyncio.sleep", new_callable=AsyncMock):
                    result = await service._with_database_retry(recovering_operation)

        self.assertEqual(result, "written")
        self.assertEqual(attempts, 4)
        self.assertEqual(dispose.await_count, 3)


if __name__ == "__main__":
    unittest.main()
