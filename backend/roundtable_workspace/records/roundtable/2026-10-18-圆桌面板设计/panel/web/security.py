"""本地会话、CSRF、Host/Origin 边界（02 §13.2 / 04 §4）。

- 127.0.0.1 不是授权机制：写请求分别校验 Host 白名单、Origin、有效会话与绑定该会话的 CSRF token；
  有效 cookie 或可信 Origin 都不能单独放行写入；
- 会话 cookie：rt_local_session，HttpOnly + SameSite=Strict（HTTPS 时加 Secure）；
  数据库只存 token 的 SHA-256，日志与审计导出不出现 cookie 或 CSRF 值；
- 服务端会话绑定 live / replay 与 knowledge_cutoff_at；X-Replay-Id 只是一致性断言，
  漏了仍按会话截点，伪造或不一致 → 403。
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from http.cookies import CookieError, SimpleCookie

from ..domain import clock
from ..domain.canonical import new_id
from .errors import ApiError

COOKIE_NAME = "rt_local_session"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_cookies(header: str | None) -> dict[str, str]:
    if not header:
        return {}
    c = SimpleCookie()
    try:
        c.load(header)
    except CookieError:
        return {}
    return {k: v.value for k, v in c.items()}


def check_host(headers: dict, allowed: set[str]) -> None:
    host = (headers.get("host") or "").strip().lower()
    if host not in allowed:
        raise ApiError(403, "HOST_NOT_ALLOWED", "Host 不在本机允许列表内")


def check_origin(headers: dict, allowed: set[str]) -> None:
    origin = headers.get("origin")
    if origin is None:
        raise ApiError(403, "ORIGIN_REQUIRED", "写请求必须带同源 Origin")
    if origin == "null" or origin not in allowed:
        raise ApiError(403, "ORIGIN_NOT_ALLOWED", "Origin 不被信任（不接受 null 或跨站来源）")
    site = headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        raise ApiError(403, "CROSS_SITE_REQUEST", "跨站请求被拒绝")


def create_session(db, ttl_hours: int) -> tuple[str, dict]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = clock.now()
    row = {
        "session_id": new_id("session"),
        "session_token_hash": token_hash(token),
        "csrf_token": csrf,
        "mode": "live",
        "replay_id": None,
        "knowledge_cutoff_at": None,
        "context_revision": 0,
        "created_at": clock.utc_iso(now),
        "expires_at": clock.utc_iso(now + timedelta(hours=ttl_hours)),
    }
    with db.txn() as c:
        c.execute("""INSERT INTO local_sessions(session_id, session_token_hash, csrf_token, mode, replay_id,
                     knowledge_cutoff_at, context_revision, created_at, expires_at)
                     VALUES(:session_id,:session_token_hash,:csrf_token,:mode,:replay_id,
                     :knowledge_cutoff_at,:context_revision,:created_at,:expires_at)""", row)
    return token, row


def load_session(db, token: str | None) -> dict | None:
    if not token:
        return None
    r = db.one("SELECT * FROM local_sessions WHERE session_token_hash=?", (token_hash(token),))
    if r is None:
        return None
    s = dict(r)
    if clock.parse_utc(s["expires_at"]) <= clock.now():
        return None
    return s


def context_payload(s: dict) -> dict:
    return {"session_id": s["session_id"], "mode": s["mode"], "replay_id": s["replay_id"],
            "knowledge_cutoff_at": s["knowledge_cutoff_at"], "context_revision": int(s["context_revision"]),
            "csrf_token": s["csrf_token"], "created_at": s["created_at"], "expires_at": s["expires_at"]}


def cookie_header(token: str, secure: bool, max_age: int) -> str:
    parts = [f"{COOKIE_NAME}={token}", "Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={max_age}"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def check_csrf(headers: dict, session: dict) -> None:
    tok = headers.get("x-csrf-token")
    if not tok:
        raise ApiError(403, "CSRF_REQUIRED", "写请求缺少 X-CSRF-Token")
    if not secrets.compare_digest(tok, session["csrf_token"]):
        raise ApiError(403, "CSRF_INVALID", "CSRF token 与当前会话不匹配")


def check_replay_header(headers: dict, session: dict) -> None:
    h = headers.get("x-replay-id")
    if h is None:
        return
    if session["mode"] != "replay" or h != session["replay_id"]:
        raise ApiError(403, "REPLAY_CONTEXT_MISMATCH",
                       "X-Replay-Id 与服务端会话上下文不一致；上下文只能经 POST /api/v1/session/context 切换")
