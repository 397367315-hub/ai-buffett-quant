"""事前审查与事后复盘（PLAN-003 / JOURNAL-002）。

- preflight：审查草稿或冻结版本的完整性与「此刻」的可执行性（数据、交易制度、资金、冷却、时段），
  所有收益字段为 null，不把尚未发生的交易判成违纪；每项 met / failed / unknown / inapplicable；
- post_trade：只能针对冻结版本（哈希一致）；方向收益（计划快照收盘 → 窗口终点收盘）、
  执行收益（只来自有证据的模拟成交，没有成交为 null）、含息总回报（公司行动未核验为 null）分开；
- 纪律只依据「行为发生时已经生效」的事前规则：生效前的行为为 unknown（no_rule_effective_at_event_time）；
  T+1 / 停牌 / 跌停导致不能卖出记为交易约束（inapplicable），不是违规；
  模拟账本里没有卖出事件 = 确实没卖；用户自报账本里缺记录 ≠ 没卖（unknown）；
- 盈亏不影响纪律判定。
"""
from __future__ import annotations

import json
from decimal import Decimal

from ..domain import clock, trading_rules as tr
from ..domain.canonical import canonical_json, new_id
from ..domain.fees import compute_fees
from ..domain.money import mul_shares, ratio_str
from ..jobs import queue, worker
from ..jobs.worker import JobFailed, JobResult
from ..web.errors import ApiError
from . import calendar_service, journal_service as js, plan_service, snapshot_service


def _cr(cid, desc, result, ev=None, missing=None):
    return {"condition_id": cid[:95], "description": desc, "result": result, "evidence_ids": ev or [],
            "missing_inputs": missing or []}


TRADE_EVENTS = ("paper_fill", "manual_trade_record")


def discipline_review(rules: list[dict], events: list[dict], *, review_event_time: str | None = None) -> dict:
    """纪律审查的判定核心（JOURNAL-002 / AC-036 / AC-037 / AC-098），纯函数。

    rules：事前规则 [{rule_id, effective_from(UTC), rule_origin, ...}]；events：日志事件（JournalEvent 形状）。
    - 没有任何事前规则 → unknown（no_predefined_rule），违规数 0：没写规则就谈不上违反；
    - 被审查的行为发生时没有已生效的规则 → unknown（no_rule_effective_at_event_time），不追溯适用后来的规则；
    - 有生效规则但没有任何成交 → not_executed，违规数 0：还没发生的交易不判违纪，也不编造动作历史；
    - 有成交时逐条比对由 post_trade 完成（这里只给汇总口径）。
    """
    trades = [e for e in events if e.get("event_type") in TRADE_EVENTS]
    t_event = clock.parse_utc(review_event_time) if review_event_time else None
    base = {"violation_count": 0, "action_history_fabricated": False, "retroactive_rule_applied": False,
            "rules_considered": [r["rule_id"] for r in rules], "executed_event_count": len(trades)}
    if not rules:
        return {**base, "rule_compliance": "unknown", "reason": "no_predefined_rule",
                "execution_review_state": "not_executed" if not trades else "unknown"}
    if t_event is not None:
        effective = [r for r in rules if r.get("effective_from") and clock.parse_utc(r["effective_from"]) <= t_event]
        if not effective:
            return {**base, "rule_compliance": "unknown", "reason": "no_rule_effective_at_event_time",
                    "execution_review_state": "not_executed" if not trades else "unknown",
                    "rules_effective_at_event_time": []}
        base["rules_effective_at_event_time"] = [r["rule_id"] for r in effective]
    if not trades:
        return {**base, "rule_compliance": "not_applicable", "reason": "no_execution_events",
                "execution_review_state": "not_executed"}
    return {**base, "rule_compliance": "requires_post_trade_review", "reason": "trades_present",
            "execution_review_state": "executed"}


