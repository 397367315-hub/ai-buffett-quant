"""HTTP API for the additive Strong Stock V2.1 bridge layer."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Body, HTTPException, Query

from services.strong_stock_v21 import strong_stock_v21_service


router = APIRouter(prefix="/api/v1", tags=["强势股交易决策 V2.1"])


def _date(value: str | None, field: str = "date") -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field}必须使用YYYY-MM-DD格式") from exc


async def _call(factory, *args, **kwargs):
    try:
        return {"code": 0, "data": await factory(*args, **kwargs)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="V2.1研究桥接层暂时不可用") from exc


@router.get("/strong-stock-decision/v21/overview")
async def v21_overview(date_value: str | None = Query(None, alias="date"), refresh: bool = Query(False)):
    return await _call(strong_stock_v21_service.overview, _date(date_value), refresh=refresh)


@router.post("/strong-stock-decision/v21/refresh")
async def v21_refresh(payload: dict = Body(default_factory=dict)):
    return await _call(strong_stock_v21_service.overview, _date(payload.get("date")), refresh=True)


@router.get("/market/regime")
async def market_regime(date_value: str | None = Query(None, alias="date")):
    return await _call(strong_stock_v21_service.regime, _date(date_value))


@router.get("/sectors/lifecycle")
async def sectors_lifecycle(date_value: str | None = Query(None, alias="date"), sector_type: str | None = Query(None)):
    # sector_type is retained for clients, while the bridge response exposes
    # both industry and concept lifecycles in one coherent snapshot.
    return await _call(strong_stock_v21_service.lifecycle_view, _date(date_value))


@router.get("/sectors/{sector_id}/trajectory")
async def sector_trajectory(sector_id: str, days: int = Query(20, ge=1, le=60), date_value: str | None = Query(None, alias="date")):
    return await _call(strong_stock_v21_service.trajectory, sector_id, _date(date_value), days=days)


@router.get("/sectors/migration")
async def sector_migration(date_value: str | None = Query(None, alias="date")):
    return await _call(strong_stock_v21_service.migration_view, _date(date_value))


@router.get("/opportunities/ab")
async def ab_opportunities(date_value: str | None = Query(None, alias="date"), pool: str | None = Query(None, max_length=30)):
    return await _call(strong_stock_v21_service.opportunities, _date(date_value), pool=pool)


@router.get("/opportunities/ab/{symbol}")
async def ab_opportunity_detail(symbol: str, date_value: str | None = Query(None, alias="date")):
    return await _call(strong_stock_v21_service.opportunity_detail, symbol, _date(date_value))


@router.get("/review/daily")
async def daily_review(date_value: str | None = Query(None, alias="date")):
    return await _call(strong_stock_v21_service.daily_review, _date(date_value))


@router.get("/review/verification")
async def review_verification(date_value: str | None = Query(None, alias="date")):
    return await _call(strong_stock_v21_service.verification, _date(date_value))


@router.get("/events/preheat")
async def event_preheat(date_value: str | None = Query(None, alias="date")):
    return await _call(strong_stock_v21_service.event_preheat, _date(date_value))


@router.get("/evolution/proposals")
async def evolution_proposals():
    return await _call(strong_stock_v21_service.proposals)


@router.post("/evolution/proposals/generate")
async def evolution_generate():
    return await _call(strong_stock_v21_service.generate_proposal)


@router.post("/evolution/proposals/{proposal_id}/{action}")
async def evolution_action(proposal_id: int, action: str):
    return await _call(strong_stock_v21_service.proposal_action, proposal_id, action)
