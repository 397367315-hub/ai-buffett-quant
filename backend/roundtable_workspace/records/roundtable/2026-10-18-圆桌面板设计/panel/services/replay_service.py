"""历史训练（REPLAY-001 / REPLAY-002）：服务端截点隔离、逐日推进、盲测决定、揭晓。

- strict_as_of 固定 true；回放有独立的组合、快照、Case、计划、任务与命名空间（replay_id）；
- 截点：游标会话收盘后 30 分钟（daily_bar_availability_policy_v1，date_only）；可见快照只含截点前已可知的日线，
  证券身份、行业、估值、分红等「当前表」不进入（见 docs/implementation/replay-data-eligibility.md）；
- 创建时原子绑定当前会话（mode=replay + cutoff）；漏了 X-Replay-Id 仍按会话截点；legacy 与静态快照一律拒绝；
- 推进：乐观锁 expected_cursor；当前截点没有保存任何决定（日记/观察/放弃也算）就拒绝推进；
- 揭晓：终态，固定窗口（游标之后 REVEAL_SESSIONS 个交易日）只读展示，之后不能再推进或改首轮决定；
- 归档是事后下载的历史数据：模型记忆污染单独说明，历史练习成绩不当作前瞻能力证明。
"""
from __future__ import annotations

import json
from datetime import datetime, time, timedelta

from ..config import get_settings
from ..data.evidence_store import EvidenceStore
from ..domain import clock, pit
from ..domain.canonical import canonical_json, derived_id, new_id
from ..jobs import queue, worker
from ..jobs.worker import JobFailed, JobResult
from ..web.errors import ApiError
from . import archive_service, journal_service as js, snapshot_build as sb, snapshot_service
from .snapshot_build import Dataset, SnapshotInputs

REVEAL_SESSIONS = 5
HISTORY = sb.HISTORY_SESSIONS


def cutoff_for(day: str) -> str:
    sh = datetime.combine(datetime.fromisoformat(day).date(), time(15, 0), tzinfo=clock.SHANGHAI) + timedelta(minutes=30)
    return clock.utc_iso(sh)


def _ds(day: str, raw: bytes, rows: list[dict], role: str, store: EvidenceStore) -> Dataset:
    sha = store.put(raw)
    p = archive_service.day_path(day)
    mt = clock.utc_iso(datetime.fromtimestamp(p.stat().st_mtime, tz=clock.UTC)) if p else clock.utc_iso()
    return Dataset(role=role, rows=rows, raw_sha256=sha, captured_at=mt, source_id="archive_daily_file",
                   operation_id="reference_stock_daily", params={"tradedate": day.replace("-", "")}, session_date=day,
                   first_observed_at=mt, raw_format="legacy_rows_v1",
                   note="只读历史归档（事后下载）；按 daily_bar_availability_policy_v1 视为收盘后可知")


# ── 时点事实（公告、财务、分红等非日线事实）：按 pit_visibility_policy_v1 截到知识截点 ──
def standalone_facts(db) -> list[dict]:
    """服务端登记的独立事实（evidence.field_path = facts.<id>，不属于任何快照）。"""
    out = []
    for r in db.query("SELECT canonical_payload FROM evidence WHERE snapshot_id IS NULL AND replay_id IS NULL "
                      "AND json_extract(canonical_payload, '$.field_path') LIKE 'facts.%' ORDER BY evidence_id"):
        e = json.loads(r["canonical_payload"])
        v = e["value"] if isinstance(e["value"], dict) else {"value": e["value"]}
        out.append({"fact_id": v.get("fact_id") or e["evidence_id"], "available_at": e["available_at"],
                    "first_observed_at": e["first_observed_at"],
                    "time_precision": "date" if e["time_precision"] == "date_only" else e["time_precision"],
                    "publication_date": v.get("publication_date"), "revises_fact_id": v.get("revises_fact_id"),
                    "report_period": v.get("report_period"), "value": v.get("value"), "evidence": e})
    return out


def facts_payload(db, cutoff_at: str, calendar=None) -> dict:
    view = pit.visible(standalone_facts(db), cutoff_at, calendar)
    return view


def _fact_evidence_for_snapshot(view: dict, salt: str) -> list[dict]:
    out = []
    for f in view["visible"]:
        e = dict(f["evidence"])
        orig = e["evidence_id"]
        e["evidence_id"] = derived_id("ev", salt, orig)
        e["snapshot_id"] = None
        e["related_evidence_ids"] = [orig]
        e["available_at"] = f["_effective_available_at"]
        out.append(e)
    return out


