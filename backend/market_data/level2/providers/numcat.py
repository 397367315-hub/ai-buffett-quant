"""NumCat/MeoZ Level-2 HTTP adapter.

The public contract is a regular POST endpoint (not a WebSocket): one symbol,
one trading day, and cursor-based pages.  Authentication is deliberately kept
inside this module so the rest of the application can never accidentally send
the provider key to a browser or log it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx

from config import settings
from market_data.numcat.gateway import NumCatGatewayError, numcat_gateway

from ..normalizer import normalize_symbol, row_from_fields
from .base import Level2DataType, Level2Page, Level2Provider, ProviderCapabilities


class NumCatProviderError(RuntimeError):
    """A safe, provider-neutral error; secrets are never included in its text."""


class NumCatProvider(Level2Provider):
    name = "numcat"

    _FIELDS: dict[Level2DataType, str] = {
        "trade": "symbol,tradedate,time,trade_id,price,volume,amount,bs_flag,trade_code,buy_order_id,sell_order_id",
        "order": "symbol,tradedate,time,order_id,price,volume,amount,side,order_type,order_no",
        "quote": "symbol,tradedate,time,open,high,low,close,pre_close,volume,amount,bid1,bid_vol1,ask1,ask_vol1,bid2,bid_vol2,ask2,ask_vol2,bid3,bid_vol3,ask3,ask_vol3,bid4,bid_vol4,ask4,ask_vol4,bid5,bid_vol5,ask5,ask_vol5,bid6,bid_vol6,ask6,ask_vol6,bid7,bid_vol7,ask7,ask_vol7,bid8,bid_vol8,ask8,ask_vol8,bid9,bid_vol9,ask9,ask_vol9,bid10,bid_vol10,ask10,ask_vol10",
    }
    _APINAMES: dict[Level2DataType, str] = {
        "trade": "level2_trade_history",
        "order": "level2_order_history",
        "quote": "level2_quote_history",
    }

    def __init__(
        self,
        *,
        client_factory: Callable[..., Any] | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        # A custom client is retained only for deterministic adapter tests.
        # Production requests always use the shared API-first gateway.
        self._client_factory = client_factory
        self._sleep = sleep
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0

    @property
    def configured(self) -> bool:
        # Keep the credential source identical to the API-first gateway. The
        # legacy variable remains supported for existing Render deployments.
        return bool(settings.level2_enabled and str(settings.meoz_api_key or settings.numcat_api_key or "").strip())

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_history_trade=True,
            supports_history_order=True,
            supports_history_quote=True,
            # The documented endpoints are historical. Do not advertise live
            # polling or streaming until the account capability is confirmed.
            supports_realtime_polling=False,
            supports_streaming=False,
            supports_bulk_symbols=False,
        )

    @staticmethod
    def _timeout() -> float:
        try:
            value = float(settings.numcat_timeout)
        except (TypeError, ValueError):
            value = 20.0
        return min(max(value, 2.0), 60.0)

    @staticmethod
    def _retry_count() -> int:
        try:
            value = int(settings.numcat_retry_count)
        except (TypeError, ValueError):
            value = 3
        return min(max(value, 1), 5)

    @staticmethod
    def _min_interval() -> float:
        try:
            value = float(settings.numcat_min_request_interval)
        except (TypeError, ValueError):
            value = 0.25
        return min(max(value, 0.0), 10.0)

    @staticmethod
    def _page_size(value: int | None) -> int:
        candidate = value if value is not None else settings.level2_page_size
        try:
            return min(max(int(candidate), 1), 50_000)
        except (TypeError, ValueError):
            return 5_000

    @staticmethod
    def _url() -> str:
        value = str(settings.numcat_api_base or "").strip().rstrip("/")
        if not value:
            raise NumCatProviderError("NumCat API地址未配置")
        # HTTPS is required in production. localhost/http remains useful for
        # deterministic local integration tests.
        if not value.startswith("https://") and not value.startswith(("http://localhost", "http://127.0.0.1")):
            raise NumCatProviderError("NumCat API地址必须使用HTTPS")
        return value

    async def _wait_for_rate_limit(self) -> None:
        async with self._rate_lock:
            elapsed = time.monotonic() - self._last_request_at
            delay = self._min_interval() - elapsed
            if delay > 0:
                await self._sleep(delay)
            self._last_request_at = time.monotonic()

    async def _request(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise NumCatProviderError("Level-2数据源未配置API密钥")
        if self._client_factory is None:
            try:
                return await numcat_gateway.query(
                    str(body["apiname"]),
                    fields=body.get("fields"),
                    params=dict(body.get("params") or {}),
                    market=None,
                    cache_ttl=0,
                    bypass_cache=True,
                    affinity_key=(
                        f"level2:{body['apiname']}:"
                        f"{(body.get('params') or {}).get('symbol', '')}:"
                        f"{(body.get('params') or {}).get('tradedate', '')}"
                    ),
                )
            except NumCatGatewayError as exc:
                raise NumCatProviderError(str(exc)) from exc
        url = self._url()
        attempts = self._retry_count()
        last_error: Exception | None = None
        for attempt in range(attempts):
            await self._wait_for_rate_limit()
            try:
                timeout = httpx.Timeout(self._timeout())
                async with self._client_factory(timeout=timeout, follow_redirects=True) as client:
                    response = await client.post(
                        url,
                        json=body,
                        headers={"Accept": "application/json", "Content-Type": "application/json"},
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    retry_after = _retry_after(response)
                    if attempt + 1 < attempts:
                        await self._sleep(retry_after if retry_after is not None else 0.35 * (2**attempt))
                        continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise NumCatProviderError("NumCat返回格式不可识别")
                code = payload.get("code")
                if code not in (None, 0, 200, "0", "200"):
                    # Keep the upstream message out of the exception if it
                    # could contain request details or account information.
                    raise NumCatProviderError(f"NumCat业务响应异常（code={code}）")
                return payload
            except NumCatProviderError as exc:
                last_error = exc
                # Business errors are normally not transient. A 429/5xx has
                # already been handled above, so fail quickly here.
                if attempt + 1 >= attempts or "业务响应异常" not in str(exc):
                    raise
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await self._sleep(0.35 * (2**attempt))
                    continue
        raise NumCatProviderError("NumCat请求失败，请稍后重试") from last_error

    async def fetch_page(
        self,
        data_type: Level2DataType,
        symbol: str,
        trade_date: date,
        *,
        cursor: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        page_size: int | None = None,
    ) -> Level2Page:
        if data_type not in self._APINAMES:
            raise NumCatProviderError(f"不支持的Level-2数据类型: {data_type}")
        normalized_symbol = normalize_symbol(symbol)
        params: dict[str, Any] = {
            "symbol": normalized_symbol,
            "tradedate": trade_date.strftime("%Y%m%d"),
            "page_size": self._page_size(page_size),
        }
        if cursor:
            params["cursor"] = str(cursor)
        if start_time:
            params["start_time"] = str(start_time)
        if end_time:
            params["end_time"] = str(end_time)
        payload = {
            "apiname": self._APINAMES[data_type],
            "apikey": str(settings.meoz_api_key or settings.numcat_api_key).strip(),
            "fields": self._FIELDS[data_type],
            "params": params,
        }
        response = await self._request(payload)
        return _parse_page(response, data_type, params["page_size"])


def _parse_page(payload: dict[str, Any], data_type: Level2DataType, requested_size: int) -> Level2Page:
    data: Any = payload.get("data")
    if not isinstance(data, dict):
        result = payload.get("result")
        data = result.get("data") if isinstance(result, dict) else result
    if isinstance(data, dict):
        fields = data.get("fields") or []
        raw_rows = data.get("items")
        if raw_rows is None:
            raw_rows = data.get("rows")
        if raw_rows is None:
            raw_rows = data.get("data")
        next_cursor = data.get("next_cursor") or data.get("nextCursor")
        has_more = bool(data.get("has_more", data.get("hasMore", bool(next_cursor))))
        page_size = data.get("page_size") or data.get("pageSize") or requested_size
    elif isinstance(data, list):
        fields = []
        raw_rows = data
        next_cursor = None
        has_more = False
        page_size = requested_size
    else:
        raise NumCatProviderError("NumCat响应缺少分页数据")

    if not isinstance(fields, list):
        fields = []
    if not isinstance(raw_rows, list):
        raw_rows = []
    rows = [row_from_fields(fields, row) for row in raw_rows]
    rows = [row for row in rows if row]
    return Level2Page(
        data_type=data_type,
        fields=[str(item) for item in fields],
        rows=rows,
        page_size=int(page_size or requested_size),
        next_cursor=str(next_cursor) if next_cursor not in (None, "") else None,
        has_more=bool(has_more),
        raw_metadata={
            "code": payload.get("code"),
            "message": str(payload.get("message") or ""),
        },
    )


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    try:
        return min(max(float(value), 0.1), 30.0) if value else None
    except (TypeError, ValueError):
        return None
