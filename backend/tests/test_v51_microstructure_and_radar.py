from datetime import date, datetime, timedelta

import pytest

from quant.v51_microstructure import (
    candlestick_semantics,
    expectation_deviation,
    normalize_auction_snapshots,
)
from services.event_radar import EventRadarService


def test_auction_without_history_disables_model_explicitly():
    result = normalize_auction_snapshots([])

    assert result["quality"]["status"] == "AUCTION_DATA_UNAVAILABLE"
    assert result["quality"]["model_enabled"] is False
    assert result["features"] == []


def test_single_auction_snapshot_is_observable_but_not_a_forecast():
    result = normalize_auction_snapshots([
        {
            "snapshot_time": "2026-08-21T09:25:00",
            "indicative_price": 10.4,
            "previous_close": 10.0,
            "matched_volume": 12000,
            "source": "test",
        }
    ])

    assert result["quality"]["status"] == "LIMITED_SINGLE_SNAPSHOT"
    assert result["quality"]["model_enabled"] is False
    assert result["auction_state"] == "WAIT_FOR_CONFIRMATION"


def test_expectation_windows_use_0925_as_the_actual_time_origin():
    auction = normalize_auction_snapshots([
        {
            "snapshot_time": "2026-08-21T09:25:00",
            "indicative_price": 10.2,
            "previous_close": 10.0,
            "matched_volume": 1000,
            "source": "test",
        },
        {
            "snapshot_time": "2026-08-21T09:24:00",
            "indicative_price": 10.1,
            "previous_close": 10.0,
            "matched_volume": 900,
            "source": "test",
        },
    ])
    bars = [
        {"bar_time": "2026-08-21T09:30:00", "close_price": 10.3},
        {"bar_time": "2026-08-21T09:40:00", "close_price": 10.4},
        {"bar_time": "2026-08-21T09:55:00", "close_price": 10.5},
    ]

    result = expectation_deviation(auction, bars, previous_close=10.0)

    assert result["windows"]["5m"]["observed_at"].endswith("09:30:00")
    assert result["windows"]["15m"]["observed_at"].endswith("09:40:00")
    assert result["windows"]["30m"]["observed_at"].endswith("09:55:00")


def test_candlestick_engine_returns_atomic_geometry_only():
    rows = []
    for index in range(3):
        rows.append({
            "trade_date": date(2026, 8, 19) + timedelta(days=index),
            "open_price": 10.0,
            "close_price": 10.6,
            "high_price": 10.7,
            "low_price": 9.9,
        })

    result = candlestick_semantics(rows)

    assert result["semantic_state"] == "DIRECTIONAL_BODY_ATOM"
    assert result["quality"]["confirmation_required"] is True
    assert "buy_signal" not in result
    assert "sell_signal" not in result
    assert "action" not in result


@pytest.mark.asyncio
async def test_radar_deduplicates_headlines_across_providers_and_caps_rumors():
    service = EventRadarService()
    events = await service._normalize([
        {
            "provider": "cls_http",
            "source": "财联社公开电报",
            "source_kind": "cls",
            "title": "某行业传闻将获得重大支持",
            "published_at": "2026-08-22T09:00:00",
        },
        {
            "provider": "another_provider",
            "source": "其他公开源",
            "source_kind": "mainstream_finance",
            "title": "某行业传闻将获得重大支持",
            "published_at": "2026-08-22T09:01:00",
        },
    ])

    assert len(events) == 1
    assert events[0]["event_score"] <= 59
    assert events[0]["alert_level"] == "C"


@pytest.mark.asyncio
async def test_radar_provider_failure_is_recorded_as_degraded():
    service = EventRadarService()

    async def failing_provider():
        raise TimeoutError("provider timeout")

    assert await service._provider_call("test_provider", failing_provider()) == []
    assert service._memory_health["test_provider"]["status"] == "FAILED"
    assert service._memory_health["test_provider"]["error_count"] == 1
