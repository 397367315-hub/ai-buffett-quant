"""席位 / Case / 模型输出校验 driver（SEAT / CASE）。"""
from __future__ import annotations

import json

from panel.adapters import llm_provider
from panel.config import SKILLS_DIR
from panel.data.evidence_store import EvidenceStore
from panel.domain import clock, seat_registry as sr
from panel.domain.canonical import canonical_bytes, content_hash, new_id
from panel.services import case_orchestrator, seat_runner, snapshot_service
from panel.web.handlers.seats import ensure_registry

from .. import fixtures as fx

RULE_ALIASES = {"intraday_support": "minute_bars", "level2_intent": "level2"}   # 规格里的通用条件名 → 缺失输入


def _track(ctx, kind: str, obj_id: str, snapshot_id: str | None):
    used = ctx.state.setdefault("used", [])
    used.append({"kind": kind, "id": obj_id, "snapshot_id": snapshot_id})
    sids = sorted({u["snapshot_id"] for u in used if u["snapshot_id"]})
    ctx.result["used_snapshot_ids"] = [ctx.unalias(s) for s in sids]
    first = used[0]["snapshot_id"]
    ctx.result["mixed_snapshot_warnings"] = [f"{u['kind']}:{u['id']} 用了 {u['snapshot_id']}（不是 {first}）"
                                             for u in used if u["snapshot_id"] and u["snapshot_id"] != first]


def _fact_values(ctx, snap: dict):
    vals = ctx.result.setdefault("fact_values", {})
    for fid, info in ctx.facts.items():
        ev = next((e for e in snap["evidence"] if isinstance(e.get("value"), dict) and e["value"].get("fact_id") == fid), None)
        if ev is None:
            continue
        v = ev["value"]
        val = v.get("price") or v.get("money_minor") or v.get("value") if isinstance(v, dict) else v
        bucket = vals.setdefault(fid, [])
        if val is not None and val not in bucket:
            bucket.append(val)
        if fid == "f_close_A":
            inst = next((i for i in snap["instruments"] if i["symbol"] == info.get("symbol")), None)
            bar = next((b for b in snap["daily_bars"] if b["symbol"] == info.get("symbol")
                        and b["session_date"] == snap["metadata"]["session_date"]), None)
            for x in (inst and inst["last_price_decimal"], bar and bar["close_decimal"]):
                if x is not None and x not in bucket:
                    bucket.append(x)


def snapshot_get(ctx, inp):
    if ctx.state.get("p1_counts") is None:
        ctx.ensure("snapshot_final")
    elif "snapshot_final" not in ctx.loaded:
        ctx.ensure("snapshot_final")
    sid = ctx.alias(inp.get("snapshot_id") or "snapshot_fixture_final")
    st, snap, _ = ctx.client().get(f"/api/v1/snapshots/{sid}")
    row = ctx.db.one("SELECT canonical_payload FROM snapshots WHERE snapshot_id=?", (sid,))
    ctx.result["snapshot_get_status"] = st
    ctx.result["snapshot_read_hash_equal"] = st == 200 and content_hash(snap) == content_hash(json.loads(row["canonical_payload"]))
    if st == 200:
        _track(ctx, "snapshot", sid, sid)
        _fact_values(ctx, snap)
    if ctx.state.get("p1_counts") is not None:
        c = ctx.state["p1_counts"]
        ctx.result["p1_service_calls"] = c["p1"]
        ctx.result["ledger_import_attempted"] = c["ledger_import"] > 0
        ctx.result["p0_operations_success"] = bool(ctx.state.get("p0_ok", True)) and st == 200


