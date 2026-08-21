"""Public V5 forward-forecast API."""

import asyncio
import json

from fastapi import APIRouter, Query, HTTPException

from services.factor_registry_v5 import causal_chain, factor_definition, factor_definitions
from services.forecast_v5 import forecast_v5_service
from services.ai_service import ai_service
from services.macro_dashboard import macro_dashboard_service
from services.market_decision_workbench import market_decision_workbench_service


router = APIRouter(prefix="/api/v1")


@router.get("/forecast/dashboard")
async def get_forecast_dashboard(
    refresh: bool = Query(False),
    exclude_star_market: bool = Query(True, description="排除科创板"),
    exclude_gem: bool = Query(True, description="排除创业板"),
):
    return {"code": 0, "data": await forecast_v5_service.dashboard(
        force=refresh,
        exclude_star_market=exclude_star_market,
        exclude_gem=exclude_gem,
    )}


@router.post("/forecast/event-interpretation")
async def post_forecast_event_interpretation():
    """Explain the event and factor chain from the current verified snapshot.

    This is deliberately on-demand. The dashboard can render its numeric
    state immediately and only spends an AI request when the user asks for a
    trader-style interpretation.
    """
    forecast, workbench, macro = await asyncio.gather(
        forecast_v5_service.dashboard(),
        market_decision_workbench_service.get(),
        macro_dashboard_service.dashboard(),
    )
    observed_factors = [
        {
            "name": item.get("name"),
            "state": item.get("state"),
            "value": item.get("value"),
            "delta": item.get("delta"),
            "layer": item.get("layer"),
            "source": item.get("source"),
            "updated_at": item.get("updated_at"),
        }
        for item in (forecast.get("factors", {}).get("all") or [])
        if item.get("observed")
    ][:18]
    market = {
        "forecast": {
            "generated_at": forecast.get("generated_at"),
            "data_cutoff_time": forecast.get("data_cutoff_time"),
            "phase": forecast.get("phase"),
            "risk_preference": forecast.get("risk_preference"),
            "resonance": forecast.get("resonance"),
            "timeline": forecast.get("timeline"),
            "behavior": forecast.get("behavior"),
            "sector_forecasts": (forecast.get("sector_forecasts") or [])[:8],
            "turning_points": forecast.get("turning_points"),
            "data_health": forecast.get("data_health"),
            "factors": observed_factors,
        },
        "market_state": workbench.get("market_state") or {},
        "market_metrics": workbench.get("headline_metrics") or {},
        "main_lines": (workbench.get("main_lines") or [])[:8],
        "global_markets": (macro.get("global_markets") or [])[:9],
        "macro_indicators": macro.get("macro_indicators") or {},
        "a_share_outlook": macro.get("a_share_outlook") or {},
        "macro_source_status": macro.get("source_status") or {},
        "macro_cache_used": bool(macro.get("cache_used")),
        "macro_snapshot_updated_at": macro.get("snapshot_updated_at"),
        "policy_items": (macro.get("policy", {}).get("international_items") or [])[:4],
    }
    prompt = (
        "你是专业、克制、重视证据的A股资深交易员和市场研究员。"
        "请根据下面的系统快照，写一段事件监控AI解读。只能使用输入中的事实，"
        "必须区分实时行情、最近交易日收盘和缓存快照；不要把隔夜海外收盘当成A股盘中实时。"
        "请按以下五段输出，每段用普通中文标题加换行，不使用Markdown加粗、井号标题、代码围栏或表格："
        "当前事实、因子共振链、主要矛盾、发展方向与风险、下一观察条件。"
        "最后补充一句：仅作研究参考，不构成买卖指令。禁止使用必涨、稳赚、强烈买入、庄家洗盘、主力诱多等表达。"
        "如果数据不足，明确写出不足和原因，不得补造。\n\n系统快照："
        + json.dumps(market, ensure_ascii=False, default=str)[:30000]
    )
    interpretation = await ai_service.generate(prompt, system_prompt=(
        "你只输出普通纯文本中文。禁止使用 **、__、```、### 等Markdown装饰。"
        "结论必须可追溯到给定数据，并披露缓存和数据时效边界。"
    ))
    sources = sorted(set(
        [str(item) for item in (forecast.get("data_health", {}).get("sources") or []) if item]
        + [str(key) for key, value in (macro.get("source_status") or {}).items() if value != "unavailable"]
    ))
    return {"code": 0, "data": {
        "interpretation": interpretation,
        "generated_at": forecast.get("generated_at"),
        "data_cutoff_time": forecast.get("data_cutoff_time"),
        "snapshot_updated_at": macro.get("snapshot_updated_at"),
        "cache_used": bool(forecast.get("cache_used") or macro.get("cache_used")),
        "sources": sources,
    }}


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
