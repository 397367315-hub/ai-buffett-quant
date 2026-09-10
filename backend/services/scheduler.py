from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import date, datetime, time, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database import async_session
from models import SchedulerTaskLedger

SCHEDULER_TIMEZONE = ZoneInfo("Asia/Shanghai")
SCHEDULER_JOB_DEFAULTS = {
    "coalesce": True,
    "max_instances": 1,
    "misfire_grace_time": 300,
}


def _cron_trigger(**fields: Any) -> CronTrigger:
    """Create every wall-clock schedule in the market's canonical timezone."""
    return CronTrigger(timezone=SCHEDULER_TIMEZONE, **fields)

# Render Web services can restart or sleep around a market window.  Keep the
# scheduler honest about what happened in the current process and use the
# startup audit below to recover only today's missed work.
scheduler = AsyncIOScheduler(
    timezone=SCHEDULER_TIMEZONE,
    job_defaults=SCHEDULER_JOB_DEFAULTS,
)
_scheduler_listener_bound_to: int | None = None
_scheduler_started_at: datetime | None = None
_scheduler_heartbeat_at: datetime | None = None
_job_state: dict[str, dict[str, Any]] = {}
_startup_audit_state: dict[str, dict[str, Any]] = {}
_startup_compensation_state: dict[str, dict[str, Any]] = {}

def _ledger_row(row: SchedulerTaskLedger | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "run_date": row.run_date.isoformat() if row.run_date else None,
        "run_key": row.run_key,
        "job_id": row.job_id,
        "status": row.status,
        "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
        "reason": row.reason,
    }


async def _ledger_get(run_date: date, run_key: str) -> dict[str, Any]:
    """Read the durable claim before consulting process-local scheduler state."""
    try:
        async with async_session() as session:
            row = (
                await session.execute(
                    select(SchedulerTaskLedger).where(
                        SchedulerTaskLedger.run_date == run_date,
                        SchedulerTaskLedger.run_key == run_key,
                    )
                )
            ).scalar_one_or_none()
        return {"available": True, "row": _ledger_row(row)}
    except Exception as exc:
        return {"available": False, "row": None, "reason": f"{type(exc).__name__}: {str(exc)[:180]}"}


