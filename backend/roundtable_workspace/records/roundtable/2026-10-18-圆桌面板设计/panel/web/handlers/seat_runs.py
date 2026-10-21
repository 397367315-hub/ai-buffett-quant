"""单席运行（SEAT-005）：POST 202 + Job；GET 按快照或 Case 分页查询。"""
from __future__ import annotations

import json

from ...jobs import queue
from ...services import seat_runner, snapshot_service
from ..app import Ctx, handler
from ..errors import ApiError
from .seats import ensure_registry


def _cursor_page(rows, limit, key):
    items = rows[:limit]
    nxt = key(rows[limit - 1]) if len(rows) > limit else None
    return items, nxt


@handler("post_api_v1_seat_runs")
def create(ctx: Ctx):
    b = ctx.body
    reg = ensure_registry(ctx.db)
    if b["seat_id"] not in {s["seat_id"] for s in reg["seats"]}:
        raise ApiError(422, "UNKNOWN_SEAT", f"注册表中没有 {b['seat_id']}", [{"field": "seat_id", "reason": "unknown"}])
    snap = snapshot_service.get(ctx.db, b["snapshot_id"], ctx.replay_id)
    if snap is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "快照不存在或在当前上下文中不可见")
    have = {i["symbol"] for i in snap["instruments"]}
    bad = [s for s in b["symbols"] if s not in have]
    if bad:
        raise ApiError(422, "SYMBOL_NOT_IN_SNAPSHOT", f"标的不在该快照中：{', '.join(bad[:5])}",
                       [{"field": "symbols", "reason": f"{s} not in snapshot"} for s in bad[:10]])
    if b["llm_mode"] == "required":
        raise ApiError(422, "LLM_PROVIDER_NOT_CONFIGURED", "llm_mode=required 但没有已配置的模型 provider",
                       [{"field": "llm_mode", "reason": "required provider not configured"}])
    c = ctx.db.conn()
    seat = next(s for s in reg["seats"] if s["seat_id"] == b["seat_id"])
    horizon = "multi" if len(seat["horizons"]) > 1 else seat["horizons"][0]
    from ...domain.canonical import new_id
    srid = new_id("seatrun")
    job = queue.enqueue(c, kind="seat", payload={"seat_run_id": srid}, request_sha256=ctx.request_sha256,
                        resource_type="seat_run", resource_id=srid, replay_id=ctx.replay_id,
                        max_waiting=ctx.settings.max_waiting_jobs)
    seat_runner.create_seat_run(c, seat_id=b["seat_id"], snapshot=snap, case_id=None, round_="initial",
                                cycle_id=b["cycle_id"], registry=reg, job_id=job["job_id"],
                                replay_id=ctx.replay_id, symbols=b["symbols"], horizon=horizon,
                                llm_mode=b["llm_mode"], seat_run_id=srid)
    return queue.accepted(job)


@handler("get_api_v1_seat_runs")
def list_runs(ctx: Ctx):
    q = ctx.query
    sql = "SELECT seat_run_id, created_at, canonical_payload FROM seat_runs WHERE replay_id IS ?"
    args: list = [ctx.replay_id]
    if q.get("snapshot_id"):
        sql += " AND snapshot_id=?"
        args.append(q["snapshot_id"])
    if q.get("case_id"):
        sql += " AND case_id=?"
        args.append(q["case_id"])
    if q.get("cursor"):
        try:
            ts, rid = q["cursor"].split("|", 1)
        except ValueError:
            raise ApiError(400, "BAD_CURSOR", "cursor 不合法")
        sql += " AND (created_at, seat_run_id) < (?, ?)"
        args += [ts, rid]
    limit = q.get("limit", 50)
    sql += " ORDER BY created_at DESC, seat_run_id DESC LIMIT ?"
    args.append(limit + 1)
    rows = ctx.db.query(sql, args)
    items, nxt = _cursor_page(rows, limit, lambda r: f"{r['created_at']}|{r['seat_run_id']}")
    return 200, {"items": [seat_runner.public(json.loads(r["canonical_payload"])) for r in items], "next_cursor": nxt}


@handler("get_api_v1_seat_runs_seat_run_id")
def get_run(ctx: Ctx):
    obj = seat_runner.get(ctx.db, ctx.path_params["seat_run_id"], ctx.replay_id)
    if obj is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "席位运行不存在或在当前上下文中不可见")
    return 200, obj
