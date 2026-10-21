"""金额与精度的唯一实现（BASE-002 / JOURNAL-006）。

02 §3：
  - DB 金额 INTEGER 分（带符号 64 位）；API *_minor 为十进制整数字符串，前端用 BigInt；
  - 价格 / 每股股息 / 比率为 Decimal 字符串，计算全程 decimal.Decimal，禁止经过 float；
  - 缺失 = null，不是 0；禁止 NaN / Infinity；
  - 舍入只在「最终记账金额」边界按版本化政策进行，不提前把每股金额舍到分。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from .canonical import DECIMAL_RE, MINOR_RE, UDECIMAL_RE

INT64_MIN = -(2 ** 63)
INT64_MAX = 2 ** 63 - 1
JS_SAFE_MAX = 2 ** 53 - 1


class MoneyOutOfRange(ValueError):
    """→ HTTP 422 MONEY_OUT_OF_RANGE（不截断）。"""


class PrecisionError(ValueError):
    """不合法的小数 / 含二进制浮点 → HTTP 422 PRECISION_ERROR。"""


def to_decimal(value) -> Decimal | None:
    """字符串 / int / Decimal → Decimal。float、bool、NaN、Infinity 一律拒绝。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise PrecisionError("bool 不是合法数值")
    if isinstance(value, float):
        raise PrecisionError("拒绝 float：金额、价格与比率只能用十进制字符串")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise PrecisionError("NaN/Infinity 不是合法数值")
        return value
    if isinstance(value, str):
        if not DECIMAL_RE.match(value):
            raise PrecisionError(f"不是合法十进制字符串：{value!r}")
        try:
            return Decimal(value)
        except InvalidOperation as ex:     # pragma: no cover
            raise PrecisionError(str(ex)) from ex
    raise PrecisionError(f"不支持的数值类型 {type(value).__name__}")


def parse_price(text: str | None, field: str = "price_decimal") -> Decimal | None:
    if text is None:
        return None
    if not isinstance(text, str) or not UDECIMAL_RE.match(text):
        raise PrecisionError(f"{field} 必须是非负十进制字符串")
    return Decimal(text)


def check_range(minor: int) -> int:
    if minor < INT64_MIN or minor > INT64_MAX:
        raise MoneyOutOfRange(f"{minor} 超出 64 位整数分范围")
    return minor


def yuan_to_minor(value, rounding=ROUND_HALF_UP) -> int:
    """元（Decimal/字符串）→ 分，在记账边界按 HALF_UP 舍入。"""
    d = to_decimal(value)
    if d is None:
        raise ValueError("None 不能转成分")
    with localcontext() as ctx:
        ctx.prec = 60
        minor = int((d * 100).quantize(Decimal("1"), rounding=rounding))
    return check_range(minor)


# 兼容旧名
to_minor = yuan_to_minor


def minor_str(minor: int | None) -> str | None:
    if minor is None:
        return None
    m = int(minor)
    check_range(m)
    return str(m)      # int 不会产生负零


def parse_minor(text, field: str = "*_minor", allow_negative: bool = True) -> int | None:
    if text is None:
        return None
    if not isinstance(text, str):
        raise PrecisionError(f"{field} 必须是十进制整数字符串")
    if not MINOR_RE.match(text):
        raise PrecisionError(f"{field} 不是合法整数字符串：{text!r}")
    if not allow_negative and text.startswith("-"):
        raise PrecisionError(f"{field} 不能为负")
    return check_range(int(text))


from_minor_str = parse_minor   # 兼容旧名


def minor_to_yuan_str(minor: int | None) -> str | None:
    if minor is None:
        return None
    sign = "-" if minor < 0 else ""
    a = abs(int(minor))
    return f"{sign}{a // 100}.{a % 100:02d}"


def decimal_str(d: Decimal | None, places: int | None = None) -> str | None:
    """Decimal → 契约字符串（不带指数、不带 + 号）。places=None 保留原精度。"""
    if d is None:
        return None
    if places is not None:
        d = d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    s = format(d, "f")
    if s.startswith("-") and Decimal(s) == 0:
        s = s[1:]
    return s


def ratio_str(num: Decimal, den: Decimal, places: int = 12) -> str | None:
    """比率字符串：num/den；分母为 0 返回 None（不是 0）。尾随 0 去掉。"""
    if den == 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 50
        r = (Decimal(num) / Decimal(den)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return normalize_str(r)


def normalize_str(d: Decimal) -> str:
    """去尾随 0 的十进制字符串：0.1000 → 0.1；10.0 → 10。"""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s


def mul_shares(price: Decimal, shares: int) -> Decimal:
    """价格 × 股数（精确，不舍入）。"""
    with localcontext() as ctx:
        ctx.prec = 60
        return price * Decimal(int(shares))
