"""模拟账本：服务端唯一真源（JOURNAL-004 / JOURNAL-006 / DATA-003）。

- 追加式事件（journal_events）+ 可重建投影（portfolios / positions_projection），同一事务写入；
  前端只提交意图与 expected_portfolio_revision，费用、现金净额、T+1 可卖量全部由服务端计算；
- 事件哈希链：event_hash = sha256(canonical-json-v1(事件去掉 event_hash))，prev_event_hash 指向上一条；
- 更正：追加 correction 事件（void 撤销经济效果 / replace 以本事件经济字段替代），原事件永不删除改写；
- 模拟成交只用快照报价（执行模型 paper_fill_snapshot_quote_v1）：连续竞价时段内、同一会话、报价不超过 30 分钟；
  不假装真实撮合：停牌、涨停价买入、跌停价卖出、价格限制未知 → 不可执行（422，不写事件）；
- 买入必须引用已冻结、可执行（eligible）且绑定本组合的 simulation 计划；冷却中禁止新增风险，卖出与记录始终可做；
- 缺报价：持仓市值、浮盈、总权益为 null，不用成本冒充、不补 0。
"""
from __future__ import annotations

import json
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from ..domain import clock, faults, fill_model, trading_rules as tr
from ..domain.canonical import canonical_json, content_hash, new_id
from ..domain.fees import DEFAULT_FEE_MODEL, FEE_SCHEDULES, compute_fees
from ..domain.money import check_range, mul_shares, parse_minor, ratio_str
from ..web.errors import ApiError
from . import calendar_service, plan_service, snapshot_service

EXECUTION_MODEL = "paper_fill_snapshot_quote_v1"
QUOTE_MAX_AGE = timedelta(minutes=30)
CLOCK_TOLERANCE = timedelta(minutes=5)
ECONOMIC = ("opening_balance", "cash_adjustment", "paper_fill", "manual_trade_record")


