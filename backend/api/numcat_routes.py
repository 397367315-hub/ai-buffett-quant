"""Protected NumCat data-hub endpoints.

The frontend and OpenClaw can request any documented, allowlisted NumCat
dataset without receiving the server-side API key. These endpoints are
read-only and never persist vendor responses.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from services.admin_auth import require_admin
from market_data.numcat.extended_provider import numcat_extended_provider


router = APIRouter(
    prefix="/api/v1/numcat",
    tags=["NumCat统一数据中枢"],
    dependencies=[Depends(require_admin)],
)


@router.get("/status")
async def numcat_status():
    return {"code": 0, "data": numcat_extended_provider.status()}


@router.get("/catalog")
async def numcat_catalog():
    return {
        "code": 0,
        "data": {
            "version": "0.0.481",
            "count": len(numcat_extended_provider.catalog()),
            "persistent_raw_storage": False,
            "items": numcat_extended_provider.catalog(),
        },
    }


@router.post("/query")
async def numcat_query(request: dict[str, Any] = Body(default_factory=dict)):
    apiname = str(request.get("apiname") or "").strip()
    params = request.get("params")
    fields = request.get("fields")
    refresh = request.get("refresh", False)
    if not isinstance(refresh, bool):
        raise HTTPException(status_code=422, detail="refresh必须是布尔值")
    try:
        data = await numcat_extended_provider.query(
            apiname,
            params=params,
            fields=fields,
            refresh=refresh,
            cache_ttl=request.get("cache_ttl"),
        )
        return {"code": 0, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="NumCat数据暂时不可用，请稍后重试") from exc


@router.post("/research-bundle")
async def numcat_research_bundle(request: dict[str, Any] = Body(default_factory=dict)):
    raw_symbols = request.get("symbols")
    if isinstance(raw_symbols, str):
        symbols = [item.strip() for item in raw_symbols.split(",") if item.strip()]
    elif isinstance(raw_symbols, list):
        symbols = [str(item).strip() for item in raw_symbols if str(item).strip()]
    else:
        symbols = []
    if len(symbols) > 20:
        raise HTTPException(status_code=422, detail="research-bundle一次最多查询20只股票")
    target = None
    if request.get("tradedate"):
        try:
            target = date.fromisoformat(str(request["tradedate"])[:10])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="tradedate必须使用YYYY-MM-DD格式") from exc
    for key in ("include_finance", "include_regulatory", "include_microstructure"):
        if key in request and not isinstance(request[key], bool):
            raise HTTPException(status_code=422, detail=f"{key}必须是布尔值")
    try:
        data = await numcat_extended_provider.research_bundle(
            symbols,
            tradedate=target,
            include_finance=request.get("include_finance", True),
            include_regulatory=request.get("include_regulatory", True),
            include_microstructure=request.get("include_microstructure", True),
        )
        return {"code": 0, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="NumCat研究包暂时不可用，请稍后重试") from exc


@router.post("/realtime-ticket")
async def numcat_realtime_ticket(request: dict[str, Any] = Body(default_factory=dict)):
    stream = str(request.get("stream") or "tick_stream_v1").strip()
    try:
        data = await numcat_extended_provider.realtime_ticket(
            stream,
            symbols=request.get("symbols") if isinstance(request.get("symbols"), list) else None,
            groups=request.get("groups") if isinstance(request.get("groups"), list) else None,
            event_types=request.get("event_types") if isinstance(request.get("event_types"), list) else None,
            fields=request.get("fields") if isinstance(request.get("fields"), list) else None,
            ttl_seconds=int(request.get("ttl_seconds", 120)),
        )
        return {"code": 0, "data": data}
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="NumCat实时流票据暂时不可用，请稍后重试") from exc
