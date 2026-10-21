"""健康：API、数据库、行情分别报告（AC-077：health 存活 ≠ 业务就绪）。"""
from __future__ import annotations

from ...domain import clock
from ...services import calendar_service, snapshot_service
from ..app import Ctx, handler
from ..errors import ApiError


def enabled_phases(app) -> list[str]:
    out = []
    for ph in ("P0", "P1", "P2", "P3", "P4"):
        ok, _ = app.feature_gate(ph)
        if ok:
            out.append(ph)
    return out


def health_payload(ctx: Ctx) -> dict:
    try:
        ctx.db.scalar("SELECT 1")
        db_ready = True
    except Exception:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "数据库不可读")
    missing = []
    snap = snapshot_service.latest(ctx.db, ctx.replay_id)
    cal = calendar_service.current(ctx.db) if ctx.replay_id is None else None
    if cal is None and ctx.replay_id is None:
        missing.append("trading_calendar")
    if snap is None:
        missing.append("market_snapshot")
        return {"status": "unavailable", "api_ready": True, "database_ready": db_ready, "latest_snapshot_id": None,
                "quality": "unavailable", "freshness": "unknown", "quote_date": None,
                "latest_completed_session_date": cal.latest_completed_session() if cal else None,
                "missing_inputs": missing, "checked_at": clock.utc_iso(), "enabled_phases": enabled_phases(ctx.app)}
    md = snap["metadata"]
    if ctx.replay_id is None:
        fr, why = snapshot_service.current_freshness(ctx.db, snap)
    else:
        fr, why = md["freshness"], md["freshness_reason"]
    missing += [f"snapshot:{m}" for m in md["missing_fields"]]
    if fr != "fresh":
        missing.append(f"freshness:{why}")
    status = "ready" if (md["quality"] == "complete" and fr == "fresh") else "degraded"
    return {"status": status, "api_ready": True, "database_ready": db_ready, "latest_snapshot_id": snap["snapshot_id"],
            "quality": md["quality"], "freshness": fr, "quote_date": md["quote_date"],
            "latest_completed_session_date": (cal.latest_completed_session() if cal else md["latest_completed_session_date"]),
            "missing_inputs": missing, "checked_at": clock.utc_iso(), "enabled_phases": enabled_phases(ctx.app)}


@handler("get_api_v1_health_data")
def health(ctx: Ctx):
    return 200, health_payload(ctx)
