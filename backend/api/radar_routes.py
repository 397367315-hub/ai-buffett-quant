"""AI real-time event radar API using free/public sources and cache metadata."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from services.ai_service import ai_service
from services.event_radar import event_radar_service


router = APIRouter(prefix="/api/v1/radar", tags=["AI实时事件雷达"])


@router.get("/events")
async def radar_events(
    level: str | None = Query(None, pattern="^[SABCsabc]$"),
    topic: str | None = Query(None, max_length=100),
    event_type: str | None = Query(None, max_length=60),
    status: str | None = Query(None, max_length=40),
    limit: int = Query(50, ge=1, le=200),
    refresh: bool = Query(False),
):
    try:
        return {"code": 0, "data": await event_radar_service.events(level=level, topic=topic, event_type=event_type, status=status, limit=limit, refresh=refresh)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="事件雷达暂时不可用，请稍后重试") from exc


@router.get("/events/{event_id}")
async def radar_event_detail(event_id: str):
    try:
        data = await event_radar_service.detail(event_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="事件详情暂时不可用") from exc
    if data is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    return {"code": 0, "data": data}


@router.post("/events/{event_id}/interpretation")
async def radar_event_interpretation(event_id: str):
    detail = await event_radar_service.detail(event_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    prompt = (
        "你是专业、克制的A股事件研究员。只能根据下面已经验真的结构化事件写普通中文解读。"
        "请说明当前事实、题材传导、市场确认、可能的相反证据和下一观察条件。"
        "区分事实与推断；不能编造公司主营关系，不能修改事件分数，不能输出买卖指令。"
        "不要使用Markdown加粗、井号标题、代码围栏或表格。最后写明仅作研究参考。\n\n"
        + json.dumps(detail, ensure_ascii=False, default=str)[:24000]
    )
    text = await ai_service.generate(prompt, system_prompt="只输出纯文本中文，不使用 **、__、```、### 等装饰符号。")
    cleaned = str(text or "").replace("**", "").replace("__", "").replace("```", "").replace("###", "").strip()
    return {"code": 0, "data": {"event_id": event_id, "interpretation": cleaned, "data_cutoff_time": detail.get("data_cutoff_time"), "sources": [detail.get("source")], "ai_policy": "AI只解释结构化事实，不改变分数。"}}


@router.get("/topics/hot")
async def radar_hot_topics(limit: int = Query(20, ge=1, le=50)):
    try:
        return {"code": 0, "data": await event_radar_service.hot_topics(limit)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="事件题材暂时不可用") from exc


@router.get("/alerts")
async def radar_alerts(limit: int = Query(30, ge=1, le=100)):
    try:
        return {"code": 0, "data": await event_radar_service.alerts(limit)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="事件提醒暂时不可用") from exc


@router.get("/providers/status")
async def radar_provider_status():
    try:
        return {"code": 0, "data": await event_radar_service.providers()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="数据源状态暂时不可用") from exc


@router.get("/replay")
async def radar_replay(start: str | None = Query(None), end: str | None = Query(None), limit: int = Query(100, ge=1, le=500)):
    try:
        return {"code": 0, "data": await event_radar_service.replay(start=start, end=end, limit=limit)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="事件回放暂时不可用") from exc


@router.get("/stream")
async def radar_stream():
    return StreamingResponse(event_radar_service.stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})