async def _ledger_claim(
    run_date: date,
    run_key: str,
    job_id: str,
    *,
    status: str,
    started_at: datetime | None,
    completed_at: datetime | None,
    reason: str,
) -> dict[str, Any]:
    """Atomically create a claim/terminal record using the date+run_key key."""
    try:
        async with async_session() as session:
            existing = (
                await session.execute(
                    select(SchedulerTaskLedger).where(
                        SchedulerTaskLedger.run_date == run_date,
                        SchedulerTaskLedger.run_key == run_key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return {"available": True, "claimed": False, "conflict": False, "row": _ledger_row(existing)}
            row = SchedulerTaskLedger(
                run_date=run_date,
                run_key=run_key,
                job_id=job_id,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                reason=reason,
            )
            session.add(row)
            await session.commit()
            return {"available": True, "claimed": True, "conflict": False, "row": _ledger_row(row)}
    except IntegrityError:
        # Another Render process inserted the unique key between our read and
        # insert. Its row is authoritative; never execute the handler here.
        existing = await _ledger_get(run_date, run_key)
        if existing.get("available") and existing.get("row"):
            return {"available": True, "claimed": False, "conflict": True, "row": existing["row"]}
        return {"available": False, "claimed": False, "conflict": True, "row": None, "reason": "账本唯一键冲突后无法读取认领记录"}
    except Exception as exc:
        return {"available": False, "claimed": False, "conflict": False, "row": None, "reason": f"{type(exc).__name__}: {str(exc)[:180]}"}


async def _ledger_update(
    run_date: date,
    run_key: str,
    *,
    status: str,
    completed_at: datetime | None,
    reason: str,
) -> dict[str, Any]:
    """Complete a previously claimed row without creating another record."""
    try:
        async with async_session() as session:
            row = (
                await session.execute(
                    select(SchedulerTaskLedger).where(
                        SchedulerTaskLedger.run_date == run_date,
                        SchedulerTaskLedger.run_key == run_key,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return {"available": True, "updated": False, "reason": "认领记录不存在"}
            row.status = status
            row.completed_at = completed_at
            row.reason = reason
            await session.commit()
        return {"available": True, "updated": True}
    except Exception as exc:
        return {"available": False, "updated": False, "reason": f"{type(exc).__name__}: {str(exc)[:180]}"}


def _ledger_item(spec: dict[str, Any], audit_key: str, row: dict[str, Any] | None) -> dict[str, Any]:
    status = str((row or {}).get("status") or "ALREADY_CLAIMED")
    if status == "CLAIMED":
        status = "ALREADY_CLAIMED"
    return {
        "job_id": spec["job_id"],
        "status": status,
        "date": audit_key,
        "reason": (row or {}).get("reason") or "持久化账本已有认领记录，禁止重复执行",
    }


def _now() -> datetime:
    return datetime.now(SCHEDULER_TIMEZONE)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _scheduler_event_listener(event) -> None:
    """Keep a small in-memory operational record for the status endpoint."""
    job_id = getattr(event, "job_id", None)
    if not job_id:
        return
    now = _now()
    state = _job_state.setdefault(job_id, {})
    state["last_event_at"] = now
    scheduled = getattr(event, "scheduled_run_time", None)
    state["last_scheduled_run_time"] = scheduled
    if event.code == EVENT_JOB_EXECUTED:
        state.update(
            status="success",
            last_success_at=now,
            last_error=None,
            last_error_type=None,
        )
    elif event.code == EVENT_JOB_MISSED:
        state.update(status="missed", last_error="misfire")
    elif event.code == EVENT_JOB_ERROR:
        exception = getattr(event, "exception", None)
        state.update(
            status="error",
            last_error=str(exception)[:300] if exception else "job_error",
            last_error_type=type(exception).__name__ if exception else None,
        )


async def scheduler_heartbeat() -> dict[str, Any]:
    global _scheduler_heartbeat_at
    _scheduler_heartbeat_at = _now()
    return {"status": "alive", "heartbeat_at": _iso(_scheduler_heartbeat_at)}


async def run_data_retention() -> dict[str, Any] | None:
    """Prune bounded derived snapshots without touching long-lived research data."""
    from services.data_retention import data_retention_service

    try:
        result = await data_retention_service.run()
        print(f"[Scheduler] 数据留存清理完成: 删除{result.get('deleted_rows', 0)}行")
        return result
    except Exception as exc:
        print(f"[Scheduler] 数据留存清理失败: {type(exc).__name__}")
        return None


def scheduler_status() -> dict[str, Any]:
    """Return a cheap operational view suitable for health probes and UI use."""
    now = _now()
    jobs = []
    try:
        scheduled_jobs = scheduler.get_jobs()
    except Exception:
        scheduled_jobs = []
    for job in scheduled_jobs:
        state = _job_state.get(job.id, {})
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": _iso(job.next_run_time) if job.next_run_time else None,
            "status": state.get("status", "scheduled"),
            "last_success_at": _iso(state.get("last_success_at")),
            "last_scheduled_run_time": _iso(state.get("last_scheduled_run_time")),
            "last_error": state.get("last_error"),
        })
    return {
        "status": "running" if scheduler.running else "stopped",
        "timezone": "Asia/Shanghai",
        "checked_at": now.isoformat(),
        "started_at": _iso(_scheduler_started_at),
        "heartbeat_at": _iso(_scheduler_heartbeat_at),
        "heartbeat_age_seconds": (
            round((now - _scheduler_heartbeat_at).total_seconds(), 1)
            if _scheduler_heartbeat_at else None
        ),
        "job_count": len(jobs),
        "jobs": jobs,
        "startup_audit": list(_startup_audit_state.values()),
    }


async def resume_incomplete_backfills() -> list[int]:
    """Restart persisted cache work after a transient worker or database failure."""
    from services.history_cache import history_cache

    try:
        resumed = await history_cache.resume_incomplete_runs()
        if resumed:
            print(f"[Scheduler] 已恢复历史数据回补任务: {resumed}")
        return resumed
    except Exception as exc:
        # The next interval retries a temporary database/DNS outage.
        print(f"[Scheduler] 历史数据回补恢复失败: {type(exc).__name__}")
        return []


async def resume_incomplete_fqe_syncs() -> list[int]:
    from services.fqe_reference_data import fqe_reference_data

    try:
        resumed = await fqe_reference_data.resume_incomplete_runs()
        if resumed:
            print(f"[Scheduler] 已恢复FQE审计数据任务: {resumed}")
        return resumed
    except Exception as exc:
        print(f"[Scheduler] FQE审计数据恢复失败: {type(exc).__name__}")
        return []


async def refresh_fqe_audit_data():
    """Append the latest PE snapshot and refresh strategic market evidence."""
    from services.fqe_reference_data import fqe_reference_data

    try:
        coverage = await fqe_reference_data.coverage()
        full = not coverage.get("security_total") or not coverage.get("valuation_series")
        return await fqe_reference_data.queue_sync(full=full, years=3, force=False)
    except Exception as exc:
        print(f"[Scheduler] FQE审计数据更新失败: {type(exc).__name__}")
        return None


async def refresh_ai_robot_short():
    from services.ai_robot import ai_robot_service

    try:
        return await ai_robot_service.refresh("short", trigger="schedule", background=False)
    except Exception as exc:
        print(f"[Scheduler] AI机器人短期池刷新失败: {type(exc).__name__}")
        return None


async def refresh_ai_robot_long():
    from services.ai_robot import ai_robot_service

    try:
        return await ai_robot_service.refresh("long", trigger="schedule", background=False)
    except Exception as exc:
        print(f"[Scheduler] AI机器人长期池刷新失败: {type(exc).__name__}")
        return None


async def check_ai_robot_anomalies():
    from services.ai_robot import ai_robot_service

    try:
        alerts = await ai_robot_service.check_anomalies()
        if alerts:
            print(f"[Scheduler] AI机器人池异常提醒: {len(alerts)} 条")
        return alerts
    except Exception as exc:
        print(f"[Scheduler] AI机器人池异常检查失败: {type(exc).__name__}")
        return []


async def snapshot_ai_robot_performance():
    from services.ai_robot import ai_robot_service

    try:
        return await ai_robot_service.record_performance_snapshot()
    except Exception as exc:
        print(f"[Scheduler] AI机器人组合统计失败: {type(exc).__name__}")
        return None


async def refresh_personal_report_calendar():
    from services.report_calendar import report_calendar_service

    try:
        return await report_calendar_service.refresh_snapshot()
    except Exception as exc:
        print(f"[Scheduler] 财报日历刷新失败: {type(exc).__name__}")
        return None


async def capture_financial_pit_snapshot():
    from services.stock_features import stock_feature_service

    try:
        return await stock_feature_service.capture_financial_pit()
    except Exception as exc:
        print(f"[Scheduler] 公告日财务PIT快照失败: {type(exc).__name__}")
        return None


async def capture_market_auction_snapshot():
    from services.pit_market_data import pit_market_data_service

    try:
        result = await pit_market_data_service.capture_auction()
        print(f"[Scheduler] 全市场竞价PIT快照: {result}")
        return result
    except Exception as exc:
        print(f"[Scheduler] 全市场竞价PIT快照失败: {type(exc).__name__}")
        return None


async def refresh_topic_intraday_evidence():
    from services.topic_strength import topic_strength_service

    try:
        return await topic_strength_service.get(force=True)
    except Exception as exc:
        print(f"[Scheduler] 题材分时资金证据刷新失败: {type(exc).__name__}")
        return None


async def refresh_market_decision_execution_gate():
    """Pre-warm the shared market decision before simulated entry windows."""
    from services.market_decision_workbench import market_decision_workbench_service

    try:
        payload = await market_decision_workbench_service.get(force=True)
        meta = payload.get("meta") or {}
        cognition = payload.get("market_cognition") or {}
        print(
            "[Scheduler] 市场执行闸门已更新: "
            f"{meta.get('decision_date')} / {cognition.get('final_action')}"
        )
        return payload
    except Exception as exc:
        print(f"[Scheduler] 市场执行闸门预热失败: {type(exc).__name__}")
        return None


async def refresh_forecast_v5():
    """Pre-compute the V5 forecast at each documented decision window."""
    from services.forecast_v5 import forecast_v5_service

    try:
        payload = await forecast_v5_service.dashboard(force=True)
        print(
            "[Scheduler] V5前瞻预测已更新: "
            f"{payload.get('forecast_date')} / {payload.get('phase')} / "
            f"完整度{(payload.get('data_health') or {}).get('completeness_pct')}%"
        )
        return payload
    except Exception as exc:
        print(f"[Scheduler] V5前瞻预测更新失败: {type(exc).__name__}")
        return None


async def refresh_v51_dashboard():
    """Warm the bounded V5.1 evidence summary without blocking the forecast."""
    from services.v51_microstructure_service import v51_microstructure_service

    try:
        payload = await v51_microstructure_service.auction_dashboard(refresh=True)
        print(
            "[Scheduler] V5.1微结构快照已更新: "
            f"{payload.get('trade_date')} / {payload.get('quality', {}).get('status')}"
        )
        return payload
    except Exception as exc:
        print(f"[Scheduler] V5.1微结构更新失败: {type(exc).__name__}")
        return None


async def refresh_event_radar():
    """Refresh free-source events; failures are isolated from market data jobs."""
    from services.event_radar import event_radar_service

    try:
        payload = await event_radar_service.refresh(force=True)
        print(f"[Scheduler] 事件雷达已更新: {payload.get('count', 0)} 条")
        return payload
    except Exception as exc:
        print(f"[Scheduler] 事件雷达更新失败: {type(exc).__name__}")
        return None


async def refresh_market_way_policy_source():
    """Keep official policy evidence warm before and during the trading day."""
    from services.market_way_v4 import market_way_v4_service

    try:
        return await market_way_v4_service.refresh_policy_source()
    except Exception as exc:
        print(f"[Scheduler] V4官方政策源更新失败: {type(exc).__name__}")
        return None


async def refresh_market_way_data_sources():
    """Rebuild policy, industry, financial, and PIT caches after disclosure sync."""
    from services.market_way_v4 import market_way_v4_service

    try:
        return await market_way_v4_service.refresh_sources(background=False)
    except Exception as exc:
        print(f"[Scheduler] V4数据闭环更新失败: {type(exc).__name__}")
        return None


async def refresh_dragon_board_cache():
    from services.dragon_board import dragon_board_service

    try:
        return await dragon_board_service.refresh()
    except Exception as exc:
        print(f"[Scheduler] 龙虎榜盘后缓存失败: {type(exc).__name__}")
        return None


async def refresh_margin_leverage_cache():
    """Refresh T-close/T+1 margin disclosures without blocking the app."""
    from services.margin_leverage import margin_leverage_service

    try:
        return await margin_leverage_service.sync(full=True, prewarm=True)
    except Exception as exc:
        print(f"[Scheduler] 两融杠杆中心更新失败，保留最近缓存: {type(exc).__name__}")
        return None


async def refresh_strong_stock_v21_bridge():
    """Persist the V2.1 Shadow bridge after the daily market cache lands."""
    from services.strong_stock_v21 import strong_stock_v21_service

    try:
        return await strong_stock_v21_service.daily_review()
    except Exception as exc:
        print(f"[Scheduler] 强势股V2.1桥接层更新失败，保留最近快照: {type(exc).__name__}")
        return None


async def run_overnight_preliminary_scan():
    from services.overnight_strategy import overnight_strategy_service

    try:
        return await overnight_strategy_service.start(
            "preliminary", trigger="schedule", background=False,
        )
    except Exception as exc:
        print(f"[Scheduler] 一夜持股14:30预扫描失败: {type(exc).__name__}")
        return None


async def run_overnight_entry_scan():
    from services.overnight_strategy import overnight_strategy_service

    try:
        return await overnight_strategy_service.start(
            "entry", trigger="schedule", background=False,
        )
    except Exception as exc:
        print(f"[Scheduler] 一夜持股尾盘复核失败: {type(exc).__name__}")
        return None


async def run_overnight_auction_watch():
    from services.overnight_strategy import AUCTION_STRATEGY_CONFIG, overnight_strategy_service

    try:
        return await overnight_strategy_service.start(
            "auction",
            trigger="schedule",
            background=False,
            strategy_id=AUCTION_STRATEGY_CONFIG["id"],
        )
    except Exception as exc:
        print(f"[Scheduler] 一夜持股09:25竞价盯盘失败: {type(exc).__name__}")
        return None


async def monitor_overnight_exits():
    from services.overnight_strategy import overnight_strategy_service

    try:
        return await overnight_strategy_service.start(
            "exit", trigger="schedule", background=False,
        )
    except Exception as exc:
        print(f"[Scheduler] 一夜持股早盘退出检查失败: {type(exc).__name__}")
        return None


async def force_overnight_exits():
    from services.overnight_strategy import overnight_strategy_service

    try:
        return await overnight_strategy_service.start(
            "force_exit", trigger="schedule", background=False,
        )
    except Exception as exc:
        print(f"[Scheduler] 一夜持股10:00强制退出失败: {type(exc).__name__}")
        return None


_CRITICAL_TASKS: tuple[dict[str, Any], ...] = (
    {
        "job_id": "opening_market_snapshot",
        "run_key": "market_data_opening",
        "label": "开盘后市场数据采集",
        "scheduled": time(9, 40),
        "deadline": time(11, 20),
        "handler": "run_market_data_collection",
    },
    {
        "job_id": "midday_collection",
        "run_key": "market_data_midday",
        "label": "午间市场数据采集",
        "scheduled": time(11, 35),
        "deadline": time(13, 20),
        "handler": "run_market_data_collection",
    },
    {
        "job_id": "daily_collection",
        "run_key": "market_data_close",
        "label": "盘后市场数据采集",
        "scheduled": time(15, 20),
        "deadline": time(18, 0),
        "handler": "run_market_data_collection",
    },
    {
        "job_id": "market_auction_pit_timeline",
        "run_key": "market_auction_capture",
        "normal_run_key": "market_auction_capture_timeline",
        "label": "竞价时间序列",
        "scheduled": time(9, 15),
        "deadline": time(9, 27),
        "handler": "capture_market_auction_snapshot",
        "blocked_after_deadline": True,
        "startup_compensate": True,
    },
    {
        "job_id": "market_auction_pit",
        "run_key": "market_auction_capture",
        "label": "09:25竞价快照",
        "scheduled": time(9, 25),
        "deadline": time(9, 27),
        "handler": "capture_market_auction_snapshot",
        "blocked_after_deadline": True,
        "startup_compensate": True,
    },
    {
        "job_id": "midday_ai_research",
        "label": "午间AI研究",
        "scheduled": time(11, 42),
        "deadline": time(13, 20),
        "handler": "run_midday_research",
    },
    {
        "job_id": "overnight_preliminary_scan",
        "label": "一夜持股预扫描",
        "scheduled": time(14, 30),
        "deadline": time(14, 50),
        "handler": "run_overnight_preliminary_scan",
        "startup_compensate": False,
    },
    {
        "job_id": "overnight_entry_scan",
        "label": "一夜持股尾盘复核",
        "scheduled": time(14, 55),
        "deadline": time(15, 0),
        "handler": "run_overnight_entry_scan",
        "startup_compensate": False,
    },
    {
        "job_id": "midday_close_validation",
        "label": "午间研究盘后验证",
        "scheduled": time(15, 50),
        "deadline": time(18, 0),
        "handler": "validate_midday_research",
        "startup_compensate": True,
    },
)


async def audit_missed_critical_jobs(now: datetime | None = None) -> dict[str, Any]:
    """Audit today's windows and recover only durable, safe-to-replay work.

    The durable ledger is consulted before process-local state. A research/data
    handler must first win the ``run_date + run_key`` insert race; an existing
    row, including an unfinished CLAIMED row, is never executed again.
    """
    current = now or _now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=SCHEDULER_TIMEZONE)
    else:
        current = current.astimezone(SCHEDULER_TIMEZONE)
    audit_key = current.date().isoformat()
    current_naive = current.replace(tzinfo=None)
    if current.weekday() >= 5:
        result = {"date": audit_key, "status": "NON_TRADING_DAY", "items": []}
        _startup_audit_state[audit_key] = result
        return result

    items: list[dict[str, Any]] = []
    degraded = False

    def remember(item_key: str, item: dict[str, Any]) -> None:
        _startup_audit_state[item_key] = item
        items.append(item)

    for spec in _CRITICAL_TASKS:
        scheduled_at = datetime.combine(current.date(), spec["scheduled"], tzinfo=SCHEDULER_TIMEZONE)
        deadline_at = datetime.combine(current.date(), spec["deadline"], tzinfo=SCHEDULER_TIMEZONE)
        item_key = f"{audit_key}:{spec['job_id']}"
        run_key = str(spec.get("run_key", spec["job_id"]))

        # This lookup intentionally comes before _job_state. It is the only
        # state that survives a Render process restart.
        ledger = await _ledger_get(current.date(), run_key)
        if not ledger.get("available"):
            degraded = True
            if item_key in _startup_audit_state:
                items.append(_startup_audit_state[item_key])
                continue
            if current < scheduled_at:
                remember(item_key, {"job_id": spec["job_id"], "status": "PENDING", "date": audit_key, "reason": "持久化账本不可用，尚未进入执行窗口"})
            elif current > deadline_at:
                remember(item_key, {"job_id": spec["job_id"], "status": "MISSED_WINDOW_BLOCKED", "date": audit_key, "reason": "当日执行窗口已结束；持久化账本不可用，未执行补偿"})
            elif not spec.get("startup_compensate", True):
                remember(item_key, {"job_id": spec["job_id"], "status": "STARTUP_REPLAY_BLOCKED", "date": audit_key, "reason": "持久化账本不可用，执行类任务禁止自动重放"})
            else:
                remember(item_key, {"job_id": spec["job_id"], "status": "DEGRADED", "date": audit_key, "reason": "持久化账本不可用，安全研究/数据补偿已跳过"})
            continue

        if ledger.get("row"):
            remember(item_key, _ledger_item(spec, audit_key, ledger["row"]))
            continue

        # This protects repeated calls in one process even if a database row
        # was removed externally between two audits.
        if item_key in _startup_audit_state:
            items.append(_startup_audit_state[item_key])
            continue
        if current < scheduled_at:
            remember(item_key, {"job_id": spec["job_id"], "status": "PENDING", "date": audit_key})
            continue

        if current > deadline_at:
            terminal = await _ledger_claim(
                current.date(), run_key, spec["job_id"],
                status="MISSED_WINDOW_BLOCKED", started_at=None,
                completed_at=current_naive,
                reason="当日执行窗口已结束，禁止用昨日结果代替今日数据",
            )
            if not terminal.get("available"):
                degraded = True
                remember(item_key, {"job_id": spec["job_id"], "status": "MISSED_WINDOW_BLOCKED", "date": audit_key, "reason": "当日执行窗口已结束；账本写入失败，未执行补偿"})
            elif terminal.get("row"):
                remember(item_key, _ledger_item(spec, audit_key, terminal["row"]))
            else:
                remember(item_key, {"job_id": spec["job_id"], "status": "MISSED_WINDOW_BLOCKED", "date": audit_key, "reason": "当日执行窗口已结束，禁止用昨日结果代替今日数据"})
            continue

        if not spec.get("startup_compensate", True):
            terminal = await _ledger_claim(
                current.date(), run_key, spec["job_id"],
                status="STARTUP_REPLAY_BLOCKED", started_at=None,
                completed_at=current_naive,
                reason="跨进程重启无法证明执行类任务尚未执行，禁止自动重放",
            )
            if not terminal.get("available"):
                degraded = True
                remember(item_key, {"job_id": spec["job_id"], "status": "STARTUP_REPLAY_BLOCKED", "date": audit_key, "reason": "账本写入失败，执行类任务禁止自动重放"})
            elif terminal.get("row"):
                remember(item_key, _ledger_item(spec, audit_key, terminal["row"]))
            else:
                remember(item_key, {"job_id": spec["job_id"], "status": "STARTUP_REPLAY_BLOCKED", "date": audit_key, "reason": "跨进程重启无法证明执行类任务尚未执行，禁止自动重放"})
            continue

        claim = await _ledger_claim(
            current.date(), run_key, spec["job_id"],
            status="CLAIMED", started_at=current_naive,
            completed_at=None,
            reason="启动漏跑审计已原子认领，等待安全补偿执行",
        )
        if not claim.get("available"):
            degraded = True
            remember(item_key, {"job_id": spec["job_id"], "status": "DEGRADED", "date": audit_key, "reason": "持久化账本不可用，安全研究/数据补偿已跳过"})
            continue
        if not claim.get("claimed"):
            # Includes a unique-key race with another Render process and an
            # already-claimed row from a previous process.
            remember(item_key, _ledger_item(spec, audit_key, claim.get("row")))
            continue

        handler: Callable[[], Awaitable[Any]] = globals()[spec["handler"]]
        try:
            value = await handler()
            final_status = "COMPENSATED" if value is not None else "COMPENSATION_EMPTY"
            final_reason = "启动漏跑审计在当日窗口内补偿执行" if value is not None else "补偿任务执行完成但未产生结果"
        except Exception as exc:
            final_status = "COMPENSATION_FAILED"
            final_reason = f"{type(exc).__name__}: {str(exc)[:180]}"
        updated = await _ledger_update(
            current.date(), run_key,
            status=final_status,
            completed_at=current_naive,
            reason=final_reason,
        )
        if not updated.get("available") or not updated.get("updated"):
            degraded = True
            item = {"job_id": spec["job_id"], "status": "DEGRADED", "date": audit_key, "reason": f"补偿已执行但账本完成状态未确认：{updated.get('reason') or 'unknown'}"}
        else:
            item = {"job_id": spec["job_id"], "status": final_status, "date": audit_key, "reason": final_reason}
        _startup_compensation_state[run_key] = {"status": item["status"], "reason": item["reason"]}
        remember(item_key, item)

    result = {"date": audit_key, "status": "DEGRADED" if degraded else "AUDITED", "items": items}
    _startup_audit_state[audit_key] = result
    return result


async def run_startup_missed_task_audit() -> dict[str, Any]:
    return await audit_missed_critical_jobs()


async def run_midday_research():
    from services.midday_research import midday_research_service

    try:
        return await midday_research_service.start(force=False, background=False)
    except Exception as exc:
        print(f"[Scheduler] 午间AI研究失败: {type(exc).__name__}")
        return None


async def track_midday_research(checkpoint: str):
    from services.midday_research import midday_research_service

    try:
        return await midday_research_service.track(checkpoint, force_quote=True)
    except (LookupError, ValueError) as exc:
        print(f"[Scheduler] 午间候选{checkpoint}跟踪跳过: {exc}")
        return None
    except Exception as exc:
        print(f"[Scheduler] 午间候选{checkpoint}跟踪失败: {type(exc).__name__}")
        return None


async def validate_midday_research():
    from services.midday_research import midday_research_service

    try:
        return await midday_research_service.validate_pending()
    except Exception as exc:
        print(f"[Scheduler] 午间研究盘后验证失败: {type(exc).__name__}")
        return []


async def capture_decision_workbench_window(phase: str):
    from services.decision_workbench_2026 import decision_workbench_2026_service

    try:
        result = await decision_workbench_2026_service.capture(phase, force=True)
        print(f"[Scheduler] 2026决策窗口已冻结: {phase} / {result.get('id')}")
        return result
    except RuntimeError as exc:
        print(f"[Scheduler] 2026决策窗口{phase}跳过: {exc}")
        return None
    except Exception as exc:
        print(f"[Scheduler] 2026决策窗口{phase}失败: {type(exc).__name__}")
        return None


async def close_and_validate_decision_workbench():
    from services.decision_workbench_2026 import decision_workbench_2026_service

    try:
        snapshot = await decision_workbench_2026_service.capture("close_review", force=True)
        result = await decision_workbench_2026_service.validate()
        return {"snapshot": snapshot, "validation": result}
    except RuntimeError as exc:
        print(f"[Scheduler] 2026决策盘后验证跳过: {exc}")
        return None
    except Exception as exc:
        print(f"[Scheduler] 2026决策盘后验证失败: {type(exc).__name__}")
        return None


async def run_market_data_collection() -> dict[str, Any] | None:
    """Collect and warm the shared market snapshot for a current window."""
    from services.data_sync import data_sync

    print(f"[Scheduler] 开始市场数据采集: {_now().isoformat()}")
    try:
        result = await data_sync.sync_market_snapshot()
        from services.pit_market_data import pit_market_data_service
        universe = await pit_market_data_service.capture_universe()
        result["pit_universe"] = universe
        from api.routes import refresh_market_overview_after_sync
        from services.ai_robot import ai_robot_service
        result["overview"] = (await refresh_market_overview_after_sync(result)).get("data", {})
        await ai_robot_service.warm_market_cache()
        print(f"[Scheduler] 市场数据采集完成: {result}")
        return result
    except Exception as exc:
        print(f"[Scheduler] 市场数据采集失败: {type(exc).__name__}")
        return None


def _critical_spec(job_id: str) -> dict[str, Any] | None:
    return next((item for item in _CRITICAL_TASKS if item["job_id"] == job_id), None)


def _normal_critical_run_key(spec: dict[str, Any], now: datetime) -> str:
    base = str(spec.get("normal_run_key") or spec.get("run_key") or spec["job_id"])
    if spec.get("normal_run_key"):
        return f"{base}:{now.strftime('%H%M')}"
    return base


async def run_scheduled_critical_job(
    job_id: str,
    handler: Callable[[], Awaitable[Any]],
) -> Any:
    """Run a normal critical job only after durable same-day claim."""
    spec = _critical_spec(job_id)
    if spec is None:
        return {"status": "DEGRADED", "job_id": job_id, "reason": "未注册的关键调度任务"}
    current = _now()
    if current.weekday() >= 5:
        return {"status": "NON_TRADING_DAY", "job_id": job_id}
    current_naive = current.replace(tzinfo=None)
    run_key = _normal_critical_run_key(spec, current)
    ledger = await _ledger_get(current.date(), run_key)
    if not ledger.get("available"):
        return {
            "status": "DEGRADED",
            "job_id": job_id,
            "reason": "持久化账本不可用，正常关键任务未执行",
        }
    if ledger.get("row"):
        return {
            "status": "SKIPPED_ALREADY_LEDGER",
            "job_id": job_id,
            "ledger_status": ledger["row"].get("status"),
            "reason": ledger["row"].get("reason") or "当日已有持久化执行记录",
        }

    claim = await _ledger_claim(
        current.date(), run_key, job_id,
        status="CLAIMED",
        started_at=current_naive,
        completed_at=None,
        reason="正常APScheduler执行已原子认领",
    )
    if not claim.get("available"):
        return {
            "status": "DEGRADED",
            "job_id": job_id,
            "reason": "持久化账本不可用，正常关键任务未执行",
        }
    if not claim.get("claimed"):
        return {
            "status": "SKIPPED_ALREADY_CLAIMED",
            "job_id": job_id,
            "ledger_status": (claim.get("row") or {}).get("status"),
            "reason": "另一进程已认领当日关键任务，禁止重复执行",
        }

    try:
        result = await handler()
        final_status = "COMPLETED"
        final_reason = "正常APScheduler任务执行完成" if result is not None else "正常APScheduler任务执行完成但未产生结果"
    except Exception as exc:
        result = None
        final_status = "FAILED"
        final_reason = f"{type(exc).__name__}: {str(exc)[:180]}"
    updated = await _ledger_update(
        current.date(), run_key,
        status=final_status,
        completed_at=_now().replace(tzinfo=None),
        reason=final_reason,
    )
    if not updated.get("available") or not updated.get("updated"):
        return {
            "status": "DEGRADED",
            "job_id": job_id,
            "executed": True,
            "reason": f"任务已执行但账本终态未确认：{updated.get('reason') or 'unknown'}",
        }
    if final_status == "FAILED":
        return {"status": final_status, "job_id": job_id, "reason": final_reason}
    return result


def _wrap_critical_job(job_id: str, handler: Callable[[], Awaitable[Any]]) -> Callable[[], Awaitable[Any]]:
    async def wrapped() -> Any:
        return await run_scheduled_critical_job(job_id, handler)

    wrapped.__name__ = f"critical_{job_id}"
    return wrapped


async def start_scheduler(data_collector=None, db_session=None):
    global _scheduler_listener_bound_to, _scheduler_started_at
    from services.data_sync import data_sync

    _scheduler_started_at = _now()
    scheduler_identity = id(scheduler)
    if _scheduler_listener_bound_to != scheduler_identity:
        scheduler.add_listener(
            _scheduler_event_listener,
            EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
        )
        _scheduler_listener_bound_to = scheduler_identity

    critical_handlers = {
        spec["job_id"]: _wrap_critical_job(
            spec["job_id"],
            globals()[spec["handler"]],
        )
        for spec in _CRITICAL_TASKS
    }

    async def startup_cache_recovery():
        """Refresh the lightweight overview first and avoid a cold-start data stampede."""
        latest_date = None
        try:
            from services.data_collector import shanghai_now
            stats = await data_sync.get_cache_stats()
            latest_value = (stats.get("stock_bars") or {}).get("to")
            latest_date = date.fromisoformat(str(latest_value)) if latest_value else None
            now = shanghai_now()
            cache_is_recent = bool(latest_date and 0 <= (now.date() - latest_date).days <= 3)
            if cache_is_recent:
                from api.routes import refresh_market_overview_after_sync
                overview = await refresh_market_overview_after_sync({"data_date": latest_date.isoformat()})
                print(f"[Scheduler] 冷启动使用近期行情缓存并重建速览: {latest_date}")
                return {"status": "cache_refreshed", "overview": overview.get("data", {})}
        except Exception as exc:
            print(f"[Scheduler] 冷启动缓存检查失败: {type(exc).__name__}")
        print(f"[Scheduler] 冷启动无近期行情缓存，延后至定时或手动全市场同步: {latest_date}")
        return {
            "status": "deferred",
            "data_date": latest_date.isoformat() if latest_date else None,
            "reason": "recent_stock_cache_unavailable",
        }

    scheduler.add_job(
        critical_handlers["daily_collection"],
        _cron_trigger(hour=15, minute=20, day_of_week="mon-fri"),
        id="daily_collection",
        name="每日盘后数据采集",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=1800,
    )
    scheduler.add_job(
        startup_cache_recovery,
        "date",
        run_date=datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(seconds=20),
        id="startup_cache_recovery",
        name="服务启动后缓存恢复",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        critical_handlers["opening_market_snapshot"],
        _cron_trigger(hour=9, minute=40, day_of_week="mon-fri"),
        id="opening_market_snapshot",
        name="开盘后全市场股票数与行情快照",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        critical_handlers["midday_collection"],
        _cron_trigger(hour=11, minute=35, day_of_week="mon-fri"),
        id="midday_collection",
        name="午间行情快照采集",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        critical_handlers["midday_ai_research"],
        _cron_trigger(hour=11, minute=42, day_of_week="mon-fri"),
        id="midday_ai_research", name="午间AI战术研究", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=900,
    )
    scheduler.add_job(
        track_midday_research,
        _cron_trigger(hour=13, minute=30, day_of_week="mon-fri"),
        args=["13:30"],
        id="midday_track_1330", name="午间候选13:30固定样本跟踪", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=180,
    )
    scheduler.add_job(
        track_midday_research,
        _cron_trigger(hour=14, minute=0, day_of_week="mon-fri"),
        args=["14:00"],
        id="midday_track_1400", name="午间候选14:00固定样本跟踪", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=180,
    )
    scheduler.add_job(
        track_midday_research,
        _cron_trigger(hour=14, minute=31, day_of_week="mon-fri"),
        args=["14:30"],
        id="midday_track_1430", name="午间候选14:30固定样本跟踪", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=180,
    )
    scheduler.add_job(
        track_midday_research,
        _cron_trigger(hour=14, minute=58, day_of_week="mon-fri"),
        args=["14:55"],
        id="midday_track_1455", name="午间候选14:55正式筛选对照", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=180,
    )
    scheduler.add_job(
        critical_handlers["midday_close_validation"],
        _cron_trigger(hour=15, minute=50, day_of_week="mon-fri"),
        id="midday_close_validation", name="午间研究盘后验证与AI学习", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=1800,
    )
    scheduler.add_job(
        capture_decision_workbench_window,
        _cron_trigger(hour=10, minute=40, day_of_week="mon-fri"),
        args=["morning_1040"],
        id="decision_2026_morning_freeze", name="2026工作台10:40状态冻结", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=300,
    )
    scheduler.add_job(
        capture_decision_workbench_window,
        _cron_trigger(hour=11, minute=44, day_of_week="mon-fri"),
        args=["midday_1142"],
        id="decision_2026_midday_freeze", name="2026工作台午间研究快照", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=600,
    )
    scheduler.add_job(
        capture_decision_workbench_window,
        _cron_trigger(hour=13, minute=32, day_of_week="mon-fri"),
        args=["hypothesis_1330"],
        id="decision_2026_hypothesis_1330", name="2026工作台13:30反证快照", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=300,
    )
    scheduler.add_job(
        capture_decision_workbench_window,
        _cron_trigger(hour=14, minute=2, day_of_week="mon-fri"),
        args=["hypothesis_1400"],
        id="decision_2026_hypothesis_1400", name="2026工作台14:00反证快照", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=300,
    )
    scheduler.add_job(
        capture_decision_workbench_window,
        _cron_trigger(hour=14, minute=40, day_of_week="mon-fri"),
        args=["tail_1440"],
        id="decision_2026_tail_1440", name="2026工作台14:40尾盘决策快照", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=180,
    )
    scheduler.add_job(
        capture_decision_workbench_window,
        _cron_trigger(hour=14, minute=57, day_of_week="mon-fri"),
        args=["tail_1455"],
        id="decision_2026_tail_1455", name="2026工作台14:55执行确认快照", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=180,
    )
    scheduler.add_job(
        close_and_validate_decision_workbench,
        _cron_trigger(hour=15, minute=55, day_of_week="mon-fri"),
        id="decision_2026_close_validation", name="2026工作台盘后错误归因", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=1800,
    )
    for minute, job_id, label in (
        (5, "forecast_v5_premarket", "V5盘前预测"),
        (40, "forecast_v5_morning", "V5早盘预测"),
    ):
        scheduler.add_job(
            refresh_forecast_v5,
            _cron_trigger(hour=9 if minute == 5 else 10, minute=minute, day_of_week="mon-fri"),
            id=job_id, name=label, replace_existing=True,
            coalesce=True, max_instances=1, misfire_grace_time=600,
        )
    for hour, minute, job_id, label in (
        (11, 30, "forecast_v5_midday", "V5午间预测"),
        (13, 30, "forecast_v5_afternoon", "V5午后情景预测"),
        (14, 40, "forecast_v5_tail", "V5尾盘前瞻预测"),
        (15, 10, "forecast_v5_close", "V5收盘状态预测"),
    ):
        scheduler.add_job(
            refresh_forecast_v5,
            _cron_trigger(hour=hour, minute=minute, day_of_week="mon-fri"),
            id=job_id, name=label, replace_existing=True,
            coalesce=True, max_instances=1, misfire_grace_time=900,
        )
    scheduler.add_job(
        resume_incomplete_backfills,
        "interval",
        minutes=1,
        id="resume_history_backfill",
        name="恢复未完成历史数据回补",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        resume_incomplete_fqe_syncs,
        "interval",
        minutes=2,
        id="resume_fqe_data_sync",
        name="恢复未完成FQE审计数据任务",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=120,
    )
    scheduler.add_job(
        refresh_fqe_audit_data,
        _cron_trigger(hour=16, minute=10, day_of_week="mon-fri"),
        id="fqe_audit_data_close",
        name="FQE上市历史、PE分位与市场证据盘后更新",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=1800,
    )

    async def quant_signal_scan():
        """Run after each session opens without blocking ordinary API traffic."""
        from services.data_collector import shanghai_now

        if shanghai_now().weekday() >= 5:
            return
        try:
            from quant.signals import quant_signal_service

            job = await quant_signal_service.start_scan(force=False, scheduled_only=True)
            print(f"[Scheduler] 量化信号扫描任务: {job.get('job_id')}")
        except Exception as exc:
            print(f"[Scheduler] 量化信号扫描失败: {type(exc).__name__}")

    scheduler.add_job(
        quant_signal_scan,
        _cron_trigger(hour=9, minute=32, day_of_week="mon-fri"),
        id="quant_signal_morning", name="量化信号早盘扫描", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=300,
    )

    scheduler.add_job(
        refresh_ai_robot_short,
        _cron_trigger(hour=15, minute=45, day_of_week="mon-fri"),
        id="ai_robot_short_daily", name="AI机器人短期池每日盘后刷新", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=1800,
    )
    scheduler.add_job(
        refresh_ai_robot_long,
        _cron_trigger(hour=16, minute=20, day_of_week="mon-fri"),
        id="ai_robot_long_daily", name="AI机器人长期池每日盘后刷新", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=1800,
    )
    scheduler.add_job(
        check_ai_robot_anomalies,
        _cron_trigger(hour=9, minute=15, day_of_week="mon-fri"),
        id="ai_robot_anomaly_check", name="AI机器人池盘前异常检查", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=900,
    )
    scheduler.add_job(
        snapshot_ai_robot_performance,
        _cron_trigger(hour=16, minute=50, day_of_week="mon-fri"),
        id="ai_robot_performance_close", name="AI机器人池每日盈亏与复盘", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=900,
    )
    scheduler.add_job(
        refresh_dragon_board_cache,
        _cron_trigger(hour=15, minute=35, day_of_week="mon-fri"),
        id="dragon_board_close_cache", name="龙虎榜盘后缓存", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=1800,
    )
    scheduler.add_job(
        refresh_margin_leverage_cache,
        _cron_trigger(hour=18, minute=30, day_of_week="mon-fri"),
        id="margin_leverage_first_disclosure", name="两融杠杆首次披露同步", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=3600,
    )
    scheduler.add_job(
        refresh_margin_leverage_cache,
        _cron_trigger(hour=20, minute=30, day_of_week="mon-fri"),
        id="margin_leverage_final_disclosure", name="两融杠杆晚间补充同步", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=3600,
    )
    scheduler.add_job(
        refresh_strong_stock_v21_bridge,
        _cron_trigger(hour=15, minute=45, day_of_week="mon-fri"),
        id="strong_stock_v21_bridge_close", name="强势股V2.1盘后桥接与机会快照", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=1800,
    )
    scheduler.add_job(
        refresh_personal_report_calendar,
        _cron_trigger(hour=8, minute=20, day_of_week="mon-fri"),
        id="personal_report_calendar", name="个人池财报日历刷新", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=1800,
    )
    scheduler.add_job(
        capture_financial_pit_snapshot,
        _cron_trigger(hour=16, minute=35, day_of_week="mon-fri"),
        id="financial_pit_close", name="公告日财务PIT增量快照", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=3600,
    )
    scheduler.add_job(
        refresh_market_way_policy_source,
        _cron_trigger(hour="8,12", minute=5, day_of_week="mon-fri"),
        id="market_way_policy_source", name="V4官方政策证据更新", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=1800,
    )
    scheduler.add_job(
        refresh_market_way_data_sources,
        _cron_trigger(hour=16, minute=45, day_of_week="mon-fri"),
        id="market_way_data_sources", name="V4产业财务与市场数据闭环", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=3600,
    )
    scheduler.add_job(
        critical_handlers["market_auction_pit"],
        _cron_trigger(hour=9, minute=25, day_of_week="mon-fri"),
        id="market_auction_pit", name="全市场09:25竞价PIT快照", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=60,
    )
    # Capture the observed quote timestamp at several points.  The public
    # feed does not expose unmatched order quantities, so the V5.1 timeline
    # keeps those fields null rather than inferring them.
    scheduler.add_job(
        critical_handlers["market_auction_pit_timeline"],
        _cron_trigger(hour=9, minute="15-25", day_of_week="mon-fri"),
        id="market_auction_pit_timeline", name="V5.1竞价时间序列补采", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=60,
    )
    scheduler.add_job(
        refresh_v51_dashboard,
        _cron_trigger(hour="9,10,11,13,14,15", minute="0,30", day_of_week="mon-fri"),
        id="v51_microstructure_warm", name="V5.1微结构证据预热", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=180,
    )
    scheduler.add_job(
        refresh_event_radar,
        _cron_trigger(hour="8,9,10,11,12,13,14,15,16", minute="5,35", day_of_week="mon-fri"),
        id="event_radar_refresh", name="免费数据事件雷达刷新", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=300,
    )
    scheduler.add_job(
        refresh_topic_intraday_evidence,
        _cron_trigger(hour="9,10,14", minute="35,55", day_of_week="mon-fri"),
        id="topic_intraday_evidence", name="题材分时均价与主动资金证据", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=180,
    )
    scheduler.add_job(
        quant_signal_scan,
        _cron_trigger(hour=13, minute=2, day_of_week="mon-fri"),
        id="quant_signal_afternoon", name="量化信号午后扫描", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=300,
    )
    scheduler.add_job(
        critical_handlers["overnight_preliminary_scan"],
        _cron_trigger(hour=14, minute=30, day_of_week="mon-fri"),
        id="overnight_preliminary_scan", name="一夜持股14:30预扫描", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=180,
    )
    scheduler.add_job(
        refresh_market_decision_execution_gate,
        _cron_trigger(hour=14, minute=53, day_of_week="mon-fri"),
        id="market_execution_gate_tail", name="14:55前市场执行闸门预热", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=120,
    )
    scheduler.add_job(
        critical_handlers["overnight_entry_scan"],
        _cron_trigger(hour=14, minute=55, day_of_week="mon-fri"),
        id="overnight_entry_scan", name="一夜持股14:55入场复核", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=180,
    )
    scheduler.add_job(
        refresh_market_decision_execution_gate,
        _cron_trigger(hour=9, minute=23, day_of_week="mon-fri"),
        id="market_execution_gate_auction", name="09:25前市场执行闸门预热", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=60,
    )
    scheduler.add_job(
        run_overnight_auction_watch,
        _cron_trigger(hour=9, minute="24,25,26,27", day_of_week="mon-fri"),
        id="overnight_auction_watch", name="一夜持股09:25 AI竞价盯盘", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=60,
    )
    scheduler.add_job(
        monitor_overnight_exits,
        _cron_trigger(hour=9, minute="31,40,50", day_of_week="mon-fri"),
        id="overnight_exit_monitor", name="一夜持股早盘退出监控", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=180,
    )
    scheduler.add_job(
        force_overnight_exits,
        _cron_trigger(hour=10, minute=0, day_of_week="mon-fri"),
        id="overnight_force_exit", name="一夜持股10:00强制退出", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=300,
    )

    scheduler.add_job(
        scheduler_heartbeat,
        "interval",
        seconds=30,
        id="scheduler_heartbeat",
        name="调度器心跳",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=90,
    )
    scheduler.add_job(
        run_data_retention,
        _cron_trigger(hour=3, minute=20),
        id="bounded_data_retention",
        name="高频与派生快照留存清理",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=7200,
    )
    scheduler.add_job(
        run_startup_missed_task_audit,
        "date",
        run_date=_now() + timedelta(seconds=2),
        id="startup_missed_task_audit",
        name="启动时当日关键任务漏跑审计",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=30,
    )

    if not scheduler.running:
        scheduler.start()
    print("[Scheduler] 定时任务已启动")
