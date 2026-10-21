"""安全边界类 driver：bind host、Origin/CSRF、会话 401、legacy 409、训练上下文越权、路径穿越、秘密 canary。"""
from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path

from panel import api as api_mod
from panel.domain import clock

from .. import fixtures as fx
from . import infra


def _event_count(ctx) -> int:
    return int(ctx.scalar("SELECT (SELECT COUNT(*) FROM journal_events) + (SELECT COUNT(*) FROM plan_versions) + "
                          "(SELECT COUNT(*) FROM portfolio_control_events) + (SELECT COUNT(*) FROM idempotency_keys)",
                          purpose="写入类表的行数合计"))


def server_config_inspect(ctx, inp):
    ap = api_mod.build_parser()
    d = ap.parse_args([])
    refused = api_mod.host_allowed("0.0.0.0", False) is False
    h = ctx.harness()
    allow = sorted(h.settings.allowed_origins())
    ctx.result.update({"bind_host": d.host, "default_port": d.port, "remote_bind_refused_without_flag": refused,
                       "allowed_origins": allow})


def untrusted_origin_post(ctx, inp):
    h = ctx.harness()
    before = _event_count(ctx)
    c = fx.__dict__  # noqa: F841
    from tests.support.harness import Client
    cl = Client(h, origin=inp["origin"], host=f"{inp.get('host') or '127.0.0.1'}:{h.settings.port}")
    if inp.get("valid_session_token"):
        cl.bootstrap()
    st, obj, hd = cl.post("/api/v1/portfolios", {"mode": "paper", "initial_cash_minor": "100", "name": "x"})
    st2, obj2, hd2 = cl.request("OPTIONS", "/api/v1/portfolios", headers={"Origin": inp["origin"],
                                                                          "Access-Control-Request-Method": "POST"})
    ctx.log_http({"method": "POST", "path": "/api/v1/portfolios", "request_headers": {"Origin": inp["origin"]},
                  "status": st, "response_body": obj})
    after = _event_count(ctx)
    acao = [v for k, v in list(hd.items()) + list(hd2.items()) if k.lower() == "access-control-allow-origin"]
    ctx.result.update({"http_status": st, "error_code": obj["error"]["code"] if st >= 400 else None,
                       "db_event_delta": after - before, "allow_origin_star": "*" in acao,
                       "preflight_status": st2})


def csrf_write_attack_matrix(ctx, inp):
    ctx.harness()
    a = ctx.client(new=True)
    b = ctx.client(new=True)          # 另一个会话的 CSRF
    before = _event_count(ctx)
    statuses, codes = [], []
    for state in inp["csrf_states"]:
        hdr = {}
        if state == "missing":
            st, obj, _ = a.post("/api/v1/portfolios", {"mode": "paper", "initial_cash_minor": "100", "name": "x"}, csrf=False)
        elif state == "wrong_session_token":
            st, obj, _ = a.post("/api/v1/portfolios", {"mode": "paper", "initial_cash_minor": "100", "name": "x"},
                                csrf=False, headers={"X-CSRF-Token": b.csrf})
        else:
            st, obj, _ = a.post("/api/v1/portfolios", {"mode": "paper", "initial_cash_minor": "100", "name": "x"},
                                csrf=False, headers={"X-CSRF-Token": "SYNTHETIC_CSRF_FIXTURE_ONLY"})
        statuses.append(st)
        codes.append(obj["error"]["code"] if st >= 400 else None)
    after = _event_count(ctx)
    ok_st, _, _ = a.post("/api/v1/portfolios", {"mode": "paper", "initial_cash_minor": "100", "name": "control"})
    ctx.result.update({"http_statuses": statuses, "error_codes": codes, "db_event_delta": after - before,
                       "cookie_alone_grants_write": any(s < 400 for s in statuses),
                       "control_with_valid_csrf_status": ok_st})


def unauthenticated_data_request_matrix(ctx, inp):
    h = ctx.harness()
    ctx.ensure("snapshot_final")
    from tests.support.harness import Client
    targets = {"quotes": "/api/v1/snapshots/latest?allow_stale=true",
               "replay": "/api/v1/replays/replay_fixture_main",
               "evidence": f"/api/v1/snapshots/{ctx.alias('snapshot_fixture_final')}"}
    statuses, rows = [], 0
    for state in inp["session_states"]:
        for t in inp["targets"]:
            cl = Client(h)
            if state == "expired":
                cl.bootstrap()
                with ctx.db.txn() as c:
                    c.execute("UPDATE local_sessions SET expires_at=? WHERE session_id=?",
                              ("2026-01-01T00:00:00Z", cl.context["session_id"]))
            elif state == "invalid_signature":
                cl.cookie = secrets.token_urlsafe(32)
            st, obj, _ = cl.get(targets[t])
            ctx.log_http({"method": "GET", "path": targets[t], "session_state": state, "status": st, "response_body": obj})
            statuses.append(st)
            if st == 200:
                rows += 1
    ctx.result.update({"http_statuses": statuses, "private_data_rows_returned": rows,
                       "anonymous_live_fallback": any(s == 200 for s in statuses)})


