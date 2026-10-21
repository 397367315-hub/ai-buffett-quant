"""旧接口与旧静态快照（兼容只读；训练上下文一律 409）。

- GET /api/dashboard：需要有效会话；训练上下文 409 LEGACY_DISABLED_IN_REPLAY；头部 X-API-Compatibility: legacy；
- GET /api/portfolio：只读 legacy 导出；POST /api/portfolio：409 LEGACY_LEDGER_READ_ONLY（新账本在 v1）；
- GET /api/health：只报进程存活，不含任何行情/账本数据（不能当 data-ready 验收）；
- /standalone.html、/legacy/*、/data/*：旧静态快照与数据文件不能成为绕过截点的旁路——
  无会话 401、训练上下文 409；/data/* 在真实盘也不提供（原始数据不经静态目录暴露）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ...adapters.legacy_dashboard import load_dashboard
from ...config import FRONTEND_DIST, LEGACY_DATA_DIR, WORKBENCH
from .. import security
from ..app import Ctx, Response, handler
from ..errors import ApiError


_BUILD_STAMP: str | None = None


def build_stamp() -> str:
    """启动时算一次：路由表 + 前端产物 + 入口脚本的指纹。启动器用它判断「后台跑的是不是旧版本」。"""
    global _BUILD_STAMP
    if _BUILD_STAMP is None:
        panel = Path(__file__).resolve().parents[2]
        h = hashlib.sha256()
        for f in (panel / "web" / "operations.json", FRONTEND_DIST / "index.html", panel / "api.py"):
            try:
                h.update(f.read_bytes())
            except OSError:
                h.update(b"missing")
        _BUILD_STAMP = h.hexdigest()[:16]
    return _BUILD_STAMP


def _session(app, req):
    token = security.parse_cookies(req.headers.get("cookie")).get(security.COOKIE_NAME)
    s = security.load_session(app.db, token)
    if s is None:
        raise ApiError(401, "UNAUTHENTICATED", "缺少有效的本地会话")
    return s


def _reject_replay(s) -> None:
    if s["mode"] == "replay":
        raise ApiError(409, "LEGACY_DISABLED_IN_REPLAY",
                       "训练/回放上下文不能访问旧接口或旧静态快照（会带出截点之后的真实数据）")


@handler("get_legacy_dashboard")
def legacy_dashboard(ctx: Ctx):
    _reject_replay(ctx.session)
    # 标的范围：universe=main 读「仅主板」那份；其它（含缺省）读「主板＋创业板」
    universe = (ctx.query.get("universe") or "all").lower()
    path = LEGACY_DATA_DIR / ("dashboard_main.json" if universe == "main" else "dashboard.json")
    d = load_dashboard(path)
    if d is None:
        raise ApiError(404, "LEGACY_DATA_MISSING",
                       "旧 dashboard 不存在（运行 refresh.py --once 生成 dashboard.json 与 dashboard_main.json）")
    d["universe"] = {**(d.get("universe") or {}), "id": universe if universe in ("main", "all") else "all"}
    return Response.json(200, d, {"X-API-Compatibility": "legacy", "Deprecation": "true"})


def legacy_route(app, req, rid) -> Response:
    if req.path == "/api/health" and req.method == "GET":
        return Response.json(200, {"ok": True, "legacy": True, "build": build_stamp(),
                                   "note": "仅表示进程存活；业务就绪请看 GET /api/v1/health/data"},
                             {"X-API-Compatibility": "legacy"})
    if req.path == "/api/portfolio":
        s = _session(app, req)
        _reject_replay(s)
        if req.method == "POST":
            raise ApiError(409, "LEGACY_LEDGER_READ_ONLY",
                           "旧账本已冻结为只读；请改用 /api/v1/portfolios 与 /api/v1/journal/events，"
                           "旧数据可经 POST /api/v1/portfolio/import-legacy 先 dry_run 再导入",
                           [{"field": "path", "reason": "use /api/v1/portfolio/import-legacy"}])
        if req.method == "GET":
            f = LEGACY_DATA_DIR / "portfolio.json"
            if not f.exists():
                return Response.json(200, {"exists": False, "legacy": True, "readOnly": True},
                                     {"X-API-Compatibility": "legacy"})
            d = json.loads(f.read_text(encoding="utf-8"))
            d.update({"exists": True, "legacy": True, "readOnly": True})
            return Response.json(200, d, {"X-API-Compatibility": "legacy"})
    raise ApiError(404, "NOT_FOUND", f"未知旧接口 {req.path}")


LEGACY_STATIC = {"standalone.html": "frontend/standalone.html", "legacy/standalone.html": "frontend/standalone.html",
                 "圆桌决策面板_预览.html": "圆桌决策面板_预览.html"}


def static_legacy_snapshot(app, req, rid, rel: str) -> Response:
    s = _session(app, req)
    _reject_replay(s)
    if rel.startswith("data/") or rel not in LEGACY_STATIC:
        raise ApiError(404, "NOT_FOUND", "原始数据文件不经静态目录提供；旧静态快照只有固定的几个入口")
    f = WORKBENCH / LEGACY_STATIC[rel]
    if not f.is_file():
        raise ApiError(404, "NOT_FOUND", "旧静态快照不存在")
    return Response(200, f.read_bytes(), [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store"),
                                          ("X-API-Compatibility", "legacy")])
