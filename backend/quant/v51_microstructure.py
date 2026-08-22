"""Deterministic V5.1 microstructure and market-behaviour calculations.

The module only accepts observed rows and returns auditable features.  It does
not turn a named candlestick pattern or a single quote into a trade signal.
Every public calculator includes a coverage/status object so callers can
distinguish an observed result from an unavailable model.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime, time
from typing import Any, Iterable


MODEL_VERSION = "v5.1-deterministic-1"
AUCTION_MODEL_VERSION = "v5.1-auction-rule-1"
ENGINE_VERSION = "v5.1-engine-contract-1"

AUCTION_PHASES = {
    "PRE_CANCEL": "09:15-09:20 可挂可撤",
    "LOCKED": "09:20-09:25 可挂不可撤",
    "POST_CALL": "09:25 后开盘验证",
}


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: Any) -> int | None:
    parsed = _number(value)
    return int(parsed) if parsed is not None else None


def _clamp(value: float | None, low: float = 0.0, high: float = 100.0) -> float | None:
    if value is None:
        return None
    return round(max(low, min(high, value)), 2)


def _mean(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return statistics.fmean(clean) if clean else None


def _std(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if len(clean) < 2:
        return None
    return statistics.pstdev(clean)


def _date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _minutes(moment: datetime | None) -> int | None:
    return moment.hour * 60 + moment.minute if moment else None


def _return(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1) * 100


def _bar_value(row: Any, key: str, *aliases: str) -> float | None:
    if isinstance(row, dict):
        for name in (key, *aliases):
            value = _number(row.get(name))
            if value is not None:
                return value
        return None
    for name in (key, *aliases):
        value = _number(getattr(row, name, None))
        if value is not None:
            return value
    return None


def _bar_date(row: Any) -> date | None:
    if isinstance(row, dict):
        return _date(row.get("trade_date") or row.get("date") or row.get("bar_time"))
    return _date(getattr(row, "trade_date", None) or getattr(row, "bar_time", None))


def _bar_time(row: Any) -> datetime | None:
    if isinstance(row, dict):
        return _datetime(row.get("bar_time") or row.get("timestamp") or row.get("time"))
    return _datetime(getattr(row, "bar_time", None))


def _quality(
    *,
    status: str,
    available: Iterable[str],
    expected: Iterable[str],
    source: str | None,
    cutoff: Any,
    model_version: str = MODEL_VERSION,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    available_set = {item for item in available if item}
    expected_set = {item for item in expected if item}
    missing = sorted(expected_set - available_set)
    coverage = round(len(available_set & expected_set) / len(expected_set) * 100, 1) if expected_set else 0.0
    result = {
        "status": status,
        "coverage_pct": coverage,
        "available_fields": sorted(available_set),
        "missing_fields": missing,
        "source": source or "unavailable",
        "data_cutoff_time": cutoff.isoformat() if isinstance(cutoff, (date, datetime)) else cutoff,
        "model_version": model_version,
    }
    if extra:
        result.update(extra)
    return result


def normalize_auction_snapshots(
    rows: list[dict[str, Any]],
    *,
    previous_close: float | None = None,
    data_cutoff_time: Any = None,
) -> dict[str, Any]:
    """Normalize available auction observations without filling absent fields."""
    normalized: list[dict[str, Any]] = []
    for raw in rows or []:
        observed_at = _datetime(raw.get("snapshot_time") or raw.get("quote_at") or raw.get("data_cutoff_time"))
        if observed_at is None:
            continue
        minute = _minutes(observed_at)
        phase = "PRE_CANCEL" if minute is not None and minute < 9 * 60 + 20 else "LOCKED" if minute is not None and minute <= 9 * 60 + 25 else "POST_CALL"
        price = _number(raw.get("indicative_price") or raw.get("auction_price") or raw.get("price"))
        prior = _number(raw.get("previous_close")) or previous_close
        matched_volume = _integer(raw.get("matched_volume") or raw.get("auction_volume"))
        matched_amount = _number(raw.get("matched_amount") or raw.get("auction_amount"))
        buy_volume = _integer(raw.get("unmatched_buy_volume"))
        sell_volume = _integer(raw.get("unmatched_sell_volume"))
        buy_amount = _number(raw.get("unmatched_buy_amount"))
        sell_amount = _number(raw.get("unmatched_sell_amount"))
        imbalance_base = (buy_volume or 0) + (sell_volume or 0)
        imbalance = ((buy_volume - sell_volume) / imbalance_base) if imbalance_base and buy_volume is not None and sell_volume is not None else None
        normalized.append({
            "snapshot_time": observed_at.isoformat(),
            "phase": phase,
            "phase_label": AUCTION_PHASES[phase],
            "indicative_price": price,
            "indicative_return": _return(price, prior),
            "matched_volume": matched_volume,
            "matched_amount": matched_amount,
            "unmatched_buy_volume": buy_volume,
            "unmatched_sell_volume": sell_volume,
            "unmatched_buy_amount": buy_amount,
            "unmatched_sell_amount": sell_amount,
            "imbalance_ratio": round(imbalance, 4) if imbalance is not None else None,
            "activity_count": _integer(raw.get("activity_count")),
            "source": str(raw.get("source") or "unavailable"),
        })
    normalized.sort(key=lambda item: item["snapshot_time"])
    available_fields: set[str] = set()
    for item in normalized:
        for field in ("indicative_price", "matched_volume", "matched_amount", "unmatched_buy_volume", "unmatched_sell_volume"):
            if item.get(field) is not None:
                available_fields.add(field)
    phases = {item["phase"] for item in normalized}
    latest = normalized[-1] if normalized else {}
    first = normalized[0] if normalized else {}
    transition = "UNKNOWN"
    if len(normalized) >= 2:
        first_return = first.get("indicative_return")
        last_return = latest.get("indicative_return")
        first_imbalance = first.get("imbalance_ratio")
        last_imbalance = latest.get("imbalance_ratio")
        if first_return is not None and last_return is not None and first_imbalance is not None and last_imbalance is not None:
            if last_return < first_return - 0.4 and last_imbalance < first_imbalance - 0.15:
                transition = "STRONG_TO_WEAK"
            elif last_return > first_return + 0.4 and last_imbalance > first_imbalance + 0.15:
                transition = "WEAK_TO_STRONG"
            else:
                transition = "STABLE_OR_MIXED"
    if not normalized:
        status = "AUCTION_DATA_UNAVAILABLE"
    elif len(normalized) == 1:
        status = "LIMITED_SINGLE_SNAPSHOT"
    elif {"PRE_CANCEL", "LOCKED"}.issubset(phases):
        status = "OBSERVED_TIME_SERIES"
    else:
        status = "PARTIAL_TIME_SERIES"
    expected = (
        "indicative_price", "matched_volume", "matched_amount", "unmatched_buy_volume", "unmatched_sell_volume",
    )
    source = "+".join(sorted({item["source"] for item in normalized if item.get("source") != "unavailable"})) or "unavailable"
    quality = _quality(
        status=status,
        available=available_fields,
        expected=expected,
        source=source,
        cutoff=data_cutoff_time,
        model_version=AUCTION_MODEL_VERSION,
        extra={
            "model_enabled": status == "OBSERVED_TIME_SERIES",
            "time_slice_count": len(normalized),
            "phase_coverage": sorted(phases),
            "warning": "历史竞价快照不足时不进行竞价回测或趋势外推。" if status != "OBSERVED_TIME_SERIES" else None,
        },
    )
    # A visible single snapshot is useful for observation, but it is not a
    # multi-point forecast and therefore intentionally carries no pass/fail.
    return {
        "features": normalized,
        "latest": latest,
        "transition": transition,
        "auction_state": "WAIT_FOR_CONFIRMATION" if status != "OBSERVED_TIME_SERIES" else transition,
        "quality": quality,
        "model_version": AUCTION_MODEL_VERSION,
    }


def expectation_deviation(
    auction: dict[str, Any],
    minute_bars: list[dict[str, Any]],
    *,
    previous_close: float | None = None,
    sector_return: float | None = None,
    market_return: float | None = None,
    data_cutoff_time: Any = None,
) -> dict[str, Any]:
    """Compare a 09:25 observable expectation with later intraday evidence."""
    latest = auction.get("latest") or {}
    auction_return = _number(latest.get("indicative_return"))
    if auction_return is None and latest.get("indicative_price") is not None:
        auction_return = _return(_number(latest.get("indicative_price")), previous_close)
    expected_score = _clamp(50 + (auction_return or 0) * 8)
    bars = []
    for raw in minute_bars or []:
        moment = _bar_time(raw)
        close = _bar_value(raw, "close_price", "close")
        if moment and close is not None:
            bars.append((moment, close, raw))
    bars.sort(key=lambda item: item[0])
    windows: dict[str, dict[str, Any]] = {}
    for label, horizon_minutes in (("5m", 5), ("15m", 15), ("30m", 30)):
        selected = None
        for moment, close, raw in bars:
            # Minute data may begin at 09:30; use the earliest observed bar at
            # or after the window, never interpolate a missing price.
            # 09:25 is 9*60+25 minutes after midnight.  Using 9*25 here
            # silently selected bars around 03:50 and made every expectation
            # window appear to have no confirmation during normal trading.
            if moment.hour * 60 + moment.minute >= 9 * 60 + 25 + horizon_minutes:
                selected = (moment, close, raw)
                break
        actual_return = _return(selected[1], previous_close) if selected and previous_close else None
        actual_score = _clamp(50 + (actual_return or 0) * 8) if actual_return is not None else None
        deviation = actual_score - expected_score if actual_score is not None else None
        if deviation is None:
            classification = "NO_CONFIRMATION"
        elif deviation >= 8:
            classification = "POSITIVE_SURPRISE"
        elif deviation <= -8:
            classification = "NEGATIVE_SURPRISE"
        else:
            classification = "ALIGNED"
        windows[label] = {
            "actual_return_pct": round(actual_return, 3) if actual_return is not None else None,
            "actual_score": round(actual_score, 2) if actual_score is not None else None,
            "deviation": round(deviation, 2) if deviation is not None else None,
            "classification": classification,
            "observed_at": selected[0].isoformat() if selected else None,
            "bar_available": selected is not None,
        }
    available = {"auction_expected_score"}
    available.update(f"actual_{key}_score" for key, item in windows.items() if item["actual_score"] is not None)
    status = "OBSERVED" if any(item["actual_score"] is not None for item in windows.values()) else "NO_INTRADAY_CONFIRMATION"
    quality = _quality(
        status=status,
        available=available,
        expected={"auction_expected_score", "actual_5m_score", "actual_15m_score", "actual_30m_score"},
        source="StockMinuteBar" if bars else "unavailable",
        cutoff=data_cutoff_time,
        extra={"no_forward_fill": True},
    )
    return {
        "auction_expected_state": "偏强" if expected_score >= 58 else "偏弱" if expected_score <= 42 else "中性",
        "auction_expected_score": round(expected_score, 2),
        "windows": windows,
        "sector_return_pct": sector_return,
        "market_return_pct": market_return,
        "quality": quality,
        "model_version": MODEL_VERSION,
    }


def disagreement_features(
    bars: list[Any],
    *,
    sector_return: float | None = None,
    market_return: float | None = None,
    data_cutoff_time: Any = None,
) -> dict[str, Any]:
    """Measure divergence and subsequent absorption from observed bars."""
    ordered = sorted([row for row in bars or [] if _bar_date(row)], key=_bar_date)
    recent = ordered[-10:]
    closes = [_bar_value(row, "close_price", "close") for row in recent]
    changes = [_bar_value(row, "change_pct") for row in recent]
    amounts = [_bar_value(row, "amount") for row in recent]
    valid_changes = [value for value in changes if value is not None]
    latest_change = valid_changes[-1] if valid_changes else None
    prior_changes = valid_changes[:-1]
    average_prior = _mean(prior_changes[-5:])
    amount_latest = amounts[-1] if amounts else None
    amount_prior = _mean(amounts[:-1][-5:]) if len(amounts) > 1 else None
    price_direction = "up" if (latest_change or 0) > 0.2 else "down" if (latest_change or 0) < -0.2 else "flat"
    divergence = None
    if sector_return is not None and latest_change is not None:
        divergence = latest_change - sector_return
    elif market_return is not None and latest_change is not None:
        divergence = latest_change - market_return
    disagreement_score = _clamp(abs(divergence or 0) * 12 + (10 if amount_prior and amount_latest and amount_latest > amount_prior * 1.3 else 0))
    contraction = bool(amount_prior and amount_latest and amount_latest < amount_prior * 0.85)
    retained = bool(closes and len(closes) >= 3 and closes[-1] >= min(item for item in closes[-3:] if item is not None)) if any(item is not None for item in closes) else False
    if disagreement_score is None:
        state = "NO_DATA"
    elif disagreement_score >= 65 and contraction and retained:
        state = "ABSORBED"
    elif disagreement_score >= 45:
        state = "ACTIVE_DISAGREEMENT"
    elif disagreement_score >= 25:
        state = "MILD_DISAGREEMENT"
    else:
        state = "ALIGNED"
    quality = _quality(
        status="OBSERVED" if len(ordered) >= 3 else "INSUFFICIENT_HISTORY",
        available={"latest_change", "amount_latest", "divergence"} & {"latest_change" if latest_change is not None else "", "amount_latest" if amount_latest is not None else "", "divergence" if divergence is not None else ""},
        expected={"latest_change", "amount_latest", "divergence"},
        source="StockDailyBar" if ordered else "unavailable",
        cutoff=data_cutoff_time,
        extra={"sessions": len(ordered)},
    )
    return {
        "state": state,
        "disagreement_score": disagreement_score,
        "absorption_score": _clamp((30 if contraction else 0) + (30 if retained else 0) + (40 if state == "ABSORBED" else 0)),
        "price_direction": price_direction,
        "divergence_pct": round(divergence, 3) if divergence is not None else None,
        "amount_contraction": contraction,
        "support_retention": retained,
        "quality": quality,
        "model_version": MODEL_VERSION,
    }


def supply_test_features(bars: list[Any], *, data_cutoff_time: Any = None) -> dict[str, Any]:
    """Detect an observable supply test; never infer participant intent."""
    ordered = sorted([row for row in bars or [] if _bar_date(row)], key=_bar_date)
    if len(ordered) < 21:
        return {
            "state": "INSUFFICIENT_HISTORY",
            "quality": _quality(status="INSUFFICIENT_HISTORY", available=set(), expected={"shock", "upper_rejection", "amount_zscore"}, source="StockDailyBar" if ordered else "unavailable", cutoff=data_cutoff_time, extra={"sessions": len(ordered)}),
            "model_version": MODEL_VERSION,
        }
    latest = ordered[-1]
    close = _bar_value(latest, "close_price", "close")
    high = _bar_value(latest, "high_price", "high")
    low = _bar_value(latest, "low_price", "low")
    open_price = _bar_value(latest, "open_price", "open")
    amount = _bar_value(latest, "amount")
    historical_amounts = [_bar_value(row, "amount") for row in ordered[-61:-1]]
    baseline = _mean(historical_amounts)
    deviation = _std(historical_amounts)
    amount_z = (amount - baseline) / deviation if amount is not None and baseline is not None and deviation not in (None, 0) else None
    spread = high - low if high is not None and low is not None else None
    upper_wick = high - max(open_price, close) if high is not None and open_price is not None and close is not None else None
    upper_ratio = upper_wick / spread if upper_wick is not None and spread not in (None, 0) else None
    resistance = max((_bar_value(row, "high_price", "high") for row in ordered[-21:-1]), default=None)
    shock = bool(amount_z is not None and amount_z >= 2.0)
    broke = bool(close is not None and resistance is not None and close > resistance)
    rejected = bool(upper_ratio is not None and upper_ratio >= 0.45)
    if shock and broke and not rejected:
        state = "BREAKOUT_CONFIRMED"
    elif shock and rejected:
        state = "SUPPLY_NOT_REDUCED"
    elif shock:
        state = "SUPPLY_TESTED"
    else:
        state = "NO_RECENT_TEST"
    quality = _quality(
        status="OBSERVED",
        available={"shock" if amount_z is not None else "", "upper_rejection" if upper_ratio is not None else "", "amount_zscore" if amount_z is not None else ""},
        expected={"shock", "upper_rejection", "amount_zscore"},
        source="StockDailyBar",
        cutoff=data_cutoff_time,
        extra={"sessions": len(ordered)},
    )
    return {
        "state": state,
        "test_date": _bar_date(latest).isoformat() if _bar_date(latest) else None,
        "amount_zscore": round(amount_z, 3) if amount_z is not None else None,
        "upper_rejection_ratio": round(upper_ratio, 3) if upper_ratio is not None else None,
        "resistance_price": resistance,
        "price_shock": shock,
        "support_retention": None,
        "quality": quality,
        "model_version": MODEL_VERSION,
    }


def leadership_features(stocks: list[dict[str, Any]], *, sector_name: str | None = None, data_cutoff_time: Any = None) -> dict[str, Any]:
    """Rank relative leadership and beneficiary purity from observed rows."""
    clean = []
    for item in stocks or []:
        change = _number(item.get("change_pct") or item.get("latest_change_pct"))
        flow = _number(item.get("main_net_inflow"))
        breadth = _number(item.get("breadth_share"))
        relative = _number(item.get("relative_strength"))
        if change is None and flow is None:
            continue
        score = 50 + (change or 0) * 5 + (relative or 0) * 3 + (10 if flow and flow > 0 else -5 if flow and flow < 0 else 0)
        clean.append({
            **item,
            "leadership_score": round(_clamp(score) or 0, 2),
            "change_pct": change,
            "main_net_inflow": flow,
            "breadth_share": breadth,
            "relative_strength": relative,
        })
    clean.sort(key=lambda item: item.get("leadership_score") or 0, reverse=True)
    top = clean[0] if clean else None
    positive = [item for item in clean if (item.get("change_pct") or 0) > 0]
    purity = (len(positive) / len(clean) * 100) if clean else None
    state = "NO_DATA" if not clean else "BROAD_CONFIRMATION" if purity is not None and purity >= 65 else "LEADER_CONCENTRATION" if purity is not None and purity < 35 else "MIXED"
    quality = _quality(
        status="OBSERVED" if clean else "NO_DATA",
        available={"change_pct" if any(item.get("change_pct") is not None for item in clean) else "", "main_net_inflow" if any(item.get("main_net_inflow") is not None for item in clean) else ""},
        expected={"change_pct", "main_net_inflow"},
        source="market_snapshot" if clean else "unavailable",
        cutoff=data_cutoff_time,
        extra={"stock_count": len(clean)},
    )
    return {
        "sector": sector_name,
        "state": state,
        "leadership_score": top.get("leadership_score") if top else None,
        "leader": top,
        "beneficiary_purity_pct": round(purity, 2) if purity is not None else None,
        "stocks": clean[:20],
        "quality": quality,
        "model_version": MODEL_VERSION,
    }


def liquidity_map_features(bars: list[Any], *, data_cutoff_time: Any = None, bins: int = 8) -> dict[str, Any]:
    """Build a daily close/amount volume profile, explicitly not an L2 map."""
    ordered = sorted([row for row in bars or [] if _bar_date(row)], key=_bar_date)
    points = [(_bar_value(row, "close_price", "close"), _bar_value(row, "amount") or 0) for row in ordered]
    points = [(price, amount) for price, amount in points if price is not None and price > 0]
    if len(points) < 10:
        quality = _quality(status="INSUFFICIENT_HISTORY", available={"close_price"} if points else set(), expected={"close_price", "amount"}, source="StockDailyBar" if points else "unavailable", cutoff=data_cutoff_time, extra={"sessions": len(points)})
        return {"zones": [], "quality": quality, "model_version": MODEL_VERSION, "map_type": "DAILY_CLOSE_AMOUNT_PROFILE"}
    low = min(price for price, _ in points)
    high = max(price for price, _ in points)
    width = (high - low) / max(1, bins)
    if width <= 0:
        width = max(low * 0.01, 0.01)
    buckets: list[dict[str, Any]] = []
    for index in range(bins):
        lower = low + index * width
        upper = high if index == bins - 1 else lower + width
        amount = sum(value for price, value in points if lower <= price <= upper)
        buckets.append({"lower_price": round(lower, 4), "upper_price": round(upper, 4), "amount": amount})
    maximum = max((item["amount"] for item in buckets), default=0)
    for item in buckets:
        item["strength_score"] = round(item["amount"] / maximum * 100, 2) if maximum else 0
        item["confidence"] = round(min(1.0, item["amount"] / max(1, sum(value for _, value in points))), 3)
        item["zone_type"] = "HIGH_ACTIVITY" if item["strength_score"] >= 70 else "NORMAL_ACTIVITY"
    quality = _quality(status="OBSERVED", available={"close_price", "amount"}, expected={"close_price", "amount"}, source="StockDailyBar", cutoff=data_cutoff_time, extra={"sessions": len(points), "map_type": "DAILY_CLOSE_AMOUNT_PROFILE"})
    return {"zones": buckets, "quality": quality, "model_version": MODEL_VERSION, "map_type": "DAILY_CLOSE_AMOUNT_PROFILE"}


def candlestick_semantics(bars: list[Any], *, data_cutoff_time: Any = None) -> dict[str, Any]:
    """Return atomic candle geometry and confirmation windows, not a buy label."""
    ordered = sorted([row for row in bars or [] if _bar_date(row)], key=_bar_date)
    if not ordered:
        return {"semantic_state": "NO_DATA", "quality": _quality(status="NO_DATA", available=set(), expected={"body_ratio", "upper_wick_ratio", "lower_wick_ratio"}, source="unavailable", cutoff=data_cutoff_time), "model_version": MODEL_VERSION}
    latest = ordered[-1]
    open_price = _bar_value(latest, "open_price", "open")
    close = _bar_value(latest, "close_price", "close")
    high = _bar_value(latest, "high_price", "high")
    low = _bar_value(latest, "low_price", "low")
    spread = high - low if high is not None and low is not None else None
    body = abs(close - open_price) if close is not None and open_price is not None else None
    upper = high - max(open_price, close) if high is not None and open_price is not None and close is not None else None
    lower = min(open_price, close) - low if low is not None and open_price is not None and close is not None else None
    prior_close = _bar_value(ordered[-2], "close_price", "close") if len(ordered) >= 2 else None
    gap = _return(open_price, prior_close)
    body_ratio = body / spread if body is not None and spread not in (None, 0) else None
    upper_ratio = upper / spread if upper is not None and spread not in (None, 0) else None
    lower_ratio = lower / spread if lower is not None and spread not in (None, 0) else None
    if lower_ratio is not None and lower_ratio >= 0.55 and (body_ratio or 0) <= 0.35:
        state = "LOWER_REJECTION_ATOM"
    elif upper_ratio is not None and upper_ratio >= 0.55 and (body_ratio or 0) <= 0.35:
        state = "UPPER_REJECTION_ATOM"
    elif body_ratio is not None and body_ratio >= 0.65:
        state = "DIRECTIONAL_BODY_ATOM"
    else:
        state = "BALANCED_RANGE_ATOM"
    quality = _quality(status="OBSERVED", available={"body_ratio" if body_ratio is not None else "", "upper_wick_ratio" if upper_ratio is not None else "", "lower_wick_ratio" if lower_ratio is not None else ""}, expected={"body_ratio", "upper_wick_ratio", "lower_wick_ratio"}, source="StockDailyBar", cutoff=data_cutoff_time, extra={"sessions": len(ordered), "confirmation_required": True})
    return {
        "trend_context": "up" if len(ordered) >= 5 and (_bar_value(ordered[-1], "close_price", "close") or 0) > (_bar_value(ordered[-5], "close_price", "close") or 0) else "mixed",
        "body_ratio": round(body_ratio, 4) if body_ratio is not None else None,
        "upper_wick_ratio": round(upper_ratio, 4) if upper_ratio is not None else None,
        "lower_wick_ratio": round(lower_ratio, 4) if lower_ratio is not None else None,
        "gap_size_pct": round(gap, 4) if gap is not None else None,
        "upper_rejection": bool(upper_ratio is not None and upper_ratio >= 0.45),
        "lower_rejection": bool(lower_ratio is not None and lower_ratio >= 0.45),
        "follow_through_1d": None,
        "follow_through_3d": None,
        "semantic_state": state,
        "quality": quality,
        "model_version": MODEL_VERSION,
    }


def market_reward_punishment(rows: list[dict[str, Any]], *, data_cutoff_time: Any = None) -> dict[str, Any]:
    """Summarize which observable structures are being rewarded or penalized."""
    if not rows:
        return {"state": "NO_DATA", "quality": _quality(status="NO_DATA", available=set(), expected={"breadth", "turnover", "limit_structure"}, source="unavailable", cutoff=data_cutoff_time), "model_version": MODEL_VERSION}
    up = sum(1 for item in rows if (_number(item.get("change_pct")) or 0) > 0)
    down = sum(1 for item in rows if (_number(item.get("change_pct")) or 0) < 0)
    total = up + down
    breadth = up / total * 100 if total else None
    amounts = [_number(item.get("amount")) for item in rows]
    positive_flow = sum(1 for item in rows if (_number(item.get("main_net_inflow")) or 0) > 0)
    reward = "trend_and_flow" if breadth is not None and breadth >= 60 and positive_flow >= max(1, len(rows) * 0.45) else "defensive_or_selective" if breadth is not None and breadth <= 40 else "rotation_and_dispersion"
    quality = _quality(status="OBSERVED", available={"breadth", "turnover" if any(value is not None for value in amounts) else "", "flow" if positive_flow or any((_number(item.get("main_net_inflow")) or 0) < 0 for item in rows) else ""}, expected={"breadth", "turnover", "limit_structure"}, source="market_snapshot", cutoff=data_cutoff_time, extra={"row_count": len(rows)})
    return {
        "state": reward,
        "breadth_pct": round(breadth, 2) if breadth is not None else None,
        "up_count": up,
        "down_count": down,
        "flow_positive_share_pct": round(positive_flow / len(rows) * 100, 2),
        "rewarded_structures": ["相对强度与资金同步" if reward == "trend_and_flow" else "低波动与防御承接"],
        "punished_structures": ["弱势且资金流出" if reward != "trend_and_flow" else "冲高但资金不跟随"],
        "quality": quality,
        "model_version": MODEL_VERSION,
    }


def intraday_relative_strength(
    stock_bars: list[Any],
    benchmark_bars: list[Any],
    *,
    data_cutoff_time: Any = None,
) -> dict[str, Any]:
    """Compute observed 5-minute relative strength when both series exist."""
    def close_at(rows: list[Any], minutes: int) -> float | None:
        ordered = sorted([(moment, _bar_value(row, "close_price", "close")) for row in rows if (moment := _bar_time(row))], key=lambda item: item[0])
        if len(ordered) < 2:
            return None
        end = ordered[-1]
        target = end[0].timestamp() - minutes * 60
        start = min(ordered, key=lambda item: abs(item[0].timestamp() - target))
        return _return(end[1], start[1])
    stock_return = close_at(stock_bars or [], 5)
    benchmark_return = close_at(benchmark_bars or [], 5)
    relative = stock_return - benchmark_return if stock_return is not None and benchmark_return is not None else None
    quality = _quality(status="OBSERVED" if relative is not None else "NO_BENCHMARK_CONFIRMATION", available={"stock_5m" if stock_return is not None else "", "benchmark_5m" if benchmark_return is not None else ""}, expected={"stock_5m", "benchmark_5m"}, source="StockMinuteBar" if stock_return is not None else "unavailable", cutoff=data_cutoff_time)
    return {"stock_return_5m": stock_return, "benchmark_return_5m": benchmark_return, "relative_strength_5m": relative, "state": "OUTPERFORMING" if relative is not None and relative > 0.3 else "UNDERPERFORMING" if relative is not None and relative < -0.3 else "ALIGNED" if relative is not None else "UNAVAILABLE", "quality": quality, "model_version": MODEL_VERSION}


def regulatory_risk_snapshot(
    announcements: list[dict[str, Any]] | None = None,
    status_events: list[dict[str, Any]] | None = None,
    *,
    data_cutoff_time: Any = None,
) -> dict[str, Any]:
    """Score public risk language; it does not infer undisclosed supervision."""
    negative_terms = ("风险提示", "立案", "处罚", "问询", "监管", "减持", "退市", "违规", "停牌", "诉讼", "不确定性")
    positive_terms = ("澄清", "回复问询", "完成整改", "复牌")
    evidence = []
    for item in [*(announcements or []), *(status_events or [])]:
        title = str(item.get("title") or item.get("details") or "")
        if not title:
            continue
        negative = [term for term in negative_terms if term in title]
        positive = [term for term in positive_terms if term in title]
        if negative or positive:
            evidence.append({"title": title[:200], "negative_terms": negative, "positive_terms": positive, "published_at": item.get("published_at") or item.get("change_date")})
    raw_score = sum(16 for item in evidence if item["negative_terms"]) - sum(8 for item in evidence if item["positive_terms"])
    score = _clamp(raw_score)
    state = "HIGH" if (score or 0) >= 60 else "MEDIUM" if (score or 0) >= 25 else "LOW" if evidence else "NO_PUBLIC_RISK_EVIDENCE"
    quality = _quality(status="OBSERVED" if evidence else "NO_PUBLIC_RISK_EVIDENCE", available={"announcement_text" if announcements else "", "status_events" if status_events else ""}, expected={"announcement_text", "status_events"}, source="public_announcements", cutoff=data_cutoff_time, extra={"evidence_count": len(evidence)})
    return {"state": state, "risk_score": score, "evidence": evidence[:12], "quality": quality, "model_version": MODEL_VERSION}
