"""每个验收用例的独立上下文：临时 runtime + 真实 App + 冻结时钟 + 网络守卫 + 证据记录。

- 结果投影只从真实 HTTP 响应、SQLite 查询、字节比对或浏览器 trace 生成（driver 写进 ctx.result）；
- http.ndjson 记录每个请求/响应（Cookie、X-CSRF-Token、Set-Cookie 一律脱敏），db_assertions.json 记录投影用到的查询；
- 网络守卫：用例运行期间任何非回环连接都会被拒绝并计数（broker/外发/上传都应为 0）；
- fixture 中的产品对象 ID（case_fixture_main 等）是别名，driver 通过 ctx.alias()/ctx.unalias() 双向映射。
"""
from __future__ import annotations

import hashlib
import json
import random
import socket
import tempfile
import threading
from pathlib import Path

from tests.support.harness import Client, Harness

from panel.domain import clock

from .spec import resolve_fixture

DEFAULT_NOW = "2026-10-19T07:10:00Z"           # fixture clock_monday：周一 15:10（上海）


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


ACCEPTANCE_PORT = _free_port()                 # 本进程所有用例共用一个回环端口（用例串行）
REDACT = {"cookie", "x-csrf-token", "set-cookie", "authorization"}

_net_lock = threading.Lock()
_net_attempts: list[str] = []
_orig_connect = socket.socket.connect
_orig_create_connection = socket.create_connection


def _loopback(addr) -> bool:
    try:
        host = addr[0] if isinstance(addr, tuple) else str(addr)
    except Exception:       # pragma: no cover
        return False
    return host in ("127.0.0.1", "localhost", "::1") or str(host).startswith("127.") or isinstance(addr, str)


def _guard_connect(self, addr):
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(addr):
        with _net_lock:
            _net_attempts.append(str(addr))
        raise ConnectionRefusedError(f"acceptance network guard: outbound connection to {addr} blocked")
    return _orig_connect(self, addr)


def _guard_create_connection(address, *a, **k):
    if not _loopback(address):
        with _net_lock:
            _net_attempts.append(str(address))
        raise ConnectionRefusedError(f"acceptance network guard: outbound connection to {address} blocked")
    return _orig_create_connection(address, *a, **k)


def install_network_guard() -> None:
    socket.socket.connect = _guard_connect
    socket.create_connection = _guard_create_connection


def network_attempts() -> list[str]:
    with _net_lock:
        return list(_net_attempts)


def reset_network_attempts() -> None:
    with _net_lock:
        _net_attempts.clear()


class LoggingClient(Client):
    """与 harness.Client 相同的浏览器式行为，额外记录完整（脱敏）请求/响应。"""

    def __init__(self, ctx: "CaseContext", h: Harness, **kw):
        super().__init__(h, **kw)
        self.ctx = ctx

    def request(self, method, path, body=None, headers=None, raw=None):
        st, obj, hdrs = super().request(method, path, body, headers, raw)
        req_h = {"Host": self.host}
        if headers:
            req_h.update(headers)
        if self.cookie:
            req_h["Cookie"] = "rt_local_session=<redacted>"
        self.ctx.log_http({"method": method, "path": path,
                           "request_headers": {k: ("<redacted>" if k.lower() in REDACT else v) for k, v in req_h.items()},
                           "request_body": body if raw is None else f"<raw {len(raw)} bytes>",
                           "status": st,
                           "response_headers": {k: ("<redacted>" if k.lower() in REDACT else v) for k, v in hdrs.items()},
                           "response_body": obj})
        return st, obj, hdrs