def seat_run(ctx, inp):
    ctx.ensure("snapshot_final")
    sid = ctx.alias(inp.get("snapshot_id") or "snapshot_fixture_final")
    runs = []
    for seat in inp["seat_ids"]:
        body = {"seat_id": seat, "snapshot_id": sid, "symbols": [fx.SYM_A], "cycle_id": new_id("cycle"),
                "llm_mode": "disabled", "provider_id": None, "model_id": None, "user_context_id": None, "replay_id": None}
        st, acc, _ = fx.call(ctx, "POST", "/api/v1/seat-runs", body, want=202)
        job = fx.wait(ctx, acc)
        st, run, _ = fx.call(ctx, "GET", f"/api/v1/seat-runs/{acc['job']['resource_id']}")
        runs.append(run)
        _track(ctx, "seat_run", run["seat_run_id"], run["snapshot_id"])
    ctx.result["seat_runs"] = [{"seat_id": r["seat_id"], "run_state": r["run_state"], "applicability": r["applicability"],
                                "verdict": r["verdict"], "missing_inputs": r["missing_inputs"]} for r in runs]
    if inp.get("requested_rule_ids"):
        states = {}
        for rid in inp["requested_rule_ids"]:
            need = RULE_ALIASES[rid]
            res = [c["result"] for r in runs for c in r["condition_results"] if need in c["missing_inputs"]]
            states[rid] = "unknown" if (not res or all(x == "unknown" for x in res)) else \
                ("met" if all(x == "met" for x in res) else "failed")
        ctx.result["rule_states"] = states
        ctx.result["confirmed_daily_proxy_for_bc"] = any(
            c["result"] in ("met", "failed") for r in runs for c in r["condition_results"]
            if set(c["missing_inputs"]) & {"minute_bars", "level2"})
        ctx.result["execution_eligibility"] = any(r["verdict"] == "conditions_met" for r in runs) and \
            all(v == "met" for v in states.values())
    ctx.state["seat_runs"] = runs


def case_create(ctx, inp):
    ctx.ensure("snapshot_final")
    sid = ctx.alias(inp.get("snapshot_id") or "snapshot_fixture_final")
    body = fx.case_body(sid, "fixture Case", [fx.SYM_A],
                        seats=[("trading-wyckoff-v1", "primary"), ("trading-wildman-v2", "validator"),
                               ("trading-tuixue-v1", "discipline")])
    st, acc, _ = fx.call(ctx, "POST", "/api/v1/cases", body, want=202)
    job = fx.wait(ctx, acc)
    cid = acc["job"]["resource_id"]
    ctx.aliases[inp.get("case_id") or "case_fixture_main"] = cid
    st, case, _ = fx.call(ctx, "GET", f"/api/v1/cases/{cid}")
    _track(ctx, "case", cid, case["snapshot_id"])
    for rid in case["seat_run_ids"]:
        st, r, _ = fx.call(ctx, "GET", f"/api/v1/seat-runs/{rid}")
        _track(ctx, "case_seat_run", rid, r["snapshot_id"])
    ctx.result["case_state"] = case["state"]


def case_supersede(ctx, inp):
    old = fx.ensure_case(ctx)
    plan = fx.ensure_plan(ctx, "plan_valid")
    st, old_case, _ = fx.call(ctx, "GET", f"/api/v1/cases/{old}")
    ctx.state["old_case_hash"] = content_hash(old_case)
    ctx.state["old_plan_case"] = plan["plan"]["case_id"]
    body = fx.case_body(ctx.alias("snapshot_fixture_final"), "论点变更后的新 Case", [fx.SYM_A],
                        supersedes=ctx.alias(inp["supersedes_case_id"]))
    st, acc, _ = fx.call(ctx, "POST", "/api/v1/cases", body, want=202)
    fx.wait(ctx, acc)
    ctx.aliases[inp["case_id"]] = acc["job"]["resource_id"]


def case_history_get(ctx, inp):
    new = ctx.alias("case_fixture_next")
    old = ctx.alias("case_fixture_main")
    st1, n, _ = fx.call(ctx, "GET", f"/api/v1/cases/{new}")
    st2, o, _ = fx.call(ctx, "GET", f"/api/v1/cases/{old}")
    plan_id = ctx.alias("plan_fixture_main")
    st3, p, _ = fx.call(ctx, "GET", f"/api/v1/plans/{plan_id}")
    ctx.result.update({"new_case": {"case_id": ctx.unalias(n["case_id"]), "supersedes_case_id": ctx.unalias(n["supersedes_case_id"])},
                       "old_case_hash_unchanged": content_hash(o) == ctx.state["old_case_hash"],
                       "both_cases_visible": st1 == 200 and st2 == 200,
                       "old_plan_relinked_silently": p["case_id"] != ctx.state["old_plan_case"]})


