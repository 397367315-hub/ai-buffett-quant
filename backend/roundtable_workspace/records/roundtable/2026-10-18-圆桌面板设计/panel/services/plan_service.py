"""事前计划（PLAN-001 / PLAN-003）：版本、唯一主方法、冻结哈希、执行资格、生命周期事件。

- 每个版本一行，不原地编辑：改动 = POST /versions 复制出 version+1（base_version 必须是当前最新，否则 409）；
- 冻结：同一事务校验 expected_version → 计算资格 → frozen_at/frozen_hash（canonical-json-v1，去掉 frozen_hash 本身）；
  冻结后触发器拒绝 UPDATE/DELETE；相同版本再次冻结返回原哈希；
- research 可以冻结不完整观点，但 execution_eligibility=unknown 并列出原因，不能据此新开模拟仓；
  simulation 缺触发、取消、防守/失效、时间退出、风险预算或股数 → 冻结 422（逐字段错误）；
  其余可执行性问题（现金、手数、板块、快照非终结……）→ 可冻结但 ineligible，原因逐条可查；
- 主方法变更不能靠新版本完成（422），必须另建 Case/Plan；
- 生命周期事件只追加：activated / trigger_observed / cancelled / expired / execution_unfilled / execution_filled，
  expected_previous_event_id 乐观锁；execution_filled 必须引用真实存在的 journal 事件。
"""
from __future__ import annotations

import json
import re
from decimal import Decimal

from ..domain import clock, faults, trading_rules as tr
from ..domain.canonical import canonical_json, content_hash, new_id
from ..domain.fees import FEE_SCHEDULES, compute_fees
from ..domain.money import mul_shares, parse_minor, to_decimal
from ..web.errors import ApiError
from . import case_orchestrator, snapshot_service

AUTHOR_REF_RE = re.compile(r"(p\.\s*\d+|第\s*[一二三四五六七八九十百\d]+\s*[章节页]|页\s*\d+|\d+\s*页)")
PLAN_FIELDS = ["plan_id", "version", "previous_version", "change_reason", "effective_from", "portfolio_id",
               "basis_portfolio_revision", "case_id", "snapshot_id", "status", "primary_strategy_id", "purpose",
               "execution_eligibility", "execution_eligibility_reasons", "planned_shares", "title", "hypothesis",
               "symbols", "horizon", "entry_rules", "exit_rules", "cancel_rules", "time_exit_rule", "exit_deadline",
               "invalidation_rules", "risk_budget_minor", "max_position_ratio", "benchmark_id", "evaluation_window",
               "fee_model_version", "created_at", "frozen_at", "frozen_hash", "replay_id"]


# ── 校验 ──
def _rules(body: dict) -> list[tuple[str, dict]]:
    out = []
    for k in ("entry_rules", "exit_rules", "cancel_rules", "invalidation_rules"):
        out += [(k, r) for r in body.get(k) or []]
    if body.get("time_exit_rule"):
        out.append(("time_exit_rule", body["time_exit_rule"]))
    return out


_KIND_OF = {"entry_rules": "entry", "exit_rules": "exit", "cancel_rules": "cancel", "invalidation_rules": "invalidation",
            "time_exit_rule": "time_exit"}


