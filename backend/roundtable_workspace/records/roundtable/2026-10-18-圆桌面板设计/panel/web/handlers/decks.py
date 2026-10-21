"""学习卡组（CARD-001）。"""
from __future__ import annotations

from ...services import deck_service
from ..app import Ctx, handler
from ..errors import ApiError


@handler("post_api_v1_decks")
def create(ctx: Ctx):
    deck = deck_service.create(ctx.db.conn(), ctx.db, ctx.body, ctx.replay_id)
    return 201, deck


@handler("get_api_v1_decks_deck_id")
def get(ctx: Ctx):
    d = deck_service.get(ctx.db, ctx.path_params["deck_id"], ctx.replay_id)
    if d is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "卡组不存在或在当前上下文中不可见")
    return 200, d
