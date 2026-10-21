"""证券身份、板块与交易规则（DATA-005 / JOURNAL-006）。

板块判定优先用供应商 stockbasic 的 market 字段；代码段只作受控 fallback 并登记局限：
  - 供应商 stockbasic 把沪 B（900xxx）、深 B（200xxx/201xxx）也标成「主板」，
    所以 B 股必须用交易所代码段规则额外排除（rule_id=b_share_code_segment_v1）；
  - stockbasic.market 是当前分类，不是历史时点分类——历史回放的板块按当前分类近似，并在证据里标注。
交易规则（手数、T+1、涨跌停、tick）按版本化 instrument_rules 配置，v1 只支持沪深主板 A 股的模拟成交；
其它板块明确「不支持模拟」，不拿前缀去猜它们的特殊制度。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

INSTRUMENT_RULES_VERSION = "instrument_rules_cn_a_v1"
B_SHARE_RULE_ID = "b_share_code_segment_v1"

# 供应商 market 文字 / 验收 fixture 词表 → 契约 board
_MARKET_MAP = {
    "主板": "MAIN", "main": "MAIN", "main_sh": "SH_MAIN", "main_sz": "SZ_MAIN",
    "创业板": "CHINEXT", "chinext": "CHINEXT",
    "科创板": "STAR", "star": "STAR", "star_cdr": "STAR",
    "北交所": "BSE", "beijing": "BSE", "bse": "BSE",
    "b_share_sh": "B_SHARE", "b_share_sz": "B_SHARE",
}


def exchange_of(symbol: str) -> str:
    return symbol.split(".")[1]


def is_b_share_code(symbol: str) -> bool:
    code, ex = symbol.split(".")
    return (ex == "SH" and code.startswith("900")) or (ex == "SZ" and code.startswith(("200", "201")))


@dataclass(frozen=True)
class BoardResult:
    board: str              # 契约枚举：SH_MAIN/SZ_MAIN/CHINEXT/STAR/BSE/unknown
    raw_market: str | None  # 原始市场字段（供应商文字或 fixture 值）
    category: str           # a_share / b_share / unknown
    source: str             # stockbasic / code_prefix_fallback / none
    note: str


def classify_board(symbol: str, market: str | None, name: str | None = None) -> BoardResult:
    raw = market
    m = _MARKET_MAP.get((market or "").strip()) if market else None
    ex = exchange_of(symbol)
    if m == "B_SHARE" or (m == "MAIN" and is_b_share_code(symbol)) or (name and name.endswith("B股")):
        return BoardResult("unknown", raw, "b_share", "stockbasic" if market else "code_prefix_fallback",
                           f"B 股（{B_SHARE_RULE_ID}：供应商把 B 股标成主板，按交易所代码段排除）")
    if m in ("SH_MAIN", "SZ_MAIN"):
        return BoardResult(m, raw, "a_share", "stockbasic", "")
    if m == "MAIN":
        if ex == "SH":
            return BoardResult("SH_MAIN", raw, "a_share", "stockbasic", "")
        if ex == "SZ":
            return BoardResult("SZ_MAIN", raw, "a_share", "stockbasic", "")
        return BoardResult("unknown", raw, "unknown", "stockbasic", "主板标记与交易所后缀不符")
    if m in ("CHINEXT", "STAR", "BSE"):
        return BoardResult(m, raw, "a_share", "stockbasic", "")
    if market:
        return BoardResult("unknown", raw, "unknown", "stockbasic", f"未知市场分类 {market!r}，不默认纳入")
    return BoardResult("unknown", None, "unknown", "none", "缺少市场分类")


def classify_by_code_prefix(symbol: str) -> BoardResult:
    """仅在没有 stockbasic 时使用的受控 fallback（file_import 历史归档）。结果必须标注来源。"""
    code, ex = symbol.split(".")
    if is_b_share_code(symbol):
        return BoardResult("unknown", None, "b_share", "code_prefix_fallback", "B 股代码段")
    if ex == "BJ" or code.startswith(("4", "8", "92")):
        return BoardResult("BSE", None, "a_share", "code_prefix_fallback", "北交所代码段（fallback）")
    if code.startswith(("688", "689")):
        return BoardResult("STAR", None, "a_share", "code_prefix_fallback", "科创板代码段（fallback）")
    if code.startswith(("300", "301", "302")):
        return BoardResult("CHINEXT", None, "a_share", "code_prefix_fallback", "创业板代码段（fallback）")
    if ex == "SH" and code.startswith(("600", "601", "603", "605")):
        return BoardResult("SH_MAIN", None, "a_share", "code_prefix_fallback", "沪主板代码段（fallback）")
    if ex == "SZ" and code.startswith(("000", "001", "002", "003")):
        return BoardResult("SZ_MAIN", None, "a_share", "code_prefix_fallback", "深主板代码段（fallback）")
    return BoardResult("unknown", None, "unknown", "code_prefix_fallback", "代码段无法判定")


def to_symbol(code: str, exchange_hint: str | None = None) -> str | None:
    """6 位代码 → 600000.SH。无法判定交易所时返回 None（不猜）。"""
    code = str(code).strip()
    if "." in code:
        c, ex = code.split(".", 1)
        ex = ex.upper()
        return f"{c.zfill(6)}.{ex}" if ex in ("SH", "SZ", "BJ") else None
    c = code.zfill(6)
    if exchange_hint in ("SH", "SZ", "BJ"):
        return f"{c}.{exchange_hint}"
    if c.startswith(("92", "4", "8")):      # 北交所（含 920 新代码段）要先于 9 开头的沪 B 判断
        return f"{c}.BJ"
    if c.startswith(("6", "9")):
        return f"{c}.SH"
    if c.startswith(("0", "2", "3")):
        return f"{c}.SZ"
    return None


def is_st_name(name: str | None) -> bool:
    n = (name or "").upper().replace(" ", "")
    return n.startswith(("ST", "*ST", "S*ST", "SST")) or "ST" in n[:4]


def price_limit_rule_id(board: str, is_st: bool | None) -> str | None:
    if board in ("SH_MAIN", "SZ_MAIN"):
        if is_st is None:
            return None
        return "cn_main_st_5pct_v1" if is_st else "cn_main_10pct_v1"
    if board == "CHINEXT":
        return "cn_chinext_20pct_v1"
    if board == "STAR":
        return "cn_star_20pct_v1"
    if board == "BSE":
        return "cn_bse_30pct_v1"
    return None


def lot_size(board: str) -> int | None:
    """v1 只为主板给出手数；其它板块的特殊制度未核验 → None（模拟不支持）。"""
    if board in ("SH_MAIN", "SZ_MAIN"):
        return 100
    return None


TICK = Decimal("0.01")


def is_tick_aligned(price: Decimal) -> bool:
    return (price / TICK) == (price / TICK).to_integral_value()


SIMULATION_SUPPORTED_BOARDS = ("SH_MAIN", "SZ_MAIN")


def describe_rules() -> dict:
    return {
        "instrument_rules_version": INSTRUMENT_RULES_VERSION,
        "simulation_supported_boards": list(SIMULATION_SUPPORTED_BOARDS),
        "lot_size": {"SH_MAIN": 100, "SZ_MAIN": 100},
        "settlement": "T+1：当日买入股份下一交易日起可卖",
        "tick_size_decimal": str(TICK),
        "price_limits": "优先使用快照中的供应商涨跌停价（pricelimit）；缺失时该笔模拟不可执行",
        "limit_fill_policy": "涨停价买入、跌停价卖出：日线无法证明排队成交 → 不可执行/未知",
        "unsupported": ["CHINEXT/STAR/BSE 的特殊交易制度未核验，v1 不提供模拟成交",
                        "B 股、可转债、ETF 不在范围"],
        "source_note": "工程规则版本；实施时未取得交易所规则原文核验，现行制度以交易所与券商为准",
    }
