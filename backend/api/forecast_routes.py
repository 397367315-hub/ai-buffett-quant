"""Public V5 forward-forecast API."""

from fastapi import APIRouter, Query, HTTPException

from services.factor_registry_v5 import causal_chain, factor_definition, factor_definitions
from services.forecast_v5 import forecast_v5_service


router = APIRouter(prefix="/api/v1")


@router.get("/forecast/dashboard")
async def get_forecast_dashboard(refresh: bool = Query(False)):
    return {"code": 0, "data": await forecast_v5_service.dashboard(force=refresh)}


@router.get("/forecast/market")
async def get_forecast_market(refresh: bool = Query(False)):
    data = await forecast_v5_service.dashboard(force=refresh)
    return {"code": 0, "data": {
        "version": data.get("version"), "forecast_date": data.get("forecast_date"), "phase": data.get("phase"),
        "data_cutoff_time": data.get("data_cutoff_time"), "risk_preference": data.get("risk_preference"),
        "timeline": data.get("timeline") or [], "turning_points": data.get("turning_points") or {},
        "data_health": data.get("data_health") or {}, "audit": data.get("audit") or {},
    }}


@router.get("/forecast/timeline")
async def get_forecast_timeline(refresh: bool = Query(False)):
    data = await forecast_v5_service.dashboard(force=refresh)
    return {"code": 0, "data": {"timeline": data.get("timeline") or [], "forecast_date": data.get("forecast_date"), "model_version": data.get("model_version"), "data_health": data.get("data_health")}}


@router.get("/forecast/resonance")
async def get_forecast_resonance(refresh: bool = Query(False)):
    data = await forecast_v5_service.dashboard(force=refresh)
    return {"code": 0, "data": data.get("resonance") or {}}


@router.get("/forecast/scenarios")
async def get_forecast_scenarios(refresh: bool = Query(False)):
    data = await forecast_v5_service.dashboard(force=refresh)
    return {"code": 0, "data": {"scenarios": data.get("scenarios") or {}, "turning_points": data.get("turning_points") or {}, "data_health": data.get("data_health") or {}}}


@router.get("/forecast/sectors")
async def get_forecast_sectors(refresh: bool = Query(False)):
    return {"code": 0, "data": await forecast_v5_service.sectors(force=refresh)}


@router.get("/forecast/stocks")
async def get_forecast_stocks(symbol: str | None = Query(None), refresh: bool = Query(False)):
    return {"code": 0, "data": await forecast_v5_service.stocks(symbol=symbol, force=refresh)}


@router.get("/forecast/alpha-seeds")
async def get_forecast_alpha_seeds(refresh: bool = Query(False)):
    data = await forecast_v5_service.stocks(force=refresh)
    return {"code": 0, "data": {"version": data.get("version"), "data_cutoff_time": data.get("data_cutoff_time"), "seeds": data.get("stocks") or [], "count": data.get("count", 0), "rule": "只显示潜在标的苗头，不显示推荐买入；需等待板块、市场和个股确认条件。", "data_health": data.get("data_health")}}


@router.get("/factors/market")
async def get_market_factors(refresh: bool = Query(False)):
    return {"code": 0, "data": await forecast_v5_service.factors(kind="market", force=refresh)}


@router.get("/factors/sectors/{sector}")
async def get_sector_factors(sector: str, refresh: bool = Query(False)):
    data = await forecast_v5_service.sectors(force=refresh)
    items = [item for item in data.get("sectors") or [] if item.get("code") == sector or item.get("name") == sector]
    return {"code": 0, "data": {"sector": sector, "factors": items, "data_cutoff_time": data.get("data_cutoff_time"), "data_health": data.get("data_health")}}


@router.get("/factors/stocks/{symbol}")
async def get_stock_factors(symbol: str, refresh: bool = Query(False)):
    return {"code": 0, "data": await forecast_v5_service.stocks(symbol=symbol, force=refresh)}


@router.get("/factors/changes")
async def get_factor_changes(refresh: bool = Query(False)):
    data = await forecast_v5_service.factors(kind="market", force=refresh)
    changes = sorted(data.get("factors") or [], key=lambda item: abs(item.get("delta") or 0), reverse=True)
    return {"code": 0, "data": {"changes": changes, "count": len(changes), "data_health": data.get("data_health")}}


@router.get("/causal/chains")
async def get_causal_chains(refresh: bool = Query(False)):
    return {"code": 0, "data": await forecast_v5_service.chains(force=refresh)}


@router.get("/causal/chains/{chain_id}")
async def get_causal_chain(chain_id: str, refresh: bool = Query(False)):
    if not refresh and causal_chain(chain_id) is None:
        raise HTTPException(status_code=404, detail="因果链不存在")
    data = await forecast_v5_service.chains(chain_id=chain_id, force=refresh)
    if not data.get("chains"):
        raise HTTPException(status_code=404, detail="因果链不存在")
    return {"code": 0, "data": data}


@router.get("/causal/active")
async def get_active_causal_chains(refresh: bool = Query(False)):
    return {"code": 0, "data": await forecast_v5_service.chains(active=True, force=refresh)}


@router.get("/history/regimes")
async def get_history_regimes():
    return {"code": 0, "data": await forecast_v5_service.history()}


@router.get("/history/similar")
async def get_history_similar(refresh: bool = Query(False)):
    return {"code": 0, "data": await forecast_v5_service.history(similar=True, force=refresh)}


@router.get("/data/health")
async def get_forecast_data_health(refresh: bool = Query(False)):
    return {"code": 0, "data": await forecast_v5_service.health(force=refresh)}


@router.get("/data/conflicts")
async def get_forecast_data_conflicts(limit: int = Query(100, ge=1, le=500)):
    return {"code": 0, "data": await forecast_v5_service.conflicts(limit=limit)}


@router.get("/data/freshness")
async def get_forecast_data_freshness(refresh: bool = Query(False)):
    return {"code": 0, "data": await forecast_v5_service.freshness(force=refresh)}


@router.get("/forecast/registry")
async def get_forecast_registry():
    return {"code": 0, "data": {"factors": factor_definitions(), "chains": await forecast_v5_service.chains()}}
