#!/usr/bin/env python3
"""生成交接包（handoff/）与完整备份（backup/*.zip）（ADR-014）。

    python3 tools/make_handoff.py                 # 用当前 release_manifest.json 里的 release_id
    python3 tools/make_handoff.py --skip-backup   # 只生成 handoff/ 目录

handoff/：给 AI 巴菲特量化分析系统部署方的可读材料（文档副本 + 契约 + 清单 + 逐文件 SHA-256）。
backup/：整棵工作区的 zip（排除 node_modules、运行目录、缓存数据、验收截图等可重建/非代码内容）。

排除规则写在 SKIP_DIRS / SKIP_FILES / SKIP_GLOBS，宁可少打数据，也不把密钥、运行库、几十万张截图塞进去。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WB = ROOT / "records/roundtable/2026-10-18-圆桌面板设计"
HANDOFF = ROOT / "handoff"
BACKUP = ROOT / "backup"

SKIP_DIRS = {"node_modules", "__pycache__", ".git", "runtime", "backup", "output", "audit",
             ".pytest_cache", ".venv", "__MACOSX"}
SKIP_FILES = {"panel/data/features.json", "panel/data/features_compact.json", "panel/data/valuation.json",
              ".DS_Store"}
SKIP_PREFIXES = ("panel/data/daily/", "artifacts/acceptance/", "records/roundtable/2026-10-18-圆桌面板设计/panel/data/daily/")
MAX_FILE_MB = 30

HANDOFF_FILES = [
    "docs/implementation/INTEGRATION_GUIDE.md",
    "docs/implementation/FINAL_HANDOFF.md",
    "docs/USER_GUIDE.md",
    "docs/TRAINING_AND_VALIDATION.md",
    "docs/implementation/llm-feasibility.md",
    "docs/implementation/replay-data-eligibility.md",
    "docs/implementation/alerts-feasibility.md",
    "docs/implementation/task-status.md",
    "contracts/openapi.yaml",
    "contracts/models.schema.json",
    "contracts/vendor/numcat-openapi.json",
    "release/release_manifest.json",
    "release/source_files.sha256",
    "records/roundtable/2026-10-18-圆桌面板设计/panel/services/data_catalog.json",
    "records/roundtable/2026-10-18-圆桌面板设计/panel/README.md",
    "records/roundtable/2026-10-18-圆桌面板设计/frontend/README.md",
    "打开交易圆桌.command",
]

README = """# 交接包 · 交易圆桌 · 十三席

给 **AI 巴菲特量化分析系统** 的部署与对接方。先读 `INTEGRATION_GUIDE.md`。

| 你想做什么 | 看哪里 |
|---|---|
| 跑起来、调接口、部署检查清单 | `INTEGRATION_GUIDE.md` |
| 验收结果、发布候选、已知边界 | `FINAL_HANDOFF.md` + `release/release_manifest.json` |
| 日常怎么用（含总指挥、圆桌、席位对话） | `USER_GUIDE.md` |
| 训练/回放/验证的口径 | `TRAINING_AND_VALIDATION.md` |
| 模型接入与数据源可行性 | `llm-feasibility.md` |
| 接口定义 | `contracts/openapi.yaml`（56 个操作）、`contracts/models.schema.json`（58 个模型） |
| 猫爪接口目录（94 个，89 个可查） | `data_catalog.json` + `contracts/vendor/numcat-openapi.json` |

`manifest.json` 记录本包的指纹、生成时间与各文件 SHA-256；完整代码备份在 `../backup/`。
"""


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def build_handoff(release: dict) -> dict:
    if HANDOFF.exists():
        shutil.rmtree(HANDOFF)
    (HANDOFF / "decisions").mkdir(parents=True)
    (HANDOFF / "README.md").write_text(README, encoding="utf-8")
    copied = []
    for name in HANDOFF_FILES:
        src = ROOT / name
        if not src.is_file():
            continue
        dst = HANDOFF / src.name if "/" not in name else HANDOFF / Path(name).name
        shutil.copy2(src, dst)
        copied.append(dst)
    for adr in sorted((ROOT / "docs/implementation/decisions").glob("ADR-*.md")):
        dst = HANDOFF / "decisions" / adr.name
        shutil.copy2(adr, dst)
        copied.append(dst)
    files = []
    for p in sorted(copied):
        files.append({"path": rel(p) if p.is_relative_to(ROOT) else f"handoff/{p.name}",
                      "bytes": p.stat().st_size, "sha256": sha256(p)})
    (HANDOFF / "source_files.sha256").write_text(
        "".join(f"{f['sha256']}  {f['path']}\n" for f in files), encoding="utf-8")
    cat = json.loads((WB / "panel/services/data_catalog.json").read_text(encoding="utf-8"))
    manifest = {
        "kind": "roundtable-handoff",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "release_id": release.get("release_id"), "state": release.get("state"),
        "source_sha256": release.get("source_sha256"),
        "counts": {
            "handoff_files": len(files),
            "contract_operations": 56,
            "data_api_ops": len(cat["ops"]),
            "data_api_queryable": sum(1 for o in cat["ops"] if not o.get("stream") and not o.get("download")),
        },
        "files": files,
        "start": {
            "run": "python3 records/roundtable/2026-10-18-圆桌面板设计/panel/api.py --port 8787",
            "runtime_env": "ROUNDTABLE_RUNTIME=<运行目录>",
            "model_key_env": "DEEPSEEK_API_KEY（或页面「模型设置」）",
            "data_cli_env": "MEOZ_CLI=~/Downloads/meoz-2/scripts/meoz.py",
        },
        "note": "只读交接包；代码的完整备份在 ../backup/。不含任何密钥、不含运行目录、不含原书 PDF。",
    }
    (HANDOFF / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return manifest


def _skip(path: Path, size: int) -> bool:
    r = rel(path)
    parts = set(path.parts)
    if parts & SKIP_DIRS:
        return True
    if r in SKIP_FILES or path.name in SKIP_FILES or path.name.endswith(".pyc"):
        return True
    if any(r.startswith(pre) for pre in SKIP_PREFIXES):
        return True
    if path.name == ".DS_Store":
        return True
    if size > MAX_FILE_MB * 1024 * 1024:
        return True
    return False


def build_backup(release_id: str) -> Path:
    BACKUP.mkdir(exist_ok=True)
    for old in BACKUP.glob("交易圆桌_十三席_WORK_*.zip*"):
        old.unlink()
    out = BACKUP / f"交易圆桌_十三席_WORK_{release_id or 'latest'}.zip"
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            p = Path(dirpath)
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                f = p / fn
                try:
                    size = f.stat().st_size
                except OSError:
                    continue
                if _skip(f, size):
                    continue
                z.write(f, rel(f))
                n += 1
    digest = sha256(out)
    out.with_suffix(".zip.sha256").write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    print(f"备份：{out}（{n} 个文件，{out.stat().st_size / 1e6:.1f} MB）\nSHA-256：{digest}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成交接包与完整备份")
    ap.add_argument("--skip-backup", action="store_true")
    a = ap.parse_args(argv)
    rel_manifest = ROOT / "release/release_manifest.json"
    release = json.loads(rel_manifest.read_text(encoding="utf-8")) if rel_manifest.is_file() else {}
    m = build_handoff(release)
    print(f"交接包：{HANDOFF}（{m['counts']['handoff_files']} 个文件；数据接口 {m['counts']['data_api_queryable']}/"
          f"{m['counts']['data_api_ops']} 可查；release {m['release_id']}）")
    if not a.skip_backup:
        build_backup(m.get("release_id") or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
