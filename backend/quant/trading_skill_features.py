"""Point-in-time feature engine shared by V5 trading skills and validation.

Every rolling baseline excludes the current observation unless the formula
explicitly describes a value available at the current cutoff (for example
ATR14). Missing source fields remain ``None`` and are never replaced with a
model default.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from statistics import median
from typing import Any


EPS = 1e-9


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        output = float(value)
    except (TypeError, ValueError):
        return None
    return output if math.isfinite(output) else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    average = sum(values) / len(values)
    return math.sqrt(sum((item - average) ** 2 for item in values) / len(values))


def _z(value: float | None, baseline: list[float]) -> float | None:
    deviation = _std(baseline)
    average = _mean(baseline)
    if value is None or average is None or deviation in (None, 0):
        return None
    return (value - average) / deviation


def _ratio(value: float | None, baseline: list[float]) -> float | None:
    valid = [item for item in baseline if item > 0]
    denominator = median(valid) if valid else None
    if value is None or value <= 0 or denominator in (None, 0):
        return None
    return value / denominator


def _return(closes: list[float], sessions: int) -> float | None:
    if len(closes) <= sessions or closes[-sessions - 1] <= 0:
        return None
    return closes[-1] / closes[-sessions - 1] - 1


def _percentile(value: float | None, baseline: list[float]) -> float | None:
    valid = sorted(item for item in baseline if math.isfinite(item))
    if value is None or not valid:
        return None
    return sum(item <= value for item in valid) / len(valid)


def _coalesce(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


def _date_value(row: dict[str, Any]) -> date | None:
    raw = row.get("trade_date") or row.get("date") or row.get("datetime") or row.get("bar_time")
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def normalize_daily_bars(bars: list[dict[str, Any]], *, as_of: date | None = None) -> list[dict[str, Any]]:
    """Normalize and sort valid daily observations at or before ``as_of``."""
    normalized: list[dict[str, Any]] = []
    for raw in bars:
        trade_date = _date_value(raw)
        if trade_date is None or (as_of is not None and trade_date > as_of):
            continue
        close = _coalesce(raw, "close", "close_price")
        if close is None or close <= 0:
            continue
        normalized.append({
            "trade_date": trade_date,
            "open": _coalesce(raw, "open", "open_price"),
            "close": close,
            "high": _coalesce(raw, "high", "high_price"),
            "low": _coalesce(raw, "low", "low_price"),
            "volume": _coalesce(raw, "volume"),
            "amount": _coalesce(raw, "amount"),
            "turnover": _coalesce(raw, "turnover"),
            "available_time": raw.get("available_time") or raw.get("updated_at") or trade_date.isoformat(),
        })
    normalized.sort(key=lambda item: item["trade_date"])
    # A source retry may return the same trade date twice. Keep its last
    # observed row without manufacturing an additional trading session.
    return list({item["trade_date"]: item for item in normalized}.values())


def _true_range(rows: list[dict[str, Any]], index: int) -> float | None:
    row = rows[index]
    high, low = row.get("high"), row.get("low")
    if high is None or low is None or high < low:
        return None
    if index == 0:
        return high - low
    previous_close = rows[index - 1]["close"]
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def _atr(rows: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(rows) < period:
        return None
    values = [_true_range(rows, index) for index in range(max(0, len(rows) - period), len(rows))]
    valid = [value for value in values if value is not None]
    return _mean(valid) if len(valid) >= max(5, period // 2) else None


def _realized_volatility(closes: list[float]) -> float | None:
    if len(closes) < 3:
        return None
    returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes)) if closes[index - 1] > 0]
    return _std(returns)


def _efficiency_at(rows: list[dict[str, Any]], index: int) -> float | None:
    if index < 20:
        return None
    current = rows[index]
    previous = rows[index - 1]
    amount = current.get("amount")
    baseline = [item["amount"] for item in rows[index - 20:index] if item.get("amount") not in (None, 0)]
    amount_ratio = _ratio(amount, baseline)
    if amount_ratio is None or previous["close"] <= 0:
        return None
    signed_return = current["close"] / previous["close"] - 1
    liquidity_input = math.log1p(max(amount_ratio, 0))
    return signed_return / max(liquidity_input, EPS)


def _latest_volume_shock(rows: list[dict[str, Any]], lookback: int = 20) -> dict[str, Any] | None:
    start = max(20, len(rows) - lookback)
    shocks: list[dict[str, Any]] = []
    for index in range(start, len(rows)):
        row = rows[index]
        amount_baseline = [item["amount"] for item in rows[max(0, index - 60):index] if item.get("amount") not in (None, 0)]
        turnover_baseline = [item["turnover"] for item in rows[max(0, index - 120):index] if item.get("turnover") is not None]
        amount_z = _z(row.get("amount"), amount_baseline)
        amount_ratio = _ratio(row.get("amount"), amount_baseline[-20:])
        turnover_pct = _percentile(row.get("turnover"), turnover_baseline)
        if not (
            (amount_z is not None and amount_z >= 2)
            or (amount_ratio is not None and amount_ratio >= 1.8)
            or (turnover_pct is not None and turnover_pct >= 0.95)
        ):
            continue
        anchor_values = [value for value in (row.get("high"), row.get("low"), row.get("close")) if value is not None]
        anchor = sum(anchor_values) / len(anchor_values) if len(anchor_values) == 3 else row["close"]
        shocks.append({
            "index": index, "trade_date": row["trade_date"], "anchor": anchor,
            "high": row.get("high"), "low": row.get("low"), "close": row["close"],
            "amount": row.get("amount"), "amount_z": amount_z,
            "amount_ratio_20": amount_ratio, "turnover_percentile_120": turnover_pct,
        })
    return shocks[-1] if shocks else None


def _minute_features(minute_bars: list[dict[str, Any]], previous_close: float | None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for raw in minute_bars:
        timestamp = raw.get("bar_time") or raw.get("datetime") or raw.get("time")
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                timestamp = None
        close = _coalesce(raw, "close", "close_price")
        if not isinstance(timestamp, datetime) or close is None or close <= 0:
            continue
        rows.append({
            "time": timestamp, "close": close,
            "high": _coalesce(raw, "high", "high_price") or close,
            "low": _coalesce(raw, "low", "low_price") or close,
            "volume": _coalesce(raw, "volume"), "amount": _coalesce(raw, "amount"),
            "average_price": _coalesce(raw, "average_price"),
        })
    rows.sort(key=lambda item: item["time"])
    if not rows or previous_close in (None, 0):
        return {}
    first5 = [item for item in rows if (item["time"].hour, item["time"].minute) <= (9, 35)]
    first15 = [item for item in rows if (item["time"].hour, item["time"].minute) <= (9, 45)]
    first5 = first5 or rows[:5]
    first15 = first15 or rows[:15]
    vwap_denominator = sum(item["volume"] or 0 for item in first15)
    vwap = (
        sum((item["amount"] or 0) for item in first15) / vwap_denominator
        if vwap_denominator and any(item.get("amount") for item in first15) else None
    )
    if vwap is None:
        weighted = [item["average_price"] for item in first15 if item.get("average_price") is not None]
        vwap = _mean(weighted)
    above = [item for item in first15 if vwap is not None and item["close"] >= vwap]
    low = min(item["low"] for item in first15)
    return {
        "ret_5m": first5[-1]["close"] / previous_close - 1,
        "ret_15m": first15[-1]["close"] / previous_close - 1,
        "vwap_15m": vwap,
        "vwap_reclaim": bool(vwap is not None and first15[-1]["close"] >= vwap),
        "vwap_hold_minutes": len(above),
        "first_15m_drawdown": low / previous_close - 1,
        "first_15m_recovery": (first15[-1]["close"] - low) / max(previous_close - low, EPS),
        "minute_bar_count": len(rows),
    }


def build_skill_features(
    bars: list[dict[str, Any]],
    *,
    as_of: date | None = None,
    context: dict[str, Any] | None = None,
    auction: dict[str, Any] | None = None,
    minute_bars: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one feature vector using only observations available by ``as_of``."""
    rows = normalize_daily_bars(bars, as_of=as_of)
    context = context or {}
    if not rows:
        return {"history_sessions": 0, "data_date": None, "missing_reason": "no_daily_bars"}
    latest = rows[-1]
    previous = rows[-2] if len(rows) >= 2 else None
    closes = [row["close"] for row in rows]
    amounts = [row["amount"] for row in rows if row.get("amount") not in (None, 0)]
    volumes = [row["volume"] for row in rows if row.get("volume") not in (None, 0)]
    turnovers = [row["turnover"] for row in rows if row.get("turnover") is not None]
    amount_prior = [row["amount"] for row in rows[:-1] if row.get("amount") not in (None, 0)]
    volume_prior = [row["volume"] for row in rows[:-1] if row.get("volume") not in (None, 0)]
    turnover_prior = [row["turnover"] for row in rows[:-1] if row.get("turnover") is not None]
    high, low, open_price, close = latest.get("high"), latest.get("low"), latest.get("open"), latest["close"]
    valid_range = high is not None and low is not None and high >= low
    range_value = high - low if valid_range else None
    close_location = (close - low) / max(range_value, EPS) if valid_range else None
    lower_wick = (
        (min(open_price, close) - low) / max(range_value, EPS)
        if valid_range and open_price is not None else None
    )
    upper_wick = (
        (high - max(open_price, close)) / max(range_value, EPS)
        if valid_range and open_price is not None else None
    )
    intraday_drawdown = low / previous["close"] - 1 if low is not None and previous else None
    recovery_from_low = (
        (close - low) / max(previous["close"] - low, EPS)
        if low is not None and previous and previous["close"] > low else None
    )
    amount_ratio_5 = _ratio(latest.get("amount"), amount_prior[-5:])
    amount_ratio_20 = _ratio(latest.get("amount"), amount_prior[-20:])
    amount_z_60 = _z(latest.get("amount"), amount_prior[-60:])
    volume_z_60 = _z(latest.get("volume"), volume_prior[-60:])
    turnover_ratio_20 = _ratio(latest.get("turnover"), turnover_prior[-20:])
    turnover_percentile_120 = _percentile(latest.get("turnover"), turnover_prior[-120:])
    r1 = _return(closes, 1)
    prior_r1 = closes[-2] / closes[-3] - 1 if len(closes) >= 3 and closes[-3] > 0 else None
    relative_sector_1d = (
        r1 - _number(context.get("sector_return_1d"))
        if r1 is not None and _number(context.get("sector_return_1d")) is not None else None
    )
    relative_market_1d = (
        r1 - _number(context.get("market_return_1d"))
        if r1 is not None and _number(context.get("market_return_1d")) is not None else None
    )
    raw_efficiency = _efficiency_at(rows, len(rows) - 1)
    efficiencies = [_efficiency_at(rows, index) for index in range(max(20, len(rows) - 12), len(rows))]
    valid_efficiencies = [item for item in efficiencies if item is not None]
    eff_ma3 = _mean(valid_efficiencies[-3:])
    prior_eff_ma3 = _mean(valid_efficiencies[-6:-3]) if len(valid_efficiencies) >= 6 else None
    eff_delta = eff_ma3 - prior_eff_ma3 if eff_ma3 is not None and prior_eff_ma3 is not None else None
    atr14 = _atr(rows, 14)
    atr20 = _atr(rows, 20)
    prior20 = rows[-21:-1] if len(rows) >= 21 else rows[:-1]
    prior60 = rows[-61:-1] if len(rows) >= 61 else rows[:-1]
    lows20 = [row["low"] for row in prior20 if row.get("low") is not None]
    lows60 = [row["low"] for row in prior60 if row.get("low") is not None]
    highs20 = [row["high"] for row in prior20 if row.get("high") is not None]
    highs60 = [row["high"] for row in prior60 if row.get("high") is not None]
    support20 = min(lows20) if lows20 else None
    support60 = min(lows60) if lows60 else None
    support_candidates = [value for value in (support20, support60) if value is not None]
    support_level = max(support_candidates) if support_candidates else None
    break_depth_atr = (
        (support_level - low) / atr14
        if support_level is not None and low is not None and atr14 not in (None, 0) else None
    )
    reclaim_margin = (
        (close - support_level) / atr14
        if support_level is not None and atr14 not in (None, 0) else None
    )
    reclaim_recovery = (
        (close - low) / max(support_level - low, EPS)
        if support_level is not None and low is not None and low < support_level else None
    )
    shock = _latest_volume_shock(rows)
    shock_age = len(rows) - 1 - shock["index"] if shock else None
    price_retention = close / shock["anchor"] - 1 if shock and shock["anchor"] > 0 else None
    volume_contraction_after_shock = (
        latest.get("amount") / shock["amount"]
        if shock and shock.get("amount") not in (None, 0) and shock_age else None
    )
    rolling_high_20 = max(highs20) if highs20 else None
    rolling_high_60 = max(highs60) if highs60 else None
    low120_values = [row["low"] for row in rows[-120:] if row.get("low") is not None]
    high120_values = [row["high"] for row in rows[-120:] if row.get("high") is not None]
    low120 = min(low120_values) if low120_values else None
    high120 = max(high120_values) if high120_values else None
    position120 = (
        (close - low120) / max(high120 - low120, EPS)
        if low120 is not None and high120 is not None and high120 > low120 else None
    )

    # Pullback structure: the latest peak must precede at least one session.
    # The current bar is the possible re-launch observation. Find the prior
    # impulse peak without allowing today's high to erase the pullback window.
    trend_window = rows[-45:-1] if len(rows) > 1 else []
    if not trend_window:
        trend_window = rows[-1:]
    peak_local_index = max(range(len(trend_window)), key=lambda index: trend_window[index]["close"])
    peak_global_index = len(rows) - len(trend_window) + peak_local_index
    pullback_days = len(rows) - 1 - peak_global_index
    peak_close = rows[peak_global_index]["close"]
    pullback_depth = peak_close / close - 1 if peak_close > 0 else None
    pullback_atr = (peak_close - close) / atr20 if atr20 not in (None, 0) else None
    impulse_start = max(0, peak_global_index - max(5, min(20, pullback_days or 5)))
    impulse_amount = [row["amount"] for row in rows[impulse_start:peak_global_index + 1] if row.get("amount") not in (None, 0)]
    pullback_amount = [row["amount"] for row in rows[peak_global_index + 1:] if row.get("amount") not in (None, 0)]
    volume_contraction = (
        (_mean(pullback_amount) or 0) / (_mean(impulse_amount) or EPS)
        if pullback_amount and impulse_amount else None
    )
    impulse_vol = _realized_volatility([row["close"] for row in rows[impulse_start:peak_global_index + 1]])
    pullback_vol = _realized_volatility([row["close"] for row in rows[peak_global_index:]])
    volatility_contraction = (
        pullback_vol / impulse_vol if pullback_vol is not None and impulse_vol not in (None, 0) else None
    )
    pullback_highs = [row["high"] for row in rows[max(peak_global_index + 1, len(rows) - 6):-1] if row.get("high") is not None]
    recent_pullback_high = max(pullback_highs) if pullback_highs else None
    return20 = _return(closes, 20)
    alpha20 = (
        return20 - _number(context.get("sector_return_20d"))
        if return20 is not None and _number(context.get("sector_return_20d")) is not None else None
    )
    alpha_retention = (
        (close / rows[max(0, peak_global_index - 20)]["close"] - 1)
        - (_number(context.get("sector_return_20d")) or 0)
        if peak_global_index > 0 else None
    )
    prior_turnover5 = [row["turnover"] for row in rows[-6:-1] if row.get("turnover") is not None]

    features: dict[str, Any] = {
        "data_date": latest["trade_date"].isoformat(), "available_time": latest.get("available_time"),
        "history_sessions": len(rows), "open": open_price, "close": close, "high": high, "low": low,
        "volume": latest.get("volume"), "amount": latest.get("amount"), "turnover": latest.get("turnover"),
        "r_1d": r1, "r_3d": _return(closes, 3), "r_5d": _return(closes, 5),
        "r_10d": _return(closes, 10), "return_20d": return20,
        "relative_sector_1d": relative_sector_1d, "relative_market_1d": relative_market_1d,
        "relative_sector_20d": alpha20, "alpha_retention": alpha_retention,
        "amount_ratio_5": amount_ratio_5, "amount_ratio_20": amount_ratio_20,
        "turnover_ratio_20": turnover_ratio_20, "turnover_percentile_120": turnover_percentile_120,
        "amount_z_60": amount_z_60, "volume_z_60": volume_z_60,
        "range": range_value, "close_location": close_location,
        "lower_wick_ratio": lower_wick, "upper_wick_ratio": upper_wick,
        "intraday_drawdown": intraday_drawdown, "recovery_from_low": recovery_from_low,
        "atr14": atr14, "atr20": atr20, "raw_efficiency": raw_efficiency,
        "price_volume_efficiency": raw_efficiency, "eff_ma3": eff_ma3, "eff_delta": eff_delta,
        "support_20d_low": support20, "support_60d_low": support60, "support_level": support_level,
        "break_depth_atr": break_depth_atr, "reclaim_margin": reclaim_margin,
        "recovery_ratio": reclaim_recovery,
        "shock_anchor": shock.get("anchor") if shock else None,
        "shock_low": shock.get("low") if shock else None, "shock_high": shock.get("high") if shock else None,
        "shock_date": shock["trade_date"].isoformat() if shock else None, "shock_age": shock_age,
        "shock_amount_z": shock.get("amount_z") if shock else None,
        "price_retention": price_retention, "volume_contraction_after_shock": volume_contraction_after_shock,
        "rolling_high_20": rolling_high_20, "rolling_high_60": rolling_high_60,
        "position_120": position120, "low_120": low120, "high_120": high120,
        "peak_close": peak_close, "pullback_days": pullback_days, "pullback_depth": pullback_depth,
        "pullback_atr": pullback_atr, "volume_contraction": volume_contraction,
        "volatility_contraction": volatility_contraction, "recent_pullback_high": recent_pullback_high,
        "return_acceleration": r1 - prior_r1 if r1 is not None and prior_r1 is not None else None,
        "turnover_acceleration": _ratio(latest.get("turnover"), prior_turnover5),
        "market_state": context.get("market_state"), "sector_state": context.get("sector_state"),
        "sector_strength": _number(context.get("sector_strength")),
        "sector_breadth": _number(context.get("sector_breadth")),
        "alpha_density": _number(context.get("alpha_density")),
        "crowding_score": _number(context.get("crowding_score")),
        "behavior_imbalance_score": _number(context.get("behavior_imbalance_score")),
        "fomo_score": _number(context.get("fomo_score")), "panic_score": _number(context.get("panic_score")),
        "market_psychology_state": context.get("market_psychology_state"),
    }

    if auction:
        auction_price = _coalesce(auction, "auction_price", "price")
        auction_previous = _coalesce(auction, "previous_close", "prev_close") or (previous["close"] if previous else None)
        auction_gap = (
            auction_price / auction_previous - 1
            if auction_price is not None and auction_previous not in (None, 0) else None
        )
        features.update({
            "auction_observed": bool(auction.get("quote_at") or auction.get("trade_date")),
            "auction_quote_at": str(auction.get("quote_at") or "") or None,
            "auction_gap": auction_gap,
            "auction_amount": _coalesce(auction, "auction_amount"),
            "auction_amount_ratio": _coalesce(auction, "auction_amount_ratio", "auction_volume_ratio"),
            "auction_volume_ratio": _coalesce(auction, "auction_volume_ratio"),
            "auction_relative_sector": (
                auction_gap - _number(context.get("sector_preopen_return"))
                if auction_gap is not None and _number(context.get("sector_preopen_return")) is not None else None
            ),
            "auction_relative_market": (
                auction_gap - _number(context.get("market_preopen_return"))
                if auction_gap is not None and _number(context.get("market_preopen_return")) is not None else None
            ),
        })
    if minute_bars:
        features.update(_minute_features(minute_bars, previous["close"] if previous else None))
    return features


def future_outcome(
    bars: list[dict[str, Any]],
    signal_index: int,
    *,
    horizon: int,
    benchmark_returns: dict[str, float] | None = None,
) -> dict[str, float | None]:
    """Build labels after signal construction; never call this from live skills."""
    rows = normalize_daily_bars(bars)
    if signal_index < 0 or signal_index >= len(rows) - 1:
        return {"future_return": None, "future_excess": None, "mfe": None, "mae": None}
    end = min(len(rows) - 1, signal_index + horizon)
    entry = rows[signal_index]["close"]
    future_rows = rows[signal_index + 1:end + 1]
    if not future_rows or entry <= 0:
        return {"future_return": None, "future_excess": None, "mfe": None, "mae": None}
    final_return = future_rows[-1]["close"] / entry - 1
    highs = [(row.get("high") or row["close"]) / entry - 1 for row in future_rows]
    lows = [(row.get("low") or row["close"]) / entry - 1 for row in future_rows]
    benchmark = _number((benchmark_returns or {}).get(str(horizon))) or 0.0
    return {
        "future_return": final_return,
        "future_excess": final_return - benchmark,
        "mfe": max(highs),
        "mae": min(lows),
    }
