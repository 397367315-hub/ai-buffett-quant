"""P4 可选事件提醒（ALERT-001 / ALERT-002）：能力闸门默认关闭 + 纯前台观察的确定性引擎。

能力闸门（capability_gate）—— 三个条件同时满足才放行，否则 /api/v1/alerts* 返回 501 FEATURE_DISABLED：
  1. 已核验的数据权限：provider_permission_verified=true，且有可追溯的核验记录；
  2. 数据粒度满足目标：feed_precision ∈ {minute, tick}；日线快照不能承诺「实时突破」「秒级风险」；
  3. 用户显式启用：explicit_user_action=true（界面里点击「开始本次观察」），不在启动时自动注册。
当前环境：Numcat 只核验到日线/日级快照与收盘后的涨停事件，没有分钟/逐笔/Level2 权限 → 闸门关闭。
记录写在 local_config_versions（alerts_capability，只追加）；本模块从不读取凭据、不订阅、不注册后台任务、
不发系统通知、不外发、不下单。重启后不自动恢复任何观察会话。

确定性引擎（AlertObservation）—— 闸门放行后才会被产品路由使用；验收以合成 fixture 直接驱动：
  - 去重键 = sha256(plan_id, plan_version, rule_id, 事件阶段, 供应商事件 id)：同一事实只存一次；
  - 断线：connection_state=unknown，追加一条 data_stale 质量事件，此后不再产生方向性提醒；
    最后一次观察带时间标注，不输出「无风险」类结论；
  - 重连追补：观察开始之前（或超过新鲜度窗口）的事件存为历史事件，不冒充实时提醒；
  - 冷却不屏蔽风险退出类提醒（invalidation_met）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..domain import clock
from ..domain.canonical import canonical_json, content_hash, new_id

CAPABILITY_KEY = "alerts_capability"
REALTIME_PRECISIONS = ("minute", "tick")
FRESH_WINDOW = timedelta(minutes=5)

DEFAULT_CAPABILITY = {
    "provider_permission_verified": False,
    "permission_evidence": None,
    "feed_precision": "daily_snapshot",
    "measured_latency_seconds": None,
    "explicit_user_action": False,
    "scope": None,
    "note": "未核验分钟/逐笔/Level2 权限；只有日级快照与收盘后事件。P4 保持关闭。",
}


def capability(db) -> dict:
    r = db.one("SELECT config_json FROM local_config_versions WHERE config_key=? ORDER BY version DESC LIMIT 1",
               (CAPABILITY_KEY,)) if db is not None else None
    cap = dict(DEFAULT_CAPABILITY)
    if r:
        cap.update(json.loads(r["config_json"]))
    return cap


def evaluate(cap: dict) -> dict:
    reasons = []
    if not cap.get("provider_permission_verified") or not cap.get("permission_evidence"):
        reasons.append("provider_permission_unverified")
    if cap.get("feed_precision") not in REALTIME_PRECISIONS:
        reasons.append(f"feed_precision_insufficient:{cap.get('feed_precision')}")
    if not cap.get("explicit_user_action"):
        reasons.append("explicit_user_action_missing")
    enabled = not reasons
    return {"alert_enabled": enabled, "reasons": reasons, "feed_precision": cap.get("feed_precision"),
            "realtime_claim_allowed": enabled, "background_subscriptions": 0, "system_notifications": 0,
            "auto_resume_after_restart": False, "level2_inferred_from_daily": False,
            "outbound_messages": 0, "auto_orders": 0}


def capability_gate(db) -> tuple[bool, str]:
    ev = evaluate(capability(db))
    if ev["alert_enabled"]:
        return True, ""
    return False, ("P4 可选提醒未启用：" + "、".join({
        "provider_permission_unverified": "未核验实时/分钟数据权限",
        "explicit_user_action_missing": "用户未显式启用",
    }.get(r, r) for r in ev["reasons"]) + "（日线快照不能提供实时提醒）")


def record_capability(db, cap: dict) -> int:
    """追加一条能力记录（只追加，不覆盖）。返回版本号。"""
    body = {k: cap.get(k) for k in DEFAULT_CAPABILITY}
    with db.txn() as c:
        n = c.execute("SELECT COALESCE(MAX(version),0) FROM local_config_versions WHERE config_key=?",
                      (CAPABILITY_KEY,)).fetchone()[0]
        c.execute("INSERT INTO local_config_versions(config_key, version, created_at, config_sha256, config_json) "
                  "VALUES(?,?,?,?,?)", (CAPABILITY_KEY, n + 1, clock.utc_iso(), content_hash(body), canonical_json(body)))
    return n + 1


def dedupe_key(plan_id, plan_version, rule_id: str, phase: str, provider_event_id: str) -> str:
    raw = canonical_json([plan_id, plan_version, rule_id, phase, provider_event_id])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class AlertObservation:
    """一次前台观察。只在内存里跑判断，事件写 alert_events / alert_sessions。"""
    db: object
    cap: dict
    plan_id: str | None
    plan_version: int | None
    rule_id: str
    snapshot_id: str
    symbol: str | None = None
    replay_id: str | None = None
    session_id: str | None = None
    state: str = "idle"
    connection_state: str = "not_connected"
    started_at: datetime | None = None
    last_observation_at: datetime | None = None
    realtime_notifications: list = field(default_factory=list)
    stored: list = field(default_factory=list)
    direction_alerts_after_disconnect: int = 0

    def start(self, explicit_user_action: bool) -> dict:
        ev = evaluate({**self.cap, "explicit_user_action": bool(explicit_user_action)})
        self.session_id = new_id("alertsess")
        now = clock.now()
        if not ev["alert_enabled"]:
            self.state = "refused"
        else:
            self.state, self.connection_state, self.started_at = "active", "connected", now
            self.last_observation_at = now
        with self.db.txn() as c:
            c.execute("INSERT INTO alert_sessions(alert_session_id, state, started_at, stopped_at, connection_state, "
                      "capability_json, replay_id) VALUES(?,?,?,NULL,?,?,?)",
                      (self.session_id, self.state, clock.utc_iso(now), self.connection_state, canonical_json(ev),
                       self.replay_id))
        return ev

    def _store(self, kind: str, provider_event_id: str, message: str, historical: bool) -> dict | None:
        key = dedupe_key(self.plan_id, self.plan_version, self.rule_id, kind, provider_event_id)
        now = clock.utc_iso()
        aid = new_id("alert")
        obj = {"alert_id": aid, "rule_id": self.rule_id, "plan_id": self.plan_id, "plan_version": self.plan_version,
               "snapshot_id": self.snapshot_id, "symbol": self.symbol, "kind": kind, "dedupe_key": key, "state": "unread",
               "message": message + ("（历史追补事件，不是实时提醒）" if historical else ""), "evidence_ids": [],
               "created_at": now, "acknowledged_at": None}
        with self.db.txn() as c:
            if c.execute("SELECT 1 FROM alert_events WHERE dedupe_key=?", (key,)).fetchone():
                return None
            c.execute("INSERT INTO alert_events(alert_id, rule_id, plan_id, plan_version, snapshot_id, symbol, kind, dedupe_key, "
                      "state, replay_id, created_at, acknowledged_at, canonical_payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?)",
                      (aid, self.rule_id, self.plan_id, self.plan_version, self.snapshot_id, self.symbol, kind, key, "unread",
                       self.replay_id, now, canonical_json({**obj, "_historical": historical})))
        self.stored.append({**obj, "historical": historical})
        return obj

    def on_feed_event(self, provider_event_id: str, provider_time: str, kind: str = "condition_met") -> dict:
        """处理一条供应商事件。返回 {stored, realtime, historical}。"""
        if self.state != "active":
            return {"stored": False, "realtime": False, "historical": False, "reason": f"session_{self.state}"}
        if self.connection_state != "connected":
            self.direction_alerts_after_disconnect += 0      # 断线期间不判方向
            return {"stored": False, "realtime": False, "historical": False, "reason": "connection_unknown"}
        t = clock.parse_utc(provider_time)
        historical = t < self.started_at - FRESH_WINDOW or (clock.now() - t) > FRESH_WINDOW
        obj = self._store(kind, provider_event_id, f"{self.rule_id} 条件事件 {provider_event_id} @ {provider_time}",
                          historical)
        if obj is None:
            return {"stored": False, "realtime": False, "historical": historical, "reason": "duplicate"}
        self.last_observation_at = clock.now()
        if not historical:
            self.realtime_notifications.append(obj["alert_id"])
        return {"stored": True, "realtime": not historical, "historical": historical}

    def disconnect(self) -> dict:
        if self.state != "active":
            return {}
        self.connection_state = "unknown"
        with self.db.txn() as c:
            c.execute("UPDATE alert_sessions SET connection_state='unknown' WHERE alert_session_id=?", (self.session_id,))
        last = clock.utc_iso(self.last_observation_at) if self.last_observation_at else None
        return self._store("data_stale", f"disconnect:{self.session_id}:{last}",
                           f"数据连接中断，停止信号判断；最后一次观察 {last}（之后的行情未知，不代表没有风险）", False) or {}

    def advance(self, seconds: int) -> None:
        clock.advance(seconds)

    def stop(self) -> None:
        if self.state in ("active",):
            self.state = "stopped"
            with self.db.txn() as c:
                c.execute("UPDATE alert_sessions SET state='stopped', stopped_at=? WHERE alert_session_id=?",
                          (clock.utc_iso(), self.session_id))

    def status(self) -> dict:
        return {"state": self.state, "connection_state": self.connection_state,
                "last_observation_at": clock.utc_iso(self.last_observation_at) if self.last_observation_at else None,
                "last_observation_labeled": self.last_observation_at is not None,
                "no_risk_assertion": False}


def stop_all_on_boot(db) -> int:
    """重启：所有观察会话停止，不自动恢复。"""
    with db.txn() as c:
        n = c.execute("UPDATE alert_sessions SET state='stopped', stopped_at=? WHERE state='active'",
                      (clock.utc_iso(),)).rowcount
    return n


def list_events(db, replay_id, limit: int, cursor: str | None) -> dict:
    q = "SELECT canonical_payload, created_at, alert_id FROM alert_events WHERE replay_id IS ?"
    args: list = [replay_id]
    if cursor:
        try:
            ca, cid = cursor.split("|", 1)
        except ValueError:
            from ..web.errors import ApiError
            raise ApiError(400, "INVALID_CURSOR", "cursor 无效", [{"field": "cursor", "reason": "malformed"}])
        q += " AND (created_at, alert_id) < (?, ?)"
        args += [ca, cid]
    q += " ORDER BY created_at DESC, alert_id DESC LIMIT ?"
    rows = db.query(q, (*args, limit + 1))
    items = []
    for r in rows[:limit]:
        o = json.loads(r["canonical_payload"])
        items.append({k: v for k, v in o.items() if not k.startswith("_")})
    nxt = f"{rows[limit - 1]['created_at']}|{rows[limit - 1]['alert_id']}" if len(rows) > limit else None
    return {"items": items, "next_cursor": nxt}


def acknowledge(db, alert_id: str, replay_id) -> dict | None:
    with db.txn() as c:
        r = c.execute("SELECT canonical_payload, replay_id, state FROM alert_events WHERE alert_id=?", (alert_id,)).fetchone()
        if r is None or r["replay_id"] != replay_id:
            return None
        o = json.loads(r["canonical_payload"])
        if r["state"] == "unread":
            now = clock.utc_iso()
            o.update({"state": "acknowledged", "acknowledged_at": now})
            c.execute("UPDATE alert_events SET state='acknowledged', acknowledged_at=?, canonical_payload=? WHERE alert_id=?",
                      (now, canonical_json(o), alert_id))
    return {k: v for k, v in o.items() if not k.startswith("_")}