class CaseContext:
    def __init__(self, case: dict, spec: dict, case_dir: Path, run_id: str):
        self.case = case
        self.spec = spec
        self.fixtures = spec["fixtures"]
        self.case_dir = case_dir
        self.run_id = run_id
        self.result: dict = {}
        self.aliases: dict[str, str] = {}
        self.facts: dict[str, dict] = {}          # fixture fact_id → {"evidence_id", ...}
        self.loaded: set[str] = set()             # 已物化的 fixture
        self.state: dict = {}                     # driver 之间共享的中间状态（不进入投影）
        self.http: list[dict] = []
        self.db_log: list[dict] = []
        self.evidence: list[dict] = []
        self.notes: list[str] = []
        self.closers: list = []
        self.tmp = Path(tempfile.mkdtemp(prefix=f"rt-acc-{case['test_id']}-"))
        self.runtime = self.tmp / "runtime"
        self.h: Harness | None = None
        self._client: LoggingClient | None = None
        seed = int(hashlib.sha256(case["test_id"].encode()).hexdigest()[:8], 16)
        random.seed(seed)
        self.seed = seed
        clock.set_fixed(DEFAULT_NOW)
        reset_network_attempts()
        import io
        import sys as _sys
        self._stderr_orig = _sys.stderr
        cap = io.StringIO()
        self.state["stderr_capture"] = cap

        class _Tee:
            def write(self_, s):
                cap.write(s)
                return len(s)

            def flush(self_):
                pass
        _sys.stderr = _Tee()

    # ── 运行时 ──
    def harness(self, **kw) -> Harness:
        if self.h is None:
            roots = kw.pop("file_import_roots", None) or self.state.get("file_import_roots") or (self.tmp / "archive",)
            (self.tmp / "archive").mkdir(exist_ok=True)
            kw.setdefault("use_legacy_data_cache", False)
            kw.setdefault("port", ACCEPTANCE_PORT)
            for k, v in (self.state.get("settings_overrides") or {}).items():
                kw.setdefault(k, v)
            self.h = Harness(runtime=self.runtime, file_import_roots=roots, **kw)
            from . import fixtures
            fixtures.install_defaults(self)
        return self.h

    @property
    def db(self):
        return self.harness().db

    def serve(self) -> int:
        """把被测 App 挂到真实回环端口（浏览器用），并启动后台 worker 处理页面提交的任务。"""
        h = self.harness()
        if self.state.get("served_port"):
            return self.state["served_port"]
        port = h.serve_on_port(h.settings.port)
        if h.pool is None:
            from panel.jobs import worker
            h.pool = worker.WorkerPool(lambda: h.db, h.settings, size=2)
            h.pool.start()
        self.state["served_port"] = port
        return port

    def client(self, new: bool = False, **kw) -> LoggingClient:
        h = self.harness()
        if new:
            c = LoggingClient(self, h, **kw)
            c.bootstrap()
            return c
        if self._client is not None and self._client.context and \
                clock.parse_utc(self._client.context["expires_at"]) <= clock.now():
            # 时钟跨过会话有效期（12 小时）：像浏览器一样重新建立本地会话（旧会话已被服务端拒绝）
            old_ctx = self._client.context
            self._client = None
            self.notes.append(f"会话 {old_ctx['session_id']} 已于 {old_ctx['expires_at']} 过期，重新 bootstrap")
        if self._client is None:
            self._client = LoggingClient(self, h, **kw)
            self._client.bootstrap()
        return self._client

    def restart(self) -> None:
        """模拟进程重启：关闭并在同一 runtime 上重新装配（迁移幂等、回收中断任务）。"""
        old = self.h
        cookie, csrf = (self._client.cookie, self._client.csrf) if self._client else (None, None)
        old.close(remove=False)
        roots = old.settings.file_import_roots
        self.h = Harness(runtime=self.runtime, file_import_roots=roots, **old.settings_kw)
        from . import fixtures
        fixtures.install_defaults(self, after_restart=True)
        if self._client is not None:
            c = LoggingClient(self, self.h)
            c.cookie, c.csrf = cookie, csrf
            self._client = c

    def close(self) -> None:
        import os
        import sys as _sys
        for fn in reversed(self.closers):
            try:
                fn()
            except Exception:
                pass
        try:
            if self.h is not None:
                self.h.close(remove=False)
        finally:
            from . import fixtures
            fixtures.uninstall_overrides()
            for mod, name, f in self.state.get("p1_patched") or []:
                setattr(mod, name, f)
            if self.state.get("env_cleanup"):
                os.environ.pop(self.state["env_cleanup"], None)
            clock.set_fixed(None)
            _sys.stderr = self._stderr_orig

    # ── fixture / 别名 ──
    def fixture(self, fid: str) -> dict:
        return resolve_fixture(self.fixtures, fid)

    def ensure(self, fid: str):
        from . import fixtures
        return fixtures.ensure(self, fid)

    def alias(self, name: str) -> str:
        return self.aliases.get(name, name)

    def unalias(self, real):
        if isinstance(real, str):
            for k, v in self.aliases.items():
                if v == real:
                    return k
        return real

    # ── 结果 ──
    def out(self, inp: dict | None) -> dict:
        if inp and inp.get("save_as"):
            return self.result.setdefault(inp["save_as"], {})
        return self.result

    # ── 证据 ──
    def log_http(self, rec: dict) -> None:
        rec["at"] = clock.utc_iso()
        self.http.append(rec)

    def q(self, sql: str, params=(), purpose: str = "") -> list[dict]:
        rows = [dict(r) for r in self.db.query(sql, params)]
        self.db_log.append({"sql": sql, "params": list(params), "rows": len(rows), "purpose": purpose,
                            "sample": rows[:5]})
        return rows

    def scalar(self, sql: str, params=(), purpose: str = ""):
        v = self.db.scalar(sql, params)
        self.db_log.append({"sql": sql, "params": list(params), "value": v, "purpose": purpose})
        return v

    def add_evidence(self, path: Path, mime: str, purpose: str) -> None:
        data = Path(path).read_bytes()
        rel = Path(path).resolve().relative_to(self.case_dir.parents[1].resolve())
        self.evidence.append({"path": str(rel), "mime_type": mime, "sha256": hashlib.sha256(data).hexdigest(),
                              "produced_at": clock.utc_iso(), "purpose": purpose})

    def write_evidence(self) -> None:
        self.case_dir.mkdir(parents=True, exist_ok=True)
        if self.http:
            p = self.case_dir / "http.ndjson"
            with p.open("w", encoding="utf-8") as f:
                for r in self.http:
                    f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            self.add_evidence(p, "application/x-ndjson", "HTTP 请求/响应（脱敏）")
        if self.db_log:
            p = self.case_dir / "db_assertions.json"
            p.write_text(json.dumps(self.db_log, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            self.add_evidence(p, "application/json", "结果投影用到的 SQLite 查询")
        cap = self.state.get("stderr_capture")
        if cap is not None and cap.getvalue():
            p = self.case_dir / "server_stderr.log"
            p.write_text(cap.getvalue(), encoding="utf-8")
            self.add_evidence(p, "text/plain", "被测服务本用例期间的 stderr（不含 cookie/token）")
        p = self.case_dir / "result.json"
        p.write_text(json.dumps({"result": self.result, "aliases": self.aliases, "notes": self.notes},
                                ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        self.add_evidence(p, "application/json", "确定性结果投影")