def _fen(d: Decimal) -> int:
    return check_range(int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


# ── 投影 ──
def replay_events(events: list[dict]) -> dict:
    """按序重放（含更正）：返回 {cash, positions{sym:{shares, cost, lots{session:shares}}}, watch, last_hash, seq}。"""
    effective: dict[str, dict | None] = {}
    order: list[str] = []
    for e in events:
        if e["event_type"] == "correction":
            tgt = e["corrects_event_id"]
            if e["correction_action"] == "void":
                effective[tgt] = None
            else:
                base = effective.get(tgt) or {}
                rep = dict(base)
                for k in ("symbol", "side", "shares", "price_decimal", "gross_amount_minor", "net_cash_delta_minor",
                          "fees", "session_date", "effective_at", "note"):
                    rep[k] = e.get(k)
                rep["event_type"] = e["corrected_event_type"]
                effective[tgt] = rep
        else:
            effective[e["event_id"]] = e
            order.append(e["event_id"])
    cash = 0
    pos: dict[str, dict] = {}
    watch: set[str] = set()
    for eid in order:
        e = effective.get(eid)
        if e is None:
            continue
        t = e["event_type"]
        if t in ("opening_balance", "cash_adjustment") and e.get("net_cash_delta_minor") is not None:
            cash += int(e["net_cash_delta_minor"])
        elif t in ("paper_fill", "manual_trade_record"):
            sym, sh = e["symbol"], int(e["shares"])
            p = pos.setdefault(sym, {"shares": 0, "cost": 0, "lots": {}})
            if e.get("net_cash_delta_minor") is not None:
                cash += int(e["net_cash_delta_minor"])
            if e["side"] == "buy":
                p["shares"] += sh
                p["cost"] += -int(e["net_cash_delta_minor"]) if e.get("net_cash_delta_minor") is not None \
                    else int(e.get("gross_amount_minor") or 0)
                p["lots"][e["session_date"]] = p["lots"].get(e["session_date"], 0) + sh
            else:
                before = p["shares"]
                if before:
                    p["cost"] -= int((Decimal(p["cost"]) * sh / before).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                p["shares"] = before - sh
                if p["shares"] <= 0:
                    p.update({"shares": 0, "cost": 0, "lots": {}})
        elif t == "watch_added" and e.get("symbol"):
            watch.add(e["symbol"])
        elif t == "watch_removed" and e.get("symbol"):
            watch.discard(e["symbol"])
    return {"cash": cash, "positions": {k: v for k, v in pos.items() if v["shares"] > 0}, "watch": sorted(watch)}


def events_of(c, portfolio_id: str) -> list[dict]:
    return [json.loads(r["canonical_payload"]) for r in c.execute(
        "SELECT canonical_payload FROM journal_events WHERE portfolio_id=? ORDER BY sequence", (portfolio_id,))]


def available_shares(state: dict, sym: str, session_date: str) -> int:
    p = state["positions"].get(sym)
    if not p:
        return 0
    return max(0, p["shares"] - p["lots"].get(session_date, 0))


def sell_preflight(c, portfolio_id: str, symbol: str, shares: int, session_date: str) -> dict:
    """卖出前检查（不写事件）：按上海交易会话与分批持仓判断 T+1 可卖量。"""
    state = replay_events(events_of(c, portfolio_id))
    avail = available_shares(state, symbol, session_date)
    held = state["positions"].get(symbol, {}).get("shares", 0)
    if shares > avail:
        status = "rejected_t1" if shares <= held else "rejected_exceeds_holding"
    else:
        status = "allowed"
    return {"status": status, "available_shares": avail, "held_shares": held, "session_date": session_date,
            "locked_today_shares": held - avail}


# ── 日记（diary_v1：结构化 JSON 写在 note 事件里）──
def parse_diary(note: str) -> dict | None:
    t = (note or "").strip()
    if not t.startswith("{"):
        return None
    try:
        d = json.loads(t)
    except ValueError:
        return None
    return d if isinstance(d, dict) and d.get("schema") == "diary_v1" else None


OBSERVATION_KINDS = ("impulse_observed", "emotion_observed", "rule_deviation_observed", "plan_deviation_observed")


def behavior_summary(events: list[dict]) -> dict:
    """行为汇总（AC-040）：只计数与提示复核；单次事件不推断习惯、不做心理诊断。"""
    obs = []
    for e in events:
        if e.get("event_type") != "note":
            continue
        d = parse_diary(e.get("note") or "")
        if d and d.get("kind") in OBSERVATION_KINDS:
            obs.append({"event_id": e["event_id"], "kind": d["kind"], "effective_at": e["effective_at"]})
    by_kind: dict[str, int] = {}
    for o in obs:
        by_kind[o["kind"]] = by_kind.get(o["kind"], 0) + 1
    return {"observed_event_count": len(obs), "by_kind": by_kind, "events": obs,
            "diagnosis": None, "long_term_habit_asserted": False,
            "recommended_action": "review_event" if obs else "none",
            "note": "只记录自报观察并提示复盘这一笔；单次或少数事件不能推断长期习惯，本系统不做心理或情绪健康诊断"}


def _write_projection(c, portfolio_id: str, revision: int, seq: int, state: dict) -> None:
    now = clock.utc_iso()
    today = clock.shanghai_date()
    c.execute("DELETE FROM positions_projection WHERE portfolio_id=?", (portfolio_id,))
    for sym, p in sorted(state["positions"].items()):
        row = {"symbol": sym, "shares": p["shares"], "cost_basis_minor": str(p["cost"]), "lots": p["lots"]}
        c.execute("""INSERT INTO positions_projection(portfolio_id, symbol, shares, available_shares, cost_basis_minor,
                     last_price_decimal, market_value_minor, unrealized_pnl_minor, unrealized_return_ratio,
                     valuation_snapshot_id, projection_revision, canonical_payload)
                     VALUES(?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,?,?)""",
                  (portfolio_id, sym, p["shares"], max(0, p["shares"] - p["lots"].get(today, 0)), max(0, p["cost"]),
                   revision, canonical_json(row)))
    old = c.execute("SELECT canonical_payload FROM portfolios WHERE portfolio_id=?", (portfolio_id,)).fetchone()
    meta = json.loads(old["canonical_payload"]) if old else {}
    meta["watch"] = state["watch"]
    c.execute("""UPDATE portfolios SET revision=?, cash_minor=?, last_event_sequence=?, updated_at=?,
                 canonical_payload=? WHERE portfolio_id=?""",
              (revision, state["cash"], seq, now, canonical_json(meta), portfolio_id))


def _append(c, portfolio: dict, ev: dict, idem: tuple[str, str]) -> dict:
    legacy_sha = ev.pop("_legacy_source_sha256", None)
    last = c.execute("SELECT sequence, event_hash FROM journal_events WHERE portfolio_id=? ORDER BY sequence DESC LIMIT 1",
                     (portfolio["portfolio_id"],)).fetchone()
    ev["sequence"] = (last["sequence"] + 1) if last else 1
    ev["prev_event_hash"] = last["event_hash"] if last else None
    ev["event_hash"] = content_hash({k: v for k, v in ev.items() if k != "event_hash"})
    f = ev.get("fees") or {}
    c.execute("""INSERT INTO journal_events(event_id, portfolio_id, sequence, event_type, mode, effective_at, recorded_at,
                 session_date, symbol, side, shares, price_decimal, gross_amount_minor, net_cash_delta_minor, commission_minor,
                 stamp_minor, transfer_minor, fee_total_minor, fee_model_version, snapshot_id, plan_id, plan_version,
                 corrects_event_id, correction_action, corrected_event_type, legacy_source_sha256, legacy_trade_id,
                 prev_event_hash, event_hash, replay_id, idempotency_method, idempotency_path, idempotency_key, canonical_payload)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'POST',?,?,?)""",
              (ev["event_id"], ev["portfolio_id"], ev["sequence"], ev["event_type"], ev["mode"], ev["effective_at"],
               ev["recorded_at"], ev["session_date"], ev["symbol"], ev["side"], ev["shares"], ev["price_decimal"],
               parse_minor(ev["gross_amount_minor"]) if ev["gross_amount_minor"] is not None else None,
               parse_minor(ev["net_cash_delta_minor"]) if ev["net_cash_delta_minor"] is not None else None,
               int(f["commission_minor"]) if f else None, int(f["stamp_minor"]) if f else None,
               int(f["transfer_minor"]) if f else None, int(f["total_minor"]) if f else None,
               f.get("fee_model_version") if f else None, ev["snapshot_id"], ev["plan_id"], ev["plan_version"],
               ev["corrects_event_id"], ev["correction_action"], ev["corrected_event_type"],
               legacy_sha if ev["legacy_trade_id"] is not None else None, ev["legacy_trade_id"],
               ev["prev_event_hash"], ev["event_hash"], ev["replay_id"], idem[0], idem[1], canonical_json(ev)))
    faults.checkpoint("after_event_before_projection_commit")
    state = replay_events(events_of(c, portfolio["portfolio_id"]))
    if state["cash"] < 0 and portfolio["mode"] == "paper":
        raise ApiError(422, "INSUFFICIENT_CASH", "现金不足（模拟账户不允许透支）")
    rev = int(portfolio["revision"]) + 1
    _write_projection(c, portfolio["portfolio_id"], rev, ev["sequence"], state)
    return ev


def ensure_internal_key(c, path: str, key: str, request_sha256: str, response: dict) -> None:
    """非 HTTP 路径（导入、验收 driver）写事件时同样登记幂等键（journal_events 外键要求）。"""
    if c.execute("SELECT 1 FROM idempotency_keys WHERE method='POST' AND request_path=? AND idempotency_key=?",
                 (path, key)).fetchone():
        return
    c.execute("""INSERT INTO idempotency_keys(method, request_path, idempotency_key, request_sha256, response_status,
                 response_json, created_at) VALUES('POST',?,?,?,?,?,?)""",
              (path, key, request_sha256, 201, canonical_json(response), clock.utc_iso()))


# ── 组合 ──
def create_portfolio(c, body: dict, replay_id, idem: tuple[str, str]) -> dict:
    pid = new_id("portfolio")
    now = clock.utc_iso()
    cash = parse_minor(body["initial_cash_minor"], "initial_cash_minor", allow_negative=False)
    c.execute("""INSERT INTO portfolios(portfolio_id, mode, revision, cash_minor, market_value_minor, total_equity_minor,
                 quality, last_event_sequence, replay_id, created_at, updated_at, canonical_payload)
                 VALUES(?,?,0,0,NULL,NULL,'complete',0,?,?,?,?)""",
              (pid, body["mode"], replay_id, now, now, canonical_json({"name": body["name"], "watch": []})))
    p = dict(c.execute("SELECT * FROM portfolios WHERE portfolio_id=?", (pid,)).fetchone())
    ev = _event(pid, "opening_balance", body["mode"] if body["mode"] == "user_reported" else "paper", replay_id,
                effective_at=now, session_date=clock.shanghai_date(), net_cash_delta_minor=str(cash),
                note=f"期初资金（{body['name']}）")
    _append(c, p, ev, idem)
    return portfolio_view(c, pid, replay_id)


def _event(pid, etype, mode, replay_id, **kw) -> dict:
    ev = {"event_id": new_id("evt"), "portfolio_id": pid, "sequence": 0, "event_type": etype, "mode": mode,
          "effective_at": kw.get("effective_at") or clock.utc_iso(), "recorded_at": clock.utc_iso(),
          "session_date": kw.get("session_date") or clock.shanghai_date(), "symbol": None, "side": None, "shares": None,
          "price_decimal": None, "gross_amount_minor": None, "net_cash_delta_minor": None, "fees": None,
          "snapshot_id": None, "plan_id": None, "plan_version": None, "corrects_event_id": None,
          "correction_action": None, "corrected_event_type": None, "legacy_trade_id": None, "note": "",
          "prev_event_hash": None, "event_hash": "0" * 64, "replay_id": replay_id, "evidence_ids": []}
    ev.update({k: v for k, v in kw.items() if k in ev})
    return ev


def portfolio_row(c, pid: str, replay_id) -> dict:
    r = c.execute("SELECT * FROM portfolios WHERE portfolio_id=?", (pid,)).fetchone()
    if r is None or r["replay_id"] != replay_id:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "组合不存在或在当前上下文中不可见")
    return dict(r)


def portfolio_view(c, pid: str, replay_id, snapshot_id: str | None = None, db=None) -> dict:
    portfolio_row(c, pid, replay_id)
    state = replay_events(events_of(c, pid))
    snap = None
    if db is not None:
        snap = snapshot_service.get(db, snapshot_id, replay_id) if snapshot_id else snapshot_service.latest(db, replay_id)
        if snapshot_id and snap is None:
            raise ApiError(404, "RESOURCE_NOT_VISIBLE", "估值快照不存在或不可见", [{"field": "snapshot_id", "reason": "not visible"}])
    quotes = {i["symbol"]: i for i in (snap or {}).get("instruments", [])}
    session = clock.shanghai_date()
    positions, missing, mv_total, all_known = [], [], 0, True
    for sym, p in sorted(state["positions"].items()):
        q = quotes.get(sym)
        last = q["last_price_decimal"] if q else None
        if last is None:
            missing.append(f"quote:{sym}")
            all_known = False
            mv = pnl = rr = None
        else:
            mv = _fen(mul_shares(Decimal(last), p["shares"]))
            pnl = mv - p["cost"]
            rr = ratio_str(Decimal(pnl), Decimal(p["cost"])) if p["cost"] else None
            mv_total += mv
        positions.append({"symbol": sym, "shares": p["shares"], "available_shares": available_shares(state, sym, session),
                          "cost_basis_minor": str(max(0, p["cost"])), "last_price_decimal": last,
                          "market_value_minor": str(mv) if mv is not None else None,
                          "unrealized_pnl_minor": str(pnl) if pnl is not None else None,
                          "unrealized_return_ratio": rr,
                          "valuation_snapshot_id": snap["snapshot_id"] if (snap and last is not None) else None})
    if positions and snap is None:
        missing.append("valuation_snapshot")
    known_all = all_known and (snap is not None or not positions)
    row = dict(c.execute("SELECT * FROM portfolios WHERE portfolio_id=?", (pid,)).fetchone())
    return {"portfolio_id": pid, "mode": row["mode"], "revision": int(row["revision"]), "cash_minor": str(state["cash"]),
            "positions": positions, "market_value_minor": str(mv_total) if known_all else None,
            "total_equity_minor": str(state["cash"] + mv_total) if known_all else None,
            "quality": "complete" if known_all else ("partial" if any(p["market_value_minor"] for p in positions) else
                                                     ("unavailable" if positions else "complete")),
            "missing_inputs": missing, "last_event_sequence": int(row["last_event_sequence"]), "replay_id": row["replay_id"]}


# ── 冷却 ──
def cooldown_state(c, pid: str, at=None) -> dict:
    rows = [json.loads(r["canonical_payload"]) for r in c.execute(
        "SELECT canonical_payload FROM portfolio_control_events WHERE portfolio_id=? ORDER BY recorded_at, rowid", (pid,))]
    active = None
    for e in rows:
        if e["event_type"] == "enabled":
            active = e
        elif active and e["related_control_event_id"] == active["event_id"]:
            active = None
    now = at or clock.now()
    if active and active["scheduled_end_at"] and clock.parse_utc(active["scheduled_end_at"]) <= now:
        return {"active": False, "state": "expired_by_schedule", "enabled_event": active, "events": rows}
    if active:
        return {"active": True, "state": "active", "enabled_event": active, "events": rows}
    last = rows[-1] if rows else None
    st = "inactive" if last is None else {"released": "overridden", "expired": "expired"}.get(last["event_type"], "inactive")
    return {"active": False, "state": st, "enabled_event": None, "events": rows}


ACTION_CLASS = {"new_long": "increase_risk", "increase_long": "increase_risk", "reduce_long": "reduce_risk",
                "exit_long": "reduce_risk", "cancel_plan": "cancel", "journal_append": "record", "view": "view"}


def risk_action_allowed(c, pid: str, action: str) -> tuple[bool, str]:
    st = cooldown_state(c, pid)
    cls = ACTION_CLASS.get(action, "increase_risk")
    if not st["active"]:
        return True, "no_active_cooldown"
    pol = c.execute("SELECT config_json FROM control_policy_versions WHERE policy_id=? AND policy_version=?",
                    (st["enabled_event"]["policy_id"], st["enabled_event"]["policy_version"])).fetchone()
    blocked = set(json.loads(pol["config_json"]).get("blocked_action_classes", ["increase_risk"])) if pol else {"increase_risk"}
    if cls in blocked:
        return False, f"cooldown_blocks_{cls}"
    return True, f"cooldown_allows_{cls}"


# ── 追加事件（主入口） ──
def append(c, db, body: dict, replay_id, idem: tuple[str, str]) -> dict:
    p = portfolio_row(c, body["portfolio_id"], replay_id)
    if body["expected_portfolio_revision"] != int(p["revision"]):
        raise ApiError(409, "PORTFOLIO_REVISION_CONFLICT", "组合已有更新事件，请刷新后重试",
                       [{"field": "expected_portfolio_revision",
                         "reason": f"expected {body['expected_portfolio_revision']}, current {p['revision']}"}])
    t = body["event_type"]
    if t == "opening_balance":
        raise ApiError(422, "OPENING_BALANCE_ONLY_AT_CREATION", "期初资金只在创建组合时写入")
    if body["mode"] == "imported_legacy":
        raise ApiError(422, "IMPORTED_LEGACY_ONLY_VIA_IMPORT", "旧账本只能经 /portfolio/import-legacy 导入")
    expect_mode = "paper" if p["mode"] == "paper" else "user_reported"
    if body["mode"] != expect_mode:
        raise ApiError(422, "EVENT_MODE_MISMATCH", f"该组合是 {p['mode']}，事件 mode 应为 {expect_mode}",
                       [{"field": "mode", "reason": f"expected {expect_mode}"}])
    if t == "paper_fill":
        ev = _paper_fill(c, db, p, body, replay_id)
    elif t == "manual_trade_record":
        ev = _manual(c, db, p, body, replay_id)
    elif t == "cash_adjustment":
        if p["mode"] != "user_reported":
            raise ApiError(422, "CASH_ADJUSTMENT_NOT_ALLOWED", "模拟账户不允许手工调整现金（不能抹平对账差异）")
        if body["net_cash_delta_minor"] is None:
            raise ApiError(422, "VALIDATION_ERROR", "cash_adjustment 需要 net_cash_delta_minor",
                           [{"field": "net_cash_delta_minor", "reason": "required"}])
        ev = _event(p["portfolio_id"], t, body["mode"], replay_id, effective_at=body["effective_at"],
                    session_date=clock.shanghai_date(clock.parse_utc(body["effective_at"])),
                    net_cash_delta_minor=str(parse_minor(body["net_cash_delta_minor"])), note=body["note"])
    elif t in ("watch_added", "watch_removed"):
        if not body["symbol"]:
            raise ApiError(422, "VALIDATION_ERROR", "观察清单事件需要 symbol", [{"field": "symbol", "reason": "required"}])
        for k in ("side", "shares", "price_decimal", "net_cash_delta_minor", "reported_fees"):
            if body.get(k) is not None:
                raise ApiError(422, "VALIDATION_ERROR", f"{t} 不能带 {k}", [{"field": k, "reason": "must be null"}])
        ev = _event(p["portfolio_id"], t, body["mode"], replay_id, effective_at=body["effective_at"],
                    session_date=clock.shanghai_date(clock.parse_utc(body["effective_at"])), symbol=body["symbol"],
                    snapshot_id=body["snapshot_id"], plan_id=body["plan_id"], plan_version=body["plan_version"],
                    note=body["note"])
    elif t == "note":
        for k in ("side", "shares", "price_decimal", "net_cash_delta_minor", "reported_fees"):
            if body.get(k) is not None:
                raise ApiError(422, "VALIDATION_ERROR", f"note 不能带 {k}", [{"field": k, "reason": "must be null"}])
        if not body["note"].strip():
            raise ApiError(422, "VALIDATION_ERROR", "日记内容不能为空", [{"field": "note", "reason": "empty"}])
        if body["plan_id"] and plan_service.version(db, body["plan_id"], body["plan_version"] or 1, replay_id) is None:
            raise ApiError(404, "RESOURCE_NOT_VISIBLE", "关联的计划不存在或不可见")
        ev = _event(p["portfolio_id"], t, body["mode"], replay_id, effective_at=body["effective_at"],
                    session_date=clock.shanghai_date(clock.parse_utc(body["effective_at"])), symbol=body["symbol"],
                    snapshot_id=body["snapshot_id"], plan_id=body["plan_id"], plan_version=body["plan_version"],
                    note=body["note"])
    elif t == "correction":
        ev = _correction(c, db, p, body, replay_id)
    else:   # pragma: no cover
        raise ApiError(422, "VALIDATION_ERROR", f"未知事件类型 {t}")
    ev["evidence_ids"] = list(body.get("evidence_ids") or [])
    from .evidence_guard import check_evidence_refs
    cutoff = None
    if replay_id is not None:
        r = c.execute("SELECT knowledge_cutoff_at FROM replay_sessions WHERE replay_id=?", (replay_id,)).fetchone()
        cutoff = r["knowledge_cutoff_at"] if r else None
    check_evidence_refs(db, ev["evidence_ids"], replay_id, cutoff)
    return _append(c, p, ev, idem)


def _require_trade_fields(body):
    errs = [{"field": k, "reason": "required"} for k in ("symbol", "side", "shares", "price_decimal") if body.get(k) is None]
    if errs:
        raise ApiError(422, "VALIDATION_ERROR", "成交事件缺少字段", errs)


def _calendar_for(db, replay_id):
    return calendar_service.current(db) if replay_id is None else None


def _paper_fill(c, db, p, body, replay_id) -> dict:
    _require_trade_fields(body)
    if body["net_cash_delta_minor"] is not None or body["reported_fees"] is not None:
        raise ApiError(422, "SERVER_COMPUTES_FEES", "模拟成交的费用与现金净额由服务端计算，客户端必须传 null",
                       [{"field": "net_cash_delta_minor", "reason": "must be null"},
                        {"field": "reported_fees", "reason": "must be null"}])
    if replay_id is not None:
        raise ApiError(422, "REPLAY_FILL_NOT_SUPPORTED", "历史训练里不直接录模拟成交：冻结计划后推进，揭晓后在复盘里看假设撮合")
    if not body["snapshot_id"]:
        raise ApiError(422, "SNAPSHOT_REQUIRED", "模拟成交必须引用一个快照报价", [{"field": "snapshot_id", "reason": "required"}])
    snap = snapshot_service.get(db, body["snapshot_id"], replay_id)
    if snap is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "快照不存在或不可见")
    sym, side, shares = body["symbol"], body["side"], int(body["shares"])
    inst = next((i for i in snap["instruments"] if i["symbol"] == sym), None)
    if inst is None:
        raise ApiError(422, "SYMBOL_NOT_IN_SNAPSHOT", f"{sym} 不在快照中")
    eff = clock.parse_utc(body["effective_at"])
    now = clock.now()
    if abs((eff - now).total_seconds()) > CLOCK_TOLERANCE.total_seconds():
        raise ApiError(422, "EFFECTIVE_AT_NOT_NOW", "模拟成交时间必须是当前时刻（不接受补录或预约成交）",
                       [{"field": "effective_at", "reason": f"server now {clock.utc_iso(now)}"}])
    cal = _calendar_for(db, replay_id)
    if cal is None:
        raise ApiError(422, "CALENDAR_UNAVAILABLE", "交易日历不可用，不能生成模拟成交")
    if not cal.can_simulate_new_trade(eff):
        raise ApiError(422, "NOT_IN_CONTINUOUS_SESSION", f"{clock.shanghai(eff).strftime('%Y-%m-%d %H:%M')} 不在连续竞价时段（{cal.phase(eff)}）",
                       [{"field": "effective_at", "reason": cal.phase(eff)}])
    md = snap["metadata"]
    if md["session_date"] != clock.shanghai_date(eff):
        raise ApiError(422, "QUOTE_SESSION_MISMATCH", f"快照会话 {md['session_date']} 与成交时刻的会话不同；收盘后的观点不能追认当日成交",
                       [{"field": "snapshot_id", "reason": "session mismatch"}])
    cap = clock.parse_utc(md["captured_at"])
    if not (cap <= eff <= cap + QUOTE_MAX_AGE):
        raise ApiError(422, "QUOTE_TOO_OLD", "快照报价已超过 30 分钟，先刷新快照", [{"field": "snapshot_id", "reason": "stale quote"}])
    if inst["board"] not in tr.SIMULATION_SUPPORTED_BOARDS:
        raise ApiError(422, "SIMULATION_NOT_SUPPORTED", f"{inst['board']} 的交易制度未核验，v1 不提供模拟成交")
    if inst["suspended"] is None:
        raise ApiError(422, "SUSPENSION_STATUS_UNKNOWN", "停复牌状态未知，不能模拟成交",
                       fill_model.rejection_details("SUSPENSION_STATUS_UNKNOWN"))
    if inst["suspended"]:
        raise ApiError(422, "INSTRUMENT_SUSPENDED", "停牌：不能成交（持仓保持不变）",
                       fill_model.rejection_details("INSTRUMENT_SUSPENDED"))
    price = Decimal(body["price_decimal"])
    if inst["last_price_decimal"] is None or price != Decimal(inst["last_price_decimal"]):
        raise ApiError(422, "PRICE_NOT_FROM_SNAPSHOT", "模拟成交价必须等于该快照报价",
                       [{"field": "price_decimal", "reason": f"snapshot price {inst['last_price_decimal']}"}])
    if inst["upper_limit_decimal"] is None or inst["lower_limit_decimal"] is None:
        raise ApiError(422, "PRICE_LIMIT_UNKNOWN", "涨跌停价未知，不能判断可否成交",
                       fill_model.rejection_details("PRICE_LIMIT_UNKNOWN"))
    if side == "buy" and price >= Decimal(inst["upper_limit_decimal"]):
        raise ApiError(422, "LIMIT_UP_BUY_UNVERIFIABLE", "涨停价买入无法证明排队成交：不可执行",
                       fill_model.rejection_details("LIMIT_UP_BUY_UNVERIFIABLE"))
    if side == "sell" and price <= Decimal(inst["lower_limit_decimal"]):
        raise ApiError(422, "LIMIT_DOWN_SELL_UNVERIFIABLE", "跌停价卖出无法证明成交：不可执行（风险敞口仍在）",
                       fill_model.rejection_details("LIMIT_DOWN_SELL_UNVERIFIABLE"))
    lot = tr.lot_size(inst["board"])
    state = replay_events(events_of(c, p["portfolio_id"]))
    session = md["session_date"]
    fee_model = DEFAULT_FEE_MODEL
    if side == "buy":
        if shares % lot:
            raise ApiError(422, "LOT_SIZE_VIOLATION", f"买入必须是 {lot} 股的整数倍", [{"field": "shares", "reason": f"multiple of {lot}"}])
        ok, why = risk_action_allowed(c, p["portfolio_id"], "new_long" if sym not in state["positions"] else "increase_long")
        if not ok:
            raise ApiError(409, "COOLDOWN_ACTIVE", "冷却中：暂停新增风险（卖出、取消与记录仍可做）", [{"field": "event_type", "reason": why}])
        if not body["plan_id"] or not body["plan_version"]:
            raise ApiError(422, "PLAN_REQUIRED_FOR_BUY", "模拟买入必须引用已冻结且可执行的 simulation 计划",
                           [{"field": "plan_id", "reason": "required for buy"}])
        plan = plan_service.version(db, body["plan_id"], body["plan_version"], replay_id)
        if plan is None:
            raise ApiError(404, "RESOURCE_NOT_VISIBLE", "计划不存在或不可见")
        problems = []
        if plan["status"] != "frozen":
            problems.append("plan_not_frozen")
        if plan["purpose"] != "simulation":
            problems.append(f"plan_purpose_{plan['purpose']}_not_executable")
        if plan["execution_eligibility"] != "eligible":
            problems.append("plan_not_eligible:" + ",".join(plan["execution_eligibility_reasons"][:5]))
        if plan["portfolio_id"] != p["portfolio_id"]:
            problems.append("plan_bound_to_other_portfolio")
        if sym not in plan["symbols"]:
            problems.append("symbol_not_in_plan")
        if plan["frozen_at"] and clock.parse_utc(plan["frozen_at"]) > eff:
            problems.append("plan_frozen_after_fill_time")
        if clock.parse_utc(plan["effective_from"]) > eff:
            problems.append("plan_not_yet_effective")
        evs = plan_service.list_events(db, plan["plan_id"], replay_id, plan["version"])
        if evs and evs[-1]["event_type"] in ("cancelled", "expired"):
            problems.append(f"plan_{evs[-1]['event_type']}")
        bought = sum(int(e["shares"]) for e in events_of(c, p["portfolio_id"])
                     if e["event_type"] == "paper_fill" and e["side"] == "buy" and e["plan_id"] == plan["plan_id"]
                     and e["plan_version"] == plan["version"])
        if plan["planned_shares"] and bought + shares > plan["planned_shares"]:
            problems.append(f"exceeds_planned_shares({bought}+{shares}>{plan['planned_shares']})")
        if problems:
            raise ApiError(422, "PLAN_NOT_EXECUTABLE", "该计划不能用于模拟买入",
                           [{"field": "plan_id", "reason": x} for x in problems])
        fee_model = plan["fee_model_version"] or DEFAULT_FEE_MODEL
    else:
        avail = available_shares(state, sym, session)
        held = state["positions"].get(sym, {}).get("shares", 0)
        if shares > avail:
            raise ApiError(422, "T1_NOT_SELLABLE", f"可卖 {avail} 股（当日买入的部分下一交易日才能卖），本次 {shares} 股",
                           [{"field": "shares", "reason": f"available {avail}"}])
        if shares % lot and shares != held:
            raise ApiError(422, "LOT_SIZE_VIOLATION", f"卖出须为 {lot} 股整数倍（清仓零股除外）",
                           [{"field": "shares", "reason": f"multiple of {lot} unless closing"}])
        if body["plan_id"]:
            plan = plan_service.version(db, body["plan_id"], body["plan_version"] or 0, replay_id)
            if plan is None:
                raise ApiError(404, "RESOURCE_NOT_VISIBLE", "计划不存在或不可见")
            fee_model = plan["fee_model_version"] or DEFAULT_FEE_MODEL
    gross = mul_shares(price, shares)
    fees = compute_fees(side, gross, fee_model)
    g = _fen(gross)
    net = -(g + int(fees["total_minor"])) if side == "buy" else g - int(fees["total_minor"])
    if side == "buy" and -net > state["cash"]:
        raise ApiError(422, "INSUFFICIENT_CASH", f"现金不足：需要 {-net} 分，可用 {state['cash']} 分")
    return _event(p["portfolio_id"], "paper_fill", "paper", replay_id, effective_at=body["effective_at"],
                  session_date=session, symbol=sym, side=side, shares=shares, price_decimal=body["price_decimal"],
                  gross_amount_minor=str(g), net_cash_delta_minor=str(net), fees=fees, snapshot_id=body["snapshot_id"],
                  plan_id=body["plan_id"], plan_version=body["plan_version"],
                  note=(body["note"] or "") + f"［执行模型 {EXECUTION_MODEL}：按快照报价的模拟假设，不是撮合］")


