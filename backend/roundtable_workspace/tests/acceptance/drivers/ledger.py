"""账本 / 模拟成交 / 冷却 / 日记 / 自检 driver（JOURNAL / DATA-003）。"""
from __future__ import annotations

import json
import threading
from decimal import Decimal

from panel.domain import clock, fill_model, psychology
from panel.domain.canonical import content_hash
from panel.services import journal_service as js
from panel.services import plan_service

from .. import fixtures as fx
from ..context import network_attempts

QUOTE_DAY = "2026-10-19"
QUOTE_AT = "2026-10-19T01:50:00Z"       # 09:50 上海：连续竞价
FILL_AT = "2026-10-19T02:00:00Z"        # 10:00 上海


def fill_body(pid, rev, *, side, shares, price, snapshot_id, plan=None, symbol=fx.SYM_A, effective_at=None):
    return {"portfolio_id": pid, "event_type": "paper_fill", "mode": "paper", "effective_at": effective_at or clock.utc_iso(),
            "symbol": symbol, "side": side, "shares": shares, "price_decimal": price, "net_cash_delta_minor": None,
            "snapshot_id": snapshot_id, "plan_id": plan["plan_id"] if plan else None,
            "plan_version": plan["version"] if plan else None, "corrects_event_id": None, "correction_action": None,
            "corrected_event_type": None, "note": "", "replay_id": None, "expected_portfolio_revision": rev,
            "evidence_ids": [], "reported_fees": None}


def _pf(ctx, pid):
    return fx.call(ctx, "GET", f"/api/v1/portfolios/{pid}")[1]


def _cash_and_shares(ctx, pid, sym=fx.SYM_A):
    p = _pf(ctx, pid)
    pos = next((x for x in p["positions"] if x["symbol"] == sym), None)
    return int(p["cash_minor"]), (pos["shares"] if pos else 0), p["revision"]


# ── 日记 / 行为 ──
def journal_event_append(ctx, inp):
    ctx.harness()
    et = inp["event_type"]
    if et == "manual_trade_report":
        if inp.get("recorded_at"):
            clock.set_fixed(inp["recorded_at"])
        st, pf, _ = fx.call(ctx, "POST", "/api/v1/portfolios", {"mode": "user_reported", "initial_cash_minor": "1000000",
                                                                 "name": "我自报的账户（未核验）"}, want=201)
        pid = pf["portfolio_id"]
        body = {"portfolio_id": pid, "event_type": "manual_trade_record", "mode": "user_reported",
                "effective_at": inp.get("recorded_at") or clock.utc_iso(), "symbol": fx.SYM_A, "side": "buy", "shares": 100,
                "price_decimal": "10.0000", "net_cash_delta_minor": "-100501", "snapshot_id": None, "plan_id": None,
                "plan_version": None, "corrects_event_id": None, "correction_action": None, "corrected_event_type": None,
                "note": "我自己报的一笔成交", "replay_id": None, "expected_portfolio_revision": pf["revision"],
                "evidence_ids": [], "reported_fees": None}
        st, ev, _ = fx.call(ctx, "POST", "/api/v1/journal/events", body, want=201)
        clock.set_fixed(ctx.fixture("clock_monday")["now"])
    else:
        j = ctx.fixture("journal")
        clock.set_fixed(j.get("clock") or clock.utc_iso())
        pid = ctx.state.get("journal_portfolio")
        if not pid:
            st, pf, _ = fx.call(ctx, "POST", "/api/v1/portfolios", {"mode": "paper", "initial_cash_minor": "1000000",
                                                                     "name": "日记组合"}, want=201)
            pid = pf["portfolio_id"]
        rev = _pf(ctx, pid)["revision"]
        note = json.dumps({"schema": "diary_v1", "kind": et, "self_report": inp.get("self_report") or ""}, ensure_ascii=False)
        st, ev, _ = fx.call(ctx, "POST", "/api/v1/journal/events", fx.note_body(pid, rev, note), want=201)
    ctx.state["journal_portfolio"] = pid
    ctx.result["journal_event_id"] = ev["event_id"]
    ctx.result["journal_event_type"] = ev["event_type"]


def behavior_summary(ctx, inp):
    pid = ctx.state["journal_portfolio"]
    st, evs, _ = fx.call(ctx, "GET", f"/api/v1/journal/events?portfolio_id={pid}")
    r = js.behavior_summary(evs["items"])
    ctx.result.update({k: r[k] for k in ("observed_event_count", "diagnosis", "long_term_habit_asserted",
                                         "recommended_action")})


