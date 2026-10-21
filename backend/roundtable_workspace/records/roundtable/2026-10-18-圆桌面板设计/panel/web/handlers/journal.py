"""模拟账本与日志（JOURNAL-001 / JOURNAL-004）、冷却控制（JOURNAL-003）。"""
from __future__ import annotations

import json

from ...domain import clock
from ...domain.canonical import canonical_json, new_id
from ...services import journal_service as js
from ..app import Ctx, handler
from ..errors import ApiError


@handler("post_api_v1_portfolios")
def create_portfolio(ctx: Ctx):
    if ctx.replay_id is not None:
        raise ApiError(409, "REPLAY_CONTEXT_CONFLICT", "训练上下文的隔离组合由回放创建，不能另建")
    return 201, js.create_portfolio(ctx.db.conn(), ctx.body, ctx.replay_id, (ctx.request.path, ctx.idempotency_key))


@handler("get_api_v1_portfolios_portfolio_id")
def get_portfolio(ctx: Ctx):
    c = ctx.db.conn()
    return 200, js.portfolio_view(c, ctx.path_params["portfolio_id"], ctx.replay_id, ctx.query.get("snapshot_id"), ctx.db)


@handler("post_api_v1_journal_events")
def append_event(ctx: Ctx):
    return 201, js.append(ctx.db.conn(), ctx.db, ctx.body, ctx.replay_id, (ctx.request.path, ctx.idempotency_key))


@handler("get_api_v1_journal_events")
def list_events(ctx: Ctx):
    q = ctx.query
    return 200, js.list_events(ctx.db.conn(), q["portfolio_id"], ctx.replay_id, q.get("cursor"), q.get("limit", 50))


# ── 冷却控制事件 ──
DEFAULT_POLICIES = [
    {"policy_id": "cooldown_manual", "policy_version": "v1",
     "config": {"title": "手动冷却", "rule_origin": "user_rule",
                "blocked_action_classes": ["increase_risk"],
                "allowed_action_classes": ["reduce_risk", "cancel", "record", "view"],
                "release": "随时可解除，需写原因；解除本身不算违例（除非用户事前另有规则）",
                "description": "暂停新开仓与加仓；查看行情、查看持仓、记录、取消未执行计划、减仓与风险退出始终可用"}},
    {"policy_id": "cooldown_until_next_session", "policy_version": "v1",
     "config": {"title": "冷却到下一交易日", "rule_origin": "user_rule",
                "blocked_action_classes": ["increase_risk"],
                "allowed_action_classes": ["reduce_risk", "cancel", "record", "view"],
                "release": "到预定结束时间自动失效；提前解除需写原因",
                "description": "与手动冷却相同的限制，带预定结束时间"}},
]


def seed_policies(db) -> None:
    from ...domain.canonical import content_hash
    with db.txn() as c:
        for p in DEFAULT_POLICIES:
            if c.execute("SELECT 1 FROM control_policy_versions WHERE policy_id=? AND policy_version=?",
                         (p["policy_id"], p["policy_version"])).fetchone():
                continue
            c.execute("INSERT INTO control_policy_versions(policy_id, policy_version, created_at, policy_sha256, config_json) "
                      "VALUES(?,?,?,?,?)", (p["policy_id"], p["policy_version"], clock.utc_iso(), content_hash(p["config"]),
                                           canonical_json(p["config"])))


@handler("post_api_v1_portfolios_portfolio_id_controls_events")
def control_event(ctx: Ctx):
    c = ctx.db.conn()
    pid = ctx.path_params["portfolio_id"]
    js.portfolio_row(c, pid, ctx.replay_id)
    b = ctx.body
    pol = c.execute("SELECT 1 FROM control_policy_versions WHERE policy_id=? AND policy_version=?",
                    (b["policy_id"], b["policy_version"])).fetchone()
    if pol is None:
        raise ApiError(422, "CONTROL_POLICY_UNKNOWN", "冷却策略未登记（只能引用版本化预设策略）",
                       [{"field": "policy_id", "reason": "unknown policy/version"}])
    st = js.cooldown_state(c, pid, clock.parse_utc(b["effective_at"]))
    last = st["events"][-1]["event_id"] if st["events"] else None
    if b["expected_previous_event_id"] != last:
        raise ApiError(409, "CONTROL_EVENT_CONFLICT", "冷却事件链已变化",
                       [{"field": "expected_previous_event_id", "reason": f"current {last}"}])
    et = b["event_type"]
    if et == "enabled":
        if st["active"]:
            raise ApiError(409, "COOLDOWN_ALREADY_ACTIVE", "已有生效中的冷却")
        if b["related_control_event_id"] is not None:
            raise ApiError(422, "VALIDATION_ERROR", "enabled 不引用其它事件", [{"field": "related_control_event_id", "reason": "must be null"}])
        if b["scheduled_end_at"] and clock.parse_utc(b["scheduled_end_at"]) <= clock.parse_utc(b["effective_at"]):
            raise ApiError(422, "VALIDATION_ERROR", "预定结束时间必须晚于开始", [{"field": "scheduled_end_at", "reason": "not after effective_at"}])
    else:
        en = st["enabled_event"]
        if en is None or b["related_control_event_id"] != en["event_id"]:
            raise ApiError(409, "NO_ACTIVE_COOLDOWN", "没有对应的生效中冷却可以解除/到期",
                           [{"field": "related_control_event_id", "reason": "must reference the active enabled event"}])
        if et == "expired" and (en["scheduled_end_at"] is None or
                                clock.parse_utc(b["effective_at"]) < clock.parse_utc(en["scheduled_end_at"])):
            raise ApiError(422, "COOLDOWN_NOT_DUE", "冷却还没到预定结束时间；要提前结束请用 released 并写原因")
    ev = {"event_id": new_id("ctlevt"), "portfolio_id": pid, "control_type": "cooldown", "event_type": et,
          "policy_id": b["policy_id"], "policy_version": b["policy_version"], "effective_at": b["effective_at"],
          "scheduled_end_at": b["scheduled_end_at"], "recorded_at": clock.utc_iso(),
          "related_control_event_id": b["related_control_event_id"], "previous_event_id": last, "reason": b["reason"],
          "replay_id": ctx.replay_id}
    c.execute("""INSERT INTO portfolio_control_events(event_id, portfolio_id, control_type, event_type, policy_id, policy_version,
                 effective_at, scheduled_end_at, recorded_at, related_control_event_id, previous_event_id, replay_id, reason,
                 canonical_payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (ev["event_id"], pid, "cooldown", et, b["policy_id"], b["policy_version"], b["effective_at"],
               b["scheduled_end_at"], ev["recorded_at"], b["related_control_event_id"], last, ctx.replay_id, b["reason"],
               canonical_json(ev)))
    return 201, ev


@handler("get_api_v1_portfolios_portfolio_id_controls_events")
def control_events(ctx: Ctx):
    c = ctx.db.conn()
    pid = ctx.path_params["portfolio_id"]
    js.portfolio_row(c, pid, ctx.replay_id)
    items = js.cooldown_state(c, pid)["events"]
    cur = ctx.query.get("cursor")
    if cur:
        ids = [e["event_id"] for e in items]
        if cur not in ids:
            raise ApiError(400, "BAD_CURSOR", "cursor 不合法")
        items = items[ids.index(cur) + 1:]
    page = items[:100]
    return 200, {"items": page, "next_cursor": page[-1]["event_id"] if len(items) > 100 else None}
