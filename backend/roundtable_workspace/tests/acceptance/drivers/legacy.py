"""源件 / 旧账本 / 旧回测 driver（BASE-002 / JOURNAL-005 / EVAL-001）。"""
from __future__ import annotations

import builtins
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

from panel.domain import faults
from panel.domain.canonical import content_hash, sha256_hex
from panel.services import legacy_evaluation, legacy_importer, source_registry

from ..runner import byte_manifest
from ..spec import WORK_ROOT


def _src_root(ctx, given: str | None = None) -> Path:
    if given:
        p = Path(given)
        if p.is_dir():
            return p
        alt = WORK_ROOT.parent / p.name          # 挂载环境下 /Users/<u>/Desktop/X ↔ 工作区同级目录
        if alt.is_dir():
            return alt
        return p
    return Path(ctx.harness().settings.source_root)


@contextmanager
def write_guard(root: Path):
    """登记期间对源包的任何写入意图都计数并拒绝（open 写模式 / replace / rename / unlink / mkdir）。"""
    counts = {"writes": 0}
    root = root.resolve()
    o_open, o_replace, o_rename, o_unlink, o_mkdir = builtins.open, os.replace, os.rename, os.unlink, os.mkdir

    def inside(p) -> bool:
        try:
            return root in Path(p).resolve().parents or Path(p).resolve() == root
        except Exception:
            return False

    def g_open(file, mode="r", *a, **k):
        if isinstance(file, (str, os.PathLike)) and any(m in mode for m in "wax+") and inside(file):
            counts["writes"] += 1
            raise PermissionError(f"source root is read-only: {file}")
        return o_open(file, mode, *a, **k)

    def g_two(fn):
        def inner(src, dst, *a, **k):
            if inside(src) or inside(dst):
                counts["writes"] += 1
                raise PermissionError("source root is read-only")
            return fn(src, dst, *a, **k)
        return inner

    def g_one(fn):
        def inner(p, *a, **k):
            if inside(p):
                counts["writes"] += 1
                raise PermissionError("source root is read-only")
            return fn(p, *a, **k)
        return inner
    builtins.open, os.replace, os.rename, os.unlink, os.mkdir = g_open, g_two(o_replace), g_two(o_rename), g_one(o_unlink), \
        g_one(o_mkdir)
    try:
        yield counts
    finally:
        builtins.open, os.replace, os.rename, os.unlink, os.mkdir = o_open, o_replace, o_rename, o_unlink, o_mkdir


def source_manifest_capture(ctx, inp):
    root = _src_root(ctx, ctx.fixture("legacy_source")["root"])
    ctx.result[f"source_files_{inp.get('save_as') or 'snapshot'}"] = byte_manifest(root)
    ctx.state["source_root"] = root


def source_registration_dry_run(ctx, inp):
    root = _src_root(ctx, inp.get("source_root") or ctx.fixture("legacy_source")["root"])
    with write_guard(root) as g:
        r = source_registry.dry_run(root)
    out_dir = ctx.runtime / "source_registration"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dry_run.json").write_text(json.dumps({k: v for k, v in r.items() if k not in ("declared", "actual")} |
                                                     {"declared_sha256": content_hash(r["declared"]),
                                                      "actual_sha256": content_hash(r["actual"])},
                                                     ensure_ascii=False, indent=1), encoding="utf-8")
    fx_src = ctx.fixture("legacy_source")
    ex = fx_src["frozen_example"]
    ctx.result.update({"imported_original_hashes": r["actual"], "declared_original_hashes": r["declared"],
                       "source_writes": g["writes"], "registration_ok": r["ok"], "mismatches": r["mismatches"],
                       "missing": r["missing"], "declared_count": r.get("declared_count"),
                       "frozen_example_sha256": r["actual"].get(ex), "frozen_example_declared": fx_src["declared_example_sha256"]})
    ctx.state["p0_ok"] = r["ok"]


# ── 旧账本导入（中断恢复 + 冲突保留） ──
def _ledger_texts(ctx, with_conflict: bool) -> tuple[str, str]:
    c = ctx.fixture("legacy_ledger_conflict")
    t = c["portfolio_trade"]
    trades = [
        {"id": "t_fixture_1", "ts": "2026-10-16T02:00:00Z", "code": "600001", "name": "合成甲", "side": "buy", "shares": 100,
         "price": 10.0, "amount": 1000.0, "fee": {"commission": 5.0, "stamp": 0.0, "transfer": 0.01, "total": 5.01},
         "net": -1005.01},
        {"id": "t_fixture_2", "ts": "2026-10-16T02:10:00Z", "code": "000001", "name": "合成乙", "side": "buy", "shares": 100,
         "price": 12.0, "amount": 1200.0, "fee": {"commission": 5.0, "stamp": 0.0, "transfer": 0.01, "total": 5.01},
         "net": -1205.01},
        {"id": t["id"] if with_conflict else "t_fixture_3", "ts": "2026-10-16T02:20:00Z", "code": "600002", "name": "合成丙",
         "side": "buy", "shares": 100,
         "price": float(t["price"]), "amount": float(t["price"]) * 100,
         "fee": {"commission": 5.0, "stamp": 0.0, "transfer": 0.01, "total": 5.01}, "net": -(float(t["price"]) * 100 + 5.01)},
    ]
    cash = round(100000 - sum(-x["net"] for x in trades), 2)
    ledger = {"ledger": {"initial": 100000.0, "cash": cash, "trades": trades, "watch": []}}
    lt = json.dumps(ledger, ensure_ascii=False, indent=1)
    lines = []
    for x in trades:
        y = dict(x, savedAt="2026-10-16T03:00:00Z")
        if with_conflict and x["id"] == c["jsonl_trade"]["id"]:
            y["price"] = float(c["jsonl_trade"]["price"])
            y["amount"] = y["price"] * 100
        lines.append(json.dumps(y, ensure_ascii=False))
    return lt, "\n".join(lines) + "\n"


