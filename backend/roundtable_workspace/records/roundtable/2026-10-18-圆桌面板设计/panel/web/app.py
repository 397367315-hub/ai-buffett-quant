"""HTTP 应用核心：路由、校验、会话/CSRF、幂等、统一错误（BASE-004）。

与传输层无关：api.py 的 ThreadingHTTPServer 把每个请求转成 Request 交给 App.handle()；
验收 runner 也直接调用 App.handle() 或经真实端口发 HTTP。
依赖方向：HTTP → 用例服务 → 领域/仓储；本文件不直接改账本。
"""
from __future__ import annotations

import json
import mimetypes
import re
import sqlite3
import sys
import traceback
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote

from ..config import ENABLED_PHASES, FRONTEND_DIST, Settings
from ..domain import clock
from ..domain.canonical import canonical_json, content_hash, json_dumps, new_id, sha256_hex, strict_json_loads
from ..domain.money import MoneyOutOfRange, PrecisionError
from ..domain.schema import SchemaError, contract
from . import security
from .errors import ApiError

OPERATIONS_FILE = Path(__file__).resolve().parent / "operations.json"


# ── 传输无关的请求/响应 ──
@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]                 # 键一律小写
    body: bytes = b""
    client: str = "127.0.0.1"

    @classmethod
    def build(cls, method: str, target: str, headers: dict, body: bytes = b"", client: str = "127.0.0.1"):
        path, _, qs = target.partition("?")
        return cls(method.upper(), unquote(path), parse_qs(qs, keep_blank_values=True),
                   {k.lower(): v for k, v in headers.items()}, body, client)


