"""Small, provider-neutral NumCat/MeoZ gateway.

The gateway is intentionally narrower than a market-data warehouse. It owns
authentication, route affinity, bounded retry, rate limits, request
coalescing and short-lived memory caching. Business services receive the
decoded provider payload and never need to know the vendor URL or API key.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from config import settings


class NumCatGatewayError(RuntimeError):
    """Safe gateway error; credentials and upstream bodies are never exposed."""


@dataclass(slots=True)
class _CacheEntry:
    expires_at: float
    payload: dict[str, Any]


class NumCatUsageGovernor:
    """Bounded in-process QPS/RPM governor for a single backend worker."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._recent: deque[float] = deque()
        self._recent_by_endpoint: dict[str, deque[float]] = {}

    @staticmethod
    def _limit(value: Any, default: int) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return default

    async def acquire(self, endpoint: str, *, heavy: bool = False) -> None:
        qps = self._limit(settings.numcat_heavy_qps if heavy else settings.numcat_global_qps, 5 if heavy else 50)
        rpm = self._limit(settings.numcat_global_rpm, 500)
        endpoint_key = str(endpoint or "unknown")[:80]
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._recent and now - self._recent[0] >= 60:
                    self._recent.popleft()
                endpoint_rows = self._recent_by_endpoint.setdefault(endpoint_key, deque())
                while endpoint_rows and now - endpoint_rows[0] >= 1:
                    endpoint_rows.popleft()
                if len(self._recent) < rpm and len(endpoint_rows) < qps:
                    self._recent.append(now)
                    endpoint_rows.append(now)
                    return
                wait_seconds = 0.05
                if len(self._recent) >= rpm and self._recent:
                    wait_seconds = max(wait_seconds, 60 - (now - self._recent[0]))
                if len(endpoint_rows) >= qps and endpoint_rows:
                    wait_seconds = max(wait_seconds, 1 - (now - endpoint_rows[0]))
                await asyncio.sleep(min(wait_seconds, 30.0))

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        recent = sum(now - item < 60 for item in self._recent)
        by_endpoint = {
            key: sum(now - item < 60 for item in values)
            for key, values in self._recent_by_endpoint.items()
            if values
        }
        return {
            "requests_last_minute": recent,
            "requests_by_endpoint_last_minute": by_endpoint,
            "global_rpm_limit": self._limit(settings.numcat_global_rpm, 500),
            "global_qps_limit": self._limit(settings.numcat_global_qps, 50),
            "heavy_endpoint_qps_limit": self._limit(settings.numcat_heavy_qps, 5),
        }


