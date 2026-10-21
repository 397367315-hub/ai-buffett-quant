"""Authenticated transport adapter for the vendored roundtable API.

The browser keeps only the public panel session id. Its local cookie token
stays server-side, and every request also requires the host's signed login.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from config import settings
from market_data.numcat.gateway import numcat_gateway
from services.admin_auth import require_admin
from services.roundtable_host import get_roundtable_runtime
from services.roundtable_runtime import RoundtableRuntimePersistenceError


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/roundtable", tags=["交易圆桌"], dependencies=[Depends(require_admin)])
MAX_BODY = 24 * 1024 * 1024
SAFE_RESPONSE_HEADERS = {"content-type", "content-disposition", "cache-control", "retry-after", "x-request-id"}
TERMINAL_JOB_STATES = {"succeeded", "partial", "failed", "cancelled"}


def _terminal_job_response(body: bytes) -> bool:
    try:
        payload = json.loads(body.decode("utf-8"))
        job = payload.get("job") if isinstance(payload, dict) else None
        state = job.get("state") if isinstance(job, dict) else payload.get("state") if isinstance(payload, dict) else None
        return state in TERMINAL_JOB_STATES
    except (ValueError, UnicodeDecodeError, AttributeError):
        return False


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": message, "details": [], "retryable": status >= 500,
    }}, headers={"Cache-Control": "no-store"})


def trusted_origin(origin: str) -> bool:
    if origin == "https://ai-buffett-quant.netlify.app":
        return True
    if re.fullmatch(r"https://[a-z0-9-]+--ai-buffett-quant\.netlify\.app", origin):
        return True
    parsed = urlsplit(origin)
    return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"} and not parsed.username


@router.get("/host/status")
async def roundtable_host_status():
    try:
        runtime = await get_roundtable_runtime()
        from panel.domain.seat_registry import registry_payload
        skills_root = runtime.asset_root / "skills" if runtime.asset_root else runtime.vendor_root / "skills"
        payload = registry_payload(skills_root)
        return {"code": 0, "data": {
            "name": "交易圆桌", "upstream_release": "rc-20261022-84ff309c13b8",
            "ready": not runtime.unavailable, "seat_count": len(payload.get("seats") or []),
            "persistence": {"mode": "host_database_checkpoint", "revision": runtime.revision,
                            "available": not runtime.unavailable},
            "shared_providers": {"numcat_configured": numcat_gateway.configured,
                                 "deepseek_configured": bool(settings.deepseek_api_key),
                                 "deepseek_model": settings.deepseek_model},
            "capabilities": {"research": True, "simulation": True, "actual_trading": False,
                             "alerts": False, "subscriptions": False},
        }}
    except Exception as exc:
        logger.error("Roundtable status unavailable: %s", type(exc).__name__)
        return error(503, "ROUNDTABLE_NOT_READY", "交易圆桌尚未就绪，请检查资料与持久化配置")


@router.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
async def roundtable_gateway(path: str, request: Request):
    if not path or any(piece == ".." for piece in path.split("/")) or "\\" in path or "\x00" in path:
        return error(400, "INVALID_PATH", "交易圆桌请求路径无效")
    origin = request.headers.get("origin")
    if origin and not trusted_origin(origin):
        return error(403, "ORIGIN_NOT_ALLOWED", "来源页面不在系统允许范围内")
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > MAX_BODY):
        return error(413, "PAYLOAD_TOO_LARGE", "请求超过交易圆桌导入上限")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            return error(413, "PAYLOAD_TOO_LARGE", "请求超过交易圆桌导入上限")
    try:
        runtime = await get_roundtable_runtime()
        from panel.web.app import Request as PanelRequest
        internal_headers = {name: value for name, value in request.headers.items()
                            if name.lower() in {"content-type", "x-csrf-token", "idempotency-key", "x-replay-id", "accept"}}
        internal_headers.update({"host": "127.0.0.1:8787", "origin": "http://127.0.0.1:8787",
                                 "sec-fetch-site": "same-origin", "content-length": str(len(body))})
        session_id = request.headers.get("x-roundtable-session", "")
        if session_id:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,180}", session_id):
                return error(401, "UNAUTHENTICATED", "圆桌会话已失效，请重新进入")
            token = runtime.session_tokens.get(session_id)
            if token:
                internal_headers["cookie"] = "rt_local_session=" + token
        target = "/api/" + path
        if request.url.query:
            target += "?" + request.url.query
        panel_request = PanelRequest.build(request.method, target, internal_headers, bytes(body))
        panel_response = await run_in_threadpool(runtime.handle, panel_request)
        # Failed model validations can still spend budget/write an audit, so
        # persist every POST, including an error response. A job's visible
        # terminal result is checkpointed before returning its polling GET.
        force = request.method not in {"GET", "HEAD"}
        if request.method == "GET" and path.startswith("v1/jobs/"):
            # Polls are read-only; only persist the terminal projection so a
            # fast client cannot turn every poll into a full archive upload.
            force = _terminal_job_response(panel_response.body)
        if force:
            runtime.mark_dirty()
        await runtime.flush(force=force)
        headers = {key: value for key, value in panel_response.headers
                   if key.lower() in SAFE_RESPONSE_HEADERS}
        headers["Cache-Control"] = "no-store"
        return Response(content=b"" if request.method == "HEAD" else panel_response.body,
                        status_code=panel_response.status, headers=headers)
    except RoundtableRuntimePersistenceError:
        return error(503, "PERSISTENCE_UNAVAILABLE", "圆桌状态尚未可靠保存，请稍后重试")
    except Exception as exc:
        logger.error("Roundtable request unavailable: %s", type(exc).__name__)
        return error(503, "ROUNDTABLE_UNAVAILABLE", "交易圆桌暂不可用，请稍后重试")
