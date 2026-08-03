import asyncio
import socket
import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.exc import OperationalError

from services.history_cache import HistoryCacheService, collector, engine
from services.data_sync import DataSyncService, history_cache


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
    async def test_current_concept_snapshot_uses_verified_stock_trade_date(self):
        service = HistoryCacheService()
        service._upsert = AsyncMock(side_effect=[1, 2])
        service._clear_stale_current_board_snapshot = AsyncMock(return_value=3)
        rows = [{
            "code": "BK001", "name": "测试概念", "close_price": 10.0,
            "change_pct": 1.2, "main_net_inflow": 100, "main_net_inflow_pct": 2.0,
            "super_large_net_inflow": 50, "large_net_inflow": 20,
            "medium_net_inflow": 10, "small_net_inflow": 5,
            "up_count": 3, "down_count": 1, "leading_stock": "测试股份",
        }]

        with patch.object(collector, "fetch_all_concept_flow", new_callable=AsyncMock, return_value=rows):
            result = await service.cache_current_concept_flow(
                trade_date=date(2026, 8, 3),
                verified_trade_date=True,
            )

        self.assertEqual(result["trade_date"], "2026-08-03")
        self.assertEqual(result["cleared_stale_rows"], 3)
        service._clear_stale_current_board_snapshot.assert_awaited_once()
        payload = service._upsert.await_args_list[1].args[1]
        self.assertEqual(payload[0]["trade_date"], date(2026, 8, 3))

    async def test_market_sync_uses_stock_snapshot_date_for_board_snapshots(self):
        stock_bars = {
            "status": "success",
            "data_date": "2026-08-03",
            "count": 5_000,
        }
        with (
            patch.object(history_cache, "cache_current_stock_bars", new_callable=AsyncMock, return_value=stock_bars),
            patch.object(history_cache, "cache_current_northbound", new_callable=AsyncMock, return_value={"status": "success"}),
            patch.object(history_cache, "cache_current_concept_flow", new_callable=AsyncMock, return_value={"status": "success"}) as concept,
            patch.object(history_cache, "cache_current_industry_flow", new_callable=AsyncMock, return_value={"status": "success"}) as industry,
        ):
            result = await DataSyncService.sync_market_snapshot()

        expected = {"trade_date": date(2026, 8, 3), "verified_trade_date": True}
        concept.assert_awaited_once_with(**expected)
        industry.assert_awaited_once_with(**expected)
        self.assertEqual(result["stock_bars"], stock_bars)

    async def test_current_stock_snapshot_writes_only_timestamp_verified_rows(self):
        service = HistoryCacheService()
        quote_at = datetime(2026, 8, 3, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
        stale_at = datetime(2026, 8, 2, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
        snapshot = {
            "source": "eastmoney",
            "data_date": "2026-08-03",
            "source_updated_at": quote_at.isoformat(),
            "is_realtime": False,
            "complete": True,
            "stocks": [
                {
                    "code": "600519", "name": "贵州茅台", "price": 120.0,
                    "open": 118.0, "high": 121.0, "low": 117.5,
                    "volume": 100_000, "amount": 12_000_000,
                    "amplitude": 3.0, "change_pct": 1.7,
                    "change_amount": 2.0, "turnover": 0.8,
                    "quote_timestamp": int(quote_at.timestamp()),
                },
                {
                    "code": "000001", "name": "平安银行", "price": 10.0,
                    "quote_timestamp": int(stale_at.timestamp()),
                },
            ],
        }
        service._upsert = AsyncMock(return_value=1)

        with patch.object(collector, "fetch_quant_market_snapshot", new_callable=AsyncMock, return_value=snapshot) as fetch:
            result = await service.cache_current_stock_bars()

        fetch.assert_awaited_once_with(include_special=True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["data_date"], "2026-08-03")
        self.assertEqual(result["verified_stocks"], 1)
        rows = service._upsert.await_args.args[1]
        self.assertEqual(rows[0]["stock_code"], "600519")
        self.assertEqual(rows[0]["trade_date"].isoformat(), "2026-08-03")
        self.assertEqual(rows[0]["volume"], 100_000)

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

    async def test_database_backfill_failure_is_left_for_scheduler_recovery(self):
        service = HistoryCacheService()
        database_error = socket.gaierror(-2, "Name or service not known")

        with patch.object(service, "_set_run", new_callable=AsyncMock, side_effect=database_error) as set_run:
            await service._run_backfill(3, 365, True, None)

        self.assertEqual(set_run.await_count, 1)

    async def test_network_backfill_failure_is_left_for_scheduler_recovery(self):
        service = HistoryCacheService()
        service._set_run = AsyncMock()

        with patch.object(
            collector,
            "fetch_all_concept_flow",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("upstream timed out"),
        ):
            with patch.object(collector, "fetch_all_industry_flow", new_callable=AsyncMock, return_value=[]):
                await service._run_backfill(3, 365, True, None)

        self.assertEqual(service._set_run.await_count, 1)

    async def test_industry_backfill_does_not_write_concept_only_columns(self):
        service = HistoryCacheService()
        captured: dict[str, list[dict]] = {}

        async def capture_upsert(model, rows, keys):
            del keys
            captured[model.__tablename__] = rows
            return len(rows)

        payload = {
            "code": "BK0475",
            "history": [{
                "trade_date": "2026-07-30",
                "close_price": 100.0,
                "change_pct": 1.0,
                "main_net_inflow": 1,
                "main_net_inflow_pct": 1.0,
                "super_large_net_inflow": 1,
                "large_net_inflow": 1,
                "medium_net_inflow": 1,
                "small_net_inflow": 1,
            }],
        }

        service._upsert = capture_upsert
        service._set_run = AsyncMock()
        with patch.object(collector, "fetch_board_flow_history", new_callable=AsyncMock, return_value=payload):
            await service._backfill_boards(
                run_id=4,
                board_jobs=[("industry", {"code": "BK0475"})],
                days=1,
                completed_offset=0,
                initial_records_written=0,
            )

        row = captured["industry_fund_flow_daily"][0]
        self.assertNotIn("leading_stock", row)
        self.assertEqual(row["board_code"], "BK0475")

    async def test_board_backfill_retries_transient_network_failure(self):
        service = HistoryCacheService()
        attempts = 0

        async def capture_upsert(model, rows, keys):
            del model, keys
            return len(rows)

        async def flaky_fetch(board_code, days):
            nonlocal attempts
            del board_code, days
            attempts += 1
            if attempts == 1:
                raise httpx.ReadTimeout("upstream timed out")
            return {
                "code": "BK0475",
                "history": [{
                    "trade_date": "2026-07-30",
                    "close_price": 100.0,
                    "change_pct": 1.0,
                    "main_net_inflow": 1,
                    "main_net_inflow_pct": 1.0,
                    "super_large_net_inflow": 1,
                    "large_net_inflow": 1,
                    "medium_net_inflow": 1,
                    "small_net_inflow": 1,
                }],
            }

        service._upsert = capture_upsert
        service._set_run = AsyncMock()
        with patch.object(collector, "fetch_board_flow_history", side_effect=flaky_fetch):
            with patch("services.history_cache.asyncio.sleep", new_callable=AsyncMock):
                written, failures, issue = await service._backfill_boards(
                    run_id=4,
                    board_jobs=[("industry", {"code": "BK0475"})],
                    days=1,
                    completed_offset=0,
                    initial_records_written=0,
                )

        self.assertEqual(attempts, 2)
        self.assertEqual(written, 1)
        self.assertEqual(failures, 0)
        self.assertIsNone(issue)

    async def test_board_backfill_does_not_treat_a_snapshot_as_year_history(self):
        service = HistoryCacheService()
        service._set_run = AsyncMock()
        payload = {
            "code": "BK0475",
            "history": [{
                "trade_date": "2026-07-30",
                "close_price": 100.0,
                "change_pct": 1.0,
                "main_net_inflow": 1,
                "main_net_inflow_pct": 1.0,
                "super_large_net_inflow": 1,
                "large_net_inflow": 1,
                "medium_net_inflow": 1,
                "small_net_inflow": 1,
            }],
        }

        with patch.object(collector, "fetch_board_flow_history", new_callable=AsyncMock, return_value=payload):
            with patch("services.history_cache.shanghai_now", return_value=datetime(2026, 7, 30)):
                written, failures, issue = await service._backfill_boards(
                    run_id=4,
                    board_jobs=[("industry", {"code": "BK0475"})],
                    days=365,
                    completed_offset=2,
                    initial_records_written=3,
                )

        self.assertEqual((written, failures, issue), (0, 0, "snapshot_only"))
        service._set_run.assert_awaited_once_with(
            4,
            completed_tasks=3,
            records_written=3,
        )

    async def test_current_board_snapshot_retains_small_order_flow(self):
        service = HistoryCacheService()
        captured: dict[str, list[dict]] = {}

        async def capture_upsert(model, rows, keys):
            del keys
            captured[model.__tablename__] = rows
            return len(rows)

        service._upsert = capture_upsert
        with patch("services.history_cache.shanghai_now", return_value=datetime(2026, 7, 30)):
            written = await service._cache_current_board_snapshots([
                ("concept", {
                    "code": "BK0001", "close_price": 10.0, "change_pct": 1.0,
                    "main_net_inflow": 100, "main_net_inflow_pct": 1.0,
                    "super_large_net_inflow": 40, "large_net_inflow": 30,
                    "medium_net_inflow": 20, "small_net_inflow": 10,
                    "up_count": 8, "down_count": 2, "leading_stock": "测试股",
                }),
                ("industry", {
                    "code": "BK0002", "close_price": 20.0, "change_pct": -1.0,
                    "main_net_inflow": -100, "main_net_inflow_pct": -1.0,
                    "super_large_net_inflow": -40, "large_net_inflow": -30,
                    "medium_net_inflow": -20, "small_net_inflow": -10,
                    "up_count": 2, "down_count": 8,
                }),
            ])

        self.assertEqual(written, 2)
        self.assertEqual(captured["concept_fund_flow_daily"][0]["small_net_inflow"], 10)
        self.assertEqual(captured["industry_fund_flow_daily"][0]["small_net_inflow"], -10)

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
