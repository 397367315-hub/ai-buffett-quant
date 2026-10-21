"""Protected, session-bound legacy dashboard refresh controls."""
from __future__ import annotations

import re
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from api.roundtable_routes import trusted_origin
from services.admin_auth import require_admin
from services.roundtable_host import get_roundtable_runtime
from services.roundtable_legacy import roundtable_legacy_refresh

router = APIRouter(prefix="/api/v1/roundtable/host", tags=["交易圆桌旧面板"])


async def _bound_runtime(request: Request, *, write: bool):
    origin = request.headers.get("origin")
    if not origin or not trusted_origin(origin):
        raise HTTPException(status_code=403, detail="来源页面不在系统允许范围内")
    runtime = await get_roundtable_runtime()
    from panel.web import security
    session_id = request.headers.get("x-roundtable-session", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,180}", session_id):
        raise HTTPException(status_code=401, detail="缺少有效的圆桌会话")
    token = runtime.session_tokens.get(session_id)
    session = security.load_session(runtime.db, token)
    if session is None:
        raise HTTPException(status_code=401, detail="圆桌会话已失效")
    if session.get("mode") == "replay":
        raise HTTPException(status_code=409, detail="回放上下文禁止访问 legacy 刷新")
    if write:
        try:
            security.check_csrf({str(k).lower(): v for k, v in request.headers.items()}, session)
        except Exception as exc:
            code = getattr(exc, "code", "CSRF_INVALID")
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    return runtime


@router.post("/refresh", dependencies=[Depends(require_admin)], status_code=202)
async def start_refresh(request: Request, payload: dict | None = None):
    runtime = await _bound_runtime(request, write=True)
    body = payload or {}
    try:
        state = await roundtable_legacy_refresh.start(
            runtime, history_days=body.get("history_days", 130)
        )
        await runtime.flush(force=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"code": 0, "data": state}


@router.get("/refresh/status", dependencies=[Depends(require_admin)])
async def refresh_status(request: Request):
    runtime = await _bound_runtime(request, write=False)
    return {"code": 0, "data": roundtable_legacy_refresh.status(runtime)}
