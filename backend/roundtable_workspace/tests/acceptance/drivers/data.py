"""P0 数据类 driver：快照新鲜度/终结/缺失/单位/非法行/幂等与修订、会话解析、精度、健康与就绪、异步任务、股票池、价格口径。"""
from __future__ import annotations

import json
from decimal import Decimal

from panel.domain import calendar as cal_mod, clock, corporate_actions as ca, money, universe as uv
from panel.domain.canonical import content_hash
from panel.services import calendar_service, plan_service, snapshot_build as sb, snapshot_service
from panel.services.snapshot_service import SnapshotRejected
from panel.web.errors import ApiError

from .. import fixtures as fx
from . import infra


def _snap(ctx, sid):
    return snapshot_service.get(ctx.db, sid, None)


# ── 新鲜度 ──
def snapshot_classify(ctx, inp):
    ctx.harness()
    if inp.get("snapshot_id") or not inp.get("quote_date"):
        r = ctx.ensure("snapshot_final")
        snap = _snap(ctx, ctx.alias(inp.get("snapshot_id") or "snapshot_fixture_final"))
        fr, why = snapshot_service.current_freshness(ctx.db, snap)
        st, view, _ = ctx.client().get("/api/v1/snapshots/latest?allow_stale=true")
        md = snap["metadata"]
        ctx.result.setdefault("freshness_values", []).append(fr)
        ctx.result.setdefault("quote_dates", []).append(md["quote_date"])
        ctx.result.update({"source_mode": md["source_mode"], "freshness": fr, "freshness_reason": why,
                           "latest_http_status": st})
        if st == 200:
            ctx.result.setdefault("api_freshness_values", []).append(view["metadata"]["freshness"])
        return
    cal = calendar_service.load(ctx.db, inp.get("calendar_version")) if inp.get("calendar_version") else \
        calendar_service.current(ctx.db)
    fr, why = sb.classify_freshness(cal, inp["quote_date"], clock.now())
    ctx.result.update({"source_mode": inp.get("source_mode"), "freshness": fr, "freshness_reason": why,
                       "captured_at": inp.get("captured_at"), "calendar_version": cal.calendar_version if cal else None})


def session_resolve(ctx, inp):
    ctx.harness()
    cal = calendar_service.current(ctx.db)
    r = cal.resolve(clock.now())
    ctx.result.update(r)


# ── 摄入 ──
def _variant(ctx, inp) -> dict:
    fid = inp.get("fixture_id") or "snapshot_final"
    f = ctx.fixture(fid)
    patch = inp.get("patch") or {}
    if patch:
        f = {**f, **patch}
    return f


def _rows_for_variant(f: dict) -> tuple[list[dict], list]:
    """按 fixture 的覆盖率字段造合成行（前一日 ≥1 亿成交额家数 vs 当日）。"""
    rows = fx.base_rows(f)
    hist = []
    ratio = f.get("coverage_above_1e8_ratio_to_prev")
    if ratio is not None:
        n_prev = 10
        n_today = int((Decimal(ratio) * n_prev).to_integral_value())
        d = f["session_date"].replace("-", "")
        prev_rows, today_rows = [], []
        for i in range(n_prev):
            code = f"6010{i:02d}"
            big = "150000000"
            prev_rows.append({"tradedate": "x", "symbol": code, "name": f"合成大{i}", "open": "10", "high": "10.5",
                              "low": "9.8", "close": "10.2", "pre_close": "10", "vol": "150000", "amount": big})
            today_rows.append({"tradedate": d, "symbol": code, "name": f"合成大{i}", "open": "10.2", "high": "10.6",
                               "low": "10.0", "close": "10.4", "pre_close": "10.2", "vol": "150000" if i < n_today else "5000",
                               "amount": big if i < n_today else "5200000"})
        rows = rows + today_rows
        prev_day = "2026-10-15"
        hist = [(prev_day, [dict(r, tradedate=prev_day.replace("-", "")) for r in prev_rows])]
    return rows, hist


