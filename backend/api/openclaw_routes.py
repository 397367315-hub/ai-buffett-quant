"""OpenClaw-compatible stateless MCP HTTP endpoints."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response, status

from config import settings
from services.admin_auth import verify_admin_token
from services.openclaw_gateway import openclaw_gateway


router = APIRouter(prefix="/api/v1/openclaw", tags=["OpenClaw"])


def _authorize(
    openclaw_key: str | None,
    authorization: str | None,
) -> None:
    if not settings.openclaw_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OpenClaw接口未启用")
    scheme, _, bearer = str(authorization or "").partition(" ")
    provided = str(openclaw_key or "").strip()
    if not provided and scheme.lower() == "bearer":
        provided = bearer.strip()
    expected = str(settings.openclaw_api_key or "").strip()
    if expected:
        valid = bool(provided) and hmac.compare_digest(provided, expected)
    else:
        # Local development fallback: reuse the existing seven-day admin
        # session until a dedicated production OpenClaw key is configured.
        valid = bool(scheme.lower() == "bearer" and bearer and verify_admin_token(bearer))
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OpenClaw认证失败",
            headers={"WWW-Authenticate": "Bearer"},
        )


AuthKey = Annotated[str | None, Header(alias="X-OpenClaw-Key")]
Authorization = Annotated[str | None, Header()]


@router.get("/manifest")
async def openclaw_manifest(
    openclaw_key: AuthKey = None,
    authorization: Authorization = None,
):
    _authorize(openclaw_key, authorization)
    return {"code": 0, "data": openclaw_gateway.manifest()}


@router.get("/tools")
async def openclaw_tools(
    openclaw_key: AuthKey = None,
    authorization: Authorization = None,
):
    _authorize(openclaw_key, authorization)
    return {"code": 0, "data": {"tools": openclaw_gateway.manifest()["tools"]}}


@router.post("/mcp")
async def openclaw_mcp(
    request: dict,
    response: Response,
    openclaw_key: AuthKey = None,
    authorization: Authorization = None,
):
    _authorize(openclaw_key, authorization)
    result = await openclaw_gateway.handle_rpc(request)
    if result is None:
        response.status_code = status.HTTP_202_ACCEPTED
        return Response(status_code=status.HTTP_202_ACCEPTED)
    return result
