"""测试与验收共用的进程内装配：独立临时 runtime + 真实 App（不经网络端口也能走完整 HTTP 管线）。

Client 模拟浏览器：保存 HttpOnly 会话 cookie、写请求带同源 Origin 与 CSRF、每个意图一个幂等键。
需要真实端口（浏览器/并发验收）时用 serve_on_port()。
"""
from __future__ import annotations

import json
import secrets
import shutil
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

WORK_ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = WORK_ROOT / "records/roundtable/2026-10-18-圆桌面板设计"
if str(WORKBENCH) not in sys.path:
    sys.path.insert(0, str(WORKBENCH))

from panel.config import Settings, set_settings          # noqa: E402
from panel.domain import clock                           # noqa: E402
from panel.jobs import worker                            # noqa: E402
from panel.web.app import App, Request                   # noqa: E402


class Harness:
    def __init__(self, *, workers: bool = False, port: int = 8787, runtime: Path | None = None,
                 file_import_roots: tuple = (), keep: bool = False, validate_responses: bool = True,
                 **settings_kw):
        self.tmp = Path(runtime) if runtime else Path(tempfile.mkdtemp(prefix="rt-harness-"))
        self.keep = keep or runtime is not None
        roots = tuple(Path(r) for r in file_import_roots) if file_import_roots else ()
        self.settings_kw = dict(settings_kw)
        self.settings = Settings(runtime_dir=self.tmp, port=port, validate_responses=validate_responses,
                                 file_import_roots=roots or (WORKBENCH / "panel/data/daily",), **settings_kw)
        from panel.api import build_app
        self.app, self.pool, self.interrupted = build_app(self.settings, start_workers=workers)
        self.db = self.app.db
        self.workers = workers
        self._httpd = None

    def client(self, **kw) -> "Client":
        return Client(self, **kw)

    def drain(self, max_jobs: int = 500) -> int:
        return worker.drain(self.db, self.settings, max_jobs)

    def restart(self) -> "Harness":
        """模拟进程重启：同一 runtime 重新装配（迁移幂等、回收中断任务）。"""
        self.close(remove=False)
        return Harness(workers=self.workers, port=self.settings.port, runtime=self.tmp, keep=True,
                       file_import_roots=self.settings.file_import_roots, **self.settings_kw)

    def serve_on_port(self, port: int = 0) -> int:
        from panel import api as api_mod
        api_mod._Handler.app = self.app
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), api_mod._Handler)
        self._httpd.daemon_threads = True
        t = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        t.start()
        return self._httpd.server_address[1]

    def close(self, remove: bool | None = None) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self.pool:
            self.pool.stop()
        self.db.close()
        if (remove if remove is not None else not self.keep):
            shutil.rmtree(self.tmp, ignore_errors=True)


class Client:
    def __init__(self, h: Harness, origin: str | None = None, host: str | None = None):
        self.h = h
        port = h.settings.port
        self.origin = origin or f"http://127.0.0.1:{port}"
        self.host = host or f"127.0.0.1:{port}"
        self.cookie: str | None = None
        self.csrf: str | None = None
        self.context: dict | None = None
        self.log: list[dict] = []

    # ── 底层 ──
    def request(self, method: str, path: str, body=None, headers: dict | None = None, raw: bytes | None = None):
        hdrs = {"Host": self.host, "Accept": "application/json"}
        if self.cookie:
            hdrs["Cookie"] = f"rt_local_session={self.cookie}"
        if body is not None or raw is not None:
            hdrs["Content-Type"] = "application/json"
        if headers:
            hdrs.update(headers)
        data = raw if raw is not None else (json.dumps(body, ensure_ascii=False).encode() if body is not None else b"")
        resp = self.h.app.handle(Request.build(method, path, {k: v for k, v in hdrs.items() if v is not None}, data))
        for k, v in resp.headers:
            if k.lower() == "set-cookie" and v.startswith("rt_local_session="):
                self.cookie = v.split(";")[0].split("=", 1)[1]
        try:
            obj = json.loads(resp.body) if resp.body and resp.header("Content-Type", ) and \
                "json" in resp.header("Content-Type") else None
        except ValueError:
            obj = None
        self.log.append({"method": method, "path": path, "status": resp.status,
                         "request_id": resp.header("X-Request-Id")})
        return resp.status, obj, dict(resp.headers)

    def bootstrap(self):
        st, obj, _ = self.request("POST", "/api/v1/session/bootstrap", {}, {"Origin": self.origin})
        assert st == 201, (st, obj)
        self.csrf = obj["csrf_token"]
        self.context = obj
        return obj

    def get(self, path: str, headers: dict | None = None):
        return self.request("GET", path, headers=headers)

    def post(self, path: str, body, key: str | None = None, headers: dict | None = None, csrf: bool = True,
             origin: bool = True):
        hdrs = {}
        if origin:
            hdrs["Origin"] = self.origin
        if csrf and self.csrf:
            hdrs["X-CSRF-Token"] = self.csrf
        hdrs["Idempotency-Key"] = key or f"key-{secrets.token_hex(8)}"
        if headers:
            hdrs.update(headers)
        return self.request("POST", path, body, {k: v for k, v in hdrs.items() if v is not None})

    def wait_job(self, job_id: str, timeout: float = 120.0) -> dict:
        t0 = time.time()
        while True:
            if not self.h.workers:
                self.h.drain()
            st, job, _ = self.get(f"/api/v1/jobs/{job_id}")
            assert st == 200, (st, job)
            if job["state"] in ("succeeded", "partial", "failed", "cancelled"):
                return job
            if time.time() - t0 > timeout:
                raise TimeoutError(f"job {job_id} 仍为 {job['state']}")
            time.sleep(0.05 if not self.h.workers else 0.25)

    def refresh_context(self) -> dict:
        st, obj, _ = self.get("/api/v1/session/context")
        assert st == 200, (st, obj)
        self.context = obj
        return obj
