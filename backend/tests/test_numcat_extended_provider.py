import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from config import settings
from market_data.numcat.extended_provider import (
    DOCUMENTED_APINAMES,
    MISSING_TYPED_APINAMES,
    TYPED_PROVIDER_APINAMES,
    NumCatExtendedProvider,
    _clean_params,
)
from market_data.numcat.gateway import NumCatGateway


def test_official_catalog_has_all_73_allowlisted_apinames():
    provider = NumCatExtendedProvider()
    catalog = provider.catalog()

    assert len(DOCUMENTED_APINAMES) == 73
    assert len(catalog) == 73
    assert {item["apiname"] for item in catalog} == DOCUMENTED_APINAMES
    assert TYPED_PROVIDER_APINAMES | MISSING_TYPED_APINAMES == DOCUMENTED_APINAMES
    assert all(item["generic_query"] for item in catalog)
    assert {"tick", "finance_indicator", "level2_trade_history", "theme_reason"} <= DOCUMENTED_APINAMES


@pytest.mark.asyncio
async def test_generic_query_rejects_unknown_endpoint_before_gateway_call():
    provider = NumCatExtendedProvider()
    with patch(
        "market_data.numcat.extended_provider.numcat_gateway.query",
        new=AsyncMock(),
    ) as query:
        with pytest.raises(ValueError, match="不支持的猫爪接口"):
            await provider.query("arbitrary_url")
    query.assert_not_awaited()


def test_generic_params_are_bounded_and_only_accept_simple_json_values():
    assert len(_clean_params({"symbols": [f"{value:06d}" for value in range(260)]})["symbols"]) == 200
    with pytest.raises(ValueError, match="简单JSON值"):
        _clean_params({"nested": {"url": "https://example.invalid"}})
    with pytest.raises(ValueError, match="48KB"):
        _clean_params({f"field_{index}": "x" * 2000 for index in range(40)})


@pytest.mark.asyncio
async def test_realtime_query_uses_short_default_cache_and_never_persists_raw_response():
    provider = NumCatExtendedProvider()
    payload = {
        "code": 200,
        "data": {"fields": ["symbol", "close"], "items": [["600519", 1500.0]]},
    }
    with patch(
        "market_data.numcat.extended_provider.numcat_gateway.query",
        new=AsyncMock(return_value=payload),
    ) as query:
        result = await provider.query("tick", params={"symbols": ["600519"]})

    assert query.await_args.kwargs["cache_ttl"] == 30
    assert result["persistent_raw_storage"] is False
    assert result["cache_policy"] == "memory_only_bounded"
    assert result["row_count"] == 1


@pytest.mark.asyncio
async def test_finance_wrapper_forces_latest_contract():
    provider = NumCatExtendedProvider()
    with patch.object(provider, "rows", new=AsyncMock(return_value=[])) as rows:
        await provider.finance(
            "finance_income_statement",
            {"symbols": ["600519"], "version": "legacy"},
        )

    assert rows.await_args.kwargs["params"]["version"] == "latest"
    # The wrapper always uses the latest disclosed revision so downstream
    # PIT consumers cannot silently mix old and new financial revisions.
    with patch.object(provider, "rows", new=AsyncMock(return_value=[])) as rows:
        await provider.finance("finance_indicator", {"symbols": ["600519"]})
    assert rows.await_args.kwargs["params"]["version"] == "latest"


@pytest.mark.asyncio
async def test_auction_enrichment_scopes_by_date_then_filters_symbols_locally():
    provider = NumCatExtendedProvider()
    calls = []

    async def rows(apiname, **kwargs):
        calls.append((apiname, kwargs.get("params")))
        if apiname == "auc_kp":
            return [{"symbol": "600519", "ztwme": 100}, {"symbol": "000001", "ztwme": 50}]
        return [{"symbol": "600519", "fd_amount": 10}, {"symbol": "000001", "fd_amount": 5}]

    with patch.object(provider, "rows", new=AsyncMock(side_effect=rows)):
        limit_buy = await provider.auction_limit_buy(["600519"], tradedate=date(2026, 9, 2))
        one_price = await provider.auction_one_price(["000001"], tradedate=date(2026, 9, 2))

    assert calls == [
        ("auc_kp", {"tradedate": "20260902"}),
        ("daily_auc_fd", {"tradedate": "20260902"}),
    ]
    assert [row["symbol"] for row in limit_buy] == ["600519"]
    assert [row["symbol"] for row in one_price] == ["000001"]


