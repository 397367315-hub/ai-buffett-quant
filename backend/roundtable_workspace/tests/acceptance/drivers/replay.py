"""训练 / 时点 driver（REPLAY-001 / REPLAY-002）。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from panel.data.evidence_store import EvidenceStore
from panel.domain import clock, universe as uv
from panel.domain.canonical import content_hash
from panel.services import archive_service, replay_service, snapshot_service

from .. import fixtures as fx
from ..errors import Blocked


# ── 时点事实 ──
def replay_payload_get(ctx, inp):
    ctx.ensure("publication")
    if "dividend" in ctx.case["fixture_ids"] and "f_dividend_2026" not in ctx.facts:
        d = ctx.fixture("dividend")
        fx.standalone_fact(ctx, {"fact_id": "f_dividend_2026", "available_at": d["cash_available_at"],
                                 "first_observed_at": d["cash_available_at"], "time_precision": "timestamp",
                                 "value": d["cash_per_share"]}, source_id="fixture_dividend_feed")
    cutoff = inp["cutoff_at"]
    view = replay_service.facts_payload(ctx.db, cutoff, fx.calendar_of(ctx))
    if inp.get("facts"):
        allow = set(inp["facts"])
        view = {**view, "visible": [f for f in view["visible"] if f["fact_id"] in allow],
                "excluded": {k: v for k, v in view["excluded"].items() if k in allow}}
    vis_ids = sorted(f["fact_id"] for f in view["visible"])
    # 把这一截点的载荷存成不可变快照（与训练可见快照同一个事实筛选），用于事后核对没有被改写
    snap_fx = dict(ctx.fixture("snapshot_final"), facts=[])
    evid = replay_service._fact_evidence_for_snapshot(view, f"payload:{cutoff}")
    r = fx.ingest_fixture_snapshot(ctx, snap_fx, snapshot_id=f"snap_payload_{re.sub(r'[^0-9]', '', cutoff)[:12]}",
                                   extra_evidence=evid, lineage_key=f"payload:{cutoff}")
    n = len(ctx.state.setdefault("payload_calls", []))
    ctx.state["payload_calls"].append({"cutoff": cutoff, "snapshot_id": r["snapshot_id"], "digest": r["digest"]})
    fin = next((f.get("value") for f in view["visible"] if f["fact_id"] in ("f_financial_2025", "f_financial_2025_revision")), None)
    if n == 0:
        ctx.result.update({"before_fact_ids": vis_ids, "first_financial_value": fin,
                           "future_dividend_in_first": "f_dividend_2026" in vis_ids})
    else:
        ctx.result.update({"after_fact_ids": vis_ids, "second_financial_value": fin})
    first = ctx.state["payload_calls"][0]
    row = ctx.db.one("SELECT canonical_payload FROM snapshots WHERE snapshot_id=?", (first["snapshot_id"],))
    ctx.result["first_snapshot_mutated"] = content_hash(json.loads(row["canonical_payload"])) != first["digest"]
    ctx.result.update({"eligible_fact_ids": vis_ids, "exclusion_reasons": sorted(set(view["excluded"].values())),
                       "excluded": view["excluded"], "superseded": view["superseded"],
                       "report_period_used_as_available_at": view["report_period_used_as_available_at"],
                       "synthetic_midnight_assigned": view["synthetic_midnight_assigned"],
                       "policy_version": view["policy_version"]})


def replay_universe_get(ctx, inp):
    f = ctx.fixture("historical_universe")
    cut = clock.shanghai_date(clock.parse_utc(inp["cutoff_at"]))
    r = uv.historical_members(f["members"], cut)
    ctx.result.update({"symbols": r["symbols"], "status_by_symbol": {k.replace(".", "_"): v for k, v in r["status_by_symbol"].items()},
                       "excluded": r["excluded"], "current_survivor_filter_used": r["current_survivor_filter_used"]})


# ── 合成归档上的真实回放 ──
ARCHIVE_DAYS_BEFORE = 70


def ensure_archive(ctx, through: str = "2026-10-21"):
    ctx.harness()          # 先装配被测服务：归档根目录来自它的 Settings.file_import_roots
    if ctx.state.get("archive_days"):
        return ctx.state["archive_days"]
    days = fx.weekdays("2026-04-01", 260)
    days = [d for d in days if d <= through]
    root = fx.write_archive(ctx, days, symbols=24, seed=ctx.seed % 10000)
    ctx.state["archive_days"] = days
    ctx.state["archive_root"] = root
    return days


def _visible_input_hash(snap: dict) -> str:
    """截点可见输入的规范哈希：只取原始数据集（角色、日期、原字节 sha256），不含 ID 与时间戳。"""
    items = sorted([e["field_path"], e["source_ref"]["content_sha256"]] for e in snap["evidence"]
                   if e["kind"] == "api_fact" and e["field_path"] and (e["field_path"].startswith("seat_inputs.daily")
                                                                        or e["field_path"] == "daily_bars[*]"))
    return content_hash(items)


def replay_run(ctx, inp):
    ensure_archive(ctx)
    cut = clock.parse_utc(inp["cutoff_at"])
    day = clock.shanghai_date(cut)
    m = archive_service.manifest()
    cl = ctx.client(new=True)
    syms = ["600001.SH", "000001.SZ"]
    st, acc, _ = cl.post("/api/v1/replays", {"symbols": syms, "start_session_date": day,
                                             "end_session_date": ctx.state["archive_days"][-1], "strict_as_of": True,
                                             "data_archive_fingerprint": m["fingerprint"], "initial_cash_minor": "1000000"})
    if st != 202:
        raise AssertionError(f"replay create {st} {acc}")
    job = cl.wait_job(acc["job"]["job_id"], timeout=600)
    rid = acc["job"]["resource_id"]
    st, rep, _ = cl.get(f"/api/v1/replays/{rid}")
    st, snap, _ = cl.get(f"/api/v1/snapshots/{rep['visible_snapshot_id']}")
    body = {"seat_id": "trading-wyckoff-v1", "snapshot_id": snap["snapshot_id"], "symbols": syms, "cycle_id": "cycle_replay_hash",
            "llm_mode": "disabled", "provider_id": None, "model_id": None, "user_context_id": None, "replay_id": rid}
    st, acc2, _ = cl.post("/api/v1/seat-runs", body)
    cl.wait_job(acc2["job"]["job_id"], timeout=600)
    st, run, _ = cl.get(f"/api/v1/seat-runs/{acc2['job']['resource_id']}")
    out_ev = [e for e in run["evidence_ids"] if e]
    raw_shas = sorted(json.loads(r["canonical_payload"])["source_ref"]["content_sha256"] for r in ctx.db.query(
        "SELECT canonical_payload FROM evidence WHERE evidence_id IN (%s) AND json_extract(canonical_payload, '$.field_path') LIKE 'seat_outputs.%%'"
        % ",".join("?" * len(out_ev)), out_ev)) if out_ev else []
    nums = sorted(re.findall(r"-?\d+(?:\.\d+)?", run["explanation"]))
    o = ctx.out(inp)
    o.update({"replay_id": rid, "knowledge_cutoff_at": rep["knowledge_cutoff_at"], "visible_session": snap["metadata"]["session_date"],
              "canonical_input_hash": _visible_input_hash(snap),
              "deterministic_rules_hash": content_hash({"raw": raw_shas, "verdict": run["verdict"],
                                                        "conditions": [(c["condition_id"], c["result"]) for c in run["condition_results"]]}),
              "numeric_claims": nums, "archive_fingerprint": m["fingerprint"],
              "future_days_in_archive": [d for d in ctx.state["archive_days"] if d > day]})
    b, a = ctx.result.get("before"), ctx.result.get("after")
    if b and a:
        ctx.result["llm_numeric_claims_equal"] = b["numeric_claims"] == a["numeric_claims"]
        ctx.result["llm_mode_note"] = "llm_mode=disabled：数值声明只来自规则解释文本；两次逐项相同"


def future_fixture_replace(ctx, inp):
    days = ensure_archive(ctx)
    root = Path(ctx.state["archive_root"])
    fut = [d for d in days if d > "2026-10-16"]
    for d in fut:
        p = root / f"{d.replace('-', '')}.json"
        rows = json.loads(p.read_text(encoding="utf-8"))
        for r in rows:
            if r["symbol"] in ("600001", "000001"):
                r["close"] = float(inp["future_price"])
                r["high"] = max(r["high"], r["close"])
        p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    ctx.result["replaced_future_days"] = fut


def replay_fact_inject(ctx, inp):
    ctx.ensure("server_session_context")
    cl = ctx.state["sessions"]["session_fixture_replay"]
    rid = cl.context["replay_id"]
    rep = ctx.db.one("SELECT isolated_portfolio_id, knowledge_cutoff_at FROM replay_sessions WHERE replay_id=?", (rid,))
    pid = rep["isolated_portfolio_id"]
    st, pf, _ = cl.get(f"/api/v1/portfolios/{pid}")
    eid = ctx.facts[inp["client_fact_id"]]["evidence_id"]
    body = fx.note_body(pid, pf["revision"], json.dumps({"schema": "diary_v1", "kind": "evidence_note",
                                                          "claimed_available_at": inp["claimed_available_at"],
                                                          "text": "客户端声称这条事实早已可知"}, ensure_ascii=False),
                        effective_at=rep["knowledge_cutoff_at"], replay_id=rid)
    body["evidence_ids"] = [eid]
    st, obj, _ = cl.post("/api/v1/journal/events", body)
    payloads = [cl.get("/api/v1/snapshots/latest")[1], cl.get("/api/v1/workspace/index")[1],
                cl.get(f"/api/v1/journal/events?portfolio_id={pid}")[1]]
    from .security import _scan_future
    ctx.result.update({"http_status": st, "error_code": obj["error"]["code"].lower() if st >= 400 else None,
                       "visible_future_fact_count": len(_scan_future(ctx, payloads))})