def snapshot_ingest(ctx, inp):
    ctx.harness()
    out = ctx.out(inp)
    if inp.get("fixture_id") == "invalid_rows":
        f = ctx.fixture("invalid_rows")
        before = ctx.scalar("SELECT COUNT(*) FROM snapshots", purpose="拒收前快照数")
        try:
            snapshot_service.ingest_explicit(ctx.db, __import__("panel.data.evidence_store", fromlist=["x"]).EvidenceStore(
                ctx.h.settings.evidence_dir), session_date="2026-10-16", rows=f["rows"], source_mode="fixture",
                calendar=calendar_service.current(ctx.db), finality="final_confirmed", finality_basis="fixture",
                captured_at="2026-10-16T07:10:00Z", source_id=fx.FX_SOURCE, is_synthetic=True)
            out.update({"http_status": 201})
        except SnapshotRejected as ex:
            out.update({"http_status": ex.status, "error_code": ex.code,
                        "error_fields": sorted({d["field"] for d in ex.details}),
                        "rejected_row_count": len({d["reason"].split(":")[0] for d in ex.details})})
        after = ctx.scalar("SELECT COUNT(*) FROM snapshots", purpose="拒收后快照数")
        out["committed_snapshots"] = after - before
        return
    f = _variant(ctx, inp)
    rows, hist = _rows_for_variant(f)
    if "f_close_A" in (inp.get("patch") or {}):
        f = dict(f, facts=[dict(x, price=inp["patch"]["f_close_A"]) if x["fact_id"] == "f_close_A" else x
                           for x in f.get("facts") or []])
    target = f.get("snapshot_id") if f.get("fixture_id") == "snapshot_final" else None
    r = fx.ingest_fixture_snapshot(ctx, f, snapshot_id=target, history=hist, rows=rows,
                                   lineage_key=f"fixture:snapshot_final:{f.get('session_date')}")
    if f.get("fixture_id") == "snapshot_final" and target and r["snapshot_id"] == target:
        ctx.aliases["snapshot_fixture_final"] = target
        ctx.loaded.add("snapshot_final")
    snap = _snap(ctx, r["snapshot_id"])
    md = snap["metadata"]
    sym = fx.SYM_A
    bar = next((b for b in snap["daily_bars"] if b["symbol"] == sym and b["session_date"] == md["session_date"]), None)
    blockers = plan_service.snapshot_execution_blockers(snap, sym)
    out.update({"snapshot_id": r["snapshot_id"], "bar_status": bar["bar_status"] if bar else None,
                "quote_date": md["quote_date"], "quality": md["quality"], "missing_fields": md["missing_fields"],
                "low_amount_anomaly": bool(r["build"].anomalies.get("low_amount_anomaly")),
                "anomalies": r["build"].anomalies, "warnings": [w.split("：")[0] for w in r["build"].warnings],
                "warnings_text": r["build"].warnings,
                "execution_eligibility": not blockers, "execution_blockers": blockers,
                "created": r["created"], "supersedes": r["supersedes"]})
    # 每次摄入后重算此前保存过的快照摘要：digest_before 是首次入库时的摘要，digest_after 是最新读回的摘要
    out["digest_before"] = out.get("digest_before") or r["digest"]
    for k, v in list(ctx.result.items()):
        if isinstance(v, dict) and v.get("snapshot_id") and "digest_before" in v:
            row = ctx.db.one("SELECT canonical_payload FROM snapshots WHERE snapshot_id=?", (v["snapshot_id"],))
            v["digest_after"] = content_hash(json.loads(row["canonical_payload"])) if row else None
    if out is ctx.result:
        row = ctx.db.one("SELECT canonical_payload FROM snapshots WHERE snapshot_id=?", (r["snapshot_id"],))
        out["digest_after"] = content_hash(json.loads(row["canonical_payload"]))
    lin = ctx.q("SELECT snapshot_id, supersedes_snapshot_id, reason FROM snapshot_lineage WHERE supersedes_snapshot_id IS NOT NULL",
                purpose="快照修订血缘")
    ctx.result["snapshot_revision_lineage_present"] = any(x["snapshot_id"] == r["snapshot_id"] for x in lin) or \
        bool(ctx.result.get("snapshot_revision_lineage_present"))
    ctx.state["last_ingested"] = r["snapshot_id"]