def _import_body(lt: str, tt: str, dry_run: bool) -> dict:
    return {"dry_run": dry_run, "source_kind": "uploaded_json", "source_sha256": legacy_importer.combined_sha(lt, tt),
            "ledger_json_text": lt, "ledger_sha256": sha256_hex(lt.encode()), "trades_jsonl_text": tt,
            "trades_sha256": sha256_hex(tt.encode()), "allow_cash_adjustment": False}


def legacy_import(ctx, inp):
    ctx.harness()
    root = _src_root(ctx, ctx.fixture("legacy_source")["root"])
    ctx.state["source_root"] = root
    ctx.result.setdefault("source_files_before", byte_manifest(root))
    lt, tt = _ledger_texts(ctx, with_conflict=True)
    ctx.state["legacy_body"] = _import_body(lt, tt, dry_run=False)
    ctx.state["legacy_key"] = "legacy-import-fixture-0001"
    if inp.get("fault_at") == "after_first_batch":
        faults.arm("legacy_import_after_first_batch")
    st, obj, _ = ctx.client().post("/api/v1/portfolio/import-legacy", ctx.state["legacy_body"], key=ctx.state["legacy_key"])
    ctx.result.setdefault("import_attempts", []).append({"status": st, "result": obj})
    # 同一中断场景在无冲突账本上：第一批之后故障 → 回滚 → 同键重试只提交一次
    faults.disarm_all()
    lt2, tt2 = _ledger_texts(ctx, with_conflict=False)
    body2 = _import_body(lt2, tt2, dry_run=False)
    faults.arm("legacy_import_after_first_batch")
    s1, o1, _ = ctx.client().post("/api/v1/portfolio/import-legacy", body2, key="legacy-clean-0001")
    faults.disarm_all()
    s2, o2, _ = ctx.client().post("/api/v1/portfolio/import-legacy", body2, key="legacy-clean-0001")
    s3, o3, _ = ctx.client().post("/api/v1/portfolio/import-legacy", body2, key="legacy-clean-0002")
    commits = ctx.scalar("SELECT COUNT(*) FROM import_manifests WHERE source_sha256=? AND status='committed'",
                         (body2["source_sha256"],), purpose="无冲突账本的提交次数")
    ctx.result["clean_resume_check"] = {"fault_status": s1, "resume_status": s2, "resume_again_status": s3,
                                        "committed_imports": commits,
                                        "same_import_returned": o2.get("import_id") == o3.get("import_id")}


def legacy_import_resume(ctx, inp):
    st, obj, _ = ctx.client().post("/api/v1/portfolio/import-legacy", ctx.state["legacy_body"], key=ctx.state["legacy_key"])
    ctx.result.setdefault("import_attempts", []).append({"status": st, "result": obj})
    body = ctx.state["legacy_body"]
    commits = ctx.scalar("SELECT COUNT(*) FROM import_manifests WHERE source_sha256=? AND status='committed'",
                         (body["source_sha256"],), purpose="冲突账本的提交次数")
    legacy_events = ctx.scalar("SELECT COUNT(*) FROM journal_events WHERE legacy_trade_id=?", ("same_trade",),
                               purpose="冲突成交是否被导入")
    rejected_rows = [r for a in ctx.result["import_attempts"] if isinstance(a["result"], dict)
                     for r in a["result"].get("rejected_rows") or []]
    clean = ctx.result.get("clean_resume_check") or {}
    ctx.result.update({"duplicate_import_count": max(0, commits - 1) + max(0, (clean.get("committed_imports") or 1) - 1),
                       "conflict_records_preserved": any(r.get("legacy_trade_id") == "same_trade" for r in rejected_rows),
                       "conflicting_trade_auto_accepted": legacy_events > 0,
                       "import_statuses": [a["result"].get("status") if isinstance(a["result"], dict) and "status" in a["result"]
                                           else a["status"] for a in ctx.result["import_attempts"]]})
    root = ctx.state.get("source_root")
    if root is not None:
        ctx.result["source_files_after"] = byte_manifest(root)


def legacy_evaluation_import(ctx, inp):
    root = Path(ctx.harness().settings.source_root)
    before = byte_manifest(root / legacy_evaluation.LEGACY_DIR_REL)
    m = legacy_evaluation.material()
    after = byte_manifest(root / legacy_evaluation.LEGACY_DIR_REL)
    ctx.state["legacy_material"] = m
    ctx.result.update({"original_artifacts_mutated": before != after or not before, "legacy_material_status": m["status"],
                       "legacy_rows": len(m["rows"])})
