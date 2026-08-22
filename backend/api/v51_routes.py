"""V5.1 microstructure API.  Numeric outputs come from deterministic rules."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, select

from database import async_session
from models import V51EngineSnapshot
from services.v51_microstructure_service import v51_microstructure_service


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["V5.1微结构"])


async def _run(call, message: str):
    try:
        return {"code": 0, "data": await call}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("V5.1 endpoint failed: %s", message)
        raise HTTPException(status_code=503, detail=f"{message}，请稍后重试") from exc


@router.get("/v51/dashboard")
async def v51_dashboard(refresh: bool = Query(False)):
    auction, reward, leadership = await __import__("asyncio").gather(
        v51_microstructure_service.auction_dashboard(refresh=refresh),
        v51_microstructure_service.reward_punishment(refresh=refresh),
        v51_microstructure_service.leadership_sectors(refresh=refresh),
    )
    return {"code": 0, "data": {"auction": auction, "reward_punishment": reward, "leadership": leadership, "model_version": "v5.1-contract-1"}}


@router.get("/v51/diagnose/{symbol}")
async def v51_diagnose(symbol: str, refresh: bool = Query(False), as_of: str | None = Query(None)):
    from datetime import date

    target = None
    if as_of:
        try:
            target = date.fromisoformat(as_of)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="as_of必须使用YYYY-MM-DD格式") from exc
    return await _run(v51_microstructure_service.diagnose(symbol, refresh=refresh, as_of=target), "V5.1个股诊断不可用")


@router.get("/auction/dashboard")
async def auction_dashboard(refresh: bool = Query(False)):
    return await _run(v51_microstructure_service.auction_dashboard(refresh=refresh), "竞价总览不可用")


@router.get("/auction/{symbol}/timeline")
async def auction_timeline(symbol: str, refresh: bool = Query(False)):
    data = await _run(v51_microstructure_service.diagnose(symbol, refresh=refresh), "竞价时间线不可用")
    payload = data["data"]
    auction = payload.get("engines", {}).get("auction_microstructure", {})
    return {"code": 0, "data": {"symbol": payload.get("symbol"), "timeline": auction.get("features", []), "transition": auction.get("transition"), "quality": auction.get("quality"), "model_version": auction.get("model_version")}}


@router.get("/auction/{symbol}")
async def auction_symbol(symbol: str, refresh: bool = Query(False)):
    data = await _run(v51_microstructure_service.diagnose(symbol, refresh=refresh), "个股竞价微结构不可用")
    return {"code": 0, "data": {"symbol": data["data"].get("symbol"), "auction": data["data"].get("engines", {}).get("auction_microstructure"), "quality": data["data"].get("quality")}}


@router.get("/expectation-deviation/scan")
async def expectation_scan(limit: int = Query(30, ge=1, le=80), refresh: bool = Query(False)):
    return await _run(v51_microstructure_service.scan(engine="expectation_deviation", limit=limit, refresh=refresh), "预期差扫描不可用")


@router.get("/expectation-deviation/{symbol}")
async def expectation_symbol(symbol: str, refresh: bool = Query(False)):
    data = await _run(v51_microstructure_service.diagnose(symbol, refresh=refresh), "个股预期差不可用")
    return {"code": 0, "data": data["data"].get("engines", {}).get("expectation_deviation", {})}


@router.get("/disagreement/scan")
async def disagreement_scan(limit: int = Query(30, ge=1, le=80), refresh: bool = Query(False)):
    return await _run(v51_microstructure_service.scan(engine="disagreement_absorption", limit=limit, refresh=refresh), "分歧消化扫描不可用")


@router.get("/disagreement/{symbol}")
async def disagreement_symbol(symbol: str, refresh: bool = Query(False)):
    data = await _run(v51_microstructure_service.diagnose(symbol, refresh=refresh), "个股分歧消化不可用")
    return {"code": 0, "data": data["data"].get("engines", {}).get("disagreement_absorption", {})}


@router.get("/supply-test/scan")
async def supply_scan(limit: int = Query(30, ge=1, le=80), refresh: bool = Query(False)):
    return await _run(v51_microstructure_service.scan(engine="supply_test", limit=limit, refresh=refresh), "供给测试扫描不可用")


@router.get("/supply-test/{symbol}")
async def supply_symbol(symbol: str, refresh: bool = Query(False)):
    data = await _run(v51_microstructure_service.diagnose(symbol, refresh=refresh), "个股供给测试不可用")
    return {"code": 0, "data": data["data"].get("engines", {}).get("supply_test", {})}


@router.get("/leadership/sectors")
async def leadership_sectors(refresh: bool = Query(False)):
    return await _run(v51_microstructure_service.leadership_sectors(refresh=refresh), "板块领导力不可用")


@router.get("/leadership/{sector}/stocks")
async def leadership_stocks(sector: str, refresh: bool = Query(False)):
    return await _run(v51_microstructure_service.leadership_sector(sector, refresh=refresh), "板块成分领导力不可用")


@router.get("/leadership/{sector}")
async def leadership_sector(sector: str, refresh: bool = Query(False)):
    return await _run(v51_microstructure_service.leadership_sector(sector, refresh=refresh), "板块领导力不可用")


@router.get("/catalyst/{value}")
async def catalyst(value: str, refresh: bool = Query(False)):
    return await _run(v51_microstructure_service.catalyst(value, refresh=refresh), "题材受益纯度不可用")


@router.get("/liquidity-map/{symbol}")
async def liquidity_map(symbol: str, refresh: bool = Query(False)):
    data = await _run(v51_microstructure_service.diagnose(symbol, refresh=refresh), "流动性地图不可用")
    return {"code": 0, "data": data["data"].get("engines", {}).get("liquidity_map", {})}


@router.get("/candlestick-semantic/{symbol}/history")
async def candlestick_history(symbol: str, limit: int = Query(30, ge=1, le=120)):
    try:
        rows = []
        async with async_session() as session:
            rows = list((await session.execute(
                select(V51EngineSnapshot).where(
                    V51EngineSnapshot.stock_code == symbol,
                    V51EngineSnapshot.engine_id == "candlestick_semantic",
                ).order_by(desc(V51EngineSnapshot.data_cutoff_time)).limit(limit)
            )).scalars().all())
        return {"code": 0, "data": {"symbol": symbol, "history": [{"data_cutoff_time": row.data_cutoff_time.isoformat(), "status": row.status, "coverage_pct": row.coverage_pct, "payload": row.payload} for row in rows]}}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="K线语义历史不可用") from exc


@router.get("/candlestick-semantic/{symbol}")
async def candlestick_symbol(symbol: str, refresh: bool = Query(False)):
    data = await _run(v51_microstructure_service.diagnose(symbol, refresh=refresh), "K线语义不可用")
    return {"code": 0, "data": data["data"].get("engines", {}).get("candlestick_semantic", {})}


@router.get("/market/reward-punishment")
async def market_reward_punishment(refresh: bool = Query(False)):
    return await _run(v51_microstructure_service.reward_punishment(refresh=refresh), "市场奖惩效应不可用")


@router.get("/intraday-validation/{symbol}")
async def intraday_validation(symbol: str, refresh: bool = Query(False)):
    data = await _run(v51_microstructure_service.diagnose(symbol, refresh=refresh), "分时相对强弱不可用")
    return {"code": 0, "data": data["data"].get("engines", {}).get("intraday_relative_strength", {})}


@router.get("/regulatory-risk/{symbol}")
async def regulatory_risk(symbol: str, refresh: bool = Query(False)):
    return await _run(v51_microstructure_service.regulatory(symbol, refresh=refresh), "监管风险不可用")