def user_position_import(ctx, inp):
    f = ctx.fixture(inp.get("fixture_id") or "user_manual_position")
    before = len(network_attempts())
    st, pf, _ = fx.call(ctx, "POST", "/api/v1/portfolios", {"mode": "user_reported", "initial_cash_minor": "0",
                                                             "name": "用户陈述持仓"}, want=201)
    gross = Decimal(f["cost_price"]) * f["shares"]
    body = {"portfolio_id": pf["portfolio_id"], "event_type": "manual_trade_record", "mode": "user_reported",
            "effective_at": clock.utc_iso(), "symbol": f["symbol"], "side": "buy", "shares": f["shares"],
            "price_decimal": f["cost_price"], "net_cash_delta_minor": str(-int(gross * 100)), "snapshot_id": None,
            "plan_id": None, "plan_version": None, "corrects_event_id": None, "correction_action": None,
            "corrected_event_type": None, "note": "用户陈述的持仓", "replay_id": None,
            "expected_portfolio_revision": pf["revision"], "evidence_ids": [], "reported_fees": None}
    st, ev, _ = fx.call(ctx, "POST", "/api/v1/journal/events", body, want=201)
    p = _pf(ctx, pf["portfolio_id"])
    ctx.result.update({"portfolio_mode": p["mode"], "event_mode": ev["mode"],
                       "source_mode": "user_manual" if ev["mode"] == "user_reported" else ev["mode"],
                       "verified_by_broker": ("未经券商核验" not in ev["note"]) or ev["mode"] != "user_reported",
                       "broker_connection_attempts": len(network_attempts()) - before,
                       "source_mode_mapping": "JournalEvent.mode=user_reported ≡ 来源 user_manual（用户陈述，不升级为券商核验）"})


# ── T+1 / 时段 / 停牌 / 跌停 / 日线路径 ──
def simulation_sell_preflight(ctx, inp):
    pid = ctx.ensure("ledger_t1")["portfolio_id"]
    c = ctx.db.conn()
    r = js.sell_preflight(c, pid, fx.SYM_A, int(inp["shares"]), inp["session_date"])
    ctx.result.setdefault("statuses", []).append(r["status"])
    ctx.result.setdefault("checks", []).append(r)
    if inp["session_date"] == "2026-10-19":
        ctx.result["available_shares_on_19"] = r["available_shares"]
    checks = ctx.result["checks"]
    ctx.result["today_buys_unlocked_early"] = any(x["session_date"] == "2026-10-19" and x["status"] == "allowed"
                                                  and int(x["available_shares"]) > 300 for x in checks)
    if len(checks) == 3 and not ctx.state.get("t1_http_checked"):
        # 同一结论在真实成交路径上再验一次：10/19 盘中卖 400 股 → T1_NOT_SELLABLE（不写事件）
        ctx.state["t1_http_checked"] = True
        clock.set_fixed(FILL_AT)
        sid = fx.intraday_snapshot(ctx, QUOTE_DAY, QUOTE_AT, {fx.SYM_A: "10.0000"})
        rev = _pf(ctx, pid)["revision"]
        st, obj, _ = ctx.client().post("/api/v1/journal/events", fill_body(pid, rev, side="sell", shares=400, price="10.0000",
                                                                           snapshot_id=sid))
        ctx.result["http_sell_400_on_19"] = {"status": st, "code": obj["error"]["code"] if st >= 400 else None}
        clock.set_fixed(ctx.fixture("clock_monday")["now"])


def simulation_preflight_matrix(ctx, inp):
    plan = fx.ensure_plan(ctx, "plan_valid", freeze=True)["plan"]
    pid = plan["portfolio_id"]
    cal = fx.calendar_of(ctx)
    fills_before = ctx.scalar("SELECT COUNT(*) FROM journal_events WHERE event_type='paper_fill'")
    values, detail = [], []
    for t in inp["times"]:
        clock.set_fixed(t)
        values.append(cal.can_simulate_new_trade(clock.parse_utc(t)))
        rev = _pf(ctx, pid)["revision"]
        st, obj, _ = ctx.client().post("/api/v1/journal/events", fill_body(pid, rev, side="buy", shares=100, price="10.0000",
                                                                           snapshot_id=plan["snapshot_id"], plan=plan))
        detail.append({"at": t, "phase": cal.phase(clock.parse_utc(t)), "status": st,
                       "code": obj["error"]["code"] if st >= 400 else None})
    fills_after = ctx.scalar("SELECT COUNT(*) FROM journal_events WHERE event_type='paper_fill'", purpose="模拟成交数")
    ctx.result.update({"can_execute_values": values, "attempts": detail, "created_fill_count": fills_after - fills_before})