def session_authenticate(ctx, inp):
    ctx.ensure("server_session_context")
    cl = ctx.state["sessions"][inp["session_id"]]
    ctx.state["current_session"] = cl
    ctx.result["authenticated_session"] = inp["session_id"]


def future_markers(ctx) -> dict[str, list[str]]:
    """未来事实的可识别标记：fixture fact_id、其证据 ID；未来 live 快照的 ID 及其全部证据 ID。"""
    rf = ctx.fixture("replay_future")
    out: dict[str, list[str]] = {}
    for f in rf["future_facts"]:
        fid = f["fact_id"]
        eid = (ctx.facts.get(fid) or {}).get("evidence_id")
        out[fid] = [fid] + ([eid] if eid else [])
    fut = [r["evidence_id"] for r in ctx.db.query("SELECT evidence_id FROM evidence WHERE snapshot_id=?",
                                                  ("snapshot_fixture_future_live",))]
    out.setdefault("f_close_future", []).extend(["snapshot_fixture_future_live"] + fut)
    return out


def scan_future(ctx, blob: str) -> list[str]:
    return sorted(fid for fid, marks in future_markers(ctx).items() if any(m and m in blob for m in marks))


def _scan_future(ctx, payloads: list) -> list[str]:
    _positive_control(ctx)
    return scan_future(ctx, json.dumps(payloads, ensure_ascii=False, default=str))


def _positive_control(ctx) -> None:
    """检测器自检：用真实盘会话读取同一份「未来」数据，标记必须能被识别出来，否则扫描结果不可信。"""
    if "detector_positive_control" in ctx.result:
        return
    live = (ctx.state.get("sessions") or {}).get("session_fixture_live")
    if live is None:
        return
    st, snap, _ = live.get("/api/v1/snapshots/snapshot_fixture_future_live")
    facts = [json.loads(r["canonical_payload"]) for r in ctx.db.query(
        "SELECT canonical_payload FROM evidence WHERE snapshot_id IS NULL AND replay_id IS NULL AND "
        "json_extract(canonical_payload, '$.field_path') LIKE 'facts.%'")]
    hits = scan_future(ctx, json.dumps([snap, facts], ensure_ascii=False, default=str))
    expect = sorted(future_markers(ctx))
    ctx.result["detector_positive_control"] = st == 200 and hits == expect
    ctx.result["detector_markers"] = {k: len(v) for k, v in future_markers(ctx).items()}


def replay_data_request(ctx, inp):
    cl = ctx.state["current_session"]
    hdr = {}
    if inp.get("optional_replay_header"):
        hdr["X-Replay-Id"] = inp["optional_replay_header"]
    paths = ["/api/v1/snapshots/latest", "/api/v1/session/context", "/api/v1/workspace/index",
             "/api/v1/snapshots/snapshot_fixture_future_live", "/api/v1/health/data"]
    if inp.get("query_cutoff_at"):
        paths = [p + ("&" if "?" in p else "?") + f"cutoff_at={inp['query_cutoff_at']}" for p in paths[:1]] + paths
    payloads, statuses, codes = [], [], []
    for p in paths:
        st, obj, _ = cl.get(p, headers=hdr)
        statuses.append(st)
        payloads.append(obj)
        codes.append(obj["error"]["code"] if isinstance(obj, dict) and "error" in obj else None)
    ctx_after = cl.get("/api/v1/session/context")[1]
    rejected = all(s >= 400 for s in statuses[:1]) if hdr or inp.get("query_cutoff_at") else False
    ctx.result.update({"http_statuses": statuses, "error_codes": codes,
                       "authorized_replay_id": ctx_after.get("replay_id"),
                       "effective_cutoff_at": ctx_after.get("knowledge_cutoff_at"),
                       "future_fact_ids": _scan_future(ctx, payloads),
                       "header_omission_unbound_session": ctx_after.get("mode") != "replay",
                       "request_rejected": rejected,
                       "error_code": next((c.lower() for c in codes if c), None),
                       "server_replay_id_after": ctx_after.get("replay_id"),
                       "server_cutoff_after": ctx_after.get("knowledge_cutoff_at")})