def normalize_provider_rows(ctx, inp):
    f = ctx.fixture(inp.get("fixture_id") or "unit_conversion")
    raw = dict(f["provider_raw"])
    n = sb.normalize_units_demo(raw)
    ctx.result.update({"volume_shares": n["volume_shares"], "amount": {"money_minor": n["amount_minor"]},
                       "change_ratio": n["change_ratio"], "raw_units_preserved": n["raw"] == f["provider_raw"],
                       "provider_units": f["provider_units"]})


def snapshot_request(ctx, inp):
    """非交易日请求：拒绝并给出原因，不补 0 值行情。"""
    ctx.ensure("snapshot_final")
    before = ctx.scalar("SELECT COUNT(*) FROM snapshots", purpose="请求前快照数")
    st, obj, _ = ctx.client().post("/api/v1/snapshots", {"session_date": inp["session_date"], "source_mode": "provider_api",
                                                         "symbols": [], "allow_stale": True})
    after = ctx.scalar("SELECT COUNT(*) FROM snapshots", purpose="请求后快照数")
    reason = None
    if st >= 400:
        reason = next((d["reason"] for d in obj["error"]["details"] if d["field"] == "session_date"), obj["error"]["code"].lower())
    ctx.result.update({"http_status": st, "quote": None if st >= 400 else obj, "missing_reason": reason,
                       "synthetic_zero_rows": after - before})


def direction_return_calculate(ctx, inp):
    e, x = inp.get("entry_price"), inp.get("exit_price")
    r = money.ratio_str(Decimal(x) - Decimal(e), Decimal(e)) if (e is not None and x is not None) else None
    ctx.result["return_ratio"] = r


def money_round_trip(ctx, inp):
    """API 字符串 ↔ DB INTEGER ↔ 浏览器 JSON.parse：大整数分不丢精度（前端只用 BigInt）。"""
    v = inp["money_minor"]
    st, pf, _ = fx.call(ctx, "POST", "/api/v1/portfolios", {"mode": "user_reported", "initial_cash_minor": v,
                                                             "name": "精度往返"}, want=201)
    st2, got, _ = fx.call(ctx, "GET", f"/api/v1/portfolios/{pf['portfolio_id']}")
    db_v = ctx.db.one("SELECT cash_minor, typeof(cash_minor) t FROM portfolios WHERE portfolio_id=?", (pf["portfolio_id"],))
    ctx.result.update({"api_money_minor": got["cash_minor"], "db_integer_minor": str(db_v["cash_minor"]),
                       "db_storage_type": db_v["t"], "client_type": type(got["cash_minor"]).__name__.replace("str", "string")})
    from ..browser.node_eval import money_client_check
    chk = money_client_check(v)
    ctx.result.update({"client_type": chk["client_type"], "js_number_coercions": chk["js_number_coercions"],
                       "client_parsed_equals": chk["parsed_equals"], "client_check": chk})


# ── 上游故障 ──
def snapshot_build(ctx, inp):
    ctx.ensure("snapshot_final")
    first = ctx.alias("snapshot_fixture_final")
    d0 = content_hash(json.loads(ctx.db.one("SELECT canonical_payload FROM snapshots WHERE snapshot_id=?",
                                            (first,))["canonical_payload"]))
    st, acc, _ = ctx.client().post("/api/v1/snapshots", {"session_date": inp["requested_session_date"],
                                                         "source_mode": "provider_api", "symbols": [], "allow_stale": True})
    if st != 202:
        ctx.result.update({"submit_status": st, "job_status": None})
        return
    job = ctx.client().wait_job(acc["job"]["job_id"], timeout=300)
    calls = [c for c in infra.fake_calls(ctx) if c["op"] == "reference_stock_daily"
             and (c["params"] or {}).get("tradedate") == inp["requested_session_date"].replace("-", "")]
    st2, latest, hd = ctx.client().get("/api/v1/snapshots/latest")
    st3, latest_stale, hd3 = ctx.client().get("/api/v1/snapshots/latest?allow_stale=true")
    d1 = content_hash(json.loads(ctx.db.one("SELECT canonical_payload FROM snapshots WHERE snapshot_id=?",
                                            (first,))["canonical_payload"]))
    labeled = (st2 == 503 and latest["error"]["code"] == "DATA_STALE") and \
        (st3 == 200 and latest_stale["metadata"]["freshness"] != "fresh")
    ctx.result.update({"submit_status": st, "job_status": job["state"], "job_error_code": (job["error"] or {}).get("error", {}).get("code"),
                       "last_snapshot_id": ctx.unalias(latest_stale["snapshot_id"]) if st3 == 200 else None,
                       "old_snapshot_mutated": d0 != d1, "retry_count": max(0, len(calls) - 1),
                       "provider_calls_for_session": len(calls), "stale_display_labeled": labeled,
                       "latest_status_without_allow_stale": st2})


