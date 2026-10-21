"""方法验证 driver（EVAL-002 / EVAL-003 / EVAL-004）。"""
from __future__ import annotations

import copy
import json
import time
from decimal import Decimal

from panel.adapters import llm_provider
from panel.domain import clock
from panel.domain.canonical import content_hash
from panel.services import archive_service, stats, validation_service as vs
from panel.web.errors import ApiError
from panel.web.handlers.seats import ensure_registry

from .. import fixtures as fx
from ..context import network_attempts
from .replay import ensure_archive


class OfflineFixtureProvider:
    """离线确定性模型替身（不联网、不读密钥）：只复述给定事实数量。"""
    provider_id = "fixture_offline"
    model_id = "fixture_model"

    def __init__(self):
        self.calls = 0

    def explain(self, prompt, facts):
        self.calls += 1
        return f"根据 {len(facts)} 条已给事实复述，不给目标价与胜率。"


# ── 登记（manifest）构造 ──
def plan_manifest(ctx, *, window=("2026-10-19", "2026-10-30"), trial_budget=10, training_window=None, hypothesis=None,
                  stopping_rules=None, statistical_plan=None, holding=5) -> dict:
    plan = fx.ensure_plan(ctx, "plan_valid", freeze=True)["plan"]
    m = archive_service.manifest()
    reg = ensure_registry(ctx.db)
    seat = next(s for s in reg["seats"] if s["seat_id"] == plan["primary_strategy_id"])
    return {"target_type": "plan", "target_id": plan["plan_id"], "target_version": str(plan["version"]),
            "dataset_fingerprint": m["fingerprint"] if m["files"] else "0" * 64, "rule_version": seat["rule_version"],
            "calendar_version": fx.calendar_of(ctx).calendar_version, "fee_model_version": "fee_fixture_v1",
            "execution_model_version": "plan_prospective_v1", "seed": 20261019,
            "window": {"start_session_date": window[0], "end_session_date": window[1]}, "split_policy": "holdout",
            "hypothesis": hypothesis or "冻结后的计划在窗口内的方向变化（前瞻登记）", "skill_sha256": seat["skill_sha256"],
            "code_revision": "acceptance", "universe_policy_id": "universe_cn_mainboard_v1", "universe_fingerprint": "0" * 64,
            "sample_selection_policy": f"single_plan horizon={holding}", "inclusion_criteria": ["冻结计划的标的"],
            "exclusion_criteria": ["停牌日不计"], "training_window": training_window, "benchmark_id": "equal_weight_universe",
            "baseline_policy": "fixed_rules,single_assistant,full_roundtable 同任务对照", "allocation_budget_minor": None,
            "trial_budget": trial_budget,
            "statistical_plan": statistical_plan or {"primary_metric": "direction_return_ratio",
                                                     "secondary_metrics": ["net_simulation_return_ratio"],
                                                     "null_hypothesis": "方向变化均值为 0", "minimum_sample_size": 2,
                                                     "confidence_interval_method": "hac lag=10 block=10 replicates=2000",
                                                     "significance_level_ratio": "0.05",
                                                     "multiple_testing_policy": "single_preregistered_test",
                                                     "outlier_policy": "keep_all"},
            "stopping_rules": stopping_rules or [{"condition_id": "stop_on_budget", "description": "预算到界前停止调度"},
                                                 {"condition_id": "stop_on_data_leak", "description": "输入含截点后数据即停"}]}


