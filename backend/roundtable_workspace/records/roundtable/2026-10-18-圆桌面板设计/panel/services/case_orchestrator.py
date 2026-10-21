"""圆桌编排（CASE-001 / SEAT-005）：独立首轮 → 有限质询 → 综合；不投票、不平均价位。

状态：queued → preparing → initial_round → challenge_round → synthesizing → succeeded/partial/failed
- 首轮每席独立运行（只读同一快照、本席技能与问题），全部终态后才进入质询；
- 质询（仅规则模式）：同一时间尺度上结论冲突的席位互相记录结构化反证，规则结论不因此改变；
- 综合：共同事实（同一事实只算一份）、方法分歧（保留时间尺度）、主方法下的确认/失效条件、缺口；
  不计算赞成票、平均分或「总体看多」；
- Case 编排是状态驱动的：首轮派发子任务后释放工作槽（DETACHED），子任务完成时推进，避免 4 个 Case 占满槽位死锁；
- 终态 Case 不可改写（触发器）；重跑新建 case_id 并记录 supersedes_case_id。
"""
from __future__ import annotations

import json

from ..adapters import llm_provider
from ..config import get_settings
from ..domain import clock, seat_registry as sr
from ..domain.canonical import canonical_json, new_id
from ..jobs import queue, worker
from ..jobs.worker import DETACHED, JobFailed
from ..web.errors import ApiError
from . import seat_runner, snapshot_service

TERMINAL = ("succeeded", "partial", "failed")


def validate_request(db, body: dict, replay_id, registry: dict) -> dict:
    seats = {s["seat_id"]: s for s in registry["seats"]}
    errs = []
    for sid in body["seat_ids"]:
        if sid not in seats:
            errs.append({"field": "seat_ids", "reason": f"unknown seat {sid}"})
    roles = body["role_assignments"]
    role_seats = [r["seat_id"] for r in roles]
    if len(set(role_seats)) != len(role_seats):
        errs.append({"field": "role_assignments", "reason": "duplicate seat in role_assignments"})
    if set(role_seats) != set(body["seat_ids"]):
        errs.append({"field": "role_assignments", "reason": "every seat in seat_ids needs exactly one role"})
    primaries = [r["seat_id"] for r in roles if r["role"] == "primary"]
    if len(primaries) != 1:
        errs.append({"field": "role_assignments", "reason": f"exactly one primary required (got {len(primaries)})"})
    elif primaries[0] != body["primary_strategy_id"]:
        errs.append({"field": "primary_strategy_id", "reason": "must equal the seat with role=primary"})
    if body["primary_strategy_id"] not in body["seat_ids"]:
        errs.append({"field": "primary_strategy_id", "reason": "primary must be one of seat_ids"})
    if errs:
        raise ApiError(422, "CASE_ROLES_INVALID", "席位与角色不一致", errs)
    snap = snapshot_service.get(db, body["snapshot_id"], replay_id)
    if snap is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "快照不存在或在当前上下文中不可见",
                       [{"field": "snapshot_id", "reason": "not visible"}])
    have = {i["symbol"] for i in snap["instruments"]}
    bad = [s for s in body["symbols"] if s not in have]
    if bad:
        raise ApiError(422, "SYMBOL_NOT_IN_SNAPSHOT", f"标的不在该快照中：{', '.join(bad[:5])}",
                       [{"field": "symbols", "reason": f"{s} not in snapshot"} for s in bad[:10]])
    if body["supersedes_case_id"]:
        prev = db.one("SELECT state, replay_id FROM cases WHERE case_id=?", (body["supersedes_case_id"],))
        if prev is None or prev["replay_id"] != replay_id:
            raise ApiError(404, "RESOURCE_NOT_VISIBLE", "被替代的 Case 不存在或不可见",
                           [{"field": "supersedes_case_id", "reason": "not visible"}])
    if body["llm_mode"] == "required" or body["llm_mode"] == "optional":
        try:
            _, note = llm_provider.resolve(body["llm_mode"], body["provider_id"], body["model_id"],
                                           get_settings().runtime_dir)
        except llm_provider.ProviderNotConfigured as ex:
            raise ApiError(422, "LLM_PROVIDER_NOT_CONFIGURED", f"llm_mode=required 但没有可用的模型：{ex}",
                           [{"field": "llm_mode", "reason": "required provider not configured"}])
    return snap


