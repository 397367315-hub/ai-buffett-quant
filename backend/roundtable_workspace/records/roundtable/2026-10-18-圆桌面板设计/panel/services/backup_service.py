"""运行目录的一致性备份、空目录恢复与业务对账（05 §4、§7）。

备份内容：
  - SQLite：在线一致性备份（sqlite3 backup API，含 WAL 中已提交的页），不是复制主文件；
  - 不可变证据仓 evidence/blobs：内容寻址，复制前后逐个按文件名校验 SHA-256；
  - manifest.json：每个文件的 SHA-256 与字节数、schema 版本、备份时刻。
不备份 work/（席位输入物化与运行缓存，可由快照与证据重建）和 backups/ 本身。

恢复只允许到不存在或空的目录，绝不覆盖正在使用的运行目录；恢复后调用 verify_runtime 做业务对账：
schema 版本与迁移校验和、PRAGMA integrity_check / foreign_key_check、每个组合的事件哈希链、
按事件重放的现金与持股和投影表一致、冻结计划的 frozen_hash 可重算、快照指纹可重算、证据文件齐全且未被改动。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from ..domain import clock
from ..domain.canonical import content_hash

DB_NAME = "roundtable.sqlite3"


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _blob_files(runtime: Path):
    root = Path(runtime) / "evidence" / "blobs"
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.bin") if p.is_file())


def backup_runtime(db_path: Path, runtime: Path, dest_root: Path, label: str = "manual") -> dict:
    """把运行目录做一次一致性备份到 dest_root/<backup_id>/，返回清单（同时写 manifest.json）。"""
    t0 = time.monotonic()
    started = clock.utc_iso()
    backup_id = f"backup-{label}-{clock.now().strftime('%Y%m%dT%H%M%S%fZ')}"
    out = Path(dest_root) / backup_id
    out.mkdir(parents=True, exist_ok=False)
    # 1) SQLite 在线备份（独立只读连接；与正在运行的服务并发安全）
    src = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(str(out / DB_NAME))
    try:
        src.backup(dst)
        last_seq = src.execute("SELECT COALESCE(MAX(sequence),0) FROM journal_events").fetchone()[0]
        versions = [r[0] for r in src.execute("SELECT version FROM schema_migrations ORDER BY version")]
    finally:
        src.close()
        dst.close()
    chk = sqlite3.connect(str(out / DB_NAME))
    try:
        ic = chk.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        chk.close()
    if ic != "ok":
        raise RuntimeError(f"备份库完整性检查失败：{ic}")
    # 2) 证据仓：内容寻址，逐个校验后复制
    files = []
    for p in _blob_files(runtime):
        sha = _sha256_file(p)
        if p.name != f"{sha}.bin":
            raise RuntimeError(f"证据文件内容与文件名不符（拒绝备份被改动的证据）：{p.name}")
        rel = p.relative_to(runtime)
        q = out / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, q)
        files.append({"path": str(rel), "sha256": sha, "bytes": p.stat().st_size})
    db_file = {"path": DB_NAME, "sha256": _sha256_file(out / DB_NAME), "bytes": (out / DB_NAME).stat().st_size}
    manifest = {"backup_id": backup_id, "label": label, "started_at": started, "completed_at": clock.utc_iso(),
                "method": "sqlite3_backup_api_online+content_addressed_blob_copy", "db": db_file,
                "schema_versions": versions, "journal_max_sequence": last_seq, "evidence_files": len(files),
                "evidence_bytes": sum(f["bytes"] for f in files), "files": files,
                "excluded": ["work/（可重建缓存）", "backups/"], "integrity_check": ic,
                "elapsed_seconds": round(time.monotonic() - t0, 3)}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**manifest, "backup_dir": str(out)}


def restore_runtime(backup_dir: Path, target_runtime: Path) -> dict:
    """恢复到空目录：先核清单里每个文件的哈希，再用 backup API 写库、复制证据。"""
    t0 = time.monotonic()
    backup_dir = Path(backup_dir)
    target = Path(target_runtime)
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"恢复目标不是空目录，拒绝覆盖：{target}")
    m = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    bad = [f["path"] for f in [m["db"], *m["files"]] if _sha256_file(backup_dir / f["path"]) != f["sha256"]]
    if bad:
        raise RuntimeError(f"备份文件与清单不符：{bad[:5]}")
    target.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{backup_dir / DB_NAME}?mode=ro", uri=True)
    dst = sqlite3.connect(str(target / DB_NAME))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    for f in m["files"]:
        q = target / f["path"]
        q.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_dir / f["path"], q)
        try:
            os.chmod(q, 0o440)
        except OSError:
            pass
    return {"backup_id": m["backup_id"], "target": str(target), "restored_files": len(m["files"]) + 1,
            "elapsed_seconds": round(time.monotonic() - t0, 3)}


def _walk(obj, key: str, acc: set):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and isinstance(v, str):
                acc.add(v)
            else:
                _walk(v, key, acc)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, key, acc)


def verify_runtime(runtime: Path, migrations_dir: Path | None = None) -> dict:
    """只读业务对账。返回逐项结果；任何一项 False 都不能宣称恢复成功。"""
    from ..data.db import file_checksum, list_migrations
    from . import journal_service
    runtime = Path(runtime)
    c = sqlite3.connect(f"file:{runtime / DB_NAME}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA foreign_keys=ON")
        ic = c.execute("PRAGMA integrity_check").fetchone()[0]
        fk = [list(r) for r in c.execute("PRAGMA foreign_key_check").fetchall()]
        applied = {int(r["version"]): r["checksum"] for r in c.execute("SELECT version, checksum FROM schema_migrations")}
        files = {v: file_checksum(p) for v, _n, p in list_migrations()}
        migrations_ok = applied == files
        # 事件链与余额
        chains, balances = {}, {}
        for r in c.execute("SELECT portfolio_id, cash_minor, revision, last_event_sequence FROM portfolios ORDER BY portfolio_id"):
            pid = r["portfolio_id"]
            chains[pid] = journal_service.verify_chain(c, pid)
            st = journal_service.replay_events(journal_service.events_of(c, pid))
            proj = {x["symbol"]: x["shares"] for x in c.execute(
                "SELECT symbol, shares FROM positions_projection WHERE portfolio_id=?", (pid,))}
            balances[pid] = {"cash_minor_projection": r["cash_minor"], "cash_minor_replayed": st["cash"],
                             "shares_projection": proj, "shares_replayed": {k: v["shares"] for k, v in st["positions"].items()},
                             "revision": r["revision"], "last_event_sequence": r["last_event_sequence"]}
            balances[pid]["ok"] = (r["cash_minor"] == st["cash"] and proj == balances[pid]["shares_replayed"])
        # 冻结计划哈希可重算
        plans = []
        for r in c.execute("SELECT plan_id, version, frozen_hash, canonical_payload FROM plan_versions "
                           "WHERE status='frozen' ORDER BY plan_id, version"):
            p = json.loads(r["canonical_payload"])
            again = content_hash({k: v for k, v in p.items() if k != "frozen_hash"})
            plans.append({"plan_id": r["plan_id"], "version": r["version"], "frozen_hash": r["frozen_hash"],
                          "recomputed_equal": again == r["frozen_hash"] == p.get("frozen_hash")})
        # 快照指纹可重算
        snaps = []
        for r in c.execute("SELECT snapshot_id, fingerprint, canonical_payload FROM snapshots ORDER BY snapshot_id"):
            p = json.loads(r["canonical_payload"])
            again = content_hash({k: v for k, v in p.items() if k not in ("snapshot_id", "fingerprint")})
            snaps.append({"snapshot_id": r["snapshot_id"], "fingerprint": r["fingerprint"], "recomputed_equal": again == r["fingerprint"]})
        # 证据文件齐全且未改动
        referenced: set = set()
        for r in c.execute("SELECT canonical_payload FROM evidence"):
            p = json.loads(r["canonical_payload"])
            loc = (p.get("source_ref") or {}).get("locator") or p.get("locator") or ""
            if isinstance(loc, str) and loc.startswith("evidence:"):
                referenced.add(loc.split(":", 1)[1])
        blobs = {p.name[:-4]: p for p in _blob_files(runtime)}
        corrupted = [sha for sha, p in blobs.items() if _sha256_file(p) != sha]
        missing = sorted(referenced - set(blobs))
        counts = {r[0]: c.execute(f'SELECT COUNT(*) FROM "{r[0]}"').fetchone()[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()}
    finally:
        c.close()
    ok = (ic == "ok" and not fk and migrations_ok and all(v["ok"] for v in chains.values())
          and all(v["ok"] for v in balances.values()) and all(p["recomputed_equal"] for p in plans)
          and all(s["recomputed_equal"] for s in snaps) and not corrupted and not missing)
    return {"ok": ok, "integrity_check": ic, "foreign_key_errors": fk, "schema_versions": sorted(applied),
            "migration_checksums_match": migrations_ok, "journal_chains": chains, "balances": balances,
            "frozen_plans": plans, "snapshots": snaps, "evidence_referenced": len(referenced),
            "evidence_files": len(blobs), "evidence_missing": missing, "evidence_corrupted": corrupted, "row_counts": counts}