def seat_manifest(ctx, *, trial_budget, every=5, start_idx=72, end_idx=None) -> dict:
    days = ensure_archive(ctx)
    m = archive_service.manifest()
    reg = ensure_registry(ctx.db)
    seat = next(s for s in reg["seats"] if s["seat_id"] == "trading-wyckoff-v1")
    end_idx = end_idx or (start_idx + every * 6)
    return {"target_type": "seat", "target_id": seat["seat_id"], "target_version": seat["rule_version"],
            "dataset_fingerprint": m["fingerprint"], "rule_version": seat["rule_version"],
            "calendar_version": m["calendar_version"], "fee_model_version": "fee_fixture_v1",
            "execution_model_version": "candidate_research_t1_open_th_close_v1", "seed": 20261019,
            "window": {"start_session_date": days[start_idx], "end_session_date": days[end_idx]},
            "split_policy": "full_sample_descriptive", "hypothesis": "威科夫候选 5 日后验变化扣成本后优于等权基准",
            "skill_sha256": seat["skill_sha256"], "code_revision": "acceptance", "universe_policy_id": "universe_cn_mainboard_v1",
            "universe_fingerprint": "0" * 64, "sample_selection_policy": f"every_{every}_sessions horizon=5",
            "inclusion_criteria": ["沪深主板非 ST"], "exclusion_criteria": ["当日停牌"], "training_window": None,
            "benchmark_id": "equal_weight_universe", "baseline_policy": "等权全池", "allocation_budget_minor": None,
            "trial_budget": trial_budget,
            "statistical_plan": {"primary_metric": "excess_return_ratio", "secondary_metrics": [],
                                 "null_hypothesis": "超额均值=0", "minimum_sample_size": 2,
                                 "confidence_interval_method": "hac lag=1 block=2 replicates=200",
                                 "significance_level_ratio": "0.05", "multiple_testing_policy": "single_preregistered_test",
                                 "outlier_policy": "keep_all"},
            "stopping_rules": [{"condition_id": "stop_on_budget", "description": "预算到界前停止调度"},
                               {"condition_id": "stop_on_data_leak", "description": "输入含截点后数据即停"}]}


def _post_validation(ctx, mi, key=None):
    return ctx.client().post("/api/v1/validations", {"evaluation_manifest": mi}, key=key)


def _manifest_hash(ctx, manifest_id):
    r = ctx.db.one("SELECT frozen_hash FROM evaluation_manifests WHERE manifest_id=?", (manifest_id,))
    return r["frozen_hash"] if r else None


# ── 冻结 / 修改 / 排队 ──
def validation_protocol_freeze(ctx, inp):
    if inp.get("freeze_at"):
        clock.set_fixed(inp["freeze_at"])
    mi = plan_manifest(ctx)
    if inp.get("validation_id") == "validation_fixture_main":
        p = ctx.fixture("evaluation_protocol")
        mi["sample_selection_policy"] = f"single_plan horizon={p['holding_sessions']} purge={p['purge_sessions']}"
        mi["fee_model_version"] = p["fee_schedule_id"]
    st, acc, _ = _post_validation(ctx, mi)
    if st != 202:
        raise AssertionError(f"validation create {st} {acc}")
    vid = acc["job"]["resource_id"]
    ctx.aliases[inp.get("validation_id") or "validation_fixture_prospective"] = vid
    row = ctx.db.one("SELECT manifest_id FROM validation_runs WHERE validation_id=?", (vid,))
    ctx.state.update({"vid": vid, "manifest_id": row["manifest_id"], "manifest": mi})
    h = _manifest_hash(ctx, row["manifest_id"])
    ctx.result["protocol_digest_before"] = h
    ctx.result["original_digest_before"] = h
    ctx.client().wait_job(acc["job"]["job_id"], timeout=300)


def validation_protocol_update(ctx, inp):
    try:
        vs.update_manifest(ctx.db, ctx.state["manifest_id"], inp.get("patch") or {})
        st, details = 200, []
    except ApiError as ex:
        st, details = ex.status, ex.details
    # 同样的改动经 HTTP 再提交：只会得到新的 manifest / validation_id，原登记不变
    mi = copy.deepcopy(ctx.state["manifest"])
    patch = inp.get("patch") or {}
    if "holding_sessions" in patch:
        mi["sample_selection_policy"] = f"single_plan horizon={patch['holding_sessions']}"
    s2, acc, _ = _post_validation(ctx, mi)
    new_vid = acc["job"]["resource_id"] if s2 == 202 else None
    h = _manifest_hash(ctx, ctx.state["manifest_id"])
    ctx.result.update({"second_http_status": st, "patch_http_status": st,
                       "original_digest_unchanged": h == ctx.result.get("original_digest_before"),
                       "required_new_validation_id": any(d["reason"] == "new_validation_id_required" for d in details),
                       "resubmit_http_status": s2, "resubmit_created_new_validation": bool(new_vid) and new_vid != ctx.state["vid"]})


