import asyncio
import logging
import os
import re
import secrets
import time
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


ALLOWED_HOSTS = {
    "data.eastmoney.com",
    "push2.eastmoney.com",
    "push2ex.eastmoney.com",
    "push2delay.eastmoney.com",
    "push2his.eastmoney.com",
    "datacenter.eastmoney.com",
    "datacenter-web.eastmoney.com",
    "web.ifzq.gtimg.cn",
}

PUSH2_HOST = "push2.eastmoney.com"
PUSH2_DELAY_HOST = "push2delay.eastmoney.com"
PUSH2_HISTORY_HOST = "push2his.eastmoney.com"
SECTOR_FLOW_PATH = "/api/qt/clist/get"
SECTOR_FLOW_FALLBACK_URL = "https://data.eastmoney.com/dataapi/bkzj/getbkzj"
UPSTREAM_HEALTH_URL = "https://push2.eastmoney.com/api/qt/clist/get"
UPSTREAM_HEALTH_PARAMS = {
    "pn": "1",
    "pz": "1",
    "po": "0",
    "np": "1",
    "fid": "f62",
    "fs": "m:90+t:3",
    "fields": "f12,f14,f62",
    "fltt": "2",
    "ut": "b2884a393a59ad6402e4dd90d24e112f",
}


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


# Existing Render environments can retain earlier values. Cap them here so a
# stale setting cannot turn one failed market request into a minute-long wait.
UPSTREAM_TIMEOUT = min(_positive_float("DATA_PROXY_TIMEOUT", 8.0), 8.0)
UPSTREAM_MAX_ATTEMPTS = min(_positive_int("DATA_PROXY_MAX_ATTEMPTS", 2), 2)
UPSTREAM_RETRY_DELAY = _positive_float("DATA_PROXY_RETRY_DELAY", 0.35)
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

logger = logging.getLogger(__name__)


class FetchRequest(BaseModel):
    url: str
    params: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)


app = FastAPI(title="China Stock Data Proxy", version="1.0.0")


class UpstreamPayloadError(RuntimeError):
    """Raised when an upstream returns an application-level error response."""


def _require_token(token: str | None):
    expected = os.getenv("DATA_PROXY_TOKEN", "")
    if expected and (not token or not secrets.compare_digest(token, expected)):
        raise HTTPException(status_code=401, detail="Invalid data proxy token")


def _validate_url(url: str):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Upstream URL is invalid") from exc

    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or port not in (None, 443)
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(status_code=400, detail="Upstream host is not allowed")


async def _get_upstream_json(url: str, params: dict, headers: dict) -> dict:
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
        for attempt in range(UPSTREAM_MAX_ATTEMPTS):
            try:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as exc:
                last_error = exc
                status_code = (
                    exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError)
                    else None
                )
                if (
                    attempt + 1 >= UPSTREAM_MAX_ATTEMPTS
                    or status_code is not None
                    and status_code not in RETRYABLE_STATUS_CODES
                ):
                    raise
                await asyncio.sleep(UPSTREAM_RETRY_DELAY * (attempt + 1))

    if last_error is not None:
        raise last_error
    raise RuntimeError("Upstream request did not run")


def _payload_has_error(data: dict) -> bool:
    return isinstance(data, dict) and "rc" in data and str(data.get("rc")) not in {"0"}


def _replace_host(url: str, host: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(netloc=host).geturl()


def _sector_flow_fallback_params(params: dict) -> dict | None:
    """Translate push2 sector filters to the public data-center endpoint."""
    fs = str(params.get("fs", "")).replace(" ", "+")
    match = re.fullmatch(r"m:90(?:\+| )(t|s):?(\d+)", fs)
    if not match:
        return None

    board_type, board_id = match.groups()
    key = str(params.get("fid0") or params.get("fid") or "f62")
    if not re.fullmatch(r"f\d+", key):
        key = "f62"
    return {"key": key, "code": f"m:90+{board_type}:{board_id}"}


async def _fetch_market_request(url: str, params: dict, headers: dict) -> dict:
    parsed = urlparse(url)
    candidates: list[tuple[str, dict]] = [(url, params)]

    if parsed.hostname in {PUSH2_HOST, PUSH2_HISTORY_HOST}:
        # The delay node is reachable from overseas Render regions and keeps
        # the same JSON contract as the regular and historical push2 nodes.
        candidates.insert(0, (_replace_host(url, PUSH2_DELAY_HOST), params))

    if parsed.hostname == PUSH2_HOST:
        if parsed.path == SECTOR_FLOW_PATH:
            fallback_params = _sector_flow_fallback_params(params)
            if fallback_params:
                normalized_params = dict(params)
                normalized_params["fs"] = fallback_params["code"]
                candidates[0] = (
                    _replace_host(url, PUSH2_DELAY_HOST),
                    normalized_params,
                )
                candidates.insert(
                    1,
                    (_replace_host(url, PUSH2_HOST), normalized_params),
                )
                candidates.append((SECTOR_FLOW_FALLBACK_URL, fallback_params))

    last_error: Exception | None = None
    for candidate_url, candidate_params in candidates:
        try:
            data = await _get_upstream_json(candidate_url, candidate_params, headers)
            if _payload_has_error(data):
                raise UpstreamPayloadError(
                    f"Upstream returned rc={data.get('rc')}"
                )
            return data
        except (
            httpx.HTTPStatusError,
            httpx.RequestError,
            ValueError,
            UpstreamPayloadError,
        ) as exc:
            last_error = exc
            candidate = urlparse(candidate_url)
            logger.warning(
                "EastMoney candidate failed: host=%s path=%s error=%s",
                candidate.hostname,
                candidate.path,
                type(exc).__name__,
            )

    if last_error is not None:
        raise last_error
    raise RuntimeError("No upstream candidates configured")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/upstream")
async def upstream_health():
    started_at = time.monotonic()
    try:
        data = await _fetch_market_request(
            UPSTREAM_HEALTH_URL,
            UPSTREAM_HEALTH_PARAMS,
            {
                "User-Agent": "Mozilla/5.0 AppleWebKit/537.36",
                "Referer": "https://data.eastmoney.com/",
                "Accept": "application/json,text/plain,*/*",
            },
        )
        records = (data.get("data") or {}).get("diff") or []
        if not records:
            raise RuntimeError("Upstream returned no records")
        return {
            "status": "ok",
            "upstream": "eastmoney-failover",
            "records": len(records),
            "latency_ms": round((time.monotonic() - started_at) * 1000),
        }
    except Exception as exc:
        logger.warning("EastMoney upstream health check failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="EastMoney upstream is unavailable") from exc


@app.post("/fetch")
async def fetch_market_data(
    request: FetchRequest,
    x_data_proxy_token: str | None = Header(default=None),
):
    _require_token(x_data_proxy_token)
    _validate_url(request.url)

    safe_headers = {
        "User-Agent": request.headers.get(
            "User-Agent",
            "Mozilla/5.0 AppleWebKit/537.36",
        ),
        "Referer": request.headers.get("Referer", "https://data.eastmoney.com/"),
        "Accept": request.headers.get("Accept", "application/json,text/plain,*/*"),
    }

    try:
        return await _fetch_market_request(request.url, request.params, safe_headers)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Upstream HTTP error: {e.response.status_code}") from e
    except Exception as e:
        logger.warning("EastMoney proxy request failed: %s", type(e).__name__)
        raise HTTPException(status_code=502, detail="Upstream request failed") from e