def create(c, db, body: dict, replay_id, request_sha256: str, max_waiting: int) -> dict:
    plan = plan_service.version(db, body["plan_id"], body["plan_version"], replay_id)
    if plan is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "计划版本不存在或不可见")
    if body["review_kind"] == "post_trade":
        if plan["status"] != "frozen":
            raise ApiError(409, "PLAN_NOT_FROZEN", "事后复盘只能针对已冻结的计划版本")
        if body["frozen_hash"] != plan["frozen_hash"]:
            raise ApiError(409, "FROZEN_HASH_MISMATCH", "frozen_hash 与该版本不符", [{"field": "frozen_hash", "reason": "mismatch"}])
    elif body["frozen_hash"] is not None and body["frozen_hash"] != plan["frozen_hash"]:
        raise ApiError(409, "FROZEN_HASH_MISMATCH", "frozen_hash 与该版本不符", [{"field": "frozen_hash", "reason": "mismatch"}])
    if snapshot_service.get(db, body["as_of_snapshot_id"], replay_id) is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "as_of 快照不存在或不可见", [{"field": "as_of_snapshot_id", "reason": "not visible"}])
    review_id = new_id("review")
    job = queue.enqueue(c, kind="review", payload={"review_id": review_id, **body}, request_sha256=request_sha256,
                        resource_type="review", resource_id=review_id, replay_id=replay_id, max_waiting=max_waiting,
                        total_units=1)
    row = _skeleton(review_id, job["job_id"], body, plan, replay_id)
    _store(c, row, insert=True)
    return job


def _skeleton(review_id, job_id, body, plan, replay_id) -> dict:
    pre = body["review_kind"] == "preflight"
    return {"review_id": review_id, "job_id": job_id, "plan_id": body["plan_id"], "plan_version": body["plan_version"],
            "frozen_hash": plan["frozen_hash"] if (not pre or plan["status"] == "frozen") else None,
            "review_kind": body["review_kind"], "preflight_eligibility": "unknown" if pre else None,
            "as_of_snapshot_id": body["as_of_snapshot_id"], "run_state": "queued",
            "directional_price_return_ratio": None, "total_return_ratio": None, "executed_return_ratio": None,
            "benchmark_return_ratio": None, "max_drawdown_ratio": None, "rule_adherence": [],
            "corporate_action_evidence_ids": [], "trade_event_ids": [], "missing_inputs": [], "conclusions": [],
            "created_at": clock.utc_iso(), "replay_id": replay_id}


