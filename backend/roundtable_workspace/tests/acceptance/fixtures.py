"""验收 fixture 物化：把 acceptance_cases.json 的合成 fixture 变成被测系统里的真实对象。

原则：
  - 被测行为一律走真实实现（HTTP 或服务函数）；只有「描述既有状态」的前置数据（如 T+1 账本里已有的两笔成交、
    已绑定训练上下文的会话）才用内部服务直接写入，且写入方式与产品代码相同（哈希链、投影、幂等键）；
  - 合成快照 source_mode=fixture、is_synthetic=true，所有数据集都带原始字节哈希；
  - fixture 中的产品 ID（snapshot_fixture_final / case_fixture_main / plan_fixture_main / portfolio_fixture_main /
    replay_fixture_main …）：快照与回放直接使用该 ID；Case/Plan/Portfolio 由 API 生成 ID，ctx.aliases 记录映射。
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from panel.adapters import llm_provider
from panel.data.evidence_store import EvidenceStore
from panel.domain import calendar as cal_mod, clock
from panel.domain.canonical import canonical_bytes, canonical_json, content_hash, derived_id, new_id
from panel.domain.fees import compute_fees
from panel.domain.money import mul_shares
from panel.jobs import worker
from panel.jobs.worker import JobFailed
from panel.services import calendar_service, snapshot_build as sb, snapshot_service, validation_service
from panel.services.snapshot_build import Dataset, SnapshotInputs

FX_SOURCE = "provider_fixture_daily"
SYM_A = "600001.SH"


class DisabledProvider:
    """验收默认 provider：任何取数都失败（不访问 live 数据接口、不读 .env）。"""

    def __init__(self, *_a, **_k):
        pass

    def __getattr__(self, name):
        def _f(*_a, **_k):
            raise JobFailed("PROVIDER_DISABLED_IN_ACCEPTANCE", "验收不访问供应商：请用 provider_fault_set 注入受控 provider")
        return _f


# ── 安装 / 卸载 ──
def calendar_of(ctx) -> cal_mod.TradingCalendar:
    return cal_mod.from_fixture(ctx.fixture("calendar"))


def install_defaults(ctx, after_restart: bool = False) -> None:
    db = ctx.h.db
    if not ctx.state.get("calendar_fault"):
        cal = calendar_of(ctx)
        calendar_service.store(db, cal)
        calendar_service.set_override(cal)
    else:
        calendar_service.set_override(None, unavailable=True)
    if not ctx.state.get("provider_factory"):
        snapshot_service.set_provider_factory(lambda s, st: DisabledProvider())
    else:
        snapshot_service.set_provider_factory(ctx.state["provider_factory"])
    llm_provider.set_provider_factory(ctx.state.get("llm_factory"))
    validation_service.INTEGRITY_HOOK = ctx.state.get("integrity_hook")


def uninstall_overrides() -> None:
    calendar_service.set_override(None)
    snapshot_service.set_provider_factory(None)
    llm_provider.set_provider_factory(None)
    validation_service.INTEGRITY_HOOK = None
    from panel.domain import faults
    faults.disarm_all()


# ── HTTP 小工具 ──
def call(ctx, method: str, path: str, body=None, want=(200, 201, 202), key: str | None = None, client=None):
    c = client or ctx.client()
    if method == "GET":
        st, obj, hd = c.get(path)
    else:
        st, obj, hd = c.post(path, body, key=key)
    if want and st not in (want if isinstance(want, tuple) else (want,)):
        raise AssertionError(f"{method} {path} → {st} {json.dumps(obj, ensure_ascii=False)[:600]}")
    return st, obj, hd


def wait(ctx, accepted: dict, client=None) -> dict:
    c = client or ctx.client()
    return c.wait_job(accepted["job"]["job_id"], timeout=900)


# ── 合成快照 ──
def _ds(store, role, rows, captured, session_date, fmt="explicit_rows_v1", source=FX_SOURCE) -> Dataset:
    raw = canonical_bytes({"role": role, "session_date": session_date, "rows": rows})
    return Dataset(role=role, rows=rows, raw_sha256=store.put(raw), captured_at=captured, source_id=source,
                   authority_type="synthetic", session_date=session_date, first_observed_at=captured,
                   raw_format=fmt, note="synthetic acceptance fixture")


def base_rows(fx: dict) -> list[dict]:
    """snapshot_final 系列的当日行（合成）。"""
    d = (fx.get("session_date") or "2026-10-16").replace("-", "")
    close_a = None
    amount_a = None
    for f in fx.get("facts") or []:
        if f.get("fact_id") == "f_close_A":
            close_a = f.get("price")
        if f.get("fact_id") == "f_amount_A":
            amount_a = f.get("money_minor")
    close_a = fx.get("f_close_A") or close_a or "10.0000"
    drop_a = "quote_price" in fx and fx.get("quote_price") is None      # snapshot_missing：合成甲没有报价
    amt_a = str(Decimal(amount_a or "123456789") / 100) if fx.get("amount", "x") is not None else None
    rows = [
        {"tradedate": d, "symbol": "600001", "name": "合成甲", "open": "9.9500",
         "high": str(max(Decimal("10.0800"), Decimal(close_a))), "low": str(min(Decimal("9.9000"), Decimal(close_a))),
         "close": close_a, "pre_close": "9.9500", "pct_chg": None, "vol": "1234.57", "amount": amt_a},
        {"tradedate": d, "symbol": "000001", "name": "合成乙", "open": "12.0000", "high": "12.2000", "low": "11.9000",
         "close": "12.1000", "pre_close": "12.0000", "pct_chg": None, "vol": "2000", "amount": "2420000"},
        {"tradedate": d, "symbol": "600002", "name": "合成丙", "open": "8.0000", "high": "8.0000", "low": "7.8000",
         "close": "7.9000", "pre_close": "8.0000", "pct_chg": None, "vol": "500", "amount": "395000"},
    ]
    if fx.get("pct_chg_zero") is not None or fx.get("pct_chg_negative") is not None:
        rows += [
            {"tradedate": d, "symbol": "600005", "name": "合成平", "open": "5.0000", "high": "5.0500", "low": "4.9500",
             "close": "5.0000", "pre_close": "5.0000", "vol": "100", "amount": "50000"},
            {"tradedate": d, "symbol": "600006", "name": "合成跌", "open": "10.0000", "high": "10.0000", "low": "9.8500",
             "close": "9.8750", "pre_close": "10.0000", "vol": "100", "amount": "98750"},
            {"tradedate": d, "symbol": "600007", "name": "合成缺额", "open": "6.0000", "high": "6.1000", "low": "5.9000",
             "close": "6.0500", "pre_close": "6.0000", "vol": "100", "amount": None},
        ]
    if drop_a:
        rows = [r for r in rows if r["symbol"] != "600001"]
    elif fx.get("amount", "x") is None:
        pass
    return rows


def intraday_snapshot(ctx, day: str, captured_at: str, closes: dict[str, str], *, suspended=(), pre_close=None,
                      snapshot_id: str | None = None, finality="not_final") -> str:
    """盘中快照（paper_fill_snapshot_quote_v1 的报价来源）：bar_status=partial，停牌名单可控。"""
    d = day.replace("-", "")
    rows = []
    for sym, px in closes.items():
        code = sym.split(".")[0]
        pc = (pre_close or {}).get(sym) or px
        rows.append({"tradedate": d, "symbol": code, "name": f"合成{code}", "open": pc, "high": max(px, pc, key=Decimal),
                     "low": min(px, pc, key=Decimal), "close": px, "pre_close": pc, "vol": "1000",
                     "amount": str(Decimal(px) * 100000)})
    susp = [{"tradedate": d, "symbol": s, "suspend_type": "S"} for s in suspended]
    f = {"fixture_id": "intraday_quote", "session_date": day, "quote_date": day, "captured_at": captured_at,
         "provider_finality": finality, "facts": []}
    r = ingest_fixture_snapshot(ctx, f, snapshot_id=snapshot_id, rows=rows, suspend_rows=susp,
                                lineage_key=f"fixture:intraday:{day}:{captured_at}")
    return r["snapshot_id"]


def _market_of(code: str) -> str:
    if code.startswith(("300", "301", "302")):
        return "创业板"
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("4", "8", "92")):
        return "北交所"
    return "主板"


def stock_basic(rows, extra=()) -> list[dict]:
    out = []
    for r in list(rows) + list(extra):
        code = str(r["symbol"]).zfill(6)
        out.append({"code": code, "symbol": code, "name": r.get("name") or code, "market": _market_of(code),
                    "list_date": "20100101", "list_status": "L", "industry": "合成行业"})
    return out


def limit_rows(rows, d) -> list[dict]:
    out = []
    for r in rows:
        pc = Decimal(str(r["pre_close"]))
        up = (pc * Decimal("1.1")).quantize(Decimal("0.01"))
        dn = (pc * Decimal("0.9")).quantize(Decimal("0.01"))
        out.append({"tradedate": d, "symbol": f"{str(r['symbol']).zfill(6)}.{'SH' if str(r['symbol']).startswith('6') else 'SZ'}",
                    "up_limit": str(up), "down_limit": str(dn)})
    return out


def fact_evidence(fx: dict, snapshot_hint: str) -> tuple[list[dict], dict]:
    """fixture facts → Evidence（ID 由 fact_id 派生；fact_id 原样保留在 value.fact_id）。"""
    out, idx = [], {}
    for f in fx.get("facts") or []:
        fid = f["fact_id"]
        eid = derived_id("ev", "fixture_fact", snapshot_hint, fid, content_hash(f))
        if "price" in f:
            unit, val = "price_decimal", {"fact_id": fid, "price": f["price"]}
            path = f"daily_bars[{f.get('symbol')}].close_decimal"
        elif "money_minor" in f:
            unit, val = "money_minor", {"fact_id": fid, "money_minor": f["money_minor"]}
            path = f"daily_bars[{f.get('symbol')}].amount_minor"
        else:
            unit, val = "json", {"fact_id": fid, "value": f.get("value")}
            path = f"facts.{fid.lower()}"
        prec = {"timestamp": "exact", "date": "date_only"}.get(f.get("time_precision"), f.get("time_precision") or "unknown")
        e = {"evidence_id": eid, "kind": "api_fact", "snapshot_id": None, "symbol": f.get("symbol"),
             "observed_at": f.get("available_at"), "available_at": f.get("available_at"),
             "first_observed_at": f.get("first_observed_at"), "time_precision": prec,
             "availability_basis": "recorded_observation" if f.get("available_at") else "unknown",
             "captured_at": fx.get("captured_at") or clock.utc_iso(),
             "source_ref": {"source_id": f.get("source_id") or FX_SOURCE, "authority_type": "synthetic",
                            "locator": f"fixture:{fx.get('fixture_id')}#{fid}", "content_sha256": content_hash(val),
                            "retrieved_at": fx.get("captured_at") or clock.utc_iso(), "published_at": None,
                            "citation": f"synthetic fixture fact {fid}"},
             "field_path": path, "value": val, "unit": unit, "quality": "verified", "related_evidence_ids": []}
        out.append(e)
        idx[fid] = {"evidence_id": eid, **{k: v for k, v in f.items() if k != "fact_id"}}
    return out, idx


def ingest_fixture_snapshot(ctx, fx: dict, *, snapshot_id: str | None, replay_id: str | None = None,
                            history: list[tuple[str, list[dict]]] | None = None, rows: list[dict] | None = None,
                            st_rows: list | None = None, suspend_rows: list | None = None, with_all=True,
                            lineage_key: str | None = None, extra_evidence: list[dict] | None = None) -> dict:
    """返回 {"snapshot_id","created","supersedes","build","digest"}。"""
    db = ctx.db
    store = EvidenceStore(ctx.h.settings.evidence_dir)
    D = fx.get("session_date") or "2026-10-16"
    Dc = D.replace("-", "")
    cap = fx.get("captured_at") or "2026-10-16T07:10:00Z"
    rows = rows if rows is not None else base_rows(fx)
    ev, idx = fact_evidence(fx, snapshot_id or D)
    ev += list(extra_evidence or [])
    finality = fx.get("provider_finality", "final_confirmed")
    hist_ds = [_ds(store, "daily_history", r, cap, d) for d, r in (history or [])]
    datasets = {}
    if with_all:
        datasets = dict(
            stockbasic=_ds(store, "stockbasic", stock_basic(rows), cap, D),
            st=_ds(store, "st", st_rows or [], cap, D),
            suspend=_ds(store, "suspend", suspend_rows or [], cap, D),
            pricelimit=_ds(store, "pricelimit", limit_rows(rows, Dc), cap, D),
            valuation=_ds(store, "valuation", [{"symbol": f"{str(r['symbol']).zfill(6)}.{'SH' if str(r['symbol']).startswith('6') else 'SZ'}",
                                                "close": r["close"], "turnover_rate": "1.0", "pe_ttm": "10"} for r in rows], cap, D),
            limit_events=_ds(store, "limit_events", [], cap, D),
            index_daily=_ds(store, "index_daily", [{"symbol": code.split(".")[0], "tradedate": Dc, "close": "3000",
                                                    "pre_close": "2990"} for code, _ in sb.INDICES], cap, D))
    session = _ds(store, "daily_session", rows, cap, D) if not fx.get("provider_revision") else \
        Dataset(role="daily_session", rows=rows, raw_sha256=store.put(canonical_bytes(
            {"role": "daily_session", "session_date": D, "rows": rows, "provider_revision": fx["provider_revision"]})),
            captured_at=cap, source_id=FX_SOURCE, authority_type="synthetic", session_date=D, first_observed_at=cap,
            raw_format="explicit_rows_v1", note=f"synthetic acceptance fixture revision {fx['provider_revision']}")
    inp = SnapshotInputs(session_date=D, source_mode="fixture", symbols=[], calendar=calendar_service.current(db),
                         session_daily=session, history=hist_ds, finality=finality,
                         finality_basis=f"fixture provider_finality={finality!r}", is_synthetic=True,
                         replay_id=replay_id, explicit_evidence=ev,
                         price_basis=fx.get("price_basis") or "raw",
                         adjustment_policy_version=fx.get("adjustment_policy_version") or sb.ADJUSTMENT_POLICY_RAW,
                         **datasets)
    res = sb.build(inp, clock.parse_utc(fx.get("generated_at") or cap) if fx.get("generated_at") else None)
    sid, created, sup = snapshot_service.store_snapshot(
        db, res, replay_id=replay_id, snapshot_id=snapshot_id,
        lineage_key=lineage_key or f"fixture:{fx.get('_extends') or fx.get('fixture_id')}:{D}")
    row = db.one("SELECT canonical_payload FROM snapshots WHERE snapshot_id=?", (sid,))
    stored = json.loads(row["canonical_payload"])
    for e in stored["evidence"]:
        v = e.get("value")
        if isinstance(v, dict) and v.get("fact_id") in idx:
            idx[v["fact_id"]] = {**idx[v["fact_id"]], "evidence_id": e["evidence_id"], "snapshot_id": sid}
    ctx.facts.update(idx)
    return {"snapshot_id": sid, "created": created, "supersedes": sup, "build": res,
            "digest": content_hash(stored)}


# ── 物化入口 ──
def ensure(ctx, fid: str):
    if fid in ctx.loaded:
        return ctx.state.get(f"fx:{fid}")
    fn = LOADERS.get(fid)
    val = fn(ctx) if fn else ctx.fixture(fid)
    ctx.loaded.add(fid)
    ctx.state[f"fx:{fid}"] = val
    return val


def _snapshot_final(ctx):
    ctx.harness()
    fx = ctx.fixture("snapshot_final")
    r = ingest_fixture_snapshot(ctx, fx, snapshot_id=fx["snapshot_id"])
    ctx.aliases[fx["snapshot_id"]] = r["snapshot_id"]
    return r


def _clock_monday(ctx):
    clock.set_fixed(ctx.fixture("clock_monday")["now"])
    return ctx.fixture("clock_monday")


def _portfolio_context(ctx):
    fx = ctx.fixture("portfolio_context")
    st, pf, _ = call(ctx, "POST", "/api/v1/portfolios", {"mode": "paper", "initial_cash_minor": fx["cash"]["money_minor"],
                                                          "name": "fixture 模拟组合"}, want=201)
    pid = pf["portfolio_id"]
    rev = pf["revision"]
    while rev < fx["revision"]:
        body = note_body(pid, rev, f"fixture 预置事件 r{rev + 1}")
        st, ev, _ = call(ctx, "POST", "/api/v1/journal/events", body, want=201)
        rev += 1
    ctx.aliases[fx["portfolio_id"]] = pid
    return {"portfolio_id": pid, "revision": rev}


def note_body(pid, rev, text, effective_at=None, replay_id=None, symbol=None, plan_id=None, plan_version=None,
              mode="paper"):
    return {"portfolio_id": pid, "event_type": "note", "mode": mode, "effective_at": effective_at or clock.utc_iso(),
            "symbol": symbol, "side": None, "shares": None, "price_decimal": None, "net_cash_delta_minor": None,
            "snapshot_id": None, "plan_id": plan_id, "plan_version": plan_version, "corrects_event_id": None,
            "correction_action": None, "corrected_event_type": None, "note": text, "replay_id": replay_id,
            "expected_portfolio_revision": rev, "evidence_ids": [], "reported_fees": None}


def default_portfolio(ctx) -> str:
    if "portfolio_fixture_main" in ctx.aliases:
        return ctx.aliases["portfolio_fixture_main"]
    return ensure(ctx, "portfolio_context")["portfolio_id"]


def rule(rid, kind, desc, origin="user_rule", price=None, symbol=None, deadline=None, evidence=None, ref=None):
    return {"rule_id": rid, "description": desc, "rule_origin": origin,
            "origin_ref": ref or ("user:fixture" if origin == "user_rule" else "ADR-fixture-1"),
            "source_version": "v1", "rule_version": "v1", "kind": kind, "symbol": symbol, "price_decimal": price,
            "threshold_ratio": None, "deadline_session_date": deadline, "evidence_ids": evidence or []}


def ensure_case(ctx) -> str:
    if "case_fixture_main" in ctx.aliases:
        return ctx.aliases["case_fixture_main"]
    _snapshot_final(ctx) if "snapshot_final" not in ctx.loaded else None
    ctx.loaded.add("snapshot_final")
    sid = ctx.alias("snapshot_fixture_final")
    body = case_body(sid, "fixture Case：合成甲的威科夫阶段", [SYM_A])
    st, acc, _ = call(ctx, "POST", "/api/v1/cases", body, want=202)
    job = wait(ctx, acc)
    if job["state"] not in ("succeeded", "partial", "failed"):
        raise AssertionError(f"case job {job}")
    ctx.aliases["case_fixture_main"] = job["resource_id"] or acc["job"]["resource_id"]
    return ctx.aliases["case_fixture_main"]


def case_body(snapshot_id, title, symbols, seats=None, primary="trading-wyckoff-v1", supersedes=None, horizon="swing"):
    seats = seats or [("trading-wyckoff-v1", "primary"), ("trading-tuixue-v1", "discipline")]
    return {"supersedes_case_id": supersedes, "snapshot_id": snapshot_id, "title": title,
            "question": f"{title}（验收 fixture）", "symbols": symbols, "horizon": horizon,
            "seat_ids": [s for s, _ in seats], "primary_strategy_id": primary,
            "role_assignments": [{"seat_id": s, "role": r} for s, r in seats], "llm_mode": "disabled",
            "provider_id": None, "model_id": None, "user_context_id": None, "replay_id": None}


def plan_body(ctx, fx: dict, *, case_id: str, snapshot_id: str, portfolio_id: str | None, basis_rev: int | None,
              effective_from: str) -> dict:
    ev_close = [ctx.facts["f_close_A"]["evidence_id"]] if "f_close_A" in ctx.facts else []
    purpose = fx.get("purpose", "simulation")
    et = fx.get("entry_trigger") or {}
    dfn = fx.get("defense_or_invalidation") or {}
    rb = fx.get("risk_budget")
    pb = fx.get("position_budget")
    shares = None
    if pb and et.get("price"):
        shares = 100   # position_budget 100501 分 = 100 股 × 10.00 + 费用 501 分（fee_fixture_v1）
    body = {"change_reason": fx.get("change_reason") or "initial", "effective_from": effective_from,
            "portfolio_id": portfolio_id if purpose == "simulation" else None,
            "basis_portfolio_revision": basis_rev if purpose == "simulation" else None,
            "case_id": case_id, "snapshot_id": snapshot_id, "primary_strategy_id": fx["primary_strategy_id"],
            "purpose": purpose, "planned_shares": shares,
            "cancel_rules": [rule(f"cancel_{c.get('type', 'x')}", "cancel", "数据源失效或证据作废则取消本计划")
                             for c in (fx.get("cancel_rules") or [])],
            "time_exit_rule": (rule("time_exit", "time_exit", f"持有满 {fx['time_exit_rule']['holding_sessions']} 个交易日退出")
                               if fx.get("time_exit_rule") else None),
            "exit_deadline": None, "title": f"fixture 计划（{purpose}）",
            "hypothesis": "放量突破 10.00 视为 SOS 确认", "symbols": [SYM_A], "horizon": "swing",
            "entry_rules": ([rule("entry_trigger", "entry", "突破 10.00 触发（price_break）", price=et.get("price"),
                                  symbol=SYM_A, evidence=ev_close)] if et else []),
            "exit_rules": [],
            "invalidation_rules": ([rule("defense", "invalidation", f"跌破 {dfn['price']} 失效", price=dfn.get("price"),
                                         symbol=SYM_A)] if dfn.get("price") else []),
            "risk_budget_minor": rb["money_minor"] if rb else None, "max_position_ratio": None,
            "benchmark_id": None, "evaluation_window": {"start_session_date": "2026-10-19", "end_session_date": "2026-10-20"},
            "fee_model_version": fx.get("fee_schedule_id"), "replay_id": None}
    return body


def ensure_plan(ctx, which: str = "plan_valid", freeze: bool = False) -> dict:
    key = f"plan:{which}:{freeze}"
    if key in ctx.state:
        return ctx.state[key]
    fx = ctx.fixture(which)
    case_id = ensure_case(ctx)
    pid = default_portfolio(ctx) if fx.get("purpose", "simulation") == "simulation" else None
    rev = None
    if pid:
        st, pf, _ = call(ctx, "GET", f"/api/v1/portfolios/{pid}")
        rev = pf["revision"]
    eff = (ctx.fixture("plan_change").get("effective_from") if "plan_change" in ctx.fixtures else None) or "2026-10-19T01:00:00Z"
    body = plan_body(ctx, fx, case_id=case_id, snapshot_id=ctx.alias("snapshot_fixture_final"), portfolio_id=pid,
                     basis_rev=rev, effective_from=eff)
    st, plan, _ = call(ctx, "POST", "/api/v1/plans", body, want=201)
    ctx.aliases[fx.get("plan_id") or "plan_fixture_main"] = plan["plan_id"]
    out = {"plan": plan, "body": body}
    if freeze:
        frozen_at = ctx.fixture("plan_frozen").get("frozen_at") or clock.utc_iso()
        keep = clock.now()
        clock.set_fixed(frozen_at)
        try:
            st, fr, _ = call(ctx, "POST", f"/api/v1/plans/{plan['plan_id']}/freeze", {"expected_version": 1}, want=200)
        finally:
            clock.set_fixed(keep)
        out["plan"] = fr
    ctx.state[key] = out
    return out


def _plan_valid(ctx):
    return ensure_plan(ctx, "plan_valid", freeze=False)


def _plan_research(ctx):
    return ensure_plan(ctx, "plan_research", freeze=False)


def _plan_frozen(ctx):
    return ensure_plan(ctx, "plan_valid", freeze=True)


def append_fixture_trade(ctx, pid: str, *, side: str, shares: int, price: str, effective_at: str, session_date: str,
                         symbol: str = SYM_A, fee_model: str = "fee_fixture_v1", note: str = "") -> dict:
    """前置账本状态：与 _paper_fill 相同的费用/净额算法，经哈希链 + 投影写入（只用于 fixture 既有状态）。"""
    from panel.services import journal_service as js
    db = ctx.db
    gross = mul_shares(Decimal(price), shares)
    fees = compute_fees(side, gross, fee_model)
    g = int((gross * 100).to_integral_value())
    net = -(g + int(fees["total_minor"])) if side == "buy" else g - int(fees["total_minor"])
    with db.txn() as c:
        p = dict(c.execute("SELECT * FROM portfolios WHERE portfolio_id=?", (pid,)).fetchone())
        key = f"fixture-{new_id('k', 8)}"
        path = f"/internal/acceptance/fixtures/{pid}"
        js.ensure_internal_key(c, path, key, content_hash({"fixture": note}), {"fixture": note})
        ev = js._event(pid, "paper_fill", "paper", p["replay_id"], effective_at=effective_at, session_date=session_date,
                       symbol=symbol, side=side, shares=shares, price_decimal=price, gross_amount_minor=str(g),
                       net_cash_delta_minor=str(net), fees=fees, note=f"fixture 既有成交：{note}")
        return js._append(c, p, ev, (path, key))


def _ledger_t1(ctx):
    fx = ctx.fixture("ledger_t1")
    st, pf, _ = call(ctx, "POST", "/api/v1/portfolios", {"mode": "paper", "initial_cash_minor": "1000000",
                                                          "name": "fixture T+1 账本"}, want=201)
    pid = pf["portfolio_id"]
    for e in fx["events"]:
        append_fixture_trade(ctx, pid, side=e["side"], shares=e["shares"], price=e["price"],
                             effective_at=e["recorded_at"], session_date=e["session_date"], note=e["event_id"])
    ctx.aliases["portfolio_fixture_t1"] = pid
    return {"portfolio_id": pid}


def register_policy(ctx, policy_id: str, version: str, config: dict) -> None:
    with ctx.db.txn() as c:
        if c.execute("SELECT 1 FROM control_policy_versions WHERE policy_id=? AND policy_version=?",
                     (policy_id, version)).fetchone():
            return
        c.execute("INSERT INTO control_policy_versions(policy_id, policy_version, created_at, policy_sha256, config_json) "
                  "VALUES(?,?,?,?,?)", (policy_id, version, clock.utc_iso(), content_hash(config), canonical_json(config)))


def control_body(event_type, policy_id, version, effective_at, prev, related=None, end=None, reason="fixture"):
    return {"control_type": "cooldown", "event_type": event_type, "policy_id": policy_id, "policy_version": version,
            "effective_at": effective_at, "scheduled_end_at": end, "related_control_event_id": related,
            "reason": reason, "expected_previous_event_id": prev}


def _cooldown(ctx):
    fx = ctx.fixture("cooldown")
    pid = default_portfolio(ctx)
    register_policy(ctx, "user_cooldown_fixture_v1", "v1",
                    {"title": "fixture 用户冷却规则", "rule_origin": fx["rule_origin"],
                     "blocked_action_classes": fx["blocked_action_classes"],
                     "allowed_action_classes": fx["allowed_action_classes"], "effective_from": "2026-10-19T01:00:00Z"})
    st, ev, _ = call(ctx, "POST", f"/api/v1/portfolios/{pid}/controls/events",
                     control_body("enabled", "user_cooldown_fixture_v1", "v1", clock.utc_iso(), None,
                                  end=fx["cooldown_ends_at"], reason="predefined_drawdown_limit"), want=201)
    return {"portfolio_id": pid, "enabled_event": ev}


def _psychology_rules(ctx):
    return ctx.fixture("psychology_rules")


def _calendar(ctx):
    return calendar_of(ctx)


def standalone_fact(ctx, fact: dict, *, source_id="fixture_publication_feed", namespace_replay=None) -> str:
    """独立事实（公告/财务/分红/前瞻）：evidence.field_path = facts.<id>，不属于任何快照。"""
    fid = fact["fact_id"]
    eid = derived_id("ev", "standalone_fact", fid)
    prec = {"timestamp": "exact", "date": "date_only"}.get(fact.get("time_precision"), fact.get("time_precision") or
                                                            ("exact" if fact.get("available_at") else "unknown"))
    val = {k: fact.get(k) for k in ("fact_id", "value", "publication_date", "revises_fact_id", "report_period", "price")
           if fact.get(k) is not None}
    val["fact_id"] = fid
    cap = fact.get("first_observed_at") or fact.get("available_at") or clock.utc_iso()
    e = {"evidence_id": eid, "kind": "api_fact", "snapshot_id": None, "symbol": fact.get("symbol"),
         "observed_at": fact.get("available_at"), "available_at": fact.get("available_at"),
         "first_observed_at": fact.get("first_observed_at"), "time_precision": prec,
         "availability_basis": "authoritative_publication" if fact.get("available_at") else "unknown",
         "captured_at": cap,
         "source_ref": {"source_id": source_id, "authority_type": "synthetic", "locator": f"fixture:{fid}",
                        "content_sha256": content_hash(val), "retrieved_at": cap, "published_at": fact.get("available_at"),
                        "citation": f"synthetic standalone fact {fid}"},
         "field_path": f"facts.{fid.lower()}", "value": val, "unit": "json", "quality": "verified",
         "related_evidence_ids": []}
    with ctx.db.txn() as c:
        c.execute("""INSERT OR IGNORE INTO evidence(evidence_id, snapshot_id, kind, symbol, source_id, authority_type,
                     content_sha256, observed_at, available_at, first_observed_at, captured_at, time_precision,
                     availability_basis, quality, replay_id, canonical_payload) VALUES(?,NULL,'api_fact',?,?,'synthetic',?,?,?,?,?,?,?,
                     'verified',?,?)""",
                  (eid, fact.get("symbol"), source_id, content_hash(val), fact.get("available_at"), fact.get("available_at"),
                   fact.get("first_observed_at"), cap, prec, e["availability_basis"], namespace_replay, canonical_json(e)))
    ctx.facts[fid] = {"evidence_id": eid, **fact}
    return eid


def _publication(ctx):
    ctx.harness()
    fx = ctx.fixture("publication")
    for f in fx["facts"]:
        standalone_fact(ctx, f)
    return fx


def _replay_future(ctx):
    ctx.harness()
    fx = ctx.fixture("replay_future")
    for f in fx["future_facts"]:
        ff = dict(f)
        if "price" in ff:
            ff["value"] = ff["price"]
            ff["symbol"] = SYM_A
        ff.setdefault("time_precision", "timestamp")
        standalone_fact(ctx, ff, source_id="fixture_future_feed")
    return fx


def _server_session_context(ctx):
    """训练上下文 fixture：replay_fixture_main（截点 2026-10-16T07:10Z）+ 绑定会话 + 截点内的可见快照。"""
    ctx.harness()
    fx = ctx.fixture("server_session_context")
    rf = ctx.fixture("replay_future")
    rid = rf["replay_id"]
    cutoff = rf["cutoff_at"]
    db = ctx.db
    if db.one("SELECT 1 FROM replay_sessions WHERE replay_id=?", (rid,)) is None:
        now = clock.utc_iso()
        pid = new_id("portfolio")
        jid = new_id("job")
        with db.txn() as c:
            c.execute("""INSERT INTO jobs(job_id, kind, state, phase, completed_units, total_units, attempt, resource_type,
                         resource_id, replay_id, request_sha256, created_at, started_at, finished_at, error_json,
                         canonical_payload, not_before, parent_job_id, timeout_seconds, worker_id)
                         VALUES(?,'replay','succeeded','succeeded',1,1,1,'replay',?,?,?,?,?,?,NULL,?,NULL,NULL,900,'fixture')""",
                      (jid, rid, rid, "0" * 64, now, now, now, canonical_json({"replay_id": rid, "fixture": True})))
            c.execute("""INSERT INTO portfolios(portfolio_id, mode, revision, cash_minor, market_value_minor, total_equity_minor,
                         quality, last_event_sequence, replay_id, created_at, updated_at, canonical_payload)
                         VALUES(?,'paper',0,0,NULL,NULL,'complete',0,?,?,?,?)""",
                      (pid, rid, now, now, canonical_json({"name": "fixture 训练隔离组合", "watch": []})))
            obj = {"replay_id": rid, "job_id": jid, "state": "active", "symbols": [SYM_A], "start_session_date": "2026-10-16",
                   "end_session_date": "2026-10-20", "cursor_session_date": "2026-10-16", "knowledge_cutoff_at": cutoff,
                   "strict_as_of": True, "revealed": False, "visible_snapshot_id": None,
                   "calendar_version": calendar_of(ctx).calendar_version, "data_archive_fingerprint": "0" * 64,
                   "isolated_portfolio_id": pid, "created_at": now, "revealed_at": None}
            c.execute("""INSERT INTO replay_sessions(replay_id, job_id, state, start_session_date, end_session_date,
                         cursor_session_date, knowledge_cutoff_at, strict_as_of, revealed, visible_snapshot_id, calendar_version,
                         data_archive_fingerprint, isolated_portfolio_id, created_at, revealed_at, canonical_payload)
                         VALUES(?,?,?,?,?,?,?,1,0,NULL,?,?,?,?,NULL,?)""",
                      (rid, jid, "active", "2026-10-16", "2026-10-20", "2026-10-16", cutoff, obj["calendar_version"],
                       "0" * 64, pid, now, canonical_json(obj)))
        snap_fx = ctx.fixture("snapshot_final")
        r = ingest_fixture_snapshot(ctx, snap_fx, snapshot_id=f"snap_{rid}_20261016", replay_id=rid,
                                    lineage_key=f"replay:{rid}:2026-10-16")
        with db.txn() as c:
            c.execute("UPDATE replay_sessions SET visible_snapshot_id=? WHERE replay_id=?", (r["snapshot_id"], rid))
        # 「未来」的真实盘数据也在服务器上：2026-10-20 的 live 快照（收盘 12.0000）+ 未来事实
        fut = dict(snap_fx, session_date="2026-10-20", quote_date="2026-10-20", captured_at="2026-10-20T07:10:00Z",
                   generated_at=None, f_close_A="12.0000", facts=[])
        ingest_fixture_snapshot(ctx, fut, snapshot_id="snapshot_fixture_future_live", lineage_key="fixture:future:2026-10-20")
        _replay_future(ctx)
        ctx.loaded.add("replay_future")
    sessions = {}
    from panel.web.handlers.session import bind_session_to_replay
    for s in fx["sessions"]:
        cl = ctx.client(new=True)
        row = dict(db.one("SELECT * FROM local_sessions WHERE session_id=?", (cl.context["session_id"],)))
        if s["mode"] in ("replay", "training"):
            with db.txn() as c:
                bind_session_to_replay(c, row, s["replay_id"], s.get("cutoff_at") or cutoff)
        if s.get("expired"):
            with db.txn() as c:
                c.execute("UPDATE local_sessions SET expires_at=? WHERE session_id=?",
                          ("2026-01-01T00:00:00Z", row["session_id"]))
        cl.refresh_context() if not s.get("expired") else None
        sessions[s["session_id"]] = cl
    ctx.state["sessions"] = sessions
    ctx.aliases[rid] = rid
    return {"replay_id": rid, "cutoff_at": cutoff}


LOADERS = {
    "snapshot_final": _snapshot_final,
    "clock_monday": _clock_monday,
    "calendar": _calendar,
    "portfolio_context": _portfolio_context,
    "plan_valid": _plan_valid,
    "plan_research": _plan_research,
    "plan_frozen": _plan_frozen,
    "ledger_t1": _ledger_t1,
    "cooldown": _cooldown,
    "psychology_rules": _psychology_rules,
    "publication": _publication,
    "replay_future": _replay_future,
    "server_session_context": _server_session_context,
}


# ── 合成历史归档（回放 / 方法验证） ──
def weekdays(start: str, n: int) -> list[str]:
    d = date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def write_archive(ctx, days: list[str], symbols: int = 40, seed: int = 7, overrides: dict | None = None,
                  root: Path | None = None) -> Path:
    """确定性随机游走的合成日线归档（legacy_rows_v1：与 panel/data/daily 的行格式相同）。"""
    root = root or (ctx.tmp / "archive")
    root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    codes = [f"6000{i:02d}" for i in range(1, symbols // 2 + 1)] + [f"0000{i:02d}" for i in range(1, symbols - symbols // 2 + 1)]
    px = {c: Decimal(str(round(rng.uniform(5, 30), 2))) for c in codes}
    for d in days:
        rows = []
        for c in codes:
            pc = px[c]
            r = Decimal(str(round(rng.gauss(0.001, 0.02), 4)))
            r = max(Decimal("-0.095"), min(Decimal("0.095"), r))
            close = (pc * (1 + r)).quantize(Decimal("0.01"))
            o = (pc * (1 + Decimal(str(round(rng.gauss(0, 0.005), 4))))).quantize(Decimal("0.01"))
            hi = max(o, close) + Decimal("0.05")
            lo = max(Decimal("0.01"), min(o, close) - Decimal("0.05"))
            vol = rng.randint(10000, 200000)
            row = {"tradedate": d.replace("-", ""), "symbol": c, "name": f"合成{c}", "open": float(o), "high": float(hi),
                   "low": float(lo), "close": float(close), "pre_close": float(pc),
                   "pct_chg": float(((close / pc) - 1) * 100), "amount": float(close * vol * 100), "vol": vol}
            if overrides and (d, c) in overrides:
                row.update(overrides[(d, c)])
            rows.append(row)
            px[c] = close
        (root / f"{d.replace('-', '')}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return root
