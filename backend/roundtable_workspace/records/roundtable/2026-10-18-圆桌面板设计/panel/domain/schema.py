"""JSON Schema（2020-12 子集）校验器——只用标准库。

覆盖契约里实际用到的关键字：type / enum / const / pattern / format(date, date-time) /
minimum / maximum / minLength / maxLength / minItems / maxItems / uniqueItems / items /
properties / required / additionalProperties / anyOf / oneOf / allOf / if-then-else / $ref。

请求校验与（测试模式下的）响应校验都用它，所以错误要能指出字段：
  SchemaError.issues = [{"field": "a.b[0].c", "reason": "...", "kind": "unknown_field|type|..."}]
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$")


class SchemaError(ValueError):
    def __init__(self, issues: list[dict]):
        self.issues = issues
        first = issues[0] if issues else {"field": "", "reason": "invalid"}
        super().__init__(f"{first['field'] or '<root>'}: {first['reason']}")

    @property
    def has_unknown_fields(self) -> bool:
        return any(i.get("kind") == "unknown_field" for i in self.issues)


def _type_ok(value: Any, t: str) -> bool:
    if t == "null":
        return value is None
    if t == "boolean":
        return isinstance(value, bool)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "string":
        return isinstance(value, str)
    if t == "array":
        return isinstance(value, list)
    if t == "object":
        return isinstance(value, dict)
    return False


def _format_ok(value: Any, fmt: str) -> bool:
    if not isinstance(value, str):
        return True
    if fmt == "date":
        if not _DATE_RE.match(value):
            return False
        try:
            date.fromisoformat(value)
            return True
        except ValueError:
            return False
    if fmt == "date-time":
        if not _DT_RE.match(value):
            return False
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return True
        except ValueError:
            return False
    return True


class Validator:
    def __init__(self, root: dict, defs_key: str = "$defs"):
        self.root = root
        self.defs_key = defs_key

    def resolve(self, ref: str) -> dict:
        if not ref.startswith("#/"):
            raise KeyError(f"只支持文档内引用：{ref}")
        node: Any = self.root
        for part in ref[2:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
        return node

    def validate(self, value: Any, schema: dict, path: str = "") -> list[dict]:
        issues: list[dict] = []
        self._v(value, schema, path, issues)
        return issues

    # ── 核心递归 ──
    def _v(self, value: Any, s: Any, path: str, out: list[dict]) -> None:
        if s is True or s is None:
            return
        if s is False:
            out.append({"field": path, "reason": "不允许的值", "kind": "forbidden"})
            return
        if "$ref" in s:
            self._v(value, self.resolve(s["$ref"]), path, out)
            # $ref 旁边的兄弟关键字（description 之外）仍需校验
            rest = {k: v for k, v in s.items() if k not in ("$ref", "description")}
            if rest:
                self._v(value, rest, path, out)
            return
        if "type" in s:
            types = s["type"] if isinstance(s["type"], list) else [s["type"]]
            if not any(_type_ok(value, t) for t in types):
                out.append({"field": path, "reason": f"类型应为 {'|'.join(types)}", "kind": "type"})
                return
        if "const" in s and value != s["const"]:
            out.append({"field": path, "reason": f"必须等于 {json.dumps(s['const'], ensure_ascii=False)}", "kind": "const"})
        if "enum" in s and value not in s["enum"]:
            out.append({"field": path, "reason": f"取值不在 {s['enum']}", "kind": "enum"})
        if isinstance(value, str):
            if "minLength" in s and len(value) < s["minLength"]:
                out.append({"field": path, "reason": f"长度应 ≥ {s['minLength']}", "kind": "length"})
            if "maxLength" in s and len(value) > s["maxLength"]:
                out.append({"field": path, "reason": f"长度应 ≤ {s['maxLength']}", "kind": "length"})
            if "pattern" in s and not re.search(s["pattern"], value):
                out.append({"field": path, "reason": f"格式不符 {s['pattern']}", "kind": "pattern"})
            if "format" in s and not _format_ok(value, s["format"]):
                out.append({"field": path, "reason": f"不是合法 {s['format']}", "kind": "format"})
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in s and value < s["minimum"]:
                out.append({"field": path, "reason": f"应 ≥ {s['minimum']}", "kind": "range"})
            if "maximum" in s and value > s["maximum"]:
                out.append({"field": path, "reason": f"应 ≤ {s['maximum']}", "kind": "range"})
        if isinstance(value, list):
            if "minItems" in s and len(value) < s["minItems"]:
                out.append({"field": path, "reason": f"至少 {s['minItems']} 项", "kind": "items"})
            if "maxItems" in s and len(value) > s["maxItems"]:
                out.append({"field": path, "reason": f"至多 {s['maxItems']} 项", "kind": "items"})
            if s.get("uniqueItems"):
                seen = set()
                for x in value:
                    key = json.dumps(x, sort_keys=True, ensure_ascii=False)
                    if key in seen:
                        out.append({"field": path, "reason": "元素必须唯一", "kind": "unique"})
                        break
                    seen.add(key)
            if "items" in s:
                for i, x in enumerate(value):
                    self._v(x, s["items"], f"{path}[{i}]", out)
        if isinstance(value, dict):
            props = s.get("properties", {})
            for r in s.get("required", []):
                if r not in value:
                    out.append({"field": _join(path, r), "reason": "缺少必填字段", "kind": "required"})
            for k, x in value.items():
                if k in props:
                    self._v(x, props[k], _join(path, k), out)
                else:
                    ap = s.get("additionalProperties", True)
                    if ap is False:
                        out.append({"field": _join(path, k), "reason": "未知字段", "kind": "unknown_field"})
                    elif isinstance(ap, dict):
                        self._v(x, ap, _join(path, k), out)
        if "allOf" in s:
            for sub in s["allOf"]:
                self._v(value, sub, path, out)
        if "anyOf" in s:
            if not any(not self.validate(value, sub, path) for sub in s["anyOf"]):
                # 给出最接近的分支错误，便于定位
                best = min((self.validate(value, sub, path) for sub in s["anyOf"]), key=len)
                out.extend(best or [{"field": path, "reason": "不满足任何允许的形状", "kind": "anyOf"}])
        if "oneOf" in s:
            ok = [sub for sub in s["oneOf"] if not self.validate(value, sub, path)]
            if len(ok) != 1:
                out.append({"field": path, "reason": f"应恰好满足一个分支（实际 {len(ok)}）", "kind": "oneOf"})
        if "if" in s:
            cond = not self.validate(value, s["if"], path)
            branch = s.get("then") if cond else s.get("else")
            if branch is not None:
                self._v(value, branch, path, out)


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


# ── 契约加载 ──
@lru_cache(maxsize=4)
def load_contract(contracts_dir: str) -> dict:
    """models.schema.json + 由 openapi 生成的 operations.json（含列表包装 schema）。"""
    d = Path(contracts_dir)
    models = json.loads((d / "models.schema.json").read_text(encoding="utf-8"))
    return models


class ContractValidator:
    """按模型名校验：validate_model("PlanCreateRequest", obj) → 抛 SchemaError。"""

    def __init__(self, models: dict, extra_defs: dict | None = None):
        root = dict(models)
        defs = dict(models.get("$defs", {}))
        if extra_defs:
            defs.update(extra_defs)
        root["$defs"] = defs
        self.root = root
        self.v = Validator(root)

    def has(self, name: str) -> bool:
        return name in self.root["$defs"]

    def issues(self, name: str, value: Any) -> list[dict]:
        return self.v.validate(value, {"$ref": f"#/$defs/{name}"})

    def check(self, name: str, value: Any) -> None:
        iss = self.issues(name, value)
        if iss:
            raise SchemaError(iss)


def list_wrapper(item_model: str) -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["items", "next_cursor"],
            "properties": {"items": {"type": "array", "items": {"$ref": f"#/$defs/{item_model}"}},
                           "next_cursor": {"anyOf": [{"type": "string"}, {"type": "null"}]}}}


WORKSPACE_INDEX = {"type": "object", "additionalProperties": False,
                   "required": ["replay_id", "cases", "plans", "portfolios", "replays", "validations", "decks", "reviews",
                                "archive"],
                   "properties": {"replay_id": {"anyOf": [{"type": "string", "pattern": "^[a-z][a-z0-9_-]{1,95}$"},
                                                          {"type": "null"}]},
                                  **{k: {"type": "array", "items": {"type": "object"}}
                                     for k in ("cases", "plans", "portfolios", "replays", "validations", "decks", "reviews")},
                                  "archive": {"anyOf": [{"type": "object"}, {"type": "null"}]}}}

LIST_WRAPPERS = {
    "WorkspaceIndex": WORKSPACE_INDEX,
    "SeatRunList": list_wrapper("SeatRun"),
    "JournalEventList": list_wrapper("JournalEvent"),
    "AlertList": list_wrapper("AlertEvent"),
    "PlanLifecycleEventList": list_wrapper("PlanLifecycleEvent"),
    "PortfolioControlEventList": list_wrapper("PortfolioControlEvent"),
}

_cv: ContractValidator | None = None


def contract(contracts_dir: Path | None = None) -> ContractValidator:
    global _cv
    if _cv is None:
        from ..config import CONTRACTS_DIR
        models = load_contract(str(contracts_dir or CONTRACTS_DIR))
        _cv = ContractValidator(models, LIST_WRAPPERS)
    return _cv
