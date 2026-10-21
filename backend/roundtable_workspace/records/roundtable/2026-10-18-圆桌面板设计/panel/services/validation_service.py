"""方法验证（EVAL-002 / EVAL-003 / EVAL-004）：排队前冻结 EvaluationManifest，worker 只读冻结版本。

三类结果分开：
  - 候选研究（target_type=seat）：在冻结的历史归档上，按样本规则逐个会话重建截点输入、运行席位脚本，
    看候选在 T+1 开盘 → T+h 收盘的后验变化（扣成本、减等权基准）；没有成交模型，execution_return_ratio=null；
  - 前瞻登记（target_type=plan/case 且窗口在未来）：冻结后等待窗口结束，未到期不出结论；
  - 执行模拟：只来自有证据的模拟成交（由复盘统计），这里不伪造。
统计用预注册的 HAC 与移动块 bootstrap；预算（trial_budget）在调度下一项之前检查，触顶即追加 stop 事件；
完整性停止规则（输入里出现截点之后的数据）触发后立即停止、保留已完成结果、禁止 clean 绩效结论。
样本外无可靠超额只评价该实现、数据与口径，不判定原书或作者方法无效。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from ..config import SKILLS_DIR, get_settings
from ..data.evidence_store import EvidenceStore
from ..domain import clock, seat_registry as sr, trading_rules as tr, universe as uv
from ..domain.canonical import canonical_json, content_hash, derived_id, new_id
from ..domain.fees import FEE_SCHEDULES
from ..jobs import queue, worker
from ..jobs.worker import JobFailed, JobResult
from ..web.errors import ApiError
from . import archive_service, plan_service, seat_runner, stats

EXECUTION_MODELS = {"candidate_research_t1_open_th_close_v1", "plan_prospective_v1"}
CANDIDATE_RESEARCH = "candidate_research_t1_open_th_close_v1"


def _int(pattern: str, text: str, default: int) -> int:
    m = re.search(pattern, text or "")
    return int(m.group(1)) if m else default


def roundtrip_cost(fee_model: str) -> Decimal:
    m = FEE_SCHEDULES[fee_model]
    return m.commission_ratio * 2 + m.stamp_sell_ratio + m.transfer_ratio * 2


def _check(db, mi: dict, replay_id) -> list[dict]:
    errs = []
    w = mi["window"]
    if w["end_session_date"] < w["start_session_date"]:
        errs.append({"field": "window", "reason": "end before start"})
    if mi["universe_policy_id"] not in uv.POLICIES:
        errs.append({"field": "universe_policy_id", "reason": "unknown universe policy"})
    if mi["fee_model_version"] not in FEE_SCHEDULES:
        errs.append({"field": "fee_model_version", "reason": "unknown fee model"})
    if mi["execution_model_version"] not in EXECUTION_MODELS:
        errs.append({"field": "execution_model_version", "reason": f"unknown; supported {sorted(EXECUTION_MODELS)}"})
    arch = archive_service.manifest()
    cal = archive_service.calendar()
    horizon = _int(r"horizon=(\d+)", mi["sample_selection_policy"], 5)
    purge = _int(r"purge=(\d+)", mi["sample_selection_policy"], 0)
    tw = mi["training_window"]
    if tw:
        sc = split_check(tw["end_session_date"], w["start_session_date"], horizon, purge, 0, cal)
        if not sc["ok"]:
            errs.append({"field": "training_window", "reason": sc["reason"]})
    if mi["target_type"] == "seat":
        reg = sr.registry_payload(SKILLS_DIR)
        seat = next((s for s in reg["seats"] if s["seat_id"] == mi["target_id"]), None)
        if seat is None:
            errs.append({"field": "target_id", "reason": "unknown seat"})
        else:
            if mi["skill_sha256"] != seat["skill_sha256"]:
                errs.append({"field": "skill_sha256", "reason": f"与当前技能目录不符（当前 {seat['skill_sha256'][:12]}…）"})
            if mi["rule_version"] != seat["rule_version"]:
                errs.append({"field": "rule_version", "reason": f"当前规则版本 {seat['rule_version']}"})
        if mi["execution_model_version"] != CANDIDATE_RESEARCH:
            errs.append({"field": "execution_model_version", "reason": "seat 目标只支持候选研究"})
        if arch["coverage_end"] is None or w["end_session_date"] > arch["coverage_end"]:
            errs.append({"field": "window", "reason": f"历史候选研究的窗口必须在归档范围内（至 {arch['coverage_end']}）"})
        elif mi["dataset_fingerprint"] != arch["fingerprint"]:
            errs.append({"field": "dataset_fingerprint", "reason": f"expected archive fingerprint {arch['fingerprint']}"})
        if cal is not None and mi["calendar_version"] != cal.calendar_version:
            errs.append({"field": "calendar_version", "reason": f"expected {cal.calendar_version}"})
    elif mi["target_type"] == "plan":
        try:
            pid, ver = mi["target_id"], int(mi["target_version"])
        except ValueError:
            errs.append({"field": "target_version", "reason": "plan target_version must be integer version"})
        else:
            p = plan_service.version(db, pid, ver, replay_id)
            if p is None or p["status"] != "frozen":
                errs.append({"field": "target_id", "reason": "plan version not visible or not frozen"})
    elif mi["target_type"] == "case":
        from . import case_orchestrator
        if case_orchestrator.load(db, mi["target_id"], replay_id) is None:
            errs.append({"field": "target_id", "reason": "case not visible"})
    sp = mi["statistical_plan"]
    if sp["minimum_sample_size"] < 2:
        errs.append({"field": "statistical_plan.minimum_sample_size", "reason": "must be >= 2"})
    return errs


def freeze_manifest(c, mi: dict) -> dict:
    mid = f"manifest_{content_hash(mi)[:20]}"
    row = c.execute("SELECT * FROM evaluation_manifests WHERE manifest_id=? AND version=1", (mid,)).fetchone()
    if row:
        return json.loads(row["canonical_payload"])
    now = clock.utc_iso()
    rec = {"manifest_id": mid, "version": 1, "content": mi, "created_at": now, "frozen_at": now}
    rec["frozen_hash"] = content_hash(rec)
    c.execute("""INSERT INTO evaluation_manifests(manifest_id, version, created_at, frozen_at, frozen_hash, content_json,
                 canonical_payload) VALUES(?,?,?,?,?,?,?)""",
              (mid, 1, now, now, rec["frozen_hash"], canonical_json(mi), canonical_json(rec)))
    return rec


def split_check(train_end: str, test_start: str, horizon: int, purge: int = 0, embargo: int = 0, calendar=None) -> dict:
    """训练/验证切分的标签窗口检查（AC-062）：训练末端样本的 h 日标签不能穿越测试起点。

    间隔用交易日计算；没有覆盖该区间的日历时，用自然日差作为交易日数的上界（保守：只会更早报重叠）。
    需要 gap > horizon（或 purge/embargo ≥ horizon）才算隔离。
    """
    if train_end >= test_start:
        return {"ok": False, "code": "TRAIN_TEST_OVERLAP", "gap_sessions": 0,
                "reason": "label_window_overlap: training window must end before evaluation window"}
    from datetime import date
    if calendar is not None and calendar.status(train_end) != "unknown" and calendar.status(test_start) != "unknown":
        gap = len([d for d in calendar.sessions_between(train_end, test_start) if train_end < d < test_start])
        basis = "trading_sessions"
    else:
        gap = (date.fromisoformat(test_start) - date.fromisoformat(train_end)).days - 1
        basis = "calendar_days_upper_bound"
    isolated = gap >= horizon or max(purge, embargo) >= horizon
    return {"ok": isolated, "code": None if isolated else "LABEL_WINDOW_OVERLAP", "gap_sessions": gap, "gap_basis": basis,
            "horizon_sessions": horizon, "purge_sessions": purge, "embargo_sessions": embargo,
            "test_data_opened_for_tuning": False,
            "reason": "isolated" if isolated else
            f"label_window_overlap: 训练末端 {horizon} 个交易日的标签窗口穿越测试起点（间隔 {gap}，purge {purge}，embargo {embargo}）"}


def create(c, db, body: dict, replay_id, request_sha256: str, max_waiting: int) -> dict:
    mi = body["evaluation_manifest"]
    errs = _check(db, mi, replay_id)
    if errs:
        code = "LABEL_WINDOW_OVERLAP" if any("label_window_overlap" in e["reason"] for e in errs) else "MANIFEST_INVALID"
        raise ApiError(422, code, "评估登记不合格，未冻结、未排队", errs)
    rec = freeze_manifest(c, mi)
    return enqueue_frozen(c, rec["manifest_id"], rec["version"], replay_id, request_sha256, max_waiting)


def enqueue_frozen(c, manifest_id: str, manifest_version: int, replay_id, request_sha256: str, max_waiting: int) -> dict:
    """只接受已冻结的登记排队；worker 只读冻结版本（AC-091）。"""
    m = c.execute("SELECT canonical_payload, frozen_hash FROM evaluation_manifests WHERE manifest_id=? AND version=?",
                  (manifest_id, manifest_version)).fetchone()
    if m is None:
        raise ApiError(409, "MANIFEST_NOT_FROZEN", "评估登记尚未冻结：先冻结再排队（草稿不能进入队列）",
                       [{"field": "evaluation_manifest", "reason": "not frozen"}])
    rec = json.loads(m["canonical_payload"])
    mi = rec["content"]
    vid = new_id("validation")
    job = queue.enqueue(c, kind="validation", payload={"validation_id": vid}, request_sha256=request_sha256,
                        resource_type="validation", resource_id=vid, replay_id=replay_id, max_waiting=max_waiting)
    run = {"validation_id": vid, "job_id": job["job_id"], "state": "queued", "target_type": mi["target_type"],
           "target_id": mi["target_id"], "target_version": mi["target_version"],
           "dataset_fingerprint": mi["dataset_fingerprint"], "rule_version": mi["rule_version"],
           "calendar_version": mi["calendar_version"], "fee_model_version": mi["fee_model_version"],
           "execution_model_version": mi["execution_model_version"], "seed": mi["seed"], "window": mi["window"],
           "split_policy": mi["split_policy"],
           "metrics": {"sample_count": 0, "executed_trade_count": 0, "win_rate_ratio": None, "expectancy_r": None,
                       "max_drawdown_ratio": None, "excess_return_ratio": None, "directional_price_return_ratio": None,
                       "execution_return_ratio": None},
           "limit_notes": ["排队中：登记已冻结，worker 只读取冻结版本"], "evidence_ids": [], "created_at": clock.utc_iso(),
           "finished_at": None, "evaluation_manifest": rec}
    c.execute("""INSERT INTO validation_runs(validation_id, job_id, target_type, target_id, target_version, state,
                 dataset_fingerprint, rule_version, calendar_version, fee_model_version, execution_model_version, seed,
                 start_session_date, end_session_date, split_policy, replay_id, created_at, finished_at, manifest_json,
                 result_json, canonical_payload, manifest_id, manifest_version)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,NULL,?,?,1)""",
              (vid, job["job_id"], mi["target_type"], mi["target_id"], mi["target_version"], "queued",
               mi["dataset_fingerprint"], mi["rule_version"], mi["calendar_version"], mi["fee_model_version"],
               mi["execution_model_version"], mi["seed"], mi["window"]["start_session_date"], mi["window"]["end_session_date"],
               mi["split_policy"], replay_id, run["created_at"], canonical_json(rec), canonical_json(run), rec["manifest_id"]))
    assert manifest_version == 1
    _event(c, vid, "queued", "frozen manifest " + rec["frozen_hash"][:16], {"manifest_hash": rec["frozen_hash"]})
    return job


def _event(c, vid: str, etype: str, reason: str | None, payload: dict) -> None:
    n = c.execute("SELECT COALESCE(MAX(sequence),0) FROM validation_events WHERE validation_id=?", (vid,)).fetchone()[0]
    c.execute("INSERT INTO validation_events(validation_id, sequence, event_type, reason, recorded_at, canonical_payload) "
              "VALUES(?,?,?,?,?,?)", (vid, n + 1, etype, reason, clock.utc_iso(), canonical_json(payload)))


def events(db, vid: str) -> list[dict]:
    """追加式事件；列字段（sequence/event_type/reason/recorded_at）优先于载荷里的同名键。"""
    out = []
    for r in db.query("SELECT * FROM validation_events WHERE validation_id=? ORDER BY sequence", (vid,)):
        payload = json.loads(r["canonical_payload"])
        out.append({**payload, "payload": payload, "sequence": r["sequence"], "event_type": r["event_type"],
                    "reason": r["reason"], "recorded_at": r["recorded_at"]})
    return out


def get(db, vid: str, replay_id) -> dict | None:
    r = db.one("SELECT canonical_payload, replay_id FROM validation_runs WHERE validation_id=?", (vid,))
    if r is None or r["replay_id"] != replay_id:
        return None
    return json.loads(r["canonical_payload"])


def _save(c, run: dict, result: dict | None = None) -> None:
    c.execute("UPDATE validation_runs SET state=?, finished_at=?, result_json=?, canonical_payload=? WHERE validation_id=?",
              (run["state"], run["finished_at"], canonical_json(result) if result is not None else None,
               canonical_json(run), run["validation_id"]))


# ── 候选研究：截点输入 + 席位脚本 ──
def _features_for(day: str, days: list[str]) -> tuple[list[dict], dict[str, dict]]:
    rows, today = [], {}
    for d in days:
        x = archive_service.read_day(d)
        if not x:
            continue
        for r in x[1]:
            dr = seat_runner._daily_row(r, d)
            if dr:
                rows.append(dr)
                if d == day:
                    today[dr["symbol"]] = dr
    return rows, today


def _candidate_ok(code6: str, name: str) -> bool:
    sym = tr.to_symbol(code6)
    if not sym:
        return False
    br = tr.classify_by_code_prefix(sym)
    return br.board in ("SH_MAIN", "SZ_MAIN") and not tr.is_st_name(name) and "退" not in (name or "")


def _run_seat_on(seat_id: str, rows: list[dict], today: dict, store: EvidenceStore, timeout: float) -> tuple[dict | None, str]:
    s = get_settings()
    tmp = Path(tempfile.mkdtemp(dir=str(s.work_dir), prefix="val-"))
    try:
        (tmp / "features.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        files = {"features": "features.json"}
        pool = []
        for code, r in today.items():
            pc, hi, cl = r.get("pre_close"), r.get("high"), r.get("close")
            if not pc or hi is None or cl is None:
                continue
            br = tr.classify_by_code_prefix(tr.to_symbol(code) or f"{code}.SH")
            from .snapshot_build import derive_limits
            L = derive_limits(br.board, tr.is_st_name(r.get("name")), Decimal(repr(pc)))
            if not L:
                continue
            up = float(L[0])
            typ = "u" if abs(cl - up) < 0.0045 else ("ub" if abs(hi - up) < 0.0045 else None)
            if typ:
                pool.append({"tradedate": r["tradedate"], "symbol": code, "name": r["name"], "type": typ,
                             "limit_times": None, "first_time": None, "open_times": None, "fd_amount": None,
                             "amount": r.get("amount"), "close": cl, "pct_chg": r.get("pct_chg")})
        (tmp / "limit_pool.json").write_text(json.dumps(pool, ensure_ascii=False), encoding="utf-8")
        files["limit_pool"] = "limit_pool.json"
        mf = {"files": files, "missing_inputs": [], "evidence": {}}
        out = tmp / "out"
        out.mkdir()
        cmds = seat_runner._cmd(seat_id, tmp, mf, [], out)
        if cmds is None:
            return None, "input_unavailable"
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONHASHSEED": "0"}
        with seat_runner._proc_sem:
            for cmd in cmds:
                p = subprocess.run(cmd, capture_output=True, timeout=timeout, shell=False, env=env, cwd=str(out))
                if p.returncode != 0:
                    return None, "script_failed"
        f = out / seat_runner.SCRIPT_OUT[seat_id]
        if not f.exists():
            return None, "script_failed"
        raw = f.read_bytes()
        store.put(raw)
        return json.loads(raw), ""
    except subprocess.TimeoutExpired:
        return None, "script_timeout"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fwd(day_entry: dict, day_exit: dict, code: str) -> float | None:
    a, b = day_entry.get(code), day_exit.get(code)
    if not a or not b or not a.get("open") or b.get("close") is None or a["open"] <= 0:
        return None
    return b["close"] / a["open"] - 1


INTEGRITY_HOOK = None      # 验收注入：callable(day, rows) → rows（模拟未来数据泄漏）


def run_candidate_research(db, run: dict, rec: dict, vid: str, ctx) -> tuple[str, dict]:
    mi = rec["content"]
    cal = archive_service.calendar()
    store = EvidenceStore(get_settings().evidence_dir)
    every = _int(r"every_(\d+)_sessions", mi["sample_selection_policy"], 10)
    horizon = _int(r"horizon=(\d+)", mi["sample_selection_policy"], 5)
    cost = roundtrip_cost(mi["fee_model_version"])
    all_days = cal.sessions_between("0000-01-01", "9999-12-31")
    window_days = [d for d in all_days if mi["window"]["start_session_date"] <= d <= mi["window"]["end_session_date"]]
    samples = window_days[::every]
    outcomes, series, cand_rets = [], [], []
    stopped = None
    used = 0
    for i, D in enumerate(samples):
        if used + 1 > mi["trial_budget"]:
            stopped = "budget_limit"
            with db.txn() as c:
                _event(c, vid, "stopped", "budget_limit", {"next_sample": D, "used": used, "trial_budget": mi["trial_budget"]})
            break
        idx = all_days.index(D)
        hist = all_days[max(0, idx - sb_history() + 1): idx + 1]
        if len(hist) < 61:
            outcomes.append({"session": D, "outcome": "insufficient_history"})
            with db.txn() as c:
                _event(c, vid, "item_completed", "insufficient_history", {"session": D})
            continue
        if idx + horizon >= len(all_days):
            outcomes.append({"session": D, "outcome": "label_unavailable"})
            with db.txn() as c:
                _event(c, vid, "item_completed", "label_unavailable", {"session": D})
            continue
        rows, today = _features_for(D, hist)
        if INTEGRITY_HOOK:
            rows = INTEGRITY_HOOK(D, rows)
        if any(r["tradedate"] > D.replace("-", "") for r in rows):
            stopped = "integrity_failure"
            outcomes.append({"session": D, "outcome": "integrity_failure"})
            with db.txn() as c:
                _event(c, vid, "stopped", "integrity_failure", {"session": D, "reason": "输入含截点之后的数据"})
            break
        used += 1
        raw, err = _run_seat_on(mi["target_id"], rows, today, store, timeout=max(60.0, ctx.remaining() / 2))
        if raw is None:
            outcomes.append({"session": D, "outcome": err})
            with db.txn() as c:
                _event(c, vid, "item_failed", err, {"session": D})
            continue
        gate_open = (raw.get("gate") or {}).get("open")
        cands = [str(cc.get("symbol")).zfill(6) for cc in raw.get("candidates") or []]
        cands = [x for x in cands if x in today and _candidate_ok(x, today[x]["name"])]
        entry_day, exit_day = all_days[idx + 1], all_days[idx + horizon]
        ent = _features_for(entry_day, [entry_day])[1]
        ext = _features_for(exit_day, [exit_day])[1]
        uni = [x for x, r in today.items() if _candidate_ok(x, r["name"])]
        bench = [v for v in (_fwd(ent, ext, x) for x in uni) if v is not None]
        b = sum(bench) / len(bench) if bench else None
        if not gate_open or not cands:
            outcomes.append({"session": D, "outcome": "no_signal", "gate_open": gate_open, "candidates": len(cands)})
            with db.txn() as c:
                _event(c, vid, "item_completed", "no_signal", {"session": D})
            continue
        rets = [v for v in (_fwd(ent, ext, x) for x in cands) if v is not None]
        if not rets or b is None:
            outcomes.append({"session": D, "outcome": "label_unavailable"})
            continue
        m = sum(rets) / len(rets)
        ex = m - float(cost) - b
        series.append(ex)
        cand_rets.append(m)
        outcomes.append({"session": D, "outcome": "evaluated", "candidates": len(cands), "mean_return": stats.fmt(m),
                         "benchmark": stats.fmt(b), "excess": stats.fmt(ex)})
        with db.txn() as c:
            _event(c, vid, "item_completed", "evaluated", {"session": D, "excess": stats.fmt(ex)})
        ctx.progress(i + 1, len(samples), "evaluating")
    sp = mi["statistical_plan"]
    lag = _int(r"lag=(\d+)", sp["confidence_interval_method"], 10)
    block = _int(r"block=(\d+)", sp["confidence_interval_method"], 10)
    reps = _int(r"replicates=(\d+)", sp["confidence_interval_method"], 2000)
    rep = stats.report(series, lag=lag, block=block, replicates=reps, seed=mi["seed"]) if series else \
        {"n": 0, "status": "inconclusive", "reason": "没有可评价样本"}
    counts: dict[str, int] = {}
    for o in outcomes:
        counts[o["outcome"]] = counts.get(o["outcome"], 0) + 1
    result = {"category": "candidate_research", "outcomes": outcomes, "outcome_counts": counts, "stats": rep,
              "stopped": stopped, "horizon_sessions": horizon, "every_sessions": every, "cost_roundtrip_ratio": str(cost),
              "clean_performance_claim_allowed": stopped != "integrity_failure"}
    state = "failed" if stopped == "integrity_failure" else ("partial" if stopped else "succeeded")
    return state, result


def sb_history() -> int:
    from .snapshot_build import HISTORY_SESSIONS
    return HISTORY_SESSIONS + 1


def run_prospective(db, run: dict, rec: dict) -> tuple[str, dict]:
    mi = rec["content"]
    from . import snapshot_service
    snap = snapshot_service.latest(db, run.get("replay_id") if False else None)
    end = mi["window"]["end_session_date"]
    if snap is None or snap["metadata"]["session_date"] < end:
        return "partial", {"category": "prospective_registration", "status": "in_progress",
                           "reason": f"窗口到 {end} 尚未结束（或数据未到）：不出结论", "clean_performance_claim_allowed": False}
    return "succeeded", {"category": "prospective_registration", "status": "window_complete",
                         "note": "窗口已结束；方向收益见计划复盘（post_trade），执行收益只来自成交事件",
                         "clean_performance_claim_allowed": True}


@worker.register("validation")
def run_job(ctx: worker.JobContext):
    db = ctx.db
    vid = ctx.payload["validation_id"]
    r = db.one("SELECT * FROM validation_runs WHERE validation_id=?", (vid,))
    if r is None:
        raise JobFailed("VALIDATION_MISSING", "验证不存在")
    run = json.loads(r["canonical_payload"])
    m = db.one("SELECT canonical_payload, frozen_hash FROM evaluation_manifests WHERE manifest_id=? AND version=?",
               (r["manifest_id"], r["manifest_version"]))
    rec = json.loads(m["canonical_payload"])
    if rec["frozen_hash"] != m["frozen_hash"] or content_hash({k: v for k, v in rec.items() if k != "frozen_hash"}) != rec["frozen_hash"]:
        raise JobFailed("MANIFEST_HASH_MISMATCH", "冻结登记校验失败，拒绝运行")
    if run["state"] != "queued":
        return JobResult("succeeded", resource_id=vid, resource_type="validation")
    run["state"] = "running"
    with db.txn() as c:
        _save(c, run)
        _event(c, vid, "started", None, {"worker_manifest_hash": rec["frozen_hash"]})
    if rec["content"]["target_type"] == "seat":
        state, result = run_candidate_research(db, run, rec, vid, ctx)
    else:
        state, result = run_prospective(db, run, rec)
    store = EvidenceStore(get_settings().evidence_dir)
    sha = store.put_json(result)
    eid = derived_id("ev", vid, sha)
    now = clock.utc_iso()
    ev = {"evidence_id": eid, "kind": "rule_derivation", "snapshot_id": None, "symbol": None, "observed_at": None,
          "available_at": None, "first_observed_at": now, "time_precision": "unknown", "availability_basis": "unknown",
          "captured_at": now, "source_ref": {"source_id": "validation_runner", "authority_type": "user",
                                             "locator": f"evidence:{sha}", "content_sha256": sha, "retrieved_at": now,
                                             "published_at": None, "citation": f"validation {vid} 完整结果"},
          "field_path": f"validations.{vid}.result", "value": {"result_sha256": sha}, "unit": "json", "quality": "verified",
          "related_evidence_ids": []}
    rep = result.get("stats") or {}
    s_ = result.get("outcome_counts") or {}
    ser = [o for o in result.get("outcomes", []) if o.get("outcome") == "evaluated"]
    excess = rep.get("mean") if rep.get("status") == "computed" else (
        stats.fmt(sum(float(o["excess"]) for o in ser) / len(ser)) if ser else None)
    direc = stats.fmt(sum(float(o["mean_return"]) for o in ser) / len(ser)) if ser else None
    notes = []
    if result["category"] == "candidate_research":
        notes = [f"类别：候选研究（不是执行收益）；T+1 开盘 → T+{result['horizon_sessions']} 收盘，每 {result['every_sessions']} 个交易日取一个样本",
                 f"成本：按费用模型双边约 {result['cost_roundtrip_ratio']}（不含最低佣金）；无排队、涨跌停、停牌成交建模",
                 "股票池：沪深主板非 ST（历史归档无时点证券身份表，板块按代码段受控 fallback，ST 按当日名称）",
                 f"样本结果：{json.dumps(s_, ensure_ascii=False)}（负结果、无信号、失败全部保留）",
                 (f"推断：HAC(lag={rep.get('hac_lag')}) 95% CI {rep.get('hac_normal_ci')}；块 bootstrap(b={rep.get('block_length')}, "
                  f"B={rep.get('replicates')}, seed={rep.get('seed')}) CI {rep.get('block_percentile_ci')}；非重叠子样本均值 "
                  f"{(rep.get('nonoverlap_subsample') or {}).get('mean')}") if rep.get("status") == "computed"
                 else f"推断：{rep.get('reason', 'inconclusive')}",
                 "结论边界：该实现、该数据、该口径的结果；样本外无可靠超额不能判定原书或作者方法无效"]
        if result.get("stopped"):
            notes.append(f"已停止：{result['stopped']}（停止事件已持久化，不自动续跑）")
    else:
        notes = [result.get("reason") or result.get("note") or ""]
    run.update({"state": state, "finished_at": clock.utc_iso(),
                "metrics": {"sample_count": len(ser), "executed_trade_count": 0, "win_rate_ratio": None,
                            "expectancy_r": None, "max_drawdown_ratio": None, "excess_return_ratio": excess,
                            "directional_price_return_ratio": direc, "execution_return_ratio": None},
                "limit_notes": notes, "evidence_ids": [eid]})
    with db.txn() as c:
        c.execute("""INSERT OR IGNORE INTO evidence(evidence_id, snapshot_id, kind, symbol, source_id, authority_type,
                     content_sha256, observed_at, available_at, first_observed_at, captured_at, time_precision,
                     availability_basis, quality, replay_id, canonical_payload) VALUES(?,NULL,'rule_derivation',NULL,
                     'validation_runner','user',?,NULL,NULL,?,?,'unknown','unknown','verified',?,?)""",
                  (eid, sha, now, now, run.get("replay_id") if False else None, canonical_json(ev)))
        _save(c, run, result)
        _event(c, vid, "finished", state, {"result_sha256": sha})
    if state == "failed":
        raise JobFailed("VALIDATION_STOPPED_INTEGRITY", "完整性停止规则触发（输入含截点之后的数据）", partial_resource=vid)
    return JobResult(state, resource_id=vid, resource_type="validation")


@worker.on_failure("validation")
def _failed(db, row, code):
    p = json.loads(row["canonical_payload"])
    vid = p.get("validation_id")
    r = db.one("SELECT canonical_payload, state FROM validation_runs WHERE validation_id=?", (vid,))
    if r and r["state"] in ("queued", "running"):
        run = json.loads(r["canonical_payload"])
        run.update({"state": "failed", "finished_at": clock.utc_iso(),
                    "limit_notes": run["limit_notes"] + [f"任务失败：{code}"]})
        with db.txn() as c:
            _save(c, run)
            _event(c, vid, "finished", f"failed:{code}", {})


# ── 冻结后的修改请求：一律拒绝，改动必须新建 validation_id（AC-068 / AC-093） ──
def update_manifest(db, manifest_id: str, patch: dict) -> None:
    r = db.one("SELECT frozen_hash FROM evaluation_manifests WHERE manifest_id=? ORDER BY version DESC LIMIT 1",
               (manifest_id,))
    if r is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "评估登记不存在")
    raise ApiError(409, "MANIFEST_FROZEN", "评估登记已冻结：不能原地换票、变窗口、调参数或改预算/停止规则；"
                   "如需改动，请提交新的登记（新的 validation_id），原登记保留",
                   [{"field": k, "reason": "frozen"} for k in sorted(patch)][:20] +
                   [{"field": "validation_id", "reason": "new_validation_id_required"}])


def run_of_manifest(db, manifest_id: str) -> dict | None:
    r = db.one("SELECT validation_id FROM validation_runs WHERE manifest_id=? ORDER BY created_at LIMIT 1", (manifest_id,))
    return dict(r) if r else None


# ── 前瞻登记：冻结后才开始记报价；结项只用冻结之后第一笔可追溯报价（AC-061） ──
def quote_append(db, vid: str, symbol: str, price: str, provider_time: str, captured_at: str,
                 source_id: str = "prospective_quote_feed") -> str:
    r = db.one("SELECT canonical_payload FROM validation_runs WHERE validation_id=?", (vid,))
    if r is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "验证不存在")
    rec = json.loads(r["canonical_payload"])["evaluation_manifest"]
    after = clock.parse_utc(provider_time) >= clock.parse_utc(rec["frozen_at"])
    store = EvidenceStore(get_settings().evidence_dir)
    body = {"validation_id": vid, "symbol": symbol, "price_decimal": price, "provider_time": provider_time,
            "captured_at": captured_at}
    sha = store.put_json(body)
    eid = derived_id("ev", vid, "quote", provider_time, sha)
    ev = {"evidence_id": eid, "kind": "api_fact", "snapshot_id": None, "symbol": symbol, "observed_at": provider_time,
          "available_at": provider_time, "first_observed_at": captured_at, "time_precision": "exact",
          "availability_basis": "recorded_observation", "captured_at": captured_at,
          "source_ref": {"source_id": source_id, "authority_type": "vendor", "locator": f"evidence:{sha}",
                         "content_sha256": sha, "retrieved_at": captured_at, "published_at": None,
                         "citation": f"prospective quote for {vid}"},
          "field_path": f"validations.{vid}.quotes", "value": price, "unit": "price_decimal",
          "quality": "verified", "related_evidence_ids": []}
    with db.txn() as c:
        c.execute("""INSERT OR IGNORE INTO evidence(evidence_id, snapshot_id, kind, symbol, source_id, authority_type,
                     content_sha256, observed_at, available_at, first_observed_at, captured_at, time_precision,
                     availability_basis, quality, replay_id, canonical_payload) VALUES(?,NULL,'api_fact',?,?,'vendor',?,?,?,?,?,
                     'exact','recorded_observation','verified',NULL,?)""",
                  (eid, symbol, source_id, sha, provider_time, provider_time, captured_at, captured_at, canonical_json(ev)))
        _event(c, vid, "item_completed", "quote_recorded", {"evidence_id": eid, "after_freeze": after})
    return eid


def prospective_settle(db, vid: str, exit_price: str, exit_at: str | None = None) -> dict:
    r = db.one("SELECT canonical_payload FROM validation_runs WHERE validation_id=?", (vid,))
    if r is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "验证不存在")
    run = json.loads(r["canonical_payload"])
    rec = run["evaluation_manifest"]
    frozen = clock.parse_utc(rec["frozen_at"])
    quotes = []
    for e in db.query("SELECT canonical_payload FROM evidence WHERE json_extract(canonical_payload, '$.field_path')=? ORDER BY available_at",
                      (f"validations.{vid}.quotes",)):
        q = json.loads(e["canonical_payload"])
        if clock.parse_utc(q["available_at"]) >= frozen:
            quotes.append(q)
    out = {"validation_id": vid, "reference_quote_evidence_id": None, "reference_price": None,
           "quote_after_freeze": False, "direction_return_ratio": None, "executed_return_ratio": None,
           "execution_events": [], "note": ""}
    if not quotes:
        out["note"] = "冻结后还没有可追溯报价：不出方向结论（不用冻结前价格当成本）"
        return out
    ref = quotes[0]
    from decimal import Decimal
    from ..domain.money import ratio_str
    rp, xp = Decimal(ref["value"]), Decimal(exit_price)
    out.update({"reference_quote_evidence_id": ref["evidence_id"], "reference_price": ref["value"],
                "quote_after_freeze": True, "direction_return_ratio": ratio_str(xp - rp, rp)})
    mi = rec["content"]
    if mi["target_type"] == "plan":
        evs = db.query("SELECT event_id FROM journal_events WHERE plan_id=? AND event_type IN ('paper_fill','manual_trade_record')",
                       (mi["target_id"],))
        out["execution_events"] = [e["event_id"] for e in evs]
    out["note"] = ("方向变化 = 冻结后第一笔可追溯报价 → 结项价；没有成交事件，执行收益为 null"
                   if not out["execution_events"] else "有成交事件：执行收益见复盘（post_trade）")
    with db.txn() as c:
        _event(c, vid, "item_completed", "prospective_settled", {k: out[k] for k in ("reference_quote_evidence_id",
                                                                                        "direction_return_ratio")})
    return out


# ── 批量结果：负结果、无信号、超时、模型无效全部留档（AC-064） ──
OUTCOME_KINDS = {"negative": "item_completed", "positive": "item_completed", "no_signal": "item_completed",
                 "provider_timeout": "item_failed", "model_invalid": "item_failed", "data_missing": "item_failed"}


def record_outcomes(db, vid: str, outcomes: list[dict]) -> list[int]:
    seqs = []
    with db.txn() as c:
        for o in outcomes:
            kind = o["outcome"]
            et = OUTCOME_KINDS.get(kind)
            if et is None:
                raise ApiError(422, "OUTCOME_UNKNOWN", f"未知结果类型 {kind}")
            reason = kind if et == "item_completed" else f"excluded:{kind}"
            _event(c, vid, et, reason, {k: v for k, v in o.items()})
            seqs.append(c.execute("SELECT MAX(sequence) FROM validation_events WHERE validation_id=?", (vid,)).fetchone()[0])
    return seqs


# ── 历史回放中的模型解释：显式披露记忆污染，不升级为干净前瞻证据（AC-060） ──
def historical_llm_run(db, *, provider, network_enabled: bool, anonymize_symbols: bool, prompt_template: str,
                       facts: list[dict], case_id: str | None = None) -> dict:
    from ..domain.canonical import sha256_hex
    from ..adapters import llm_provider
    prompt = prompt_template.format(n=len(facts))
    tool_log = [{"tool": "facts_lookup", "fact_ids": [f["fact_id"] for f in facts], "network": network_enabled}]
    if anonymize_symbols:
        tool_log.append({"tool": "anonymizer", "mapping_hashed": True})
    out = provider.explain(prompt, facts) if provider is not None else None
    status = "not_called" if provider is None else "accepted"
    checks = llm_provider.validate_text(out or "") if out else {"status": "accepted", "policy_errors": []}
    if out and checks["status"] != "accepted":
        status = "invalid_output"
    rid = new_id("modelrun")
    with db.txn() as c:
        c.execute("""INSERT INTO model_runs(model_run_id, case_id, seat_run_id, provider_id, model_id, llm_mode, prompt_sha256,
                     output_sha256, status, error_codes_json, created_at) VALUES(?,?,NULL,?,?,?,?,?,?,?,?)""",
                  (rid, case_id, getattr(provider, "provider_id", None), getattr(provider, "model_id", None),
                   "optional" if provider is not None else "disabled", sha256_hex(prompt),
                   sha256_hex(out) if out else None, status, canonical_json(checks.get("policy_errors") or []),
                   clock.utc_iso()))
    store = EvidenceStore(get_settings().evidence_dir)
    log_sha = store.put_json({"model_run_id": rid, "tool_log": tool_log})
    return {"model_run_id": rid, "model_id": getattr(provider, "model_id", None), "prompt_sha256": sha256_hex(prompt),
            "tool_log_sha256": log_sha, "tool_log": tool_log, "status": status,
            "model_memory_contamination_possible": True, "clean_prospective_evidence": False,
            "network_enabled": network_enabled, "anonymized": anonymize_symbols,
            "note": "历史数据可能已在模型训练语料中：匿名化只能降低、不能排除记忆污染；结果只作解释练习，不作前瞻证据"}


# ── 协议审查（纯函数，验收与界面共用） ──
def claim_check(sample_days: int, weekly_wins: int | None, weekly_trials: int | None, proposed_claim: str) -> dict:
    """结论措辞审查（AC-065）：小样本与代理实现不能升级成「原书无效 / 没有技能 / 稳定盈利」。"""
    banned = ("original_book_invalid", "no_skill", "proven_profitable", "stable_profit", "原书无效", "稳定盈利")
    strong = any(b in proposed_claim for b in banned)
    reasons = []
    if sample_days < 250:
        reasons.append(f"sample_days={sample_days} 远少于可推断的年度样本，且重叠前瞻收益不独立")
    if weekly_trials is not None and weekly_trials < 20:
        reasons.append(f"一周 {weekly_wins}/{weekly_trials} 胜负只是 {weekly_trials} 次观察，不能评价技能")
    reasons.append("代理实现（日线规则、候选排序）不等于原书完整方法")
    return {"claim_allowed": (not strong) and sample_days >= 250, "conclusion": "inconclusive",
            "negative_evidence_preserved": True, "proxy_is_original_method": False, "reasons": reasons,
            "allowed_wording": "该版本、该样本、该口径未提供可靠正超额证据，并存在已披露的实现与数据限制"}


ABLATION_REQUIRED = {"fixed_rules", "single_assistant", "full_roundtable"}


def ablation_protocol(arms: list[str], snapshot_ids: list[str], tasks: list[str], cutoffs: list[str]) -> dict:
    """消融协议（AC-066）：每个臂用同一组快照、任务与截点；删席臂必须登记；不使用「独立票数」指标。"""
    plan = [{"arm": a, "snapshot_ids": list(snapshot_ids), "tasks": list(tasks), "cutoffs": list(cutoffs)} for a in arms]
    same = all(p["snapshot_ids"] == plan[0]["snapshot_ids"] and p["tasks"] == plan[0]["tasks"]
               and p["cutoffs"] == plan[0]["cutoffs"] for p in plan) if plan else False
    return {"arms": plan, "shared_snapshot_ids": same, "shared_tasks_and_cutoffs": same,
            "all_ablation_arms_registered": ABLATION_REQUIRED <= set(arms) and any(a.startswith("remove_") for a in arms),
            "metrics": ["grounding_error_ratio", "plan_eligibility_error_ratio", "plan_deviation_ratio"],
            "independent_votes_metric_used": False,
            "note": "多个席位引用同一事实只算一份证据；比较的是规则/计划质量，不是赞成票数"}


def ablation_protocol_validate(arms: list[str], shared_snapshot_ids: bool = True, shared_tasks: bool = True) -> dict:
    return ablation_protocol(arms, ["snapshot_fixture_final"] if shared_snapshot_ids else [],
                             ["task"] if shared_tasks else [], ["cutoff"])


def cost_matched_report(arms: list[dict], equal_task_budget: bool, equal_compute_budget: bool) -> dict:
    """成本匹配比较（AC-067）：同任务预算与同算力预算两种报告都要有，并列出 token/调用/费用/延迟。"""
    fields = ["tokens", "calls", "currency_cost", "latency"]
    rows = [{"arm": a["arm"], **{f: a.get(f) for f in fields}, "quality": a.get("quality")} for a in arms]
    return {"task_matched_report_present": bool(equal_task_budget) and bool(rows),
            "compute_matched_report_present": bool(equal_compute_budget) and bool(rows),
            "reported_cost_fields": fields, "quality_metrics_present": all(r["quality"] is not None for r in rows),
            "rows": rows}


def cost_matched_validate(equal_task_budget: bool, equal_compute_budget: bool) -> dict:
    arms = [{"arm": a, "tokens": 0, "calls": 0, "currency_cost": "0", "latency": "0", "quality": None}
            for a in ("fixed_rules", "single_assistant", "full_roundtable")]
    return cost_matched_report(arms, equal_task_budget, equal_compute_budget)
