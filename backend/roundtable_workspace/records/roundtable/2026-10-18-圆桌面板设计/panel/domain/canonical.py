"""canonical-json-v1 与内容哈希、ID 规则。

canonical-json-v1（02 §4）：UTF-8；字符串 NFC；对象键排序；紧凑分隔；数组保留顺序；
禁止 NaN/Infinity；Decimal 按输入精度原样输出（"1.00" 与 "1" 不同）；float 一律拒绝。
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import unicodedata
from decimal import Decimal
from typing import Any

ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,95}$")
SYMBOL_RE = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
DECIMAL_RE = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")
UDECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
MINOR_RE = re.compile(r"^-?(0|[1-9][0-9]*)$")
UMINOR_RE = re.compile(r"^(0|[1-9][0-9]*)$")


def _norm(v: Any) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, Decimal):
        if not v.is_finite():
            raise ValueError("canonical json 不接受非有限 Decimal")
        return str(v)
    if isinstance(v, float):
        raise TypeError("canonical json 不接受 float；金额/价格/比率请用 Decimal 字符串")
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        return unicodedata.normalize("NFC", v)
    if isinstance(v, dict):
        out = {}
        for k in sorted(v.keys()):
            if not isinstance(k, str):
                raise TypeError("对象键必须是字符串")
            out[unicodedata.normalize("NFC", k)] = _norm(v[k])
        return out
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    raise TypeError(f"canonical json 不支持 {type(v).__name__}")


def canonical_json(obj: Any) -> str:
    return json.dumps(_norm(obj), ensure_ascii=False, separators=(",", ":"), allow_nan=False, sort_keys=True)


def canonical_bytes(obj: Any) -> bytes:
    return canonical_json(obj).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_hash(obj: Any) -> str:
    return sha256_hex(canonical_bytes(obj))


def new_id(prefix: str, nbytes: int = 8) -> str:
    """服务端生成的 ID：<prefix>_<hex>，满足 [a-z][a-z0-9_-]{1,95}。"""
    if not ID_RE.match(prefix + "_x"):
        raise ValueError(f"非法 ID 前缀 {prefix!r}")
    return f"{prefix}_{secrets.token_hex(nbytes)}"


def derived_id(prefix: str, *parts: Any, length: int = 20) -> str:
    """确定性 ID：同样输入 → 同样 ID（用于幂等摄入与证据编号）。"""
    h = content_hash(list(parts))[:length]
    return f"{prefix}_{h}"


def is_id(v: Any) -> bool:
    return isinstance(v, str) and bool(ID_RE.match(v))


def is_symbol(v: Any) -> bool:
    return isinstance(v, str) and bool(SYMBOL_RE.match(v))


def strict_json_loads(raw: bytes | str, *, parse_float=Decimal) -> Any:
    """严格解析：拒绝 NaN/Infinity，数字小数用 Decimal 承接（不经过 float）。"""
    def bad_const(name):
        raise ValueError(f"JSON 不允许 {name}")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw, parse_float=parse_float, parse_constant=bad_const)


def json_dumps(obj: Any) -> str:
    """响应输出：Decimal 原样转字符串之外的任何数字都必须已经是 int。"""
    def default(o):
        if isinstance(o, Decimal):
            return str(o)
        raise TypeError(f"无法序列化 {type(o).__name__}")
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, default=default)