def build_visible(db, replay: dict, day: str, *, reveal_until: str | None = None) -> str:
    store = EvidenceStore(get_settings().evidence_dir)
    cal = archive_service.calendar()
    days = cal.sessions_between("0000-01-01", day)
    hist_days = [d for d in days if d < day][-HISTORY:]
    rd = archive_service.read_day(day)
    if rd is None:
        raise JobFailed("ARCHIVE_SESSION_MISSING", f"归档缺少 {day}")
    history = []
    for d in hist_days:
        x = archive_service.read_day(d)
        if x:
            history.append(_ds(d, x[0], x[1], "daily_history", store))
    extra = []
    note = f"训练截点 {replay['knowledge_cutoff_at']}（{day} 收盘后）：只含截点前可知的日线，不是当前行情"
    fresh = ("fresh", note)
    if reveal_until:
        # 揭晓：游标当日 + 固定窗口内的后续日线作为只读历史展示（窗口末日作为会话日）
        extra.append(_ds(day, rd[0], rd[1], "daily_history", store))
        for d in [x for x in cal.sessions_between(day, reveal_until) if day < x < reveal_until]:
            x = archive_service.read_day(d)
            if x:
                extra.append(_ds(d, x[0], x[1], "daily_history", store))
        fresh = ("unknown", f"已揭晓：固定窗口 {day} → {reveal_until} 的后续日线只读展示，不再是盲测")
    session = _ds(reveal_until or day, *(archive_service.read_day(reveal_until) if reveal_until else rd),
                  "daily_session", store)
    facts = facts_payload(db, cutoff_for(reveal_until or day), cal)
    explicit = _fact_evidence_for_snapshot(facts, f"{replay['replay_id']}:{reveal_until or day}")
    inp = SnapshotInputs(session_date=reveal_until or day, source_mode="file_import", symbols=replay["symbols"],
                         explicit_evidence=explicit,
                         calendar=cal, session_daily=session, history=history + extra, finality="final_confirmed",
                         finality_basis="archive_settled_v1：归档日线为事后下载的沉淀日线",
                         replay_id=replay["replay_id"], freshness_override=fresh,
                         reference_now=clock.parse_utc(cutoff_for(reveal_until or day)),
                         warnings=["replay_strict_as_of：证券身份按代码段受控 fallback；ST 按当日名称；行业/估值/分红不可用",
                                   "model_memory_contamination_possible：历史练习可能被模型记忆污染（本系统默认不调用模型）"])
    lp = snapshot_service.build_limit_pool(inp)
    if lp is not None:
        inp.derived.append(snapshot_service._derived(store, "limit_pool", lp, "limit_pool_v1", session.captured_at,
                                                     "roundtable_limit_pool_derived", "由归档日线推导（无封板时间）",
                                                     inp.session_date))
    res = sb.build(inp)
    sid, _created, _sup = snapshot_service.store_snapshot(
        db, res, replay_id=replay["replay_id"], lineage_key=f"replay:{replay['replay_id']}:{inp.session_date}",
        snapshot_id=f"snap_{replay['replay_id'][-12:]}_{inp.session_date.replace('-', '')}{'_reveal' if reveal_until else ''}")
    return sid


def get(db, replay_id: str) -> dict | None:
    r = db.one("SELECT canonical_payload FROM replay_sessions WHERE replay_id=?", (replay_id,))
    if r is None:
        return None
    base = json.loads(r["canonical_payload"])
    row = dict(db.one("SELECT * FROM replay_sessions WHERE replay_id=?", (replay_id,)))
    base.update({"state": row["state"], "cursor_session_date": row["cursor_session_date"],
                 "knowledge_cutoff_at": row["knowledge_cutoff_at"], "revealed": bool(row["revealed"]),
                 "visible_snapshot_id": row["visible_snapshot_id"], "revealed_at": row["revealed_at"]})
    return base


