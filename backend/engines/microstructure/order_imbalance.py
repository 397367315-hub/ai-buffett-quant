"""Weighted order-book imbalance calculations."""

from __future__ import annotations

import math
from typing import Any

from market_data.level2.models import OrderBookSnapshot


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _side_value(item: Any, name: str) -> float | None:
    if isinstance(item, dict):
        return _number(item.get(name))
    return _number(getattr(item, name, None))


def _imbalance(bid: float, ask: float) -> float | None:
    total = bid + ask
    return (bid - ask) / total if total > 0 else None


def calculate_obi(snapshot: OrderBookSnapshot | None, max_level: int = 10) -> dict[str, Any]:
    if snapshot is None:
        return {"value": None, "obi_1": None, "obi_3": None, "obi_5": None, "obi_10": None, "available": False}
    bids = {int(_side_value(level, "level") or index + 1): _side_value(level, "volume") for index, level in enumerate(snapshot.bids)}
    asks = {int(_side_value(level, "level") or index + 1): _side_value(level, "volume") for index, level in enumerate(snapshot.asks)}
    values: dict[int, float | None] = {}
    for depth in (1, 3, 5, 10):
        bid_total = sum((bids.get(level) or 0.0) for level in range(1, min(depth, max_level) + 1))
        ask_total = sum((asks.get(level) or 0.0) for level in range(1, min(depth, max_level) + 1))
        values[depth] = _imbalance(bid_total, ask_total)
    weighted_bid = weighted_ask = 0.0
    weight_total = 0.0
    for level in range(1, max_level + 1):
        weight = 1.0 / level
        weighted_bid += (bids.get(level) or 0.0) * weight
        weighted_ask += (asks.get(level) or 0.0) * weight
        weight_total += weight
    value = _imbalance(weighted_bid, weighted_ask)
    return {
        "value": value,
        "obi_1": values[1],
        "obi_3": values[3],
        "obi_5": values[5],
        "obi_10": values[10],
        "weighted_bid_depth": weighted_bid / weight_total if weight_total else None,
        "weighted_ask_depth": weighted_ask / weight_total if weight_total else None,
        "available": value is not None,
    }
