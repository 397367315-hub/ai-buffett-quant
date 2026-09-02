import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from config import settings
from market_data.numcat.gateway import NumCatGateway, NumCatGatewayError
from market_data.numcat.market_provider import NumCatMarketProvider


class _Client:
    def __init__(self, handler):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, **kwargs):
        return await self._handler(url, kwargs)


def _factory(handler):
    return lambda **_kwargs: _Client(handler)


async def _no_sleep(_delay):
    return None


def _settings_context(**overrides):
    defaults = {
        "level2_enabled": True,
        "meoz_enabled": True,
        "meoz_api_key": "server-only-test-key",
        "numcat_api_key": "",
        "meoz_api_route": "dedicated",
        "numcat_route": "dedicated",
        "numcat_sz_base_url": "http://sz.numcat.test/api",
        "numcat_sh_base_url": "http://sh.numcat.test/api",
        "numcat_public_base_url": "https://public.numcat.test/api",
        "numcat_api_base": "https://public.numcat.test/api",
        "numcat_allow_public_fallback": False,
        "numcat_retry_count": 1,
    }
    defaults.update(overrides)
    stack = []
    for key, value in defaults.items():
        item = patch.object(settings, key, value)
        item.start()
        stack.append(item)

    class _Context:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            for item in reversed(stack):
                item.stop()

    return _Context()


@pytest.mark.asyncio
async def test_gateway_cache_and_singleflight_request_only_once():
    gateway = NumCatGateway()
    gate = asyncio.Event()

    async def request(*_args, **_kwargs):
        await gate.wait()
        return {"code": 200, "data": {"fields": [], "items": []}}

    gateway._request = AsyncMock(side_effect=request)
    with _settings_context():
        first = asyncio.create_task(gateway.query("daily", params={"symbols": "600519"}, cache_ttl=60))
        second = asyncio.create_task(gateway.query("daily", params={"symbols": "600519"}, cache_ttl=60))
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(first, second)
        await gateway.query("daily", params={"symbols": "600519"}, cache_ttl=60)

    assert gateway._request.await_count == 1
    assert gateway.status()["usage"]["cache_hits"] >= 1


@pytest.mark.asyncio
async def test_connection_failure_switches_dedicated_line_but_not_public():
    urls = []

    async def handler(url, _kwargs):
        urls.append(url)
        if "sz." in url:
            raise httpx.ConnectError("offline", request=httpx.Request("POST", url))
        return httpx.Response(200, json={"code": 200, "data": {"fields": [], "items": []}}, request=httpx.Request("POST", url))

    gateway = NumCatGateway(client_factory=_factory(handler), sleep=_no_sleep)
    with _settings_context():
        await gateway.query("daily", market="sz", bypass_cache=True)

    assert urls == ["http://sz.numcat.test/api", "http://sh.numcat.test/api"]
    assert all("public" not in url for url in urls)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403])
async def test_client_error_does_not_switch_dedicated_line(status):
    urls = []

    async def handler(url, _kwargs):
        urls.append(url)
        return httpx.Response(status, json={"code": status}, request=httpx.Request("POST", url))

    gateway = NumCatGateway(client_factory=_factory(handler), sleep=_no_sleep)
    with _settings_context():
        with pytest.raises(NumCatGatewayError):
            await gateway.query("daily", market="sz", bypass_cache=True)

    assert urls == ["http://sz.numcat.test/api"]


@pytest.mark.asyncio
async def test_429_retries_same_route_without_switching():
    urls = []

    async def handler(url, _kwargs):
        urls.append(url)
        return httpx.Response(429, headers={"Retry-After": "0.1"}, json={"code": 429}, request=httpx.Request("POST", url))

    gateway = NumCatGateway(client_factory=_factory(handler), sleep=_no_sleep)
    with _settings_context(numcat_retry_count=2):
        with pytest.raises(NumCatGatewayError):
            await gateway.query("daily", market="sz", bypass_cache=True)

    assert urls == ["http://sz.numcat.test/api", "http://sz.numcat.test/api"]


def test_public_route_is_only_added_when_explicitly_enabled():
    gateway = NumCatGateway()
    with _settings_context(numcat_allow_public_fallback=False):
        assert all(name != "public" for name, _ in gateway._routes(None, "task"))
    with _settings_context(numcat_allow_public_fallback=True):
        assert gateway._routes(None, "task")[-1] == ("public", "https://public.numcat.test/api")
    with _settings_context(meoz_api_route="public"):
        assert gateway._routes(None, "task") == [("public", "https://public.numcat.test/api")]