def create(c, db, body: dict, session: dict, request_sha256: str, max_waiting: int) -> dict:
    if session["mode"] == "replay":
        raise ApiError(409, "TRAINING_NOT_ENDED", "当前会话已绑定一场训练；先揭晓并切回真实盘")
    m = archive_service.manifest()
    if not m["files"]:
        raise ApiError(503, "ARCHIVE_UNAVAILABLE", "服务端没有配置可用的历史日线归档")
    if body["data_archive_fingerprint"] != m["fingerprint"]:
        raise ApiError(422, "ARCHIVE_FINGERPRINT_MISMATCH", "数据归档指纹与服务端归档不符（请核对后用当前归档指纹重建）",
                       [{"field": "data_archive_fingerprint", "reason": f"expected {m['fingerprint']}"},
                        {"field": "archive_coverage", "reason": f"{m['coverage_start']}..{m['coverage_end']} ({m['session_count']} sessions)"}])
    cal = archive_service.calendar()
    s, e = body["start_session_date"], body["end_session_date"]
    if e < s:
        raise ApiError(422, "VALIDATION_ERROR", "结束日早于开始日", [{"field": "end_session_date", "reason": "before start"}])
    for f, d in (("start_session_date", s), ("end_session_date", e)):
        if cal.status(d) != "open":
            raise ApiError(422, "NOT_AN_ARCHIVE_SESSION", f"{d} 不是归档中的交易日", [{"field": f, "reason": "not in archive"}])
    prior = [d for d in cal.sessions_between("0000-01-01", s) if d < s]
    if len(prior) < 20:
        raise ApiError(422, "INSUFFICIENT_HISTORY", f"开始日之前只有 {len(prior)} 个交易日，席位规则需要更长历史",
                       [{"field": "start_session_date", "reason": "need >=20 prior sessions"}])
    day0 = archive_service.read_day(s)[1]
    present = {str(r.get("symbol")).zfill(6) for r in day0}
    bad = [x for x in body["symbols"] if x.split(".")[0] not in present]
    if bad:
        raise ApiError(422, "SYMBOL_NOT_IN_ARCHIVE_SESSION", f"开始日归档里没有：{', '.join(bad[:5])}",
                       [{"field": "symbols", "reason": x} for x in bad[:10]])
    rid = new_id("replay")
    pid = new_id("portfolio")
    now = clock.utc_iso()
    job = queue.enqueue(c, kind="replay", payload={"replay_id": rid}, request_sha256=request_sha256, resource_type="replay",
                        resource_id=rid, replay_id=rid, max_waiting=max_waiting, total_units=1)
    cutoff = cutoff_for(s)
    c.execute("""INSERT INTO portfolios(portfolio_id, mode, revision, cash_minor, market_value_minor, total_equity_minor,
                 quality, last_event_sequence, replay_id, created_at, updated_at, canonical_payload)
                 VALUES(?,'paper',0,0,NULL,NULL,'complete',0,?,?,?,?)""",
              (pid, rid, now, now, canonical_json({"name": f"训练隔离组合 {rid[-6:]}", "watch": []})))
    obj = {"replay_id": rid, "job_id": job["job_id"], "state": "queued", "symbols": body["symbols"],
           "start_session_date": s, "end_session_date": e, "cursor_session_date": s, "knowledge_cutoff_at": cutoff,
           "strict_as_of": True, "revealed": False, "visible_snapshot_id": None, "calendar_version": cal.calendar_version,
           "data_archive_fingerprint": m["fingerprint"], "isolated_portfolio_id": pid, "created_at": now, "revealed_at": None}
    c.execute("""INSERT INTO replay_sessions(replay_id, job_id, state, start_session_date, end_session_date, cursor_session_date,
                 knowledge_cutoff_at, strict_as_of, revealed, visible_snapshot_id, calendar_version, data_archive_fingerprint,
                 isolated_portfolio_id, created_at, revealed_at, canonical_payload) VALUES(?,?,?,?,?,?,?,1,0,NULL,?,?,?,?,NULL,?)""",
              (rid, job["job_id"], "queued", s, e, s, cutoff, cal.calendar_version, m["fingerprint"], pid, now,
               canonical_json(obj)))
    p = dict(c.execute("SELECT * FROM portfolios WHERE portfolio_id=?", (pid,)).fetchone())
    ikey = f"replay-open-{rid}"
    js.ensure_internal_key(c, f"/internal/replays/{rid}/opening", ikey, request_sha256, {"replay_id": rid})
    js._append(c, p, js._event(pid, "opening_balance", "paper", rid, effective_at=cutoff, session_date=s,
                               net_cash_delta_minor=body["initial_cash_minor"], note="训练期初资金（隔离组合）"),
               (f"/internal/replays/{rid}/opening", ikey))
    from ..web.handlers.session import bind_session_to_replay
    bind_session_to_replay(c, session, rid, cutoff)
    return job


@worker.register("replay")
def run_job(ctx: worker.JobContext):
    rid = ctx.payload["replay_id"]
    rep = get(ctx.db, rid)
    if rep is None:
        raise JobFailed("REPLAY_MISSING", "回放不存在")
    sid = build_visible(ctx.db, rep, rep["cursor_session_date"])
    with ctx.db.txn() as c:
        c.execute("UPDATE replay_sessions SET state='active', visible_snapshot_id=? WHERE replay_id=? AND revealed=0",
                  (sid, rid))
    return JobResult("succeeded", resource_id=rid, resource_type="replay")


