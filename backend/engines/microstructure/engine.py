"""Conservative, auditable Level-2 feature calculations.

These functions deliberately describe observable structure.  They do not
identify accounts, infer intent, or emit a buy/sell order.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from statistics import median, pstdev
from typing import Any, Iterable, Mapping

from config import settings
from market_data.level2.models import OrderBookSnapshot, OrderTick, TradeTick

from .active_flow import classify_trade
from .hfi import DEFAULT_WEIGHTS, build_hfi, clamp
from .order_imbalance import calculate_obi


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _amount(trade: TradeTick) -> float:
    amount = _number(trade.amount)
    if amount is not None and amount > 0:
        return amount
    price = _number(trade.price)
    volume = _number(trade.volume)
    return price * volume if price is not None and volume is not None and price > 0 and volume > 0 else 0.0


def _price(trade: TradeTick) -> float | None:
    value = _number(trade.price)
    return value if value is not None and value > 0 else None


def _quote_level(quote: OrderBookSnapshot | None, side: str, level: int = 1) -> tuple[float | None, float | None]:
    if quote is None:
        return None, None
    levels = quote.bids if side == "bid" else quote.asks
    item = next((row for row in levels if row.level == level), None)
    return (item.price, item.volume) if item else (None, None)


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    clean = sorted(value for value in values if value > 0 and math.isfinite(value))
    if not clean:
        return None
    position = (len(clean) - 1) * max(0.0, min(100.0, percentile)) / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return clean[lower]
    return clean[lower] + (clean[upper] - clean[lower]) * (position - lower)


def _quote_index(quotes: list[OrderBookSnapshot]) -> tuple[list[datetime], list[OrderBookSnapshot]]:
    ordered = sorted(quotes, key=lambda item: item.timestamp)
    return [item.timestamp for item in ordered], ordered


def _quote_at(
    timestamps: list[datetime],
    quotes: list[OrderBookSnapshot],
    timestamp: datetime,
) -> OrderBookSnapshot | None:
    # Avoid importing bisect in the hot loop for the common short historical
    # samples; the index is still binary searched for large sessions.
    import bisect

    position = bisect.bisect_right(timestamps, timestamp) - 1
    return quotes[position] if position >= 0 else None


def _annotate_trades(
    trades: list[TradeTick],
    quotes: list[OrderBookSnapshot],
) -> list[dict[str, Any]]:
    timestamps, ordered_quotes = _quote_index(quotes)
    ordered = sorted(trades, key=lambda item: item.timestamp)
    previous_price: float | None = None
    result: list[dict[str, Any]] = []
    for trade in ordered:
        quote = _quote_at(timestamps, ordered_quotes, trade.timestamp) if ordered_quotes else None
        side, method, confidence = classify_trade(trade, quote, previous_price)
        result.append({
            "trade": trade,
            "side": side,
            "method": method,
            "confidence": confidence,
            "amount": _amount(trade),
        })
        if _price(trade) is not None:
            previous_price = _price(trade)
    return result


def _split_scores(annotated: list[dict[str, Any]], threshold: float | None) -> tuple[float | None, float | None, dict[str, Any]]:
    if len(annotated) < 3:
        return None, None, {"sample_count": len(annotated), "method": "insufficient_consecutive_trades"}
    scores = {"buy": 0.0, "sell": 0.0}
    candidate_counts = {"buy": 0, "sell": 0}
    regularity: dict[str, list[float]] = {"buy": [], "sell": []}
    for previous, current in zip(annotated, annotated[1:]):
        side = current["side"]
        if side not in scores or previous["side"] != side:
            continue
        left: TradeTick = previous["trade"]
        right: TradeTick = current["trade"]
        gap = max(0.0, (right.timestamp - left.timestamp).total_seconds())
        left_price = _price(left)
        right_price = _price(right)
        price_near = left_price is not None and right_price is not None and abs(right_price / left_price - 1) <= 0.004
        left_amount = previous["amount"]
        right_amount = current["amount"]
        size_near = bool(left_amount and right_amount and 0.35 <= right_amount / left_amount <= 2.85)
        id_link = bool(
            (side == "buy" and left.buy_order_id and left.buy_order_id == right.buy_order_id)
            or (side == "sell" and left.sell_order_id and left.sell_order_id == right.sell_order_id)
        )
        if gap <= 5 and (price_near or id_link) and size_near:
            candidate_counts[side] += 1
            regularity[side].append(gap)
    total = max(1, len(annotated) - 1)
    for side in scores:
        coverage = candidate_counts[side] / total
        gaps = regularity[side]
        gap_regular = 1.0
        if len(gaps) >= 2:
            average = sum(gaps) / len(gaps)
            gap_regular = max(0.0, 1.0 - (pstdev(gaps) / max(average, 0.1)))
        id_bonus = min(1.0, candidate_counts[side] / 5.0)
        # A cluster must be visible; isolated small trades do not qualify.
        scores[side] = (coverage * 45 + gap_regular * 30 + id_bonus * 25) if candidate_counts[side] >= 2 else 0.0
    return (
        round(min(100.0, scores["buy"]), 2) if scores["buy"] else 0.0,
        round(min(100.0, scores["sell"]), 2) if scores["sell"] else 0.0,
        {"sample_count": len(annotated), "candidate_pairs": candidate_counts, "threshold": threshold},
    )


def _replenishment(quotes: list[OrderBookSnapshot]) -> tuple[float | None, float | None, dict[str, int]]:
    ordered = sorted(quotes, key=lambda item: item.timestamp)
    if len(ordered) < 2:
        return None, None, {"bid_count": 0, "ask_count": 0}
    counts = {"bid": 0, "ask": 0}
    for previous, current in zip(ordered, ordered[1:]):
        for side in ("bid", "ask"):
            _, previous_volume = _quote_level(previous, side)
            _, current_volume = _quote_level(current, side)
            if previous_volume and current_volume and current_volume >= previous_volume * 1.15:
                counts[side] += 1
    denominator = max(1, len(ordered) - 1)
    return (
        round(min(100.0, counts["bid"] / denominator * 250), 2),
        round(min(100.0, counts["ask"] / denominator * 250), 2),
        {"bid_count": counts["bid"], "ask_count": counts["ask"]},
    )


def _cancel_ratios(orders: list[OrderTick]) -> tuple[float | None, float | None, dict[str, int]]:
    if not orders:
        return None, None, {"buy_cancel_count": 0, "sell_cancel_count": 0, "buy_total": 0, "sell_total": 0}
    counts = {"buy_cancel_count": 0, "sell_cancel_count": 0, "buy_total": 0, "sell_total": 0}
    for order in orders:
        side = order.side if order.side in {"buy", "sell"} else None
        if side is None:
            continue
        counts[f"{side}_total"] += 1
        kind = str(order.order_type or "").strip().lower()
        is_cancel = any(token in kind for token in ("cancel", "撤", "取消", "撤销")) or kind in {"c", "cancelled"}
        if is_cancel:
            counts[f"{side}_cancel_count"] += 1
    return (
        counts["buy_cancel_count"] / counts["buy_total"] if counts["buy_total"] else None,
        counts["sell_cancel_count"] / counts["sell_total"] if counts["sell_total"] else None,
        counts,
    )


def _absorption(
    annotated: list[dict[str, Any]],
    bid_replenishment: float | None,
    ask_replenishment: float | None,
) -> tuple[float | None, float | None, dict[str, Any]]:
    buy = sum(item["amount"] for item in annotated if item["side"] == "buy")
    sell = sum(item["amount"] for item in annotated if item["side"] == "sell")
    active = buy + sell
    prices = [_price(item["trade"]) for item in annotated]
    clean_prices = [value for value in prices if value is not None]
    if active <= 0 or len(clean_prices) < 2:
        return None, None, {"method": "insufficient_active_flow_or_price"}
    move_pct = (clean_prices[-1] / clean_prices[0] - 1) * 100 if clean_prices[0] else 0.0
    # High opposing flow with low adverse price movement is an absorption
    # feature. It remains a score, not a statement about participant intent.
    down_resistance = max(0.0, 1.0 - min(1.0, max(0.0, -move_pct) / 1.0))
    up_resistance = max(0.0, 1.0 - min(1.0, max(0.0, move_pct) / 1.0))
    bid_score = sell / active * (0.65 * down_resistance + 0.35 * (float(bid_replenishment or 0) / 100)) * 100
    ask_score = buy / active * (0.65 * up_resistance + 0.35 * (float(ask_replenishment or 0) / 100)) * 100
    return (
        round(min(100.0, bid_score), 2),
        round(min(100.0, ask_score), 2),
        {"price_move_pct": round(move_pct, 4), "active_amount": active},
    )


def _spoof_score(quotes: list[OrderBookSnapshot]) -> tuple[float | None, dict[str, Any]]:
    """Conservative quote-only anomaly proxy; no intent is inferred."""
    ordered = sorted(quotes, key=lambda item: item.timestamp)
    if len(ordered) < 3:
        return None, {"method": "order_lifecycle_required", "sample_count": len(ordered)}
    events = 0
    for previous, current in zip(ordered, ordered[1:]):
        for side in ("bid", "ask"):
            _, old_volume = _quote_level(previous, side)
            _, new_volume = _quote_level(current, side)
            if old_volume and new_volume and old_volume > 0 and new_volume / old_volume <= 0.35:
                events += 1
    score = min(100.0, events / max(1, len(ordered) - 1) * 180)
    return round(score, 2), {"large_depth_drop_events": events, "sample_count": len(ordered), "method": "quote_depth_drop_proxy"}


def _micro_score(quotes: list[OrderBookSnapshot]) -> tuple[float | None, dict[str, Any]]:
    if not quotes:
        return None, {"method": "quote_unavailable"}
    spreads: list[float] = []
    depths: list[float] = []
    full_depth = 0
    for quote in quotes:
        bid, bid_volume = _quote_level(quote, "bid")
        ask, ask_volume = _quote_level(quote, "ask")
        if bid and ask and bid > 0 and ask >= bid:
            spreads.append((ask - bid) / ((ask + bid) / 2) * 100)
        depth = sum((level.volume or 0) for level in quote.bids + quote.asks)
        if depth > 0:
            depths.append(depth)
        if sum(level.price is not None and level.volume is not None for level in quote.bids + quote.asks) >= 16:
            full_depth += 1
    if not spreads and not depths:
        return None, {"method": "quote_fields_insufficient", "sample_count": len(quotes)}
    median_spread = median(spreads) if spreads else None
    spread_score = 75.0 if median_spread is None else max(0.0, min(100.0, 100.0 - median_spread * 180))
    depth_score = min(100.0, math.log10(max(median(depths), 1)) * 14) if depths else 0.0
    depth_coverage = full_depth / len(quotes) * 100 if quotes else 0.0
    score = spread_score * 0.55 + depth_score * 0.25 + depth_coverage * 0.20
    return round(score, 2), {
        "median_spread_pct": round(median_spread, 6) if median_spread is not None else None,
        "depth_coverage_pct": round(depth_coverage, 1),
        "sample_count": len(quotes),
    }


def _qas(
    *,
    order_count: int,
    quote_count: int,
    minute_count: int,
    cancel_buy: float | None,
    cancel_sell: float | None,
    split_buy: float | None,
    split_sell: float | None,
    replenishment_bid: float | None,
    replenishment_ask: float | None,
    price_vs_vwap: float | None,
) -> tuple[float | None, str, dict[str, Any]]:
    if order_count == 0 and quote_count == 0:
        return None, "暂无样本", {"method": "order_and_quote_unavailable"}
    duration = max(1, minute_count)
    update_score = min(100.0, (order_count + quote_count) / duration / 20 * 100)
    cancel_score = min(100.0, ((cancel_buy or 0) + (cancel_sell or 0)) / 2 * 100)
    repl_score = ((replenishment_bid or 0) + (replenishment_ask or 0)) / 2
    split_score = ((split_buy or 0) + (split_sell or 0)) / 2
    vwap_score = min(100.0, abs(price_vs_vwap or 0) / 1.5 * 100)
    score = update_score * 0.25 + cancel_score * 0.20 + repl_score * 0.18 + split_score * 0.20 + vwap_score * 0.17
    if score >= 70:
        kind = "执行算法型" if split_score >= 55 and vwap_score >= 35 else "高频更新型"
    elif score >= 45:
        kind = "中等活跃"
    else:
        kind = "低活跃"
    return round(score, 2), kind, {
        "update_score": round(update_score, 2),
        "cancel_score": round(cancel_score, 2),
        "replenishment_score": round(repl_score, 2),
        "split_score": round(split_score, 2),
        "vwap_tracking_score": round(vwap_score, 2),
    }


def _weights_from_settings() -> dict[str, float]:
    return {
        "active_flow": float(settings.level2_hfi_active_flow_weight),
        "absorption": float(settings.level2_hfi_absorption_weight),
        "split": float(settings.level2_hfi_split_weight),
        "imbalance": float(settings.level2_hfi_imbalance_weight),
        "replenishment": float(settings.level2_hfi_replenishment_weight),
        "vwap": float(settings.level2_hfi_vwap_weight),
        "impact": float(settings.level2_hfi_impact_weight),
    }


def build_feature_series(
    trades: list[TradeTick],
    orders: list[OrderTick] | None = None,
    quotes: list[OrderBookSnapshot] | None = None,
    *,
    hfi_weights: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate normalized records into one row per local trading minute."""
    orders = orders or []
    quotes = quotes or []
    annotated = _annotate_trades(trades, quotes)
    if not annotated and not quotes and not orders:
        return []
    amounts = [item["amount"] for item in annotated if item["amount"] > 0]
    threshold = _percentile(amounts, 85) or (median(amounts) * 3 if amounts else None)
    trade_groups: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    order_groups: dict[datetime, list[OrderTick]] = defaultdict(list)
    quote_groups: dict[datetime, list[OrderBookSnapshot]] = defaultdict(list)
    for item in annotated:
        timestamp: datetime = item["trade"].timestamp
        trade_groups[timestamp.replace(second=0, microsecond=0)].append(item)
    for order in orders:
        order_groups[order.timestamp.replace(second=0, microsecond=0)].append(order)
    for quote in quotes:
        quote_groups[quote.timestamp.replace(second=0, microsecond=0)].append(quote)
    minutes = sorted(set(trade_groups) | set(order_groups) | set(quote_groups))
    output: list[dict[str, Any]] = []
    weights = dict(hfi_weights or _weights_from_settings() or DEFAULT_WEIGHTS)
    for minute in minutes:
        minute_trades = trade_groups.get(minute, [])
        minute_orders = order_groups.get(minute, [])
        minute_quotes = sorted(quote_groups.get(minute, []), key=lambda item: item.timestamp)
        latest_quote = minute_quotes[-1] if minute_quotes else None
        buy_amount = sum(item["amount"] for item in minute_trades if item["side"] == "buy")
        sell_amount = sum(item["amount"] for item in minute_trades if item["side"] == "sell")
        neutral_amount = sum(item["amount"] for item in minute_trades if item["side"] == "neutral")
        active_amount = buy_amount + sell_amount
        prices = [_price(item["trade"]) for item in minute_trades]
        clean_prices = [item for item in prices if item is not None]
        priced_items = [
            (_price(item["trade"]), _number(item["trade"].volume) or 0)
            for item in minute_trades
        ]
        vwap_denominator = sum(volume for price, volume in priced_items if price is not None and volume > 0)
        vwap = (
            sum(price * volume for price, volume in priced_items if price is not None and volume > 0) / vwap_denominator
            if vwap_denominator else None
        )
        last_price = clean_prices[-1] if clean_prices else (latest_quote.last_price if latest_quote else None)
        price_vs_vwap = (last_price / vwap - 1) * 100 if last_price and vwap else None
        split_buy, split_sell, split_meta = _split_scores(minute_trades, threshold)
        repl_bid, repl_ask, repl_meta = _replenishment(minute_quotes)
        cancel_buy, cancel_sell, cancel_meta = _cancel_ratios(minute_orders)
        absorption_buy, absorption_sell, absorption_meta = _absorption(minute_trades, repl_bid, repl_ask)
        obi = calculate_obi(latest_quote)
        active_flow = (buy_amount - sell_amount) / active_amount if active_amount else None
        absorption_direction = ((absorption_buy or 0) - (absorption_sell or 0)) / 100 if absorption_buy is not None or absorption_sell is not None else None
        split_direction = ((split_buy or 0) - (split_sell or 0)) / 100 if split_buy is not None or split_sell is not None else None
        replenishment_direction = ((repl_bid or 0) - (repl_ask or 0)) / 100 if repl_bid is not None or repl_ask is not None else None
        vwap_direction = clamp((price_vs_vwap or 0) / 1.5) if price_vs_vwap is not None else None
        move_direction = ((clean_prices[-1] / clean_prices[0] - 1) if len(clean_prices) >= 2 and clean_prices[0] else None)
        impact_direction = clamp((active_flow or 0) * (1 if (move_direction or 0) >= 0 else -1)) if active_flow is not None and move_direction is not None else None
        confidence_parts = []
        if minute_trades:
            confidence_parts.append(min(1.0, len(minute_trades) / 20))
            confidence_parts.append(sum(item["confidence"] for item in minute_trades) / len(minute_trades))
        if minute_quotes:
            depth_fields = sum(sum(level.price is not None and level.volume is not None for level in quote.bids + quote.asks) for quote in minute_quotes)
            confidence_parts.append(min(1.0, depth_fields / max(1, len(minute_quotes) * 20)))
        if minute_orders:
            confidence_parts.append(min(1.0, len(minute_orders) / 20))
        confidence = sum(confidence_parts) / len(confidence_parts) * 100 if confidence_parts else 0.0
        if not minute_quotes or not minute_trades:
            confidence = min(confidence, 55.0)
        qas, qas_type, qas_meta = _qas(
            order_count=len(minute_orders), quote_count=len(minute_quotes), minute_count=1,
            cancel_buy=cancel_buy, cancel_sell=cancel_sell, split_buy=split_buy, split_sell=split_sell,
            replenishment_bid=repl_bid, replenishment_ask=repl_ask, price_vs_vwap=price_vs_vwap,
        )
        hfi = build_hfi(
            {
                "active_flow": active_flow,
                "absorption": absorption_direction,
                "split": split_direction,
                "imbalance": obi.get("value"),
                "replenishment": replenishment_direction,
                "vwap": vwap_direction,
                "impact": impact_direction,
            },
            weights=weights,
            confidence=confidence,
        )
        distribution, distribution_meta = _distribution_score(minute_trades, latest_quote, absorption_sell, repl_ask, price_vs_vwap)
        spoof, spoof_meta = _spoof_score(minute_quotes)
        micro, micro_meta = _micro_score(minute_quotes)
        explanation: list[str] = []
        if active_flow is not None:
            explanation.append(f"主动成交净方向 {('偏买' if active_flow > 0.15 else '偏卖' if active_flow < -0.15 else '中性')}，分类置信度 {confidence:.0f}%")
        if absorption_buy is not None and absorption_buy >= 65:
            explanation.append("主动卖出与价格变化不完全同步，出现较强买方承接特征")
        if absorption_sell is not None and absorption_sell >= 65:
            explanation.append("主动买入与价格效率不匹配，上方卖压吸收特征升高")
        if spoof is not None and spoof >= 60:
            explanation.append("盘口深度出现多次快速撤离，属于疑似异常挂撤单结构")
        if not explanation:
            explanation.append("当前分钟样本不足以形成明确微观结构解释")
        output.append({
            "minute": minute,
            "trade_count": len(minute_trades),
            "order_count": len(minute_orders),
            "quote_count": len(minute_quotes),
            "buy_amount": buy_amount or None,
            "sell_amount": sell_amount or None,
            "neutral_amount": neutral_amount or None,
            "net_active_amount": (buy_amount - sell_amount) if active_amount else None,
            "large_buy_amount": sum(item["amount"] for item in minute_trades if item["side"] == "buy" and threshold and item["amount"] >= threshold) or None,
            "large_sell_amount": sum(item["amount"] for item in minute_trades if item["side"] == "sell" and threshold and item["amount"] >= threshold) or None,
            "split_buy_score": split_buy,
            "split_sell_score": split_sell,
            "absorption_buy_score": absorption_buy,
            "absorption_sell_score": absorption_sell,
            "replenishment_bid": repl_bid,
            "replenishment_ask": repl_ask,
            "cancel_buy_ratio": cancel_buy,
            "cancel_sell_ratio": cancel_sell,
            "order_imbalance": obi.get("value"),
            "order_imbalance_1": obi.get("obi_1"),
            "order_imbalance_3": obi.get("obi_3"),
            "order_imbalance_5": obi.get("obi_5"),
            "order_imbalance_10": obi.get("obi_10"),
            "vwap": vwap,
            "last_price": last_price,
            "price_vs_vwap": price_vs_vwap,
            "qas": qas,
            "qas_type": qas_type,
            "hfi": hfi.get("value"),
            "hfi_components": hfi,
            "micro_score": micro,
            "distribution_score": distribution,
            "spoof_risk": spoof,
            "confidence": round(confidence, 1),
            "data_quality": "complete" if minute_trades and minute_quotes else "degraded",
            "components": {
                "active_flow": {"normalized": active_flow, "direction_method": _direction_methods(minute_trades)},
                "split": split_meta,
                "replenishment": repl_meta,
                "cancellation": cancel_meta,
                "absorption": absorption_meta,
                "qas": qas_meta,
                "distribution": distribution_meta,
                "spoof": spoof_meta,
                "microstructure": micro_meta,
                "hfi": hfi,
            },
            "explanation": explanation,
        })
    return output