def validate_rules(body: dict) -> None:
    errs = []
    ids = set()
    for k, r in _rules(body):
        if r["rule_id"] in ids:
            errs.append({"field": f"{k}.rule_id", "reason": f"rule_id {r['rule_id']} 重复"})
        ids.add(r["rule_id"])
        if r["kind"] != _KIND_OF[k] and not (k == "exit_rules" and r["kind"] in ("exit", "position")):
            errs.append({"field": f"{k}.kind", "reason": f"{r['rule_id']} 的 kind={r['kind']} 与所在列表不符"})
        if r["rule_origin"] == "author_quote" and not AUTHOR_REF_RE.search(r["origin_ref"] or ""):
            errs.append({"field": f"{k}.origin_ref", "reason": f"{r['rule_id']} 标为 author_quote 但没有可核验的书名与页码/章节"})
        if r["price_decimal"] is not None:
            p = Decimal(r["price_decimal"])
            if p <= 0:
                errs.append({"field": f"{k}.price_decimal", "reason": f"{r['rule_id']} 价格必须为正"})
            elif not tr.is_tick_aligned(p):
                errs.append({"field": f"{k}.price_decimal", "reason": f"{r['rule_id']} 价格 {p} 不在 0.01 元最小变动单位上"})
        if r["symbol"] is not None and r["symbol"] not in body["symbols"]:
            errs.append({"field": f"{k}.symbol", "reason": f"{r['rule_id']} 的标的不在计划 symbols 内"})
    # 多头价位一致性（v1 模拟只做多）：防守/失效价必须低于入场价；取消价不得与入场价完全重叠
    entries = [Decimal(r["price_decimal"]) for r in body.get("entry_rules") or [] if r["price_decimal"]]
    defense = [Decimal(r["price_decimal"]) for r in (body.get("invalidation_rules") or []) + (body.get("exit_rules") or [])
               if r["price_decimal"] and r["kind"] in ("invalidation", "exit") and r.get("threshold_ratio") is None]
    cancels = [Decimal(r["price_decimal"]) for r in body.get("cancel_rules") or [] if r["price_decimal"]]
    if entries:
        e = min(entries)
        for i, d in enumerate(r for r in (body.get("invalidation_rules") or []) if r["price_decimal"]):
            if Decimal(d["price_decimal"]) >= e:
                errs.append({"field": "defense_price", "reason": f"失效/防守价 {d['price_decimal']} 不低于入场价 {e}（多头计划）"})
        for cp in cancels:
            if cp in entries:
                errs.append({"field": "cancel_price", "reason": f"取消价 {cp} 与入场价完全重叠"})
    if body.get("max_position_ratio") is not None:
        mp = Decimal(body["max_position_ratio"])
        if not (Decimal(0) < mp <= 1):
            errs.append({"field": "max_position_ratio", "reason": "仓位上限比例应在 (0, 1]"})
    if body.get("evaluation_window"):
        w = body["evaluation_window"]
        if w["end_session_date"] < w["start_session_date"]:
            errs.append({"field": "evaluation_window", "reason": "结束早于开始"})
    if body.get("fee_model_version") and body["fee_model_version"] not in FEE_SCHEDULES:
        errs.append({"field": "fee_model_version", "reason": f"未知费用模型 {body['fee_model_version']}"})
    if errs:
        raise ApiError(422, "PLAN_RULES_INCONSISTENT", "计划规则存在字段错误或互相矛盾的价位", errs)


def _completeness(body: dict) -> list[dict]:
    """simulation 四要件与预算（缺一项 → 一个字段错误）。"""
    miss = []
    if not body.get("entry_rules"):
        miss.append({"field": "entry_rules", "reason": "缺入场触发条件"})
    if not body.get("cancel_rules"):
        miss.append({"field": "cancel_rules", "reason": "缺取消条件（入场前失效/有效期）"})
    if not body.get("invalidation_rules") and not any(r["kind"] == "exit" for r in body.get("exit_rules") or []):
        miss.append({"field": "invalidation_rules", "reason": "缺防守/失效条件"})
    if not body.get("time_exit_rule") and not body.get("exit_deadline"):
        miss.append({"field": "time_exit_rule", "reason": "缺时间退出规则"})
    if body.get("risk_budget_minor") is None:
        miss.append({"field": "risk_budget_minor", "reason": "缺单笔风险预算"})
    if body.get("planned_shares") is None:
        miss.append({"field": "planned_shares", "reason": "缺计划股数"})
    return miss


from .snapshot_service import CRITICAL_SNAPSHOT_FIELDS, execution_blockers as snapshot_execution_blockers  # noqa: E402


