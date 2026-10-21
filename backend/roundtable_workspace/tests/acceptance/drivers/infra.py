"""通用控制类 driver：时钟、重启、重新打开数据库、故障注入、阶段开关、日历/上游/HTTP 故障。"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

from panel.domain import clock, faults
from panel.services import calendar_service, snapshot_service

from .. import fixtures as fx


def clock_set(ctx, inp):
    clock.set_fixed(inp["now"])
    ctx.result.setdefault("clock_history", []).append(inp["now"])


def process_restart(ctx, inp):
    """同一 runtime 上重新装配：迁移幂等、running → PROCESS_INTERRUPTED、观察会话停止、不自动恢复任何任务。"""
    ctx.harness()
    before_jobs = ctx.scalar("SELECT COUNT(*) FROM jobs", purpose="重启前任务数")
    ctx.restart()
    after_jobs = ctx.scalar("SELECT COUNT(*) FROM jobs", purpose="重启后任务数")
    queued_new = ctx.scalar("SELECT COUNT(*) FROM jobs WHERE state='queued'", purpose="重启后排队任务")
    ctx.result["process_restarted"] = True
    ctx.result["jobs_created_by_restart"] = after_jobs - before_jobs
    ctx.result["queued_after_restart"] = queued_new
    for probe in ctx.state.get("post_restart_probes") or []:
        probe(ctx)


def db_reopen(ctx, inp):
    """关闭本线程连接后重新打开，做完整性检查（AC-035：事务半途故障后不留半个对象）。"""
    db = ctx.db
    db.close()
    rep = db.integrity_report()
    ctx.result["db_integrity_after_reopen"] = rep["integrity_check"]
    ctx.result["foreign_key_errors_after_reopen"] = rep["foreign_key_errors"]
    point = ctx.state.get("fault_point")
    if point:
        key = ctx.state.get("fault_idempotency_key")
        plan_id = ctx.state.get("fault_plan_id")
        frozen = ctx.scalar("SELECT COUNT(*) FROM plan_versions WHERE plan_id=? AND status='frozen'", (plan_id,),
                            purpose="故障后已冻结版本数")
        idem = ctx.scalar("SELECT COUNT(*) FROM idempotency_keys WHERE idempotency_key=?", (key,),
                          purpose="故障请求的幂等记录")
        half = ctx.scalar("SELECT COUNT(*) FROM plan_versions WHERE status='frozen' AND (frozen_hash IS NULL OR frozen_at IS NULL)",
                          purpose="半冻结（缺哈希/时间）版本")
        orphan_journal = ctx.scalar(
            "SELECT COUNT(*) FROM journal_events j LEFT JOIN idempotency_keys k ON k.method=j.idempotency_method AND "
            "k.request_path=j.idempotency_path AND k.idempotency_key=j.idempotency_key WHERE k.idempotency_key IS NULL",
            purpose="没有幂等记录的孤立账本事件")
        proj_mismatch = 0
        for r in ctx.q("SELECT portfolio_id, last_event_sequence FROM portfolios", purpose="投影与事件序号对账"):
            mx = ctx.scalar("SELECT COALESCE(MAX(sequence),0) FROM journal_events WHERE portfolio_id=?", (r["portfolio_id"],))
            if int(mx) != int(r["last_event_sequence"]):
                proj_mismatch += 1
        ctx.result["transaction_committed"] = bool(frozen or idem)
        ctx.result["orphan_events"] = int(orphan_journal)
        ctx.result["partial_projections"] = int(half) + proj_mismatch
        # 重试：同一个幂等键在故障解除后应正常完成且只完成一次
        faults.disarm_all()
        c = ctx.client()
        st, obj, _ = c.post(f"/api/v1/plans/{plan_id}/freeze", {"expected_version": 1}, key=key)
        st2, obj2, _ = c.post(f"/api/v1/plans/{plan_id}/freeze", {"expected_version": 1}, key=key)
        n_frozen = ctx.scalar("SELECT COUNT(*) FROM plan_versions WHERE plan_id=? AND status='frozen'", (plan_id,))
        ctx.result["retry_statuses"] = [st, st2]
        ctx.result["retry_safe"] = st == 200 and st2 == 200 and obj == obj2 and n_frozen == 1


def fault_inject(ctx, inp):
    """登记故障点；在下一个被测操作发起前才 arm（fixture 物化本身不应被打断）。"""
    ctx.state["fault_point"] = inp["point"]


def stage_modules_disable(ctx, inp):
    """关掉后续阶段：路由层 501；同时给 P1 服务函数装计数器，证明 P0 流程没有调用它们。"""
    stages = set(inp.get("stages") or [])
    h = ctx.harness()
    base_gate = h.app.feature_gate
    h.app.feature_gate = lambda ph: (False, f"{ph} disabled for AC") if ph in stages else base_gate(ph)
    from panel.services import (case_orchestrator, journal_service, legacy_importer, plan_service, review_service,
                                seat_runner)
    counts = {"p1": 0, "ledger_import": 0}
    ctx.state["p1_counts"] = counts
    patched = []
    for mod in (case_orchestrator, journal_service, plan_service, review_service, seat_runner, legacy_importer):
        for name in dir(mod):
            f = getattr(mod, name)
            if callable(f) and getattr(f, "__module__", "") == mod.__name__ and not isinstance(f, type) and not name.startswith("__"):
                def wrap(fn, nm=name, m=mod):
                    def inner(*a, **k):
                        counts["p1"] += 1
                        if m is legacy_importer:
                            counts["ledger_import"] += 1
                        return fn(*a, **k)
                    return inner
                setattr(mod, name, wrap(f))
                patched.append((mod, name, f))
    ctx.state["p1_patched"] = patched
    ctx.notes.append(f"已关闭阶段 {sorted(stages)}；P1 服务函数 {len(patched)} 个已装调用计数器")


def calendar_fault_set(ctx, inp):
    ctx.harness()
    if inp.get("state") == "unavailable":
        ctx.state["calendar_fault"] = True
        calendar_service.set_override(None, unavailable=True)
    ctx.result["calendar_state"] = inp.get("state")


# ── 受控上游：真实 NumcatAdapter + 假 meoz CLI（按剧本返回 429 / 超时 / 认证失败 / 正常数据） ──
FAKE_CLI = textwrap.dedent('''
    import json, os, sys, time
    args = sys.argv[1:]
    op = args[args.index("--operation-id") + 1]
    req = json.load(open(args[args.index("--request-file") + 1], encoding="utf-8"))
    here = os.path.dirname(os.path.abspath(__file__))
    scen = json.load(open(os.path.join(here, "scenario.json"), encoding="utf-8"))
    log = os.path.join(here, "calls.ndjson")
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"op": op, "params": req.get("params")}) + "\\n")
    seq = scen.get("faults", {}).get(op)
    if seq:
        state_f = os.path.join(here, "state_" + op + ".txt")
        n = int(open(state_f).read()) if os.path.exists(state_f) else 0
        open(state_f, "w").write(str(n + 1))
        fault = seq[n] if n < len(seq) else seq[-1]
        if fault.startswith("429"):
            print(json.dumps({"results": [{"code": 429, "retry_after": 1, "msg": "rate limited"}]}))
            sys.exit(0)
        if fault == "timeout":
            time.sleep(float(scen.get("sleep", 5)))
            sys.exit(0)
        if fault == "auth":
            key = os.environ.get("NUMCAT_API_KEY", "")
            sys.stderr.write("auth failed for key=" + key + " env=" + json.dumps(dict(os.environ)) + "\\n")
            print(json.dumps({"error": "auth failed: invalid api key " + key}))
            sys.exit(3)
    data = scen.get("data", {}).get(op)
    if data is None:
        print(json.dumps({"results": [{"code": 200, "data": {"fields": [], "items": []}}]}))
    else:
        rows = data
        if op == "reference_stock_daily":
            day = (req.get("params") or {}).get("tradedate")
            rows = data.get(day, [])
        fields = list(rows[0].keys()) if rows else []
        print(json.dumps({"results": [{"code": 200, "data": {"fields": fields, "items": [[r[k] for k in fields] for r in rows]}}]}))
''')


def install_fake_cli(ctx, scenario: dict, max_retry: int = 1, timeout: float = 2.0) -> Path:
    d = ctx.tmp / "fake_meoz"
    d.mkdir(exist_ok=True)
    (d / "meoz.py").write_text(FAKE_CLI, encoding="utf-8")
    (d / "scenario.json").write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
    h = ctx.harness()
    h.settings.meoz_cli = d / "meoz.py"
    h.settings.provider_max_retry = max_retry
    h.settings.provider_timeout_seconds = timeout
    from panel.adapters.numcat import NumcatAdapter

    def factory(settings, store):
        return NumcatAdapter(settings.meoz_cli, store, settings.runtime_dir / "provider_cache",
                             max_retry=settings.provider_max_retry, timeout=settings.provider_timeout_seconds)
    ctx.state["provider_factory"] = factory
    snapshot_service.set_provider_factory(factory)
    return d


def fake_calls(ctx) -> list[dict]:
    f = ctx.tmp / "fake_meoz" / "calls.ndjson"
    if not f.exists():
        return []
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]


def provider_scenario_ok(ctx, days: list[str]) -> dict:
    """正常剧本：交易日历 = fixture 日历；每个日子的日线为合成行。"""
    cal = fx.calendar_of(ctx)
    tradecal = [{"cal_date": d.replace("-", ""), "is_open": 1} for d in sorted(cal.open_sessions)]
    daily = {}
    for d in days:
        rows = fx.base_rows({"session_date": d})
        daily[d.replace("-", "")] = [{**r, "symbol": f"{r['symbol']}.{'SH' if r['symbol'].startswith('6') else 'SZ'}",
                                       "pct_chg": "0"} for r in rows]
    return {"faults": {}, "data": {"reference_calendar_range": tradecal, "reference_stock_daily": daily}}


def provider_fault_set(ctx, inp):
    ctx.ensure("snapshot_final")
    p = ctx.fixture("provider_failures")
    scen = provider_scenario_ok(ctx, ["2026-10-16", "2026-10-19"])
    scen["faults"]["reference_stock_daily"] = list(inp.get("sequence") or [])
    install_fake_cli(ctx, scen, max_retry=int(p.get("max_retry", 1)), timeout=2.0)
    ctx.state["fault_sequence"] = inp.get("sequence")


def http_fault_set(ctx, inp):
    """浏览器侧注入：已打开的页面里，所有 /api/** 请求都返回指定状态（Playwright route）。"""
    from ..browser import pages
    pages.http_fault_set(ctx, inp)
    if not ctx.state.get("fault_routed"):
        raise AssertionError("http_fault_set 需要先 browser_open 打开页面")


# ── 备份 / 恢复演练（AC-076） ──
def database_backup(ctx, inp):
    f = ctx.fixture("backup_fixture")
    h = ctx.harness()
    st, pf, _ = fx.call(ctx, "POST", "/api/v1/portfolios", {"mode": "paper", "initial_cash_minor": "1000000",
                                                             "name": "备份演练组合"}, want=201)
    pid = pf["portfolio_id"]
    rev = pf["revision"]
    while rev < int(f["committed_events"]):
        st, ev, _ = fx.call(ctx, "POST", "/api/v1/journal/events", fx.note_body(pid, rev, f"备份演练事件 {rev + 1}"), want=201)
        rev += 1
    db = ctx.db
    mode = db.scalar("PRAGMA journal_mode")
    wal = ctx.runtime / "roundtable.sqlite3-wal"
    last = db.one("SELECT event_id FROM journal_events ORDER BY recorded_at DESC, sequence DESC LIMIT 1")["event_id"]
    ctx.state["backup_counts"] = {"events": db.scalar("SELECT COUNT(*) FROM journal_events"),
                                  "idem": db.scalar("SELECT COUNT(*) FROM idempotency_keys"),
                                  "idem_digest": [tuple(r) for r in db.query(
                                      "SELECT method, request_path, idempotency_key, request_sha256 FROM idempotency_keys "
                                      "ORDER BY request_path, idempotency_key")]}
    path = db.backup_now(h.settings.backup_dir, label="acceptance")
    ctx.state["backup_file"] = path
    ctx.result.update({"journal_mode": mode, "wal_file_present_at_backup": wal.exists() and wal.stat().st_size > 0,
                       "original_last_event_id": last, "backup_method": inp.get("method"),
                       "backup_file": path.name})


def database_restore_to_temp(ctx, inp):
    from panel.data import db as dbm
    target = ctx.tmp / "restore_drill" / "restored.sqlite3"
    rep = dbm.restore_to(ctx.state["backup_file"], target)
    ctx.state["restored"] = target
    ctx.result["restore_report"] = {"integrity_check": rep["integrity_check"], "schema_versions": rep["schema_versions"]}


def database_integrity_assert(ctx, inp):
    import sqlite3
    c = sqlite3.connect(str(ctx.state["restored"]))
    try:
        ic = c.execute("PRAGMA integrity_check").fetchone()[0]
        c.execute("PRAGMA foreign_keys=ON")
        fk = [list(r) for r in c.execute("PRAGMA foreign_key_check").fetchall()]
        last = c.execute("SELECT event_id FROM journal_events ORDER BY recorded_at DESC, sequence DESC LIMIT 1").fetchone()[0]
        n_ev = c.execute("SELECT COUNT(*) FROM journal_events").fetchone()[0]
        idem = [tuple(r) for r in c.execute("SELECT method, request_path, idempotency_key, request_sha256 FROM idempotency_keys "
                                             "ORDER BY request_path, idempotency_key")]
    finally:
        c.close()
    b = ctx.state["backup_counts"]
    ctx.result.update({"integrity_check": ic, "foreign_key_errors": fk, "restored_last_event_id": last,
                       "event_count_equal": n_ev == b["events"], "idempotency_records_equal": idem == b["idem_digest"],
                       "restored_event_count": n_ev})
