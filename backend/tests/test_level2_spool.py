from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from market_data.level2.models import BookLevel, OrderBookSnapshot, OrderTick, TradeTick
from market_data.level2.spool import Level2Spool, Level2SpoolCapacityError


TARGET = date(2026, 8, 29)


def _trade(minute: int, *, trade_id: str | None = None, amount: float | None = 100.0, raw=None, price=10.0, volume=10.0):
    return TradeTick(
        "600519", TARGET, datetime(2026, 8, 29, 9, minute, 0),
        trade_id=trade_id, price=price, volume=volume, amount=amount, raw=raw or {"vendor": "discard"},
    )


def _quote(minute: int, second: int = 0):
    return OrderBookSnapshot(
        "600519", TARGET, datetime(2026, 8, 29, 9, minute, second),
        bids=[BookLevel(9.9, 100, 1), BookLevel(9.8, 90, 2)],
        asks=[BookLevel(10.1, 80, 1)],
    )


@pytest.mark.asyncio
async def test_spool_is_disposable_and_forwards_only_job_metadata():
    delegate = SimpleNamespace(save_job=AsyncMock())
    with Level2Spool(delegate, "600519", TARGET) as spool:
        db_path = spool.db_path
        assert await spool.get_job("600519", TARGET, "trade") is None
        values = {"symbol": "600519", "trade_date": TARGET, "data_type": "trade", "status": "running", "rows": 1}
        await spool.save_job(values)
        assert delegate.save_job.await_args.args[0] == values
        assert await spool.save_trades([_trade(31, trade_id="t1")]) == 1
        assert spool.stats()["trade_count"] == 1
    assert not db_path.exists()
    assert not db_path.parent.exists()


@pytest.mark.asyncio
async def test_dedup_preserves_repository_source_id_semantics_and_discards_raw():
    delegate = SimpleNamespace(save_job=AsyncMock(), save_trades=AsyncMock())
    with Level2Spool(delegate, "600519", TARGET) as spool:
        row = _trade(31, trade_id="same", raw={"secret": "must-not-persist"})
        assert await spool.save_trades([row, row]) == 1
        stored = list(spool.iter_minutes())[0][1]
        assert stored[0].trade_id == "same"
        assert stored[0].raw == {}
        assert "raw" not in spool._connection.execute("SELECT payload_json FROM records").fetchone()[0]
        assert "secret" not in spool._connection.execute("SELECT payload_json FROM records").fetchone()[0]
        delegate.save_trades.assert_not_awaited()


@pytest.mark.asyncio
async def test_amount_threshold_is_sql_percentile_with_fallback_amounts():
    delegate = SimpleNamespace(save_job=AsyncMock())
    amounts = [1, 2, 3, 4, 5, 6, 7, 8, 9, 100]
    with Level2Spool(delegate, "600519", TARGET) as spool:
        rows = [_trade(31 + (index % 2), trade_id=str(index), amount=amount, price=amount, volume=1) for index, amount in enumerate(amounts)]
        rows.append(_trade(31, trade_id="fallback", amount=None, price=20, volume=1))
        assert await spool.save_trades(rows) == 11
        # 85th percentile of [1..9, 20, 100] is position 8.5 -> 14.5.
        assert spool.amount_threshold() == pytest.approx(14.5)


@pytest.mark.asyncio
async def test_iter_minutes_is_sorted_and_stats_include_quote_depth():
    delegate = SimpleNamespace(save_job=AsyncMock())
    with Level2Spool(delegate, "600519", TARGET) as spool:
        await spool.save_trades([_trade(32, trade_id="late"), _trade(31, trade_id="early")])
        await spool.save_orders([OrderTick("600519", TARGET, datetime(2026, 8, 29, 9, 31, 1), order_id="o1")])
        await spool.save_quotes([_quote(31)])
        minutes = list(spool.iter_minutes())
        assert [minute for minute, *_ in minutes] == [datetime(2026, 8, 29, 9, 31), datetime(2026, 8, 29, 9, 32)]
        assert [row.trade_id for row in minutes[0][1]] == ["early"]
        stats = spool.stats()
        assert stats["trade_count"] == 2
        assert stats["order_count"] == 1
        assert stats["quote_count"] == 1
        assert stats["quote_depth_observations"] == 3
        assert stats["full_depth_quote_count"] == 0
        assert stats["trade_date_consistent"] is True


@pytest.mark.asyncio
async def test_wrong_symbol_or_date_is_rejected_and_exception_cleans_up():
    delegate = SimpleNamespace(save_job=AsyncMock())
    spool = Level2Spool(delegate, "600519", TARGET)
    db_path = spool.db_path
    with pytest.raises(RuntimeError, match="boom"):
        with spool:
            assert await spool.save_trades([
                _trade(31, trade_id="good"),
                TradeTick("000001", TARGET, datetime(2026, 8, 29, 9, 31), trade_id="wrong-symbol"),
                TradeTick("600519", TARGET + timedelta(days=1), datetime(2026, 8, 30, 9, 31), trade_id="wrong-date"),
            ]) == 1
            assert spool.rejected_rows == 2
            raise RuntimeError("boom")
    assert not db_path.parent.exists()


@pytest.mark.asyncio
async def test_minute_cap_is_visible_as_partial_input():
    delegate = SimpleNamespace(save_job=AsyncMock())
    with Level2Spool(delegate, "600519", TARGET) as spool:
        rows = [_trade(31, trade_id=f"t-{index}") for index in range(spool.ROWS_PER_KIND_PER_MINUTE + 1)]
        assert await spool.save_trades(rows) == len(rows)
        assert spool.truncated_minutes == 1
        assert spool.truncated_minute_codes == ["2026-08-29T09:31:00"]
        minute_rows = list(spool.iter_minutes())[0][1]
        assert len(minute_rows) == spool.ROWS_PER_KIND_PER_MINUTE


@pytest.mark.asyncio
async def test_disk_cap_raises_explicitly_and_context_cleans_up(monkeypatch):
    delegate = SimpleNamespace(save_job=AsyncMock())
    monkeypatch.setattr(Level2Spool, "MAX_BYTES", 64 * 1024)
    spool = Level2Spool(delegate, "600519", TARGET)
    db_path = spool.db_path
    oversized = _trade(31, trade_id="large")
    oversized.trade_code = "x" * 200_000
    with pytest.raises(Level2SpoolCapacityError, match="disk cap"):
        with spool:
            await spool.save_trades([oversized])
    assert not db_path.parent.exists()


@pytest.mark.asyncio
async def test_aware_timestamps_are_serialized_in_asia_shanghai_time():
    delegate = SimpleNamespace(save_job=AsyncMock())
    row = TradeTick(
        "600519", TARGET, datetime(2026, 8, 28, 16, 0, tzinfo=timezone.utc),
        trade_id="tz", amount=1,
    )
    with Level2Spool(delegate, "600519", TARGET) as spool:
        assert await spool.save_trades([row]) == 1
        minute, trades, _, _ = next(spool.iter_minutes())
        assert minute == datetime(2026, 8, 29, 0, 0)
        assert trades[0].timestamp == datetime(2026, 8, 29, 0, 0)
