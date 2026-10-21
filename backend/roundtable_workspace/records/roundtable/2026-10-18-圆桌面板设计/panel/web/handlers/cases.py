"""圆桌 Case（CASE-001）。"""
from __future__ import annotations

from ...jobs import queue
from ...services import case_orchestrator
from ..app import Ctx, handler
from ..errors import ApiError
from .seats import ensure_registry


@handler("post_api_v1_cases")
def create(ctx: Ctx):
    reg = ensure_registry(ctx.db)
    case_orchestrator.validate_request(ctx.db, ctx.body, ctx.replay_id, reg)
    job, _obj = case_orchestrator.create(ctx.db.conn(), ctx.body, replay_id=ctx.replay_id,
                                         request_sha256=ctx.request_sha256, registry=reg,
                                         max_waiting=ctx.settings.max_waiting_jobs)
    return queue.accepted(job)


@handler("get_api_v1_cases_case_id")
def get_case(ctx: Ctx):
    obj = case_orchestrator.load(ctx.db, ctx.path_params["case_id"], ctx.replay_id)
    if obj is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "Case 不存在或在当前上下文中不可见")
    return 200, obj


@handler("get_api_v1_cases_case_id_final")
def get_final(ctx: Ctx):
    obj = case_orchestrator.load(ctx.db, ctx.path_params["case_id"], ctx.replay_id)
    if obj is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "Case 不存在或在当前上下文中不可见")
    if obj["state"] not in case_orchestrator.TERMINAL:
        raise ApiError(409, "CASE_NOT_FINAL", f"Case 尚未到终态（当前 {obj['state']}）",
                       [{"field": "state", "reason": obj["state"]}])
    return 200, obj