@dataclass
class Response:
    status: int
    body: bytes
    headers: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def json(cls, status: int, obj: Any, headers: dict | list | None = None) -> "Response":
        h = [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")]
        if isinstance(headers, dict):
            h.extend(headers.items())
        elif headers:
            h.extend(headers)
        return cls(status, json_dumps(obj).encode("utf-8"), h)

    def header(self, name: str) -> str | None:
        for k, v in self.headers:
            if k.lower() == name.lower():
                return v
        return None


# ── 路由表 ──
@dataclass
class Operation:
    operation_id: str
    method: str
    path: str
    phase: str
    needs_session: bool
    needs_csrf: bool
    idempotency_required: bool
    params: list[dict]
    request_schema: str | None
    responses: dict
    regex: re.Pattern = None
    path_params: list[str] = field(default_factory=list)


def _compile(path: str) -> tuple[re.Pattern, list[str]]:
    names = re.findall(r"\{([a-z_]+)\}", path)
    pat = "^" + re.sub(r"\{[a-z_]+\}", r"([^/]+)", re.escape(path).replace(r"\{", "{").replace(r"\}", "}")) + "$"
    return re.compile(pat), names


def load_operations() -> list[Operation]:
    doc = json.loads(OPERATIONS_FILE.read_text(encoding="utf-8"))
    ops = []
    for o in doc["operations"]:
        op = Operation(o["operation_id"], o["method"], o["path"], o["phase"] or "P0", o["needs_session"],
                       o["needs_csrf"], o["idempotency_required"], o["params"], o["request_schema"], o["responses"])
        op.regex, op.path_params = _compile(op.path)
        ops.append(op)
    # 静态路径（latest、registry、context…）优先于带参数路径
    ops.sort(key=lambda x: (x.path.count("{"), -len(x.path)))
    return ops


# ── 处理函数注册 ──
HANDLERS: dict[str, Callable] = {}


def handler(operation_id: str):
    def deco(fn):
        HANDLERS[operation_id] = fn
        return fn
    return deco


@dataclass
class Ctx:
    app: "App"
    request: Request
    op: Operation
    request_id: str
    session: dict | None
    path_params: dict[str, str]
    query: dict[str, Any]
    body: Any
    idempotency_key: str | None = None
    request_sha256: str | None = None

    @property
    def db(self):
        return self.app.db

    @property
    def settings(self) -> Settings:
        return self.app.settings

    @property
    def replay_id(self) -> str | None:
        return self.session["replay_id"] if self.session and self.session["mode"] == "replay" else None

    @property
    def cutoff(self) -> str | None:
        return self.session["knowledge_cutoff_at"] if self.session and self.session["mode"] == "replay" else None


IDEMPOTENCY_TTL = timedelta(days=30)
# 不可逆事实关联的键不自动清理
_PERMANENT_KEY_OPS = {"post_api_v1_plans_plan_id_freeze", "post_api_v1_journal_events",
                      "post_api_v1_portfolio_import_legacy", "post_api_v1_plans_plan_id_events",
                      "post_api_v1_portfolios_portfolio_id_controls_events", "post_api_v1_portfolios",
                      "post_api_v1_plans", "post_api_v1_plans_plan_id_versions"}


class App:
    def __init__(self, settings: Settings, db, feature_gate: Callable[[str], tuple[bool, str]] | None = None):
        self.settings = settings
        self.db = db
        self.ops = load_operations()
        self.contract = contract()
        self.feature_gate = feature_gate or (lambda phase: (phase in ENABLED_PHASES, f"{phase} 未启用"))
        self.access_log: list[dict] = []      # 测试/验收可读；不含 cookie/token
        from . import handlers  # noqa: F401  注册全部 handler

    # ── 入口 ──
    def handle(self, req: Request) -> Response:
        rid = new_id("req", 6)
        try:
            if req.path.startswith("/api/"):
                resp = self._api(req, rid)
            else:
                resp = self._static(req, rid)
        except ApiError as ex:
            resp = Response.json(ex.status, ex.payload(rid), ex.headers)
        except Exception as ex:   # pragma: no cover - 兜底，不回显栈
            print(f"[{rid}] 未处理异常 {type(ex).__name__}: {ex}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            resp = Response.json(500, ApiError(500, "INTERNAL_ERROR", "服务内部错误（详情见本机日志）").payload(rid))
        resp.headers.extend([("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                             ("X-Request-Id", rid)])
        self.access_log.append({"request_id": rid, "method": req.method, "path": req.path,
                                "status": resp.status, "at": clock.utc_iso()})
        if len(self.access_log) > 5000:
            del self.access_log[:1000]
        return resp

    # ── API ──
    def _api(self, req: Request, rid: str) -> Response:
        security.check_host(req.headers, self.settings.allowed_hosts())
        if req.method == "OPTIONS":
            raise ApiError(403, "CORS_NOT_SUPPORTED", "不提供跨源访问")
        if req.path.startswith("/api/") and not req.path.startswith("/api/v1/") and req.path != "/api/dashboard":
            from .handlers.legacy import legacy_route
            return legacy_route(self, req, rid)
        op, pparams = self._match(req)
        enabled, why = self.feature_gate(op.phase)
        limit = self.settings.max_import_body_bytes if op.operation_id == "post_api_v1_portfolio_import_legacy" \
            else self.settings.max_body_bytes
        clen = req.headers.get("content-length")
        if (clen and clen.isdigit() and int(clen) > limit) or len(req.body) > limit:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", f"请求体超过 {limit} 字节上限")
        # 写请求先验来源：不可信 Origin 无论有没有会话都 403，且不触碰数据库
        if req.method == "POST":
            security.check_origin(req.headers, self.settings.allowed_origins())
            ctype = (req.headers.get("content-type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                raise ApiError(400, "UNSUPPORTED_MEDIA_TYPE", "POST 只接受 application/json")
        session = None
        if op.needs_session:
            token = security.parse_cookies(req.headers.get("cookie")).get(security.COOKIE_NAME)
            session = security.load_session(self.db, token)
            if session is None:
                raise ApiError(401, "UNAUTHENTICATED", "缺少有效的本地会话；请先 POST /api/v1/session/bootstrap")
            if session["mode"] == "replay":
                rep = self.db.one("SELECT knowledge_cutoff_at FROM replay_sessions WHERE replay_id=?",
                                  (session["replay_id"],))
                if rep:
                    session["knowledge_cutoff_at"] = rep["knowledge_cutoff_at"]
            security.check_replay_header(req.headers, session)
            if req.method == "POST" and op.needs_csrf:
                security.check_csrf(req.headers, session)
        if not enabled:
            raise ApiError(501, "FEATURE_DISABLED", why)
        query = self._parse_query(op, req)
        body = None
        # 有 schema 的操作按 schema 校验；契约扩展（ADR-006/007 这类没有新增模型的）也要能拿到 JSON 体，
        # 否则 handler 永远读到 None。两者都先解析，再按需校验。
        if op.request_schema or (req.method == "POST" and (req.body or b"").strip()):
            try:
                body = strict_json_loads(req.body or b"{}")
            except (ValueError, UnicodeDecodeError) as ex:
                raise ApiError(400, "BAD_JSON", f"请求体不是合法 JSON：{ex}")
            if op.request_schema:
                self._validate(op.request_schema, body)
        ikey = None
        rhash = None
        if op.idempotency_required:
            ikey = (req.headers.get("idempotency-key") or "").strip()
            if not (8 <= len(ikey) <= 128):
                raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "POST 必须带 Idempotency-Key（8–128 字符）",
                               [{"field": "Idempotency-Key", "reason": "missing or invalid length"}])
            rhash = content_hash({"body": body, "query": query})
        ctx = Ctx(self, req, op, rid, session, pparams, query, body, ikey, rhash)
        if isinstance(body, dict) and "replay_id" in body and op.operation_id != "post_api_v1_session_context":
            if body["replay_id"] != ctx.replay_id:
                raise ApiError(409, "REPLAY_CONTEXT_CONFLICT", "请求体 replay_id 与服务端会话上下文不一致",
                               [{"field": "replay_id", "reason": f"session context is {ctx.replay_id!r}"}])
        fn = HANDLERS.get(op.operation_id)
        if fn is None:
            raise ApiError(501, "NOT_IMPLEMENTED", f"{op.operation_id} 尚未实现")
        if op.idempotency_required:
            return self._idempotent(ctx, fn)
        return self._run(ctx, fn)

    def _match(self, req: Request) -> tuple[Operation, dict]:
        known_path = False
        for op in self.ops:
            m = op.regex.match(req.path)
            if not m:
                continue
            known_path = True
            if op.method != req.method:
                continue
            return op, dict(zip(op.path_params, m.groups()))
        if known_path:
            raise ApiError(404, "METHOD_NOT_SUPPORTED", f"{req.method} 不支持该路径")
        raise ApiError(404, "NOT_FOUND", f"未知路径 {req.path}")

    def _parse_query(self, op: Operation, req: Request) -> dict:
        allowed = {p["name"]: p for p in op.params if p["in"] == "query"}
        out: dict[str, Any] = {}
        for k, vals in req.query.items():
            if k not in allowed:
                raise ApiError(400, "UNKNOWN_PARAMETER", f"不支持的查询参数 {k}", [{"field": k, "reason": "unknown"}])
            v = vals[-1]
            sch = allowed[k]["schema"]
            t = sch.get("type")
            if t == "integer":
                if not re.fullmatch(r"-?\d+", v):
                    raise ApiError(422, "VALIDATION_ERROR", f"{k} 必须是整数", [{"field": k, "reason": "integer"}])
                iv = int(v)
                if "minimum" in sch and iv < sch["minimum"] or "maximum" in sch and iv > sch["maximum"]:
                    raise ApiError(422, "VALIDATION_ERROR", f"{k} 超出范围", [{"field": k, "reason": "range"}])
                out[k] = iv
            elif t == "boolean":
                if v not in ("true", "false"):
                    raise ApiError(422, "VALIDATION_ERROR", f"{k} 必须是 true/false", [{"field": k, "reason": "boolean"}])
                out[k] = v == "true"
            else:
                if "pattern" in sch and not re.search(sch["pattern"], v):
                    raise ApiError(422, "VALIDATION_ERROR", f"{k} 格式不符", [{"field": k, "reason": "pattern"}])
                out[k] = v
        for name, p in allowed.items():
            if name not in out and "default" in p["schema"]:
                out[name] = p["schema"]["default"]
            if p["required"] and name not in out:
                raise ApiError(422, "VALIDATION_ERROR", f"缺少必填查询参数 {name}", [{"field": name, "reason": "required"}])
        for p in op.params:
            if p["in"] == "path":
                pass
        return out

    def _validate(self, model: str, body: Any) -> None:
        try:
            self.contract.check(model, body)
        except SchemaError as ex:
            if ex.has_unknown_fields:
                unk = [i for i in ex.issues if i["kind"] == "unknown_field"]
                raise ApiError(400, "UNKNOWN_FIELD", f"请求包含未知字段：{unk[0]['field']}",
                               [{"field": i["field"], "reason": i["reason"]} for i in unk])
            raise ApiError(422, "VALIDATION_ERROR", f"请求字段不合法：{ex}",
                           [{"field": i["field"], "reason": i["reason"]} for i in ex.issues[:20]])

    def _call(self, ctx: Ctx, fn) -> Response:
        try:
            res = fn(ctx)
        except MoneyOutOfRange as ex:
            raise ApiError(422, "MONEY_OUT_OF_RANGE", str(ex))
        except PrecisionError as ex:
            raise ApiError(422, "PRECISION_ERROR", str(ex))
        except sqlite3.IntegrityError as ex:
            msg = str(ex)
            code = msg if re.fullmatch(r"[A-Z_]+", msg) else "INTEGRITY_CONFLICT"
            raise ApiError(409, code, f"数据约束拒绝：{msg}")
        if isinstance(res, Response):
            return res
        status, obj, *rest = res
        hdrs = rest[0] if rest else None
        if self.settings.validate_responses:
            sch = (ctx.op.responses.get(str(status)) or {}).get("schema")
            if sch and self.contract.has(sch):
                iss = self.contract.issues(sch, obj)
                if iss:
                    raise RuntimeError(f"响应不符合契约 {sch}: {iss[:3]}")
        return Response.json(status, obj, hdrs)

    def _run(self, ctx: Ctx, fn) -> Response:
        return self._call(ctx, fn)

    def _idempotent(self, ctx: Ctx, fn) -> Response:
        """同一事务：查幂等键 → 执行业务 → 写幂等响应 → 提交。同键同体回放；同键异体 409。"""
        path = ctx.request.path
        with self.db.txn() as c:
            row = c.execute("SELECT request_sha256, response_status, response_json FROM idempotency_keys "
                            "WHERE method='POST' AND request_path=? AND idempotency_key=?",
                            (path, ctx.idempotency_key)).fetchone()
            if row:
                if row["request_sha256"] != ctx.request_sha256:
                    raise ApiError(409, "IDEMPOTENCY_CONFLICT", "同一个 Idempotency-Key 被用于不同的请求",
                                   [{"field": "Idempotency-Key", "reason": "already used with a different request"}])
                stored = json.loads(row["response_json"])
                return Response.json(int(row["response_status"]), stored["body"], stored.get("headers") or {})
            resp = self._call(ctx, fn)
            if resp.status >= 400:
                return resp
            body = json.loads(resp.body)
            headers = {k: v for k, v in resp.headers if k in ("Location", "Retry-After")}
            rtype, rid_, jid = _resource_of(body)
            perm = ctx.op.operation_id in _PERMANENT_KEY_OPS
            c.execute("""INSERT INTO idempotency_keys(method, request_path, idempotency_key, request_sha256,
                         response_status, response_json, resource_type, resource_id, job_id, replay_id,
                         created_at, expires_at) VALUES('POST',?,?,?,?,?,?,?,?,?,?,?)""",
                      (path, ctx.idempotency_key, ctx.request_sha256, resp.status,
                       canonical_json({"body": body, "headers": headers}), rtype, rid_, jid, ctx.replay_id,
                       clock.utc_iso(), None if perm else clock.utc_iso(clock.now() + IDEMPOTENCY_TTL)))
            return resp

    # ── 静态资源 ──
    def _static(self, req: Request, rid: str) -> Response:
        if req.method not in ("GET", "HEAD"):
            raise ApiError(404, "NOT_FOUND", "静态资源只支持 GET")
        from .handlers.legacy import static_legacy_snapshot
        rel = req.path.lstrip("/")
        if any(part in ("..", ".") for part in rel.replace("\\", "/").split("/")) or "\x00" in rel:
            raise ApiError(404, "NOT_FOUND", "资源不存在")          # 路径穿越一律 404（在任何路由之前）
        if rel in ("standalone.html", "legacy/standalone.html", "圆桌决策面板_预览.html") or rel.startswith(("data/", "legacy/")):
            security.check_host(req.headers, self.settings.allowed_hosts())
            return static_legacy_snapshot(self, req, rid, rel)
        root = FRONTEND_DIST.resolve()
        if rel in ("", "index.html"):
            target = root / "index.html"
        else:
            if any(part.startswith(".") for part in rel.split("/")) or "\\" in rel:
                raise ApiError(404, "NOT_FOUND", "资源不存在")
            target = (root / rel).resolve()
            if root not in target.parents and target != root:
                raise ApiError(404, "NOT_FOUND", "资源不存在")
        if not target.is_file():
            if "." not in rel.split("/")[-1]:
                target = root / "index.html"      # SPA 深链
            else:
                raise ApiError(404, "NOT_FOUND", "资源不存在")
        if not target.is_file():
            raise ApiError(503, "FRONTEND_NOT_BUILT", "缺少前端构建产物 frontend/dist")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        headers = [("Content-Type", ctype)]
        if target.name == "index.html":
            headers += [("Cache-Control", "no-store"),
                        ("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; "
                                                    "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                                                    "frame-ancestors 'none'; base-uri 'self'")]
        else:
            headers.append(("Cache-Control", "no-cache"))
        return Response(200, target.read_bytes() if req.method == "GET" else b"", headers)


def _resource_of(body: dict) -> tuple[str | None, str | None, str | None]:
    if not isinstance(body, dict):
        return None, None, None
    if "job" in body and isinstance(body["job"], dict):
        j = body["job"]
        return j.get("resource_type"), j.get("resource_id"), j.get("job_id")
    for key, rtype in (("plan_id", "plan"), ("event_id", "event"), ("portfolio_id", "portfolio"),
                       ("import_id", "import"), ("deck_id", "deck"), ("alert_id", "alert"),
                       ("session_id", "session"), ("replay_id", "replay")):
        if body.get(key):
            return rtype, body[key], None
    return None, None, None


def request_body_hash(raw: bytes) -> str:
    return sha256_hex(raw)