@pytest.mark.asyncio
async def test_research_bundle_returns_successful_sections_and_explicit_failures():
    provider = NumCatExtendedProvider()

    async def rows(apiname, **_kwargs):
        if apiname == "suspend":
            raise RuntimeError("upstream unavailable")
        return [{"apiname": apiname, "symbol": "600519"}]

    with (
        patch.object(provider, "rows", new=AsyncMock(side_effect=rows)),
        patch.object(provider, "calendar", new=AsyncMock(return_value=[{"tradedate": "20260903"}])),
        patch.object(provider, "tick_snapshot", new=AsyncMock(return_value=[{"symbol": "600519"}])),
        patch.object(provider, "last_tick", new=AsyncMock(return_value=[])),
        patch.object(provider, "auction_limit_buy", new=AsyncMock(return_value=[])),
        patch.object(provider, "auction_one_price", new=AsyncMock(return_value=[])),
        patch.object(provider, "price_limit", new=AsyncMock(return_value=[])),
        patch.object(provider, "st", new=AsyncMock(return_value=[])),
        patch.object(provider, "suspend", new=AsyncMock(side_effect=RuntimeError("upstream unavailable"))),
        patch.object(provider, "limit_event_history", new=AsyncMock(return_value=[])),
        patch.object(provider, "finance", new=AsyncMock(return_value=[])),
    ):
        result = await provider.research_bundle(["600519.SH"], tradedate=date(2026, 9, 3))

    assert result["symbols"] == ["600519"]
    assert result["available"] is True
    assert result["partial"] is True
    assert result["sections"]["tick"]["available"] is True
    assert result["sections"]["suspend"]["available"] is False
    assert result["persistent_raw_storage"] is False


@pytest.mark.asyncio
async def test_gateway_does_not_cache_oversized_payload_and_stays_within_byte_limit():
    gateway = NumCatGateway()
    gateway._request = AsyncMock(return_value={"code": 200, "data": {"blob": "x" * 70000}})
    with (
        patch.object(settings, "meoz_enabled", True),
        patch.object(settings, "meoz_api_key", "server-only-key"),
        patch.object(settings, "numcat_cache_max_payload_bytes", 64 * 1024),
        patch.object(settings, "numcat_cache_max_bytes", 64 * 1024),
    ):
        await gateway.query("news", cache_ttl=60)

    assert gateway.status()["cache_entries"] == 0
    assert gateway.status()["cache_bytes"] == 0
    assert gateway.status()["usage"]["cache_skipped_oversize"] == 1


@pytest.mark.asyncio
async def test_gateway_evicts_entries_to_respect_total_byte_limit():
    gateway = NumCatGateway()

    async def request(api_name, *_args, **_kwargs):
        return {"code": 200, "data": {"api": api_name, "blob": "x" * 30000}}

    gateway._request = AsyncMock(side_effect=request)
    with (
        patch.object(settings, "meoz_enabled", True),
        patch.object(settings, "meoz_api_key", "server-only-key"),
        patch.object(settings, "numcat_cache_max_payload_bytes", 64 * 1024),
        patch.object(settings, "numcat_cache_max_bytes", 64 * 1024),
    ):
        for index in range(5):
            await gateway.query(f"test-{index}", cache_ttl=60)
            await asyncio.sleep(0)

    assert 0 < gateway.status()["cache_bytes"] <= 64 * 1024
    assert gateway.status()["cache_entries"] < 5
