import os
import tempfile
from datetime import datetime
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from main import app
from models import SchedulerTaskLedger
from services import scheduler as scheduler_module
from services.pit_market_data import AUCTION_REQUIRED_TIMEPOINTS, auction_quality


async def _temporary_ledger():
    fd, path = tempfile.mkstemp(prefix="scheduler-p0-ledger-", suffix=".db")
    os.close(fd)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(SchedulerTaskLedger.__table__.create)
    return engine, async_sessionmaker(engine, expire_on_commit=False), path


def test_auction_contract_requires_every_0915_to_0925_point():
    assert AUCTION_REQUIRED_TIMEPOINTS == tuple(
        f"09:{minute:02d}" for minute in range(15, 26)
    )
    result = auction_quality(
        universe_count=2000,
        observed_stocks=2000,
        timeline_stocks=2000,
        timepoints=["09:15", "09:16", "09:17"],
    )
    assert result["status"] == "INSUFFICIENT"
    assert result["execution_allowed"] is False
    assert "09:25" in result["missing_time_points"]


def test_scheduler_has_operational_status_route():
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/health/scheduler" in paths


@pytest.mark.asyncio
async def test_startup_audit_does_not_use_yesterdays_success():
    scheduler_module._job_state.clear()
    scheduler_module._startup_audit_state.clear()
    scheduler_module._startup_compensation_state.clear()
    scheduler_module._job_state["midday_ai_research"] = {
        "last_success_at": datetime(2026, 9, 9, 11, 42, tzinfo=ZoneInfo("Asia/Shanghai")),
    }

    engine, ledger_session, ledger_path = await _temporary_ledger()
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(scheduler_module, "async_session", ledger_session))
            mocked_handlers = {
                name: stack.enter_context(
                    patch.object(
                        scheduler_module,
                        name,
                        new_callable=AsyncMock,
                        return_value={"mocked": name},
                    )
                )
                for name in {spec["handler"] for spec in scheduler_module._CRITICAL_TASKS}
            }
            result = await scheduler_module.audit_missed_critical_jobs(
                datetime(2026, 9, 10, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
            )
    finally:
        await engine.dispose()
        os.unlink(ledger_path)

    item = next(item for item in result["items"] if item["job_id"] == "midday_ai_research")
    assert item["status"] == "COMPENSATED"
    mocked_handlers["run_midday_research"].assert_awaited_once()
    mocked_handlers["run_market_data_collection"].assert_awaited_once()