@pytest.mark.asyncio
async def test_provider_maps_documented_two_dimensional_fields_and_units():
    provider = NumCatMarketProvider()

    async def query(api_name, **_kwargs):
        if api_name == "stk_factor_pro":
            return {
                "code": 200,
                "data": {
                    "fields": ["tradedate", "symbol", "name", "close", "vol", "amount", "turnover_rate_f"],
                    "items": [["20260829", "600519", "贵州茅台", 1500, 12, 1800000, 1.8]],
                },
            }
        if api_name == "screening":
            return {
                "code": 200,
                "data": {
                    "fields": ["tradedate", "symbol", "name", "close", "vol", "amount", "volume_ratio", "total_mv"],
                    "items": [["20260829", "600519", "贵州茅台", 1500, 12, 1800000, 1.5, 1900000000000]],
                },
            }
        if api_name == "stockbasic":
            return {
                "code": 200,
                "data": {
                    "fields": ["symbol", "name", "industry", "market", "exchange", "list_status", "list_date"],
                    "items": [["600519", "贵州茅台", "食品饮料", "主板", "SSE", "L", "20010827"]],
                },
            }
        if api_name == "valuation":
            return {
                "code": 200,
                "data": {
                    "fields": ["tradedate", "symbol", "name", "pe_ttm", "pb", "total_mv"],
                    "items": [["20260829", "600519", "贵州茅台", 22.5, 7.2, 1900000000000]],
                },
            }
        raise AssertionError(api_name)

    with patch("market_data.numcat.market_provider.numcat_gateway.query", new=AsyncMock(side_effect=query)):
        daily = await provider.daily("600519")
        screening = await provider.screening(symbols=["600519"], tradedate=None)

    assert daily[0]["trade_date"] == "2026-08-29"
    assert daily[0]["volume"] == 1200
    assert daily[0]["turnover"] == 1.8
    assert screening[0]["volume"] == 1200
    assert screening[0]["sector"] == "食品饮料"
    assert screening[0]["pe"] == 22.5
    assert screening[0]["pb"] == 7.2
    assert screening[0]["quote_source"] == "numcat"


def test_screening_contract_does_not_request_fields_owned_by_other_endpoints():
    from market_data.numcat.market_provider import SCREENING_FIELDS

    fields = set(SCREENING_FIELDS.split(","))
    assert {"volume_ratio", "free_float_mv", "theme_names_kpl"} <= fields
    assert fields.isdisjoint({"pe_ttm", "pb", "roe", "industry", "mf_main", "mf_main_pct"})


@pytest.mark.asyncio
async def test_security_directory_requests_all_documented_listing_states():
    provider = NumCatMarketProvider()
    statuses = []

    async def query(api_name, **kwargs):
        assert api_name == "stockbasic"
        status = kwargs["params"]["list_status"]
        statuses.append(status)
        symbol = {"L": "600519", "D": "000003", "P": "000005"}[status]
        return {
            "code": 200,
            "data": {
                "fields": ["symbol", "name", "list_status"],
                "items": [[symbol, status, status]],
            },
        }

    with patch("market_data.numcat.market_provider.numcat_gateway.query", new=AsyncMock(side_effect=query)):
        rows = await provider.security_directory()

    assert set(statuses) == {"L", "D", "P"}
    assert {item["list_status"] for item in rows} == {"L", "D", "P"}


@pytest.mark.asyncio
async def test_minute_normalizes_trade_date_without_changing_documented_volume_unit():
    provider = NumCatMarketProvider()
    payload = {
        "code": 200,
        "data": {
            "fields": ["tradedate", "symbol", "trademin", "time", "vol"],
            "items": [["20260829", "600519.SH", "0935", "09:35:00", 12]],
        },
    }
    with patch(
        "market_data.numcat.market_provider.numcat_gateway.query",
        new=AsyncMock(return_value=payload),
    ):
        rows = await provider.minute("600519")

    assert rows[0]["tradedate"] == "2026-08-29"
    assert rows[0]["vol"] == 12


