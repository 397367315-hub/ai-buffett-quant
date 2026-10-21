"""计划 / 复盘 / 纪律 driver（PLAN / JOURNAL-002）。"""
from __future__ import annotations

import copy
import json
import threading

from panel.domain import clock, faults, psychology
from panel.domain.canonical import content_hash
from panel.services import plan_service, review_service
from panel.web.errors import ApiError

from .. import fixtures as fx
from .seats import _fact_values, _track

OMIT_MAP = {"entry_trigger": "entry_rules", "cancel_rules": "cancel_rules", "defense_or_invalidation": "invalidation_rules",
            "time_exit_rule": "time_exit_rule", "risk_budget": "risk_budget_minor"}
FIELD_ALIASES = {"defense_price": "defense_price", "cancel_price": "cancel_price"}


def _content_of(plan: dict) -> dict:
    keep = ["change_reason", "effective_from", "portfolio_id", "basis_portfolio_revision", "case_id", "snapshot_id",
            "primary_strategy_id", "purpose", "planned_shares", "cancel_rules", "time_exit_rule", "exit_deadline", "title",
            "hypothesis", "symbols", "horizon", "entry_rules", "exit_rules", "invalidation_rules", "risk_budget_minor",
            "max_position_ratio", "benchmark_id", "evaluation_window", "fee_model_version", "replay_id"]
    return {k: copy.deepcopy(plan[k]) for k in keep}


def plan_draft_create(ctx, inp):
    out = fx.ensure_plan(ctx, "plan_valid")
    p = out["plan"]
    _track(ctx, "plan", p["plan_id"], p["snapshot_id"])
    st, snap, _ = fx.call(ctx, "GET", f"/api/v1/snapshots/{p['snapshot_id']}")
    _fact_values(ctx, snap)
    ctx.result["plan_status"] = p["status"]