def simulation_preflight(ctx, inp):
    """日历不可用：此刻能否生成新模拟成交 → 不能，原因 calendar_unavailable。"""
    plan = fx.ensure_plan(ctx, "plan_valid", freeze=True)["plan"]
    pid = plan["portfolio_id"]
    from panel.services import calendar_service
    cal = calendar_service.current(ctx.db)
    rev = _pf(ctx, pid)["revision"]
    st, obj, _ = ctx.client().post("/api/v1/journal/events", fill_body(pid, rev, side="buy", shares=100, price="10.0000",
                                                                       snapshot_id=plan["snapshot_id"], plan=plan))
    codes = [obj["error"]["code"].lower()] if st >= 400 else []
    ctx.result.update({"can_simulate_new_trade": bool(cal and cal.can_simulate_new_trade()) and st < 400,
                       "error_codes": codes, "fill_attempt_status": st})


def instrument_status_set(ctx, inp):
    pid = ctx.ensure("ledger_t1")["portfolio_id"]
    clock.set_fixed(FILL_AT)
    susp = [fx.SYM_A] if inp.get("status") == "suspended" else []
    sid = fx.intraday_snapshot(ctx, QUOTE_DAY, QUOTE_AT, {fx.SYM_A: "10.0000"}, suspended=susp)
    ctx.state["exec_snapshot"] = sid
    ctx.state["exec_portfolio"] = pid


def simulation_execute(ctx, inp):
    if inp.get("only_daily_data"):
        f = ctx.fixture("limit_down")
        bar = f["daily_bar"]
        pc = Decimal("10.0000")
        lower = (pc * Decimal("0.9")).quantize(Decimal("0.01"))
        r = fill_model.resolve_exit_on_daily(side=inp["side"], bar=bar, lower_limit=str(lower),
                                             verified_queue_liquidity=f.get("verified_queue_liquidity"))
        ctx.result.update({"fill_status": r["fill_status"], "executed_return_ratio": r["executed_return_ratio"],
                           "risk_exposure_remains": r["risk_exposure_remains"], "warning_codes": r["warning_codes"],
                           "trigger": inp.get("trigger"), "model": r["model"]})
        return
    pid = ctx.state["exec_portfolio"]
    sid = ctx.state["exec_snapshot"]
    cash0, sh0, rev = _cash_and_shares(ctx, pid)
    st, obj, _ = ctx.client().post("/api/v1/journal/events", fill_body(pid, rev, side=inp["side"], shares=int(inp["shares"]),
                                                                       price="10.0000", snapshot_id=sid))
    cash1, sh1, rev1 = _cash_and_shares(ctx, pid)
    det = {d["field"]: d["reason"] for d in obj["error"]["details"]} if st >= 400 else {}
    ctx.result.update({"http_status": st, "error_code": obj["error"]["code"] if st >= 400 else None,
                       "fill_status": det.get("fill_status") or ("filled" if st == 201 else None),
                       "fill_price": obj.get("price_decimal") if st == 201 else None,
                       "db": {"cash_change": {"money_minor": str(cash1 - cash0)}},
                       "position_preserved": sh1 == sh0, "revision_unchanged": rev1 == rev})


def simulation_resolve(ctx, inp):
    bar = {"open": inp["bar_open"], "high": inp["bar_high"], "low": inp["bar_low"], "close": inp["bar_close"]}
    r = fill_model.resolve_daily_bar(entry_trigger_price=inp["entry_trigger_price"], stop_price=inp["stop_price"], bar=bar)
    ctx.result.update({k: r[k] for k in ("exact_path_known", "fill_assumption_labeled", "verified_real_fill_count",
                                         "execution_return_authoritative", "possible_orders", "warnings")})


