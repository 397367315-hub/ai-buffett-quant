"""公司行动与价格口径（JOURNAL-006 / DATA-005 / REPLAY-001）。

- 现金股息：每股金额保留原精度（Decimal 字符串），只在「总额」记账边界按 aggregate_cash_half_up_to_fen 舍入，
  不先把每股金额舍到分；除息参考扣减（交易所除权参考价的扣减额）≠ 实际到账现金，两者分开存放；
- 回报口径：raw 价格 + 单列现金股息；total_return_adjusted 价格已含股息再投资 → 再加现金就是重复计算（422）；
- 价格序列：pre_close 只是供应商的可比昨收（可能已按除息参考价调整），不能据此声称整段历史已前复权；
- 历史 as-of 复权（pit_adjustment_v1）：只使用截点之前已可知（available_at ≤ cutoff）且除权日 ≤ 截点会话的公司行动；
  截点后才公布的行动（哪怕除权日更早）一律不用；结果带 used_action_ids 与哈希，便于前后比对。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, localcontext

from . import clock
from .canonical import content_hash
from .money import normalize_str

CASH_ROUNDING = "aggregate_cash_half_up_to_fen"
AS_OF_POLICY = "pit_adjustment_v1"


from ..web.errors import ApiError


class DoubleCounted(ApiError):
    """价格已含息又加现金股息：HTTP 422 DOUBLE_COUNTED_CORPORATE_ACTION。"""

    def __init__(self, message: str):
        super().__init__(422, "DOUBLE_COUNTED_CORPORATE_ACTION", message,
                         [{"field": "price_basis", "reason": "total_return_adjusted already includes cash dividends"}])


def cash_dividend(shares: int, cash_per_share: str, rounding_policy: str = CASH_ROUNDING,
                  ex_reference_deduction: str | None = None) -> dict:
    if rounding_policy != CASH_ROUNDING:
        raise ValueError(f"未登记的舍入政策 {rounding_policy}")
    cps = Decimal(cash_per_share)
    with localcontext() as ctx:
        ctx.prec = 60
        gross = cps * int(shares)
        minor = int((gross * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return {"shares": int(shares), "cash_per_share": cash_per_share, "gross_exact_yuan": normalize_str(gross),
            "cash_receivable_minor": str(minor), "rounding_policy": rounding_policy,
            "ex_reference_deduction": ex_reference_deduction, "ex_reference_used_as_cash": False,
            "per_share_early_rounding": False}


def return_basis(price_basis: str, include_cash: bool) -> dict:
    if price_basis in ("total_return_adjusted",) and include_cash:
        raise DoubleCounted("价格已是含息全收益口径，再加现金股息会重复计算")
    if price_basis not in ("raw", "total_return_adjusted", "as_of_adjusted"):
        raise ValueError(f"未知价格口径 {price_basis}")
    return {"price_basis": price_basis, "include_cash": include_cash,
            "raw_cash_separate": price_basis == "raw" and include_cash,
            "components": (["price_return", "cash_dividend"] if price_basis == "raw" and include_cash else ["price_return"])}


def normalize_series(bars: list[dict], declared_basis: str, adjustment_policy_version: str | None) -> dict:
    """只做口径登记与原值保留：不从 pre_close 反推整段复权。"""
    out = []
    notes = []
    prev = None
    for b in sorted(bars, key=lambda x: x["session_date"]):
        raw = b.get("raw_close")
        pc = b.get("pre_close")
        if prev is not None and pc is not None and Decimal(pc) != Decimal(prev["raw_close"]):
            notes.append(f"{b['session_date']} pre_close {pc} ≠ 前一日原始收盘 {prev['raw_close']}："
                         "可能是除权除息参考价，只用于当日涨跌幅比较，不回写历史")
        out.append({"session_date": b["session_date"], "close": raw, "raw_close": raw, "pre_close": pc})
        prev = b
    return {"price_basis": declared_basis, "adjustment_policy_version": adjustment_policy_version,
            "bars": out, "whole_series_adjusted_from_pre_close": False,
            "original_raw_prices_preserved": all(x["close"] == x["raw_close"] for x in out), "notes": notes}


def adjust_as_of(bars: list[dict], actions: list[dict], cutoff_at: str) -> dict:
    """前复权到截点：只用截点前已可知的行动。"""
    cut = clock.parse_utc(cutoff_at)
    cut_session = clock.shanghai_date(cut)
    used, ignored = [], []
    for a in actions:
        av = a.get("available_at")
        if av is None or clock.parse_utc(av) > cut or (a.get("ex_session_date") or "9999") > cut_session:
            ignored.append({"action_id": a["action_id"], "reason": "not_known_at_cutoff" if (av is None or clock.parse_utc(av) > cut)
                            else "ex_date_after_cutoff", "available_at": av})
            continue
        used.append(a)
    series = sorted(bars, key=lambda x: x["session_date"])
    closes = {b["session_date"]: Decimal(b["raw_close"]) for b in series}
    factor = {d: Decimal(1) for d in closes}
    for a in used:
        ex = a["ex_session_date"]
        prev_days = [d for d in closes if d < ex]
        if not prev_days:
            continue
        pd = max(prev_days)
        pc = closes[pd]
        if a.get("cash_per_share"):
            f = (pc - Decimal(a["cash_per_share"])) / pc
        elif a.get("split_ratio"):
            f = Decimal(1) / Decimal(a["split_ratio"])
        else:
            continue
        for d in prev_days:
            factor[d] *= f
    adj = [{"session_date": d, "raw_close": format(closes[d], "f"),
            "adjusted_close": format((closes[d] * factor[d]).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), "f")}
           for d in sorted(closes)]
    return {"policy": AS_OF_POLICY, "cutoff_at": cutoff_at, "price_basis": "as_of_adjusted",
            "used_action_ids": [a["action_id"] for a in used], "ignored_actions": ignored, "bars": adj,
            "adjusted_hash": content_hash({"bars": adj, "used": [a["action_id"] for a in used]})}