def replay_scoped_attack_matrix(ctx, inp):
    cl = ctx.state["current_session"]
    before = cl.get("/api/v1/session/context")[1]
    accepted, payloads, detail = 0, [], []
    for a in inp["attempts"]:
        if a == "foreign_replay_id":
            st, obj, _ = cl.get("/api/v1/snapshots/latest", headers={"X-Replay-Id": "replay_fixture_other"})
        elif a == "future_snapshot_id":
            st, obj, _ = cl.get("/api/v1/snapshots/snapshot_fixture_future_live")
        elif a == "forged_cutoff_body":
            st, obj, _ = cl.post("/api/v1/session/context", {"expected_context_revision": before["context_revision"],
                                                              "mode": "replay", "replay_id": before["replay_id"],
                                                              "acknowledge_training_end": False,
                                                              "knowledge_cutoff_at": "2026-10-23T07:10:00Z"})
        else:   # new_cookie_context_in_header：试图用自造的上下文头/第二个 cookie 改上下文
            st, obj, _ = cl.get("/api/v1/snapshots/snapshot_fixture_future_live",
                                headers={"X-Session-Context": json.dumps({"mode": "live"}),
                                         "Cookie": f"rt_local_session={cl.cookie}; rt_local_session_mode=live"})
        ok = 200 <= st < 300
        accepted += 1 if ok else 0
        payloads.append(obj)
        detail.append({"attempt": a, "status": st, "code": obj["error"]["code"] if isinstance(obj, dict) and "error" in obj else None})
    after = cl.get("/api/v1/session/context")[1]
    ctx.result.update({"accepted_attack_count": accepted, "attempts": detail,
                       "forbidden_fact_ids": _scan_future(ctx, payloads),
                       "server_context_mutated": (before["replay_id"], before["knowledge_cutoff_at"], before["mode"]) !=
                                                 (after["replay_id"], after["knowledge_cutoff_at"], after["mode"])})


def legacy_data_request_matrix(ctx, inp):
    ctx.ensure("server_session_context")
    targets = {"legacy_dashboard": "/api/dashboard", "legacy_portfolio": "/api/portfolio",
               "legacy_static_snapshot": "/standalone.html"}
    statuses, rows, bypass = [], 0, False
    for sid in inp["session_ids"]:
        cl = ctx.state["sessions"][sid]
        for t in inp["targets"]:
            st, obj, hd = cl.request("GET", targets[t], headers={"Accept": "text/html,application/json"})
            statuses.append(st)
            ctx.log_http({"method": "GET", "path": targets[t], "session": sid, "status": st,
                          "response_body": obj if isinstance(obj, dict) else None})
            if st == 200:
                rows += 1
        for extra in ("/data/dashboard.json", "/legacy/standalone.html", "/圆桌决策面板_预览.html"):
            st, obj, _ = cl.request("GET", extra)
            if st == 200:
                bypass = True
    ctx.result.update({"http_statuses": statuses, "future_or_live_rows_returned": rows,
                       "static_data_bypass_allowed": bypass})


def adversarial_http_requests(ctx, inp):
    h = ctx.harness()
    cl = ctx.client()
    before = _event_count(ctx)
    src_root = Path(h.settings.source_root)
    probes = []
    for sc in inp["scenarios"]:
        if sc == "dotdot_static_path":
            for p in ("/../../../../etc/passwd", "/assets/../../panel/config.py", "/%2e%2e/%2e%2e/contracts/openapi.yaml",
                      "/..%2f..%2fpanel%2fapi.py"):
                st, obj, hd = cl.request("GET", p)
                probes.append((sc, p, st, obj))
        elif sc == "evidence_arbitrary_absolute_path":
            for p in ("/api/v1/snapshots/%2Fetc%2Fpasswd", f"/api/v1/snapshots/{src_root}/MANIFEST.json",
                      "/data/../../../MANIFEST.json", "/legacy/..%2F..%2Fdata%2Fportfolio.json"):
                st, obj, hd = cl.request("GET", p)
                probes.append((sc, p, st, obj))
        elif sc == "oversized_json_body":
            big = {"mode": "paper", "initial_cash_minor": "100", "name": "x" * (2 << 20)}
            st, obj, hd = cl.post("/api/v1/portfolios", big)
            probes.append((sc, "/api/v1/portfolios (2 MiB)", st, obj))
        elif sc == "null_origin_write":
            st, obj, hd = cl.post("/api/v1/portfolios", {"mode": "paper", "initial_cash_minor": "100", "name": "x"},
                                  headers={"Origin": "null"})
            probes.append((sc, "/api/v1/portfolios (Origin: null)", st, obj))
    after = _event_count(ctx)
    leaked = 0
    markers = [b"rt_local_session", b"NUMCAT_API_KEY"]
    for f in (src_root / "MANIFEST.json", src_root / "AGENTS.md"):
        if f.is_file():
            markers.append(f.read_bytes()[:64])
    for _, _, st, obj in probes:
        body = json.dumps(obj, ensure_ascii=False).encode() if obj is not None else b""
        if st == 200 and any(m and m in body for m in markers):
            leaked += 1
    ctx.result.update({"probes": [{"scenario": a, "target": b, "status": c} for a, b, c, _ in probes],
                       "accepted_attack_requests": sum(1 for _, _, st, _ in probes if 200 <= st < 300),
                       "leaked_source_bytes": leaked, "db_event_delta": after - before})


