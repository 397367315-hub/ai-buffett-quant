"""持久任务队列（BASE-004 / 02 §9）。

- 状态 queued → running → succeeded/partial/failed；等待中的任务可 cancelled（仅内部管理用，
  v1 不提供公开取消接口）；超时与取消是终态原因，不能据此判条件失败；
- 最多 4 个活跃执行、100 个等待（Settings 可配，满队列 429 QUEUE_FULL）；
- 只对明确可重试的错误额外重试 ≤2 次，指数退避，上游 Retry-After 优先；
- 进程重启：running → failed PROCESS_INTERRUPTED（保留部分产物）；queued 重新排队；
- 重跑必须是新 job_id 并关联原任务，不悄悄改完成结果。
"""
from __future__ import annotations

import json
from datetime import timedelta

from ..domain import clock
from ..domain.canonical import canonical_json, new_id
from ..web.errors import ApiError

TERMINAL = ("succeeded", "partial", "failed", "cancelled")
RESOURCE_URL = {
    "snapshot": "/api/v1/snapshots/{id}", "seat_run": "/api/v1/seat-runs/{id}", "case": "/api/v1/cases/{id}",
    "review": "/api/v1/reviews/{id}", "replay": "/api/v1/replays/{id}", "validation": "/api/v1/validations/{id}",
}
DEFAULT_TIMEOUTS = {"snapshot": 900, "seat": 300, "case": 1800, "review": 120, "replay": 900, "validation": 3600}


def enqueue(c, *, kind: str, payload: dict, request_sha256: str, resource_type: str | None,
            resource_id: str | None, replay_id: str | None, max_waiting: int,
            parent_job_id: str | None = None, timeout_seconds: int | None = None,
            total_units: int | None = None, phase: str = "queued", job_id: str | None = None) -> dict:
    waiting = c.execute("SELECT COUNT(*) FROM jobs WHERE state='queued'").fetchone()[0]
    if waiting >= max_waiting:
        raise ApiError(429, "QUEUE_FULL", f"等待中的任务已达上限 {max_waiting}，请稍后重试",
                       [{"field": "queue", "reason": f"waiting={waiting}"}], headers={"Retry-After": "5"})
    jid = job_id or new_id("job")
    now = clock.utc_iso()
    c.execute("""INSERT INTO jobs(job_id, kind, state, phase, completed_units, total_units, attempt,
                 resource_type, resource_id, replay_id, request_sha256, created_at, started_at, finished_at,
                 error_json, canonical_payload, not_before, parent_job_id, timeout_seconds, worker_id)
                 VALUES(?,?,?,?,?,?,1,?,?,?,?,?,NULL,NULL,NULL,?,NULL,?,?,NULL)""",
              (jid, kind, "queued", phase, 0, total_units, resource_type, resource_id, replay_id,
               request_sha256, now, canonical_json(payload), parent_job_id,
               timeout_seconds or DEFAULT_TIMEOUTS.get(kind, 600)))
    return job_dict(c.execute("SELECT * FROM jobs WHERE job_id=?", (jid,)).fetchone())


def job_dict(r) -> dict:
    err = json.loads(r["error_json"]) if r["error_json"] else None
    return {"job_id": r["job_id"], "kind": r["kind"], "state": r["state"], "phase": r["phase"] or "",
            "completed_units": int(r["completed_units"] or 0), "total_units": r["total_units"],
            "resource_type": r["resource_type"], "resource_id": r["resource_id"],
            "created_at": r["created_at"], "started_at": r["started_at"], "finished_at": r["finished_at"],
            "attempt": int(r["attempt"]), "error": err}


def accepted(job: dict) -> tuple[int, dict, dict]:
    status_url = f"/api/v1/jobs/{job['job_id']}"
    tmpl = RESOURCE_URL.get(job["resource_type"] or "")
    resource_url = tmpl.format(id=job["resource_id"]) if tmpl and job["resource_id"] else None
    return 202, {"job": job, "status_url": status_url, "resource_url": resource_url}, \
        {"Location": status_url, "Retry-After": "2"}


def error_obj(code: str, message: str, job_id: str, retryable: bool = False, details: list | None = None) -> dict:
    return {"error": {"code": code, "message": message, "request_id": job_id if job_id[0].isalpha() else "job_x",
                      "retryable": retryable, "details": details or []}}


def claim_next(db, worker_id: str, kinds: tuple[str, ...] | None = None) -> dict | None:
    now = clock.utc_iso()
    with db.txn() as c:
        q = ("SELECT * FROM jobs WHERE state='queued' AND (not_before IS NULL OR not_before<=?) "
             + ("AND kind IN (%s) " % ",".join("?" * len(kinds)) if kinds else "")
             + "ORDER BY created_at, job_id LIMIT 1")
        r = c.execute(q, (now, *(kinds or ()))).fetchone()
        if r is None:
            return None
        c.execute("UPDATE jobs SET state='running', started_at=COALESCE(started_at, ?), worker_id=?, phase=? "
                  "WHERE job_id=? AND state='queued'", (now, worker_id, "running", r["job_id"]))
        return dict(c.execute("SELECT * FROM jobs WHERE job_id=?", (r["job_id"],)).fetchone())


def progress(db, job_id: str, done: int, total: int | None, phase: str | None = None) -> None:
    with db.txn() as c:
        cur = c.execute("SELECT total_units FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if cur is None:
            return
        total = total if total is not None else cur["total_units"]
        if total is not None and done > total:
            total = done
        c.execute("UPDATE jobs SET completed_units=?, total_units=?, phase=COALESCE(?, phase) WHERE job_id=?",
                  (done, total, phase, job_id))


def finish(c, job_id: str, state: str, *, resource_id: str | None = None, error: dict | None = None,
           phase: str | None = None, resource_type: str | None = None) -> None:
    assert state in TERMINAL
    now = clock.utc_iso()
    sets = ["state=?", "finished_at=?", "phase=?"]
    vals: list = [state, now, phase or state]
    if resource_id is not None:
        sets.append("resource_id=?")
        vals.append(resource_id)
        sets.append("resource_type=COALESCE(?, resource_type)")
        vals.append(resource_type)
    if error is not None:
        sets.append("error_json=?")
        vals.append(canonical_json(error))
    c.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE job_id=?", (*vals, job_id))


def retry_later(c, job_id: str, attempt: int, delay_seconds: float, error: dict) -> None:
    nb = clock.utc_iso(clock.now() + timedelta(seconds=delay_seconds))
    c.execute("UPDATE jobs SET state='queued', attempt=?, not_before=?, worker_id=NULL, phase='retry_wait', "
              "error_json=? WHERE job_id=?", (attempt, nb, canonical_json(error), job_id))


def recover_on_boot(db) -> list[str]:
    """重启恢复：running → failed PROCESS_INTERRUPTED；queued 保持（由 worker 重新领取）。"""
    with db.txn() as c:
        rows = c.execute("SELECT job_id FROM jobs WHERE state='running'").fetchall()
        ids = [r["job_id"] for r in rows]
        for jid in ids:
            finish(c, jid, "failed", error=error_obj("PROCESS_INTERRUPTED", "进程重启时任务仍在运行，已标记失败；部分产物保留",
                                                      jid), phase="interrupted")
    return ids
