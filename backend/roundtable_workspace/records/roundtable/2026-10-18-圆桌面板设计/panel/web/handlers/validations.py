"""方法验证（EVAL-002）。"""
from __future__ import annotations

from ...jobs import queue
from ...services import validation_service
from ..app import Ctx, handler
from ..errors import ApiError


@handler("post_api_v1_validations")
def create(ctx: Ctx):
    job = validation_service.create(ctx.db.conn(), ctx.db, ctx.body, ctx.replay_id, ctx.request_sha256,
                                    ctx.settings.max_waiting_jobs)
    return queue.accepted(job)


@handler("get_api_v1_validations_validation_id")
def get(ctx: Ctx):
    r = validation_service.get(ctx.db, ctx.path_params["validation_id"], ctx.replay_id)
    if r is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "验证不存在或在当前上下文中不可见")
    return 200, r
