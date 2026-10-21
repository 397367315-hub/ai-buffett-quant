"""Bounded background refresh for the roundtable legacy dashboard.

The refresh runs in one worker thread, reusing the panel's shared NumCat hook.
It never manufactures data: a missing quote/flow source leaves the task failed
or explicitly partial and exposes the missing stage in status.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
import json
import os
from datetime import datetime, timezone
from typing import Any


class RoundtableLegacyRefresh:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._task: asyncio.Task | None = None
        self._runtime = None
        self._stop = threading.Event()
        self._state: dict[str, Any] = {
            "status": "idle", "job_id": None, "stage": None,
            "started_at": None, "finished_at": None, "error": None,
            "asof": None, "output": None,
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _bind(self, runtime) -> None:
        if runtime is self._runtime:
            return
        self._runtime = runtime
        path = runtime.runtime_dir / "legacy_refresh.json"
        if path.is_file():
            state = json.loads(path.read_text())
            if isinstance(state, dict):
                self._state = state
                if state.get("status") in {"queued", "running"}:
                    self._state.update(status="failed", stage="interrupted", finished_at=self._now(),
                                       error="服务重启，之前的同步已中断；已保存的行情仍可复用，请重新同步")
                    self._save()

    def _save(self) -> None:
        if self._runtime is None:
            return
        path = self._runtime.runtime_dir / "legacy_refresh.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._state, ensure_ascii=False))
        os.replace(temporary, path)
        self._runtime.mark_dirty()

    def status(self, runtime=None) -> dict[str, Any]:
        with self._lock:
            if runtime is not None:
                self._bind(runtime)
            return dict(self._state)

    async def start(self, runtime, *, history_days: int = 130) -> dict[str, Any]:
        try:
            history_days = int(history_days)
        except (TypeError, ValueError) as exc:
            raise ValueError("history_days 必须是整数") from exc
        if history_days < 30 or history_days > 130:
            raise ValueError("history_days 必须在 30 到 130 之间")
        limit_mb = _memory_limit_mb()
        if limit_mb is not None and limit_mb < 384:
            raise ValueError("当前实例内存不足以运行全市场研究，请先调整实例资源")
        with self._lock:
            self._bind(runtime)
            if self._task is not None and not self._task.done():
                return dict(self._state)
            job_id = "rt-refresh-" + uuid.uuid4().hex[:12]
            self._stop.clear()
            self._state = {"status": "queued", "job_id": job_id, "stage": "queued",
                           "started_at": self._now(), "finished_at": None,
                           "error": None, "asof": None, "output": None,
                           "history_days": history_days, "memory_limit_mb": limit_mb}
            self._save()
            self._task = asyncio.create_task(self._run(runtime, job_id, history_days), name=job_id)
            return dict(self._state)

    async def _run(self, runtime, job_id: str, history_days: int) -> None:
        with self._lock:
            self._state["status"] = "running"
            self._state["stage"] = "refresh"
            self._save()
        try:
            result = await asyncio.to_thread(self._refresh_sync, runtime, self._set_stage, history_days)
            runtime.mark_dirty()
            await runtime.flush(force=True)
            with self._lock:
                self._state.update(status="completed", stage="done", finished_at=self._now(), **result)
                self._save()
            await runtime.flush(force=True)
        except Exception as exc:
            with self._lock:
                self._state.update(status="failed", stage="error", finished_at=self._now(),
                                   error=f"{type(exc).__name__}: {str(exc)[:500]}")
                if not runtime.unavailable:
                    self._save()

    def _set_stage(self, stage: str) -> None:
        if self._stop.is_set():
            raise InterruptedError("服务停止，圆桌同步已中断")
        with self._lock:
            self._state["stage"] = stage
            self._save()

    async def shutdown(self, runtime) -> None:
        self._stop.set()
        if self._task and not self._task.done():
            with self._lock:
                self._state.update(status="failed", stage="interrupted", finished_at=self._now(),
                                   error="服务重启，同步已中断；请重新同步")
                if not runtime.unavailable:
                    self._save()
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    @staticmethod
    def _refresh_sync(runtime, set_stage, history_days: int) -> dict[str, Any]:
        import importlib
        import sys
        from pathlib import Path

        vendor = Path(__file__).resolve().parents[1] / "roundtable_workspace"
        panel_root = vendor / "records" / "roundtable" / "2026-10-18-圆桌面板设计"
        path_text = str(panel_root)
        if path_text not in sys.path:
            sys.path.insert(0, path_text)
        refresh = importlib.import_module("panel.refresh")
        set_stage("refresh_all")
        asof = refresh.build(verbose=False, universe="all", history_days=history_days,
                             progress=lambda stage: set_stage("含创业板 · " + stage))
        set_stage("refresh_main")
        main_asof = refresh.build(verbose=False, universe="main", history_days=history_days,
                                  progress=lambda stage: set_stage("主板 · " + stage))
        # Only safe, portable metadata crosses the API boundary.
        return {"asof": asof, "main_asof": main_asof,
                "stats": {"all": _dashboard_stats(refresh.DATA / "dashboard.json"),
                           "main": _dashboard_stats(refresh.DATA / "dashboard_main.json")},
                "history_days": history_days,
                "output": None, "error": None}


def _dashboard_stats(path) -> dict[str, Any]:
    import json
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False, "reason": "dashboard缺失"}
    seats = payload.get("seats") or payload.get("results") or []
    candidate_rows = payload.get("candidates")
    if isinstance(candidate_rows, list):
        unique = set()
        for row in candidate_rows:
            if isinstance(row, dict):
                identity = row.get("symbol") or row.get("code") or row.get("stock_code")
                unique.add(("id", str(identity)) if identity else ("row", json.dumps(row, ensure_ascii=False, sort_keys=True)))
            else:
                unique.add(("value", str(row)))
        candidate_count = len(unique)
    else:
        candidate_count = None
    data_status = payload.get("dataStatus") if isinstance(payload.get("dataStatus"), dict) else {}
    asof = payload.get("quoteDate") or data_status.get("quote_date")
    features = payload.get("features")
    feature_rows = len(features) if isinstance(features, list) else None
    return {"available": True, "asof": asof,
            "seat_count": len(seats), "candidate_count": candidate_count,
            "feature_rows": feature_rows}


def _memory_limit_mb() -> int | None:
    """Read container memory ceiling without requiring psutil or secrets."""
    for name in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(name, encoding="ascii") as f:
                raw = f.read().strip()
            if raw and raw != "max":
                value = int(raw)
                if value > 0:
                    return value // (1024 * 1024)
        except (OSError, ValueError):
            continue
    return None


roundtable_legacy_refresh = RoundtableLegacyRefresh()
