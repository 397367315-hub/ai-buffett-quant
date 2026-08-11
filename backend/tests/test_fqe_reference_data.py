import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import SecurityMaster, SecurityStatusEvent, StockValuationHistory
from services.fqe_reference_data import FQEReferenceDataService


class FQEReferenceDataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session_patch = patch("services.fqe_reference_data.async_session", self.session_factory)
        self.session_patch.start()
        self.service = FQEReferenceDataService()

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()

    def test_valuation_record_calculates_positive_pe_percentile(self):
        record = self.service._valuation_record(
            "600519",
            "贵州茅台",
            [
                {"TRADE_DATE": "2026-08-05", "PE_TTM": 20},
                {"TRADE_DATE": "2026-08-06", "PE_TTM": -5},
                {"TRADE_DATE": "2026-08-07", "PE_TTM": 10},
            ],
            date(2023, 8, 1),
            "test",
        )

        self.assertEqual(record["sample_count"], 3)
        self.assertEqual(record["positive_sample_count"], 2)
        self.assertEqual(record["latest_pe_ttm"], 10)
        self.assertEqual(record["pe_percentile_3y"], 50)

    async def test_enrichment_supplies_listing_pe_and_survivorship_contracts(self):
        async with self.session_factory() as session:
            session.add_all([
                SecurityMaster(
                    stock_code="600519", stock_name="贵州茅台", exchange="SH",
                    list_date=date(2001, 8, 27), status="listed", is_currently_listed=True,
                    date_quality="ftshare_catalog", source="test",
                ),
                SecurityMaster(
                    stock_code="000003", stock_name="PT金田A", exchange="SZ",
                    list_date=date(1991, 7, 3), delist_date=date(2002, 6, 14),
                    status="delisted", is_currently_listed=False,
                    date_quality="status_event", source="test",
                ),
                SecurityStatusEvent(
                    stock_code="000003", stock_name="PT金田A",
                    change_date=date(2002, 6, 14), change_type="终止上市", source="test",
                ),
                StockValuationHistory(
                    stock_code="600519", stock_name="贵州茅台",
                    history=[["2026-08-06", 21.0], ["2026-08-07", 20.0]],
                    requested_start=date(2023, 8, 1), history_start=date(2026, 8, 6),
                    history_end=date(2026, 8, 7), sample_count=2,
                    positive_sample_count=2, latest_pe_ttm=20.0,
                    pe_percentile_3y=50.0, sync_status="available", source="test",
                    updated_at=datetime.utcnow(),
                ),
            ])
            await session.commit()

        result = await self.service.enrich(
            [{"code": "600519", "name": "贵州茅台", "pe": 20.0}],
            date(2026, 8, 9),
        )

        stock = result["stocks"][0]
        self.assertGreater(stock["list_days"], 8_000)
        self.assertEqual(stock["pe_percentile_3y"], 50.0)
        self.assertEqual(result["data_contract"]["listing_history"]["status"], "available")
        self.assertEqual(result["data_contract"]["pe_history_percentile"]["status"], "available")
        self.assertEqual(result["data_contract"]["survivorship_bias"]["status"], "available")
        self.assertEqual(result["coverage"]["current_valuation_series"], 1)

    async def test_enrichment_recalculates_percentile_from_current_pe(self):
        async with self.session_factory() as session:
            session.add(StockValuationHistory(
                stock_code="600519", stock_name="贵州茅台",
                history=[["2026-08-05", 10.0], ["2026-08-06", 20.0]],
                requested_start=date(2023, 8, 1), history_start=date(2026, 8, 5),
                history_end=date(2026, 8, 6), sample_count=2,
                positive_sample_count=2, latest_pe_ttm=20.0,
                pe_percentile_3y=50.0, sync_status="available", source="test",
                updated_at=datetime.utcnow(),
            ))
            await session.commit()

        result = await self.service.enrich(
            [{"code": "600519", "name": "贵州茅台", "pe_ttm": 30.0}],
            date(2026, 8, 9),
        )

        self.assertEqual(result["stocks"][0]["pe_percentile_3y"], 100.0)
        self.assertEqual(result["stocks"][0]["pe_history_end"], "2026-08-06")

    async def test_security_master_uses_ftshare_when_eastmoney_is_partial(self):
        with (
            patch(
                "services.fqe_reference_data.collector.fetch_security_directory_snapshot",
                new=AsyncMock(return_value={
                    "records": [], "complete": False, "failed_pages": [1],
                    "errors": {"1": "ReadTimeout"},
                }),
            ),
            patch(
                "services.fqe_reference_data.ftshare_mcp_client.get_full_stock_filter",
                new=AsyncMock(return_value=[{
                    "symbol": "600519.SH", "name": "贵州茅台", "listing_date": "2001-08-27",
                }]),
            ),
        ):
            result = await self.service._sync_security_master()

        self.assertEqual(result["master_count"], 1)
        self.assertFalse(result["directory_complete"])
        self.assertTrue(any("FTShare" in warning for warning in result["warnings"]))
        async with self.session_factory() as session:
            row = await session.get(SecurityMaster, "600519")
        self.assertIsNotNone(row)
        self.assertEqual(row.list_date, date(2001, 8, 27))
        self.assertTrue(row.is_currently_listed)

    async def test_security_master_preserves_existing_dates_when_upstreams_fail(self):
        async with self.session_factory() as session:
            session.add(SecurityMaster(
                stock_code="000003", stock_name="PT金田A", exchange="SZ",
                list_date=date(1991, 7, 3), delist_date=date(2002, 6, 14),
                status="delisted", is_currently_listed=False,
                date_quality="status_event", source="existing",
            ))
            await session.commit()

        with (
            patch(
                "services.fqe_reference_data.collector.fetch_security_directory_snapshot",
                new=AsyncMock(side_effect=TimeoutError("directory offline")),
            ),
            patch(
                "services.fqe_reference_data.ftshare_mcp_client.get_full_stock_filter",
                new=AsyncMock(side_effect=TimeoutError("catalog offline")),
            ),
            patch.object(self.service, "_status_events", new=AsyncMock(return_value=[])),
        ):
            result = await self.service._sync_security_master()

        self.assertEqual(result["master_count"], 1)
        async with self.session_factory() as session:
            row = await session.get(SecurityMaster, "000003")
        self.assertEqual(row.list_date, date(1991, 7, 3))
        self.assertEqual(row.delist_date, date(2002, 6, 14))
        self.assertEqual(row.status, "delisted")
        self.assertFalse(row.is_currently_listed)

    async def test_status_event_failed_batch_splits_and_continues(self):
        calls: list[list[str]] = []

        async def fake_status_changes(codes):
            calls.append(list(codes))
            if len(codes) > 1:
                raise TimeoutError("batch timeout")
            return {"data": [{
                "trade_code": f"{codes[0]}.SH", "name": codes[0],
                "change_date": "2020-01-01", "change_type": "上市",
            }], "metadata": {"truncated": False}}

        with (
            patch(
                "services.fqe_reference_data.ftshare_mcp_client.get_stock_status_changes",
                new=fake_status_changes,
            ),
            patch("services.fqe_reference_data.asyncio.sleep", new=AsyncMock()),
        ):
            result = await self.service._status_events(["600000", "600001"])

        self.assertEqual({item["trade_code"] for item in result}, {"600000.SH", "600001.SH"})
        self.assertEqual(sum(1 for call in calls if len(call) == 2), 3)


if __name__ == "__main__":
    unittest.main()
