#!/usr/bin/env python3
"""运行目录备份 / 恢复 / 对账（给用户与运维用的命令行）。

    python3 tools/runtime_backup.py backup  --runtime <运行目录>            # 服务可以不停：SQLite 在线一致性备份
    python3 tools/runtime_backup.py restore --from <备份目录> --to <空目录>   # 只恢复到空目录，绝不覆盖
    python3 tools/runtime_backup.py verify  --runtime <运行目录>            # 只读业务对账：哈希链/余额/计划哈希/快照指纹/证据

运行目录默认取环境变量 ROUNDTABLE_RUNTIME，再退回 WORK_ROOT/runtime（与 panel/api.py 相同）。
备份默认写到 <运行目录>/backups/；建议另外复制到运行目录所在磁盘以外的位置。
退出码：0 成功；1 对账失败或备份/恢复失败；2 参数错误。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

WORK_ROOT = Path(__file__).resolve().parents[1]
WB = WORK_ROOT / "records/roundtable/2026-10-18-圆桌面板设计"
sys.path.insert(0, str(WB))

from panel.services import backup_service as bk   # noqa: E402


def _runtime(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    env = os.environ.get("ROUNDTABLE_RUNTIME")
    return Path(env).expanduser().resolve() if env else WORK_ROOT / "runtime"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--runtime")
    b.add_argument("--dest", help="备份根目录（默认 <运行目录>/backups）")
    b.add_argument("--label", default="manual")
    r = sub.add_parser("restore")
    r.add_argument("--from", dest="src", required=True)
    r.add_argument("--to", dest="dst", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--runtime")
    v.add_argument("--json", action="store_true", help="输出完整 JSON")
    a = ap.parse_args(argv)

    if a.cmd == "backup":
        rt = _runtime(a.runtime)
        db = rt / bk.DB_NAME
        if not db.exists():
            print(f"找不到数据库：{db}", file=sys.stderr)
            return 1
        m = bk.backup_runtime(db, rt, Path(a.dest).expanduser() if a.dest else rt / "backups", a.label)
        print(json.dumps({k: m[k] for k in ("backup_id", "backup_dir", "schema_versions", "journal_max_sequence",
                                             "evidence_files", "elapsed_seconds", "integrity_check")}, ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "restore":
        res = bk.restore_runtime(Path(a.src).expanduser(), Path(a.dst).expanduser())
        rep = bk.verify_runtime(Path(a.dst).expanduser())
        print(json.dumps({**res, "verify_ok": rep["ok"]}, ensure_ascii=False, indent=1))
        return 0 if rep["ok"] else 1
    rep = bk.verify_runtime(_runtime(a.runtime))
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(json.dumps({"ok": rep["ok"], "integrity_check": rep["integrity_check"],
                          "migration_checksums_match": rep["migration_checksums_match"],
                          "journal_chains_ok": all(v["ok"] for v in rep["journal_chains"].values()),
                          "balances_ok": all(v["ok"] for v in rep["balances"].values()),
                          "frozen_plans_ok": all(p["recomputed_equal"] for p in rep["frozen_plans"]),
                          "snapshots_ok": all(s["recomputed_equal"] for s in rep["snapshots"]),
                          "evidence_missing": len(rep["evidence_missing"]), "evidence_corrupted": len(rep["evidence_corrupted"])},
                         ensure_ascii=False, indent=1))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
