"""任务查询（所有异步 POST 共用）。只返回当前上下文可见的任务。"""
from __future__ import annotations

from ...jobs import queue
from ..app import Ctx, handler
from ..errors import ApiError


@handler("get_api_v1_jobs_job_id")
def get_job(ctx: Ctx):
    r = ctx.db.one("SELECT * FROM jobs WHERE job_id=?", (ctx.path_params["job_id"],))
    if r is None or r["replay_id"] != ctx.replay_id:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "任务不存在或在当前上下文中不可见")
    return 200, queue.job_dict(r)
