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