# ── 健康 / 就绪 ──
def health_get(ctx, inp):
    if "snapshot_missing" in ctx.case["fixture_ids"]:
        f = ctx.fixture("snapshot_missing")
        rows, hist = _rows_for_variant(f)
        fx.ingest_fixture_snapshot(ctx, f, snapshot_id="snapshot_fixture_missing", rows=rows)
    st, h, _ = ctx.client().get("/api/v1/health/data")
    st2, legacy, _ = ctx.client().get("/api/health")
    ctx.result.update({"health_status_code": st, "health": h, "health_alive": st == 200 and h["api_ready"] is True
                       and st2 == 200})


def business_readiness_check(ctx, inp):
    h = ctx.result.get("health") or ctx.client().get("/api/v1/health/data")[1]
    ready = h["status"] == "ready"
    ctx.result.update({"data_ready": ready, "missing_reason_visible": bool(h["missing_inputs"]),
                       "missing_inputs": h["missing_inputs"],
                       "overall_acceptance_passed": False,
                       "readiness_rule": "health 只说明进程/数据库可用；业务就绪要求最新快照 quality=complete 且此刻 fresh；"
                                         "验收是否通过只由 report.json 的用例状态决定"})


# ── 异步任务 ──
def async_job_submit(ctx, inp):
    ctx.harness()
    days = ["2026-10-16", "2026-10-19"]
    if inp.get("fault") == "worker_controlled_failure_after_valid_request":
        scen = infra.provider_scenario_ok(ctx, days)
        scen["faults"]["reference_stock_daily"] = ["auth"]
        infra.install_fake_cli(ctx, scen, max_retry=0, timeout=5.0)
    elif "provider_factory" not in ctx.state:
        infra.install_fake_cli(ctx, infra.provider_scenario_ok(ctx, days), max_retry=0, timeout=10.0)
    day = "2026-10-19" if inp.get("fault") else "2026-10-16"     # 故障轮用当天会话：不命中历史会话的原始响应复用
    st, acc, hd = ctx.client().post("/api/v1/snapshots", {"session_date": day, "source_mode": "provider_api",
                                                          "symbols": [], "allow_stale": True})
    ctx.result.setdefault("submit_http_statuses", []).append(st)
    ctx.result.setdefault("submits", []).append({"status": st, "job": acc.get("job") if isinstance(acc, dict) else None,
                                                 "location": hd.get("Location"), "retry_after": hd.get("Retry-After")})
    ctx.result["job_ids_present"] = all(s.get("job") and s["job"].get("job_id") for s in ctx.result["submits"])
    ctx.result["success_inferred_from_202"] = any(s.get("job") and s["job"]["state"] in ("succeeded", "partial")
                                                  for s in ctx.result["submits"])
    ctx.state["last_job_id"] = acc["job"]["job_id"] if st == 202 else None


def job_poll(ctx, inp):
    jid = ctx.state.get("last_job_id")
    job = ctx.client().wait_job(jid, timeout=300)
    polls = ctx.result.setdefault("polls", [])
    rec = {"job_id": jid, "state": job["state"], "resource_id": job["resource_id"],
           "error_code": (job["error"] or {}).get("error", {}).get("code") if job["error"] else None}
    if job["state"] in ("succeeded", "partial"):
        row = ctx.db.one("SELECT snapshot_id, generated_at FROM snapshots WHERE snapshot_id=?", (job["resource_id"],))
        rec["resource_committed"] = row is not None
        rec["finished_after_resource"] = row is not None and job["finished_at"] is not None
    polls.append(rec)
    ok = [p for p in polls if p["state"] in ("succeeded", "partial")]
    ctx.result["success_only_after_db_commit"] = bool(ok) and all(p.get("resource_committed") for p in ok)
    bad = [p for p in polls if p["state"] == "failed"]
    ctx.result["failure_error_code_present"] = bool(bad) and all(p["error_code"] for p in bad)