def _manual(c, db, p, body, replay_id) -> dict:
    _require_trade_fields(body)
    if p["mode"] != "user_reported":
        raise ApiError(422, "EVENT_MODE_MISMATCH", "手工成交记录只能写入 user_reported 组合（不升级为券商核验）")
    shares = int(body["shares"])
    price = Decimal(body["price_decimal"])
    gross = _fen(mul_shares(price, shares))
    state = replay_events(events_of(c, p["portfolio_id"]))
    if body["side"] == "sell" and shares > state["positions"].get(body["symbol"], {}).get("shares", 0):
        raise ApiError(422, "SELL_EXCEEDS_HOLDING", "卖出股数超过记录中的持仓")
    fees = body["reported_fees"]
    if body["net_cash_delta_minor"] is not None:
        net = parse_minor(body["net_cash_delta_minor"])
    elif fees is not None:
        tot = int(fees["total_minor"])
        net = -(gross + tot) if body["side"] == "buy" else gross - tot
    else:
        raise ApiError(422, "CASH_EFFECT_UNKNOWN", "手工记录需提供实际费用（reported_fees）或现金净额（net_cash_delta_minor）；不默算",
                       [{"field": "reported_fees", "reason": "or net_cash_delta_minor required"}])
    return _event(p["portfolio_id"], "manual_trade_record", "user_reported", replay_id, effective_at=body["effective_at"],
                  session_date=clock.shanghai_date(clock.parse_utc(body["effective_at"])), symbol=body["symbol"],
                  side=body["side"], shares=shares, price_decimal=body["price_decimal"], gross_amount_minor=str(gross),
                  net_cash_delta_minor=str(net), fees=fees, snapshot_id=body["snapshot_id"], plan_id=body["plan_id"],
                  plan_version=body["plan_version"],
                  note=(body["note"] or "") + "［用户手工陈述，未经券商核验］")