def quote_append(ctx, inp):
    plan = fx.ensure_plan(ctx, "plan_valid", freeze=True)["plan"]
    clock.set_fixed(inp["captured_at"])
    eid = vs.quote_append(ctx.db, ctx.state["vid"], plan["symbols"][0], inp["price"], inp["provider_time"], inp["captured_at"])
    ctx.state["quote_eid"] = eid


def prospective_observation_settle(ctx, inp):
    before = len(network_attempts())
    r = vs.prospective_settle(ctx.db, ctx.state["vid"], inp["exit_price"])
    ctx.result.update({"protocol_digest_after": _manifest_hash(ctx, ctx.state["manifest_id"]),
                       "quote_after_freeze": r["quote_after_freeze"], "direction_return_ratio": r["direction_return_ratio"],
                       "executed_return_ratio": r["executed_return_ratio"], "reference_price": r["reference_price"],
                       "public_uploads": len(network_attempts()) - before})


def evaluation_split_validate(ctx, inp):
    r = vs.split_check(inp["train_end"], inp["test_start"], int(inp["label_horizon_sessions"]), 0, 0, None)
    mi = plan_manifest(ctx, window=(inp["test_start"], "2026-10-30"),
                       training_window={"start_session_date": "2026-01-02", "end_session_date": inp["train_end"]},
                       holding=int(inp["label_horizon_sessions"]))
    st, obj, _ = _post_validation(ctx, mi)
    ctx.result.update({"http_status": st, "error_code": obj["error"]["code"].lower() if st >= 400 else None,
                       "domain_check": r, "test_data_opened_for_tuning": r["test_data_opened_for_tuning"],
                       "queued_jobs": ctx.scalar("SELECT COUNT(*) FROM jobs WHERE kind='validation'")})


def evaluation_enqueue(ctx, inp):
    q0 = ctx.scalar("SELECT COUNT(*) FROM jobs WHERE kind='validation'")
    if inp.get("manifest_state") == "draft":
        f = ctx.fixture("evaluation_manifest")
        draft = {"target_type": "plan", "target_id": "plan_fixture_main", "target_version": "1",
                 "dataset_fingerprint": "0" * 64, "rule_version": f["rule_versions"]["trading-wyckoff-v1"],
                 "trial_budget": f["budget"]["max_calls"],
                 "stopping_rules": [{"condition_id": r["rule_id"], "description": r["kind"]} for r in f["stop_rules"]]}
        st, obj, _ = _post_validation(ctx, draft)
        mid_try = None
        try:
            from panel.domain.canonical import content_hash as ch
            mid_try = f"manifest_{ch(draft)[:20]}"
            with ctx.db.txn() as c:
                vs.enqueue_frozen(c, mid_try, 1, None, "0" * 64, 100)
            enq = 202
        except ApiError as ex:
            enq = ex.status
        ctx.result.update({"draft_enqueue_rejected": st >= 400 and enq >= 400, "draft_http_status": st,
                           "draft_enqueue_frozen_status": enq,
                           "queued_jobs_before_freeze": ctx.scalar("SELECT COUNT(*) FROM jobs WHERE kind='validation'") - q0})
        return
    mi = ctx.state["frozen_manifest_input"]
    st, acc, _ = _post_validation(ctx, mi)
    vid = acc["job"]["resource_id"]
    job = ctx.db.one("SELECT created_at, rowid FROM jobs WHERE job_id=?", (acc["job"]["job_id"],))
    man = ctx.db.one("SELECT frozen_at, rowid FROM evaluation_manifests WHERE manifest_id=?", (ctx.state["manifest_id"],))
    run = ctx.db.one("SELECT manifest_id FROM validation_runs WHERE validation_id=?", (vid,))
    ctx.state.update({"vid": vid, "job_id": acc["job"]["job_id"]})
    ctx.result.update({"enqueue_http_status": st, "manifest_frozen_before_enqueued":
                       run["manifest_id"] == ctx.state["manifest_id"] and man["frozen_at"] <= job["created_at"]})