# ── 股票池 ──
def candidate_universe_build(ctx, inp):
    f = ctx.fixture(inp.get("fixture_id") or "universe_eligibility")
    from panel.domain import trading_rules as tr
    members = []
    for m in f["members"]:
        br = tr.classify_board(m["symbol"], m["stockbasic_market"])
        members.append(uv.Member(m["symbol"], br.board, br.category, br.raw_market, m["special_treatment"], True,
                                 "", br.source))
    res = uv.build(uv.CANDIDATE_POLICY, f["effective_session_date"], members)
    ctx.result.update({"eligible_symbols": res.eligible, "excluded": res.excluded,
                       "excluded_markets": sorted(res.excluded_markets),
                       "st_excluded": all(res.excluded.get(m["symbol"]) == "special_treatment"
                                          for m in f["members"] if m["special_treatment"]),
                       "unknown_market_included": any(m["symbol"] in res.eligible for m in f["members"]
                                                      if m["stockbasic_market"] == "unknown")})


# ── 价格口径 ──
def price_series_normalize(ctx, inp):
    f = ctx.fixture(inp.get("fixture_id") or "price_basis")
    r = ca.normalize_series(f["bars"], f["declared_price_basis"], f["adjustment_policy_version"])
    ctx.result.update({"price_basis": r["price_basis"], "whole_series_adjusted_from_pre_close": r["whole_series_adjusted_from_pre_close"],
                       "adjustment_policy_version_present": bool(r["adjustment_policy_version"]),
                       "original_raw_prices_preserved": r["original_raw_prices_preserved"], "notes": r["notes"]})


def price_series_adjust(ctx, inp):
    f = ctx.state.get("price_basis_fixture") or ctx.fixture("price_basis")
    r = ca.adjust_as_of(f["bars"], f["actions"], inp["cutoff_at"])
    key = "adjusted_hash_after" if "adjusted_hash_before" in ctx.result else "adjusted_hash_before"
    ctx.result[key] = r["adjusted_hash"]
    ctx.result["used_action_ids"] = r["used_action_ids"]
    ctx.result["future_split_applied"] = "future_split" in r["used_action_ids"]
    ctx.result.setdefault("runs", []).append(r)


def future_action_patch(ctx, inp):
    f = ctx.fixture("price_basis")
    acts = []
    for a in f["actions"]:
        if a["action_id"] == "future_split":
            a = dict(a, **{k: v for k, v in inp.items()})
        acts.append(a)
    f["actions"] = acts
    ctx.state["price_basis_fixture"] = f
    ctx.result["future_action_available_at_preserved"] = all(
        a.get("available_at") == b.get("available_at") for a, b in zip(acts, ctx.fixture("price_basis")["actions"]))


def corporate_action_calculate(ctx, inp):
    r = ca.cash_dividend(inp["shares"], inp["cash_per_share"], inp["rounding_policy"], inp.get("ex_reference_deduction"))
    ctx.result.update({"cash_receivable": {"money_minor": r["cash_receivable_minor"]}, "cash_per_share": r["cash_per_share"],
                       "ex_reference_deduction": r["ex_reference_deduction"],
                       "ex_reference_used_as_cash": r["ex_reference_used_as_cash"],
                       "per_share_early_rounding": r["per_share_early_rounding"], "gross_exact_yuan": r["gross_exact_yuan"]})


def return_calculate(ctx, inp):
    try:
        r = ca.return_basis(inp["price_basis"], inp["include_cash"])
        status = 200
        ctx.result.setdefault("raw_cash_separate", r["raw_cash_separate"])
    except ca.DoubleCounted as ex:
        status = ex.status
        ctx.result["error_code"] = ex.code.lower()
    key = "first_http_status" if "first_http_status" not in ctx.result else "second_http_status"
    ctx.result[key] = status
