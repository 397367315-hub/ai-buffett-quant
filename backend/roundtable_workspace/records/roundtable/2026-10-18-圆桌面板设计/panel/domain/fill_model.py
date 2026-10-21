"""成交假设（EVAL-003 / JOURNAL-006）：日线只告诉你 OHLC，不告诉你先后。

- daily_bar_fill_assumption_v1：只有日线时，触价 ≠ 成交；同一根 K 线同时碰到入场价和止损价 → 路径未知，
  任何「先入场后止损」或「先止损未入场」的说法都只是假设，必须标注，不能当权威执行收益；
- 停牌：确定无法成交 → unfilled，成交价 null，现金与持仓不变；
- 跌停卖出 / 涨停买入：是否排到队无法由日线或快照证明 → fill_status unknown（不是已卖出），
  风险敞口仍在，警告 queue_execution_unverified；只有核验过的排队/逐笔流动性证据才能改变这个结论（v1 没有）；
- 本模块不产生任何「已核验的真实成交」：verified_real_fill_count 恒为 0。
"""
from __future__ import annotations

from decimal import Decimal

DAILY_MODEL = "daily_bar_fill_assumption_v1"

# 模拟成交被拒时的成交状态语义（journal_service 把它写进 422 的 details，前端据此显示）
REJECTION_FILL_STATUS = {
    "INSTRUMENT_SUSPENDED": ("unfilled", []),
    "LIMIT_DOWN_SELL_UNVERIFIABLE": ("unknown", ["queue_execution_unverified"]),
    "LIMIT_UP_BUY_UNVERIFIABLE": ("unknown", ["queue_execution_unverified"]),
    "SUSPENSION_STATUS_UNKNOWN": ("unknown", ["suspension_status_unknown"]),
    "PRICE_LIMIT_UNKNOWN": ("unknown", ["price_limit_unknown"]),
}


def rejection_details(code: str) -> list[dict]:
    st, warns = REJECTION_FILL_STATUS.get(code, ("unfilled", []))
    return [{"field": "fill_status", "reason": st}] + [{"field": "warning", "reason": w} for w in warns]


def _d(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def resolve_daily_bar(*, entry_trigger_price=None, stop_price=None, bar: dict, side: str = "buy") -> dict:
    """在一根日线上判断触发与止损；只给「可能」与假设标签，不给精确路径。"""
    o, h, l, c = (_d(bar.get(k)) for k in ("open", "high", "low", "close"))
    entry, stop = _d(entry_trigger_price), _d(stop_price)
    touched_entry = entry is not None and h is not None and l is not None and l <= entry <= h if side == "buy" \
        else (entry is not None and l is not None and h is not None and l <= entry <= h)
    gap_entry = entry is not None and o is not None and o >= entry if side == "buy" else False
    touched_stop = stop is not None and l is not None and l <= stop
    both = bool(touched_entry or gap_entry) and bool(touched_stop)
    orders = []
    if both:
        orders = ["entry_then_stop", "stop_then_entry_or_no_entry"]
    elif touched_entry or gap_entry:
        orders = ["entry_only"]
    elif touched_stop:
        orders = ["stop_only_without_position"]
    return {"model": DAILY_MODEL, "entry_touched": bool(touched_entry or gap_entry), "stop_touched": bool(touched_stop),
            "exact_path_known": False, "possible_orders": orders,
            "fill_assumption": ("assumed_fill_at_trigger_price_if_touched" if (touched_entry or gap_entry) else None),
            "fill_assumption_labeled": True,
            "assumption_label": "假设：日线无法证明盘中先后与排队，触价只当作假设成交，不是真实成交",
            "verified_real_fill_count": 0, "execution_return_authoritative": False,
            "warnings": (["intrabar_path_ambiguous"] if both else []) + ["daily_bar_only"]}


def resolve_exit_on_daily(*, side: str, bar: dict, lower_limit=None, upper_limit=None, suspended: bool | None = False,
                          verified_queue_liquidity=None) -> dict:
    """只有日线时的退出/入场尝试：停牌 → unfilled；涨跌停封死且无排队证据 → unknown。"""
    o, h, l, c = (_d(bar.get(k)) for k in ("open", "high", "low", "close"))
    if suspended:
        return {"fill_status": "unfilled", "fill_price": None, "executed_return_ratio": None,
                "risk_exposure_remains": True, "warning_codes": ["instrument_suspended"], "model": DAILY_MODEL}
    lo, up = _d(lower_limit), _d(upper_limit)
    sealed_down = l is not None and h is not None and l == h and lo is not None and h <= lo
    sealed_up = l is not None and h is not None and l == h and up is not None and l >= up
    if (side == "sell" and (sealed_down or (lo is None and l == h and o == c and c == l))) or \
            (side == "buy" and sealed_up):
        if verified_queue_liquidity is None:
            return {"fill_status": "unknown", "fill_price": None, "executed_return_ratio": None,
                    "risk_exposure_remains": True,
                    "warning_codes": ["queue_execution_unverified", "limit_locked_daily_bar"], "model": DAILY_MODEL}
    return {"fill_status": "assumed", "fill_price": str(c) if c is not None else None, "executed_return_ratio": None,
            "risk_exposure_remains": side == "buy", "warning_codes": ["daily_bar_only", "fill_is_assumption"],
            "model": DAILY_MODEL}