def simulation_fee_round_trip(ctx, inp):
    """真实两笔模拟成交：10/19 买 100 股 @10.00，10/20 卖 100 股 @11.00（fee_fixture_v1），从账本事件复算。"""
    plan = fx.ensure_plan(ctx, "plan_valid", freeze=True)["plan"]
    pid = plan["portfolio_id"]
    clock.set_fixed("2026-10-19T02:00:00Z")
    s1 = fx.intraday_snapshot(ctx, "2026-10-19", "2026-10-19T01:50:00Z", {fx.SYM_A: inp["buy_price"]},
                              pre_close={fx.SYM_A: "9.9500"})
    rev = _pf(ctx, pid)["revision"]
    st, buy, _ = fx.call(ctx, "POST", "/api/v1/journal/events",
                         fill_body(pid, rev, side="buy", shares=inp["shares"], price=inp["buy_price"], snapshot_id=s1, plan=plan),
                         want=201)
    p_hold = _pf(ctx, pid)
    clock.set_fixed("2026-10-20T02:00:00Z")
    s2 = fx.intraday_snapshot(ctx, "2026-10-20", "2026-10-20T01:50:00Z", {fx.SYM_A: inp["sell_price"]},
                              pre_close={fx.SYM_A: "10.5000"})
    rev = _pf(ctx, pid)["revision"]
    st, sell, _ = fx.call(ctx, "POST", "/api/v1/journal/events",
                          fill_body(pid, rev, side="sell", shares=inp["shares"], price=inp["sell_price"], snapshot_id=s2, plan=plan),
                          want=201)
    buy_total = -int(buy["net_cash_delta_minor"])
    sell_net = int(sell["net_cash_delta_minor"])
    pos = next((x for x in p_hold["positions"] if x["symbol"] == fx.SYM_A), None)
    rr = pos["unrealized_return_ratio"] if pos else None
    ctx.result.update({"buy_total": {"money_minor": str(buy_total)}, "sell_net": {"money_minor": str(sell_net)},
                       "net_pnl": {"money_minor": str(sell_net - buy_total)},
                       "buy_fees": buy["fees"], "sell_fees": sell["fees"],
                       "ratio_type": "decimal_string" if isinstance(rr, str) and rr.replace("-", "").replace(".", "").isdigit()
                       else type(rr).__name__, "sample_ratio": rr})


def portfolio_mark(ctx, inp):
    pid = ctx.ensure("ledger_t1")["portfolio_id"]
    f = ctx.fixture("snapshot_missing")
    from .data import _rows_for_variant
    rows, _ = _rows_for_variant(f)
    r = fx.ingest_fixture_snapshot(ctx, f, snapshot_id="snapshot_fixture_missing", rows=rows)
    st, p, _ = fx.call(ctx, "GET", f"/api/v1/portfolios/{pid}?snapshot_id={r['snapshot_id']}")
    pos = next(x for x in p["positions"] if x["symbol"] == fx.SYM_A)
    known = [int(x["market_value_minor"]) for x in p["positions"] if x["market_value_minor"] is not None]
    ctx.result.update({"position": {"market_value": pos["market_value_minor"], "unrealized_pnl": pos["unrealized_pnl_minor"],
                                    "cost_value_present": pos["cost_basis_minor"] is not None,
                                    "last_price": pos["last_price_decimal"]},
                       "portfolio": {"total_value": p["total_equity_minor"], "market_value": p["market_value_minor"],
                                     "known_subtotal_present": p["cash_minor"] is not None,
                                     "known_subtotal_minor": str(int(p["cash_minor"]) + sum(known)),
                                     "quality": p["quality"], "missing_inputs": p["missing_inputs"]}})