class NumCatGateway:
    """API-first gateway with explicit route and fallback semantics."""

    HEAVY_ENDPOINTS = {
        "daily", "minute", "daily_auc", "daily_auc_detail", "valuation",
        "screening", "tick_history", "emoindic_daily", "limit_pool",
        "margin_detail", "margin_summary", "longhubang_stock",
        "longhubang_seat", "level2_trade_history", "level2_order_history",
        "level2_quote_history",
    }
    CONNECTION_ERRORS = (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
        httpx.NetworkError,
    )

    def __init__(
        self,
        *,
        client_factory: Callable[..., Any] = httpx.AsyncClient,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._client_factory = client_factory
        self._sleep = sleep
        self.governor = NumCatUsageGovernor()
        self._cache: dict[str, _CacheEntry] = {}
        self._cache_bytes = 0
        self._inflight: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        self._metrics = {
            "requests": 0,
            "cache_hits": 0,
            "fallback_count": 0,
            "429_count": 0,
            "errors": 0,
            "last_latency_ms": None,
            "avg_latency_ms": None,
            "cache_bytes": 0,
            "cache_skipped_oversize": 0,
        }

    @property
    def api_key(self) -> str:
        return str(settings.meoz_api_key or settings.numcat_api_key or "").strip()

    @property
    def configured(self) -> bool:
        return bool(settings.meoz_enabled and self.api_key)

    @property
    def route(self) -> str:
        value = str(settings.meoz_api_route or settings.numcat_route or "dedicated").strip().lower()
        return value if value in {"dedicated", "public"} else "dedicated"

    @staticmethod
    def _base(value: Any) -> str:
        return str(value or "").strip().rstrip("/")

    def _routes(self, market: str | None, affinity_key: str) -> list[tuple[str, str]]:
        market_key = str(market or "").strip().lower()
        public = self._base(settings.numcat_public_base_url or settings.numcat_api_base)
        configured_single = self._base(settings.numcat_api_base)
        dedicated = [
            ("sz", self._base(settings.numcat_sz_base_url)),
            ("sh", self._base(settings.numcat_sh_base_url)),
        ]
        if market_key in {"sz", "sh"}:
            preferred = [item for item in dedicated if item[0] == market_key]
            remaining = [item for item in dedicated if item[0] != market_key]
            dedicated = preferred + remaining
        dedicated = [item for item in dedicated if item[1]]
        if len(dedicated) > 1 and market_key not in {"sz", "sh"}:
            offset = int(hashlib.sha256(affinity_key.encode()).hexdigest()[:8], 16) % len(dedicated)
            dedicated = dedicated[offset:] + dedicated[:offset]

        if self.route == "public":
            return [("public", public)] if public else []

        # Preserve explicitly configured private HTTPS endpoints without
        # silently treating the public default as a dedicated route.
        dedicated_urls = {url for _, url in dedicated}
        single_is_explicit = bool(configured_single and configured_single not in {public, *dedicated_urls})
        routes = [("configured", configured_single)] if single_is_explicit else dedicated
        if settings.numcat_allow_public_fallback and public and all(url != public for _, url in routes):
            routes.append(("public", public))
        return routes

    @staticmethod
    def _safe_json(value: Any) -> Any:
        try:
            json.dumps(value, ensure_ascii=True)
            return value
        except (TypeError, ValueError):
            return str(value)

    def _cache_key(self, api_name: str, fields: str | list[str] | None, params: dict[str, Any]) -> str:
        encoded = json.dumps({"api": api_name, "fields": fields, "params": params}, sort_keys=True, ensure_ascii=True, default=str)
        return hashlib.sha256(encoded.encode()).hexdigest()

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._cache_delete(key)
            return None
        self._metrics["cache_hits"] += 1
        return entry.payload

    def _cache_put(self, key: str, payload: dict[str, Any], ttl: float) -> None:
        if ttl <= 0:
            return
        try:
            payload_bytes = len(json.dumps(payload, ensure_ascii=True, default=str).encode("utf-8"))
            max_payload_bytes = min(max(int(settings.numcat_cache_max_payload_bytes), 64 * 1024), 16 * 1024 * 1024)
            max_cache_bytes = min(max(int(settings.numcat_cache_max_bytes), max_payload_bytes), 128 * 1024 * 1024)
        except (TypeError, ValueError):
            payload_bytes = 0
            max_payload_bytes = 2 * 1024 * 1024
            max_cache_bytes = 16 * 1024 * 1024
        if payload_bytes <= 0 or payload_bytes > max_payload_bytes:
            self._metrics["cache_skipped_oversize"] += 1
            return
        try:
            limit = min(max(int(settings.numcat_cache_max_entries), 32), 10_000)
        except (TypeError, ValueError):
            limit = 512
        self._cache_delete(key)
        while self._cache and (
            len(self._cache) >= limit or self._cache_bytes + payload_bytes > max_cache_bytes
        ):
            oldest = min(self._cache, key=lambda item: self._cache[item].expires_at)
            self._cache_delete(oldest)
        self._cache[key] = _CacheEntry(time.monotonic() + ttl, payload)
        self._cache_bytes += payload_bytes
        self._metrics["cache_bytes"] = self._cache_bytes

    def _cache_delete(self, key: str) -> None:
        entry = self._cache.pop(key, None)
        if entry is None:
            return
        try:
            self._cache_bytes = max(
                0,
                self._cache_bytes - len(json.dumps(entry.payload, ensure_ascii=True, default=str).encode("utf-8")),
            )
        except (TypeError, ValueError):
            self._cache_bytes = 0
        self._metrics["cache_bytes"] = self._cache_bytes

    async def query(
        self,
        api_name: str,
        *,
        params: dict[str, Any] | None = None,
        fields: str | list[str] | None = None,
        market: str | None = None,
        cache_ttl: float = 0,
        bypass_cache: bool = False,
        affinity_key: str | None = None,
    ) -> dict[str, Any]:
        """Query one endpoint and return the raw decoded provider envelope.

        400/401/403/429 and business errors never switch routes. Only
        connection-class failures may try the next explicitly configured
        dedicated route. Public fallback is opt-in through configuration.
        """
        if not self.configured:
            raise NumCatGatewayError("NumCat数据源未配置服务端API密钥")
        name = str(api_name or "").strip()
        if not name or len(name) > 120:
            raise NumCatGatewayError("NumCat接口名称无效")
        clean_params = dict(params or {})
        key = self._cache_key(name, fields, clean_params)
        if not bypass_cache:
            cached = self._cache_get(key)
            if cached is not None:
                return cached
        async with self._lock:
            if not bypass_cache:
                cached = self._cache_get(key)
                if cached is not None:
                    return cached
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._request(name, clean_params, fields, market=market, affinity_key=affinity_key or key))
                self._inflight[key] = task
        try:
            payload = await asyncio.shield(task)
            self._cache_put(key, payload, float(cache_ttl))
            return payload
        finally:
            async with self._lock:
                if self._inflight.get(key) is task:
                    self._inflight.pop(key, None)

    async def _request(
        self,
        api_name: str,
        params: dict[str, Any],
        fields: str | list[str] | None,
        *,
        market: str | None,
        affinity_key: str,
    ) -> dict[str, Any]:
        routes = self._routes(market, affinity_key)
        if not routes:
            raise NumCatGatewayError("NumCat数据源地址未配置")
        timeout_value = min(max(float(settings.numcat_timeout or 7.0), 2.0), 60.0)
        max_attempts = min(max(int(settings.numcat_retry_count or 2), 1), 5)
        last_error: Exception | None = None
        for route_index, (route_name, url) in enumerate(routes):
            for attempt in range(max_attempts):
                await self.governor.acquire(api_name, heavy=api_name in self.HEAVY_ENDPOINTS)
                started = time.monotonic()
                self._metrics["requests"] += 1
                body = {
                    "apiname": api_name,
                    "apikey": self.api_key,
                    "fields": fields or "",
                    "params": params,
                }
                try:
                    async with self._client_factory(timeout=httpx.Timeout(timeout_value), follow_redirects=True) as client:
                        response = await client.post(
                            url,
                            json=body,
                            headers={"Accept": "application/json", "Content-Type": "application/json"},
                        )
                    latency_ms = (time.monotonic() - started) * 1000
                    self._record_latency(latency_ms)
                    if response.status_code == 429:
                        self._metrics["429_count"] += 1
                        retry_after = self._retry_after(response)
                        if attempt + 1 < max_attempts:
                            await self._sleep(retry_after if retry_after is not None else min(0.5 * (2**attempt), 5.0))
                            continue
                        raise NumCatGatewayError("NumCat额度或频率限制，请稍后重试")
                    if response.status_code >= 500:
                        if attempt + 1 < max_attempts:
                            await self._sleep(min(0.35 * (2**attempt), 3.0))
                            continue
                        raise NumCatGatewayError("NumCat服务暂时不可用")
                    if response.status_code in {400, 401, 403}:
                        raise NumCatGatewayError(f"NumCat请求被拒绝（HTTP {response.status_code}）")
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise NumCatGatewayError("NumCat返回格式不可识别")
                    code = payload.get("code")
                    if code not in (None, 0, 200, "0", "200"):
                        raise NumCatGatewayError(f"NumCat业务响应异常（code={code}）")
                    return payload
                except NumCatGatewayError:
                    self._metrics["errors"] += 1
                    raise
                except self.CONNECTION_ERRORS as exc:
                    last_error = exc
                    if attempt + 1 < max_attempts:
                        await self._sleep(min(0.35 * (2**attempt), 3.0))
                        continue
                    if route_index + 1 < len(routes):
                        self._metrics["fallback_count"] += 1
                        break
                except (httpx.HTTPError, ValueError, TypeError) as exc:
                    last_error = exc
                    self._metrics["errors"] += 1
                    if attempt + 1 < max_attempts:
                        await self._sleep(min(0.35 * (2**attempt), 3.0))
                        continue
                    raise NumCatGatewayError("NumCat请求失败，请稍后重试") from exc
        self._metrics["errors"] += 1
        raise NumCatGatewayError("NumCat连接失败，请检查专线配置") from last_error

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        try:
            return min(max(float(value), 0.1), 30.0) if value else None
        except (TypeError, ValueError):
            return None

    def _record_latency(self, value: float) -> None:
        self._metrics["last_latency_ms"] = round(value, 1)
        previous = self._metrics.get("avg_latency_ms")
        self._metrics["avg_latency_ms"] = round(value if previous is None else previous * 0.8 + value * 0.2, 1)

    def status(self) -> dict[str, Any]:
        return {
            "provider": "numcat",
            "configured": self.configured,
            "route": self.route,
            "dedicated_routes_configured": bool(self._base(settings.numcat_sz_base_url) or self._base(settings.numcat_sh_base_url)),
            "public_fallback_enabled": bool(settings.numcat_allow_public_fallback),
            "api_key_exposed_to_frontend": False,
            "cache_entries": len(self._cache),
            "cache_bytes": self._cache_bytes,
            "cache_max_bytes": settings.numcat_cache_max_bytes,
            "cache_max_payload_bytes": settings.numcat_cache_max_payload_bytes,
            "cache_policy": "短缓存+请求合并+字节上限，默认不持久化原始行情",
            "usage": {**self._metrics, **self.governor.snapshot()},
        }

    def clear_expired_cache(self) -> int:
        now = time.monotonic()
        expired = [key for key, entry in self._cache.items() if entry.expires_at <= now]
        for key in expired:
            self._cache_delete(key)
        return len(expired)


numcat_gateway = NumCatGateway()