def _store(c, r: dict, insert: bool = False) -> None:
    vals = (r["review_id"], r["job_id"], r["plan_id"], r["plan_version"], r["frozen_hash"], r["review_kind"],
            r["preflight_eligibility"], r["as_of_snapshot_id"], r["run_state"], r["directional_price_return_ratio"],
            r["total_return_ratio"], r["executed_return_ratio"], r["benchmark_return_ratio"], r["max_drawdown_ratio"],
            r["replay_id"], r["created_at"], canonical_json(r))
    if insert:
        c.execute("""INSERT INTO reviews(review_id, job_id, plan_id, plan_version, frozen_hash, review_kind, preflight_eligibility,
                     as_of_snapshot_id, run_state, directional_price_return_ratio, total_return_ratio, executed_return_ratio,
                     benchmark_return_ratio, max_drawdown_ratio, replay_id, created_at, canonical_payload)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", vals)
    else:
        c.execute("""UPDATE reviews SET preflight_eligibility=?, run_state=?, directional_price_return_ratio=?,
                     total_return_ratio=?, executed_return_ratio=?, benchmark_return_ratio=?, max_drawdown_ratio=?,
                     canonical_payload=? WHERE review_id=?""",
                  (r["preflight_eligibility"], r["run_state"], r["directional_price_return_ratio"], r["total_return_ratio"],
                   r["executed_return_ratio"], r["benchmark_return_ratio"], r["max_drawdown_ratio"], canonical_json(r),
                   r["review_id"]))


def get(db, review_id: str, replay_id) -> dict | None:
    r = db.one("SELECT canonical_payload, replay_id FROM reviews WHERE review_id=?", (review_id,))
    if r is None or r["replay_id"] != replay_id:
        return None
    return json.loads(r["canonical_payload"])


# ── preflight ──
def preflight(db, plan: dict, snap: dict, replay_id, at=None) -> tuple[list[dict], str, list[str], list[str]]:
    at = at or clock.now()
    res: list[dict] = []
    missing: list[str] = []
    for m in plan_service._completeness(plan):
        res.append(_cr(f"pf_{m['field']}", f"计划要件：{m['reason']}", "failed", missing=[m["field"]]))
        missing.append(m["field"])
    if plan["purpose"] == "research":
        res.append(_cr("pf_purpose", "用途为 research：只冻结研究观点，不能据此新开模拟仓", "failed"))
    elif plan["purpose"] == "user_trade_record":
        res.append(_cr("pf_purpose", "用途为 user_trade_record：手工陈述记录，不经模拟执行", "inapplicable"))
    else:
        res.append(_cr("pf_purpose", "用途为 simulation", "met"))
    md = snap["metadata"]
    res.append(_cr("pf_snapshot_quality", f"快照质量 {md['quality']}、新鲜度 {md['freshness']}（{md['freshness_reason']}）",
                   "met" if md["quality"] == "complete" and md["freshness"] == "fresh" else "unknown",
                   missing=[] if md["quality"] == "complete" else [f"snapshot_quality_{md['quality']}"]))
    for sym in plan["symbols"]:
        inst = next((i for i in snap["instruments"] if i["symbol"] == sym), None)
        if inst is None:
            res.append(_cr(f"pf_quote_{sym[:6]}", f"{sym} 不在 as_of 快照中", "unknown", missing=[f"quote:{sym}"]))
            missing.append(f"quote:{sym}")
            continue
        res.append(_cr(f"pf_board_{sym[:6]}", f"{sym} 板块 {inst['board']}（v1 模拟只支持沪深主板）",
                       "met" if inst["board"] in tr.SIMULATION_SUPPORTED_BOARDS else "failed"))
        if inst["suspended"] is None:
            res.append(_cr(f"pf_suspend_{sym[:6]}", f"{sym} 停复牌状态未知", "unknown", missing=["suspend_status"]))
        else:
            res.append(_cr(f"pf_suspend_{sym[:6]}", f"{sym} {'停牌中（交易约束）' if inst['suspended'] else '未停牌'}",
                           "failed" if inst["suspended"] else "met"))
        last = inst["last_price_decimal"]
        for r in plan["entry_rules"]:
            if r["price_decimal"] and last:
                ok = Decimal(last) >= Decimal(r["price_decimal"])
                res.append(_cr(f"pf_trigger_{r['rule_id']}", f"触发 {r['rule_id']}：现价 {last} {'已' if ok else '未'}达到 {r['price_decimal']}"
                               "（达价不等于成交）", "met" if ok else "failed", r["evidence_ids"]))
            else:
                res.append(_cr(f"pf_trigger_{r['rule_id']}", f"触发 {r['rule_id']}：{r['description']}（无法由价格核验）", "unknown",
                               r["evidence_ids"], missing=["trigger_evidence"]))
        if inst["upper_limit_decimal"] and last and Decimal(last) >= Decimal(inst["upper_limit_decimal"]):
            res.append(_cr(f"pf_limitup_{sym[:6]}", f"{sym} 在涨停价：买入无法证明成交", "failed"))
    if plan["purpose"] == "simulation" and plan["portfolio_id"]:
        c = db.conn()
        try:
            pv = js.portfolio_view(c, plan["portfolio_id"], replay_id)
            entry = [Decimal(r["price_decimal"]) for r in plan["entry_rules"] if r["price_decimal"]]
            if entry and plan["planned_shares"]:
                gross = mul_shares(max(entry), plan["planned_shares"])
                need = int((gross * 100).to_integral_value()) + int(compute_fees("buy", gross, plan["fee_model_version"])["total_minor"])
                res.append(_cr("pf_cash", f"所需资金 {need} 分 / 当前现金 {pv['cash_minor']} 分（不预留资金）",
                               "met" if need <= int(pv["cash_minor"]) else "failed"))
            ok, why = js.risk_action_allowed(c, plan["portfolio_id"], "new_long")
            res.append(_cr("pf_cooldown", "冷却状态：" + ("无生效冷却" if ok else "冷却中，新开仓暂停（减仓与记录不受影响）"),
                           "met" if ok else "failed"))
        except ApiError:
            res.append(_cr("pf_portfolio", "绑定的组合不可见", "unknown", missing=["portfolio"]))
    cal = calendar_service.current(db) if replay_id is None else None
    if cal is None:
        res.append(_cr("pf_session", "交易日历不可用：无法判断此刻能否模拟成交", "unknown", missing=["calendar"]))
        missing.append("calendar")
    else:
        ph = cal.phase(at)
        res.append(_cr("pf_session", f"此刻交易时段：{ph}（只影响现在能否成交，不是计划缺陷）",
                       "met" if ph == "continuous" else "unknown"))
    hard_fail = any(r["result"] == "failed" for r in res)
    elig = "ineligible" if hard_fail else ("unknown" if any(r["result"] == "unknown" for r in res) else "eligible")
    if plan["purpose"] == "research":
        elig = "unknown" if not [r for r in res if r["result"] == "failed" and r["condition_id"] != "pf_purpose"] else "ineligible"
    concl = ["事前审查只检查计划完整性与当前可执行性，不判断尚未发生的交易是否违纪",
             "所有收益字段为 null：还没有发生的执行没有收益"]
    return res, elig, sorted(set(missing)), concl


# ── post_trade ──
def _bars(snap: dict, sym: str) -> dict[str, dict]:
    return {b["session_date"]: b for b in snap["daily_bars"] if b["symbol"] == sym and b["close_decimal"]}


def _close_at_plan(db, plan, sym, replay_id) -> Decimal | None:
    s = snapshot_service.get(db, plan["snapshot_id"], replay_id)
    inst = next((i for i in (s or {}).get("instruments", []) if i["symbol"] == sym), None)
    return Decimal(inst["last_price_decimal"]) if inst and inst["last_price_decimal"] else None


def post_trade(db, plan: dict, snap: dict, replay_id) -> dict:
    out = {"rule_adherence": [], "missing_inputs": [], "trade_event_ids": [], "conclusions": [],
           "directional_price_return_ratio": None, "total_return_ratio": None, "executed_return_ratio": None,
           "benchmark_return_ratio": None, "max_drawdown_ratio": None, "corporate_action_evidence_ids": []}
    sym = plan["symbols"][0]
    w = plan["evaluation_window"]
    bars = _bars(snap, sym)
    start_px = _close_at_plan(db, plan, sym, replay_id)
    as_of_day = snap["metadata"]["session_date"]
    if as_of_day < w["end_session_date"]:
        out["missing_inputs"].append("evaluation_window_not_complete")
        out["conclusions"].append(f"评估窗口到 {w['end_session_date']}，as_of 只到 {as_of_day}：方向收益不提前出结论")
    else:
        end_bar = bars.get(w["end_session_date"])
        end_px = Decimal(end_bar["close_decimal"]) if end_bar else None
        if end_px is None:
            inst = next((i for i in snap["instruments"] if i["symbol"] == sym), None)
            if inst and as_of_day == w["end_session_date"] and inst["last_price_decimal"]:
                end_px = Decimal(inst["last_price_decimal"])
        if start_px and end_px:
            out["directional_price_return_ratio"] = ratio_str(end_px - start_px, start_px)
            path = [Decimal(b["close_decimal"]) for d, b in sorted(bars.items()) if w["start_session_date"] <= d <= w["end_session_date"]]
            if path:
                peak, mdd = start_px, Decimal(0)
                for p_ in path:
                    peak = max(peak, p_)
                    mdd = min(mdd, p_ / peak - 1)
                out["max_drawdown_ratio"] = ratio_str(mdd, Decimal(1))
        else:
            out["missing_inputs"].append("price_window_incomplete")
    cas = [a for a in snap["corporate_actions"] if a["symbol"] == sym]
    if cas:
        out["corporate_action_evidence_ids"] = sorted({e for a in cas for e in a["evidence_ids"]})
    out["missing_inputs"].append("corporate_actions_unverified" if not cas else "total_return_policy")
    out["missing_inputs"].append("benchmark_series")
    c = db.conn()
    fills = [json.loads(r["canonical_payload"]) for r in c.execute(
        "SELECT canonical_payload FROM journal_events WHERE plan_id=? AND plan_version=? AND replay_id IS ? "
        "AND event_type IN ('paper_fill','manual_trade_record') ORDER BY effective_at, sequence",
        (plan["plan_id"], plan["version"], replay_id))]
    voided = {json.loads(r["canonical_payload"])["corrects_event_id"] for r in c.execute(
        "SELECT canonical_payload FROM journal_events WHERE event_type='correction' AND correction_action='void' "
        "AND replay_id IS ?", (replay_id,))}
    fills = [f for f in fills if f["event_id"] not in voided]
    out["trade_event_ids"] = [f["event_id"] for f in fills]
    mode = fills[0]["mode"] if fills else ("paper" if plan["purpose"] == "simulation" else "user_reported")
    eff_from = clock.parse_utc(plan["effective_from"])
    res = out["rule_adherence"]
    buys = [f for f in fills if f["side"] == "buy"]
    sells = [f for f in fills if f["side"] == "sell"]
    # 入场
    for r in plan["entry_rules"]:
        if not buys:
            res.append(_cr(f"pt_{r['rule_id']}", f"入场 {r['rule_id']}：没有成交（未执行不是违例）", "inapplicable"))
            continue
        for b in buys:
            if clock.parse_utc(b["effective_at"]) < eff_from:
                res.append(_cr(f"pt_{r['rule_id']}_{b['event_id'][-6:]}", f"{b['event_id']} 早于计划生效：no_rule_effective_at_event_time",
                               "unknown", missing=["rule_effective_at_event_time"]))
                continue
            if r["price_decimal"] and r["threshold_ratio"] is not None:
                cap = Decimal(r["price_decimal"]) * (1 + Decimal(r["threshold_ratio"]))
                ok = Decimal(b["price_decimal"]) <= cap
                res.append(_cr(f"pt_{r['rule_id']}_{b['event_id'][-6:]}",
                               f"入场价 {b['price_decimal']} {'未超过' if ok else '超过'}事前上限 {cap.quantize(Decimal('0.01'))}",
                               "met" if ok else "failed", [b["event_id"]]))
            else:
                res.append(_cr(f"pt_{r['rule_id']}_{b['event_id'][-6:]}", f"按冻结计划成交（{b['price_decimal']}×{b['shares']}）",
                               "met", [b["event_id"]]))
    # 股数
    if plan["planned_shares"]:
        tot = sum(int(b["shares"]) for b in buys)
        res.append(_cr("pt_position_shares", f"累计买入 {tot} 股 / 计划 {plan['planned_shares']} 股",
                       "met" if tot <= plan["planned_shares"] else "failed", [b["event_id"] for b in buys]))
    # 失效/防守
    for r in plan["invalidation_rules"]:
        if not buys or not r["price_decimal"]:
            res.append(_cr(f"pt_{r['rule_id']}", f"失效 {r['rule_id']}：{'无持仓' if not buys else '无价格条件，无法由日线核验'}",
                           "inapplicable" if not buys else "unknown"))
            continue
        first_buy_day = buys[0]["session_date"]
        breach = [d for d, b in sorted(bars.items()) if d >= first_buy_day and b["low_decimal"] and
                  Decimal(b["close_decimal"]) <= Decimal(r["price_decimal"])]
        if not breach:
            res.append(_cr(f"pt_{r['rule_id']}", f"窗口内收盘未跌破失效价 {r['price_decimal']}", "inapplicable"))
            continue
        d0 = breach[0]
        sold_after = [s for s in sells if s["session_date"] >= d0]
        if sold_after:
            res.append(_cr(f"pt_{r['rule_id']}", f"{d0} 收盘跌破失效价，之后已卖出", "met", [s["event_id"] for s in sold_after]))
        elif d0 == first_buy_day:
            res.append(_cr(f"pt_{r['rule_id']}", f"{d0} 买入当日跌破失效价：T+1 当日不可卖，记交易约束（不是违规）",
                           "inapplicable"))
        elif mode == "paper":
            res.append(_cr(f"pt_{r['rule_id']}", f"{d0} 收盘跌破失效价，模拟账本之后没有卖出事件", "failed"))
        else:
            res.append(_cr(f"pt_{r['rule_id']}", f"{d0} 收盘跌破失效价；用户自报账本缺卖出记录 ≠ 没卖", "unknown",
                           missing=["user_reported_sell_record"]))
    # 时间退出
    if plan["exit_deadline"]:
        due = clock.parse_utc(plan["exit_deadline"])
        if clock.now() < due:
            res.append(_cr("pt_time_exit", f"时间退出截止 {plan['exit_deadline']} 未到", "inapplicable"))
        elif not buys:
            res.append(_cr("pt_time_exit", "没有持仓，时间退出不适用", "inapplicable"))
        else:
            closed = sum(int(s["shares"]) for s in sells) >= sum(int(b["shares"]) for b in buys)
            res.append(_cr("pt_time_exit", f"截止 {plan['exit_deadline']}：{'已离场' if closed else '仍持有'}",
                           "met" if closed else ("failed" if mode == "paper" else "unknown")))
    # 执行收益（只来自有证据的成交）
    if buys:
        cost = sum(-int(b["net_cash_delta_minor"]) for b in buys)
        proceeds = sum(int(s["net_cash_delta_minor"]) for s in sells)
        held = sum(int(b["shares"]) for b in buys) - sum(int(s["shares"]) for s in sells)
        mv = 0
        if held > 0:
            inst = next((i for i in snap["instruments"] if i["symbol"] == sym), None)
            if inst and inst["last_price_decimal"]:
                mv = int((mul_shares(Decimal(inst["last_price_decimal"]), held) * 100).to_integral_value())
            else:
                mv = None
                out["missing_inputs"].append(f"quote:{sym}")
        if mv is not None and cost:
            out["executed_return_ratio"] = ratio_str(Decimal(proceeds + mv - cost), Decimal(cost))
    else:
        out["conclusions"].append("没有成交事件：执行收益为 null（不用冻结收盘价虚拟入场）")
    n_ok = sum(1 for r in res if r["result"] == "met")
    n_bad = sum(1 for r in res if r["result"] == "failed")
    n_unk = sum(1 for r in res if r["result"] == "unknown")
    out["conclusions"].insert(0, f"过程：遵守 {n_ok} 项、违例 {n_bad} 项、未知 {n_unk} 项（只按事前冻结且已生效的规则判断）")
    out["conclusions"].append("结果另列：方向收益、执行收益、含息总回报分开；盈亏不改变纪律判定")
    out["missing_inputs"] = sorted(set(out["missing_inputs"]))
    return out


@worker.register("review")
def run_job(ctx: worker.JobContext):
    p = ctx.payload
    db = ctx.db
    row = get(db, p["review_id"], ctx.replay_id)
    plan = plan_service.version(db, p["plan_id"], p["plan_version"], ctx.replay_id)
    snap = snapshot_service.get(db, p["as_of_snapshot_id"], ctx.replay_id)
    if plan is None or snap is None or row is None:
        raise JobFailed("REVIEW_INPUT_MISSING", "计划或快照不可用")
    if p["review_kind"] == "preflight":
        res, elig, missing, concl = preflight(db, plan, snap, ctx.replay_id)
        row.update({"rule_adherence": res, "preflight_eligibility": elig, "missing_inputs": missing, "conclusions": concl,
                    "run_state": "succeeded"})
    else:
        row.update(post_trade(db, plan, snap, ctx.replay_id))
        row["run_state"] = "succeeded"
    with db.txn() as c:
        _store(c, row)
    return JobResult("succeeded", resource_id=p["review_id"], resource_type="review")


@worker.on_failure("review")
def _failed(db, row, code):
    p = json.loads(row["canonical_payload"])
    r = get(db, p.get("review_id"), row["replay_id"])
    if r and r["run_state"] in ("queued", "running"):
        r["run_state"] = "failed"
        r["conclusions"] = [f"复盘任务失败（{code}），没有结论"]
        with db.txn() as c:
            _store(c, r)