@worker.on_failure("replay")
def _failed(db, row, code):
    p = json.loads(row["canonical_payload"])
    with db.txn() as c:
        c.execute("UPDATE replay_sessions SET state='failed' WHERE replay_id=? AND state='queued'", (p.get("replay_id"),))


def _decision_saved(c, rep: dict) -> bool:
    cur = rep["cursor_session_date"]
    n = c.execute("""SELECT COUNT(*) FROM journal_events WHERE portfolio_id=? AND event_type<>'opening_balance'
                     AND session_date=?""", (rep["isolated_portfolio_id"], cur)).fetchone()[0]
    if n:
        return True
    n2 = c.execute("SELECT COUNT(*) FROM plan_versions WHERE replay_id=? AND status='frozen' AND snapshot_id=?",
                   (rep["replay_id"], rep["visible_snapshot_id"])).fetchone()[0]
    return bool(n2)


def step(db, rid: str, body: dict) -> dict:
    rep = get(db, rid)
    if rep is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "回放不存在")
    if rep["revealed"]:
        raise ApiError(409, "REPLAY_ALREADY_REVEALED", "已揭晓的训练不能再推进")
    if rep["state"] != "active":
        raise ApiError(409, "REPLAY_NOT_ACTIVE", f"回放当前状态 {rep['state']}，不能推进")
    if body["expected_cursor_session_date"] != rep["cursor_session_date"]:
        raise ApiError(409, "REPLAY_CURSOR_CONFLICT", "回放游标已变化",
                       [{"field": "expected_cursor_session_date", "reason": f"current {rep['cursor_session_date']}"}])
    with db.txn() as c:
        if not _decision_saved(c, rep):
            raise ApiError(409, "DECISION_REQUIRED", "推进前先保存本截点的决定（继续等待 / 准备条件 / 放弃也要记一笔）")
    cal = archive_service.calendar()
    nxt = cal.next_session(rep["cursor_session_date"])
    if nxt is None or nxt > rep["end_session_date"]:
        with db.txn() as c:
            c.execute("UPDATE replay_sessions SET state='completed' WHERE replay_id=? AND revealed=0", (rid,))
        return get(db, rid)
    rep2 = dict(rep)
    rep2["knowledge_cutoff_at"] = cutoff_for(nxt)
    sid = build_visible(db, rep2, nxt)
    with db.txn() as c:
        cur = c.execute("SELECT cursor_session_date FROM replay_sessions WHERE replay_id=?", (rid,)).fetchone()
        if cur["cursor_session_date"] != rep["cursor_session_date"]:
            raise ApiError(409, "REPLAY_CURSOR_CONFLICT", "回放游标已被并发推进")
        c.execute("""UPDATE replay_sessions SET cursor_session_date=?, knowledge_cutoff_at=?, visible_snapshot_id=?,
                     state=CASE WHEN ?=end_session_date THEN 'completed' ELSE 'active' END WHERE replay_id=?""",
                  (nxt, rep2["knowledge_cutoff_at"], sid, nxt, rid))
        c.execute("UPDATE local_sessions SET knowledge_cutoff_at=? WHERE replay_id=?", (rep2["knowledge_cutoff_at"], rid))
    return get(db, rid)


def reveal(db, rid: str, body: dict) -> dict:
    rep = get(db, rid)
    if rep is None:
        raise ApiError(404, "RESOURCE_NOT_VISIBLE", "回放不存在")
    if rep["revealed"]:
        return rep
    if body["expected_cursor_session_date"] != rep["cursor_session_date"]:
        raise ApiError(409, "REPLAY_CURSOR_CONFLICT", "回放游标已变化",
                       [{"field": "expected_cursor_session_date", "reason": f"current {rep['cursor_session_date']}"}])
    if rep["state"] not in ("active", "completed"):
        raise ApiError(409, "REPLAY_NOT_ACTIVE", f"回放当前状态 {rep['state']}")
    cal = archive_service.calendar()
    after = [d for d in cal.sessions_between(rep["cursor_session_date"], "9999-12-31") if d > rep["cursor_session_date"]]
    until = after[REVEAL_SESSIONS - 1] if len(after) >= REVEAL_SESSIONS else (after[-1] if after else None)
    sid = build_visible(db, rep, rep["cursor_session_date"], reveal_until=until) if until else rep["visible_snapshot_id"]
    with db.txn() as c:
        c.execute("""UPDATE replay_sessions SET revealed=1, revealed_at=?, state='revealed', visible_snapshot_id=?
                     WHERE replay_id=? AND revealed=0""", (clock.utc_iso(), sid, rid))
    return get(db, rid)
