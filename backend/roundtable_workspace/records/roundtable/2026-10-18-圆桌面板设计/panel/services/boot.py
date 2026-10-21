"""启动装配：注册全部任务处理器、重启后的级联恢复、阶段开关。

- 任务处理器按模块导入即注册（worker.HANDLERS）；
- 重启恢复：上次进程里仍在 running 的任务已由 queue.recover_on_boot 标为 failed/PROCESS_INTERRUPTED，
  这里把它们关联的业务对象（Case / SeatRun / Review / Replay / Validation）推进到对应的失败终态，
  不把 running 伪装成成功，也不重复入账；
- 阶段开关：P0–P3 默认启用；P4 由提醒能力闸门决定（无权限/未启用 → 501 FEATURE_DISABLED）。
"""
from __future__ import annotations

import importlib
import sys

from ..config import ENABLED_PHASES

_JOB_MODULES = ["snapshot_service", "seat_runner", "case_orchestrator", "review_service", "replay_service",
                "validation_service", "deck_service", "alert_service"]
for _m in _JOB_MODULES:
    try:
        importlib.import_module(f"{__package__}.{_m}")
    except ImportError as _ex:    # 开发期间模块尚未就绪时不阻断 P0
        print(f"[boot] 任务模块 {_m} 未加载：{_ex}", file=sys.stderr)


def after_restart(db, interrupted_job_ids: list[str]) -> None:
    if not interrupted_job_ids:
        return
    from ..domain import clock
    from ..domain.canonical import canonical_json
    now = clock.utc_iso()
    with db.txn() as c:
        for jid in interrupted_job_ids:
            j = c.execute("SELECT kind, resource_id FROM jobs WHERE job_id=?", (jid,)).fetchone()
            if j is None:
                continue
            rid = j["resource_id"]
            if j["kind"] == "case" and rid:
                c.execute("UPDATE cases SET state='failed', finished_at=? WHERE case_id=? AND state NOT IN "
                          "('succeeded','partial','failed')", (now, rid))
            elif j["kind"] == "seat" and rid:
                err = canonical_json({"error": {"code": "PROCESS_INTERRUPTED", "message": "进程重启时席位运行中断",
                                                "request_id": jid, "retryable": True, "details": []}})
                c.execute("UPDATE seat_runs SET run_state='failed', verdict='unknown', candidate_count=NULL, "
                          "finished_at=?, error_json=? WHERE seat_run_id=? AND run_state IN ('queued','running')",
                          (now, err, rid))
            elif j["kind"] == "validation" and rid:
                c.execute("UPDATE validation_runs SET state='failed', finished_at=? WHERE validation_id=? AND state IN "
                          "('queued','running')", (now, rid))
            elif j["kind"] == "replay" and rid:
                c.execute("UPDATE replay_sessions SET state='failed' WHERE replay_id=? AND state='queued'", (rid,))
            elif j["kind"] == "review" and rid:
                pass   # 复盘结果只在成功时写入；中断即无结果行


def feature_gate(db):
    def gate(phase: str) -> tuple[bool, str]:
        if phase in ENABLED_PHASES:
            return True, ""
        if phase == "P4":
            try:
                from .alert_service import capability_gate
                ok, why = capability_gate(db)
                return ok, why
            except ImportError:
                return False, "P4 可选提醒未启用：缺少已核验的实时数据权限与用户显式启用"
        return False, f"{phase} 未启用"
    return gate