@pytest.mark.asyncio
async def test_provider_maps_market_emotion_contract_without_losing_zero_values():
    provider = NumCatMarketProvider()
    payload = {
        "code": 200,
        "data": {
            "fields": ["tradedate", "s2", "s6", "s10", "am", "mf_main", "u5", "u12", "d3", "fp108", "l17", "l22"],
            "items": [["20260829", 2876, 2015, 173, 1512384567890, -32488641390, 71, 21, 6, 22.83, 8, 70.59]],
        },
    }
    with patch(
        "market_data.numcat.market_provider.numcat_gateway.query",
        new=AsyncMock(return_value=payload),
    ):
        rows = await provider.market_emotion(recentdays=5)

    assert len(rows) == 1
    assert {key: rows[0][key] for key in (
        "trade_date", "up_count", "down_count", "flat_count", "stock_count",
        "market_amount", "main_net_inflow", "limit_up_count", "failed_limit_count",
        "failed_limit_rate", "limit_down_count", "max_streak_height", "promotion_rate",
        "source",
    )} == {
        "trade_date": "2026-08-29",
        "up_count": 2876,
        "down_count": 2015,
        "flat_count": 173,
        "stock_count": 5064,
        "market_amount": 1512384567890,
        "main_net_inflow": -32488641390,
        "limit_up_count": 71,
        "failed_limit_count": 21,
        "failed_limit_rate": 22.83,
        "limit_down_count": 6,
        "max_streak_height": 8,
        "promotion_rate": 70.59,
        "source": "numcat_emoindic_daily",
    }


@pytest.mark.asyncio
async def test_provider_maps_limit_pool_and_dragon_board_with_seat_evidence():
    provider = NumCatMarketProvider()

    async def query(api_name, **_kwargs):
        if api_name == "limit_pool":
            return {
                "code": 200,
                "data": {
                    "fields": ["tradedate", "symbol", "name", "type", "limit_times", "fd_amount", "close", "pct_chg"],
                    "items": [["20260829", "000001", "平安银行", "u", 2, 1000000, 12.3, 10]],
                },
            }
        if api_name == "longhubang_stock":
            return {
                "code": 200,
                "data": {
                    "fields": ["tradedate", "symbol", "name", "close", "pct_chg", "turnover_rate", "lhb_buy", "lhb_sell", "lhb_amount", "net_amount", "float_value", "reason"],
                    "items": [["20260829", "000001", "平安银行", 12.3, 10, 8.2, 5000, 2000, 7000, 3000, 900000000, "日涨幅偏离值达7%"]],
                },
            }
        if api_name == "longhubang_seat":
            return {
                "code": 200,
                "data": {
                    "fields": ["tradedate", "symbol", "reason", "seat_name", "buy", "sell", "net_buy"],
                    "items": [["20260829", "000001", "日涨幅偏离值达7%", "机构专用", 4000, 1000, 3000]],
                },
            }
        raise AssertionError(api_name)

    with patch(
        "market_data.numcat.market_provider.numcat_gateway.query",
        new=AsyncMock(side_effect=query),
    ):
        pool = await provider.limit_pool("u", tradedate=date(2026, 8, 29))
        board = await provider.dragon_board(tradedate=date(2026, 8, 29))

    assert pool["stocks"][0]["seal_amount"] == 1000000
    assert pool["stocks"][0]["continuous_days"] == 2
    assert board[0]["institution_count"] == 1
    assert board[0]["institution_net_amount"] == 3000
    assert board[0]["source"] == "numcat_longhubang"


@pytest.mark.asyncio
async def test_provider_maps_margin_and_auction_detail_contracts():
    provider = NumCatMarketProvider()

    async def query(api_name, **_kwargs):
        if api_name == "margin_summary":
            return {"code": 200, "data": {"fields": ["tradedate", "exchange", "financing_balance"], "items": [["20260828", "SSE", 1000]]}}
        if api_name == "margin_detail":
            return {"code": 200, "data": {"fields": ["tradedate", "symbol", "name", "exchange", "financing_balance", "financing_buy_amount", "financing_repayment_amount"], "items": [["20260828", "600519", "贵州茅台", "SSE", 800, 120, 100]]}}
        if api_name == "daily_auc_detail":
            return {"code": 200, "data": {"fields": ["tradedate", "symbol", "time", "m_price", "auc_vol", "um_vol"], "items": [["20260829", "600519", "09:25:00", 1500, 1200, 50]]}}
        raise AssertionError(api_name)

    with patch(
        "market_data.numcat.market_provider.numcat_gateway.query",
        new=AsyncMock(side_effect=query),
    ):
        summary = await provider.margin_summary(tradedate=date(2026, 8, 28))
        detail = await provider.margin_detail(["600519"], tradedate=date(2026, 8, 28))
        auction = await provider.auction_detail_snapshot(["600519"], tradedate=date(2026, 8, 29))

    assert summary[0]["tradedate"] == "2026-08-28"
    assert detail[0]["financing_buy_amount"] == 120
    assert auction[0]["auc_vol"] == 1200
    assert auction[0]["time"] == "09:25:00"
