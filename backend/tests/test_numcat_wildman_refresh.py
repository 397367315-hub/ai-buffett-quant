from datetime import date
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

from market_data.numcat.market_provider import NumCatMarketProvider
from services.data_collector import EastMoneyDataCollector


class _CachingGateway:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.cache = {}
        self.calls = []

    async def query(self, api_name, *, params, fields, cache_ttl, bypass_cache, affinity_key):
        del fields, affinity_key
        self.calls.append({
            "api_name": api_name,
            "params": params,
            "cache_ttl": cache_ttl,
            "bypass_cache": bypass_cache,
        })
        key = (api_name, tuple(sorted(params.items())))
        if not bypass_cache and key in self.cache:
            return self.cache[key]
        payload = next(self.payloads)
        if cache_ttl:
            self.cache[key] = payload
        return payload


def _emotion_payload(tradedate):
    return {
        "data": {
            "fields": ["tradedate", "s2", "s6", "s10"],
            "items": [[tradedate, 1, 2, 3]],
        }
    }


def _pool_payload(tradedate):
    return {
        "data": {
            "fields": ["tradedate", "symbol", "name"],
            "items": [[tradedate, "600519", "贵州茅台"]],
        }
    }


@pytest.mark.asyncio
async def test_refresh_bypasses_cached_emotion_and_limit_pool_payloads():
    emotion_gateway = _CachingGateway([
        _emotion_payload("20260918"),
        _emotion_payload("20260922"),
    ])
    pool_gateway = _CachingGateway([
        _pool_payload("20260918"),
        _pool_payload("20260922"),
    ])
    provider = NumCatMarketProvider()

    with patch("market_data.numcat.market_provider.numcat_gateway", emotion_gateway):
        cached = await provider.market_emotion(recentdays=30)
        reused = await provider.market_emotion(recentdays=30)
        fresh = await provider.market_emotion(recentdays=30, refresh=True)
        after_refresh = await provider.market_emotion(recentdays=30)

    with patch("market_data.numcat.market_provider.numcat_gateway", pool_gateway):
        pool_cached = await provider.limit_pool("u", tradedate=date(2026, 9, 18))
        pool_reused = await provider.limit_pool("u", tradedate=date(2026, 9, 18))
        pool_fresh = await provider.limit_pool("u", tradedate=date(2026, 9, 18), refresh=True)
        pool_after_refresh = await provider.limit_pool("u", tradedate=date(2026, 9, 18))

    assert cached[0]["trade_date"] == reused[0]["trade_date"] == "2026-09-18"
    assert fresh[0]["trade_date"] == after_refresh[0]["trade_date"] == "2026-09-22"
    assert [call["cache_ttl"] for call in emotion_gateway.calls] == [60, 60, 60, 60]
    assert [call["bypass_cache"] for call in emotion_gateway.calls] == [False, False, True, False]

    assert pool_cached["trade_date"] == pool_reused["trade_date"] == "2026-09-18"
    assert pool_fresh["trade_date"] == pool_after_refresh["trade_date"] == "2026-09-22"
    assert [call["cache_ttl"] for call in pool_gateway.calls] == [900, 900, 900, 900]
    assert [call["bypass_cache"] for call in pool_gateway.calls] == [False, False, True, False]


@pytest.mark.asyncio
async def test_collector_forwards_refresh_and_preserves_numcat_vendor_date():
    collector = EastMoneyDataCollector()
    pool = {
        "stocks": [{"code": "600519"}],
        "total": 1,
        "trade_date": "2026-09-22",
        "source": "numcat_limit_pool",
    }
    with (
        patch.object(type(__import__(
            "services.data_collector", fromlist=["numcat_market_provider"]
        ).numcat_market_provider), "configured", new_callable=PropertyMock, return_value=True),
        patch("services.data_collector.numcat_market_provider.limit_pool", new=AsyncMock(return_value=pool)) as limit_pool,
        patch.object(collector, "fetch_json", new_callable=AsyncMock) as fetch_json,
    ):
        result = await collector.fetch_limit_up_pool(
            page_size=500, target_date=date(2026, 9, 18), refresh=True,
        )

    assert result["trade_date"] == "2026-09-22"
    limit_pool.assert_awaited_once_with(
        "u", tradedate=date(2026, 9, 18), refresh=True,
    )
    fetch_json.assert_not_awaited()
