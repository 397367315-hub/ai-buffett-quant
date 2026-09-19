from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from market_data.level2.providers.base import Level2Page, ProviderCapabilities
from market_data.level2.repository import Level2Repository
from models import (
    Level2Feature1m, Level2FetchJob, Level2OrderHistory,
    Level2QualitySnapshot, Level2QuoteHistory, Level2TradeHistory,
)
from services.level2_service import Level2Service


class Provider:
    name = "numcat"
    configured = True
    capabilities = ProviderCapabilities(supports_history_trade=True, supports_history_order=True, supports_history_quote=True)

    def __init__(self, minutes=("09:31:00", "09:32:00"), fail_orders=False):
        self.minutes = minutes
        self.fail_orders = fail_orders

    async def fetch_page(self, kind, symbol, trade_date, **kwargs):
        if kind == "order" and self.fail_orders:
            raise RuntimeError("upstream unavailable")
        rows = []
        for index, minute in enumerate(self.minutes):
            row = {"symbol": symbol, "tradedate": trade_date.strftime("%Y%m%d"), "time": minute}
            if kind == "trade":
                row.update(trade_id=str(index), price=10 + index / 100, volume=100, bs_flag="B")
            elif kind == "order":
                row.update(order_id=str(index), price=10, volume=100, side="B", order_type="new")
            else:
                row.update(close=10, high=11, low=9)
                for level in range(1, 11):
                    row.update({f"bid{level}": 10 - level / 100, f"bid_vol{level}": 100,
                                f"ask{level}": 10 + level / 100, f"ask_vol{level}": 80})
            rows.append(row)
        return Level2Page(kind, [], rows, len(rows), None, False)


@pytest.mark.asyncio
async def test_sync_persists_only_features_and_replaces_old_minutes_atomically():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [model.__table__ for model in (Level2Feature1m, Level2FetchJob, Level2QualitySnapshot,
                                           Level2TradeHistory, Level2OrderHistory, Level2QuoteHistory)]
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    target = date(2026, 9, 18)
    try:
        with patch("market_data.level2.repository.async_session", sessions):
            provider = Provider()
            service = Level2Service(provider=provider, repository=Level2Repository())
            result = await service.sync("600621", target)
            assert result["status"] == "complete"
            assert result["feature_count"] == 2
            assert result["quality"]["checks"]["storage_mode"] == "features_only"
            async with sessions() as session:
                for model in (Level2TradeHistory, Level2OrderHistory, Level2QuoteHistory):
                    assert await session.scalar(select(func.count()).select_from(model)) == 0
                assert await session.scalar(select(func.count()).select_from(Level2Feature1m)) == 2
            provider.minutes = ("09:31:00",)
            provider.fail_orders = True
            refreshed = await service.sync("600621", target, force=True)
            assert refreshed["quality"]["pagination_complete"] is False
            assert refreshed["status"] != "complete"
            async with sessions() as session:
                features = list((await session.scalars(select(Level2Feature1m))).all())
                assert len(features) == 1
                assert features[0].minute.minute == 31
                assert features[0].confidence <= 55
                assert (await session.scalar(select(Level2QualitySnapshot))).checks["storage_mode"] == "features_only"
            with patch.object(provider, "fetch_page", AsyncMock(side_effect=RuntimeError("offline"))):
                failed = await service.sync("600621", target, force=True)
                assert failed["cache_preserved"] is True
                assert failed["status"] == "refresh_failed"
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(Level2Feature1m)) == 1
    finally:
        await engine.dispose()