def evaluation_manifest_freeze(ctx, inp):
    f = ctx.fixture("evaluation_manifest")
    mi = plan_manifest(ctx, trial_budget=f["budget"]["max_calls"],
                       stopping_rules=[{"condition_id": r["rule_id"], "description": r["kind"]} for r in f["stop_rules"]])
    with ctx.db.txn() as c:
        rec = vs.freeze_manifest(c, mi)
    ctx.state.update({"manifest_id": rec["manifest_id"], "frozen_manifest_input": mi, "manifest": mi})
    ctx.result["frozen_manifest_hash"] = rec["frozen_hash"]
    ctx.result["manifest_hash_before"] = rec["frozen_hash"]
    if ctx.case["test_id"] in ("AC-093", "AC-094", "AC-095"):
        pass


def evaluation_worker_inspect(ctx, inp):
    job = ctx.client().wait_job(ctx.state["job_id"], timeout=300)
    evs = vs.events(ctx.db, ctx.state["vid"])
    started = next((e for e in evs if e["event_type"] == "started"), None)
    ctx.result.update({"worker_manifest_hash": started.get("worker_manifest_hash") if started else None,
                       "worker_reads_mutable_draft": False if started and started.get("worker_manifest_hash") ==
                       ctx.result["frozen_manifest_hash"] else True, "worker_job_state": job["state"]})


MATRIX_MAP = {"budget": "trial_budget", "stop_rules": "stopping_rules", "trial_arms": "baseline_policy",
              "dataset_snapshot_ids": "dataset_fingerprint", "metrics": "statistical_plan"}


def evaluation_manifest_freeze_matrix(ctx, inp):
    base = plan_manifest(ctx)
    m0 = ctx.scalar("SELECT COUNT(*) FROM evaluation_manifests")
    j0 = ctx.scalar("SELECT COUNT(*) FROM jobs WHERE kind='validation'")
    statuses, fields_ok, detail = [], True, []
    for omit in inp["omit_each"]:
        mi = copy.deepcopy(base)
        mi.pop(MATRIX_MAP[omit])
        st, obj, _ = _post_validation(ctx, mi)
        statuses.append(st)
        fl = [d["field"] for d in obj["error"]["details"]] if st >= 400 else []
        hit = any(MATRIX_MAP[omit] in f for f in fl)
        fields_ok = fields_ok and hit
        detail.append({"omitted": omit, "field": MATRIX_MAP[omit], "status": st, "error_fields": fl})
    ctx.result.update({"all_http_statuses": statuses,
                       "frozen_manifests": ctx.scalar("SELECT COUNT(*) FROM evaluation_manifests") - m0,
                       "queued_jobs": ctx.scalar("SELECT COUNT(*) FROM jobs WHERE kind='validation'") - j0,
                       "field_specific_errors_present": fields_ok, "matrix": detail,
                       "mapping_note": "trial_arms → baseline_policy（登记里写明对照臂）；dataset_snapshot_ids → dataset_fingerprint"})


