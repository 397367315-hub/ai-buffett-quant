"""任务执行：工作线程池、超时、重试、终态写回（BASE-004）。

处理函数注册在 HANDLERS[kind]，签名 fn(ctx: JobContext) -> JobResult | DETACHED。
- 返回 JobResult(state, resource_id) → 本任务终结；
- 返回 DETACHED → 释放工作槽、任务保持 running，由子任务完成后的编排器推进（Case 用）；
- 抛 RetryableError → 按 Retry-After / 指数退避重排（attempt+1，最多额外 2 次）；
- 抛 JobFailed / 其它异常 → failed，错误码独立存储；JobTimeout → failed JOB_TIMEOUT。
成功只在业务对象与任务终态同一事务提交之后才对外可见。
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import timedelta

from ..domain import clock
from ..domain.canonical import new_id
from . import queue

MAX_EXTRA_RETRIES = 2


class RetryableError(Exception):
    def __init__(self, code: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.code, self.message, self.retry_after = code, message, retry_after


class JobFailed(Exception):
    def __init__(self, code: str, message: str, details: list | None = None, partial_resource: str | None = None):
        super().__init__(message)
        self.code, self.message, self.details, self.partial_resource = code, message, details or [], partial_resource


class JobTimeout(JobFailed):
    def __init__(self, message: str = "任务超时"):
        super().__init__("JOB_TIMEOUT", message)


@dataclass
class JobResult:
    state: str = "succeeded"          # succeeded / partial
    resource_id: str | None = None
    resource_type: str | None = None


DETACHED = object()
HANDLERS: dict[str, callable] = {}


def register(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


class JobContext:
    def __init__(self, db, settings, row: dict):
        self.db = db
        self.settings = settings
        self.row = row
        self.job_id = row["job_id"]
        self.kind = row["kind"]
        self.attempt = int(row["attempt"])
        self.replay_id = row["replay_id"]
        self.resource_id = row["resource_id"]
        import json
        self.payload = json.loads(row["canonical_payload"])
        started = clock.parse_utc(row["started_at"]) if row.get("started_at") else clock.now()
        self.deadline = started + timedelta(seconds=int(row.get("timeout_seconds") or 600))

    def remaining(self) -> float:
        return max(0.0, (self.deadline - clock.now()).total_seconds())

    def check_deadline(self) -> None:
        if clock.now() >= self.deadline:
            raise JobTimeout(f"{self.kind} 任务超过 {self.row.get('timeout_seconds')} 秒")

    def progress(self, done: int, total: int | None = None, phase: str | None = None) -> None:
        queue.progress(self.db, self.job_id, done, total, phase)


def run_one(db, settings, row: dict) -> None:
    """执行一个已领取（running）的任务并写回终态。"""
    fn = HANDLERS.get(row["kind"])
    jid = row["job_id"]
    if fn is None:
        with db.txn() as c:
            queue.finish(c, jid, "failed", error=queue.error_obj("NO_HANDLER", f"没有 {row['kind']} 处理器", jid))
        return
    ctx = JobContext(db, settings, row)
    try:
        res = fn(ctx)
        if res is DETACHED:
            return
        res = res or JobResult()
        with db.txn() as c:
            cur = c.execute("SELECT state FROM jobs WHERE job_id=?", (jid,)).fetchone()
            if cur and cur["state"] == "running":
                queue.finish(c, jid, res.state, resource_id=res.resource_id, resource_type=res.resource_type)
    except RetryableError as ex:
        attempt = int(row["attempt"])
        if attempt <= MAX_EXTRA_RETRIES:
            delay = ex.retry_after if ex.retry_after is not None else 2 ** attempt
            with db.txn() as c:
                queue.retry_later(c, jid, attempt + 1, delay,
                                  queue.error_obj(ex.code, ex.message, jid, retryable=True))
        else:
            with db.txn() as c:
                queue.finish(c, jid, "failed", error=queue.error_obj(ex.code, f"{ex.message}（已重试 {attempt - 1} 次）",
                                                                     jid, retryable=True))
            _on_failed(db, row, ex.code)
    except JobFailed as ex:
        with db.txn() as c:
            queue.finish(c, jid, "failed", resource_id=ex.partial_resource,
                         error=queue.error_obj(ex.code, ex.message, jid, details=ex.details))
        _on_failed(db, row, ex.code)
    except Exception as ex:   # 未预期错误：失败但不泄露栈到 API
        print(f"[job {jid}] {type(ex).__name__}: {ex}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        with db.txn() as c:
            queue.finish(c, jid, "failed", error=queue.error_obj("JOB_INTERNAL_ERROR",
                                                                 f"{type(ex).__name__}（详情见本机日志）", jid))
        _on_failed(db, row, "JOB_INTERNAL_ERROR")


FAILURE_HOOKS: dict[str, callable] = {}


def on_failure(kind: str):
    def deco(fn):
        FAILURE_HOOKS[kind] = fn
        return fn
    return deco


def _on_failed(db, row: dict, code: str) -> None:
    hook = FAILURE_HOOKS.get(row["kind"])
    if hook:
        try:
            hook(db, row, code)
        except Exception:     # pragma: no cover
            traceback.print_exc(file=sys.stderr)


def drain(db, settings, max_jobs: int = 1000, kinds: tuple[str, ...] | None = None) -> int:
    """同步执行队列中可执行的任务（测试/验收用；生产用 WorkerPool）。"""
    n = 0
    wid = "inline"
    while n < max_jobs:
        row = queue.claim_next(db, wid, kinds)
        if row is None:
            break
        run_one(db, settings, row)
        n += 1
    return n


class WorkerPool:
    def __init__(self, db_factory, settings, size: int = 4, poll_seconds: float = 0.25):
        self.db_factory = db_factory
        self.settings = settings
        self.size = size
        self.poll = poll_seconds
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        for i in range(self.size):
            t = threading.Thread(target=self._loop, name=f"rt-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout)

    def _loop(self) -> None:
        db = self.db_factory()
        wid = new_id("worker", 4)
        while not self._stop.is_set():
            try:
                row = queue.claim_next(db, wid)
            except Exception:      # pragma: no cover
                traceback.print_exc(file=sys.stderr)
                time.sleep(1)
                continue
            if row is None:
                self._stop.wait(self.poll)
                continue
            run_one(db, self.settings, row)
