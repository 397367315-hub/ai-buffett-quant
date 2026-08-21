"""V5 Trading Skill Registry, runtime scan and validation-lab API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from quant.schemas import TradingSkillScanRequest, TradingSkillValidationRequest
from services.trading_skill_registry import (
    get_registered_skill,
    list_rejected_knowledge,
    list_registered_skills,
)
from services.trading_skill_service import trading_skill_service
from services.trading_skill_validation import trading_skill_validation_service


router = APIRouter(prefix="/api/v1/trading-skills", tags=["V5交易技能"])


@router.get("/registry")
async def skill_registry():
    return {"code": 0, "data": {"skills": await list_registered_skills(), "lifecycle": ["IDEA", "EXPERIMENTAL", "SHADOW", "ACTIVE", "DEGRADED", "DEPRECATED"]}}


@router.get("/registry/{skill_id}")
async def skill_registry_detail(skill_id: str):
    result = await get_registered_skill(skill_id)
    if result is None:
        raise HTTPException(status_code=404, detail="交易Skill不存在")
    return {"code": 0, "data": result}


@router.get("/rejected-knowledge")
async def rejected_knowledge():
    return {"code": 0, "data": await list_rejected_knowledge()}


@router.get("/dashboard")
async def skill_dashboard(
    refresh: bool = Query(False),
    exclude_star_market: bool = Query(True, description="排除科创板"),
    exclude_gem: bool = Query(True, description="排除创业板"),
):
    return {"code": 0, "data": await trading_skill_service.dashboard(
        force=refresh,
        exclude_star_market=exclude_star_market,
        exclude_gem=exclude_gem,
    )}


@router.post("/scan", status_code=status.HTTP_202_ACCEPTED)
async def skill_scan(payload: TradingSkillScanRequest):
    result = await trading_skill_service.scan(
        skill_ids=payload.skill_ids or None,
        force=payload.force,
        exclude_star_market=payload.exclude_star_market,
        exclude_gem=payload.exclude_gem,
    )
    return {"code": 0, "data": result}


@router.get("/stock/{stock_code}")
async def skill_stock(
    stock_code: str,
    refresh: bool = Query(False),
    exclude_star_market: bool = Query(True),
    exclude_gem: bool = Query(True),
):
    return {"code": 0, "data": await trading_skill_service.stock(
        stock_code,
        force=refresh,
        exclude_star_market=exclude_star_market,
        exclude_gem=exclude_gem,
    )}


@router.get("/latest")
async def latest_skill_scan():
    return {"code": 0, "data": await trading_skill_service.latest()}


@router.post("/validation", status_code=status.HTTP_202_ACCEPTED)
async def start_skill_validation(payload: TradingSkillValidationRequest):
    job = await trading_skill_validation_service.start(payload.model_dump(mode="json"))
    return {"code": 0, "data": job, "message": "Skill验证任务已提交"}


@router.get("/validation/status/{job_id}")
async def skill_validation_status(job_id: str):
    job = trading_skill_validation_service.job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Skill验证任务不存在")
    return {"code": 0, "data": job}


@router.get("/validation/history")
async def skill_validation_history(
    skill_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    return {"code": 0, "data": await trading_skill_validation_service.latest(skill_id, limit)}
