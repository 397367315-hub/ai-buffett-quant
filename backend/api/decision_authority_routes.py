"""Read-only cross-module decision authority endpoint."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from services.wildman_decision_authority import read_authority


router = APIRouter(prefix="/api/v1/decision-authority", tags=["统一辅助决策口径"])


@router.get("")
async def get_decision_authority(date_value: str | None = Query(None, alias="date")):
    if date_value:
        try:
            date.fromisoformat(date_value[:10])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="date必须使用YYYY-MM-DD格式") from exc
    try:
        return {"code": 0, "data": await read_authority(date_value)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="统一辅助决策快照暂不可用") from exc
