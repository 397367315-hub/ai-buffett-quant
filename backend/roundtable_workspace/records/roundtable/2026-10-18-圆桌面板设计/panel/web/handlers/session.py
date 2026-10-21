"""本地会话：bootstrap / 读取上下文 / 显式切换（02 §13.2）。"""
from __future__ import annotations

from ...domain import clock
from .. import security
from ..app import Ctx, Request, Response, handler
from ..errors import ApiError


@handler("post_api_v1_session_bootstrap")
def bootstrap(ctx: Ctx):
    # App 已校验 Host、Origin、Content-Type 与请求体（SessionBootstrapRequest = {}）
    token, row = security.create_session(ctx.db, ctx.settings.session_ttl_hours)
    secure = ctx.request.headers.get("x-forwarded-proto") == "https"
    cookie = security.cookie_header(token, secure, ctx.settings.session_ttl_hours * 3600)
    return Response.json(201, security.context_payload(row), [("Set-Cookie", cookie)])


@handler("get_api_v1_session_context")
def get_context(ctx: Ctx):
    return 200, security.context_payload(ctx.session)


@handler("post_api_v1_session_context")
def change_context(ctx: Ctx):
    b = ctx.body
    s = ctx.session
    if b["expected_context_revision"] != int(s["context_revision"]):
        raise ApiError(409, "CONTEXT_REVISION_CONFLICT", "会话上下文已变化，请刷新后重试",
                       [{"field": "expected_context_revision",
                         "reason": f"expected {b['expected_context_revision']}, current {s['context_revision']}"}])
    c = ctx.db.conn()
    if b["mode"] == "replay":
        rid = b["replay_id"]
        if not rid:
            raise ApiError(422, "REPLAY_ID_REQUIRED", "切换到回放必须给 replay_id",
                           [{"field": "replay_id", "reason": "required"}])
        rep = c.execute("SELECT state, knowledge_cutoff_at FROM replay_sessions WHERE replay_id=?", (rid,)).fetchone()
        if rep is None:
            raise ApiError(404, "RESOURCE_NOT_VISIBLE", "回放不存在")
        if s["mode"] == "replay" and s["replay_id"] != rid:
            raise ApiError(409, "TRAINING_NOT_ENDED", "当前绑定的训练尚未结束，不能直接切到另一场回放")
        mode, replay_id, cutoff = "replay", rid, rep["knowledge_cutoff_at"]
    else:
        if b["replay_id"] is not None:
            raise ApiError(422, "VALIDATION_ERROR", "切回 live 时 replay_id 必须为 null",
                           [{"field": "replay_id", "reason": "must be null for live"}])
        if s["mode"] == "replay":
            rep = c.execute("SELECT revealed FROM replay_sessions WHERE replay_id=?", (s["replay_id"],)).fetchone()
            if rep is None or not rep["revealed"]:
                raise ApiError(409, "TRAINING_NOT_ENDED", "训练尚未揭晓：先 POST /replays/{id}/reveal 结束训练，再切回真实盘")
            if not b["acknowledge_training_end"]:
                raise ApiError(422, "ACKNOWLEDGEMENT_REQUIRED", "切回真实盘需要明确 acknowledge_training_end=true",
                               [{"field": "acknowledge_training_end", "reason": "must be true"}])
        mode, replay_id, cutoff = "live", None, None
    rev = int(s["context_revision"]) + 1
    c.execute("""UPDATE local_sessions SET mode=?, replay_id=?, knowledge_cutoff_at=?, context_revision=?
                 WHERE session_id=? AND context_revision=?""",
              (mode, replay_id, cutoff, rev, s["session_id"], s["context_revision"]))
    row = dict(c.execute("SELECT * FROM local_sessions WHERE session_id=?", (s["session_id"],)).fetchone())
    return 200, security.context_payload(row)


def bind_session_to_replay(c, session: dict, replay_id: str, cutoff: str) -> None:
    """回放创建时原子绑定当前会话（同一事务）。"""
    c.execute("""UPDATE local_sessions SET mode='replay', replay_id=?, knowledge_cutoff_at=?,
                 context_revision=context_revision+1 WHERE session_id=?""", (replay_id, cutoff, session["session_id"]))