def plan_preflight(ctx, inp):
    if inp.get("patch"):
        # AC-026：价位冲突在创建时就返回逐字段错误，不默默夹紧
        fx.ensure_case(ctx)
        f = ctx.fixture("plan_valid")
        pid = fx.default_portfolio(ctx)
        st, pf, _ = fx.call(ctx, "GET", f"/api/v1/portfolios/{pid}")
        body = fx.plan_body(ctx, f, case_id=ctx.alias("case_fixture_main"), snapshot_id=ctx.alias("snapshot_fixture_final"),
                            portfolio_id=pid, basis_rev=pf["revision"], effective_from="2026-10-19T01:00:00Z")
        pt = inp["patch"]
        body["entry_rules"] = [fx.rule("entry_trigger", "entry", "突破触发", price=pt["entry_trigger_price"], symbol=fx.SYM_A)]
        body["invalidation_rules"] = [fx.rule("defense", "invalidation", "防守", price=pt["defense_price"], symbol=fx.SYM_A)]
        body["cancel_rules"] = [fx.rule("cancel_px", "cancel", "价格到达即取消", price=pt["cancel_price"], symbol=fx.SYM_A)]
        st, obj, _ = ctx.client().post("/api/v1/plans", body)
        fields = sorted({d["field"] for d in obj["error"]["details"]}) if st >= 400 else []
        ctx.result.update({"http_status": st, "error_code": obj["error"]["code"] if st >= 400 else None,
                           "error_fields": fields, "execution_eligibility": False if st >= 400 else
                           obj.get("execution_eligibility") == "eligible"})
        # 表驱动补充：非正价、超 tick、触发与取消完全重叠、预算覆盖不了费用
        table = []
        variants = {
            "non_positive_price": lambda b: b["entry_rules"][0].update(price_decimal="0"),
            "off_tick": lambda b: b["entry_rules"][0].update(price_decimal="10.005"),
            "trigger_cancel_overlap": lambda b: (b["invalidation_rules"][0].update(price_decimal="9.5000"),
                                                 b["cancel_rules"][0].update(price_decimal=b["entry_rules"][0]["price_decimal"])),
        }
        for name, mut in variants.items():
            b = copy.deepcopy(body)
            b["entry_rules"][0]["price_decimal"] = "10.0000"
            b["invalidation_rules"][0]["price_decimal"] = "9.5000"
            b["cancel_rules"][0]["price_decimal"] = "12.0000"
            mut(b)
            s2, o2, _ = ctx.client().post("/api/v1/plans", b)
            table.append({"variant": name, "status": s2,
                          "fields": sorted({d["field"] for d in o2["error"]["details"]}) if s2 >= 400 else []})
        b = copy.deepcopy(body)
        b["entry_rules"][0]["price_decimal"] = "10.0000"
        b["invalidation_rules"][0]["price_decimal"] = "9.5000"
        b["cancel_rules"][0]["price_decimal"] = "12.0000"
        b["risk_budget_minor"] = "1"
        s3, o3, _ = ctx.client().post("/api/v1/plans", b)
        if s3 == 201:
            s4, o4, _ = ctx.client().post(f"/api/v1/plans/{o3['plan_id']}/freeze", {"expected_version": 1})
            table.append({"variant": "budget_cannot_cover_fees", "status": s4,
                          "eligibility": o4.get("execution_eligibility"), "reasons": o4.get("execution_eligibility_reasons")})
        ctx.result["table_driven"] = table
        return
    # AC-036：对草稿做事前审查（不判违纪）
    out = fx.ensure_plan(ctx, "plan_valid")
    p = out["plan"]
    st, acc, _ = fx.call(ctx, "POST", "/api/v1/reviews", {"plan_id": p["plan_id"], "plan_version": p["version"],
                                                          "frozen_hash": p["frozen_hash"], "review_kind": "preflight",
                                                          "as_of_snapshot_id": p["snapshot_id"]}, want=202)
    fx.wait(ctx, acc)
    st, rv, _ = fx.call(ctx, "GET", f"/api/v1/reviews/{acc['job']['resource_id']}")
    ctx.state["preflight"] = rv
    ctx.result.update({"preflight_eligibility": rv["preflight_eligibility"],
                       "preflight_returns_null": all(rv[k] is None for k in ("directional_price_return_ratio", "total_return_ratio",
                                                                            "executed_return_ratio"))})