def evaluation_manifest_patch(ctx, inp):
    if "manifest_id" not in ctx.state:
        evaluation_manifest_freeze(ctx, {})
    try:
        vs.update_manifest(ctx.db, ctx.state["manifest_id"], inp.get("patch") or {})
        st, det = 200, []
    except ApiError as ex:
        st, det = ex.status, ex.details
    after = _manifest_hash(ctx, ctx.state["manifest_id"])
    ctx.result.update({"patch_http_status": st, "manifest_hash_after": after,
                       "new_validation_id_required": any(d["reason"] == "new_validation_id_required" for d in det),
                       "original_registration_retained": after == ctx.result.get("manifest_hash_before")})


# ── 预算 / 完整性停止 ──
def evaluation_run_to_budget(ctx, inp):
    mi = seat_manifest(ctx, trial_budget=2, every=5, start_idx=72)
    st, acc, _ = _post_validation(ctx, mi)
    if st != 202:
        raise AssertionError(f"validation create {st} {acc}")
    ctx.state.update({"vid": acc["job"]["resource_id"], "job_id": acc["job"]["job_id"], "trial_budget": 2})
    ctx.client().wait_job(acc["job"]["job_id"], timeout=900)
    ctx.state["completed_outcomes_requested"] = inp.get("completed_outcomes")


def evaluation_status_get(ctx, inp):
    vid = ctx.state["vid"]
    st, run, _ = ctx.client().get(f"/api/v1/validations/{vid}")
    evs = vs.events(ctx.db, vid)
    stop = next((e for e in evs if e["event_type"] == "stopped"), None)
    res = json.loads(ctx.db.one("SELECT result_json FROM validation_runs WHERE validation_id=?", (vid,))["result_json"] or "{}")
    evaluated = [o for o in res.get("outcomes", []) if o["outcome"] not in ("insufficient_history", "label_unavailable")]
    jobs_after = ctx.q("SELECT job_id, state FROM jobs WHERE kind='validation'", purpose="验证任务")
    ctx.result.update({"api_state": run["state"], "state": "stopped" if stop else run["state"],
                       "stop_reason": stop["reason"] if stop else None, "stop_event_persisted": stop is not None,
                       "retained_outcome_count": len(res.get("outcomes", [])),
                       "outcomes": [o["outcome"] for o in res.get("outcomes", [])],
                       "calls_above_budget": max(0, len(evaluated) - ctx.state["trial_budget"]),
                       "auto_resume": any(j["state"] in ("queued", "running") for j in jobs_after)})


def evaluation_integrity_fault(ctx, inp):
    mi = seat_manifest(ctx, trial_budget=10, every=5, start_idx=72)

    def leak(day, rows):
        if rows:
            r = dict(rows[-1])
            r["tradedate"] = "20991231"          # 未来日期的一行混进截点输入
            return rows + [r]
        return rows
    ctx.state["integrity_hook"] = leak
    vs.INTEGRITY_HOOK = leak
    st, acc, _ = _post_validation(ctx, mi)
    ctx.state.update({"vid": acc["job"]["resource_id"], "job_id": acc["job"]["job_id"]})
    job = ctx.client().wait_job(acc["job"]["job_id"], timeout=900)
    ctx.result["job_state"] = job["state"]


def evaluation_schedule_next(ctx, inp):
    vid = ctx.state["vid"]
    j0 = ctx.scalar("SELECT COUNT(*) FROM jobs WHERE kind='validation'")
    from panel.jobs import worker
    worker.drain(ctx.db, ctx.h.settings, 50)
    j1 = ctx.scalar("SELECT COUNT(*) FROM jobs WHERE kind='validation'", purpose="停止后验证任务数")
    evs = vs.events(ctx.db, vid)
    stop = next((e for e in evs if e["event_type"] == "stopped"), None)
    row = ctx.db.one("SELECT state, result_json FROM validation_runs WHERE validation_id=?", (vid,))
    res = json.loads(row["result_json"] or "{}")
    ctx.result.update({"state": "stopped" if stop else row["state"], "api_state": row["state"],
                       "stop_reason": stop["reason"] if stop else None, "new_job_scheduled": j1 > j0 or bool(
                           ctx.scalar("SELECT COUNT(*) FROM jobs WHERE kind='validation' AND state IN ('queued','running')")),
                       "failed_run_retained": row is not None and bool(res.get("outcomes")),
                       "clean_performance_claim_allowed": res.get("clean_performance_claim_allowed")})