def simulation_reset(ctx, inp):
    pid = ctx.ensure("ledger_t1")["portfolio_id"]
    before = len(network_attempts())
    ev_before = [r["event_hash"] for r in ctx.q("SELECT event_hash FROM journal_events WHERE portfolio_id=? ORDER BY sequence",
                                                (pid,), purpose="重置前原账本事件")]
    st, newp, _ = ctx.client().post("/api/v1/portfolios", {"mode": "paper", "initial_cash_minor": "1000000",
                                                           "name": "模拟账户（重置后的新账户）"}, key=inp["idempotency_key"])
    ctx.result.setdefault("reset_statuses", []).append(st)
    ctx.result.setdefault("reset_portfolio_ids", []).append(newp.get("portfolio_id"))
    ev_after = [r["event_hash"] for r in ctx.q("SELECT event_hash FROM journal_events WHERE portfolio_id=? ORDER BY sequence",
                                               (pid,), purpose="重置后原账本事件")]
    st2, old, _ = ctx.client().get(f"/api/v1/portfolios/{pid}")
    chain = js.verify_chain(ctx.db.conn(), pid)
    ctx.result.update({"broker_calls": ctx.result.get("broker_calls", 0) + len(network_attempts()) - before,
                       "original_trade_events_preserved": ev_before == ev_after and len(ev_after) >= 3,
                       "db": {"reset_events": ctx.scalar("SELECT COUNT(*) FROM idempotency_keys WHERE idempotency_key=?",
                                                         (inp["idempotency_key"],), purpose="重置命令的幂等记录")},
                       "previous_account_auditable": st2 == 200 and chain["ok"],
                       "reset_model": "重置 = 新建一个模拟组合（幂等）；旧组合与全部事件原样保留可查，不删除、不改写"})


# ── 冷却 ──
def risk_action_preflight_matrix(ctx, inp):
    st = ctx.ensure("cooldown")
    pid = st["portfolio_id"]
    c = ctx.db.conn()
    allowed, denied = [], []
    for a in inp["actions"]:
        ok, why = js.risk_action_allowed(c, pid, a)
        (allowed if ok else denied).append(a)
    rev = _pf(ctx, pid)["revision"]
    s_note, _, _ = ctx.client().post("/api/v1/journal/events", fx.note_body(pid, rev, "冷却中记录一笔观察"))
    ctx.result.update({"allowed_actions": allowed, "denied_actions": denied,
                       "risk_reduction_hidden": "reduce_long" not in allowed, "journal_append_http_status": s_note})


def cooldown_rule_register(ctx, inp):
    lc = ctx.fixture("lifecycle")
    cfg = {"title": "用户冷却规则（fixture）", "rule_origin": lc["rule_origin"], "blocked_action_classes": ["increase_risk"],
           "allowed_action_classes": ["reduce_risk", "cancel", "record", "view"], "effective_from": inp["effective_from"]}
    fx.register_policy(ctx, lc["rule_id"], "v1", cfg)
    ctx.state.setdefault("registered_rules", []).append({"rule_id": lc["rule_id"], "effective_from": inp["effective_from"],
                                                         "rule_origin": lc["rule_origin"]})


def cooldown_activate(ctx, inp):
    lc = ctx.fixture("lifecycle")
    plan = fx.ensure_plan(ctx, "plan_valid", freeze=True)["plan"]
    pid = plan["portfolio_id"]
    rule_id = inp.get("rule_id") or lc["rule_id"]
    cfg = {"title": "用户冷却规则（fixture）", "rule_origin": inp.get("rule_origin") or lc["rule_origin"],
           "blocked_action_classes": ["increase_risk"], "allowed_action_classes": ["reduce_risk", "cancel", "record", "view"],
           "effective_from": lc["rule_effective_from"]}
    fx.register_policy(ctx, rule_id, "v1", cfg)
    st, a1, _ = fx.call(ctx, "POST", f"/api/v1/plans/{plan['plan_id']}/events",
                        {"plan_version": plan["version"], "frozen_hash": plan["frozen_hash"], "event_type": "activated",
                         "effective_at": clock.utc_iso(), "snapshot_id": None, "condition_ids": [], "journal_event_id": None,
                         "reason": "计划开始观察", "expected_previous_event_id": None}, want=201)
    st, ctl, _ = fx.call(ctx, "POST", f"/api/v1/portfolios/{pid}/controls/events",
                         fx.control_body("enabled", rule_id, "v1", clock.utc_iso(), None,
                                         reason=inp.get("reason") or "predefined_drawdown_limit"), want=201)
    st, a2, _ = fx.call(ctx, "POST", f"/api/v1/plans/{plan['plan_id']}/events",
                        {"plan_version": plan["version"], "frozen_hash": plan["frozen_hash"], "event_type": "execution_unfilled",
                         "effective_at": clock.utc_iso(), "snapshot_id": None, "condition_ids": [], "journal_event_id": None,
                         "reason": f"cooldown_active:{ctl['event_id']}（新开仓暂停，不是成交失败）",
                         "expected_previous_event_id": a1["event_id"]}, want=201)
    ctx.state.update({"cooldown_portfolio": pid, "cooldown_enabled_event": ctl, "cooldown_plan": plan,
                      "lifecycle_events": [a1["event_id"], a2["event_id"]], "rule_id": rule_id,
                      "trade_events_before": ctx.scalar("SELECT COUNT(*) FROM journal_events WHERE portfolio_id=? AND "
                                                        "event_type IN ('paper_fill','cash_adjustment','manual_trade_record')",
                                                        (pid,))})
    ctx.result["activation_event_preserved"] = True


