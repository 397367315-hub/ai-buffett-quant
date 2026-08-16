from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from services.weekend_research import weekend_research_service


router = APIRouter(prefix="/api/v1/research", tags=["AI研究中心"])


class WeeklyResearchRequest(BaseModel):
    mode: str = "quick"
    topic: str | None = Field(default=None, max_length=300)


class TopicResearchRequest(BaseModel):
    question: str = Field(min_length=2, max_length=300)
    mode: str = "topic"


class JudgmentRequest(BaseModel):
    target_type: str
    target_key: str = Field(min_length=1, max_length=100)
    action: str = "APPROVE"
    user_judgment: str | None = Field(default=None, max_length=3000)
    reason: str | None = Field(default=None, max_length=3000)


class JudgmentValidationRequest(BaseModel):
    validation_result: str
    actual_result: str | None = Field(default=None, max_length=3000)
    correct_party: str | None = None


class HypothesisRequest(BaseModel):
    session_id: str
    scope: str = "market"
    target: str | None = Field(default=None, max_length=100)
    title: str = Field(default="用户研究假设", max_length=200)
    statement: str = Field(min_length=2, max_length=5000)
    horizon: str = "T+5"
    evidence: list[str] = Field(default_factory=list)
    falsification: list[str] = Field(default_factory=list)
    due_date: str | None = None


class HypothesisValidationRequest(BaseModel):
    result: str
    actual_result: str = Field(min_length=1, max_length=5000)
    error_type: str | None = Field(default=None, max_length=50)
    lesson: str | None = Field(default=None, max_length=3000)
    correct_party: str | None = None


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    print(f"Research API failed: {type(exc).__name__}: {exc}")
    return HTTPException(status_code=503, detail="研究服务暂时不可用，请稍后重试")


async def _research_or_404(session_id: str) -> dict:
    result = await weekend_research_service.get(session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="研究记录不存在")
    return result


@router.post("/weekly/start", status_code=status.HTTP_202_ACCEPTED)
async def start_weekly_research(request: WeeklyResearchRequest):
    try:
        result = await weekend_research_service.start(mode=request.mode, topic=request.topic)
    except Exception as exc:
        raise _error(exc) from exc
    return {"code": 0, "data": result}


@router.get("/weekly")
async def list_weekly_research(
    limit: int = Query(30, ge=1, le=100),
    research_status: str | None = Query(None, alias="status"),
):
    try:
        rows = await weekend_research_service.list(limit=limit, status=research_status)
    except Exception as exc:
        raise _error(exc) from exc
    return {"code": 0, "data": {"sessions": rows, "count": len(rows)}}


@router.get("/weekly/latest")
async def get_latest_weekly_research():
    return {"code": 0, "data": await weekend_research_service.latest()}


@router.get("/weekly/{session_id}")
async def get_weekly_research(session_id: str):
    return {"code": 0, "data": await _research_or_404(session_id)}


async def _component(session_id: str, component: str) -> dict:
    research = await _research_or_404(session_id)
    report = research.get("report") or {}
    return {
        "code": 0,
        "data": report.get(component),
        "meta": report.get("meta") or {},
        "status": research.get("status"),
    }


@router.get("/weekly/{session_id}/market")
async def get_weekly_market(session_id: str):
    return await _component(session_id, "market_autopsy")


@router.get("/weekly/{session_id}/conflicts")
async def get_weekly_conflicts(session_id: str):
    return await _component(session_id, "conflicts")


@router.get("/weekly/{session_id}/sectors")
async def get_weekly_sectors(session_id: str):
    return await _component(session_id, "sectors")


@router.get("/weekly/{session_id}/stocks")
async def get_weekly_stocks(session_id: str):
    return await _component(session_id, "candidates")


@router.get("/weekly/{session_id}/candidates")
async def get_weekly_candidates(session_id: str):
    return await _component(session_id, "candidates")


@router.get("/weekly/{session_id}/scenarios")
async def get_weekly_scenarios(session_id: str):
    return await _component(session_id, "scenarios")


@router.post("/{session_id}/judgment")
async def save_research_judgment(session_id: str, request: JudgmentRequest):
    try:
        result = await weekend_research_service.save_judgment(session_id, request.model_dump())
    except Exception as exc:
        raise _error(exc) from exc
    return {"code": 0, "data": result}


@router.post("/{session_id}/override")
async def override_research_judgment(session_id: str, request: JudgmentRequest):
    payload = request.model_dump()
    payload["action"] = "MODIFY"
    try:
        result = await weekend_research_service.save_judgment(session_id, payload)
    except Exception as exc:
        raise _error(exc) from exc
    return {"code": 0, "data": result}


@router.post("/{session_id}/reject")
async def reject_research_judgment(session_id: str, request: JudgmentRequest):
    payload = request.model_dump()
    payload["action"] = "REJECT"
    try:
        result = await weekend_research_service.save_judgment(session_id, payload)
    except Exception as exc:
        raise _error(exc) from exc
    return {"code": 0, "data": result}


@router.post("/{session_id}/judgment/{judgment_id}/validate")
async def validate_research_judgment(
    session_id: str,
    judgment_id: int,
    request: JudgmentValidationRequest,
):
    try:
        result = await weekend_research_service.validate_judgment(
            session_id, judgment_id, request.model_dump(),
        )
    except Exception as exc:
        raise _error(exc) from exc
    return {"code": 0, "data": result}


@router.post("/{session_id}/archive")
async def archive_research(session_id: str):
    try:
        result = await weekend_research_service.archive(session_id)
    except Exception as exc:
        raise _error(exc) from exc
    return {"code": 0, "data": result}


@router.post("/hypothesis", status_code=status.HTTP_201_CREATED)
async def create_research_hypothesis(request: HypothesisRequest):
    try:
        result = await weekend_research_service.create_hypothesis(request.model_dump())
    except Exception as exc:
        raise _error(exc) from exc
    return {"code": 0, "data": result}


@router.get("/hypothesis/{hypothesis_id}")
async def get_research_hypothesis(hypothesis_id: int):
    result = await weekend_research_service.get_hypothesis(hypothesis_id)
    if result is None:
        raise HTTPException(status_code=404, detail="研究假设不存在")
    return {"code": 0, "data": result}


@router.post("/hypothesis/{hypothesis_id}/validate")
async def validate_research_hypothesis(
    hypothesis_id: int,
    request: HypothesisValidationRequest,
):
    try:
        result = await weekend_research_service.validate_hypothesis(
            hypothesis_id, request.model_dump(),
        )
    except Exception as exc:
        raise _error(exc) from exc
    return {"code": 0, "data": result}


@router.get("/cases")
async def list_research_cases(limit: int = Query(50, ge=1, le=200)):
    rows = await weekend_research_service.cases(limit=limit)
    return {"code": 0, "data": {"cases": rows, "count": len(rows)}}


@router.get("/cases/{case_id}")
async def get_research_case(case_id: int):
    result = await weekend_research_service.get_case(case_id)
    if result is None:
        raise HTTPException(status_code=404, detail="市场案例不存在")
    return {"code": 0, "data": result}


@router.get("/insights")
async def get_research_insights():
    return {"code": 0, "data": await weekend_research_service.insights()}


@router.post("/topic", status_code=status.HTTP_202_ACCEPTED)
async def start_topic_research(request: TopicResearchRequest):
    try:
        result = await weekend_research_service.start(mode="topic", topic=request.question)
    except Exception as exc:
        raise _error(exc) from exc
    return {"code": 0, "data": result}
