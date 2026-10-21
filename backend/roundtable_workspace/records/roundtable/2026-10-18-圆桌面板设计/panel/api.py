#!/usr/bin/env python3
"""交易圆桌本地服务入口（BASE-004）。

    python3 panel/api.py                 # 默认只监听 127.0.0.1:8787
    python3 panel/api.py --port 8790
    python3 panel/api.py --no-workers    # 只读调试：不执行后台任务

- 只做配置、组装路由与工作线程；业务在 web/handlers 与 services；
- 默认只绑定回环地址；任何非回环地址都需要 --allow-remote，而远程部署另有身份网关验收（05 文档），
  不能只把 bind 改成 0.0.0.0；
- 启动时：迁移（迁移前一致性备份）→ 回收上次进程中断的任务 → 写入当前 13 席注册表 → 启动 4 个工作线程；
- 没有自动刷新、行情订阅、系统通知或开机自启；行情只在用户点击「采集」时取（POST /api/v1/snapshots）。
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 以脚本方式运行时 sys.path[0] 是 panel/ 本身；改为其父目录，避免 panel/ 下的包名遮蔽标准库
sys.path = [p for p in sys.path if Path(p or ".").resolve() != HERE]
sys.path.insert(0, str(HERE.parent))

from panel.config import FRONTEND_DIST, Settings, set_settings          # noqa: E402
from panel.data import db as dbm                                       # noqa: E402
from panel.jobs import queue, worker                                   # noqa: E402
from panel.web.app import App, Request                                 # noqa: E402

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def build_app(settings: Settings, start_workers: bool = True):
    settings.ensure_dirs()
    set_settings(settings)
    db = dbm.Database(settings.db_path)
    db.migrate(backup_dir=settings.backup_dir)
    dbm.reset_db(db)
    interrupted = queue.recover_on_boot(db)
    from panel.services import boot  # noqa: E402  注册任务处理器与重启恢复钩子
    boot.after_restart(db, interrupted)
    app = App(settings, db, feature_gate=boot.feature_gate(db))
    from panel.web.handlers.seats import ensure_registry
    from panel.web.handlers.journal import seed_policies
    from panel.services import alert_service
    ensure_registry(db)
    seed_policies(db)                      # 版本化冷却预设（只追加）
    alert_service.stop_all_on_boot(db)     # P4 观察会话重启后不自动恢复
    pool = None
    if start_workers:
        pool = worker.WorkerPool(lambda: db, settings, size=settings.max_active_jobs)
        pool.start()
    return app, pool, interrupted


class _Handler(BaseHTTPRequestHandler):
    app: App = None
    server_version = "roundtable-local"
    sys_version = ""

    def _serve(self, method: str):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        # 超过上限的请求体不读入内存；App 按 Content-Length 返回 413（HTTP/1.0 响应后即断开连接）
        body = self.rfile.read(n) if 0 < n <= self.app.settings.max_import_body_bytes else b""
        req = Request.build(method, self.path, dict(self.headers.items()), body, self.client_address[0])
        resp = self.app.handle(req)
        try:
            self.send_response(resp.status)
            for k, v in resp.headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(resp.body)))
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(resp.body)
        except (BrokenPipeError, ConnectionResetError):
            # 页面关闭/刷新导致连接先断：不是服务错误，不打印回溯（避免日志里一堆假异常）
            pass

    def do_GET(self):
        self._serve("GET")

    def do_HEAD(self):
        self._serve("HEAD")

    def do_POST(self):
        self._serve("POST")

    def do_OPTIONS(self):
        self._serve("OPTIONS")

    def do_PUT(self):
        self._serve("PUT")

    def do_DELETE(self):
        self._serve("DELETE")

    def log_message(self, fmt, *args):      # 不记录 cookie / token / 请求体
        if os.environ.get("ROUNDTABLE_QUIET") != "1":
            sys.stderr.write(f"{self.address_string()} {self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}\n")


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="交易圆桌本地服务（单用户、仅本机）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default=DEFAULT_HOST, help="监听地址，默认仅本机回环")
    ap.add_argument("--allow-remote", action="store_true", help="允许非回环地址（远程部署须另做身份网关验收）")
    ap.add_argument("--no-workers", action="store_true", help="不启动后台任务线程（只读调试）")
    ap.add_argument("--dev-origin", action="append", default=[], help="额外允许的本机开发 Origin，如 http://127.0.0.1:5173")
    return ap


def host_allowed(host: str, allow_remote: bool) -> bool:
    return host in LOOPBACK or allow_remote


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if not host_allowed(a.host, a.allow_remote):
        print(f"拒绝在 {a.host} 监听：v1 是本机单用户；远程需显式 --allow-remote 且另行完成身份网关验收", file=sys.stderr)
        return 2
    if not (FRONTEND_DIST / "index.html").exists():
        print(f"提示：缺少前端构建产物 {FRONTEND_DIST}/index.html（cd frontend && npm ci && npm run build）", file=sys.stderr)
    for o in a.dev_origin:
        if not o.startswith(("http://127.0.0.1:", "http://localhost:")):
            print(f"拒绝 dev origin {o}：只允许本机地址", file=sys.stderr)
            return 2
    s = Settings(host=a.host, port=a.port, dev_origins=tuple(a.dev_origin))
    app, pool, interrupted = build_app(s, start_workers=not a.no_workers)
    if interrupted:
        print(f"已把 {len(interrupted)} 个上次进程中断的任务标为 failed/PROCESS_INTERRUPTED", file=sys.stderr)
    _Handler.app = app
    httpd = ThreadingHTTPServer((a.host, a.port), _Handler)
    httpd.daemon_threads = True

    def _graceful(signum, frame):            # SIGTERM / 关闭终端窗口(SIGHUP) 与 Ctrl-C 同样走干净停止：不再接新请求 → 停工作线程 → 关库
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _graceful)
    # 关窗口（SIGHUP）也干净停；但用 nohup 等方式显式忽略了 SIGHUP 的后台运行要尊重，不去改它
    if hasattr(signal, "SIGHUP") and signal.getsignal(signal.SIGHUP) == signal.SIG_DFL:
        signal.signal(signal.SIGHUP, _graceful)
    print(f"交易圆桌本地服务：http://{a.host}:{a.port}/   数据库 {s.db_path}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        if pool:
            pool.stop()
        try:
            app.db.close()
        except Exception:                      # pragma: no cover
            pass
        print("交易圆桌本地服务已停止（工作线程已停，数据库已关闭）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
