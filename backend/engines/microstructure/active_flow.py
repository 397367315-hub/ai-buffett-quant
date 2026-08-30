"""Active buy/sell classification with an explicit confidence and method."""

from __future__ import annotations

from typing import Any

from market_data.level2.models import OrderBookSnapshot, TradeTick


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _top(quote: Any, side: str) -> tuple[float | None, float | None]:
    levels = _value(quote, "asks" if side == "ask" else "bids", []) or []
    first = levels[0] if levels else None
    return _value(first, "price"), _value(first, "volume")


def classify_trade(
    trade: TradeTick,
    quote: OrderBookSnapshot | None = None,
    previous_price: float | None = None,
) -> tuple[str, str, float]:
    """Return ``side, method, confidence`` without pretending unknown flags are known."""
    if trade.side in {"buy", "sell"}:
        return trade.side, trade.direction_method or "explicit_side", max(float(trade.direction_confidence or 0.95), 0.8)
    price = trade.price
    if price is not None and quote is not None:
        bid, _ = _top(quote, "bid")
        ask, _ = _top(quote, "ask")
        if ask is not None and price >= ask:
            return "buy", "quote_ask_rule", 0.82
        if bid is not None and price <= bid:
            return "sell", "quote_bid_rule", 0.82
    if price is not None and previous_price is not None:
        if price > previous_price:
            return "buy", "tick_rule", 0.56
        if price < previous_price:
            return "sell", "tick_rule", 0.56
    return "neutral", "unclassified", 0.2


def summarize_active_flow(trades: list[TradeTick], quotes: list[OrderBookSnapshot] | None = None) -> dict[str, Any]:
    """Classify a sequence and return auditable aggregate amounts."""
    ordered = sorted(trades, key=lambda item: item.timestamp)
    ordered_quotes = sorted(quotes or [], key=lambda item: item.timestamp)
    quote_index = 0
    previous_price: float | None = None
    buy_amount = sell_amount = neutral_amount = 0.0
    buy_count = sell_count = neutral_count = 0
    confidence_total = 0.0
    classified: list[dict[str, Any]] = []
    for trade in ordered:
        while quote_index + 1 < len(ordered_quotes) and ordered_quotes[quote_index + 1].timestamp <= trade.timestamp:
            quote_index += 1
        quote = ordered_quotes[quote_index] if ordered_quotes and ordered_quotes[quote_index].timestamp <= trade.timestamp else None
        side, method, confidence = classify_trade(trade, quote, previous_price)
        amount = float(trade.amount or 0.0)
        if side == "buy":
            buy_amount += amount
            buy_count += 1
        elif side == "sell":
            sell_amount += amount
            sell_count += 1
        else:
            neutral_amount += amount
            neutral_count += 1
        confidence_total += confidence
        classified.append({"trade": trade, "side": side, "method": method, "confidence": confidence})
        if trade.price is not None:
            previous_price = trade.price
    total = buy_amount + sell_amount + neutral_amount
    active_total = buy_amount + sell_amount
    return {
        "buy_amount": buy_amount,
        "sell_amount": sell_amount,
        "neutral_amount": neutral_amount,
        "net_active_amount": buy_amount - sell_amount,
        "active_buy_ratio": buy_amount / active_total if active_total else None,
        "trade_count": len(ordered),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "neutral_count": neutral_count,
        "direction_confidence": confidence_total / len(ordered) if ordered else 0.0,
        "classified": classified,
        "total_amount": total,
    }
