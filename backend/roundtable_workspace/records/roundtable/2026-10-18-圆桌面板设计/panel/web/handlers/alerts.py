"""P4 提醒（ALERT-002/003）。能力闸门关闭时 App 在进入处理函数之前就返回 501 FEATURE_DISABLED。"""
from __future__ import annotations

from ...services import alert_service
from ..app import Ctx, handler
from ..errors import ApiError


@handler("get_api_v1_alerts")
def list_alerts(ctx: Ctx):
    return 200, alert_service.list_events(ctx.db, ctx.replay_id, int(ctx.query.get("limit", 50)), ctx.query.get("cursor"))


@handler("post_api_v1_alerts_alert_id_acknowledge")
def acknowledge(ctx: Ctx):
    o = alert_service.acknowledge(ctx.db, ctx.path_params["alert_id"], ctx.replay_id)
    if o is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "提醒不存在或在当前上下文中不可见")
    return 200, o