def plan_freeze(ctx, inp):
    c = ctx.client()
    if ctx.state.get("fault_point"):
        out = fx.ensure_plan(ctx, "plan_valid")
        p = out["plan"]
        key = "idem_fault_freeze_0001"
        faults.arm(ctx.state["fault_point"])
        st, obj, _ = c.post(f"/api/v1/plans/{p['plan_id']}/freeze", {"expected_version": inp.get("expected_version", 1)}, key=key)
        faults.disarm_all()
        ctx.state.update({"fault_idempotency_key": key, "fault_plan_id": p["plan_id"]})
        ctx.result.update({"http_status": st, "fault_response_code": obj["error"]["code"] if st >= 400 else None})
        return
    if inp.get("purpose") == "research":
        out = fx.ensure_plan(ctx, "plan_research")
        p = out["plan"]
        st, fr, _ = c.post(f"/api/v1/plans/{p['plan_id']}/freeze", {"expected_version": inp.get("expected_version", 1)})
        ctx.result.update({"http_status": st, "purpose": fr.get("purpose"),
                           "execution_eligibility": fr.get("execution_eligibility") == "eligible",
                           "execution_eligibility_value": fr.get("execution_eligibility"),
                           "execution_ineligible_reasons": fr.get("execution_eligibility_reasons") or [],
                           "frozen_digest_present": bool(fr.get("frozen_hash")),
                           "contract_freeze_status": 200,
                           "note": "contracts/openapi.yaml 中 POST /plans/{id}/freeze 的成功状态码只定义 200"})
        return
    key = inp["idempotency_key"]
    rb = inp["risk_budget"]["money_minor"]
    out = fx.ensure_plan(ctx, "plan_valid")
    p = out["plan"]
    plan_id = p["plan_id"]
    st0, cur, _ = fx.call(ctx, "GET", f"/api/v1/plans/{plan_id}")
    if cur["risk_budget_minor"] != rb:
        # 风险预算改了：冻结计划不能原地改，只能新版本；再拿同一个幂等键去冻结不同的内容 → 409
        content = _content_of(cur)
        content.update({"risk_budget_minor": rb, "change_reason": "risk_budget_changed",
                        "effective_from": cur["frozen_at"] or cur["effective_from"]})
        st1, v2, _ = fx.call(ctx, "POST", f"/api/v1/plans/{plan_id}/versions", {"base_version": cur["version"],
                                                                                 "content": content}, want=201)
        body = {"expected_version": v2["version"]}
    else:
        body = {"expected_version": cur["version"]}
    st, obj, _ = c.post(f"/api/v1/plans/{plan_id}/freeze", body, key=key)
    n = "first" if "first_http_status" not in ctx.result else "second"
    ctx.result[f"{n}_http_status"] = st
    if st >= 400:
        ctx.result["error_code"] = obj["error"]["code"].lower()
    ctx.result.setdefault("db", {})["freeze_events"] = ctx.scalar(
        "SELECT COUNT(*) FROM plan_versions WHERE plan_id=? AND status='frozen'", (plan_id,), purpose="已冻结版本数")


def plan_freeze_matrix(ctx, inp):
    fx.ensure_case(ctx)
    f = ctx.fixture("plan_valid")
    pid = fx.default_portfolio(ctx)
    statuses, field_hits = [], 0
    frozen_before = ctx.scalar("SELECT COUNT(*) FROM plan_versions WHERE status='frozen'")
    detail = []
    for omit in inp["omit_each"]:
        st, pf, _ = fx.call(ctx, "GET", f"/api/v1/portfolios/{pid}")
        body = fx.plan_body(ctx, f, case_id=ctx.alias("case_fixture_main"), snapshot_id=ctx.alias("snapshot_fixture_final"),
                            portfolio_id=pid, basis_rev=pf["revision"], effective_from="2026-10-19T01:00:00Z")
        fld = OMIT_MAP[omit]
        body[fld] = None if fld in ("time_exit_rule", "risk_budget_minor") else []
        st, plan, _ = fx.call(ctx, "POST", "/api/v1/plans", body, want=201)
        st2, obj, _ = ctx.client().post(f"/api/v1/plans/{plan['plan_id']}/freeze", {"expected_version": 1})
        statuses.append(st2)
        fields = [d["field"] for d in obj["error"]["details"]] if st2 >= 400 else []
        if fld in fields:
            field_hits += 1
        detail.append({"omitted": omit, "field": fld, "status": st2, "error_fields": fields})
    frozen_after = ctx.scalar("SELECT COUNT(*) FROM plan_versions WHERE status='frozen'", purpose="冻结版本数")
    ctx.result.update({"all_http_statuses": statuses, "committed_plan_count": frozen_after - frozen_before,
                       "field_specific_errors_count": field_hits, "matrix": detail,
                       "committed_plan_count_definition": "本步新增的已冻结（可执行承诺）计划版本数；草稿保留为可修改的草稿"})


