"""P4 提醒 driver（ALERT-001 / ALERT-002）。

能力闸门按设计关闭（未核验分钟/逐笔权限、只有日级快照）：
  - AC-074 直接验证闸门关闭、无后台订阅、重启不自动恢复；
  - AC-072 / AC-073 需要一个已启用的前台观察会话作为前提——闸门拒绝启用，因此记 blocked（不是 pass）；
    同时用「合成已核验能力」跑一遍确定性引擎（断线降级、追补去重）作为工程自检证据，写进 result.engine_selftest。
"""
from __future__ import annotations

import threading

from panel.domain import clock
from panel.services import alert_service as al

from ..errors import Blocked


def _cap_from_fixture(ctx, inp=None) -> dict:
    f = ctx.fixture("alert_feed")
    cap = dict(al.DEFAULT_CAPABILITY)
    cap.update({"provider_permission_verified": f["provider_permission_verified"], "feed_precision": f["feed_precision"],
                "explicit_user_action": f["explicit_user_action"]})
    if inp:
        for k in ("provider_permission_verified", "feed_precision", "explicit_user_action"):
            if k in inp:
                cap[k] = inp[k]
    return cap


def _alert_threads() -> int:
    return sum(1 for t in threading.enumerate() if "alert" in t.name.lower() or "subscri" in t.name.lower())


def alert_capability_preflight(ctx, inp):
    h = ctx.harness()
    cap = _cap_from_fixture(ctx, inp)
    ev = al.evaluate(cap)
    st, obj, _ = ctx.client().get("/api/v1/alerts")
    ctx.result.update({"alert_enabled": ev["alert_enabled"] and st == 200, "gate_reasons": ev["reasons"],
                       "alerts_http_status": st, "alerts_error_code": obj["error"]["code"] if st >= 400 else None,
                       "background_subscriptions": _alert_threads() + int(ctx.scalar(
                           "SELECT COUNT(*) FROM alert_sessions WHERE state='active'") or 0),
                       "level2_inferred_from_daily": ev["level2_inferred_from_daily"]})

    def probe(c):
        st2, obj2, _ = c.client().get("/api/v1/alerts")
        active = int(c.scalar("SELECT COUNT(*) FROM alert_sessions WHERE state='active'") or 0)
        c.result["auto_resume_after_restart"] = active > 0 or st2 == 200
        c.result["background_subscriptions"] = max(c.result.get("background_subscriptions", 0), _alert_threads() + active)
    ctx.state.setdefault("post_restart_probes", []).append(probe)


def _selftest(ctx) -> dict:
    """合成「已核验分钟级权限 + 用户显式启用」能力下的引擎自检（不改变用例状态）。"""
    f = ctx.fixture("alert_feed")
    cap = {**al.DEFAULT_CAPABILITY, "provider_permission_verified": True, "permission_evidence": "synthetic_selftest",
           "feed_precision": "minute", "explicit_user_action": True}
    ctx.ensure("snapshot_final")
    keep = clock.now()
    clock.set_fixed("2026-10-19T02:01:00Z")
    try:
        obs = al.AlertObservation(ctx.db, cap, None, None, "selftest_rule", ctx.alias("snapshot_fixture_final"))
        obs.start(True)
        res = [obs.on_feed_event(e["provider_event_id"], e["provider_time"]) for e in
               [f["events"][0], f["events"][0], f["events"][1]]]
        stored = len(obs.stored)
        realtime = len(obs.realtime_notifications)
        old_hist = any(s["historical"] for s in obs.stored if "feed_event_old" in s["message"])
        obs.disconnect()
        obs.advance(60)
        after = obs.on_feed_event("feed_event_after_disconnect", clock.utc_iso())
        st = obs.status()
        obs.stop()
        return {"unique_stored_event_count_before_disconnect": stored, "realtime_notification_count": realtime,
                "old_event_marked_historical": old_hist, "connection_state_after_disconnect": st["connection_state"],
                "new_direction_alert_after_disconnect": after["stored"], "last_observation_labeled": st["last_observation_labeled"],
                "no_risk_assertion": st["no_risk_assertion"]}
    finally:
        clock.set_fixed(keep)


def _blocked(ctx, what: str):
    cap = _cap_from_fixture(ctx)
    ev = al.evaluate({**cap, "explicit_user_action": True})
    obs = al.AlertObservation(ctx.db, cap, None, None, "fixture_rule", ctx.alias("snapshot_fixture_final"))
    r = obs.start(True)
    ctx.result["gate_refused"] = obs.state == "refused"
    ctx.result["gate_reasons"] = r["reasons"]
    ctx.result["engine_selftest"] = _selftest(ctx)
    raise Blocked(f"P4 能力闸门按设计关闭：{', '.join(ev['reasons'])}（fixture alert_feed.provider_permission_verified="
                  f"{cap['provider_permission_verified']}，feed_precision={cap['feed_precision']}）。{what} 需要一个已启用的"
                  "前台观察会话，本交付不开启 P4；引擎在合成已核验能力下的自检结果见 result.engine_selftest",
                  code="P4_GATE_CLOSED")


def alert_session_enable(ctx, inp):
    ctx.ensure("snapshot_final")
    ctx.state["alert_enable_input"] = inp
    _blocked(ctx, "断线降级（AC-072）")


def alert_feed_disconnect(ctx, inp):
    _blocked(ctx, "断线降级（AC-072）")


def alert_clock_advance(ctx, inp):
    clock.advance(int(inp.get("seconds") or 0))


def alert_feed_reconnect(ctx, inp):
    ctx.ensure("snapshot_final")
    _blocked(ctx, "重连追补去重（AC-073）")
