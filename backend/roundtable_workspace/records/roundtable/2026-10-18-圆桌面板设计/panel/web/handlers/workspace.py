"""工作区索引（契约扩展 ADR-001）：列出当前上下文可见的 Case / 计划 / 组合 / 回放 / 验证 / 卡组 / 复盘，
以及服务端只读历史归档的指纹（创建训练与方法验证登记时需要冻结它）。

v1 契约没有列表接口，浏览器清空本地状态后就找不回已冻结的计划与复盘；本扩展只读、按会话命名空间过滤，
不返回任何新业务字段之外的数据。见 docs/implementation/decisions/ADR-001-workspace-index.md。
"""
from __future__ import annotations

import json

from ..app import Ctx, handler


@handler("get_api_v1_workspace_index")
def index(ctx: Ctx):
    db, rid = ctx.db, ctx.replay_id
    lim = ctx.query.get("limit", 100)
    cases = []
    for r in db.query("SELECT canonical_payload FROM cases WHERE replay_id IS ? ORDER BY created_at DESC LIMIT ?", (rid, lim)):
        c = json.loads(r["canonical_payload"])
        cases.append({k: c[k] for k in ("case_id", "title", "state", "snapshot_id", "primary_strategy_id", "symbols",
                                        "horizon", "supersedes_case_id", "created_at", "finished_at")})
    plans = []
    for r in db.query("""SELECT p.canonical_payload FROM plan_versions p JOIN (
                           SELECT plan_id, MAX(version) v FROM plan_versions WHERE replay_id IS ? GROUP BY plan_id) m
                         ON p.plan_id=m.plan_id AND p.version=m.v ORDER BY p.created_at DESC LIMIT ?""", (rid, lim)):
        p = json.loads(r["canonical_payload"])
        plans.append({k: p[k] for k in ("plan_id", "version", "status", "title", "purpose", "case_id", "symbols",
                                        "execution_eligibility", "primary_strategy_id", "created_at", "frozen_at")})
    portfolios = []
    for r in db.query("SELECT portfolio_id, mode, revision, created_at, canonical_payload FROM portfolios WHERE replay_id IS ? "
                      "ORDER BY created_at DESC LIMIT ?", (rid, lim)):
        meta = json.loads(r["canonical_payload"])
        portfolios.append({"portfolio_id": r["portfolio_id"], "mode": r["mode"], "revision": r["revision"],
                           "name": meta.get("name") or "", "created_at": r["created_at"]})
    replays = []
    if rid is None:
        for r in db.query("SELECT replay_id, state, start_session_date, end_session_date, cursor_session_date, revealed, "
                          "created_at FROM replay_sessions ORDER BY created_at DESC LIMIT ?", (lim,)):
            replays.append({k: r[k] for k in r.keys()} | {"revealed": bool(r["revealed"])})
    else:
        r = db.one("SELECT replay_id, state, start_session_date, end_session_date, cursor_session_date, revealed, created_at "
                   "FROM replay_sessions WHERE replay_id=?", (rid,))
        if r:
            replays.append({k: r[k] for k in r.keys()} | {"revealed": bool(r["revealed"])})
    validations = []
    for r in db.query("SELECT validation_id, state, target_type, target_id, created_at, finished_at FROM validation_runs "
                      "WHERE replay_id IS ? ORDER BY created_at DESC LIMIT ?", (rid, lim)):
        validations.append({k: r[k] for k in r.keys()})
    decks = []
    for r in db.query("SELECT deck_id, version, source_type, source_id, created_at, canonical_payload FROM card_decks "
                      "WHERE replay_id IS ? ORDER BY created_at DESC LIMIT ?", (rid, lim)):
        d = json.loads(r["canonical_payload"])
        decks.append({"deck_id": r["deck_id"], "version": r["version"], "title": d["title"], "source_type": r["source_type"],
                      "source_id": r["source_id"], "created_at": r["created_at"]})
    reviews = []
    for r in db.query("SELECT review_id, plan_id, plan_version, review_kind, run_state, created_at FROM reviews "
                      "WHERE replay_id IS ? ORDER BY created_at DESC LIMIT ?", (rid, lim)):
        reviews.append({k: r[k] for k in r.keys()})
    from ...services import archive_service
    m = archive_service.manifest()
    archive = None
    if m["files"]:
        archive = {"fingerprint": m["fingerprint"], "coverage_start": m["coverage_start"], "coverage_end": m["coverage_end"],
                   "session_count": m["session_count"], "calendar_version": m["calendar_version"]}
        if rid is not None:
            rep = db.one("SELECT knowledge_cutoff_at FROM replay_sessions WHERE replay_id=?", (rid,))
            # 训练上下文：只告诉归档指纹与起点，不暴露截点之后的覆盖范围
            archive = {"fingerprint": m["fingerprint"], "coverage_start": m["coverage_start"], "coverage_end": None,
                       "session_count": None, "calendar_version": m["calendar_version"],
                       "note": f"训练上下文：只显示截点 {rep['knowledge_cutoff_at'] if rep else '?'} 之前的数据"}
    return 200, {"replay_id": rid, "cases": cases, "plans": plans, "portfolios": portfolios, "replays": replays,
                 "validations": validations, "decks": decks, "reviews": reviews, "archive": archive}