def eligibility(db, plan: dict, replay_id) -> tuple[str, list[str]]:
    """服务端派生执行资格。research/user_trade_record 永不 eligible。"""
    reasons: list[str] = []
    if plan["purpose"] == "research":
        reasons.append("purpose_research_not_executable")
        for m in _completeness(plan):
            reasons.append({"entry_rules": "entry_rules_missing", "cancel_rules": "cancel_rules_missing",
                            "invalidation_rules": "invalidation_missing", "time_exit_rule": "time_exit_missing",
                            "risk_budget_minor": "risk_budget_missing", "planned_shares": "planned_shares_missing"}[m["field"]])
        return "unknown", reasons
    if plan["purpose"] == "user_trade_record":
        return "ineligible", ["user_trade_record_is_manual_declaration_not_paper_execution"]
    miss = _completeness(plan)
    reasons += [f"{m['field']}_missing" for m in miss]
    sym = plan["symbols"][0]
    snap = snapshot_service.get(db, plan["snapshot_id"], replay_id)
    inst = next((i for i in (snap or {}).get("instruments", []) if i["symbol"] == sym), None)
    if inst is None:
        reasons.append("instrument_not_in_snapshot")
    else:
        if inst["board"] not in tr.SIMULATION_SUPPORTED_BOARDS:
            reasons.append(f"simulation_not_supported_board:{inst['board']}")
        if inst["is_st"] is not False:
            reasons.append("st_or_unknown_status")
        lot = tr.lot_size(inst["board"])
        if plan["planned_shares"] and lot and plan["planned_shares"] % lot:
            reasons.append(f"planned_shares_not_multiple_of_{lot}")
        if inst["suspended"] is None:
            reasons.append("suspension_status_unknown")
        elif inst["suspended"]:
            reasons.append("instrument_suspended")
    if snap is not None:
        reasons += snapshot_execution_blockers(snap, sym)
    if plan["portfolio_id"]:
        p = db.one("SELECT * FROM portfolios WHERE portfolio_id=?", (plan["portfolio_id"],))
        if p is None or p["replay_id"] != replay_id:
            reasons.append("portfolio_not_visible")
        else:
            if p["mode"] != "paper":
                reasons.append("portfolio_not_paper")
            entry = [Decimal(r["price_decimal"]) for r in plan["entry_rules"] if r["price_decimal"]]
            if entry and plan["planned_shares"]:
                gross = mul_shares(max(entry), plan["planned_shares"])
                fee = compute_fees("buy", gross, plan["fee_model_version"])
                need = int((gross * 100).to_integral_value()) + int(fee["total_minor"])
                if need > int(p["cash_minor"]):
                    reasons.append("insufficient_cash_at_basis_revision")
                if plan["risk_budget_minor"] is not None:
                    inv = [Decimal(r["price_decimal"]) for r in plan["invalidation_rules"] if r["price_decimal"]]
                    if inv:
                        risk = (max(entry) - max(inv)) * plan["planned_shares"]
                        risk_minor = int((risk * 100).to_integral_value()) + int(fee["total_minor"]) * 2
                        if risk_minor > int(plan["risk_budget_minor"]):
                            reasons.append("risk_exceeds_budget")
            elif not entry:
                reasons.append("entry_price_unspecified")
    else:
        reasons.append("portfolio_binding_missing")
    return ("eligible" if not reasons else "ineligible"), reasons


def _visible_case(db, case_id: str, replay_id) -> dict:
    case = case_orchestrator.load(db, case_id, replay_id)
    if case is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "Case 不存在或在当前上下文中不可见",
                       [{"field": "case_id", "reason": "not visible"}])
    return case


