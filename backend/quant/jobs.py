"""Persisted job status and retained asyncio task references."""

from __future__ import annotations

import asyncio
import uuid
from typing import Coroutine

from quant.storage import quant_store
from services.data_collector import shanghai_now


_tasks: set[asyncio.Task] = set()


def _fail_orphaned_jobs() -> None:
    """A queued/running task cannot survive a web-process restart."""
    def mutate(document: dict) -> None:
        now = shanghai_now().isoformat()
        for kind in ("scan", "backtest", "fqe", "zhaban", "zhaban_backtest", "research", "market_sync"):
            for job in document.setdefault(kind, {}).values():
                if job.get("status") in {"queued", "running"}:
                    job.update({
                        "status": "failed", "phase": "interrupted", "progress": 100,
                        "message": "服务重启，任务已中止，请重新发起",
                        "error": "process_restarted", "completed_at": now,
                    })

    quant_store.update("jobs", mutate)


def create_job(kind: str, prefix: str, metadata: dict | None = None) -> dict:
    if kind not in {"scan", "backtest", "fqe", "zhaban", "zhaban_backtest", "research", "market_sync"}:
        raise ValueError("未知任务类型")
    job = {
        "job_id": f"{prefix}_{uuid.uuid4().hex[:12]}",
        "status": "queued",
        "progress": 0,
        "phase": "queued",
        "message": "任务已排队",
        "created_at": shanghai_now().isoformat(),
        "started_at": None,
        "completed_at": None,
        "error": None,
        **(metadata or {}),
    }

    def mutate(document: dict) -> None:
        jobs = document.setdefault(kind, {})
        jobs[job["job_id"]] = job
        ordered = sorted(jobs.values(), key=lambda item: item.get("created_at") or "", reverse=True)
        document[kind] = {item["job_id"]: item for item in ordered[:50]}

    quant_store.update("jobs", mutate)
    return dict(job)


def update_job(kind: str, job_id: str, **updates) -> dict | None:
    updated = None

    def mutate(document: dict) -> None:
        nonlocal updated
        job = document.setdefault(kind, {}).get(job_id)
        if job is None:
            return
        job.update(updates)
        updated = dict(job)

    quant_store.update("jobs", mutate)
    return updated


def get_job(kind: str, job_id: str) -> dict | None:
    return quant_store.read("jobs").get(kind, {}).get(job_id)


def latest_running_job(kind: str) -> dict | None:
    jobs = quant_store.read("jobs").get(kind, {}).values()
    running = [job for job in jobs if job.get("status") in {"queued", "running"}]
    return max(running, key=lambda item: item.get("created_at") or "", default=None)


def spawn(coroutine: Coroutine) -> asyncio.Task:
    task = asyncio.create_task(coroutine)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


_fail_orphaned_jobs()
