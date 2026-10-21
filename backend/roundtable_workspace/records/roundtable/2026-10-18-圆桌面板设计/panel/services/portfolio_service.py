"""组合估值（DATA-003）：没有报价就是没有报价，不许拿成本冒充市值。

口径：
  - 数量、金额一律用 Decimal；对外输出整数分（`*_minor`）的十进制字符串，避免浮点误差。
  - 某只票当日没有报价 → `price_status="unknown"`，`market_value_minor` 为 null，
    并且**不计入** `market_value_known_minor`，也不计入浮动盈亏。
  - `totals` 明确区分「已知部分」与「未知只数」，前端照抄，不许自己补 0。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from ..domain.money import minor_str, to_decimal, to_minor


def _num(v) -> Decimal:
    """旧账本是 JSON 浮点（历史兼容）；在这里一次性转成 Decimal，之后全程十进制。"""
    if isinstance(v, bool):
        raise TypeError("金额不接受布尔值")
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError(f"金额不能是 NaN/Inf：{v!r}")
        return Decimal(repr(v))
    if isinstance(v, int):
        return Decimal(v)
    if v is None:
        return Decimal("0")
    d = to_decimal(v)
    if not d.is_finite():
        raise ValueError(f"金额不能是 NaN/Inf：{v!r}")
    return d


def replay_positions(trades: Iterable[dict]) -> dict:
    """按成交流水重放持仓：先买后卖，卖出按加权成本扣减。"""
    acc: dict[str, dict] = {}
    for t in trades:
        code = str(t.get("code") or "").zfill(6)
        shares = int(t.get("shares") or 0)
        price = _num(t.get("price"))
        fee = t.get("fee") or {}
        fee_total = _num(fee.get("total"))
        amount = _num(t.get("amount")) if t.get("amount") is not None else price * shares
        cur = acc.setdefault(code, {"name": t.get("name") or "", "shares": 0, "cost": Decimal("0")})
        if t.get("side") == "buy":
            cur["shares"] += shares
            cur["cost"] += amount + fee_total
        else:
            avg = (cur["cost"] / cur["shares"]) if cur["shares"] else Decimal("0")
            cur["cost"] = max(Decimal("0"), cur["cost"] - avg * shares)
            cur["shares"] = max(0, cur["shares"] - shares)
    return {c: v for c, v in acc.items() if v["shares"] > 0}


def valuate(ledger: dict, quotes: dict, quote_date: str) -> dict:
    """ledger: {cash, trades[]}；quotes: {code: [name, close, pre_close]}。"""
    positions = []
    known_mv = Decimal("0")
    known_cost = Decimal("0")
    unpriced = []
    for code, p in sorted(replay_positions(ledger.get("trades") or []).items()):
        cost = p["cost"]
        avg = cost / p["shares"] if p["shares"] else Decimal("0")
        q = quotes.get(code)
        item = {
            "symbol": code,
            "name": p["name"] or (q[0] if q else ""),
            "shares": p["shares"],
            "cost_basis_minor": minor_str(to_minor(cost)),
            "avg_cost_minor": minor_str(to_minor(avg)),
        }
        if q and q[1] is not None:
            last = _num(q[1])
            mv = last * p["shares"]
            item.update({
                "price_status": "known",
                "last_price": str(last),
                "market_value_minor": minor_str(to_minor(mv)),
                "pnl_minor": minor_str(to_minor(mv - cost)),
            })
            known_mv += mv
            known_cost += cost
        else:
            # 没报价：市值与浮动盈亏都是「不知道」，不是 0，也不是成本
            item.update({"price_status": "unknown", "last_price": None,
                         "market_value_minor": None, "pnl_minor": None,
                         "reason": "该股当日无报价（停牌或不在行情快照内）"})
            unpriced.append(code)
        positions.append(item)

    cash = _num(ledger.get("cash"))
    return {
        "quote_date": quote_date,
        "cash_minor": minor_str(to_minor(cash)),
        "positions": positions,
        "totals": {
            "market_value_known_minor": minor_str(to_minor(known_mv)),
            "cost_basis_known_minor": minor_str(to_minor(known_cost)),
            "floating_pnl_known_minor": minor_str(to_minor(known_mv - known_cost)),
            "equity_known_minor": minor_str(to_minor(cash + known_mv)),
            "unpriced_count": len(unpriced),
            "unpriced_symbols": unpriced,
            "complete": not unpriced,
            "note": ("全部持仓有报价" if not unpriced
                     else f"{len(unpriced)} 只没有报价，未计入市值与浮动盈亏；总资产为已知部分"),
        },
    }
