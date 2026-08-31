"""HTTP API for the margin-financing leverage dashboard."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from services.admin_auth import require_admin
from services.margin_leverage import margin_leverage_service


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/margin", tags=["两融杠杆中心"])


@router.get("/market")
async def margin_market(days: int = Query(250, ge=20, le=300)):
    try:
        return {"code": 0, "data": await margin_leverage_service.market(days)}
    except Exception as exc:
        logger.exception("Margin market dashboard failed")
        raise HTTPException(status_code=503, detail="两融市场缓存暂时不可用") from exc


@router.get("/sectors")
async def margin_sectors(
    sector_type: str = Query("industry", pattern="^(industry|concept|region)$"),
    sort: str = Query("net_buy"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(100, ge=1, le=500),
):
    try:
        return {"code": 0, "data": await margin_leverage_service.sectors(
            sector_type=sector_type, sort=sort, order=order, limit=limit,
        )}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Margin sector rankings failed")
        raise HTTPException(status_code=503, detail="两融板块榜暂时不可用") from exc


@router.get("/stocks/ranking")
async def margin_stock_rankings(
    metric: str = Query("balance"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(100, ge=1, le=500),
    sector: str | None = Query(None, max_length=100),
):
    try:
        return {"code": 0, "data": await margin_leverage_service.stock_rankings(
            metric=metric, order=order, limit=limit, sector=sector,
        )}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Margin stock rankings failed")
        raise HTTPException(status_code=503, detail="两融个股榜暂时不可用") from exc


@router.get("/stocks/{stock_code}")
async def margin_stock_detail(
    stock_code: str,
    refresh: bool = Query(False),
    history_limit: int = Query(260, ge=20, le=300),
):
    try:
        return {"code": 0, "data": await margin_leverage_service.stock_detail(
            stock_code, refresh=refresh, history_limit=history_limit,
        )}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Margin stock detail failed for %s", stock_code)
        raise HTTPException(status_code=503, detail="个股两融历史核验暂时失败，请稍后重试") from exc


@router.post("/refresh", dependencies=[Depends(require_admin)])
async def refresh_margin_data(
    full: bool = Query(True),
    prewarm: bool = Query(True),
):
    return {"code": 0, "data": margin_leverage_service.start_refresh(full=full, prewarm=prewarm)}


@router.get("/refresh/status")
async def margin_refresh_status():
    return {"code": 0, "data": await margin_leverage_service.persistent_refresh_status()}
