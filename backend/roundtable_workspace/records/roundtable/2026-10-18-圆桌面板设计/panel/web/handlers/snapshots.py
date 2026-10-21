"""快照读取与手动采集（DATA-004）。"""
from __future__ import annotations

import copy

from ...domain import clock
from ...jobs import queue
from ...services import calendar_service, postmarket_service, snapshot_service
from ..app import Ctx, Response, handler
from ..errors import ApiError


@handler("get_api_v1_snapshots_latest")
def latest(ctx: Ctx):
    snap = snapshot_service.latest(ctx.db, ctx.replay_id)
    if snap is None:
        raise ApiError(404, "SNAPSHOT_NOT_FOUND", "当前上下文中还没有快照；可 POST /api/v1/snapshots 手动采集")
    if ctx.replay_id is not None:
        return 200, snap
    fr, why = snapshot_service.current_freshness(ctx.db, snap)
    if fr == "fresh" and why == snap["metadata"]["freshness_reason"]:
        return 200, snap
    if fr == "fresh":
        view = copy.deepcopy(snap)
        view["metadata"]["freshness_reason"] = f"{why}（读取时按当前日历重估）"
        return Response.json(200, view, {"X-Freshness-Reevaluated": "true", "X-Stored-Freshness": "fresh"})
    if not ctx.query.get("allow_stale"):
        raise ApiError(503, "DATA_STALE", f"最新快照已不是此刻最新：{why}",
                       [{"field": "snapshot_id", "reason": snap["snapshot_id"]},
                        {"field": "freshness", "reason": fr}], retryable=True)
    view = copy.deepcopy(snap)
    stored = view["metadata"]["freshness"]
    view["metadata"]["freshness"] = "stale" if fr == "stale" else fr
    view["metadata"]["freshness_reason"] = f"{why}（读取时按当前日历重估；生成时为 {stored}）"
    return Response.json(200, view, {"X-Freshness-Reevaluated": "true", "X-Stored-Freshness": stored})


@handler("get_api_v1_snapshots_snapshot_id")
def get_one(ctx: Ctx):
    snap = snapshot_service.get(ctx.db, ctx.path_params["snapshot_id"], ctx.replay_id)
    if snap is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "快照不存在或在当前上下文中不可见")
    return 200, snap


@handler("post_api_v1_snapshots")
def create(ctx: Ctx):
    if ctx.replay_id is not None:
        raise ApiError(409, "REPLAY_CONTEXT_CONFLICT", "训练上下文中的可见快照只能由 POST /replays/{id}/step 生成")
    b = ctx.body
    D = b["session_date"]
    today = clock.shanghai_date()
    if D > today:
        raise ApiError(422, "SESSION_IN_FUTURE", f"{D} 在今天（上海 {today}）之后：还没有这一天的行情",
                       [{"field": "session_date", "reason": "session_in_future"}])
    cal = calendar_service.current(ctx.db)
    if cal is not None and cal.status(D) == "closed":
        raise ApiError(422, "NON_TRADING_SESSION", f"{D} 不是交易日（{cal.calendar_version}）",
                       [{"field": "session_date", "reason": "non_trading_session"}])
    if (cal is not None and b["source_mode"] == "provider_api" and D == today
            and cal.phase() == "pre_open"):
        raise ApiError(422, "SESSION_NOT_STARTED", f"{D} 还没开盘：今天的日线还不存在",
                       [{"field": "session_date", "reason": "session_not_started"}])
    job = queue.enqueue(ctx.db.conn(), kind="snapshot", payload=dict(b), request_sha256=ctx.request_sha256,
                        resource_type=None, resource_id=None, replay_id=None,
                        max_waiting=ctx.settings.max_waiting_jobs)
    return queue.accepted(job)


@handler("get_api_v1_snapshots_snapshot_id_postmarket")
def postmarket(ctx: Ctx):
    """盘后复盘（确定性规则版）：只读该快照，不调用任何模型。"""
    snap = snapshot_service.get(ctx.db, ctx.path_params["snapshot_id"], ctx.replay_id)
    if snap is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "快照不存在或在当前上下文中不可见")
    return 200, postmarket_service.build(snap)
