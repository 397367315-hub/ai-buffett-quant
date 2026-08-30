"""HTTP endpoints for the optional, on-demand Level-2 radar."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from services.admin_auth import require_admin
from services.level2_service import level2_service


logger = logging.getLogger(__name__)
# Level-2 requests can consume a paid, per-symbol vendor quota. Keep every
# endpoint behind the existing private-site session, including cache reads,
# so an unauthenticated caller cannot trigger the background sync path.
router = APIRouter(tags=["Level-2微观结构"], dependencies=[Depends(require_admin)])


def _trade_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="trade_date必须使用YYYY-MM-DD格式") from exc


async def _read(
    operation: str,
    symbol: str,
    *,
    trade_date: str | None,
    refresh: bool,
) -> dict[str, Any]:
    try:
        target = _trade_date(trade_date)
        if operation == "summary":
            data = await level2_service.summary(symbol, trade_date=target, refresh=refresh)
        elif operation == "timeline":
            data = await level2_service.timeline(symbol, trade_date=target, refresh=refresh)
        else:
            data = await level2_service.events(symbol, trade_date=target, refresh=refresh)
        return {"code": 0, "data": data}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Level-2 %s failed for %s", operation, symbol)
        raise HTTPException(status_code=503, detail="Level-2数据暂时不可用，普通行情不受影响") from exc


@router.get("/api/v1/stocks/{symbol}/level2/summary")
async def level2_summary(
    symbol: str,
    trade_date: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _read("summary", symbol, trade_date=trade_date, refresh=refresh)


@router.get("/api/v1/stocks/{symbol}/level2/timeline")
async def level2_timeline(
    symbol: str,
    trade_date: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _read("timeline", symbol, trade_date=trade_date, refresh=refresh)


@router.get("/api/v1/stocks/{symbol}/level2/events")
async def level2_events(
    symbol: str,
    trade_date: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _read("events", symbol, trade_date=trade_date, refresh=refresh)


@router.get("/api/v1/stocks/{symbol}/level2/sync/status")
async def level2_sync_status(
    symbol: str,
    trade_date: str | None = Query(None),
):
    try:
        return {"code": 0, "data": await level2_service.sync_status(symbol, _trade_date(trade_date))}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Level-2 sync status failed for %s", symbol)
        raise HTTPException(status_code=503, detail="Level-2同步状态暂时不可用") from exc


@router.post("/api/v1/internal/level2/sync/{symbol}")
async def level2_sync(
    symbol: str,
    payload: dict[str, Any] = Body(default_factory=dict),
):
    try:
        target = _trade_date(payload.get("trade_date")) or await level2_service.resolve_trade_date()
        requested_types = payload.get("data_types")
        data_types = None
        if requested_types is not None:
            if not isinstance(requested_types, list) or not requested_types or any(item not in {"trade", "order", "quote"} for item in requested_types):
                raise HTTPException(status_code=422, detail="data_types只能包含trade、order、quote")
            data_types = tuple(dict.fromkeys(requested_types))
        result = level2_service.start_sync(
            symbol,
            target,
            force=bool(payload.get("force") or payload.get("refresh")),
            data_types=data_types,
            start_time=str(payload.get("start_time") or "").strip() or None,
            end_time=str(payload.get("end_time") or "").strip() or None,
        )
        return {"code": 0, "data": {"symbol": symbol, "trade_date": target.isoformat(), **result}}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Level-2 sync start failed for %s", symbol)
        raise HTTPException(status_code=503, detail="Level-2同步任务暂时无法启动") from exc
