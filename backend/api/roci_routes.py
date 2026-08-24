"""Public ROCI V1 API and the documented legacy-prefix alias."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from roci.service import roci_service


router = APIRouter(prefix="/api/v1/roci", tags=["ROCI风险机会认知"])
legacy_router = APIRouter(prefix="/api/roci", tags=["ROCI风险机会认知"])


@router.get("/status")
async def roci_status():
    return {"code": 0, "data": await roci_service.status()}


@router.get("/dashboard")
async def roci_dashboard(refresh: bool = Query(False)):
    return {"code": 0, "data": await roci_service.dashboard(force=refresh)}


@router.get("/skills")
async def roci_skills(
    status: str | None = Query(None),
    category: str | None = Query(None),
    refresh: bool = Query(False),
):
    return {"code": 0, "data": await roci_service.skills(force=refresh, status=status, category=category)}


@router.get("/skills/{skill_id}")
async def roci_skill(skill_id: str, refresh: bool = Query(False)):
    data = await roci_service.skill_detail(skill_id, force=refresh)
    if data.get("error"):
        raise HTTPException(status_code=404, detail="ROCI Skill不存在")
    return {"code": 0, "data": data}


@router.get("/skills/{skill_id}/runs")
async def roci_skill_runs(skill_id: str, refresh: bool = Query(False)):
    data = await roci_service.skill_detail(skill_id, force=refresh)
    if data.get("error"):
        raise HTTPException(status_code=404, detail="ROCI Skill不存在")
    return {"code": 0, "data": {"skill_id": skill_id, "runs": data.get("runs") or []}}


@router.get("/skills/{skill_id}/performance")
async def roci_skill_performance(skill_id: str, refresh: bool = Query(False)):
    data = await roci_service.skill_detail(skill_id, force=refresh)
    if data.get("error"):
        raise HTTPException(status_code=404, detail="ROCI Skill不存在")
    return {"code": 0, "data": {"skill_id": skill_id, **(data.get("performance") or {})}}


@router.get("/battlefield")
async def roci_battlefield(refresh: bool = Query(False)):
    return {"code": 0, "data": await roci_service.battlefield(force=refresh)}


@router.get("/forces")
async def roci_forces(refresh: bool = Query(False)):
    return {"code": 0, "data": await roci_service.forces(force=refresh)}


@router.get("/contradiction")
async def roci_contradiction(refresh: bool = Query(False)):
    return {"code": 0, "data": await roci_service.contradiction(force=refresh)}


@router.get("/risk-pricing")
async def roci_risk_pricing(refresh: bool = Query(False)):
    return {"code": 0, "data": await roci_service.risk_pricing(force=refresh)}


@router.get("/stress-tests")
async def roci_stress_tests(symbol: str | None = Query(None), refresh: bool = Query(False)):
    return {"code": 0, "data": await roci_service.stress_tests(force=refresh, symbol=symbol)}


@router.get("/cognitive-risk")
async def roci_cognitive_risk(refresh: bool = Query(False)):
    return {"code": 0, "data": await roci_service.cognitive_risk(force=refresh)}


@router.get("/opportunities")
async def roci_opportunities(refresh: bool = Query(False)):
    return {"code": 0, "data": await roci_service.opportunities(force=refresh)}


@router.get("/recommendations")
async def roci_recommendations(refresh: bool = Query(False)):
    """Risk-adapted sector and stock research shortlist."""
    return {"code": 0, "data": await roci_service.recommendations(force=refresh)}


@router.get("/opportunities/{pattern}")
async def roci_opportunity(pattern: str, refresh: bool = Query(False)):
    data = await roci_service.opportunity(pattern, force=refresh)
    if data.get("error"):
        raise HTTPException(status_code=404, detail="机会形态不存在")
    return {"code": 0, "data": data}


@router.get("/stock/{symbol}")
async def roci_stock(symbol: str, refresh: bool = Query(False), as_of: str | None = Query(None)):
    target = None
    if as_of:
        try:
            target = date.fromisoformat(as_of)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="as_of必须使用YYYY-MM-DD格式") from exc
    try:
        return {"code": 0, "data": await roci_service.stock(symbol, force=refresh, as_of=target)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/snapshot")
async def roci_snapshot(payload: dict[str, Any] = Body(default_factory=dict)):
    symbol = payload.get("symbol")
    target = None
    if payload.get("as_of"):
        try:
            target = date.fromisoformat(str(payload["as_of"]))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="as_of必须使用YYYY-MM-DD格式") from exc
    return {"code": 0, "data": await roci_service.build(force=bool(payload.get("refresh", True)), symbol=symbol, as_of=target, persist=True)}


@router.post("/replay")
async def roci_replay(payload: dict[str, Any] = Body(...)):
    try:
        target = date.fromisoformat(str(payload.get("trade_date") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="trade_date必须使用YYYY-MM-DD格式") from exc
    return {"code": 0, "data": await roci_service.replay(symbol=payload.get("symbol"), trade_date=target)}


@router.post("/user-feedback")
async def roci_user_feedback(payload: dict[str, Any] = Body(...)):
    data = await roci_service.feedback(payload)
    if data.get("status") == "REJECTED":
        raise HTTPException(status_code=422, detail=data.get("reason"))
    return {"code": 0, "data": data}


@router.get("/lab/skills")
async def roci_lab_skills():
    return {"code": 0, "data": await roci_service.lab_skills()}


@router.post("/lab/skills/{skill_id}/backtest")
async def roci_lab_backtest(skill_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
    data = await roci_service.backtest_skill(skill_id, payload)
    if data.get("status") == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="ROCI Skill不存在")
    return {"code": 0, "data": data}


@router.post("/lab/skills/{skill_id}/promote")
async def roci_lab_promote(skill_id: str, payload: dict[str, Any] = Body(default_factory=dict)):
    data = await roci_service.promote_skill(skill_id, payload)
    if data.get("status") == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="ROCI Skill不存在")
    return {"code": 0, "data": data}


@router.post("/lab/skills/{skill_id}/disable")
async def roci_lab_disable(skill_id: str):
    data = await roci_service.disable_skill(skill_id)
    if data.get("status") == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="ROCI Skill不存在")
    return {"code": 0, "data": data}


# Keep the exact /api/roci path in the specification while preserving the
# product's established /api/v1 namespace used by apiFetch().
for _route in router.routes:
    if getattr(_route, "path", "").startswith(router.prefix):
        legacy_router.add_api_route(
            _route.path.removeprefix(router.prefix) or "/",
            _route.endpoint,
            methods=list(_route.methods or {"GET"}),
            response_model=_route.response_model,
            name=f"legacy_{_route.name}",
            operation_id=f"legacy_{_route.operation_id}" if _route.operation_id else None,
        )
