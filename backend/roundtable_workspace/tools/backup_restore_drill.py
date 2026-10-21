#!/usr/bin/env python3
"""备份恢复演练 + 干净启动 / 停止记录（05 §4、HANDOFF-002 / HANDOFF-005 的交付证据）。

真实进程 `python3 panel/api.py`，独立临时运行目录与空闲端口；数据只来自服务端配置的只读日线归档
（file_import，panel/data/daily），组合都是演练用的合成组合，不含任何用户私有持仓；不访问行情供应商、不读 .env。

  1 干净启动：命令、PID、只监听回环、首次 health 200 用时
  2 写入业务：快照 → Case(3 席) → 研究/模拟计划冻结 → 模拟组合日记 → 自报组合成交记录 → 事前复盘 → 卡组 → 历史训练（记决定/推进/揭晓/回真实盘）
  3 服务不停做在线一致性备份（SQLite backup API + 证据仓）；备份后再写 1 条日记（说明恢复点之后的事件需重录）
  4 干净停止：SIGTERM → 退出码、用时、端口释放、无残留进程
  5 恢复到空目录 → verify_runtime 业务对账（链、余额、计划哈希、快照指纹、证据）
  6 在恢复目录上启动服务，只读业务查询与备份前逐项比对 → 停止
  7 证据写入 docs/implementation/evidence/backup-restore-drill-<日期>.json

用法：cd WORK_ROOT && python3 tools/backup_restore_drill.py [--keep]
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import platform
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

WORK_ROOT = Path(__file__).resolve().parents[1]
WB = WORK_ROOT / "records/roundtable/2026-10-18-圆桌面板设计"
sys.path.insert(0, str(WB))
sys.path.insert(0, str(WORK_ROOT))

from panel.services import backup_service as bk   # noqa: E402

SYMS = ["600000.SH", "000001.SZ"]
EVIDENCE_DIR = WORK_ROOT / "docs/implementation/evidence"


def utc(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def leftover_processes(port: int) -> list[int]:
    """停止后是否还有本端口的 panel/api.py 进程：Linux 读 /proc，macOS 等没有 /proc 时用 ps。"""
    needle = f"--port {port}"
    rows: list[tuple[int, str]] = []
    proc = Path("/proc")
    if proc.is_dir():
        for d in proc.iterdir():
            if d.name.isdigit():
                try:
                    rows.append((int(d.name), (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")))
                except OSError:
                    continue
    else:
        try:
            out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        for ln in out.splitlines():
            pid, _, cmd = ln.strip().partition(" ")
            if pid.isdigit():
                rows.append((int(pid), cmd))
    return [pid for pid, cmd in rows if "panel/api.py" in cmd and needle in cmd]


def listen_addresses(port: int) -> list[str]:
    """读 /proc/net/tcp{,6} 找 LISTEN 状态的本地地址（不依赖 ss/netstat）；没有 /proc 时（macOS）用 lsof。"""
    if not Path("/proc/net/tcp").exists():
        try:
            out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True,
                                 timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        addrs = []
        for ln in out.splitlines()[1:]:
            parts = ln.split()
            name = next((p for p in parts if f":{port}" in p), "")
            if name:
                addrs.append(name.rsplit(":", 1)[0].strip("[]"))
        return addrs
    out = []
    for f, fam in (("/proc/net/tcp", 4), ("/proc/net/tcp6", 6)):
        try:
            lines = Path(f).read_text().splitlines()[1:]
        except OSError:
            continue
        for ln in lines:
            parts = ln.split()
            addr, st = parts[1], parts[3]
            ip_hex, port_hex = addr.split(":")
            if st != "0A" or int(port_hex, 16) != port:
                continue
            if fam == 4:
                ip = ".".join(str(int(ip_hex[i:i + 2], 16)) for i in (6, 4, 2, 0))
            else:
                ip = "::1" if ip_hex.endswith("01000000") and set(ip_hex[:24]) == {"0"} else ("::" if set(ip_hex) == {"0"} else ip_hex)
            out.append(ip)
    return out


class Http:
    def __init__(self, port: int):
        self.port = port
        self.cookie = None
        self.csrf = None
        self.log: list[dict] = []

    def req(self, method: str, path: str, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        hdr = {"Host": f"127.0.0.1:{self.port}", "Accept": "application/json"}
        if self.cookie:
            hdr["Cookie"] = f"rt_local_session={self.cookie}"
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
            hdr["Content-Type"] = "application/json"
        if method != "GET":
            hdr["Origin"] = f"http://127.0.0.1:{self.port}"
            if self.csrf:
                hdr["X-CSRF-Token"] = self.csrf
            hdr["Idempotency-Key"] = "drill-" + secrets.token_hex(8)
        t0 = time.monotonic()
        conn.request(method, path, body=data, headers=hdr)
        r = conn.getresponse()
        raw = r.read()
        for k, v in r.getheaders():
            if k.lower() == "set-cookie" and v.startswith("rt_local_session="):
                self.cookie = v.split(";")[0].split("=", 1)[1]
        ctype = r.getheader("Content-Type") or ""
        obj = json.loads(raw) if raw and "json" in ctype else None
        # 只记方法、路径（不含查询串）、状态、耗时；不记 cookie / CSRF / 请求体
        self.log.append({"method": method, "path": path.split("?")[0], "status": r.status,
                         "ms": round((time.monotonic() - t0) * 1000, 1)})
        conn.close()
        return r.status, obj

    def ok(self, method, path, body=None, want=(200, 201, 202)):
        st, obj = self.req(method, path, body)
        if st not in (want if isinstance(want, tuple) else (want,)):
            raise RuntimeError(f"{method} {path} → {st} {json.dumps(obj, ensure_ascii=False)[:400]}")
        return obj

    def bootstrap(self):
        obj = self.ok("POST", "/api/v1/session/bootstrap", {}, want=201)
        self.csrf = obj["csrf_token"]
        return obj

    def wait(self, accepted: dict, timeout=600) -> dict:
        jid = accepted["job"]["job_id"]
        t0 = time.time()
        while True:
            j = self.ok("GET", f"/api/v1/jobs/{jid}", want=200)
            if j["state"] in ("succeeded", "partial", "failed", "cancelled"):
                if j["state"] not in ("succeeded", "partial"):
                    raise RuntimeError(f"任务失败：{json.dumps(j, ensure_ascii=False)[:400]}")
                return j
            if time.time() - t0 > timeout:
                raise TimeoutError(jid)
            time.sleep(0.3)


class Server:
    def __init__(self, runtime: Path, logfile: Path):
        self.runtime = runtime
        self.port = free_port()
        self.logfile = logfile
        self.proc = None
        self.record: dict = {}

    def start(self) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("NUMCAT_", "ROUNDTABLE_"))}
        env.update({"ROUNDTABLE_RUNTIME": str(self.runtime), "ROUNDTABLE_QUIET": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        cmd = [sys.executable, str(WB / "panel/api.py"), "--port", str(self.port)]
        t0 = time.monotonic()
        self.proc = subprocess.Popen(cmd, cwd=str(WORK_ROOT), env=env, stdin=subprocess.DEVNULL,
                                     stdout=open(self.logfile, "ab"), stderr=subprocess.STDOUT)
        h = Http(self.port)
        while True:                      # 存活：静态首页 200（不需要会话）
            if self.proc.poll() is not None:
                raise RuntimeError(f"服务启动即退出：{self.proc.returncode}\n{self.logfile.read_text()[-2000:]}")
            try:
                st, _ = h.req("GET", "/")
                if st == 200:
                    break
            except OSError:
                pass
            if time.monotonic() - t0 > 60:
                raise TimeoutError("60 秒内首页没有 200")
            time.sleep(0.1)
        live = time.monotonic() - t0
        h.bootstrap()                    # 就绪：本地会话 + /health/data 200
        st, obj = h.req("GET", "/api/v1/health/data")
        if st != 200:
            raise RuntimeError(f"health/data → {st} {obj}")
        ready = time.monotonic() - t0
        self.record = {"command": "python3 panel/api.py --port <空闲端口>（env ROUNDTABLE_RUNTIME=<临时运行目录>）",
                       "pid": self.proc.pid, "port": self.port, "started_at": utc(),
                       "seconds_to_liveness_index_200": round(live, 3),
                       "seconds_to_readiness_health_200": round(ready, 3),
                       "unauthenticated_health_status": None,
                       "listen_addresses": listen_addresses(self.port),
                       "health": {k: obj.get(k) for k in ("status", "api", "db") if isinstance(obj, dict) and k in obj}}
        anon = Http(self.port)
        self.record["unauthenticated_health_status"] = anon.req("GET", "/api/v1/health/data")[0]
        return self.record

    def stop(self) -> dict:
        t0 = time.monotonic()
        self.proc.send_signal(signal.SIGTERM)
        try:
            code = self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = self.proc.wait()
        took = time.monotonic() - t0
        refused = False
        try:
            socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
        except OSError:
            refused = True
        leftovers = leftover_processes(self.port)
        log_tail = [ln for ln in self.logfile.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()][-3:]
        wal = self.runtime / (bk.DB_NAME + "-wal")
        rec = {"signal": "SIGTERM", "exit_code": code, "seconds_to_exit": round(took, 3), "port_released": refused,
               "leftover_processes": leftovers, "stopped_at": utc(),
               "wal_bytes_after_stop": wal.stat().st_size if wal.exists() else 0,
               "log_tail": [ln.replace(str(self.runtime), "<runtime>") for ln in log_tail]}
        return rec


def populate(h: Http) -> dict:
    ids: dict = {}
    h.bootstrap()
    acc = h.ok("POST", "/api/v1/snapshots", {"session_date": "2026-10-16", "source_mode": "file_import",
                                             "symbols": SYMS, "allow_stale": True}, want=202)
    sid = h.wait(acc)["resource_id"]
    ids["snapshot_id"] = sid
    seats = ["trading-wyckoff-v1", "trading-livermore-v1", "trading-tuixue-v1"]
    roles = [{"seat_id": seats[0], "role": "primary"}, {"seat_id": seats[1], "role": "validator"},
             {"seat_id": seats[2], "role": "discipline"}]
    acc = h.ok("POST", "/api/v1/cases", {"supersedes_case_id": None, "snapshot_id": sid, "title": "演练 Case",
                                         "question": "两只票按威科夫是否处于吸筹末段？", "symbols": SYMS, "horizon": "swing",
                                         "seat_ids": seats, "primary_strategy_id": seats[0], "role_assignments": roles,
                                         "llm_mode": "disabled", "provider_id": None, "model_id": None,
                                         "user_context_id": None, "replay_id": None}, want=202)
    case_id = h.wait(acc)["resource_id"]
    ids["case_id"] = case_id
    pf = h.ok("POST", "/api/v1/portfolios", {"mode": "paper", "initial_cash_minor": "10000000", "name": "演练模拟组合（合成）"}, want=201)
    pid = pf["portfolio_id"]
    ids["paper_portfolio_id"] = pid
    now = utc()

    def rule(rid, kind, desc, price=None, deadline=None, symbol=None):
        return {"rule_id": rid, "description": desc, "rule_origin": "user_rule", "origin_ref": "user:drill",
                "source_version": "v1", "rule_version": "v1", "kind": kind, "symbol": symbol, "price_decimal": price,
                "threshold_ratio": None, "deadline_session_date": deadline, "evidence_ids": []}
    pb = {"change_reason": "initial", "effective_from": now, "portfolio_id": None, "basis_portfolio_revision": None,
          "case_id": case_id, "snapshot_id": sid, "primary_strategy_id": seats[0], "purpose": "research",
          "planned_shares": None, "cancel_rules": [], "time_exit_rule": None, "exit_deadline": None,
          "title": "演练：研究计划", "hypothesis": "放量突破交易区间上沿则进入上涨段", "symbols": ["600000.SH"],
          "horizon": "swing", "entry_rules": [rule("r_entry", "entry", "放量突破区间上沿")], "exit_rules": [],
          "invalidation_rules": [], "risk_budget_minor": None, "max_position_ratio": None, "benchmark_id": None,
          "evaluation_window": {"start_session_date": "2026-10-20", "end_session_date": "2026-10-30"},
          "fee_model_version": None, "replay_id": None}
    rp = h.ok("POST", "/api/v1/plans", pb, want=201)
    h.ok("POST", f"/api/v1/plans/{rp['plan_id']}/freeze", {"expected_version": 1}, want=200)
    sb = dict(pb, purpose="simulation", portfolio_id=pid, basis_portfolio_revision=pf["revision"], planned_shares=100,
              title="演练：模拟计划 100 股",
              entry_rules=[rule("r_entry", "entry", "突破 7.80 触发", price="7.8000", symbol="600000.SH")],
              cancel_rules=[rule("r_cancel", "cancel", "三个交易日未触发则取消")],
              invalidation_rules=[rule("r_inv", "invalidation", "跌破 7.20 失效", price="7.2000", symbol="600000.SH")],
              exit_rules=[rule("r_exit", "exit", "跌破 7.30 防守卖出", price="7.3000", symbol="600000.SH")],
              time_exit_rule=rule("r_time", "time_exit", "持有满 10 个交易日退出", deadline="2026-11-03"),
              exit_deadline="2026-11-03T07:00:00Z", risk_budget_minor="50000", max_position_ratio="0.1",
              fee_model_version="legacy-paper-fee-v1")
    sp = h.ok("POST", "/api/v1/plans", sb, want=201)
    sfr = h.ok("POST", f"/api/v1/plans/{sp['plan_id']}/freeze", {"expected_version": 1}, want=200)
    ids["plan_ids"] = [rp["plan_id"], sp["plan_id"]]

    def note(p, rev, text, replay_id=None, eff=None):
        return {"portfolio_id": p, "event_type": "note", "mode": "paper", "effective_at": eff or utc(), "symbol": None,
                "side": None, "shares": None, "price_decimal": None, "net_cash_delta_minor": None, "snapshot_id": None,
                "plan_id": None, "plan_version": None, "corrects_event_id": None, "correction_action": None,
                "corrected_event_type": None, "note": text, "replay_id": replay_id, "expected_portfolio_revision": rev,
                "evidence_ids": [], "reported_fees": None}
    rev = pf["revision"]
    for t in ("今天只观察，不交易", json.dumps({"schema": "diary_v1", "kind": "diary", "saw": "按计划等待", "want": "",
                                           "feelings": ["平静"], "did": "只记录", "next_check": "", "link": None},
                                          ensure_ascii=False)):
        ev = h.ok("POST", "/api/v1/journal/events", note(pid, rev, t), want=201)
        rev = h.ok("GET", f"/api/v1/portfolios/{pid}", want=200)["revision"]
    up = h.ok("POST", "/api/v1/portfolios", {"mode": "user_reported", "initial_cash_minor": "1000000",
                                             "name": "演练自报账户（合成，未核验）"}, want=201)
    ids["user_reported_portfolio_id"] = up["portfolio_id"]
    h.ok("POST", "/api/v1/journal/events", {
        "portfolio_id": up["portfolio_id"], "event_type": "manual_trade_record", "mode": "user_reported",
        "effective_at": utc(), "symbol": "600000.SH", "side": "buy", "shares": 100, "price_decimal": "10.0000",
        "net_cash_delta_minor": "-100501", "snapshot_id": None, "plan_id": None, "plan_version": None,
        "corrects_event_id": None, "correction_action": None, "corrected_event_type": None, "note": "演练：自报成交记录",
        "replay_id": None, "expected_portfolio_revision": up["revision"], "evidence_ids": [], "reported_fees": None}, want=201)
    acc = h.ok("POST", "/api/v1/reviews", {"plan_id": sp["plan_id"], "plan_version": 1, "frozen_hash": sfr["frozen_hash"],
                                           "review_kind": "preflight", "as_of_snapshot_id": sid}, want=202)
    ids["review_id"] = h.wait(acc)["resource_id"]
    deck = h.ok("POST", "/api/v1/decks", {"source_type": "plan", "source_id": sp["plan_id"], "source_version": "1",
                                          "title": "计划要件"}, want=201)
    ids["deck_id"] = deck["deck_id"]
    # 历史训练：建 → 记决定 → 推进 → 揭晓 → 回真实盘
    idx = h.ok("GET", "/api/v1/workspace/index", want=200)
    acc = h.ok("POST", "/api/v1/replays", {"symbols": SYMS, "start_session_date": "2026-09-15", "end_session_date": "2026-09-30",
                                           "strict_as_of": True, "data_archive_fingerprint": idx["archive"]["fingerprint"],
                                           "initial_cash_minor": "10000000"}, want=202)
    rid = acc["job"]["resource_id"]
    h.wait(acc)
    rep = h.ok("GET", f"/api/v1/replays/{rid}", want=200)
    rpid = rep["isolated_portfolio_id"]
    rpf = h.ok("GET", f"/api/v1/portfolios/{rpid}", want=200)
    h.ok("POST", "/api/v1/journal/events", note(rpid, rpf["revision"], "继续等待", replay_id=rid, eff=rep["knowledge_cutoff_at"]), want=201)
    h.ok("POST", f"/api/v1/replays/{rid}/step", {"expected_cursor_session_date": rep["cursor_session_date"], "steps": 1}, want=200)
    rep2 = h.ok("GET", f"/api/v1/replays/{rid}", want=200)
    h.ok("POST", f"/api/v1/replays/{rid}/reveal", {"expected_cursor_session_date": rep2["cursor_session_date"],
                                                   "acknowledge_training_end": True}, want=200)
    ctx = h.ok("GET", "/api/v1/session/context", want=200)
    h.ok("POST", "/api/v1/session/context", {"expected_context_revision": ctx["context_revision"], "mode": "live",
                                             "replay_id": None, "acknowledge_training_end": True}, want=200)
    ids["replay_id"] = rid
    ids["replay_portfolio_id"] = rpid
    return ids


def digest(h: Http, ids: dict) -> dict:
    """只读业务查询：备份前与恢复后逐项比对的内容。"""
    d: dict = {"plans": {}, "portfolios": {}, "journal": {}}
    for p in ids["plan_ids"]:
        x = h.ok("GET", f"/api/v1/plans/{p}", want=200)
        d["plans"][p] = {"version": x["version"], "status": x["status"], "frozen_hash": x["frozen_hash"],
                         "execution_eligibility": x["execution_eligibility"]}
    for key in ("paper_portfolio_id", "user_reported_portfolio_id"):
        pid = ids[key]
        x = h.ok("GET", f"/api/v1/portfolios/{pid}", want=200)
        d["portfolios"][pid] = {"cash_minor": x["cash_minor"], "revision": x["revision"],
                                "positions": sorted((q["symbol"], q["shares"]) for q in x["positions"])}
        evs = h.ok("GET", f"/api/v1/journal/events?portfolio_id={pid}&limit=100", want=200)["items"]
        d["journal"][pid] = {"count": len(evs), "last_event_hash": evs[-1]["event_hash"] if evs else None,
                             "event_ids": [e["event_id"] for e in evs]}
    s = h.ok("GET", f"/api/v1/snapshots/{ids['snapshot_id']}", want=200)
    d["snapshot"] = {"snapshot_id": s["snapshot_id"], "fingerprint": s["fingerprint"], "session_date": s["metadata"]["session_date"]}
    c = h.ok("GET", f"/api/v1/cases/{ids['case_id']}", want=200)
    d["case"] = {"case_id": c["case_id"], "state": c.get("state") or c.get("run_state"),
                 "seat_run_ids": sorted(c.get("seat_run_ids") or [])}
    r = h.ok("GET", f"/api/v1/replays/{ids['replay_id']}", want=200)
    d["replay"] = {"revealed": r["revealed"], "cursor_session_date": r["cursor_session_date"], "state": r["state"]}
    k = h.ok("GET", f"/api/v1/decks/{ids['deck_id']}", want=200)
    d["deck"] = {"version": k["version"], "cards": len(k["cards"])}
    idx = h.ok("GET", "/api/v1/workspace/index", want=200)
    d["workspace_counts"] = {key: len(v) for key, v in idx.items() if isinstance(v, list)}
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留临时目录（默认演练结束删除）")
    ap.add_argument("--out", default=str(EVIDENCE_DIR / f"backup-restore-drill-{datetime.now().strftime('%Y%m%d')}.json"))
    a = ap.parse_args(argv)
    base = Path(tempfile.mkdtemp(prefix="rt-drill-"))
    rt, restored, backups = base / "runtime", base / "restored" / "runtime", base / "backups"
    ev: dict = {"schema": "backup_restore_drill_v1", "started_at": utc(),
                "environment": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                                "os": f"{platform.system()} {platform.release()} {platform.machine()}"},
                "data_scope": "只读日线归档 file_import（2026-10-16）＋合成演练组合；不含用户私有持仓；未访问行情供应商",
                "paths_note": "临时目录在本机磁盘（/tmp），演练结束删除；证据里只出现相对名"}
    try:
        from tests.acceptance.runner import code_hash
        ev["code_hash"], ev["code_files"] = code_hash()
    except Exception as e:                      # pragma: no cover
        ev["code_hash_error"] = repr(e)
    ok = True
    srv = Server(rt, base / "server1.log")
    try:
        # 1 启动
        ev["clean_start"] = srv.start()
        h = Http(srv.port)
        # 2 写入
        t0 = time.monotonic()
        ids = populate(h)
        ev["populate"] = {"seconds": round(time.monotonic() - t0, 2), "ids": ids}
        before = digest(h, ids)
        # 3 在线备份（服务继续运行）
        wal = rt / (bk.DB_NAME + "-wal")
        wal_bytes = wal.stat().st_size if wal.exists() else 0
        m = bk.backup_runtime(rt / bk.DB_NAME, rt, backups, label="drill")
        ev["backup"] = {k: m[k] for k in ("backup_id", "method", "started_at", "completed_at", "elapsed_seconds",
                                          "schema_versions", "journal_max_sequence", "evidence_files", "evidence_bytes",
                                          "integrity_check", "excluded")}
        ev["backup"]["db_bytes"] = m["db"]["bytes"]
        ev["backup"]["db_sha256"] = m["db"]["sha256"]
        ev["backup"]["server_running_during_backup"] = srv.proc.poll() is None
        ev["backup"]["wal_bytes_at_backup"] = wal_bytes
        # 备份点之后再写一条：恢复后应当不存在（需要重录）
        pid = ids["paper_portfolio_id"]
        rev = h.ok("GET", f"/api/v1/portfolios/{pid}", want=200)["revision"]
        post = h.ok("POST", "/api/v1/journal/events", {
            "portfolio_id": pid, "event_type": "note", "mode": "paper", "effective_at": utc(), "symbol": None, "side": None,
            "shares": None, "price_decimal": None, "net_cash_delta_minor": None, "snapshot_id": None, "plan_id": None,
            "plan_version": None, "corrects_event_id": None, "correction_action": None, "corrected_event_type": None,
            "note": "备份点之后写入的日记", "replay_id": None, "expected_portfolio_revision": rev, "evidence_ids": [],
            "reported_fees": None}, want=201)
        post_id = post.get("event_id") or (post.get("event") or {}).get("event_id")
        live_verify = bk.verify_runtime(rt)
        # 4 干净停止
        ev["clean_stop"] = srv.stop()
        ev["source_runtime_verify_after_stop"] = {"ok": bk.verify_runtime(rt)["ok"], "ok_while_running": live_verify["ok"]}
        # 5 恢复到空目录
        t_restore = time.monotonic()
        rres = bk.restore_runtime(Path(m["backup_dir"]), restored)
        vr = bk.verify_runtime(restored)
        restore_s = time.monotonic() - t_restore
        ev["restore"] = {**{k: v for k, v in rres.items() if k != "target"}, "target": "<空目录>/restored/runtime",
                         "verify": {k: vr[k] for k in ("ok", "integrity_check", "foreign_key_errors", "schema_versions",
                                                         "migration_checksums_match", "evidence_referenced", "evidence_files",
                                                         "evidence_missing", "evidence_corrupted")},
                         "journal_chains": vr["journal_chains"], "balances": vr["balances"],
                         "frozen_plans": vr["frozen_plans"], "snapshots": vr["snapshots"]}
        ok &= vr["ok"]
        # 6 在恢复目录上启动，只读业务查询
        srv2 = Server(restored, base / "server2.log")
        t_up = time.monotonic()
        ev["restored_start"] = srv2.start()
        h2 = Http(srv2.port)
        h2.bootstrap()
        after = digest(h2, ids)
        first_query_s = time.monotonic() - t_up
        ev["restored_stop"] = srv2.stop()
        # 比对：备份前的业务状态在恢复后逐项相同；备份点之后的事件不在恢复库里
        exp = json.loads(json.dumps(before))
        after = json.loads(json.dumps(after))
        cmp = {k: (after[k] == exp[k]) for k in exp}
        absent = post_id not in after["journal"][pid]["event_ids"]
        ev["comparison"] = {"equal_by_section": cmp, "all_equal": all(cmp.values()),
                            "post_backup_event_id": post_id, "post_backup_event_absent_after_restore": absent,
                            "before": before, "after": after}
        ok &= all(cmp.values()) and absent
        ev["rpo_rto"] = {
            "rpo_measured": "按需备份的恢复点＝备份开始时刻：备份前提交的全部事件都在恢复库；备份后写入的 1 条事件不在（需按事件记录重录，见 post_backup_event_id）",
            "rpo_policy_proposal": "日常：每个交易日收盘后（15:30 上海）＋每次迁移/发布前各备份一次 → RPO ≤ 1 个交易日；保留 30 天；由部署阶段写入 release_manifest，开发阶段不注册任何后台任务",
            "rto_measured_seconds": round(restore_s + first_query_s, 3),
            "rto_components_seconds": {"restore_and_verify": round(restore_s, 3),
                                       "start_to_first_business_query": round(first_query_s, 3)},
            "rto_target_proposal_minutes": 15,
            "note": "数据量很小（演练库），真实库按同一流程实测后再定目标；目标需用户确认"}
        ev["http_calls"] = len(h.log) + len(h2.log)
        ev["http_non_2xx"] = [x for x in h.log + h2.log if x["status"] >= 300]
    except Exception as e:
        ok = False
        ev["error"] = repr(e)
        if srv.proc and srv.proc.poll() is None:
            srv.proc.kill()
        raise
    finally:
        ev["finished_at"] = utc()
        ev["result"] = "PASS" if ok else "FAIL"
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{ev['result']}  证据 → {out.relative_to(WORK_ROOT) if out.is_relative_to(WORK_ROOT) else out}")
        if not a.keep:
            import shutil
            shutil.rmtree(base, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