def _direction_methods(items: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = defaultdict(int)
    for item in items:
        result[str(item["method"])] += 1
    return dict(result)


def _distribution_score(
    annotated: list[dict[str, Any]],
    quote: OrderBookSnapshot | None,
    absorption_sell: float | None,
    replenishment_ask: float | None,
    price_vs_vwap: float | None,
) -> tuple[float | None, dict[str, Any]]:
    buy = sum(item["amount"] for item in annotated if item["side"] == "buy")
    sell = sum(item["amount"] for item in annotated if item["side"] == "sell")
    active = buy + sell
    if active <= 0:
        return None, {"method": "active_flow_unavailable"}
    buy_share = buy / active
    range_position = None
    if quote and quote.high_price is not None and quote.low_price is not None and quote.last_price is not None and quote.high_price > quote.low_price:
        range_position = (quote.last_price - quote.low_price) / (quote.high_price - quote.low_price)
    efficiency_penalty = max(0.0, 1.0 - min(1.0, abs(price_vs_vwap or 0) / 1.0))
    score = buy_share * 40 + (range_position if range_position is not None else 0.5) * 25 + efficiency_penalty * 20 + float(absorption_sell or 0) * 0.1 + float(replenishment_ask or 0) * 0.15
    return round(min(100.0, score), 2), {"buy_share": round(buy_share, 4), "range_position": range_position, "method": "price_efficiency_and_ask_pressure"}


def _weighted_average(features: list[dict[str, Any]], key: str) -> float | None:
    values = [(float(row[key]), max(1, int(row.get("trade_count") or row.get("quote_count") or 1))) for row in features if row.get(key) is not None]
    if not values:
        return None
    return sum(value * weight for value, weight in values) / sum(weight for _, weight in values)


def _metric(value: float | None, label: str, confidence: float | None, **extra: Any) -> dict[str, Any]:
    return {
        "value": round(value, 2) if value is not None else None,
        "label": label if value is not None else "暂无样本",
        "available": value is not None,
        "confidence": round(max(0.0, min(100.0, float(confidence or 0))), 1),
        **extra,
    }


def build_summary(features: list[dict[str, Any]], quality: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build the API-level summary from persisted one-minute features."""
    quality_payload = dict(quality or {})
    if not features:
        return {
            "available": False,
            "status": "not_available",
            "data_quality": quality_payload or {"status": "not_available"},
            "confidence": 0.0,
            "hfi": _metric(None, "暂无样本", 0),
            "qas": _metric(None, "暂无样本", 0),
            "absorption": {"buy": _metric(None, "暂无样本", 0), "sell": _metric(None, "暂无样本", 0)},
            "distribution": _metric(None, "暂无样本", 0),
            "split": {"buy": _metric(None, "暂无样本", 0), "sell": _metric(None, "暂无样本", 0)},
            "replenishment": {"bid": _metric(None, "暂无样本", 0), "ask": _metric(None, "暂无样本", 0)},
            "spoof": _metric(None, "暂无样本", 0),
            "obi": _metric(None, "暂无样本", 0),
            "microstructure": _metric(None, "暂无样本", 0),
            "explanation": ["当前没有已缓存的 Level-2 逐笔或盘口样本。"],
            "timeline": [],
        }
    confidence = _weighted_average(features, "confidence") or 0.0
    status = str(quality_payload.get("status") or ("complete" if all(row.get("data_quality") == "complete" for row in features) else "degraded"))
    hfi_value = _weighted_average(features, "hfi")
    qas_value = _weighted_average(features, "qas")
    absorption_buy = _weighted_average(features, "absorption_buy_score")
    absorption_sell = _weighted_average(features, "absorption_sell_score")
    distribution = _weighted_average(features, "distribution_score")
    split_buy = _weighted_average(features, "split_buy_score")
    split_sell = _weighted_average(features, "split_sell_score")
    repl_bid = _weighted_average(features, "replenishment_bid")
    repl_ask = _weighted_average(features, "replenishment_ask")
    spoof = _weighted_average(features, "spoof_risk")
    obi = _weighted_average(features, "order_imbalance")
    micro = _weighted_average(features, "micro_score")
    hfi_label = "偏多" if hfi_value is not None and hfi_value >= 35 else "偏空" if hfi_value is not None and hfi_value <= -35 else "中性" if hfi_value is not None else "暂无样本"
    qas_label = "高" if qas_value is not None and qas_value >= 70 else "中" if qas_value is not None and qas_value >= 45 else "低" if qas_value is not None else "暂无样本"
    explanation: list[str] = []
    if hfi_value is not None:
        explanation.append(f"可观测隐性资金行为综合为{hfi_label}（HFI {hfi_value:+.1f}），不代表真实账户身份。")
    if absorption_buy is not None and absorption_buy >= 65:
        explanation.append("主动卖盘与价格变化不完全同步，买方承接特征较强；仍需趋势和板块确认。")
    if distribution is not None and distribution >= 75:
        explanation.append("主动买入与价格效率、上方深度变化出现不匹配，疑似派发风险升高。")
    if obi is not None:
        explanation.append(f"加权十档盘口失衡为{obi:+.2f}（{'买盘占优' if obi > 0.1 else '卖盘占优' if obi < -0.1 else '接近平衡'}）。")
    if not explanation:
        explanation.append("当前微观样本只支持结构观察，不单独形成交易结论。")
    latest = features[-1]
    timeline = [_timeline_row(row) for row in features]
    return {
        "available": True,
        "status": status,
        "data_quality": quality_payload,
        "confidence": round(confidence, 1),
        "hfi": _metric(hfi_value, hfi_label, confidence, range="-100..100"),
        "qas": _metric(qas_value, qas_label, confidence, activity_type=_latest_value(features, "qas_type")),
        "absorption": {
            "buy": _metric(absorption_buy, "强" if absorption_buy is not None and absorption_buy >= 70 else "一般" if absorption_buy is not None else "暂无样本", confidence),
            "sell": _metric(absorption_sell, "强" if absorption_sell is not None and absorption_sell >= 70 else "一般" if absorption_sell is not None else "暂无样本", confidence),
        },
        "distribution": _metric(distribution, "高" if distribution is not None and distribution >= 75 else "中" if distribution is not None and distribution >= 50 else "低" if distribution is not None else "暂无样本", confidence),
        "split": {
            "buy": _metric(split_buy, "明显" if split_buy is not None and split_buy >= 60 else "一般" if split_buy is not None else "暂无样本", confidence),
            "sell": _metric(split_sell, "明显" if split_sell is not None and split_sell >= 60 else "一般" if split_sell is not None else "暂无样本", confidence),
        },
        "replenishment": {
            "bid": _metric(repl_bid, "偏强" if repl_bid is not None and repl_bid >= 60 else "一般" if repl_bid is not None else "暂无样本", confidence),
            "ask": _metric(repl_ask, "偏强" if repl_ask is not None and repl_ask >= 60 else "一般" if repl_ask is not None else "暂无样本", confidence),
        },
        "spoof": _metric(spoof, "高" if spoof is not None and spoof >= 70 else "中" if spoof is not None and spoof >= 40 else "低" if spoof is not None else "暂无样本", confidence, note="仅为盘口深度异常代理，不能确认操纵意图"),
        "obi": _metric(obi, "买盘占优" if obi is not None and obi > 0.1 else "卖盘占优" if obi is not None and obi < -0.1 else "接近平衡" if obi is not None else "暂无样本", confidence, signed=True),
        "microstructure": _metric(micro, "良好" if micro is not None and micro >= 70 else "一般" if micro is not None and micro >= 45 else "偏弱" if micro is not None else "暂无样本", confidence),
        "latest": _timeline_row(latest),
        "explanation": explanation,
        "timeline": timeline,
    }


def _latest_value(features: list[dict[str, Any]], key: str) -> Any:
    for row in reversed(features):
        if row.get(key) not in (None, ""):
            return row[key]
    return None


def _timeline_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: (value.isoformat() if isinstance(value, datetime) else value)
        for key, value in dict(row).items()
        if key not in {"hfi_components", "components"}
    }


def detect_events(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return explainable markers for the Level-2 timeline."""
    events: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for row in features:
        minute = row.get("minute")
        checks = [
            ("hfi_buy", row.get("hfi") is not None and row["hfi"] >= 60, "隐性主动买入增强", "当前HFI偏多，需与趋势和板块确认"),
            ("hfi_sell", row.get("hfi") is not None and row["hfi"] <= -60, "隐性主动卖出增强", "当前HFI偏空，需关注承接和失效条件"),
            ("absorption", row.get("absorption_buy_score") is not None and row["absorption_buy_score"] >= 70, "承接增强", "主动卖出与价格下行不完全同步"),
            ("distribution", row.get("distribution_score") is not None and row["distribution_score"] >= 75, "疑似派发风险", "价格效率与主动买入出现不匹配"),
            ("split", max(row.get("split_buy_score") or 0, row.get("split_sell_score") or 0) >= 70, "程序化拆单特征增强", "同方向成交簇出现规律性特征"),
            ("obi", abs(row.get("order_imbalance") or 0) >= 0.35, "盘口突然失衡", "加权十档深度明显倾斜"),
            ("spoof", row.get("spoof_risk") is not None and row["spoof_risk"] >= 70, "疑似异常挂撤单", "盘口深度快速撤离，但不能确认操纵意图"),
        ]
        for event_type, triggered, label, reason in checks:
            if not triggered:
                continue
            # Avoid repeating the same marker on every adjacent minute unless
            # the score materially changed.
            if previous and previous.get(event_type) and event_type not in {"hfi_buy", "hfi_sell"}:
                continue
            events.append({
                "event_type": event_type,
                "label": label,
                "minute": minute.isoformat() if isinstance(minute, datetime) else minute,
                "confidence": row.get("confidence", 0),
                "reason": reason,
                "hfi": row.get("hfi"),
                "obi": row.get("order_imbalance"),
                "absorption": row.get("absorption_buy_score"),
                "distribution": row.get("distribution_score"),
            })
        previous = {event_type: triggered for event_type, triggered, _, _ in checks}
    return events