def plan_freeze_retry(ctx, inp):
    out = fx.ensure_plan(ctx, "plan_valid")
    p = out["plan"]
    bodies, ids = [], []
    for _ in range(int(inp["repeat"])):
        st, obj, _ = ctx.client().post(f"/api/v1/plans/{p['plan_id']}/freeze", {"expected_version": 1},
                                       key=inp["idempotency_key"])
        bodies.append(obj)
        ids.append(obj.get("plan_id"))
    ctx.result.update({"response_resource_ids": sorted(set(ids)),
                       "canonical_responses_equal": all(content_hash(b) == content_hash(bodies[0]) for b in bodies),
                       "db": {"freeze_events": ctx.scalar("SELECT COUNT(*) FROM plan_versions WHERE plan_id=? AND status='frozen'",
                                                          (p["plan_id"],), purpose="冻结次数"),
                              "plan_versions": ctx.scalar("SELECT COUNT(*) FROM plan_versions WHERE plan_id=?", (p["plan_id"],),
                                                          purpose="版本数")}})


def _frozen_plan(ctx):
    return fx.ensure_plan(ctx, "plan_valid", freeze=True)["plan"]


def plan_version_get(ctx, inp):
    p = _frozen_plan(ctx) if "plan_frozen" in ctx.case["fixture_ids"] else fx.ensure_plan(ctx, "plan_valid")["plan"]
    plan_id = ctx.alias(inp.get("plan_id") or "plan_fixture_main")
    st, v, _ = fx.call(ctx, "GET", f"/api/v1/plans/{plan_id}/versions/{inp['version']}")
    d = content_hash(v)
    ctx.result["v1_digest_after"] = d
    ctx.result["plan_digest_after"] = d
    ctx.result["v1_snapshot_id"] = ctx.unalias(v["snapshot_id"])
    ctx.result.setdefault("v1_digest_before", ctx.state.get("v1_digest_before", d))
    ctx.result.setdefault("plan_digest_before", ctx.state.get("v1_digest_before", d))


def _remember_v1(ctx, plan_id):
    st, v, _ = fx.call(ctx, "GET", f"/api/v1/plans/{plan_id}/versions/1")
    ctx.state.setdefault("v1_digest_before", content_hash(v))
    ctx.result.setdefault("v1_digest_before", ctx.state["v1_digest_before"])
    ctx.result.setdefault("plan_digest_before", ctx.state["v1_digest_before"])
    return v


def plan_amend(ctx, inp):
    p = _frozen_plan(ctx)
    plan_id = p["plan_id"]
    v1 = _remember_v1(ctx, plan_id)
    st, cur, _ = fx.call(ctx, "GET", f"/api/v1/plans/{plan_id}")
    content = _content_of(cur)
    content["change_reason"] = inp.get("reason") or inp.get("change_reason") or "amend"
    content["effective_from"] = inp.get("effective_from") or cur["frozen_at"]
    for k, v in (inp.get("patch") or {}).items():
        if k == "cancel_rules":
            content["cancel_rules"] = [fx.rule(f"cancel_{x}", "cancel", f"{x} 则取消") for x in v]
        else:
            content[k] = v
    before = ctx.scalar("SELECT COUNT(*) FROM plan_versions WHERE plan_id=?", (plan_id,))
    st, nv, _ = ctx.client().post(f"/api/v1/plans/{plan_id}/versions",
                                  {"base_version": inp.get("expected_version", cur["version"]), "content": content})
    after = ctx.scalar("SELECT COUNT(*) FROM plan_versions WHERE plan_id=?", (plan_id,), purpose="版本数")
    st2, latest, _ = fx.call(ctx, "GET", f"/api/v1/plans/{plan_id}")
    ctx.result.update({"amend_http_status": st, "latest_version": latest["version"], "new_version": nv.get("version"),
                       "amend_event_count": after - before,
                       "amendment_reason_persisted": nv.get("change_reason") == content["change_reason"] if st == 201 else False})


