"""验收规格的加载与引用解析（不 eval、不模糊匹配）。

- fixture：`extends` 深合并 `patch`（dict 递归合并，list/标量整体替换，显式 null 覆盖）；
- 引用：字符串 `$<fixture_id>.<path>` 取 fixture 字段，`$result.<path>` 取本用例已投影的结果；
- 路径：点号分隔，键里不含点；不存在 → 抛 KeyError（由 runner 记为 driver 错误，不当作通过）。
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

WORK_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = WORK_ROOT / "acceptance" / "acceptance_cases.json"
CATALOG_PATH = WORK_ROOT / "acceptance" / "operation_catalog.json"
OPMAP_PATH = Path(__file__).resolve().parent / "operation_map.json"

MISSING = object()


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load_spec() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def load_opmap() -> dict:
    return json.loads(OPMAP_PATH.read_text(encoding="utf-8"))


def deep_merge(base, patch):
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            out[k] = deep_merge(base.get(k), v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(patch)


def resolve_fixture(fixtures: dict, fid: str, _seen: tuple = ()) -> dict:
    if fid in _seen:
        raise ValueError(f"fixture extends 循环：{_seen + (fid,)}")
    fx = fixtures[fid]
    if "extends" not in fx:
        return copy.deepcopy(fx)
    base = resolve_fixture(fixtures, fx["extends"], _seen + (fid,))
    merged = deep_merge(base, fx.get("patch") or {})
    merged["fixture_id"] = fx["fixture_id"]
    merged["_extends"] = fx["extends"]
    return merged


def get_path(obj, path: str, default=MISSING):
    cur = obj
    for part in path.split(".") if path else []:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            if default is MISSING:
                raise KeyError(path)
            return default
    return cur


def resolve_refs(obj, fixtures: dict, result: dict):
    """把 `$fixture.field` / `$result.path` 换成实际值（只做字符串整体引用）。"""
    if isinstance(obj, str) and obj.startswith("$") and len(obj) > 1:
        head, _, rest = obj[1:].partition(".")
        if head == "result":
            return get_path(result, rest)
        if head in fixtures:
            return get_path(resolve_fixture(fixtures, head), rest)
        return obj
    if isinstance(obj, dict):
        return {k: resolve_refs(v, fixtures, result) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_refs(v, fixtures, result) for v in obj]
    return obj


def fixture_manifest_hash(spec: dict) -> str:
    raw = json.dumps(spec["fixtures"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
