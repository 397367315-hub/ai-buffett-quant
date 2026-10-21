"""事前审查 / 事后复盘（PLAN-003 / JOURNAL-002）。"""
from __future__ import annotations

from ...jobs import queue
from ...services import review_service
from ..app import Ctx, handler
from ..errors import ApiError


@handler("post_api_v1_reviews")
def create(ctx: Ctx):
    job = review_service.create(ctx.db.conn(), ctx.db, ctx.body, ctx.replay_id, ctx.request_sha256,
                                ctx.settings.max_waiting_jobs)
    return queue.accepted(job)


@handler("get_api_v1_reviews_review_id")
def get(ctx: Ctx):
    r = review_service.get(ctx.db, ctx.path_params["review_id"], ctx.replay_id)
    if r is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "复盘不存在或在当前上下文中不可见")
    return 200, r
