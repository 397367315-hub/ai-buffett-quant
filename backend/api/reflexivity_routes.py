"""API surface for the V5 Skill 10 behaviour/reflexivity diagnosis."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from services.reflexivity_service import reflexivity_service


router = APIRouter(prefix="/api/v1/trading-skills/reflexivity", tags=["Skill 10行为反身性"])
skills_alias_router = APIRouter(prefix="/api/v1/skills/reflexivity", tags=["Skill 10行为反身性"])


def _as_of(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="as_of必须使用YYYY-MM-DD格式") from exc


@router.get("/scan")
async def scan_reflexivity(
    horizon: str = Query("3d", pattern="^(1d|3d|1w|1m)$"),
    sector: str | None = Query(None, max_length=80),
    min_score: float = Query(0, ge=0, le=100),
    refresh: bool = Query(False),
    exclude_star_market: bool = Query(True),
    exclude_gem: bool = Query(True),
    limit: int = Query(50, ge=1, le=100),
):
    try:
        data = await reflexivity_service.scan(
            horizon=horizon, sector=sector, min_score=min_score, force=refresh,
            exclude_star_market=exclude_star_market, exclude_gem=exclude_gem, limit=limit,
        )
        return {"code": 0, "data": data}
    except Exception as exc:
        print(f"Reflexivity scan failed: {type(exc).__name__}")
        raise HTTPException(status_code=503, detail="行为反身性扫描暂时不可用，请稍后重试") from exc


@router.get("/{symbol}/history")
async def reflexivity_history(symbol: str, limit: int = Query(30, ge=1, le=120)):
    try:
        return {"code": 0, "data": {"symbol": symbol, "history": await reflexivity_service.history(symbol, limit)}}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="行为反身性历史暂时不可用") from exc


@router.get("/{symbol}/liquidity-map")
async def reflexivity_liquidity_map(symbol: str, as_of: str | None = Query(None), refresh: bool = Query(False)):
    try:
        data = await reflexivity_service.diagnose(symbol, as_of=_as_of(as_of), force=refresh)
        return {"code": 0, "data": {"symbol": data.get("symbol"), "data_cutoff_time": data.get("data_cutoff_time"), "liquidity_map": data.get("liquidity_map"), "audit": data.get("audit")}}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="流动性地图暂时不可用") from exc


@router.get("/{symbol}/forced-trading")
async def reflexivity_forced_trading(symbol: str, as_of: str | None = Query(None), refresh: bool = Query(False)):
    try:
        data = await reflexivity_service.diagnose(symbol, as_of=_as_of(as_of), force=refresh)
        return {"code": 0, "data": {"symbol": data.get("symbol"), "data_cutoff_time": data.get("data_cutoff_time"), "forced_trading": data.get("forced_trading"), "audit": data.get("audit")}}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="被迫交易压力暂时不可用") from exc


@router.get("/{symbol}/explain")
async def explain_reflexivity(symbol: str, as_of: str | None = Query(None), refresh: bool = Query(False)):
    try:
        return {"code": 0, "data": await reflexivity_service.explain(symbol, as_of=_as_of(as_of), force=refresh)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="行为反身性解释暂时不可用") from exc


@router.get("/{symbol}")
async def get_reflexivity(symbol: str, as_of: str | None = Query(None), refresh: bool = Query(False)):
    try:
        return {"code": 0, "data": await reflexivity_service.diagnose(symbol, as_of=_as_of(as_of), force=refresh)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        print(f"Reflexivity diagnosis failed for {symbol}: {type(exc).__name__}")
        raise HTTPException(status_code=503, detail="行为反身性诊断暂时不可用，请稍后重试") from exc


# Keep the documented /api/v1/skills/... path as an alias while the existing
# product namespace remains /api/v1/trading-skills/....
for _route in router.routes:
    if getattr(_route, "path", "").startswith(router.prefix):
        skills_alias_router.add_api_route(
            _route.path.removeprefix(router.prefix) or "/",
            _route.endpoint,
            methods=list(_route.methods or {"GET"}),
            response_model=_route.response_model,
            name=f"skills_alias_{_route.name}",
            operation_id=f"skills_alias_{_route.operation_id}" if _route.operation_id else None,
        )