def control_state_rebuild_from_events(ctx, inp):
    pid = ctx.state["cooldown_portfolio"]
    plan = ctx.state["cooldown_plan"]
    st, ctl, _ = fx.call(ctx, "GET", f"/api/v1/portfolios/{pid}/controls/events")
    st, pev, _ = fx.call(ctx, "GET", f"/api/v1/plans/{plan['plan_id']}/events")
    state = js.cooldown_state(ctx.db.conn(), pid)
    pol = ctx.db.one("SELECT config_json FROM control_policy_versions WHERE policy_id=? AND policy_version='v1'",
                     (ctx.state["rule_id"],))
    en = ctx.state["cooldown_enabled_event"]
    linked = any(en["event_id"] in e["reason"] for e in pev["items"]) and all(
        e["frozen_hash"] == plan["frozen_hash"] for e in pev["items"]) and pol is not None
    trades_after = ctx.scalar("SELECT COUNT(*) FROM journal_events WHERE portfolio_id=? AND "
                              "event_type IN ('paper_fill','cash_adjustment','manual_trade_record')", (pid,))
    ctx.result.update({"plan_lifecycle_event_persisted": {e["event_id"] for e in pev["items"]} >= set(ctx.state["lifecycle_events"]),
                       "portfolio_control_event_persisted": any(e["event_id"] == en["event_id"] for e in ctl["items"]),
                       "cooldown_active_after_restart": state["active"],
                       "rule_origin_preserved": json.loads(pol["config_json"])["rule_origin"] if pol else None,
                       "event_linkage_complete": linked,
                       "cash_trade_event_fabricated": trades_after != ctx.state["trade_events_before"]})


def cooldown_override(ctx, inp):
    if "cooldown_enabled_event" not in ctx.state:
        cooldown_activate(ctx, {"rule_id": inp.get("rule_id") or ctx.fixture("lifecycle")["rule_id"]})
    pid = ctx.state["cooldown_portfolio"]
    en = ctx.state["cooldown_enabled_event"]
    st0, ctl, _ = fx.call(ctx, "GET", f"/api/v1/portfolios/{pid}/controls/events")
    last = ctl["items"][-1]["event_id"]
    prev = en["event_id"] if last == en["event_id"] else ctx.state.get("override_prev", last)
    ctx.state.setdefault("override_prev", prev)
    body = fx.control_body("released", ctx.state["rule_id"], "v1", clock.utc_iso(), ctx.state["override_prev"],
                           related=en["event_id"], reason=inp["reason"])
    st, ev, _ = ctx.client().post(f"/api/v1/portfolios/{pid}/controls/events", body, key=inp["idempotency_key"])
    ctx.result.setdefault("override_statuses", []).append(st)
    st2, ctl2, _ = fx.call(ctx, "GET", f"/api/v1/portfolios/{pid}/controls/events")
    rel = [e for e in ctl2["items"] if e["event_type"] == "released"]
    idem = ctx.scalar("SELECT COUNT(*) FROM idempotency_keys WHERE idempotency_key=?", (inp["idempotency_key"],))
    ctx.result.update({"override_event_count": len(rel),
                       "override_reason_present": all(e["reason"] for e in rel),
                       "activation_event_preserved": any(e["event_id"] == en["event_id"] for e in ctl2["items"]),
                       "rule_and_actor_auditable": all(e["policy_id"] and e["policy_version"] and e["recorded_at"] for e in rel)
                       and idem == 1})
    probes = ctx.state.setdefault("post_restart_probes", [])
    if not any(getattr(f, "__name__", "") == "_probe_cooldown" for f in probes):
        def _probe_cooldown(c):
            c.result["cooldown_state_after_restart"] = cooldown_state_after_restart(c)
        probes.append(_probe_cooldown)


def cooldown_state_after_restart(ctx):
    pid = ctx.state["cooldown_portfolio"]
    return js.cooldown_state(ctx.db.conn(), pid)["state"]


