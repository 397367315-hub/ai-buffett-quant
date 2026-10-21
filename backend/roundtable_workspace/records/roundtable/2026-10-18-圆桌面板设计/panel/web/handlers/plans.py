"""事前计划（PLAN-001）。"""
from __future__ import annotations

from ...services import plan_service
from ..app import Ctx, handler
from ..errors import ApiError


@handler("post_api_v1_plans")
def create(ctx: Ctx):
    return 201, plan_service.create(ctx.db.conn(), ctx.db, ctx.body, ctx.replay_id)


@handler("get_api_v1_plans_plan_id")
def get_latest(ctx: Ctx):
    p = plan_service.latest(ctx.db, ctx.path_params["plan_id"], ctx.replay_id)
    if p is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "计划不存在或在当前上下文中不可见")
    return 200, p


@handler("get_api_v1_plans_plan_id_versions_version")
def get_version(ctx: Ctx):
    try:
        v = int(ctx.path_params["version"])
    except ValueError:
        raise ApiError(422, "VALIDATION_ERROR", "version 必须是整数", [{"field": "version", "reason": "integer"}])
    if v < 1:
        raise ApiError(422, "VALIDATION_ERROR", "version 从 1 开始", [{"field": "version", "reason": "minimum 1"}])
    p = plan_service.version(ctx.db, ctx.path_params["plan_id"], v, ctx.replay_id)
    if p is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "计划版本不存在或在当前上下文中不可见")
    return 200, p


@handler("post_api_v1_plans_plan_id_versions")
def new_version(ctx: Ctx):
    b = ctx.body
    return 201, plan_service.new_version(ctx.db.conn(), ctx.db, ctx.path_params["plan_id"], b["base_version"],
                                         b["content"], ctx.replay_id)


@handler("post_api_v1_plans_plan_id_freeze")
def freeze(ctx: Ctx):
    return 200, plan_service.freeze(ctx.db.conn(), ctx.db, ctx.path_params["plan_id"], ctx.body["expected_version"],
                                    ctx.replay_id)


@handler("post_api_v1_plans_plan_id_events")
def add_event(ctx: Ctx):
    return 201, plan_service.append_event(ctx.db.conn(), ctx.db, ctx.path_params["plan_id"], ctx.body, ctx.replay_id)


@handler("get_api_v1_plans_plan_id_events")
def list_events(ctx: Ctx):
    if plan_service.latest(ctx.db, ctx.path_params["plan_id"], ctx.replay_id) is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "计划不存在或在当前上下文中不可见")
    items = plan_service.list_events(ctx.db, ctx.path_params["plan_id"], ctx.replay_id, ctx.query.get("version"))
    cur = ctx.query.get("cursor")
    if cur:
        ids = [e["event_id"] for e in items]
        if cur not in ids:
            raise ApiError(400, "BAD_CURSOR", "cursor 不合法")
        items = items[ids.index(cur) + 1:]
    page = items[:100]
    return 200, {"items": page, "next_cursor": page[-1]["event_id"] if len(items) > 100 else None}
