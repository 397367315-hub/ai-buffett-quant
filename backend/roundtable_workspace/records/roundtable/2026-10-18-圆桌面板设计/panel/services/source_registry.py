"""源件登记（BASE-002 / AC-001 / AC-104）：只读核对源包原字节与其冻结清单，不写源包一个字节。

- 声明哈希来自源包自带的 `MANIFEST-20261019.sha256`（sha256sum 格式）；
- 实际哈希逐个读取原字节计算；不一致 → mismatches，不通过更新声明去「修复」；
- dry_run 只把登记结果写到 runtime（不在源包内建任何文件）；登记前后由调用方比对字节清单。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

DECLARED_MANIFEST = "MANIFEST-20261019.sha256"


def parse_sha256sum(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        h, _, path = line.partition("  ")
        if len(h) == 64 and path:
            out[path[2:] if path.startswith("./") else path] = h.lower()
    return out


def dry_run(source_root: Path, manifest_name: str = DECLARED_MANIFEST) -> dict:
    root = Path(source_root)
    mf = root / manifest_name
    if not mf.is_file():
        return {"ok": False, "error": "declared_manifest_missing", "declared": {}, "actual": {}, "mismatches": [],
                "missing": [], "source_root": str(root)}
    declared = parse_sha256sum(mf.read_text(encoding="utf-8"))
    actual, missing, mismatches = {}, [], []
    for rel, h in declared.items():
        p = root / rel
        if not p.is_file():
            missing.append(rel)
            continue
        a = hashlib.sha256(p.read_bytes()).hexdigest()
        actual[rel] = a
        if a != h:
            mismatches.append(rel)
    return {"ok": not missing and not mismatches, "source_root": str(root), "declared_manifest": manifest_name,
            "declared_count": len(declared), "declared": declared, "actual": actual, "missing": missing,
            "mismatches": mismatches, "mode": "dry_run_read_only"}