def seat_registry_import(ctx, inp):
    st, reg, _ = fx.call(ctx, "GET", "/api/v1/seats/registry")
    by_legacy = {s["legacy_seat_id"]: s["seat_id"] for s in reg["seats"]}
    ids = [s["seat_id"] for s in reg["seats"]]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    shas = {}
    collisions = []
    for s in reg["seats"]:
        k = (s["skill_sha256"], s["rule_version"])
        if k in shas:
            collisions.append([shas[k], s["seat_id"]])
        shas[k] = s["seat_id"]
    ctx.result.update({"id_map": {l: by_legacy.get(l) for l in inp["legacy_ids"]}, "seat_count": len(ids),
                       "duplicate_canonical_ids": dup, "version_collisions": collisions,
                       "registry_version": reg["registry_version"],
                       "canonical_ids_match": [by_legacy.get(l) for l in inp["legacy_ids"]] == inp["canonical_ids"]})


# ── 席位适配器：原始输出 → SeatRun 三维状态（AC-021） ──
UI_LABELS_NOTE = "标签由前端 src/lib/labels.ts 的 seatStatusLabel() 计算（node 执行）"


def _adapter_raw(run: dict) -> tuple[dict | None, list[str]]:
    reason = run.get("reason")
    if reason == "minute_level2_missing":
        return {"stage": {"stage": "未判定", "undetermined": True, "evidence": ["关键判据需分时承接与盘口（B/C 级）"]},
                "roles": [], "unknowns": ["minute_bars", "level2"]}, []
    if reason == "sos_low_breached":
        return {"gate": {"open": True, "state": "SOS 成立", "reason": "合成 fixture：市场闸门开"},
                "candidates": [], "rejected_sample": [{"symbol": "600001", "why": "跌破 SOS 低点（sos_low_breached）"}]}, []
    return None, []


def seat_adapter_run(ctx, inp):
    f = ctx.fixture(inp.get("fixture_id") or "seat_statuses")
    ctx.ensure("snapshot_final")
    db = ctx.db
    snap = snapshot_service.get(db, ctx.alias("snapshot_fixture_final"), None)
    reg = ensure_registry(db)
    store = EvidenceStore(ctx.h.settings.evidence_dir)
    out, triplets = {}, []
    from panel.jobs import queue
    for r in f["runs"]:
        seat = r["seat_id"]
        horizon = "short" if r.get("requested_horizon") == "intraday" else "multi"
        srid = new_id("seatrun")
        with db.txn() as c:
            job = queue.enqueue(c, kind="seat", payload={"seat_run_id": srid}, request_sha256="0" * 64,
                                resource_type="seat_run", resource_id=srid, replay_id=None, max_waiting=1000)
            seat_runner.create_seat_run(c, seat_id=seat, snapshot=snap, case_id=None, round_="initial",
                                        cycle_id=new_id("cycle"), registry=reg, job_id=job["job_id"], replay_id=None,
                                        symbols=[fx.SYM_A], horizon=horizon, llm_mode="disabled", seat_run_id=srid)
            queue.finish(c, job["job_id"], "succeeded", resource_id=srid, resource_type="seat_run")
        raw, missing = _adapter_raw(r)
        if raw is None:
            obj = seat_runner.execute(db, srid)       # 真实执行路径（周期不适用 → skipped）
        else:
            full = json.loads(db.one("SELECT canonical_payload FROM seat_runs WHERE seat_run_id=?", (srid,))["canonical_payload"])
            sctx = full.pop("_ctx")
            sha = store.put(canonical_bytes(raw))
            obj = seat_runner.finalize_from_raw(db, full, sctx, snap, raw, sha, {"evidence": {}}, missing, None)
        status = "skip" if obj["run_state"] == "skipped" or obj["applicability"] == "inapplicable" else \
            ("negative" if obj["verdict"] == "conditions_failed" else
             ("unknown" if obj["verdict"] == "unknown" else obj["verdict"]))
        out[seat.replace("-", "_")] = status
        triplets.append({"run_state": obj["run_state"], "applicability": obj["applicability"], "verdict": obj["verdict"]})
    from ..browser.node_eval import seat_labels
    labels = seat_labels(triplets)
    ctx.result.update({"status_by_seat": out, "triplets": triplets, "ui_labels": labels,
                       "distinct_ui_labels": len(set(labels)), "labels_note": UI_LABELS_NOTE})