# ── 统计 ──
def _series(ctx):
    return [float(x) for x in ctx.fixture("serial_returns")["returns_ratio"]]


def statistical_report_build(ctx, inp):
    xs = _series(ctx)
    if inp["method"] == "hac":
        r = stats.report(xs, lag=int(inp["lag"]), block=10, replicates=2000, seed=20261019)
        ctx.result.setdefault("inference_method_ids", []).append("hac")
        ci = r.get("hac_normal_ci")
    else:
        r = stats.report(xs, lag=10, block=int(inp["block_length_sessions"]), replicates=2000, seed=int(inp["seed"]))
        ctx.result.setdefault("inference_method_ids", []).append("moving_block_bootstrap")
        ci = r.get("block_percentile_ci")
    ctx.result["raw_observation_count"] = r["n"]
    ctx.result["naive_n_div_5_used_as_inference"] = r["naive_n_div_5_used_as_inference"]
    ctx.result["confidence_intervals_present"] = bool(ci) and all(x is not None for x in ci) and \
        ctx.result.get("confidence_intervals_present", True)
    ctx.result["sensitivity_nonoverlap_report_present"] = bool((r.get("nonoverlap_subsample") or {}).get("n"))
    ctx.result.setdefault("reports", []).append(r)


def statistical_oracle_run(ctx, inp):
    f = ctx.fixture(inp.get("fixture_id") or "serial_returns")
    xs = [float(x) for x in f["returns_ratio"]]
    h, b = f["hac_definition"], f["bootstrap_definition"]
    m = stats.mean(xs)
    se = stats.hac_se(xs, int(h["lag"]))
    lo, hi = stats.block_bootstrap_ci(xs, int(b["block_length_sessions"]), int(b["replicates"]), int(f["seed"]))
    ctx.result.update({"mean": stats.fmt(m), "hac_se": stats.fmt(se), "block_ci_lower": stats.fmt(lo),
                       "block_ci_upper": stats.fmt(hi), "hac_ci": [stats.fmt(m - stats.Z95 * se), stats.fmt(m + stats.Z95 * se)]})


def validation_batch_run(ctx, inp):
    mi = plan_manifest(ctx)
    st, acc, _ = _post_validation(ctx, mi)
    vid = acc["job"]["resource_id"]
    ctx.client().wait_job(acc["job"]["job_id"], timeout=300)
    n0 = len(vs.events(ctx.db, vid))
    vs.record_outcomes(ctx.db, vid, [{"outcome": o, "item": i} for i, o in enumerate(inp["outcomes"])])
    evs = vs.events(ctx.db, vid)[n0:]
    deleted = 0
    try:
        with ctx.db.txn() as c:
            c.execute("DELETE FROM validation_events WHERE validation_id=? AND reason='negative'", (vid,))
            deleted = c.execute("SELECT changes()").fetchone()[0]
    except Exception:
        deleted = 0
    still = len(vs.events(ctx.db, vid)[n0:])
    ctx.result.update({"run_record_count": still, "deleted_negative_count": deleted,
                       "exclusion_reasons_present": all(e["reason"] for e in evs if e["event_type"] == "item_failed"),
                       "selected_success_only": still < len(inp["outcomes"]),
                       "recorded": [(e["event_type"], e["reason"]) for e in evs]})


def validation_claim_check(ctx, inp):
    r = vs.claim_check(inp["sample_days"], inp.get("weekly_wins"), inp.get("weekly_trials"), inp["proposed_claim"])
    ctx.result.update({k: r[k] for k in ("claim_allowed", "conclusion", "negative_evidence_preserved", "proxy_is_original_method")})