# ── 自检 ──
def checklist_submit(ctx, inp):
    rules = ctx.state.get("rendered_rules") or psychology.load_rules()["rules"]
    answers = {r["rule_id"]: bool(inp.get("all_checked")) for r in rules}
    r = psychology.submit(rules, answers)
    ctx.result.update({"checklist_completion": r["checklist_completion"],
                       "emotional_health_diagnosis": r["emotional_health_diagnosis"],
                       "allowed_to_proceed": r["allowed_to_proceed"]})


# ── 模拟命令形状 / 并发 ──
def _sim_setup(ctx):
    plan = fx.ensure_plan(ctx, "plan_valid", freeze=True)["plan"]
    pid = plan["portfolio_id"]
    clock.set_fixed(FILL_AT)
    sid = ctx.state.get("sim_snapshot") or fx.intraday_snapshot(ctx, QUOTE_DAY, QUOTE_AT, {fx.SYM_A: "10.0000"},
                                                               pre_close={fx.SYM_A: "9.9500"})
    ctx.state["sim_snapshot"] = sid
    return plan, pid, sid


def simulation_command_validate_matrix(ctx, inp):
    plan, pid, sid = _sim_setup(ctx)
    cash0, sh0, rev0 = _cash_and_shares(ctx, pid)
    ev0 = ctx.scalar("SELECT COUNT(*) FROM journal_events WHERE portfolio_id=?", (pid,))
    out, accepted = [], 0
    for a in inp["attempts"]:
        b = fill_body(pid, rev0, side="buy", shares=100, price="10.0000", snapshot_id=sid, plan=plan)
        if a == "missing_portfolio_id":
            b.pop("portfolio_id")
        elif a == "stale_revision":
            b["expected_portfolio_revision"] = rev0 - 1
        elif a == "multi_symbol_order":
            b["symbol"] = "600001.SH,000001.SZ"
        st, obj, _ = ctx.client().post("/api/v1/journal/events", b)
        accepted += 1 if st < 300 else 0
        out.append({"attempt": a, "status": st, "code": obj["error"]["code"] if st >= 400 else None})
    cash1, sh1, rev1 = _cash_and_shares(ctx, pid)
    ev1 = ctx.scalar("SELECT COUNT(*) FROM journal_events WHERE portfolio_id=?", (pid,), purpose="组合事件数")
    ctx.result.update({"accepted_command_count": accepted, "attempts": out,
                       "stale_revision_http_status": next(x["status"] for x in out if x["attempt"] == "stale_revision"),
                       "invalid_shape_http_statuses": [x["status"] for x in out if x["attempt"] != "stale_revision"],
                       "db": {"cash_event_delta": ev1 - ev0}, "portfolio_revision_unchanged": rev1 == rev0})


def simulation_commands_concurrent(ctx, inp):
    plan, pid, sid = _sim_setup(ctx)
    cash0, sh0, rev0 = _cash_and_shares(ctx, pid)
    if rev0 != inp["expected_revision"]:
        ctx.notes.append(f"组合当前 revision={rev0}（fixture 期望 {inp['expected_revision']}）")
    clients = [ctx.client(new=True) for _ in range(int(inp["workers"]))]
    barrier = threading.Barrier(len(clients))
    res = [None] * len(clients)

    def work(i, cl):
        b = fill_body(pid, inp["expected_revision"], side="buy", shares=100, price="10.0000", snapshot_id=sid, plan=plan,
                      symbol=inp["symbol"])
        barrier.wait()
        res[i] = cl.post("/api/v1/journal/events", b)

    ts = [threading.Thread(target=work, args=(i, c)) for i, c in enumerate(clients)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    statuses = [r[0] for r in res]
    cash1, sh1, rev1 = _cash_and_shares(ctx, pid)
    fills = ctx.q("SELECT event_id, effective_at FROM journal_events WHERE portfolio_id=? AND event_type='paper_fill'",
                  (pid,), purpose="并发后的模拟成交")
    ctx.result.update({"statuses": statuses, "successful_commands": sum(1 for s in statuses if s == 201),
                       "conflict_http_status": next((s for s in statuses if s != 201), None),
                       "portfolio_revision": rev1, "negative_cash": cash1 < 0,
                       "duplicate_trade_events": max(0, len(fills) - 1)})