def create(c, body: dict, *, replay_id, request_sha256: str, registry: dict, max_waiting: int) -> tuple:
    case_id = new_id("case")
    cycle_id = new_id("cycle")
    job = queue.enqueue(c, kind="case", payload={"case_id": case_id}, request_sha256=request_sha256,
                        resource_type="case", resource_id=case_id, replay_id=replay_id, max_waiting=max_waiting,
                        total_units=len(body["seat_ids"]) + 2)
    now = clock.utc_iso()
    obj = {"case_id": case_id, "supersedes_case_id": body["supersedes_case_id"], "job_id": job["job_id"],
           "cycle_id": cycle_id, "snapshot_id": body["snapshot_id"], "title": body["title"],
           "question": body["question"], "symbols": body["symbols"], "horizon": body["horizon"],
           "seat_ids": body["seat_ids"], "primary_strategy_id": body["primary_strategy_id"],
           "role_assignments": body["role_assignments"], "llm_mode": body["llm_mode"],
           "provider_id": body["provider_id"], "model_id": body["model_id"], "state": "queued",
           "seat_run_ids": [], "final_result": None, "created_at": now, "finished_at": None, "replay_id": replay_id}
    c.execute("""INSERT INTO cases(case_id, job_id, cycle_id, snapshot_id, registry_version, primary_strategy_id,
                 supersedes_case_id, state, replay_id, created_at, finished_at, request_json, final_json, canonical_payload)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (case_id, job["job_id"], cycle_id, body["snapshot_id"], registry["registry_version"],
               body["primary_strategy_id"], body["supersedes_case_id"], "queued", replay_id, now, None,
               canonical_json(body), None, canonical_json(obj)))
    return job, obj


def load(db, case_id: str, replay_id=...) -> dict | None:
    r = db.one("SELECT canonical_payload, replay_id FROM cases WHERE case_id=?", (case_id,))
    if r is None or (replay_id is not ... and r["replay_id"] != replay_id):
        return None
    return json.loads(r["canonical_payload"])


def _save(c, obj: dict) -> None:
    c.execute("UPDATE cases SET state=?, finished_at=?, final_json=?, canonical_payload=? WHERE case_id=?",
              (obj["state"], obj["finished_at"], canonical_json(obj["final_result"]) if obj["final_result"] else None,
               canonical_json(obj), obj["case_id"]))


@worker.register("case")
def run_job(ctx: worker.JobContext):
    db = ctx.db
    case_id = ctx.payload["case_id"]
    obj = load(db, case_id)
    if obj is None:
        raise JobFailed("CASE_MISSING", "Case 不存在")
    if obj["state"] != "queued":
        return DETACHED
    snap = snapshot_service.get(db, obj["snapshot_id"], obj["replay_id"])
    with db.txn() as c:
        obj["state"] = "preparing"
        _save(c, obj)
    if snap is None:
        _finish(db, obj, "failed", note="冻结快照不可用，不合成任何结论")
        raise JobFailed("SNAPSHOT_UNAVAILABLE", "冻结快照不可用")
    from ..web.handlers.seats import registry_by_version
    reg_row = db.one("SELECT registry_version FROM cases WHERE case_id=?", (case_id,))
    registry = registry_by_version(db, reg_row["registry_version"])
    with db.txn() as c:
        ids = []
        for sid in obj["seat_ids"]:
            srid = new_id("seatrun")
            child = queue.enqueue(c, kind="seat", payload={"seat_run_id": srid}, request_sha256=ctx.row["request_sha256"],
                                  resource_type="seat_run", resource_id=srid, replay_id=obj["replay_id"],
                                  max_waiting=10_000, parent_job_id=ctx.job_id)
            seat_runner.create_seat_run(c, seat_id=sid, snapshot=snap, case_id=case_id, round_="initial",
                                        cycle_id=obj["cycle_id"], registry=registry, job_id=child["job_id"],
                                        replay_id=obj["replay_id"], symbols=obj["symbols"], horizon=obj["horizon"],
                                        llm_mode=obj["llm_mode"], seat_run_id=srid)
            ids.append(srid)
        obj["seat_run_ids"] = ids
        obj["state"] = "initial_round"
        _save(c, obj)
        c.execute("UPDATE jobs SET completed_units=1, phase='initial_round' WHERE job_id=?", (ctx.job_id,))
    return DETACHED


def on_seat_done(db, case_id: str) -> None:
    with db.txn() as c:
        r = c.execute("SELECT canonical_payload FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if r is None:
            return
        obj = json.loads(r["canonical_payload"])
        if obj["state"] != "initial_round":
            return
        runs = [seat_runner.public(json.loads(x["canonical_payload"])) for x in c.execute(
            "SELECT canonical_payload FROM seat_runs WHERE case_id=? AND round='initial'", (case_id,))]
        done = sum(1 for x in runs if x["run_state"] in ("succeeded", "partial", "failed", "skipped"))
        c.execute("UPDATE jobs SET completed_units=? WHERE job_id=?", (1 + done, obj["job_id"]))
        if done < len(obj["seat_ids"]):
            return
        obj["state"] = "challenge_round"
        _save(c, obj)
        challenge_ids = _challenge(c, obj, runs)
        obj["seat_run_ids"] = obj["seat_run_ids"] + challenge_ids
        obj["state"] = "synthesizing"
        _save(c, obj)
        ev_ids = sorted({e for r in runs for e in r["evidence_ids"]})
        labels = {}
        if ev_ids:
            for row in c.execute("SELECT evidence_id, canonical_payload FROM evidence WHERE evidence_id IN (%s)"
                                 % ",".join("?" * len(ev_ids)), ev_ids).fetchall():
                labels[row["evidence_id"]] = evidence_label(json.loads(row["canonical_payload"]))
        final, state = _synthesize(obj, runs, labels)
        obj["final_result"] = final
        obj["state"] = state
        obj["finished_at"] = clock.utc_iso()
        _save(c, obj)
        jstate = "failed" if state == "failed" else ("partial" if state == "partial" else "succeeded")
        c.execute("UPDATE jobs SET completed_units=total_units WHERE job_id=?", (obj["job_id"],))
        queue.finish(c, obj["job_id"], jstate, resource_id=case_id, resource_type="case",
                     error=None if jstate != "failed" else queue.error_obj("PRIMARY_SEAT_FAILED",
                                                                            "主方法席位运行失败或快照不可用", obj["job_id"]))


def _finish(db, obj: dict, state: str, note: str) -> None:
    with db.txn() as c:
        obj["state"] = state
        obj["finished_at"] = clock.utc_iso()
        obj["final_result"] = {"summary": note, "agreements": [], "disagreements": [], "confirmation_conditions": [],
                               "invalidation_conditions": [], "missing_inputs": ["market_snapshot"], "evidence_ids": [],
                               "executed_return_ratio": None}
        _save(c, obj)


_POSITIVE = ("conditions_met", "watch")


def _challenge(c, obj: dict, runs: list[dict]) -> list[str]:
    """同一时间尺度上方向相反的两席，互相记录结构化反证（规则结论不变）。"""
    roles = {r["seat_id"]: r["role"] for r in obj["role_assignments"]}
    ids = []
    for a in runs:
        opp = [b for b in runs if b["seat_run_id"] != a["seat_run_id"] and b["applicability"] == "applicable"
               and a["applicability"] == "applicable"
               and ((a["verdict"] in _POSITIVE and b["verdict"] == "conditions_failed")
                    or (a["verdict"] == "conditions_failed" and b["verdict"] in _POSITIVE))]
        if not opp:
            continue
        ch = dict(a)
        ch["seat_run_id"] = new_id("seatrun")
        ch["round"] = "challenge"
        ch["challenged_seat_run_ids"] = [b["seat_run_id"] for b in opp]
        ch["counter_evidence"] = [f"{sr.SEAT_NAMES[b['seat_id']]}（{ROLE_ZH.get(roles.get(b['seat_id']), roles.get(b['seat_id']))}）：" +
                                  "；".join(x["description"] for x in b["condition_results"]
                                           if x["result"] in ("failed", "met"))[:300] for b in opp]
        ch["explanation"] = (f"质询（仅规则模式）：与 {', '.join(sr.SEAT_NAMES[b['seat_id']] for b in opp)} 结论相反；"
                             "规则结论不因质询改变，分歧原样保留给主持汇总")
        ch["created_at"] = ch["started_at"] = ch["finished_at"] = clock.utc_iso()
        c.execute("""INSERT INTO seat_runs(seat_run_id, job_id, case_id, seat_id, snapshot_id, registry_version, round, attempt,
                     run_state, applicability, verdict, candidate_count, replay_id, created_at, finished_at, fingerprints_json,
                     error_json, canonical_payload) VALUES(?,?,?,?,?,(SELECT registry_version FROM cases WHERE case_id=?),
                     'challenge',1,?,?,?,?,?,?,?,?,NULL,?)""",
                  (ch["seat_run_id"], obj["job_id"], obj["case_id"], ch["seat_id"], ch["snapshot_id"], obj["case_id"],
                   ch["run_state"], ch["applicability"], ch["verdict"], ch["candidate_count"], obj["replay_id"],
                   ch["created_at"], ch["finished_at"], canonical_json(ch["fingerprints"]), canonical_json(ch)))
        ids.append(ch["seat_run_id"])
    return ids


ROLE_ZH = {"primary": "主方法", "validator": "反证", "discipline": "纪律", "context": "环境背景"}
_PATH_LABEL = [("daily_bars[*]", "当日日线"), ("seat_inputs.daily_history[", "历史日线"), ("seat_inputs.limit_pool", "涨停池"),
               ("seat_inputs.industry_map", "行业分类"), ("seat_inputs.valuation", "估值"), ("seat_inputs.limit_events", "涨停异动事件"),
               ("seat_inputs.dividend_years_current", "连续分红年数"), ("instruments[*]", "证券身份"), ("metrics.", "市场指标")]


def evidence_label(e: dict) -> str:
    fp = e.get("field_path") or ""
    for k, v in _PATH_LABEL:
        if fp.startswith(k):
            day = fp[len(k):-1] if k.endswith("[") else ""
            return f"{v}{(' ' + day) if day else ''}（{e['source_ref']['source_id']}）"
    return f"{fp or e['evidence_id']}（{e['source_ref']['source_id']}）"


def _synthesize(obj: dict, runs: list[dict], labels: dict[str, str] | None = None) -> tuple[dict, str]:
    labels = labels or {}
    raw_roles = {r["seat_id"]: r["role"] for r in obj["role_assignments"]}
    roles = {k: ROLE_ZH.get(v, v) for k, v in raw_roles.items()}
    by = {r["seat_id"]: r for r in runs}
    primary = by.get(obj["primary_strategy_id"])
    names = sr.SEAT_NAMES
    ev_count: dict[str, list[str]] = {}
    for r in runs:
        for e in r["evidence_ids"]:
            ev_count.setdefault(e, []).append(r["seat_id"])
    shared = {e: s for e, s in ev_count.items() if len(s) >= 2}
    agreements = [f"共同事实「{labels.get(e, e)}」被 {len(s)} 席引用（{'、'.join(names[x] for x in s)}）：只算一份证据，不是 {len(s)} 票"
                  for e, s in sorted(shared.items())][:10]
    disagreements = []
    for i, a in enumerate(runs):
        for b in runs[i + 1:]:
            if a["applicability"] != "applicable" or b["applicability"] != "applicable":
                continue
            if (a["verdict"] in _POSITIVE and b["verdict"] == "conditions_failed") or \
               (b["verdict"] in _POSITIVE and a["verdict"] == "conditions_failed"):
                disagreements.append(f"{names[a['seat_id']]}（{roles.get(a['seat_id'])}）{_vz(a['verdict'])} vs "
                                     f"{names[b['seat_id']]}（{roles.get(b['seat_id'])}）{_vz(b['verdict'])}：各按本方法判断，保留分歧，不合并")
    for r in runs:
        if r["applicability"] == "inapplicable":
            disagreements.append(f"{names[r['seat_id']]}：{r['applicability_reason']}（不适用 ≠ 看空）")
        elif r["applicability"] == "unknown" or r["verdict"] == "unknown":
            disagreements.append(f"{names[r['seat_id']]}：结论未知——{r['applicability_reason'] or r['explanation']}")
    confirm, invalid = [], []
    if primary:
        for cr in primary["condition_results"]:
            (invalid if cr["result"] == "failed" else confirm).append(cr)
    for r in runs:
        if raw_roles.get(r["seat_id"]) == "validator":
            invalid += [cr for cr in r["condition_results"] if cr["result"] == "failed"][:5]
    missing = sorted({m for r in runs for m in r["missing_inputs"]})
    failed = [r for r in runs if r["run_state"] == "failed"]
    if primary is None or primary["run_state"] == "failed":
        state = "failed"
    elif failed or any(r["run_state"] == "partial" for r in runs):
        state = "partial"
    else:
        state = "succeeded"
    ptxt = (f"主方法{names[obj['primary_strategy_id']]}：{_vz(primary['verdict'])}；{primary['applicability_reason']}"
            if primary else "主方法没有运行")
    others = "；".join(_line(r, roles) for r in runs if r["seat_id"] != obj["primary_strategy_id"])
    summary = (f"{ptxt}。参与 {len(runs)} 席，各自独立判断，不计票、不平均价位、不合成「总体看多/看空」。"
               + (f"其他席位：{others}。" if others else ""))
    if obj["llm_mode"] != "disabled":
        summary += "。模型解释：未配置可用 provider，已降级为仅规则"
    final = {"summary": summary[:2000], "agreements": agreements, "disagreements": disagreements[:20],
             "confirmation_conditions": confirm[:30], "invalidation_conditions": invalid[:30],
             "missing_inputs": missing, "evidence_ids": sorted(ev_count)[:200], "executed_return_ratio": None}
    return final, state


def _vz(v: str) -> str:
    return {"conditions_met": "条件满足", "conditions_failed": "条件不满足", "watch": "观察（A 级满足、B/C 待核）",
            "unknown": "未知"}.get(v, v)


def _line(r: dict, roles: dict) -> str:
    st = {"skipped": "未运行（不适用）", "failed": "运行失败"}.get(r["run_state"])
    return f"{sr.SEAT_NAMES[r['seat_id']]}（{roles.get(r['seat_id'])}）：{st or _vz(r['verdict'])}"