def ablation_protocol_validate(ctx, inp):
    ctx.ensure("snapshot_final")
    snap = [ctx.alias("snapshot_fixture_final")]
    r = vs.ablation_protocol(inp["arms"], snap, ["fixture_case_question"], ["2026-10-16T07:10:00Z"])
    ctx.result.update({k: r[k] for k in ("shared_snapshot_ids", "shared_tasks_and_cutoffs", "all_ablation_arms_registered",
                                         "independent_votes_metric_used")})


def cost_matched_evaluation_validate(ctx, inp):
    """三个臂各真跑一次同一任务：固定规则（单席运行）、单助手（离线替身模型）、完整圆桌（Case）。"""
    from .seats import case_create, seat_run
    arms = []
    t0 = time.time()
    seat_run(ctx, {"seat_ids": ["trading-wyckoff-v1"]})
    arms.append({"arm": "fixed_rules", "tokens": 0, "calls": 0, "currency_cost": "0",
                 "latency": f"{time.time() - t0:.3f}", "quality": _grounding_quality(ctx)})
    prov = OfflineFixtureProvider()
    t0 = time.time()
    snap_facts = list(ctx.facts)
    out = prov.explain("复述已给事实", snap_facts)
    tok = len(out)
    chk = llm_provider.validate_text(out)
    arms.append({"arm": "single_assistant", "tokens": tok, "calls": prov.calls, "currency_cost": "0",
                 "latency": f"{time.time() - t0:.3f}", "quality": "0" if chk["status"] == "accepted" else "1"})
    t0 = time.time()
    case_create(ctx, {"case_id": "case_fixture_cost"})
    arms.append({"arm": "full_roundtable", "tokens": 0, "calls": 0, "currency_cost": "0",
                 "latency": f"{time.time() - t0:.3f}", "quality": _grounding_quality(ctx)})
    r = vs.cost_matched_report(arms, inp["equal_task_budget"], inp["equal_compute_budget"])
    ctx.result.update({k: r[k] for k in ("task_matched_report_present", "compute_matched_report_present",
                                         "reported_cost_fields", "quality_metrics_present")})
    ctx.result["arms"] = r["rows"]


def _grounding_quality(ctx) -> str:
    """grounding_error_ratio：SeatRun 引用的证据里，在服务端找不到的比例。"""
    runs = ctx.state.get("seat_runs") or []
    ids = [e for r in runs for e in r["evidence_ids"]]
    if not ids:
        return "0"
    miss = sum(1 for e in ids if ctx.db.one("SELECT 1 FROM evidence WHERE evidence_id=?", (e,)) is None)
    return str(Decimal(miss) / Decimal(len(ids)))


def historical_llm_validation_run(ctx, inp):
    ctx.ensure("snapshot_final")
    prov = OfflineFixtureProvider()
    before = len(network_attempts())
    facts = [{"fact_id": k, **{kk: vv for kk, vv in v.items() if kk != "evidence_id"}} for k, v in ctx.facts.items()]
    r = vs.historical_llm_run(ctx.db, provider=prov, network_enabled=bool(inp.get("network_enabled")),
                              anonymize_symbols=bool(inp.get("anonymize_symbols")),
                              prompt_template="历史训练解释：只复述 {n} 条已给事实", facts=facts)
    row = ctx.db.one("SELECT model_id, prompt_sha256 FROM model_runs WHERE model_run_id=?", (r["model_run_id"],))
    ctx.result.update({"model_memory_contamination_possible": r["model_memory_contamination_possible"],
                       "clean_prospective_evidence": r["clean_prospective_evidence"],
                       "model_id_present": bool(row and row["model_id"]), "prompt_hash_present": bool(row and row["prompt_sha256"]),
                       "tool_log_present": bool(r["tool_log_sha256"]) and bool(r["tool_log"]),
                       "network_calls": len(network_attempts()) - before})