def _check_body(db, body: dict, replay_id, case: dict) -> None:
    if body["primary_strategy_id"] != case["primary_strategy_id"]:
        raise ApiError(422, "PRIMARY_STRATEGY_MISMATCH", "计划主方法必须与 Case 的唯一主方法一致；换主方法请另建 Case",
                       [{"field": "primary_strategy_id", "reason": f"case primary is {case['primary_strategy_id']}"}])
    snap = snapshot_service.get(db, body["snapshot_id"], replay_id)
    if snap is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "快照不存在或不可见", [{"field": "snapshot_id", "reason": "not visible"}])
    have = {i["symbol"] for i in snap["instruments"]}
    bad = [s for s in body["symbols"] if s not in have]
    if bad:
        raise ApiError(422, "SYMBOL_NOT_IN_SNAPSHOT", f"标的不在该快照中：{', '.join(bad)}",
                       [{"field": "symbols", "reason": s} for s in bad])
    if body["purpose"] == "simulation":
        if body["portfolio_id"] is None:
            raise ApiError(422, "SIMULATION_PORTFOLIO_BINDING_REQUIRED", "simulation 计划必须绑定 portfolio_id 与 basis_portfolio_revision",
                           [{"field": "portfolio_id", "reason": "required for simulation"}])
        p = db.one("SELECT revision, replay_id FROM portfolios WHERE portfolio_id=?", (body["portfolio_id"],))
        if p is None or p["replay_id"] != replay_id:
            raise ApiError(404, "RESOURCE_NOT_VISIBLE", "组合不存在或不可见", [{"field": "portfolio_id", "reason": "not visible"}])
        if body["basis_portfolio_revision"] is not None and body["basis_portfolio_revision"] > int(p["revision"]):
            raise ApiError(422, "PORTFOLIO_REVISION_INVALID", "basis_portfolio_revision 不能超过组合当前版本",
                           [{"field": "basis_portfolio_revision", "reason": f"current {p['revision']}"}])
    validate_rules(body)
    from .evidence_guard import check_evidence_refs
    cutoff = None
    if replay_id is not None:
        r = db.one("SELECT knowledge_cutoff_at FROM replay_sessions WHERE replay_id=?", (replay_id,))
        cutoff = r["knowledge_cutoff_at"] if r else None
    refs = sorted({e for _, r in _rules(body) for e in r.get("evidence_ids") or []})
    check_evidence_refs(db, refs, replay_id, cutoff, field="rules.evidence_ids")


def _plan_obj(plan_id: str, version: int, previous: int | None, body: dict, replay_id, now: str) -> dict:
    p = {k: body.get(k) for k in PLAN_FIELDS if k in body}
    p.update({"plan_id": plan_id, "version": version, "previous_version": previous, "status": "draft",
              "created_at": now, "frozen_at": None, "frozen_hash": None, "replay_id": replay_id,
              "execution_eligibility": "unknown", "execution_eligibility_reasons": ["draft_not_frozen"]})
    return {k: p.get(k) for k in PLAN_FIELDS}


