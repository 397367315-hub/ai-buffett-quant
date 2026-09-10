"""Short-lived signed admin sessions for private API surfaces."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from config import settings


TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signature(payload: str) -> str:
    secret = f"{settings.admin_username}:{settings.admin_password}".encode("utf-8")
    return hmac.new(secret, payload.encode("ascii"), hashlib.sha256).hexdigest()


def create_admin_token(username: str, *, now: int | None = None) -> str:
    issued_at = int(time.time() if now is None else now)
    claims = json.dumps(
        {"sub": username, "iat": issued_at, "exp": issued_at + TOKEN_TTL_SECONDS},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    payload = _encode(claims)
    return f"{payload}.{_signature(payload)}"


def verify_admin_token(token: str, *, now: int | None = None) -> str | None:
    try:
        payload, supplied_signature = token.split(".", 1)
        if not payload or not supplied_signature:
            return None
        if not hmac.compare_digest(supplied_signature, _signature(payload)):
            return None
        claims = json.loads(_decode(payload))
        if not isinstance(claims, dict):
            return None
        username = str(claims.get("sub") or "")
        issued_at = int(claims.get("iat") or 0)
        expires_at = int(claims.get("exp") or 0)
    except (ValueError, TypeError, OverflowError, UnicodeError, binascii.Error, json.JSONDecodeError):
        return None
    current_time = int(time.time() if now is None else now)
    if (
        username != settings.admin_username
        or issued_at <= 0
        or expires_at <= issued_at
        or expires_at <= current_time
    ):
        return None
    return username


def require_admin(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    return _require_admin_token(authorization)


def _require_admin_token(authorization: str | None) -> str:
    scheme, _, token = str(authorization or "").partition(" ")
    username = verify_admin_token(token) if scheme.lower() == "bearer" and token else None
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username


async def require_admin_for_mutation(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Protect every mutating route in a mixed public/read router.

    Read requests retain their existing access semantics. The login endpoint
    is the only mutating route that is intentionally public; all other
    mutations are validated through the same token checker as ``require_admin``.
    """
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return ""
    if request.url.path.rstrip("/") in {"/api/v1/auth/login", "/api/auth/login"}:
        return ""
    return _require_admin_token(authorization)
