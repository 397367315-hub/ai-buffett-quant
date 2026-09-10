from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base
from models import ForecastSnapshotV5, StockDailyBar, StockMinuteBar
from services import data_retention as retention_module


@pytest.mark.asyncio
async def test_retention_prunes_only_expired_derived_rows(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retention.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(retention_module, "async_session", session_factory)

    now = datetime(2026, 9, 10, 3, 20)
    async with session_factory() as session:
        session.add_all([
            StockMinuteBar(
                stock_code="600000", stock_name="浦发银行", bar_time=now - timedelta(days=121),
                interval_minutes=1, source="test",
            ),
            StockMinuteBar(
                stock_code="600001", stock_name="测试新数据", bar_time=now - timedelta(days=119),
                interval_minutes=1, source="test",
            ),
            ForecastSnapshotV5(
                forecast_date=(now - timedelta(days=731)).date(), phase="close", forecast_version="test",
                model_version="test", data_cutoff_time=now - timedelta(days=731), data_completeness_pct=100,
                confidence_ceiling_pct=0, payload={}, generated_at=now - timedelta(days=731),
            ),
            StockDailyBar(
                stock_code="600000", stock_name="浦发银行", trade_date=(now - timedelta(days=900)).date(),
                close_price=10, source="test",
            ),
        ])
        await session.commit()

    result = await retention_module.data_retention_service.run(now=now)
    assert result["deleted_rows"] == 2

    async with session_factory() as session:
        minute_codes = list((await session.execute(select(StockMinuteBar.stock_code))).scalars())
        daily_codes = list((await session.execute(select(StockDailyBar.stock_code))).scalars())
        forecasts = list((await session.execute(select(ForecastSnapshotV5.id))).scalars())
    assert minute_codes == ["600001"]
    assert daily_codes == ["600000"]
    assert forecasts == []

    status = await retention_module.data_retention_service.status()
    assert status["last_run"]["deleted_rows"] == 2
    assert "stock_daily_bars" in status["protected_datasets"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_retention_dry_run_does_not_delete(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retention-dry.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(retention_module, "async_session", session_factory)

    now = datetime(2026, 9, 10, 3, 20)
    async with session_factory() as session:
        session.add(StockMinuteBar(
            stock_code="600000", bar_time=now - timedelta(days=121), interval_minutes=1, source="test",
        ))
        await session.commit()

    result = await retention_module.data_retention_service.run(dry_run=True, now=now)
    assert result["status"] == "DRY_RUN"
    assert result["matched_rows"] == 1
    assert result["deleted_rows"] == 0
    async with session_factory() as session:
        assert await session.scalar(select(StockMinuteBar.id)) is not None
    await engine.dispose()