# ── 秘密 canary ──
def secret_canary_inject(ctx, inp):
    ctx.state["canary"] = inp["value"]
    os.environ["NUMCAT_API_KEY"] = inp["value"]       # 只有被测 adapter 的子进程能看到（假 CLI 会故意回显）
    ctx.state["env_cleanup"] = "NUMCAT_API_KEY"


def force_provider_error(ctx, inp):
    ctx.ensure("snapshot_final")
    scen = infra.provider_scenario_ok(ctx, ["2026-10-16", "2026-10-19"])
    scen["faults"]["reference_calendar_range"] = ["auth"]
    scen["faults"]["reference_stock_daily"] = ["auth"]
    infra.install_fake_cli(ctx, scen, max_retry=0, timeout=5.0)
    st, acc, _ = ctx.client().post("/api/v1/snapshots", {"session_date": "2026-10-19", "source_mode": "provider_api",
                                                         "symbols": [], "allow_stale": True})
    job = ctx.client().wait_job(acc["job"]["job_id"], timeout=120) if st == 202 else None
    st2, j, _ = ctx.client().get(f"/api/v1/jobs/{acc['job']['job_id']}") if st == 202 else (None, None, None)
    ctx.result.update({"provider_error_job_state": job["state"] if job else None,
                       "provider_error_code": (job["error"] or {}).get("error", {}).get("code") if job else None})


def acceptance_artifact_scan(ctx, inp):
    canary = inp["canary"].encode()
    hits = []
    # 1) 本用例的全部 HTTP 往返（含错误体）与 runtime（SQLite、证据仓、导入备份、日志）
    blob = json.dumps(ctx.http, ensure_ascii=False, default=str).encode()
    if canary in blob:
        hits.append("http_log")
    for p in ctx.runtime.rglob("*"):
        if p.is_file() and canary in p.read_bytes():
            hits.append(f"runtime:{p.relative_to(ctx.runtime)}")
    for p in ctx.case_dir.rglob("*") if ctx.case_dir.exists() else []:
        if p.is_file() and canary in p.read_bytes():
            hits.append(f"artifact:{p.name}")
    captured = ctx.state.get("stderr_capture")
    if captured is not None and canary in captured.getvalue().encode():
        hits.append("server_stderr")
    # 前端包里出现「api_key:」这种字段名是正常的（设置弹窗要传字段），不是凭据。
    # 这里只认真正的密钥形态，并留一个正对照，证明检测器本身没瞎。
    SECRET_RE = re.compile(
        rb"NUMCAT_API_KEY|NUMCAT_ENV_FILE|numcat\.env"
        rb"|api[_-]?key\s*[:=]\s*process\.env"
        rb"|sk-[A-Za-z0-9_-]{16,}"
        rb"|(?i:bearer\s+[A-Za-z0-9._-]{20,})"
        rb"|-----BEGIN [A-Z ]*PRIVATE KEY-----")
    detector_positive_control = bool(SECRET_RE.search(b"const k = 'sk-" + b"a" * 24 + b"';"))

    # 2) 前端构建包：不能有凭据或 canary
    from panel.config import FRONTEND_DIST
    bundle_cred = False
    for p in FRONTEND_DIST.rglob("*") if FRONTEND_DIST.exists() else []:
        if p.is_file() and p.suffix in (".js", ".html", ".css", ".json", ".map"):
            b = p.read_bytes()
            if canary in b:
                hits.append(f"bundle:{p.name}")
            if SECRET_RE.search(b):
                bundle_cred = True
    # 3) 错误响应不能回显栈或环境变量
    echo = False
    for r in ctx.http:
        body = json.dumps(r.get("response_body"), ensure_ascii=False, default=str)
        if "Traceback" in body or "NUMCAT_API_KEY" in body or "environ" in body:
            echo = True
    os.environ.pop("NUMCAT_API_KEY", None)
    ctx.result.update({"canary_matches": sorted(set(hits)), "api_credentials_in_bundle": bundle_cred,
                       "detector_positive_control": detector_positive_control,
                       "error_stack_env_echo": echo})
