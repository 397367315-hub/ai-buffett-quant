"""确定性断言（runner_contract.assertion_operators）。

每个断言返回 (ok, actual_projection, detail)。不存在的目标路径：除 `absent` 外一律失败。
比较规则：
  - eq/ne：JSON 语义相等，布尔与数字严格区分（True ≠ 1），字符串不做大小写或空白归一；
  - contains / not_contains：列表看元素（元素为 dict/list 时按规范 JSON 比较），字符串看子串；
  - set_eq：集合相等（元素规范 JSON 化）；length_eq：长度；lte/gte：Decimal 数值比较；
  - byte_equal：两份「原字节清单」（路径 → 大小 + sha256）完全一致，且清单非空；
  - sha256_equal：两侧都是 64 位十六进制（或同形清单）且相等，且非空；
  - decimal_abs_error_lte：|actual − expected| ≤ tolerance（Decimal）。
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

from .spec import MISSING, get_path

HEX64 = re.compile(r"^[a-f0-9]{64}$")


def _canon(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if type(a) is not type(b) and not (a is None or b is None):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _dec(v) -> Decimal | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _is_hash_shape(v) -> bool:
    if isinstance(v, str):
        return bool(HEX64.match(v))
    if isinstance(v, list):
        return bool(v) and all(_is_hash_shape(x) for x in v)
    if isinstance(v, dict):
        return bool(v) and all(_is_hash_shape(x) for x in v.values())
    return False


def _is_byte_manifest(v) -> bool:
    if not isinstance(v, dict) or not v:
        return False
    for item in v.values():
        if not (isinstance(item, dict) and isinstance(item.get("size"), int) and HEX64.match(str(item.get("sha256", "")))):
            return False
    return True


def evaluate(expect: dict, result: dict, resolved_value) -> dict:
    target = expect["target"]
    op = expect["op"]
    path = target[len("result."):] if target.startswith("result.") else target
    actual = get_path(result, path, MISSING)
    v = resolved_value
    ok, detail = False, ""
    if op == "absent":
        ok = actual is MISSING
        detail = "target absent" if ok else "target present"
        return {"target": target, "op": op, "expected": v, "actual": None if actual is MISSING else actual,
                "ok": ok, "detail": detail}
    if actual is MISSING:
        return {"target": target, "op": op, "expected": v, "actual": None, "ok": False,
                "detail": "target missing from projection"}
    if op == "eq":
        ok = _same(actual, v)
    elif op == "ne":
        ok = not _same(actual, v)
    elif op in ("contains", "not_contains"):
        if isinstance(actual, str):
            hit = isinstance(v, str) and v in actual
        elif isinstance(actual, list):
            hit = any(_same(x, v) for x in actual)
        elif isinstance(actual, dict):
            hit = v in actual
        else:
            hit = False
            detail = f"contains on {type(actual).__name__}"
        ok = hit if op == "contains" else (not hit and isinstance(actual, (str, list, dict)))
    elif op == "set_eq":
        if isinstance(actual, list) and isinstance(v, list):
            ok = {_canon(x) for x in actual} == {_canon(x) for x in v}
        else:
            detail = "set_eq needs lists"
    elif op == "length_eq":
        ok = isinstance(actual, (list, str, dict)) and len(actual) == v
    elif op in ("lte", "gte"):
        a, b = _dec(actual), _dec(v)
        if a is None or b is None:
            detail = "non-numeric"
        else:
            ok = a <= b if op == "lte" else a >= b
    elif op == "byte_equal":
        if not (_is_byte_manifest(actual) and _is_byte_manifest(v)):
            detail = "byte_equal needs non-empty byte manifests {path:{size,sha256}}"
        else:
            ok = _same(actual, v)
    elif op == "sha256_equal":
        if not (_is_hash_shape(actual) and _is_hash_shape(v)):
            detail = "sha256_equal needs non-empty sha256 values"
        else:
            ok = _same(actual, v)
    elif op == "decimal_abs_error_lte":
        a = _dec(actual)
        e, t = _dec((v or {}).get("expected")), _dec((v or {}).get("tolerance"))
        if a is None or e is None or t is None:
            detail = "non-numeric"
        else:
            ok = abs(a - e) <= t
            detail = f"|{a}-{e}|={abs(a - e)}"
    else:
        detail = f"unknown operator {op}"
    return {"target": target, "op": op, "expected": v, "actual": actual, "ok": bool(ok), "detail": detail}
