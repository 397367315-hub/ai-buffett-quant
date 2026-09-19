import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from services import wildman_classic_service as module
from services.wildman_classic_service import WildmanClassicService, fetch_numcat_history_batch


def test_a_share_scanner_never_requests_b_share_history():
    for code in ("200011", "201872", "900901"):
        assert module._market_exclusion(code, {}, exclude_star_market=False, exclude_gem=False) == "B股（非A股）过滤"
    assert module._market_exclusion("920071", {}, exclude_star_market=False, exclude_gem=False) is None


def provider_row(code, tradedate="20260918", close=10, qfq=True, volume=10):
    row = {
        "symbol": code,
        "tradedate": tradedate,
        "name": "测试",
        "open": close - 0.1,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "vol": volume,
        "amount": 20_000_000,
        "pct_chg": 1,
    }
    if qfq:
        row.update({"open_qfq": close - 0.1, "high_qfq": close + 0.2, "low_qfq": close - 0.2, "close_qfq": close})
    return row


@pytest.mark.asyncio
async def test_numcat_batch_uses_supported_params_normalizes_date_deduplicates_and_converts_volume():
    payload = {"data": {"fields": [], "items": [provider_row("600630", "20260918"), provider_row("600630", "2026-09-18", volume=11), provider_row("600630", "20260919")]}}
    gateway = AsyncMock()
    gateway.query.return_value = payload
    provider = type("Provider", (), {"configured": True})()
    with patch.object(module, "numcat_gateway", gateway), patch.object(module, "numcat_market_provider", provider):
        result = await fetch_numcat_history_batch(["600630"], days=100, end_date=date(2026, 9, 18))
    assert gateway.query.await_args.kwargs["params"] == {"symbols": "600630", "recentdays": 100}
    assert [row["date"] for row in result["600630"]] == ["2026-09-18"]
    assert result["600630"][0]["volume"] == 1100
    assert result["600630"][0]["pit_status"] == "future_rows_filtered"
    assert result["600630"][0]["adjustment_basis"] == "raw"


@pytest.mark.asyncio
async def test_numcat_batch_422_is_visible_to_caller():
    gateway = AsyncMock()
    gateway.query.side_effect = RuntimeError("BUSINESS422")
    provider = type("Provider", (), {"configured": True})()
    with patch.object(module, "numcat_gateway", gateway), patch.object(module, "numcat_market_provider", provider):
        with pytest.raises(RuntimeError, match="BUSINESS422"):
            await fetch_numcat_history_batch(["600630"], days=100)


@pytest.mark.asyncio
async def test_scan_returns_running_immediately_and_refresh_shares_job_key():
    service = WildmanClassicService()
    gate = asyncio.Event()
    async def slow_scan(*args, **kwargs):
        await gate.wait()
        return {"status": "completed", "rows": [], "progress": kwargs["progress"], "strategy": {}, "trade_date": "2026-09-18"}

    with patch.object(service, "_scan_uncached", new=slow_scan):
        first = await service.scan("WM_CLASSIC_520", date(2026, 9, 18), refresh=True)
        second = await service.scan("WM_CLASSIC_520", date(2026, 9, 18), refresh=False)
        assert first["status"] == "running"
        assert second["status"] == "running"
        assert len(service._jobs) == 1
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_numcat_history_is_chunked_at_32_and_db_fallback_is_explicit():
    service = WildmanClassicService()
    codes = [f"{index:06d}" for index in range(65)]
    provider = type("Provider", (), {"configured": True})()
    fetch = AsyncMock(return_value={})
    with patch.object(module, "numcat_market_provider", provider), patch.object(module, "fetch_numcat_history_batch", fetch), patch.object(service, "_load_db_bars", new=AsyncMock(return_value={})):
        result, metadata = await service._history_batch(codes, date(2026, 9, 18), refresh=False, minimum_bars=21)
    assert result == {}
    assert metadata["numcat_attempted"] is True
    assert fetch.await_count == 5
    assert all(len(call.args[0]) <= 16 for call in fetch.await_args_list)


@pytest.mark.asyncio
async def test_delayed_factors_use_entire_numcat_daily_series_without_mixing_prices():
    gateway = AsyncMock()
    gateway.query.side_effect = [
        {"data": {"items": [provider_row("600519", "20260917", close=8, qfq=True)]}},
        {"data": {"items": [provider_row("600519", "20260917", close=10, qfq=False), provider_row("600519", "20260918", close=11, qfq=False)]}},
    ]
    provider = type("Provider", (), {"configured": True})()
    with patch.object(module, "numcat_gateway", gateway), patch.object(module, "numcat_market_provider", provider):
        result = await fetch_numcat_history_batch(["600519"], end_date=date(2026, 9, 18))
    assert [bar["close"] for bar in result["600519"]] == [10, 11]
    assert all(bar["source"] == "numcat_daily" and bar["adjustment_basis"] == "raw" for bar in result["600519"])
    assert gateway.query.await_args.args[0] == "daily"


@pytest.mark.asyncio
async def test_numcat_session_date_precedes_stale_db_date():
    service = WildmanClassicService()
    provider = type("Provider", (), {
        "configured": True,
        "market_emotion": AsyncMock(return_value=[{"trade_date": "2026-09-18"}]),
    })()
    with patch.object(module, "numcat_market_provider", provider):
        assert await service._resolve_trade_date(date(2026, 9, 20)) == date(2026, 9, 18)


@pytest.mark.asyncio
async def test_suspension_filter_requires_same_day_full_session_without_resumption():
    from market_data.numcat.extended_provider import numcat_extended_provider
    provider = type("Provider", (), {"configured": True})()
    rows = [
        {"symbol": "000001", "tradedate": "20260918", "suspend_type": "S", "suspend_timing": None},
        {"symbol": "000002", "tradedate": "20260917", "suspend_type": "S"},
        {"symbol": "000003", "tradedate": "20260918", "suspend_type": "S", "suspend_timing": "10:06-10:16"},
        {"symbol": "000004", "tradedate": "20260918", "suspend_type": "S"},
        {"symbol": "000004", "tradedate": "20260918", "suspend_type": "R"},
    ]
    with patch.object(module, "numcat_market_provider", provider), patch.object(numcat_extended_provider, "suspend", new=AsyncMock(return_value=rows)):
        assert await WildmanClassicService()._suspensions(date(2026, 9, 18)) == {"000001"}


@pytest.mark.asyncio
async def test_historical_universe_keeps_later_delisted_and_excludes_not_yet_listed():
    provider = type("Provider", (), {
        "configured": True,
        "screening": AsyncMock(return_value=[]),
        "security_directory": AsyncMock(return_value=[
            {"code": "000001", "name": "历史标的", "list_date": "2000-01-01", "delist_date": "2026-08-01"},
            {"code": "000002", "name": "未上市", "list_date": "2026-07-01"},
        ]),
    })()
    with patch.object(module, "numcat_market_provider", provider):
        rows, metadata = await WildmanClassicService()._load_numcat_universe(date(2026, 6, 1))
    assert [row["code"] for row in rows] == ["000001"]
    assert metadata["historical_directory"] is True