def _insert(c, p: dict) -> None:
    w = p["evaluation_window"]
    c.execute("""INSERT INTO plan_versions(plan_id, version, previous_version, change_reason, effective_from, portfolio_id,
                 basis_portfolio_revision, case_id, snapshot_id, primary_strategy_id, purpose, status, execution_eligibility,
                 planned_shares, risk_budget_minor, max_position_ratio, exit_deadline, start_session_date, end_session_date,
                 created_at, frozen_at, frozen_hash, replay_id, canonical_payload)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (p["plan_id"], p["version"], p["previous_version"], p["change_reason"], p["effective_from"], p["portfolio_id"],
               p["basis_portfolio_revision"], p["case_id"], p["snapshot_id"], p["primary_strategy_id"], p["purpose"],
               p["status"], p["execution_eligibility"], p["planned_shares"],
               parse_minor(p["risk_budget_minor"]) if p["risk_budget_minor"] is not None else None,
               p["max_position_ratio"], p["exit_deadline"], w["start_session_date"], w["end_session_date"],
               p["created_at"], p["frozen_at"], p["frozen_hash"], p["replay_id"], canonical_json(p)))


def create(c, db, body: dict, replay_id) -> dict:
    case = _visible_case(db, body["case_id"], replay_id)
    _check_body(db, body, replay_id, case)
    p = _plan_obj(new_id("plan"), 1, None, body, replay_id, clock.utc_iso())
    _insert(c, p)
    return p


def latest(db, plan_id: str, replay_id) -> dict | None:
    r = db.one("SELECT canonical_payload, replay_id FROM plan_versions WHERE plan_id=? ORDER BY version DESC LIMIT 1",
               (plan_id,))
    if r is None or r["replay_id"] != replay_id:
        return None
    return json.loads(r["canonical_payload"])


def version(db, plan_id: str, v: int, replay_id) -> dict | None:
    r = db.one("SELECT canonical_payload, replay_id FROM plan_versions WHERE plan_id=? AND version=?", (plan_id, v))
    if r is None or r["replay_id"] != replay_id:
        return None
    return json.loads(r["canonical_payload"])


def new_version(c, db, plan_id: str, base_version: int, content: dict, replay_id) -> dict:
    cur = latest(db, plan_id, replay_id)
    if cur is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "计划不存在或不可见")
    if base_version != cur["version"]:
        raise ApiError(409, "PLAN_VERSION_CONFLICT", "计划已出现更新版本",
                       [{"field": "base_version", "reason": f"expected {base_version}, current {cur['version']}"}])
    if content["case_id"] != cur["case_id"]:
        raise ApiError(422, "PLAN_CASE_MISMATCH", "新版本必须属于同一 Case；改论点请另建 Case（supersedes_case_id）",
                       [{"field": "content.case_id", "reason": "must equal original case_id"}])
    if content["primary_strategy_id"] != cur["primary_strategy_id"]:
        raise ApiError(422, "PRIMARY_STRATEGY_CHANGE_REQUIRES_NEW_PLAN", "主方法变更不能靠新版本完成，请另建计划",
                       [{"field": "content.primary_strategy_id", "reason": "changed"}])
    if cur["status"] == "frozen" and clock.parse_utc(content["effective_from"]) < clock.parse_utc(cur["frozen_at"]):
        raise ApiError(422, "EFFECTIVE_FROM_BACKDATED", "新版本生效时间不能早于上一版的冻结时间（不能倒改历史）",
                       [{"field": "content.effective_from", "reason": f"before {cur['frozen_at']}"}])
    case = _visible_case(db, content["case_id"], replay_id)
    _check_body(db, content, replay_id, case)
    p = _plan_obj(plan_id, cur["version"] + 1, cur["version"], content, replay_id, clock.utc_iso())
    _insert(c, p)
    return p


def freeze(c, db, plan_id: str, expected_version: int, replay_id) -> dict:
    cur = latest(db, plan_id, replay_id)
    if cur is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "计划不存在或不可见")
    if expected_version != cur["version"]:
        raise ApiError(409, "PLAN_VERSION_CONFLICT", "计划已出现更新版本",
                       [{"field": "expected_version", "reason": f"expected {expected_version}, current {cur['version']}"}])
    if cur["status"] == "frozen":
        return cur          # 相同冻结内容重试：返回原哈希
    if cur["purpose"] == "simulation":
        miss = _completeness(cur)
        if miss:
            raise ApiError(422, "SIMULATION_PLAN_INCOMPLETE", "simulation 计划缺少必要条件，不能冻结为可执行计划", miss)
    elig, reasons = eligibility(db, cur, replay_id)
    p = dict(cur)
    p.update({"status": "frozen", "frozen_at": clock.utc_iso(), "execution_eligibility": elig,
              "execution_eligibility_reasons": reasons})
    p["frozen_hash"] = content_hash({k: v for k, v in p.items() if k != "frozen_hash"})
    c.execute("""UPDATE plan_versions SET status='frozen', frozen_at=?, frozen_hash=?, execution_eligibility=?,
                 canonical_payload=? WHERE plan_id=? AND version=? AND status='draft'""",
              (p["frozen_at"], p["frozen_hash"], elig, canonical_json(p), plan_id, cur["version"]))
    faults.checkpoint("after_event_before_projection_commit")
    return p


def effective_version(db, plan_id: str, as_of: str, replay_id) -> dict | None:
    """as_of 时刻生效的版本：effective_from ≤ as_of 的最新冻结版本。"""
    rows = db.query("SELECT canonical_payload FROM plan_versions WHERE plan_id=? AND status='frozen' AND replay_id IS ? "
                    "ORDER BY version", (plan_id, replay_id))
    best = None
    t = clock.parse_utc(as_of)
    for r in rows:
        p = json.loads(r["canonical_payload"])
        if clock.parse_utc(p["effective_from"]) <= t:
            best = p
    return best


# ── 生命周期事件 ──
_NEXT = {None: {"activated", "cancelled", "expired"},
         "activated": {"trigger_observed", "cancelled", "expired", "execution_unfilled", "execution_filled"},
         "trigger_observed": {"execution_filled", "execution_unfilled", "cancelled", "expired", "trigger_observed"},
         "execution_unfilled": {"trigger_observed", "execution_filled", "cancelled", "expired", "execution_unfilled"},
         "execution_filled": {"execution_filled", "expired"},
         "cancelled": set(), "expired": set()}


def append_event(c, db, plan_id: str, body: dict, replay_id) -> dict:
    v = version(db, plan_id, body["plan_version"], replay_id)
    if v is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "计划版本不存在或不可见")
    if v["status"] != "frozen" or v["frozen_hash"] != body["frozen_hash"]:
        raise ApiError(409, "PLAN_NOT_FROZEN_OR_HASH_MISMATCH", "生命周期事件只能追加到已冻结且哈希一致的版本",
                       [{"field": "frozen_hash", "reason": "mismatch or not frozen"}])
    last = c.execute("""SELECT event_id, event_type FROM plan_lifecycle_events WHERE plan_id=? AND plan_version=?
                        ORDER BY recorded_at DESC, rowid DESC LIMIT 1""", (plan_id, body["plan_version"])).fetchone()
    last_id = last["event_id"] if last else None
    if body["expected_previous_event_id"] != last_id:
        raise ApiError(409, "PLAN_EVENT_CONFLICT", "生命周期事件链已变化",
                       [{"field": "expected_previous_event_id", "reason": f"current {last_id}"}])
    allowed = _NEXT[last["event_type"] if last else None]
    if body["event_type"] not in allowed:
        raise ApiError(409, "PLAN_LIFECYCLE_INVALID_TRANSITION",
                       f"{last['event_type'] if last else '（无）'} 之后不能追加 {body['event_type']}",
                       [{"field": "event_type", "reason": "invalid transition"}])
    if body["event_type"] == "execution_filled":
        if not body["journal_event_id"]:
            raise ApiError(422, "JOURNAL_EVENT_REQUIRED", "execution_filled 必须引用真实的成交事件",
                           [{"field": "journal_event_id", "reason": "required"}])
        je = c.execute("SELECT plan_id, plan_version, event_type, replay_id FROM journal_events WHERE event_id=?",
                       (body["journal_event_id"],)).fetchone()
        if je is None or je["replay_id"] != replay_id or je["plan_id"] != plan_id or je["plan_version"] != body["plan_version"] \
                or je["event_type"] not in ("paper_fill", "manual_trade_record"):
            raise ApiError(422, "JOURNAL_EVENT_INVALID", "引用的成交事件不存在、不属于该计划版本或不是成交",
                           [{"field": "journal_event_id", "reason": "invalid"}])
    if body["snapshot_id"] and snapshot_service.get(db, body["snapshot_id"], replay_id) is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "快照不存在或不可见", [{"field": "snapshot_id", "reason": "not visible"}])
    if clock.parse_utc(body["effective_at"]) < clock.parse_utc(v["frozen_at"]):
        raise ApiError(422, "EVENT_BEFORE_FREEZE", "生命周期事件不能早于计划冻结时间",
                       [{"field": "effective_at", "reason": f"before {v['frozen_at']}"}])
    ev = {"event_id": new_id("planevt"), "plan_id": plan_id, "plan_version": body["plan_version"],
          "frozen_hash": body["frozen_hash"], "event_type": body["event_type"], "effective_at": body["effective_at"],
          "recorded_at": clock.utc_iso_ms(), "snapshot_id": body["snapshot_id"], "condition_ids": body["condition_ids"],
          "journal_event_id": body["journal_event_id"], "previous_event_id": last_id, "reason": body["reason"],
          "replay_id": replay_id}
    ev_db = dict(ev)
    ev["recorded_at"] = ev["recorded_at"][:19] + "Z"
    c.execute("""INSERT INTO plan_lifecycle_events(event_id, plan_id, plan_version, frozen_hash, event_type, effective_at,
                 recorded_at, snapshot_id, journal_event_id, previous_event_id, replay_id, reason, canonical_payload)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (ev["event_id"], plan_id, body["plan_version"], body["frozen_hash"], body["event_type"], body["effective_at"],
               ev_db["recorded_at"], body["snapshot_id"], body["journal_event_id"], last_id, replay_id, body["reason"],
               canonical_json(ev)))
    return ev


def list_events(db, plan_id: str, replay_id, version_: int | None) -> list[dict]:
    sql = "SELECT canonical_payload FROM plan_lifecycle_events WHERE plan_id=? AND replay_id IS ?"
    args: list = [plan_id, replay_id]
    if version_ is not None:
        sql += " AND plan_version=?"
        args.append(version_)
    sql += " ORDER BY recorded_at, rowid"
    return [json.loads(r["canonical_payload"]) for r in db.query(sql, args)]
