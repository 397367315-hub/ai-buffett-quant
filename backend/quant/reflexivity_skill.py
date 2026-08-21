"""Deterministic Skill 10: behaviour, reflexivity and liquidity diagnosis.

The module intentionally works from observable daily data.  It can consume
minute bars and an auction snapshot when those observations exist, but it
never reconstructs order-book intent, short covering or an actor identity
from OHLCV alone.  Every public result carries coverage and cut-off metadata
so a caller can distinguish a calculated value from an unavailable source.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from quant.trading_skill_features import build_skill_features, normalize_daily_bars
from quant.trading_skills import absorption_pressure


SKILL_ID = "skill_10_behavior_reflexivity"
SKILL_VERSION = "1.0.0"
MODEL_VERSION = "reflexivity-daily-v1"

PSYCHOLOGY_STATES = (
    "冷漠", "怀疑", "试探", "相信", "追逐", "亢奋", "分歧", "恐慌", "绝望", "修复"
)

PSYCHOLOGY_LABELS = {
    "冷漠": "交易兴趣低，价格和成交尚未形成方向",
    "怀疑": "方向出现但参与者仍在等待验证",
    "试探": "价格和成交开始改善，仍需要底层确认",
    "相信": "相对强度和承接形成，参与度正常增加",
    "追逐": "价格加速和换手扩张，追涨行为正在增加",
    "亢奋": "价格、成交和拥挤同时处于高位，边际风险上升",
    "分歧": "价格仍有表现，但资金、宽度或效率出现分化",
    "恐慌": "下跌速度和被迫卖出压力同步扩散",
    "绝望": "极端弱势后流动性收缩，尚未得到承接确认",
    "修复": "下跌压力边际减弱，承接和相对强度开始恢复",
}

STAGE_LABELS = {
    "POSITIVE_REFLEXIVITY": "正向反身性增强",
    "NEGATIVE_REFLEXIVITY": "负向反身性",
    "NEGATIVE_REFLEXIVITY_ACCELERATION": "负向反身性加速",
    "POSITIVE_REFLEXIVITY_DECAY": "正向反身性衰减",
    "ALPHA_SEED_REFLEXIVITY": "Alpha萌芽反身性",
    "NEUTRAL": "反身性暂未形成",
}

CANDIDATE_LABELS = {
    "PANIC_ABSORPTION_CANDIDATE": "恐慌吸收",
    "ALPHA_SEED_REFLEXIVITY": "Alpha萌芽",
    "POSITIVE_REFLEXIVITY_CANDIDATE": "正向反身",
    "HIGH_LEVEL_REFLEXIVITY_DECAY": "高位衰减",
    "NEGATIVE_REFLEXIVITY_ACCELERATION": "负向加速",
    "NO_CLEAR_CANDIDATE": "暂无明确候选",
}

OPPORTUNITY_CANDIDATES = {
    "PANIC_ABSORPTION_CANDIDATE",
    "ALPHA_SEED_REFLEXIVITY",
    "POSITIVE_REFLEXIVITY_CANDIDATE",
}


def _num(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _round(value: Any, digits: int = 3) -> float | None:
    parsed = _num(value)
    return round(parsed, digits) if parsed is not None else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _mean(values: list[float | None]) -> float | None:
    valid = [value for value in values if value is not None and math.isfinite(value)]
    return sum(valid) / len(valid) if valid else None


def _median(values: list[float | None]) -> float | None:
    valid = sorted(value for value in values if value is not None and math.isfinite(value))
    if not valid:
        return None
    middle = len(valid) // 2
    return valid[middle] if len(valid) % 2 else (valid[middle - 1] + valid[middle]) / 2


def _scale(value: float | None, low: float, high: float) -> float | None:
    if value is None or high <= low:
        return None
    return _clamp((value - low) / (high - low) * 100)


def _inverse_scale(value: float | None, low: float, high: float) -> float | None:
    scaled = _scale(value, low, high)
    return 100 - scaled if scaled is not None else None


def _weighted(parts: list[tuple[float | None, float]]) -> tuple[float | None, float]:
    observed = [(value, weight) for value, weight in parts if value is not None and weight > 0]
    if not observed:
        return None, 0.0
    total = sum(weight for _, weight in observed)
    return sum(value * weight for value, weight in observed) / total, total


def _trend(delta: float | None, *, threshold: float = 2.5) -> str:
    if delta is None:
        return "未形成时间序列"
    if delta >= threshold:
        return "增强"
    if delta <= -threshold:
        return "减弱"
    return "平稳"


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _close(row: dict[str, Any]) -> float | None:
    return _num(row.get("close"))


def _amount(row: dict[str, Any]) -> float | None:
    return _num(row.get("amount"))


def _volume(row: dict[str, Any]) -> float | None:
    return _num(row.get("volume"))


def _return(rows: list[dict[str, Any]], sessions: int) -> float | None:
    if len(rows) <= sessions:
        return None
    current, previous = _close(rows[-1]), _close(rows[-sessions - 1])
    if current is None or previous in (None, 0):
        return None
    return current / previous - 1


def _decimal_return(value: Any) -> float | None:
    """Accept either a decimal return or a quote API percentage field."""
    parsed = _num(value)
    if parsed is None:
        return None
    return parsed / 100 if abs(parsed) > 1 else parsed


def _amount_ratio(rows: list[dict[str, Any]], lookback: int = 20) -> float | None:
    if not rows:
        return None
    baseline = [_amount(row) for row in rows[-lookback - 1:-1]]
    median_amount = _median(baseline)
    current = _amount(rows[-1])
    if current is None or median_amount in (None, 0):
        return None
    return current / median_amount


def _turnover_ratio(rows: list[dict[str, Any]], lookback: int = 20) -> float | None:
    if not rows:
        return None
    baseline = [_num(row.get("turnover")) for row in rows[-lookback - 1:-1]]
    median_turnover = _median(baseline)
    current = _num(rows[-1].get("turnover"))
    if current is None or median_turnover in (None, 0):
        return None
    return current / median_turnover


def _prior_extreme(rows: list[dict[str, Any]], field: str, window: int, fn) -> float | None:
    sample = rows[-window - 1:-1]
    values = [_num(row.get(field)) for row in sample]
    values = [value for value in values if value is not None]
    return fn(values) if values else None


def _true_range(rows: list[dict[str, Any]], index: int) -> float | None:
    high, low = _num(rows[index].get("high")), _num(rows[index].get("low"))
    if high is None or low is None or high < low:
        return None
    if index == 0:
        return high - low
    previous = _close(rows[index - 1])
    if previous is None:
        return high - low
    return max(high - low, abs(high - previous), abs(low - previous))


def _atr(rows: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(rows) < period:
        return None
    values = [_true_range(rows, index) for index in range(len(rows) - period, len(rows))]
    return _mean(values)


def _percentile(value: float | None, values: list[float | None]) -> float | None:
    valid = sorted(item for item in values if item is not None and math.isfinite(item))
    if value is None or not valid:
        return None
    return sum(item <= value for item in valid) / len(valid) * 100


def _raw_efficiency(rows: list[dict[str, Any]], index: int) -> float | None:
    if index < 1:
        return None
    current, previous = _close(rows[index]), _close(rows[index - 1])
    if current is None or previous in (None, 0):
        return None
    amount = _amount(rows[index])
    baseline = [_amount(row) for row in rows[max(0, index - 20):index]]
    ratio = amount / (_median(baseline) or 0) if amount is not None and _median(baseline) not in (None, 0) else None
    if ratio is None or ratio <= 0:
        return None
    return (current / previous - 1) / max(math.log1p(ratio), 1e-9)


def _efficiency_series(rows: list[dict[str, Any]]) -> list[float | None]:
    return [_raw_efficiency(rows, index) for index in range(len(rows))]


def _extract_evidence(result: dict[str, Any], label: str) -> float | None:
    for item in result.get("evidence") or []:
        if item.get("label") == label:
            return _num(item.get("value"))
    return None


def _volume_shock_index(rows: list[dict[str, Any]]) -> int | None:
    candidates: list[int] = []
    for index in range(20, len(rows)):
        amount = _amount(rows[index])
        baseline = [_amount(row) for row in rows[index - 20:index]]
        median_amount = _median(baseline)
        turnover = _num(rows[index].get("turnover"))
        turnover_baseline = [_num(row.get("turnover")) for row in rows[max(0, index - 60):index]]
        turnover_pct = _percentile(turnover, turnover_baseline)
        if amount is not None and median_amount not in (None, 0) and amount / median_amount >= 1.8:
            candidates.append(index)
        elif turnover_pct is not None and turnover_pct >= 95:
            candidates.append(index)
    return candidates[-1] if candidates else None


def _vwap(rows: list[dict[str, Any]]) -> float | None:
    amount = sum(_amount(row) or 0 for row in rows)
    volume = sum(_volume(row) or 0 for row in rows)
    if amount > 0 and volume > 0:
        return amount / volume
    typical = []
    for row in rows:
        high, low, close = _num(row.get("high")), _num(row.get("low")), _close(row)
        if high is not None and low is not None and close is not None:
            typical.append((high + low + close) / 3)
    return _mean(typical)


def _volume_profile(rows: list[dict[str, Any]], bins: int = 12) -> dict[str, Any]:
    observations: list[tuple[float, float]] = []
    for row in rows[-120:]:
        high, low, close = _num(row.get("high")), _num(row.get("low")), _close(row)
        weight = _amount(row) or _volume(row)
        if close is None or weight is None or weight <= 0:
            continue
        typical = (high + low + close) / 3 if high is not None and low is not None else close
        observations.append((typical, weight))
    if not observations:
        return {"hvn": None, "lvn": None, "coverage": 0}
    low = min(price for price, _ in observations)
    high = max(price for price, _ in observations)
    if math.isclose(low, high):
        return {"hvn": low, "lvn": low, "coverage": len(observations)}
    step = (high - low) / bins
    totals = [0.0] * bins
    for price, weight in observations:
        index = min(bins - 1, max(0, int((price - low) / step)))
        totals[index] += weight
    max_index = max(range(bins), key=lambda index: totals[index])
    nonzero = [index for index, value in enumerate(totals) if value > 0]
    min_index = min(nonzero, key=lambda index: totals[index]) if nonzero else max_index
    return {
        "hvn": low + (max_index + 0.5) * step,
        "lvn": low + (min_index + 0.5) * step,
        "coverage": len(observations),
    }


def _gap_zones(rows: list[dict[str, Any]], atr: float | None) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    for index in range(1, len(rows)):
        previous_close = _close(rows[index - 1])
        open_price = _num(rows[index].get("open"))
        if previous_close in (None, 0) or open_price is None:
            continue
        gap = abs(open_price / previous_close - 1)
        threshold = max(0.02, (atr / previous_close * 0.8) if atr and previous_close else 0)
        if gap < threshold:
            continue
        zones.append({
            "price_low": min(open_price, previous_close),
            "price_high": max(open_price, previous_close),
            "price": (open_price + previous_close) / 2,
            "trade_date": _date(rows[index].get("trade_date")),
            "gap_pct": gap * 100,
        })
    return zones[-8:]


def _liquidity_map(rows: list[dict[str, Any]], point: dict[str, Any]) -> dict[str, Any]:
    close = point.get("close")
    if close is None or close <= 0:
        return {
            "available": False, "zones": [], "nearest_up_liquidity_zone": None,
            "nearest_down_liquidity_zone": None, "distance_to_up_liquidity": None,
            "distance_to_down_liquidity": None, "liquidity_asymmetry_score": None,
            "coverage": {"observed_sessions": len(rows), "required_sessions": 20},
        }

    atr = point.get("atr14")
    candidates: list[dict[str, Any]] = []

    def add(price: float | None, zone_type: str, label: str, source: str, trade_date: Any = None):
        if price is None or price <= 0 or not math.isfinite(price):
            return
        candidates.append({
            "zone_type": zone_type, "label": label, "price": round(price, 4),
            "source": source, "trade_date": _date(trade_date).isoformat() if _date(trade_date) else None,
        })

    for window, label in ((20, "20日区间"), (60, "60日区间"), (120, "120日区间")):
        prior = rows[-window - 1:-1]
        if not prior:
            continue
        high = max((_num(row.get("high")) for row in prior if _num(row.get("high")) is not None), default=None)
        low = min((_num(row.get("low")) for row in prior if _num(row.get("low")) is not None), default=None)
        add(high, "TRAPPED_SUPPLY_ZONE", f"{label}前高", "历史高点")
        add(low, "STOP_CLUSTER", f"{label}前低", "历史低点")

    shock_index = _volume_shock_index(rows)
    if shock_index is not None:
        shock_vwap = _vwap(rows[shock_index:])
        add(shock_vwap, "ABSORPTION_ZONE", "异常成交成本区", "成交异常后VWAP", rows[shock_index].get("trade_date"))
    anchored_index = shock_index
    if anchored_index is None:
        # A breakout anchor is used only when a prior high was actually
        # exceeded.  It is never fabricated from the current price.
        prior_high = _prior_extreme(rows, "high", 20, max)
        latest_close = _close(rows[-1])
        if prior_high and latest_close and latest_close > prior_high:
            anchored_index = max(0, len(rows) - 5)
    anchored = _vwap(rows[anchored_index:]) if anchored_index is not None else None
    add(anchored, "BREAKOUT_CHASE_ZONE", "锚定VWAP", "可观测成交额/成交量锚定")

    profile = _volume_profile(rows)
    add(profile.get("hvn"), "ABSORPTION_ZONE", "成交密集区HVN", "120日成交分布近似")
    add(profile.get("lvn"), "BREAKOUT_CHASE_ZONE", "成交稀疏区LVN", "120日成交分布近似")
    for gap in _gap_zones(rows, atr):
        add(gap.get("price"), "PROFIT_SUPPLY_ZONE", "缺口交易区", "历史跳空区", gap.get("trade_date"))

    # Keep one representative zone per near-identical price and preserve the
    # nearest observed level in each direction.
    candidates.sort(key=lambda item: item["price"])
    unique: list[dict[str, Any]] = []
    tolerance = max(close * 0.003, (atr or 0) * 0.25)
    for item in candidates:
        if any(abs(item["price"] - existing["price"]) <= tolerance for existing in unique):
            continue
        item["direction"] = "up" if item["price"] > close else "down" if item["price"] < close else "at_price"
        item["distance_pct"] = round((item["price"] / close - 1) * 100, 3)
        unique.append(item)
    up = [item for item in unique if item["price"] > close]
    down = [item for item in unique if item["price"] < close]
    nearest_up = min(up, key=lambda item: item["price"]) if up else None
    nearest_down = max(down, key=lambda item: item["price"]) if down else None
    up_distance = abs(nearest_up["distance_pct"]) if nearest_up else None
    down_distance = abs(nearest_down["distance_pct"]) if nearest_down else None
    asymmetry = None
    if up_distance is not None and down_distance is not None and up_distance + down_distance > 0:
        # Share of the two nearest observed liquidity distances occupied by
        # the upside.  It is a geometry measure, not a return prediction.
        asymmetry = up_distance / (up_distance + down_distance) * 100
    return {
        "available": bool(unique), "zones": unique[-24:],
        "nearest_up_liquidity_zone": nearest_up,
        "nearest_down_liquidity_zone": nearest_down,
        "distance_to_up_liquidity": up_distance,
        "distance_to_down_liquidity": down_distance,
        "liquidity_asymmetry_score": round(asymmetry, 1) if asymmetry is not None else None,
        "interpretation": "上方供应距离较远" if asymmetry is not None and asymmetry >= 60 else "下方承接距离较远" if asymmetry is not None and asymmetry <= 40 else "上下方流动性距离接近" if asymmetry is not None else "当前价格一侧缺少已观测密集区",
        "profile": {"hvn": _round(profile.get("hvn"), 4), "lvn": _round(profile.get("lvn"), 4), "coverage": profile.get("coverage", 0)},
        "prior_high": _round(point.get("high20"), 4),
        "prior_low": _round(point.get("low20"), 4),
        "recent_breakout_level": _round(point.get("high20"), 4) if point.get("close") and point.get("high20") and point["close"] > point["high20"] else None,
        "recent_breakdown_level": _round(point.get("low20"), 4) if point.get("close") and point.get("low20") and point["close"] < point["low20"] else None,
        "20d_high_low": {"high": _round(point.get("high20"), 4), "low": _round(point.get("low20"), 4)},
        "60d_high_low": {"high": _round(point.get("high60"), 4), "low": _round(point.get("low60"), 4)},
        "120d_high_low": {"high": _round(point.get("high120"), 4), "low": _round(point.get("low120"), 4)},
        "gap_zones": [
            {**item, "trade_date": _date(item.get("trade_date")).isoformat() if _date(item.get("trade_date")) else None}
            for item in _gap_zones(rows, atr)
        ],
        "anchored_vwap": _round(anchored, 4),
        "volume_shock_vwap": _round(_vwap(rows[shock_index:]) if shock_index is not None else None, 4),
        "volume_profile_hvn": _round(profile.get("hvn"), 4),
        "volume_profile_lvn": _round(profile.get("lvn"), 4),
        "coverage": {"observed_sessions": len(rows), "required_sessions": 20},
    }


def _point_snapshot(rows: list[dict[str, Any]], context: dict[str, Any] | None = None, auction: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    feature_context = dict(context)
    feature_context["sector_return_1d"] = _decimal_return(context.get("sector_return_1d"))
    feature_context["market_return_1d"] = _decimal_return(context.get("market_return_1d"))
    features = build_skill_features(rows, as_of=_date(rows[-1].get("trade_date")), context=feature_context, auction=auction)
    close = _close(rows[-1])
    previous_close = _close(rows[-2]) if len(rows) > 1 else None
    high20 = _prior_extreme(rows, "high", 20, max)
    low20 = _prior_extreme(rows, "low", 20, min)
    high60 = _prior_extreme(rows, "high", 60, max)
    low60 = _prior_extreme(rows, "low", 60, min)
    high120 = _prior_extreme(rows, "high", 120, max)
    low120 = _prior_extreme(rows, "low", 120, min)
    r1 = _return(rows, 1)
    r3 = _return(rows, 3)
    r5 = _return(rows, 5)
    r20 = _return(rows, 20)
    prior_r1 = _return(rows[:-1], 1) if len(rows) > 2 else None
    raw_eff = _raw_efficiency(rows, len(rows) - 1)
    efficiency_values = _efficiency_series(rows[:-1])
    eff_percentile = _percentile(abs(raw_eff) if raw_eff is not None else None, [abs(value) for value in efficiency_values if value is not None])
    absorption_result = absorption_pressure(features)
    absorption = _extract_evidence(absorption_result, "承接分")
    pressure = _extract_evidence(absorption_result, "抛压分")
    amount_ratio = _amount_ratio(rows)
    turnover_ratio = _turnover_ratio(rows)
    alpha = _num(context.get("stock_alpha_score"))
    if alpha is None:
        alpha = _num(context.get("alpha_score"))
    alpha_density = _num(context.get("alpha_density"))
    sector_strength = _num(context.get("sector_strength"))
    sector_breadth = _num(context.get("sector_breadth"))
    crowding = _num(context.get("crowding_score"))
    panic = _num(context.get("panic_score"))
    fomo = _num(context.get("fomo_score"))
    market_return = _decimal_return(context.get("market_return_1d"))
    sector_return = _decimal_return(context.get("sector_return_1d"))
    relative = features.get("relative_sector_1d")
    if relative is None and r1 is not None and sector_return is not None:
        relative = r1 - sector_return
    position = features.get("position_120")
    support_break = (
        (low20 - _num(rows[-1].get("low"))) / low20 * 100
        if low20 not in (None, 0) and _num(rows[-1].get("low")) is not None and _num(rows[-1].get("low")) < low20
        else 0.0
    )
    high_failure = _clamp(
        ((_num(rows[-1].get("upper_wick_ratio")) or features.get("upper_wick_ratio") or 0) * 100)
        + _clamp((1 - (features.get("close_location") or 0.5)) * 60)
    )
    # A-share public data does not expose a complete short-interest/covering
    # stream.  Keep this field explicit and out of the pressure score.
    fomo_parts = [
        (_scale(r1, 0.015, 0.08), 0.14),
        (_scale(features.get("return_acceleration"), 0.0, 0.04), 0.16),
        (_scale(turnover_ratio, 1.0, 3.0), 0.15),
        (_scale(sector_strength, 55, 90), 0.10),
        (_scale(sector_breadth, 55, 90), 0.08),
        (_scale(alpha_density, 50, 90), 0.08),
        (_scale(crowding, 50, 95), 0.14),
        (_scale(position, 0.65, 0.98), 0.15),
    ]
    forced_buy, buy_weight = _weighted(fomo_parts)
    local_panic = _weighted([
        (_scale(-r1 if r1 is not None else None, 0.015, 0.08), 0.15),
        (_scale(-r3 if r3 is not None else None, 0.03, 0.15), 0.15),
        (_scale(turnover_ratio, 1.0, 3.0), 0.14),
        (_scale(pressure, 55, 95), 0.16),
        (_scale(panic, 45, 95), 0.16),
        (_scale(1 - (features.get("close_location") or 0.5), 0.35, 0.95), 0.10),
        (_scale(support_break, 0.5, 8), 0.08),
        (_scale(crowding, 55, 95), 0.06),
    ])
    forced_sell, sell_weight = local_panic
    # Profit taking is observable as a high run-up plus high-position / upper
    # wick pressure, but it is kept separate from panic selling.
    profit_taking, profit_weight = _weighted([
        (_scale(r20, 0.15, 0.8), 0.35),
        (_scale(position, 0.7, 0.98), 0.25),
        (_scale(features.get("upper_wick_ratio"), 0.15, 0.6), 0.20),
        (_scale(turnover_ratio, 1.2, 3), 0.20),
    ])
    buy_pressure = forced_buy
    sell_pressure = forced_sell
    efficiency_delta_1d = None
    efficiency_delta_3d = None
    efficiency_acceleration = None
    if raw_eff is not None and efficiency_values:
        previous_eff = efficiency_values[-1]
        if previous_eff is not None:
            efficiency_delta_1d = raw_eff - previous_eff
            previous_delta = (
                efficiency_values[-1] - efficiency_values[-2]
                if len(efficiency_values) >= 2 and efficiency_values[-2] is not None else None
            )
            efficiency_acceleration = efficiency_delta_1d - previous_delta if previous_delta is not None else None
        prior_three = [value for value in efficiency_values[-3:] if value is not None]
        if prior_three:
            efficiency_delta_3d = raw_eff - sum(prior_three) / len(prior_three)
    capital_efficiency_score = None
    if eff_percentile is not None:
        capital_efficiency_score = 50 + (eff_percentile / 2 if (raw_eff or 0) >= 0 else -eff_percentile / 2)
    buy_response_weak = bool(
        r1 is not None and r1 > 0 and amount_ratio is not None and amount_ratio >= 1.5
        and efficiency_delta_1d is not None and efficiency_delta_1d < 0
    )
    sell_response_weak = bool(
        r1 is not None and r1 < 0 and amount_ratio is not None and amount_ratio >= 1.5
        and efficiency_delta_1d is not None and efficiency_delta_1d > 0
        and (features.get("recovery_from_low") or 0) >= 0.45
    )
    return {
        "features": features,
        "close": close, "previous_close": previous_close,
        "r1": r1, "r3": r3, "r5": r5, "r20": r20,
        "prior_r1": prior_r1, "return_acceleration": features.get("return_acceleration"),
        "amount_ratio_20": amount_ratio, "turnover_ratio_20": turnover_ratio,
        "turnover_acceleration": features.get("turnover_acceleration"),
        "high20": high20, "low20": low20, "high60": high60, "low60": low60,
        "high120": high120, "low120": low120, "position120": position,
        "support_break_distance": _round(support_break), "high_level_failure": _round(high_failure),
        "raw_efficiency": raw_eff, "efficiency_percentile": eff_percentile,
        "capital_price_efficiency": capital_efficiency_score,
        "efficiency_delta_1d": efficiency_delta_1d,
        "efficiency_delta_3d": efficiency_delta_3d,
        "efficiency_acceleration": efficiency_acceleration,
        "buy_force_price_response_weak": buy_response_weak,
        "sell_force_price_response_weak": sell_response_weak,
        "absorption_score": absorption, "pressure_score": pressure,
        "absorption_stage": absorption_result.get("stage"),
        "alpha_score": alpha, "alpha_density": alpha_density,
        "relative_sector_1d": relative, "market_return_1d": market_return,
        "sector_return_1d": sector_return, "sector_strength": sector_strength,
        "sector_breadth": sector_breadth, "crowding_score": crowding,
        "panic_score": panic, "fomo_score": fomo,
        "forced_buy_pressure": buy_pressure,
        "forced_sell_pressure": sell_pressure,
        "profit_taking_pressure": profit_taking,
        "buy_pressure_coverage": round(buy_weight / sum(weight for _, weight in fomo_parts) * 100, 1),
        "sell_pressure_coverage": round(sell_weight / 1.0 * 100, 1),
        "short_cover_pressure": {"value": None, "status": "disabled", "reason": "公开源未提供完整可验证的A股空头回补/借券压力序列"},
        "data_date": _date(rows[-1].get("trade_date")),
        "history_sessions": len(rows),
        "recovery_from_low": features.get("recovery_from_low"),
    }


def _psychology_scores(point: dict[str, Any]) -> dict[str, float]:
    r1, r3, r20 = point.get("r1"), point.get("r3"), point.get("r20")
    amount_ratio, turnover_ratio = point.get("amount_ratio_20"), point.get("turnover_ratio_20")
    position, location = point.get("position120"), (point.get("features") or {}).get("close_location")
    absorption, pressure = point.get("absorption_score"), point.get("pressure_score")
    acceleration = point.get("return_acceleration")
    crowding, alpha = point.get("crowding_score"), point.get("alpha_score")
    fomo = point.get("forced_buy_pressure")
    sell = point.get("forced_sell_pressure")
    scores = {state: 0.0 for state in PSYCHOLOGY_STATES}
    scores["冷漠"] = _mean([_inverse_scale(abs(r1) if r1 is not None else None, 0.0, 0.025), _inverse_scale(turnover_ratio, 0.8, 1.5), _inverse_scale(abs(r20) if r20 is not None else None, 0.0, 0.12)]) or 0
    scores["怀疑"] = _mean([_scale(r3, -0.03, 0.03), _inverse_scale(abs(r3) if r3 is not None else None, 0.03, 0.12), _inverse_scale(amount_ratio, 1.3, 2.5)]) or 0
    scores["试探"] = _mean([_scale(r1, 0.0, 0.03), _scale(amount_ratio, 1.0, 1.8), _scale(absorption, 45, 75), _scale(alpha, 45, 70)]) or 0
    scores["相信"] = _mean([_scale(r3, 0.02, 0.12), _scale(point.get("relative_sector_1d"), 0.0, 0.04), _scale(absorption, 55, 85), _inverse_scale(crowding, 70, 95)]) or 0
    scores["追逐"] = _mean([_scale(r1, 0.03, 0.09), _scale(acceleration, 0.01, 0.05), _scale(turnover_ratio, 1.4, 3.0), _scale(fomo, 55, 95)]) or 0
    scores["亢奋"] = _mean([_scale(r20, 0.25, 0.9), _scale(position, 0.75, 1.0), _scale(crowding, 65, 100), _scale(turnover_ratio, 1.8, 4.0)]) or 0
    scores["分歧"] = _mean([_scale(r1, 0.0, 0.06), _inverse_scale(point.get("efficiency_delta_1d"), -0.02, 0.02), _inverse_scale(point.get("alpha_density"), 40, 90), _scale(pressure, 50, 90)]) or 0
    scores["恐慌"] = _mean([_scale(-r1 if r1 is not None else None, 0.02, 0.08), _scale(-r3 if r3 is not None else None, 0.04, 0.18), _scale(sell, 55, 100), _scale(pressure, 60, 100)]) or 0
    scores["绝望"] = _mean([_scale(-r20 if r20 is not None else None, 0.2, 0.7), _inverse_scale(position, 0.1, 0.45), _scale(sell, 75, 100), _inverse_scale(location, 0.25, 0.65)]) or 0
    scores["修复"] = _mean([_scale(point.get("efficiency_delta_1d"), 0.0, 0.03), _scale(absorption, 60, 95), _inverse_scale(sell, 50, 90), _scale(point.get("features", {}).get("recovery_from_low"), 0.35, 1.0)]) or 0
    return {key: round(_clamp(value), 2) for key, value in scores.items()}


def _psychology(point: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    scores = _psychology_scores(point)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    current = ordered[0][0] if ordered else "怀疑"
    total = sum(max(value, 0.0) for _, value in ordered)
    probabilities = {
        state: round((max(scores.get(state, 0.0), 0.0) / total * 100) if total else 0.0, 1)
        for state in PSYCHOLOGY_STATES
    }
    previous_state = None
    if previous:
        previous_ordered = sorted(_psychology_scores(previous).items(), key=lambda item: item[1], reverse=True)
        previous_state = previous_ordered[0][0] if previous_ordered else None
    confidence = probabilities.get(current, 0.0)
    return {
        "psychology_state": current,
        "previous_state": previous_state,
        "transition": f"{previous_state} → {current}" if previous_state and previous_state != current else "状态保持" if previous_state else "首次可观测状态",
        "transition_probability": probabilities,
        "top_states": [{"state": state, "probability": probabilities.get(state, 0.0)} for state, _ in ordered[:4]],
        "state_confidence": confidence,
        "state_label": PSYCHOLOGY_LABELS.get(current, "按可观测价格、成交和承接判断"),
        "state_scores": scores,
        "transition_model": "observable-state classifier; transitions are not hard-coded",
    }


def _reflexivity(point: dict[str, Any], psychology: dict[str, Any], dynamics: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    r3 = point.get("r3")
    relative = point.get("relative_sector_1d")
    absorption = point.get("absorption_score")
    pressure = point.get("pressure_score")
    buy = point.get("forced_buy_pressure")
    sell = point.get("forced_sell_pressure")
    position = point.get("position120")
    crowding = point.get("crowding_score")
    alpha_density = point.get("alpha_density")
    sector_state = str(context.get("sector_state") or "")
    positive, positive_weight = _weighted([
        (_scale(r3, 0.01, 0.12), 0.22),
        (_scale(relative, 0.0, 0.05), 0.18),
        (_scale(absorption, 55, 90), 0.20),
        (_scale(point.get("efficiency_delta_1d"), 0.0, 0.03), 0.16),
        (_scale(point.get("sector_breadth"), 55, 90), 0.10),
        (_scale(alpha_density, 50, 90), 0.08),
        (_inverse_scale(crowding, 70, 100), 0.06),
    ])
    negative, negative_weight = _weighted([
        (_scale(-r3 if r3 is not None else None, 0.01, 0.15), 0.22),
        (_scale(sell, 55, 100), 0.20),
        (_scale(pressure, 55, 100), 0.18),
        (_scale(point.get("support_break_distance"), 0.5, 8), 0.14),
        (_inverse_scale(point.get("efficiency_delta_1d"), -0.03, 0.01), 0.12),
        (_inverse_scale(point.get("sector_breadth"), 35, 65), 0.08),
        (_scale(crowding, 70, 100), 0.06),
    ])
    decay, decay_weight = _weighted([
        (_scale(position, 0.75, 1.0), 0.18),
        (_scale(buy, 65, 100), 0.16),
        (_scale(crowding, 65, 100), 0.16),
        (_scale(point.get("amount_ratio_20"), 1.5, 3.5), 0.14),
        (_inverse_scale(point.get("efficiency_delta_1d"), -0.03, 0.02), 0.16),
        (_inverse_scale(alpha_density, 35, 75), 0.10),
        (_scale(dynamics.get("pressure_delta"), 3, 25), 0.10),
    ])
    panic_absorption = _weighted([
        (_scale(sell, 65, 100), 0.25),
        (_scale(point.get("panic_score"), 55, 100), 0.15),
        (_inverse_scale(point.get("efficiency_delta_1d"), -0.03, 0.01), 0.18),
        (_scale(dynamics.get("absorption_delta"), 5, 30), 0.18),
        (_inverse_scale(dynamics.get("pressure_delta"), -15, 10), 0.12),
        (_scale(point.get("recovery_from_low"), 0.35, 1.0), 0.12),
    ])[0]
    if decay is not None and decay >= 62:
        state = "POSITIVE_REFLEXIVITY_DECAY"
        score = decay
    elif negative is not None and negative >= 68 and (positive or 0) < 58:
        state = "NEGATIVE_REFLEXIVITY_ACCELERATION" if (dynamics.get("sell_delta") or 0) > 4 else "NEGATIVE_REFLEXIVITY"
        score = negative
    elif positive is not None and positive >= 62:
        state = "POSITIVE_REFLEXIVITY"
        score = positive
    elif (positive or 0) >= 48 and (point.get("alpha_score") or 0) >= 55 and (crowding or 0) < 72:
        state = "ALPHA_SEED_REFLEXIVITY"
        score = positive
    else:
        state = "NEUTRAL"
        score = max(item for item in (positive, negative, decay) if item is not None) if any(item is not None for item in (positive, negative, decay)) else None
    direction = "positive" if state in {"POSITIVE_REFLEXIVITY", "ALPHA_SEED_REFLEXIVITY"} else "negative" if state in {"NEGATIVE_REFLEXIVITY", "NEGATIVE_REFLEXIVITY_ACCELERATION"} else "decay" if state == "POSITIVE_REFLEXIVITY_DECAY" else "neutral"
    return {
        "reflexivity_state": state,
        "reflexivity_label": STAGE_LABELS[state],
        "reflexivity_direction": direction,
        "reflexivity_score": round(score, 1) if score is not None else None,
        "positive_score": round(positive, 1) if positive is not None else None,
        "negative_score": round(negative, 1) if negative is not None else None,
        "decay_score": round(decay, 1) if decay is not None else None,
        "panic_absorption_score": round(panic_absorption, 1) if panic_absorption is not None else None,
        "sector_state_input": sector_state or "未提供",
        "validation_conditions": [
            "价格变化需继续得到成交持续性和承接确认",
            "板块宽度与相对强度不应同步恶化",
            "关键成本区回踩不破，且效率不转负",
        ],
        "invalidation_conditions": [
            "放量跌破关键支撑或异常成交成本区",
            "承接快速下降、抛压连续上升",
            "价格仍强但Alpha密度和资金价格效率同步下降",
        ],
        "evidence": {
            "positive": ["相对强度", "承接变化", "资金价格效率", "板块宽度"],
            "negative": ["被迫卖出压力", "支撑破坏", "抛压变化"],
            "decay": ["高位拥挤", "成交放大", "效率边际下降", "Alpha扩散减弱"],
        },
    }


def _diagnosis_level(candidate_type: str, reflexivity_state: str, score: float | None) -> str:
    if candidate_type in {"HIGH_LEVEL_REFLEXIVITY_DECAY", "NEGATIVE_REFLEXIVITY_ACCELERATION"}:
        return "S6" if candidate_type == "NEGATIVE_REFLEXIVITY_ACCELERATION" else "S5"
    if reflexivity_state == "POSITIVE_REFLEXIVITY" and (score or 0) >= 75:
        return "S4"
    if candidate_type == "POSITIVE_REFLEXIVITY_CANDIDATE":
        return "S3"
    if candidate_type in {"PANIC_ABSORPTION_CANDIDATE", "ALPHA_SEED_REFLEXIVITY"}:
        return "S2"
    return "S1" if (score or 0) >= 45 else "S0"


def _candidate_type(point: dict[str, Any], reflexivity: dict[str, Any], dynamics: dict[str, Any]) -> str:
    if reflexivity.get("reflexivity_state") == "POSITIVE_REFLEXIVITY_DECAY":
        return "HIGH_LEVEL_REFLEXIVITY_DECAY"
    if reflexivity.get("reflexivity_state") in {"NEGATIVE_REFLEXIVITY", "NEGATIVE_REFLEXIVITY_ACCELERATION"}:
        return "NEGATIVE_REFLEXIVITY_ACCELERATION"
    if reflexivity.get("reflexivity_state") == "ALPHA_SEED_REFLEXIVITY":
        return "ALPHA_SEED_REFLEXIVITY"
    if (
        (point.get("forced_sell_pressure") or 0) >= 62
        and (reflexivity.get("panic_absorption_score") or 0) >= 58
        and (point.get("absorption_score") or 0) >= (point.get("pressure_score") or 0) - 5
        and (dynamics.get("absorption_delta") or 0) >= 0
        and (point.get("sell_force_price_response_weak") or False)
    ):
        return "PANIC_ABSORPTION_CANDIDATE"
    if (
        (point.get("alpha_score") or 0) >= 55
        and (point.get("relative_sector_1d") or 0) > 0
        and (point.get("absorption_score") or 0) >= (point.get("pressure_score") or 0)
        and (point.get("forced_buy_pressure") or 0) < 72
        and (point.get("position120") or 0.5) < 0.8
    ):
        return "ALPHA_SEED_REFLEXIVITY"
    if reflexivity.get("reflexivity_state") == "POSITIVE_REFLEXIVITY":
        return "POSITIVE_REFLEXIVITY_CANDIDATE"
    return "NO_CLEAR_CANDIDATE"


def _selection_score(point: dict[str, Any], liquidity: dict[str, Any], psychology: dict[str, Any], reflexivity: dict[str, Any], candidate_type: str, dynamics: dict[str, Any]) -> tuple[float | None, dict[str, Any]]:
    forced_structure = max(
        point.get("forced_buy_pressure") or 0,
        (point.get("forced_sell_pressure") or 0) * ((point.get("absorption_score") or 0) / 100),
    )
    asymmetry = liquidity.get("liquidity_asymmetry_score")
    liquidity_score = asymmetry if asymmetry is not None else None
    efficiency = point.get("capital_price_efficiency")
    if efficiency is not None:
        efficiency_score = _clamp(efficiency)
    else:
        efficiency_score = None
    absorption_score = None
    if point.get("absorption_score") is not None and point.get("pressure_score") is not None:
        absorption_score = _clamp(50 + (point["absorption_score"] - point["pressure_score"]) * 0.8)
    elif point.get("absorption_score") is not None:
        absorption_score = point["absorption_score"]
    state_scores = psychology.get("state_scores") or {}
    psychology_score = {
        "试探": 72, "相信": 78, "修复": 70, "冷漠": 42, "怀疑": 50,
        "追逐": 58, "亢奋": 38, "分歧": 48, "恐慌": 32, "绝望": 20,
    }.get(psychology.get("psychology_state"), 45)
    reflexivity_score = {
        "POSITIVE_REFLEXIVITY": reflexivity.get("positive_score"),
        "ALPHA_SEED_REFLEXIVITY": reflexivity.get("positive_score"),
        "PANIC_ABSORPTION_CANDIDATE": reflexivity.get("panic_absorption_score"),
        "HIGH_LEVEL_REFLEXIVITY_DECAY": reflexivity.get("decay_score"),
        "NEGATIVE_REFLEXIVITY_ACCELERATION": reflexivity.get("negative_score"),
    }.get(candidate_type, reflexivity.get("reflexivity_score"))
    parts = [
        (forced_structure, 0.15), (liquidity_score, 0.15),
        (efficiency_score, 0.20), (absorption_score, 0.20),
        (psychology_score, 0.10), (reflexivity_score, 0.20),
    ]
    score, total_weight = _weighted(parts)
    return (
        round(score, 1) if score is not None else None,
        {
            "weights": {"forced_trade": 0.15, "liquidity": 0.15, "capital_price_efficiency": 0.20, "absorption_pressure": 0.20, "psychology": 0.10, "reflexivity": 0.20},
            "components": {
                "forced_trade": _round(forced_structure, 1), "liquidity": _round(liquidity_score, 1),
                "capital_price_efficiency": _round(efficiency_score, 1), "absorption_pressure": _round(absorption_score, 1),
                "psychology": psychology_score, "reflexivity": _round(reflexivity_score, 1),
            },
            "coverage_pct": round(total_weight / 1.0 * 100, 1),
            "warning": "初始权重仅用于候选排序，需经Walk-Forward和样本外验证后校准。",
        },
    )


def _gate(
    reflexivity: dict[str, Any],
    auction: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = context or {}
    state = reflexivity.get("reflexivity_state")
    candidate = reflexivity.get("candidate_type")
    panic_market_states = {"恐慌", "绝望", "分歧", "退潮", "返势", "修复", "震荡"}
    market_state = str(context.get("market_state") or "")
    sector_state = str(context.get("sector_state") or "")
    panic_context_observed = (
        market_state in panic_market_states
        or sector_state in panic_market_states
        or (_num(context.get("panic_score")) or 0) >= 55
    )
    if state in {"POSITIVE_REFLEXIVITY_DECAY", "NEGATIVE_REFLEXIVITY", "NEGATIVE_REFLEXIVITY_ACCELERATION"}:
        status, label = "BLOCK", "行为反身性不许可追涨"
    elif candidate == "PANIC_ABSORPTION_CANDIDATE" and not panic_context_observed:
        status, label = "CAUTION", "恐慌吸收候选缺少市场/板块恐慌背景确认"
    elif candidate in OPPORTUNITY_CANDIDATES or state in {"POSITIVE_REFLEXIVITY", "ALPHA_SEED_REFLEXIVITY"}:
        status, label = "ALLOW", "行为反身性结构允许继续研究"
    else:
        status, label = "CAUTION", "行为反身性仍需确认"
    auction_gate = {"status": "WAIT", "label": "等待真实竞价/开盘数据"}
    if auction:
        gap = _num(auction.get("auction_gap"))
        ratio = _num(auction.get("auction_volume_ratio")) or _num(auction.get("auction_amount_ratio"))
        relative = _num(auction.get("auction_relative_sector"))
        if gap is not None and ratio is not None and (relative is None or relative >= -0.01):
            auction_gate = {"status": "CONFIRM", "label": "竞价相对强度通过"}
        else:
            auction_gate = {"status": "REJECT", "label": "竞价相对强度未通过"}
    return {
        "status": status, "label": label,
        "reason": "仅作为14:55研究过滤层；不连接券商下单",
        "allowed_states": ["POSITIVE_REFLEXIVITY", "ALPHA_SEED_REFLEXIVITY"],
        "auction_confirmation": auction_gate,
    }


def _skill_result(diagnosis: dict[str, Any]) -> dict[str, Any]:
    candidate = diagnosis.get("candidate_type")
    risk = candidate in {"HIGH_LEVEL_REFLEXIVITY_DECAY", "NEGATIVE_REFLEXIVITY_ACCELERATION"}
    detected = candidate != "NO_CLEAR_CANDIDATE"
    stage = candidate or diagnosis.get("reflexivity", {}).get("reflexivity_state") or "NEUTRAL"
    label = CANDIDATE_LABELS.get(stage) or STAGE_LABELS.get(stage) or "反身性结构"
    return {
        "skill_id": SKILL_ID,
        "skill_name": "行为反身性与资金博弈",
        "detected": detected,
        "stage": stage,
        "stage_label": label,
        "score": diagnosis.get("selection_score"),
        "confidence_pct": diagnosis.get("selection_score"),
        "signal_type": "RISK" if risk else "CANDIDATE" if detected else "NO_SIGNAL",
        "horizon": "1-3日",
        "data_level": "DAILY_PLUS_AUCTION" if diagnosis.get("auction_observed") else "DAILY",
        "evidence": diagnosis.get("evidence") or [],
        "invalidation_conditions": (diagnosis.get("reflexivity") or {}).get("invalidation_conditions") or [],
        "next_confirmation": (diagnosis.get("reflexivity") or {}).get("validation_conditions") or [],
        "missing_factors": diagnosis.get("missing_factors") or [],
        "direct_order": False,
        "language_boundary": "只输出候选、风险与验证条件，不构成买卖指令；不推断参与者身份和意图。",
    }


def build_reflexivity_diagnosis(
    bars: list[dict[str, Any]],
    *,
    as_of: date | None = None,
    context: dict[str, Any] | None = None,
    auction: dict[str, Any] | None = None,
    minute_bars: list[dict[str, Any]] | None = None,
    symbol: str | None = None,
    name: str | None = None,
    horizon: str = "3d",
) -> dict[str, Any]:
    """Return a six-dimension diagnosis using only bars at or before ``as_of``."""
    context = context or {}
    rows = normalize_daily_bars(bars, as_of=as_of)
    data_date = rows[-1]["trade_date"] if rows else None
    if not rows:
        diagnosis = {
            "available": False, "symbol": symbol, "name": name, "candidate_type": "NO_CLEAR_CANDIDATE",
            "candidate_label": "暂无明确候选", "selection_score": None, "diagnosis_level": "S0",
            "missing_factors": ["OHLCV日线"], "data_cutoff_time": None,
            "model_version": MODEL_VERSION, "skill_version": SKILL_VERSION,
            "horizon": horizon,
            "data_quality": {"coverage_pct": 0, "warnings": ["没有可核验的日线观察"]},
        }
        diagnosis["skill_result"] = _skill_result(diagnosis)
        return diagnosis

    # The historical context map is optional.  It lets a backtest provide
    # point-in-time sector observations without forcing current context into
    # earlier psychology states.
    history_context = context.get("history_by_date") or {}
    points: list[dict[str, Any]] = []
    for offset in range(4, -1, -1):
        end = len(rows) - offset
        if end < 21:
            continue
        point_rows = rows[:end]
        point_day = _date(point_rows[-1].get("trade_date"))
        point_context = history_context.get(point_day.isoformat(), {}) if point_day else {}
        if offset == 0:
            point_context = {**context, **point_context}
        points.append(_point_snapshot(point_rows, point_context, auction if offset == 0 else None))
    current = points[-1] if points else _point_snapshot(rows, context, auction)
    previous = points[-2] if len(points) >= 2 else None
    older = points[-3] if len(points) >= 3 else None
    dynamics = {
        "absorption_delta": (current.get("absorption_score") - previous.get("absorption_score")) if previous and current.get("absorption_score") is not None and previous.get("absorption_score") is not None else None,
        "pressure_delta": (current.get("pressure_score") - previous.get("pressure_score")) if previous and current.get("pressure_score") is not None and previous.get("pressure_score") is not None else None,
        "buy_pressure_delta": (current.get("forced_buy_pressure") - previous.get("forced_buy_pressure")) if previous and current.get("forced_buy_pressure") is not None and previous.get("forced_buy_pressure") is not None else None,
        "sell_pressure_delta": (current.get("forced_sell_pressure") - previous.get("forced_sell_pressure")) if previous and current.get("forced_sell_pressure") is not None and previous.get("forced_sell_pressure") is not None else None,
        "sell_delta": (current.get("forced_sell_pressure") - previous.get("forced_sell_pressure")) if previous and current.get("forced_sell_pressure") is not None and previous.get("forced_sell_pressure") is not None else None,
        "efficiency_delta": (current.get("capital_price_efficiency") - previous.get("capital_price_efficiency")) if previous and current.get("capital_price_efficiency") is not None and previous.get("capital_price_efficiency") is not None else None,
        "absorption_acceleration": (
            (current.get("absorption_score") - previous.get("absorption_score"))
            - (previous.get("absorption_score") - older.get("absorption_score"))
            if older and previous and current.get("absorption_score") is not None
            and previous.get("absorption_score") is not None and older.get("absorption_score") is not None else None
        ),
        "pressure_acceleration": (
            (current.get("pressure_score") - previous.get("pressure_score"))
            - (previous.get("pressure_score") - older.get("pressure_score"))
            if older and previous and current.get("pressure_score") is not None
            and previous.get("pressure_score") is not None and older.get("pressure_score") is not None else None
        ),
        "absorption_trend": _trend((current.get("absorption_score") - previous.get("absorption_score")) if previous and current.get("absorption_score") is not None and previous.get("absorption_score") is not None else None),
        "pressure_trend": _trend((current.get("pressure_score") - previous.get("pressure_score")) if previous and current.get("pressure_score") is not None and previous.get("pressure_score") is not None else None),
        "buy_pressure_trend": _trend((current.get("forced_buy_pressure") - previous.get("forced_buy_pressure")) if previous and current.get("forced_buy_pressure") is not None and previous.get("forced_buy_pressure") is not None else None),
        "sell_pressure_trend": _trend((current.get("forced_sell_pressure") - previous.get("forced_sell_pressure")) if previous and current.get("forced_sell_pressure") is not None and previous.get("forced_sell_pressure") is not None else None),
    }
    psychology = _psychology(current, previous)
    reflexivity = _reflexivity(current, psychology, dynamics, context)
    candidate = _candidate_type(current, reflexivity, dynamics)
    reflexivity["candidate_type"] = candidate
    reflexivity["candidate_label"] = CANDIDATE_LABELS.get(candidate, candidate)
    liquidity = _liquidity_map(rows, current)
    selection_score, score_breakdown = _selection_score(current, liquidity, psychology, reflexivity, candidate, dynamics)
    level = _diagnosis_level(candidate, reflexivity.get("reflexivity_state", "NEUTRAL"), selection_score)
    missing: list[str] = []
    required = {
        "20日以上日线": len(rows) >= 21,
        "成交额": current.get("amount_ratio_20") is not None,
        "OHLC范围": (current.get("features") or {}).get("close_location") is not None,
        "换手率": current.get("turnover_ratio_20") is not None,
    }
    missing.extend(key for key, present in required.items() if not present)
    if current.get("absorption_score") is None or current.get("pressure_score") is None:
        missing.append("承接/抛压影线结构")
    if current.get("relative_sector_1d") is None:
        missing.append("板块相对收益")
    if current.get("alpha_score") is None:
        missing.append("个股Alpha评分")
    if current.get("crowding_score") is None:
        missing.append("拥挤度代理")
    if not liquidity.get("available"):
        missing.append("可观测流动性区域")
    warnings = [
        "被迫交易压力是价格/成交/位置的可观测代理，不代表已识别交易者身份。",
        "SHORT_COVER因缺少完整公开空头序列已关闭，不进入压力分数。",
        "未提供L2时不推断盘口队列、撤单或主动买卖方向。",
        "短期心理和反身性只作为1-3日状态修正，不外推为季度方向。",
    ]
    if minute_bars:
        features = current.get("features") or {}
        current["intraday"] = {key: features.get(key) for key in ("ret_5m", "ret_15m", "vwap_15m", "vwap_reclaim", "vwap_hold_minutes", "minute_bar_count")}
    gate = _gate({**reflexivity, "candidate_type": candidate}, auction, context)
    evidence = [
        {"dimension": "forced_trade", "statement": f"潜在被迫买盘{_round(current.get('forced_buy_pressure'), 1)}，潜在被迫卖盘{_round(current.get('forced_sell_pressure'), 1)}", "source": "日线价格/成交/位置代理"},
        {"dimension": "liquidity", "statement": liquidity.get("interpretation"), "source": "历史高低点、成交分布和可观测VWAP"},
        {"dimension": "efficiency", "statement": "买力强但涨形弱" if current.get("buy_force_price_response_weak") else "卖力强但跌形弱" if current.get("sell_force_price_response_weak") else f"资金价格效率{_trend(current.get('efficiency_delta_1d'))}", "source": "收益/成交额效率序列"},
        {"dimension": "absorption_pressure", "statement": f"承接{_round(current.get('absorption_score'), 1)}（{dynamics.get('absorption_trend')}），抛压{_round(current.get('pressure_score'), 1)}（{dynamics.get('pressure_trend')}）", "source": "Skill 02同口径影线与恢复结构"},
        {"dimension": "psychology", "statement": f"心理阶段{psychology.get('transition')}", "source": "可观测状态分类器"},
        {"dimension": "reflexivity", "statement": reflexivity.get("reflexivity_label"), "source": "价格、Alpha、承接、效率和拥挤联合"},
    ]
    diagnosis = {
        "available": len(rows) >= 21,
        "symbol": symbol,
        "name": name,
        "data_date": data_date.isoformat() if data_date else None,
        "data_cutoff_time": current.get("features", {}).get("available_time") or (data_date.isoformat() if data_date else None),
        "model_version": MODEL_VERSION,
        "skill_version": SKILL_VERSION,
        "horizon": horizon,
        "horizon_policy": {
            "1d": "被迫交易、流动性、承接/抛压和心理权重较高",
            "3d": "六维均衡，优先观察反身性是否持续",
            "1w": "提高Alpha持续性、板块宽度和效率变化的验证要求",
            "1m": "短期心理仅作修正，需补充产业、盈利和估值数据，不外推短线情绪",
        }.get(horizon, "短期状态仅作研究参考"),
        "candidate_type": candidate,
        "candidate_label": CANDIDATE_LABELS.get(candidate, candidate),
        "selection_score": selection_score,
        "score_breakdown": score_breakdown,
        "diagnosis_level": level,
        "market_state": context.get("market_state") or "未提供",
        "sector_state": context.get("sector_state") or "未提供",
        "forced_trading": {
            "forced_buy_pressure": _round(current.get("forced_buy_pressure"), 1),
            "forced_sell_pressure": _round(current.get("forced_sell_pressure"), 1),
            "buy_pressure_trend": dynamics.get("buy_pressure_trend"),
            "sell_pressure_trend": dynamics.get("sell_pressure_trend"),
            "profit_taking_pressure": _round(current.get("profit_taking_pressure"), 1),
            "fomo_buy": _round(current.get("forced_buy_pressure"), 1),
            "short_cover": current.get("short_cover_pressure"),
            "stop_loss_sell": _round(current.get("forced_sell_pressure"), 1),
            "panic_sell": _round(current.get("forced_sell_pressure"), 1),
            "profit_taking": _round(current.get("profit_taking_pressure"), 1),
            "coverage_pct": round((current.get("buy_pressure_coverage", 0) + current.get("sell_pressure_coverage", 0)) / 2, 1),
        },
        "liquidity_map": liquidity,
        "capital_price_efficiency": {
            "score": _round(current.get("capital_price_efficiency"), 1),
            "raw_efficiency": _round(current.get("raw_efficiency"), 6),
            "up_efficiency": _round(current.get("raw_efficiency"), 6) if (current.get("raw_efficiency") or 0) > 0 else None,
            "down_efficiency": _round(abs(current.get("raw_efficiency")), 6) if (current.get("raw_efficiency") or 0) < 0 else None,
            "efficiency_delta_1d": _round(current.get("efficiency_delta_1d"), 6),
            "efficiency_delta_3d": _round(current.get("efficiency_delta_3d"), 6),
            "efficiency_acceleration": _round(current.get("efficiency_acceleration"), 6),
            "state": "买力强但涨形弱" if current.get("buy_force_price_response_weak") else "卖力强但跌形弱" if current.get("sell_force_price_response_weak") else _trend(current.get("efficiency_delta_1d")),
            "buy_force_price_response_weak": current.get("buy_force_price_response_weak"),
            "sell_force_price_response_weak": current.get("sell_force_price_response_weak"),
        },
        "absorption_pressure": {
            "absorption_score": _round(current.get("absorption_score"), 1),
            "pressure_score": _round(current.get("pressure_score"), 1),
            "absorption_delta": _round(dynamics.get("absorption_delta"), 1),
            "pressure_delta": _round(dynamics.get("pressure_delta"), 1),
            "absorption_acceleration": _round(dynamics.get("absorption_acceleration"), 1),
            "pressure_acceleration": _round(dynamics.get("pressure_acceleration"), 1),
            "absorption_trend": dynamics.get("absorption_trend"),
            "pressure_trend": dynamics.get("pressure_trend"),
            "stage": current.get("absorption_stage"),
        },
        "psychology": psychology,
        "reflexivity": reflexivity,
        "gate": gate,
        "alpha": {
            "score": _round(current.get("alpha_score"), 1),
            "density": _round(current.get("alpha_density"), 1),
            "relative_1d": _round(current.get("relative_sector_1d"), 6),
            "stage": "A3" if (current.get("alpha_score") or 0) >= 75 else "A2" if (current.get("alpha_score") or 0) >= 55 else "A1" if current.get("alpha_score") is not None else None,
        },
        "crowding": {"score": _round(current.get("crowding_score"), 1), "state": "高" if (current.get("crowding_score") or 0) >= 75 else "中" if current.get("crowding_score") is not None else "未提供"},
        "evidence": evidence,
        "validation_conditions": reflexivity.get("validation_conditions") or [],
        "invalidation_conditions": reflexivity.get("invalidation_conditions") or [],
        "missing_factors": sorted(set(missing)),
        "data_quality": {
            "coverage_pct": round(max(0, min(100, 100 - len(set(missing)) / max(1, len(required) + 5) * 100)), 1),
            "history_sessions": len(rows),
            "daily_available": True,
            "minute_available": bool(minute_bars),
            "auction_available": bool(auction),
            "l2_available": False,
            "warnings": warnings,
        },
        "audit": {
            "no_future_data": True,
            "available_time_rule": "仅使用trade_date/available_time不晚于data_cutoff_time的观察",
            "short_cover_policy": "disabled_without_verified_short_series",
            "l2_policy": "not_inferred",
        },
    }
    # Flatten the contract's headline fields as well as retaining the nested
    # sections.  This keeps API consumers simple and preserves stable names
    # from the design document for future clients.
    diagnosis.update({
        "forced_buy_pressure": diagnosis["forced_trading"].get("forced_buy_pressure"),
        "forced_sell_pressure": diagnosis["forced_trading"].get("forced_sell_pressure"),
        "nearest_up_liquidity_zone": liquidity.get("nearest_up_liquidity_zone"),
        "nearest_down_liquidity_zone": liquidity.get("nearest_down_liquidity_zone"),
        "distance_to_up_liquidity": liquidity.get("distance_to_up_liquidity"),
        "distance_to_down_liquidity": liquidity.get("distance_to_down_liquidity"),
        "liquidity_asymmetry_score": liquidity.get("liquidity_asymmetry_score"),
        "capital_price_efficiency_score": diagnosis["capital_price_efficiency"].get("score"),
        "efficiency_delta_1d": diagnosis["capital_price_efficiency"].get("efficiency_delta_1d"),
        "efficiency_delta_3d": diagnosis["capital_price_efficiency"].get("efficiency_delta_3d"),
        "efficiency_acceleration": diagnosis["capital_price_efficiency"].get("efficiency_acceleration"),
        "absorption_score": diagnosis["absorption_pressure"].get("absorption_score"),
        "pressure_score": diagnosis["absorption_pressure"].get("pressure_score"),
        "absorption_delta": diagnosis["absorption_pressure"].get("absorption_delta"),
        "pressure_delta": diagnosis["absorption_pressure"].get("pressure_delta"),
        "psychology_state": psychology.get("psychology_state"),
        "previous_psychology_state": psychology.get("previous_state"),
        "psychology_transition": psychology.get("transition"),
        "transition_probability": psychology.get("transition_probability"),
        "state_confidence": psychology.get("state_confidence"),
        "reflexivity_state": reflexivity.get("reflexivity_state"),
        "reflexivity_score": reflexivity.get("reflexivity_score"),
        "crowding_score": (diagnosis.get("crowding") or {}).get("score"),
        "reflexivity_selection_score": selection_score,
    })
    diagnosis["skill_result"] = _skill_result(diagnosis)
    return diagnosis


def basic_reflexivity_result(features: dict[str, Any]) -> dict[str, Any]:
    """Adapter for the nine-skill calculator/validation interface.

    Live scans attach a full diagnosis before calling ``evaluate_all_skills``.
    Historical validation may only provide the existing feature vector, so it
    receives a deliberately smaller, still auditable result instead of a
    fabricated liquidity or participant signal.
    """
    attached = features.get("reflexivity_diagnosis")
    if isinstance(attached, dict) and isinstance(attached.get("skill_result"), dict):
        return attached["skill_result"]
    r1 = _num(features.get("r_1d"))
    alpha = _num(features.get("relative_sector_1d"))
    absorption = _num(features.get("close_location"))
    pressure = _num(features.get("upper_wick_ratio"))
    amount = _num(features.get("amount_ratio_20"))
    if r1 is None or amount is None:
        return {
            "skill_id": SKILL_ID, "skill_name": "行为反身性与资金博弈", "detected": False,
            "stage": "INSUFFICIENT_DATA", "stage_label": "行为反身性数据不足", "score": None,
            "confidence_pct": None, "signal_type": "INSUFFICIENT_DATA", "horizon": "1-3日",
            "data_level": "DAILY", "evidence": [], "invalidation_conditions": [],
            "next_confirmation": [], "missing_factors": ["r_1d", "amount_ratio_20"],
            "direct_order": False, "language_boundary": "不构成买卖指令。",
        }
    score = _clamp(50 + (r1 / 0.08) * 25 + ((alpha or 0) / 0.05) * 15 + ((absorption or 0.5) - (pressure or 0.2)) * 20)
    stage = "POSITIVE_REFLEXIVITY" if score >= 65 and r1 > 0 else "NEGATIVE_REFLEXIVITY" if score <= 35 and r1 < 0 else "NEUTRAL"
    candidate = "POSITIVE_REFLEXIVITY_CANDIDATE" if stage == "POSITIVE_REFLEXIVITY" else "NEGATIVE_REFLEXIVITY_ACCELERATION" if stage == "NEGATIVE_REFLEXIVITY" else "NO_CLEAR_CANDIDATE"
    return {
        "skill_id": SKILL_ID, "skill_name": "行为反身性与资金博弈", "detected": candidate != "NO_CLEAR_CANDIDATE",
        "stage": candidate, "stage_label": CANDIDATE_LABELS.get(candidate, STAGE_LABELS.get(stage, stage)),
        "score": round(score, 1), "confidence_pct": round(score, 1),
        "signal_type": "RISK" if stage == "NEGATIVE_REFLEXIVITY" else "CANDIDATE" if stage == "POSITIVE_REFLEXIVITY" else "NO_SIGNAL",
        "horizon": "1-3日", "data_level": "DAILY", "evidence": [{"label": "日线反身性代理", "value": round(score, 1), "condition": "历史验证适配器"}],
        "invalidation_conditions": ["价格效率或板块相对强度反向变化"], "next_confirmation": ["需要完整流动性和承接序列复核"],
        "missing_factors": ["full_liquidity_map", "psychology_transition", "verified_short_series"],
        "direct_order": False, "language_boundary": "只输出候选、风险与验证条件，不构成买卖指令。",
    }