def _correction(c, db, p, body, replay_id) -> dict:
    tgt_id = body["corrects_event_id"]
    if not tgt_id or not body["correction_action"] or not body["corrected_event_type"]:
        raise ApiError(422, "VALIDATION_ERROR", "更正需要 corrects_event_id、correction_action、corrected_event_type")
    tgt = c.execute("SELECT canonical_payload FROM journal_events WHERE portfolio_id=? AND event_id=?",
                    (p["portfolio_id"], tgt_id)).fetchone()
    if tgt is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "被更正的事件不存在")
    te = json.loads(tgt["canonical_payload"])
    if te["event_type"] in ("correction", "opening_balance"):
        raise ApiError(422, "CORRECTION_TARGET_INVALID", "不能更正期初资金或更正事件本身")
    if te["event_type"] != body["corrected_event_type"]:
        raise ApiError(422, "CORRECTION_TYPE_MISMATCH", "corrected_event_type 与原事件类型不符",
                       [{"field": "corrected_event_type", "reason": f"original is {te['event_type']}"}])
    voided = any(e["event_type"] == "correction" and e["corrects_event_id"] == tgt_id and e["correction_action"] == "void"
                 for e in events_of(c, p["portfolio_id"]))
    if voided:
        raise ApiError(409, "EVENT_ALREADY_VOIDED", "该事件已被撤销")
    kw = {}
    if body["correction_action"] == "replace":
        if te["event_type"] in ("paper_fill",):
            raise ApiError(422, "REPLACE_PAPER_FILL_NOT_ALLOWED", "模拟成交只能撤销（void）后按规则重新提交，不能直接改价")
        _require_trade_fields(body) if te["event_type"] == "manual_trade_record" else None
        price = Decimal(body["price_decimal"]) if body["price_decimal"] else None
        kw = {"symbol": body["symbol"], "side": body["side"], "shares": body["shares"], "price_decimal": body["price_decimal"],
              "gross_amount_minor": str(_fen(mul_shares(price, int(body["shares"])))) if price is not None and body["shares"] else None,
              "net_cash_delta_minor": body["net_cash_delta_minor"], "fees": body["reported_fees"]}
    ev = _event(p["portfolio_id"], "correction", body["mode"], replay_id, effective_at=body["effective_at"],
                session_date=clock.shanghai_date(clock.parse_utc(body["effective_at"])), corrects_event_id=tgt_id,
                correction_action=body["correction_action"], corrected_event_type=body["corrected_event_type"],
                note=body["note"] or "更正", **kw)
    # 替代后整条链必须仍然合法（不能卖超、模拟账户不能透支）
    trial = events_of(c, p["portfolio_id"]) + [ev]
    st = replay_events(trial)
    if p["mode"] == "paper" and st["cash"] < 0:
        raise ApiError(422, "CORRECTION_BREAKS_LEDGER", "更正后现金为负，拒绝")
    return ev


def list_events(c, pid: str, replay_id, cursor: str | None, limit: int) -> dict:
    portfolio_row(c, pid, replay_id)
    sql = "SELECT sequence, canonical_payload FROM journal_events WHERE portfolio_id=?"
    args: list = [pid]
    if cursor:
        if not cursor.isdigit():
            raise ApiError(400, "BAD_CURSOR", "cursor 不合法")
        sql += " AND sequence>?"
        args.append(int(cursor))
    sql += " ORDER BY sequence LIMIT ?"
    args.append(limit + 1)
    rows = c.execute(sql, args).fetchall()
    items = [json.loads(r["canonical_payload"]) for r in rows[:limit]]
    return {"items": items, "next_cursor": str(rows[limit - 1]["sequence"]) if len(rows) > limit else None}


def verify_chain(c, pid: str) -> dict:
    prev = None
    n = 0
    for e in events_of(c, pid):
        h = content_hash({k: v for k, v in e.items() if k != "event_hash"})
        if h != e["event_hash"] or e["prev_event_hash"] != prev:
            return {"ok": False, "broken_at": e["event_id"]}
        prev = e["event_hash"]
        n += 1
    return {"ok": True, "events": n, "last_hash": prev}
