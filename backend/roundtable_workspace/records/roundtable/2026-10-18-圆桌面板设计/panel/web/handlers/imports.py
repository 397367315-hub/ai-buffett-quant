"""旧账本导入（JOURNAL-005）。"""
from __future__ import annotations

from ...services import legacy_importer
from ..app import Ctx, handler
from ..errors import ApiError


@handler("post_api_v1_portfolio_import_legacy")
def import_legacy(ctx: Ctx):
    if ctx.replay_id is not None:
        raise ApiError(409, "REPLAY_CONTEXT_CONFLICT", "训练上下文中不能导入旧账本")
    return 200, legacy_importer.run(ctx.db.conn(), ctx.db, ctx.body, (ctx.request.path, ctx.idempotency_key))
