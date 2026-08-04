"""Short-lived signed admin sessions for private API surfaces."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Annotated

from fastapi import Header, HTTPException, status

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
        if not hmac.compare_digest(supplied_signature, _signature(payload)):
            return None
        claims = json.loads(_decode(payload))
        username = str(claims.get("sub") or "")
        expires_at = int(claims.get("exp") or 0)
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError):
        return None
    current_time = int(time.time() if now is None else now)
    if username != settings.admin_username or expires_at < current_time:
        return None
    return username


def require_admin(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    scheme, _, token = str(authorization or "").partition(" ")
    username = verify_admin_token(token) if scheme.lower() == "bearer" and token else None
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username
