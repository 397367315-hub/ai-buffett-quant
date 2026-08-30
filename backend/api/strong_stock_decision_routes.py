"""HTTP API for the independent three-book strong-stock decision module."""

from __future__ import annotations

from datetime import date
import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from strong_stock_decision.service import strong_stock_decision_service


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/strong-stock-decision",
    tags=["强势股交易决策"],
)

# V2 is mounted separately from the V1-compatible router.  The same router is
# included under both paths in ``main.py`` so existing /api/v1 clients and the
# documented /api clients can use the new contract.
v2_router = APIRouter(tags=["强势股交易决策 V2"])


def _date(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field}必须使用YYYY-MM-DD格式") from exc


async def _evaluate(symbol: str, *, refresh: bool = False, as_of: str | None = None) -> dict[str, Any]:
    try:
        return await strong_stock_decision_service.evaluate(
            symbol,
            as_of=_date(as_of, "as_of"),
            force=refresh,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Strong-stock decision evaluation failed for %s", symbol)
        raise HTTPException(status_code=503, detail="强势股交易决策暂时不可用，请稍后重试") from exc


@router.get("/feature-status")
async def feature_status():
    enabled = bool(strong_stock_decision_service._enabled())
    return {
        "code": 0,
        "data": {
            "enabled": enabled,
            "status": "ENABLED" if enabled else "DISABLED",
            "module_id": "STRONG_STOCK_DECISION_V1",
            "mode": "SHADOW",
            "message": None if enabled else "FEATURE_STRONG_STOCK_DECISION 已关闭",
        },
    }


@router.get("/registry")
async def registry(book: str | None = Query(None, max_length=120)):
    return {"code": 0, "data": await strong_stock_decision_service.registry(book=book)}


@router.get("/{symbol}/overview")
async def overview(symbol: str, refresh: bool = Query(False), as_of: str | None = Query(None)):
    return {"code": 0, "data": await _evaluate(symbol, refresh=refresh, as_of=as_of)}


@router.get("/{symbol}/hunter")
async def hunter(symbol: str, refresh: bool = Query(False), as_of: str | None = Query(None)):
    result = await _evaluate(symbol, refresh=refresh, as_of=as_of)
    return {"code": 0, "data": {
        "symbol": result.get("symbol"),
        "trade_date": result.get("trade_date"),
        "quantity_time_space": result.get("quantity_time_space"),
        "main_force": result.get("main_force"),
        "volume_price_ma": result.get("volume_price_ma"),
        "best_trading_zone": result.get("best_trading_zone"),
        "profit_patterns": result.get("profit_patterns") or [],
        "sell_signals": result.get("sell_signals") or [],
        "signals": [item for item in result.get("signals") or [] if str(item.get("skill_id", "")).startswith("HQS_")],
    }}


@router.get("/{symbol}/big-pattern")
async def big_pattern(symbol: str, refresh: bool = Query(False), as_of: str | None = Query(None)):
    result = await _evaluate(symbol, refresh=refresh, as_of=as_of)
    return {"code": 0, "data": {"symbol": result.get("symbol"), "trade_date": result.get("trade_date"), "patterns": result.get("big_patterns") or [], "bars": result.get("bars") or []}}


@router.get("/{symbol}/star")
async def star(symbol: str, refresh: bool = Query(False), as_of: str | None = Query(None)):
    result = await _evaluate(symbol, refresh=refresh, as_of=as_of)
    return {"code": 0, "data": {"symbol": result.get("symbol"), "trade_date": result.get("trade_date"), "stars": result.get("rising_stars") or [], "bars": result.get("bars") or []}}


@router.get("/{symbol}/main-force")
async def main_force(symbol: str, refresh: bool = Query(False), as_of: str | None = Query(None)):
    result = await _evaluate(symbol, refresh=refresh, as_of=as_of)
    return {"code": 0, "data": {"symbol": result.get("symbol"), "trade_date": result.get("trade_date"), "main_force": result.get("main_force"), "signals": [item for item in result.get("signals") or [] if item.get("skill_id") in {"HQS_003", "HQS_004"}]}}


@router.get("/{symbol}/stacking")
async def stacking(symbol: str, refresh: bool = Query(False), as_of: str | None = Query(None)):
    result = await _evaluate(symbol, refresh=refresh, as_of=as_of)
    return {"code": 0, "data": {"symbol": result.get("symbol"), "trade_date": result.get("trade_date"), "stacking": result.get("volume_energy_stacking"), "topic_confirmation": result.get("topic_confirmation"), "signals": result.get("signals") or []}}


@router.get("/{symbol}/cases")
async def cases(symbol: str):
    try:
        return {"code": 0, "data": await strong_stock_decision_service.cases(symbol)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Strong-stock cases failed for %s", symbol)
        raise HTTPException(status_code=503, detail="历史案例暂时不可用") from exc


@router.get("/{symbol}/intraday")
async def intraday(symbol: str, refresh: bool = Query(False)):
    try:
        return {"code": 0, "data": await strong_stock_decision_service.intraday(symbol, force=refresh)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Strong-stock intraday failed for %s", symbol)
        raise HTTPException(status_code=503, detail="盘中验证暂时不可用") from exc


@router.get("/{symbol}/explanation")
async def explanation(symbol: str, refresh: bool = Query(False), as_of: str | None = Query(None)):
    result = await _evaluate(symbol, refresh=refresh, as_of=as_of)
    return {"code": 0, "data": {"symbol": result.get("symbol"), "trade_date": result.get("trade_date"), "explanation": result.get("explanation") or {}, "decision": result.get("decision") or {}, "source_status": result.get("source_status") or {}, "mode": result.get("mode", "SHADOW")}}


@router.get("/{symbol}/timeline")
async def timeline(symbol: str, limit: int = Query(100, ge=1, le=500)):
    try:
        return {"code": 0, "data": await strong_stock_decision_service.timeline(symbol, limit=limit)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Strong-stock timeline failed for %s", symbol)
        raise HTTPException(status_code=503, detail="决策历史暂时不可用") from exc


@router.post("/internal/evaluate")
async def internal_evaluate(payload: dict[str, Any] = Body(default_factory=dict)):
    symbol = str(payload.get("symbol") or "")
    if not symbol:
        raise HTTPException(status_code=422, detail="symbol不能为空")
    return {"code": 0, "data": await _evaluate(symbol, refresh=bool(payload.get("refresh")), as_of=payload.get("as_of"))}


@router.post("/internal/replay")
async def internal_replay(payload: dict[str, Any] = Body(default_factory=dict)):
    symbol = str(payload.get("symbol") or "")
    target = _date(payload.get("trade_date"), "trade_date")
    if not symbol or target is None:
        raise HTTPException(status_code=422, detail="symbol和trade_date不能为空")
    return {"code": 0, "data": await _evaluate(symbol, refresh=True, as_of=target.isoformat())}


@router.post("/internal/backtest")
async def internal_backtest(payload: dict[str, Any] = Body(default_factory=dict)):
    symbol = str(payload.get("symbol") or "")
    if not symbol:
        raise HTTPException(status_code=422, detail="symbol不能为空")
    try:
        horizons = payload.get("horizons")
        result = await strong_stock_decision_service.backtest(
            symbol,
            skill_id=payload.get("skill_id"),
            start=_date(payload.get("start"), "start"),
            end=_date(payload.get("end"), "end"),
            horizons=horizons if isinstance(horizons, list) else None,
        )
        return {"code": 0, "data": result}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Strong-stock backtest failed for %s", symbol)
        raise HTTPException(status_code=503, detail="Shadow回测暂时不可用") from exc


def _v2_slice(result: dict[str, Any], key: str) -> dict[str, Any]:
    v2 = result.get("v2") or {}
    return {
        "module_id": v2.get("module_id", "STRONG_STOCK_DECISION_V2"),
        "engine_version": v2.get("engine_version", "STRONG_STOCK_DECISION_V2"),
        "mode": v2.get("mode", "SHADOW"),
        "symbol": result.get("symbol"),
        "name": result.get("name"),
        "trade_date": result.get("trade_date"),
        "data_cutoff_time": result.get("data_cutoff_time"),
        "source_status": result.get("source_status") or {},
        "data_quality": v2.get("data_quality") or {},
        key: v2.get(key),
    }


async def _v2_evaluate(symbol: str, *, refresh: bool = False, as_of: str | None = None) -> dict[str, Any]:
    return await _evaluate(symbol, refresh=refresh, as_of=as_of)


@v2_router.get("/registry")
async def v2_registry(book: str | None = Query(None, max_length=120)):
    result = await strong_stock_decision_service.registry(book=book)
    return {"code": 0, "data": {**result, "module_id": "STRONG_STOCK_DECISION_V2", "v2_enabled": strong_stock_decision_service._v2_enabled(), "v2_skills": result.get("v2_skills") or []}}


@v2_router.get("/feature-status")
async def v2_feature_status():
    enabled = strong_stock_decision_service._v2_enabled()
    return {
        "code": 0,
        "data": {
            "enabled": enabled,
            "status": "ENABLED" if enabled else "DISABLED",
            "module_id": "STRONG_STOCK_DECISION_V2",
            "mode": "SHADOW",
            "message": None if enabled else "FEATURE_STRONG_STOCK_DECISION_V2 已关闭",
        },
    }


@v2_router.get("/rule-config")
async def v2_rule_config():
    return {"code": 0, "data": strong_stock_decision_service.rule_config()}


@v2_router.get("/{symbol}/overview")
async def v2_overview(symbol: str, refresh: bool = Query(False), as_of: str | None = Query(None)):
    result = await _v2_evaluate(symbol, refresh=refresh, as_of=as_of)
    return {"code": 0, "data": result.get("v2") or {"status": "UNAVAILABLE", "legacy": result}}


def _make_v2_slice_route(key: str):
    async def endpoint(symbol: str, refresh: bool = Query(False), as_of: str | None = Query(None)):
        result = await _v2_evaluate(symbol, refresh=refresh, as_of=as_of)
        return {"code": 0, "data": _v2_slice(result, key)}

    endpoint.__name__ = f"v2_{key.replace('-', '_')}"
    return endpoint


for _key in (
    "risk", "quantity_time_space", "main_force", "volume_price", "moving_average",
    "zones", "three_degree", "big_patterns", "stars", "profit_patterns",
    "stock_character", "stacking", "theme", "buy_point", "sell",
    "consensus", "explanation",
):
    v2_router.add_api_route(
        f"/{{symbol}}/{_key.replace('_', '-')}",
        _make_v2_slice_route(_key),
        methods=["GET"],
        name=f"v2_{_key}",
    )


@v2_router.get("/{symbol}/ma")
async def v2_moving_average_alias(
    symbol: str,
    refresh: bool = Query(False),
    as_of: str | None = Query(None),
):
    """Compatibility alias for clients using the short MA resource name."""
    result = await _v2_evaluate(symbol, refresh=refresh, as_of=as_of)
    return {"code": 0, "data": _v2_slice(result, "moving_average")}


@v2_router.get("/{symbol}/wang-xing-kong")
async def v2_wang_xing_kong(symbol: str, as_of: str | None = Query(None)):
    try:
        return {"code": 0, "data": await strong_stock_decision_service.wang_xing_kong(symbol, as_of=_date(as_of, "as_of"))}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("V2 wang-xing-kong failed for %s", symbol)
        raise HTTPException(status_code=503, detail="历史案例对照暂时不可用") from exc


@v2_router.get("/{symbol}/timeline")
async def v2_timeline(
    symbol: str,
    limit: int = Query(80, ge=1, le=180),
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    """Return a causal daily V2 history, independent of page-open frequency."""
    try:
        return {"code": 0, "data": await strong_stock_decision_service.research_history(
            symbol,
            limit=limit,
            start=_date(start, "start"),
            end=_date(end, "end"),
        )}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("V2 timeline failed for %s", symbol)
        raise HTTPException(status_code=503, detail="历史轨迹暂时不可用") from exc


@v2_router.get("/{symbol}/history")
async def v2_history(
    symbol: str,
    limit: int = Query(80, ge=1, le=180),
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    """Alias for clients that call the P1 feature history rather than timeline."""
    return await v2_timeline(symbol, limit=limit, start=start, end=end)


@v2_router.post("/internal/evaluate")
async def v2_internal_evaluate(payload: dict[str, Any] = Body(default_factory=dict)):
    symbol = str(payload.get("symbol") or "")
    if not symbol:
        raise HTTPException(status_code=422, detail="symbol不能为空")
    result = await _v2_evaluate(symbol, refresh=bool(payload.get("refresh")), as_of=payload.get("as_of"))
    return {"code": 0, "data": result.get("v2") or {"status": "UNAVAILABLE"}}


@v2_router.post("/internal/replay")
async def v2_internal_replay(payload: dict[str, Any] = Body(default_factory=dict)):
    symbol = str(payload.get("symbol") or "")
    target = _date(payload.get("trade_date"), "trade_date")
    if not symbol or target is None:
        raise HTTPException(status_code=422, detail="symbol和trade_date不能为空")
    result = await _v2_evaluate(symbol, refresh=True, as_of=target.isoformat())
    return {"code": 0, "data": result.get("v2") or {"status": "UNAVAILABLE"}}


@v2_router.post("/internal/backtest")
async def v2_internal_backtest(payload: dict[str, Any] = Body(default_factory=dict)):
    symbol = str(payload.get("symbol") or "")
    if not symbol:
        raise HTTPException(status_code=422, detail="symbol不能为空")
    try:
        result = await strong_stock_decision_service.backtest(
            symbol,
            skill_id=payload.get("skill_id"),
            start=_date(payload.get("start"), "start"),
            end=_date(payload.get("end"), "end"),
            horizons=payload.get("horizons") if isinstance(payload.get("horizons"), list) else None,
        )
        return {"code": 0, "data": {**result, "engine_version": "STRONG_STOCK_DECISION_V2", "mode": "SHADOW", "promotion": "DISABLED_UNTIL_VALIDATED"}}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("V2 strong-stock backtest failed for %s", symbol)
        raise HTTPException(status_code=503, detail="V2 Shadow回测暂时不可用") from exc


@v2_router.post("/internal/rebuild-case-library")
async def v2_rebuild_case_library(payload: dict[str, Any] = Body(default_factory=dict)):
    symbol = str(payload.get("symbol") or "") or None
    # Case labels are human/audit inputs. This endpoint intentionally reports
    # the current labelled inventory instead of inventing success cases.
    try:
        result = await strong_stock_decision_service.case_library_status(symbol)
        return {"code": 0, "data": result}
    except Exception as exc:
        logger.exception("V2 case-library status failed")
        raise HTTPException(status_code=503, detail="案例库状态暂时不可用") from exc