def roundtable_summarize(ctx, inp):
    f = ctx.fixture(inp.get("fixture_id") or "duplicate_evidence")
    ctx.ensure("snapshot_final")
    ev = {fid: info["evidence_id"] for fid, info in ctx.facts.items()}
    runs = []
    roles = []
    for i, cl in enumerate(f["claims"]):
        role = "primary" if i == 0 else ("validator" if i == 1 else "context")
        roles.append({"seat_id": cl["seat_id"], "role": role})
        runs.append({"seat_run_id": f"seatrun_{cl['interpretation_id']}", "seat_id": cl["seat_id"], "run_state": "succeeded",
                     "applicability": "applicable", "applicability_reason": "fixture", "verdict": "watch",
                     "candidate_count": None, "candidates": [], "evidence_ids": [ev[x] for x in cl["fact_ids"]],
                     "condition_results": [{"condition_id": f"c_{cl['interpretation_id']}", "description":
                                            f"{cl['seat_id']} 对 {','.join(cl['fact_ids'])} 的解读",
                                            "result": "met", "evidence_ids": [ev[x] for x in cl["fact_ids"]],
                                            "missing_inputs": []}],
                     "missing_inputs": [], "explanation": cl["interpretation_id"], "counter_evidence": []})
    obj = {"primary_strategy_id": f["claims"][0]["seat_id"], "role_assignments": roles, "llm_mode": "disabled"}
    final, state = case_orchestrator._synthesize(obj, runs)
    inv = {v: k for k, v in ev.items()}
    text = " ".join([final["summary"], *final["agreements"], *final["disagreements"]])
    ctx.result.update({"unique_fact_ids": sorted({inv.get(e, e) for e in final["evidence_ids"]}),
                       "interpretation_count": len(runs), "independent_vote_count": final.get("independent_vote_count"),
                       "generated_win_probability": final.get("win_probability"), "ui_text": text, "final": final})


def llm_response_validate(ctx, inp):
    ctx.ensure("snapshot_final")
    ctx.ensure("replay_future")       # 服务器上存在的「截点之后」事实（用来识别未来引用）
    plans_before = ctx.scalar("SELECT COUNT(*) FROM plan_versions", purpose="校验前计划数")
    if inp.get("text") is not None:
        r = llm_provider.validate_text(inp["text"])
        ctx.result.update({"status": r["status"], "policy_errors": r["policy_errors"],
                           "execution_eligibility": r["execution_eligibility"]})
    else:
        sid = ctx.alias(inp.get("snapshot_id") or "snapshot_fixture_final")
        snap = snapshot_service.get(ctx.db, sid, None)
        extra = [json.loads(r["canonical_payload"]) for r in ctx.db.query(
            "SELECT canonical_payload FROM evidence WHERE snapshot_id IS NULL AND json_extract(canonical_payload, '$.field_path') LIKE 'facts.%'")]
        facts = llm_provider.facts_from_snapshot(snap, extra)
        r = llm_provider.validate_claims(facts, inp["claims"], cutoff_at=snap["metadata"]["captured_at"])
        ctx.result.update({"status": r["status"], "unknown_fact_ids": r["unknown_fact_ids"],
                           "future_fact_ids": r["future_fact_ids"], "error_codes": r["error_codes"],
                           "execution_eligibility": False if r["status"] != "accepted" else None})
    plans_after = ctx.scalar("SELECT COUNT(*) FROM plan_versions", purpose="校验后计划数")
    ctx.result["committed_plan_count"] = plans_after - plans_before
