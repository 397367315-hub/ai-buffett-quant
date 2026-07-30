import logging
import os
import secrets
import time
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


ALLOWED_HOSTS = {
    "push2.eastmoney.com",
    "datacenter.eastmoney.com",
}

UPSTREAM_HEALTH_URL = "https://push2.eastmoney.com/api/qt/clist/get"
UPSTREAM_HEALTH_PARAMS = {
    "pn": "1",
    "pz": "1",
    "po": "0",
    "np": "1",
    "fid": "f62",
    "fs": "m:90+t3",
    "fields": "f12,f14,f62",
    "fltt": "2",
    "ut": "b2884a393a59ad6402e4dd90d24e112f",
}

logger = logging.getLogger(__name__)


class FetchRequest(BaseModel):
    url: str
    params: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)


app = FastAPI(title="China Stock Data Proxy", version="1.0.0")


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
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/upstream")
async def upstream_health():
    started_at = time.monotonic()
    try:
        data = await _get_upstream_json(
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
            "upstream": "eastmoney",
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
        return await _get_upstream_json(request.url, request.params, safe_headers)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Upstream HTTP error: {e.response.status_code}") from e
    except Exception as e:
        logger.warning("EastMoney proxy request failed: %s", type(e).__name__)
        raise HTTPException(status_code=502, detail="Upstream request failed") from e
