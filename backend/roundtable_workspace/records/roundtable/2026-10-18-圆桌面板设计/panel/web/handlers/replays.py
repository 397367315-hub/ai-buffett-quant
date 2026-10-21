"""历史训练（REPLAY-002）。"""
from __future__ import annotations

from ...jobs import queue
from ...services import replay_service
from ..app import Ctx, handler
from ..errors import ApiError


@handler("post_api_v1_replays")
def create(ctx: Ctx):
    job = replay_service.create(ctx.db.conn(), ctx.db, ctx.body, ctx.session, ctx.request_sha256,
                                ctx.settings.max_waiting_jobs)
    return queue.accepted(job)


def _visible(ctx: Ctx, rid: str) -> dict:
    rep = replay_service.get(ctx.db, rid)
    # 训练上下文只能看自己这一场；真实盘可看已建回放的元数据（不含未来数据）
    if rep is None or (ctx.replay_id is not None and ctx.replay_id != rid):
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "回放不存在或在当前上下文中不可见")
    return rep


@handler("get_api_v1_replays_replay_id")
def get(ctx: Ctx):
    return 200, _visible(ctx, ctx.path_params["replay_id"])


@handler("post_api_v1_replays_replay_id_step")
def step(ctx: Ctx):
    rid = ctx.path_params["replay_id"]
    if ctx.replay_id != rid:
        raise ApiError(409, "REPLAY_CONTEXT_CONFLICT", "只能在绑定这场训练的会话里推进")
    _visible(ctx, rid)
    return 200, replay_service.step(ctx.db, rid, ctx.body)


@handler("post_api_v1_replays_replay_id_reveal")
def reveal(ctx: Ctx):
    rid = ctx.path_params["replay_id"]
    if ctx.replay_id != rid:
        raise ApiError(409, "REPLAY_CONTEXT_CONFLICT", "只能在绑定这场训练的会话里揭晓")
    _visible(ctx, rid)
    return 200, replay_service.reveal(ctx.db, rid, ctx.body)