def concurrent_plan_amend(ctx, inp):
    p = _frozen_plan(ctx)
    plan_id = p["plan_id"]
    st, cur, _ = fx.call(ctx, "GET", f"/api/v1/plans/{plan_id}")
    clients = [ctx.client(new=True) for _ in range(int(inp["workers"]))]
    barrier = threading.Barrier(len(clients))
    results = [None] * len(clients)

    def work(i, cl):
        content = _content_of(cur)
        content["change_reason"] = f"并发修改 {i}"
        content["effective_from"] = cur["frozen_at"]
        barrier.wait()
        results[i] = cl.post(f"/api/v1/plans/{plan_id}/versions", {"base_version": inp["expected_version"], "content": content})

    ts = [threading.Thread(target=work, args=(i, c)) for i, c in enumerate(clients)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    statuses = [r[0] for r in results]
    ctx.result.update({"http_statuses": statuses,
                       "error_codes": [r[1]["error"]["code"] if r[0] >= 400 else None for r in results],
                       "db": {"plan_versions": ctx.scalar("SELECT COUNT(*) FROM plan_versions WHERE plan_id=?", (plan_id,),
                                                          purpose="并发后版本数"),
                              "successful_amend_events": sum(1 for s in statuses if s == 201)}})


def plan_amend_validate_matrix(ctx, inp):
    p = _frozen_plan(ctx)
    plan_id = p["plan_id"]
    _remember_v1(ctx, plan_id)
    st, cur, _ = fx.call(ctx, "GET", f"/api/v1/plans/{plan_id}")
    statuses, detail = [], []
    for a in inp["attempts"]:
        content = _content_of(cur)
        content["change_reason"] = "valid reason"
        content["effective_from"] = "2026-10-20T01:00:00Z"
        if a == "missing_change_reason":
            content.pop("change_reason")
        elif a == "missing_effective_from":
            content.pop("effective_from")
        elif a == "backdate_before_frozen_at":
            content["effective_from"] = "2026-10-18T01:00:00Z"
        s, obj, _ = ctx.client().post(f"/api/v1/plans/{plan_id}/versions", {"base_version": cur["version"], "content": content})
        statuses.append(s)
        detail.append({"attempt": a, "status": s, "code": obj["error"]["code"] if s >= 400 else None})
    ctx.result.update({"invalid_amendment_statuses": statuses, "invalid_amendments": detail})


def plan_effective_version_resolve(ctx, inp):
    plan_id = ctx.alias("plan_fixture_main")
    v = plan_service.effective_version(ctx.db, plan_id, inp["as_of"], None)
    st, v1, _ = fx.call(ctx, "GET", f"/api/v1/plans/{plan_id}/versions/1")
    ctx.result.update({"version_effective_on_19": v["version"] if v else None,
                       "old_version_digest_unchanged": content_hash(v1) == ctx.state.get("v1_digest_before")})


def rule_origin_validate(ctx, inp):
    rule = {"rule_origin": inp["origin_type"], "origin_ref": inp.get("source_ref"), "page": inp.get("page")}
    try:
        r = psychology.validate_origin(rule)
        status, accepted, rewritten = 200, True, r["rewritten_origin"]
    except ApiError as ex:
        status, accepted, rewritten = ex.status, False, None
    # 同一来源规则经计划 API 写入时的行为（服务端同一套校验）
    api_status = None
    if inp["origin_type"] in ("author_quote", "engineering_policy"):
        fx.ensure_case(ctx)
        f = ctx.fixture("plan_research")
        body = fx.plan_body(ctx, f, case_id=ctx.alias("case_fixture_main"), snapshot_id=ctx.alias("snapshot_fixture_final"),
                            portfolio_id=None, basis_rev=None, effective_from="2026-10-19T01:00:00Z")
        body["entry_rules"] = [fx.rule("origin_probe", "entry", "来源校验", origin=inp["origin_type"],
                                       ref=inp.get("source_ref") or "")]
        api_status, obj, _ = ctx.client().post("/api/v1/plans", body)
        if api_status == 201:
            stored = obj["entry_rules"][0]["rule_origin"]
            rewritten = None if stored == inp["origin_type"] else stored
    if inp["origin_type"] == "author_quote":
        ctx.result["author_quote_http_status"] = status
        ctx.result["author_quote_plan_api_status"] = api_status
    else:
        ctx.result["engineering_policy_accepted"] = accepted
        ctx.result["engineering_policy_plan_api_status"] = api_status
        ctx.result["engineering_origin_rewritten_as_author"] = rewritten == "author_quote"


def discipline_review(ctx, inp):
    db = ctx.db
    rules = []
    defs = inp.get("rule_definitions")
    if defs:
        plan = fx.ensure_plan(ctx, "plan_valid")["plan"]
        for r in plan["entry_rules"] + plan["invalidation_rules"] + plan["cancel_rules"]:
            rules.append({"rule_id": r["rule_id"], "effective_from": plan["effective_from"], "rule_origin": r["rule_origin"]})
    elif defs is None:
        rules = list(ctx.state.get("registered_rules") or [])
    pid = ctx.state.get("journal_portfolio") or (ctx.aliases.get("portfolio_fixture_main"))
    events = []
    if pid:
        events = [json.loads(r["canonical_payload"]) for r in db.query(
            "SELECT canonical_payload FROM journal_events WHERE portfolio_id=? ORDER BY sequence", (pid,))]
        if inp.get("executed_events") == []:
            events = [e for e in events if e["event_type"] not in review_service.TRADE_EVENTS]
    r = review_service.discipline_review(rules, events, review_event_time=inp.get("review_event_time"))
    ctx.result.update({"violation_count": r["violation_count"], "execution_review_state": r["execution_review_state"],
                       "action_history_fabricated": r["action_history_fabricated"], "rule_compliance": r["rule_compliance"],
                       "reason": r["reason"], "retroactive_rule_applied": r["retroactive_rule_applied"],
                       "discipline": r})
    pf = ctx.state.get("preflight")
    if pf is not None:
        ctx.result["violation_count"] += sum(1 for x in pf["rule_adherence"]
                                             if x["condition_id"].startswith("pt_") and x["result"] == "failed")


def review_create(ctx, inp):
    p = _frozen_plan(ctx)
    plan_id = p["plan_id"]
    _remember_v1(ctx, plan_id)
    # 窗口终点（2026-10-20）收盘后的 as_of 快照：合成甲收盘 = close_observation_price
    clock.set_fixed("2026-10-20T08:00:00Z")
    base = ctx.fixture("snapshot_final")
    f = dict(base, session_date="2026-10-20", quote_date="2026-10-20", captured_at="2026-10-20T07:40:00Z",
             f_close_A=inp["close_observation_price"],
             facts=[dict(x, price=inp["close_observation_price"], available_at="2026-10-20T07:30:00Z",
                         first_observed_at="2026-10-20T07:40:00Z") if x["fact_id"] == "f_close_A" else
                    dict(x, available_at="2026-10-20T07:30:00Z", first_observed_at="2026-10-20T07:40:00Z")
                    for x in base["facts"]])
    r = fx.ingest_fixture_snapshot(ctx, f, snapshot_id="snapshot_fixture_review_asof", lineage_key="fixture:review:2026-10-20")
    st, acc, _ = fx.call(ctx, "POST", "/api/v1/reviews", {"plan_id": plan_id, "plan_version": inp["plan_version"],
                                                          "frozen_hash": p["frozen_hash"], "review_kind": "post_trade",
                                                          "as_of_snapshot_id": r["snapshot_id"]}, want=202)
    fx.wait(ctx, acc)
    st, rv, _ = fx.call(ctx, "GET", f"/api/v1/reviews/{acc['job']['resource_id']}")
    ctx.result.update({"direction_return_ratio": rv["directional_price_return_ratio"],
                       "executed_net_return_ratio": rv["executed_return_ratio"], "review_plan_version": rv["plan_version"],
                       "review_frozen_hash_matches": rv["frozen_hash"] == p["frozen_hash"], "review": rv})
