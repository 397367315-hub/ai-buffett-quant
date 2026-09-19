"""Observable, time-ordered evidence for the Wildman rule adapters."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time
from math import isfinite
import re
from typing import Any


def number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def minute_key(value: Any) -> str | None:
    """Normalize an observable time to HHMM without inferring missing time."""
    if value is None:
        return None
    hour: int | None = None
    minute: int | None = None
    if isinstance(value, datetime):
        hour, minute = value.hour, value.minute
    elif isinstance(value, time):
        hour, minute = value.hour, value.minute
    else:
        text = str(value).strip()
        iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
        if "T" in iso_text or ("-" in iso_text[:10] and " " in iso_text):
            try:
                parsed = datetime.fromisoformat(iso_text)
            except ValueError:
                parsed = None
            if parsed is not None:
                hour, minute = parsed.hour, parsed.minute
        if hour is None:
            match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?", text)
            if match:
                hour, minute = int(match.group(1)), int(match.group(2))
            elif re.fullmatch(r"\d{3,4}", text):
                padded = text.zfill(4)
                hour, minute = int(padded[:2]), int(padded[2:])
    if hour is None or minute is None or not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}{minute:02d}"


def average(rows: list[Any], period: int, field: str = "close_price") -> float | None:
    values = [number(getattr(row, field, None)) for row in rows[-period:]]
    return sum(values) / period if len(values) == period and all(v is not None for v in values) else None


def crossed(rows: list[Any], fast: int, slow: int) -> bool | None:
    values = (average(rows, fast), average(rows, slow), average(rows[:-1], fast), average(rows[:-1], slow))
    if any(value is None for value in values):
        return None
    current_fast, current_slow, old_fast, old_slow = values
    return current_fast > current_slow and old_fast <= old_slow


def daily_facts(item: dict, rows: list[Any], auction: Any, theme: dict, market: dict) -> dict:
    target = market.get("trade_date")
    rows = [row for row in rows if not target or not getattr(row, "trade_date", None) or row.trade_date.isoformat() <= target]
    latest = rows[-1] if rows else None
    latest_date = getattr(latest, "trade_date", None)
    current = not target or (latest_date is not None and latest_date.isoformat() == target)
    # Old daily bars cannot prove a signal on the requested trading session.
    if not current:
        rows, latest = [], None
    close = number(getattr(latest, "close_price", None))
    low = number(getattr(latest, "low_price", None))
    high = number(getattr(latest, "high_price", None))
    opening = number(getattr(latest, "open_price", None))
    ma5, ma10, ma20, ma75 = (average(rows, p) for p in (5, 10, 20, 75))
    previous_ma20, previous_ma75 = average(rows[:-1], 20), average(rows[:-1], 75)
    mean_volume, volume = average(rows[:-1], 5, "volume"), number(getattr(latest, "volume", None))
    ratio = volume / mean_volume if volume is not None and mean_volume and mean_volume > 0 else None
    contraction = ratio < .8 if ratio is not None else None
    expansion = ratio >= 1.5 if ratio is not None else None
    historical_highs = [number(getattr(row, "high_price", None)) for row in rows[-21:-1]]
    pressure = max((v for v in historical_highs if v is not None), default=None)
    peak = max((number(getattr(row, "high_price", None)) or 0 for row in rows[-30:]), default=0)
    drawdown = (close / peak - 1) * 100 if close and peak else None
    height = int(number(item.get("continuous_days")) or 0)
    previous = item.get("previous_limit")
    previous_bar = rows[-2] if len(rows) >= 2 else None
    previous_date = getattr(previous_bar, "trade_date", None)
    previous_known = isinstance(previous, dict) and (not previous.get("trade_date") or previous_date is not None and previous_date.isoformat() == previous["trade_date"])
    previous_failed = number(previous.get("failed_attempts")) if previous_known else None
    failed = number(item.get("failed_attempts"))
    auction_date = getattr(auction, "trade_date", None)
    if target and (auction_date is None or auction_date.isoformat() != target):
        auction = None
    auction_pct = number(getattr(auction, "high_open_pct", None))
    auction_ratio = number(getattr(auction, "auction_volume_ratio", None))
    theme_count = int(theme.get("limit_up_count") or 0)
    max_height = int(theme.get("max_limit_height") or 0)
    start_date = getattr(rows[-height], "trade_date", None).isoformat() if height > 0 and len(rows) >= height and getattr(rows[-height], "trade_date", None) is not None else None
    established_date = theme.get("leader_established_date")
    after_leader = start_date > established_date if start_date and established_date else None
    own_time = minute_key(item.get("first_limit_time"))
    earliest_time = minute_key(theme.get("earliest_first_limit_time"))
    previous_time = minute_key((previous or {}).get("first_limit_time"))
    explicit_independence = item.get("independent_theme_leadership")
    independence = explicit_independence if isinstance(explicit_independence, bool) else None
    earliest_start_date = theme.get("earliest_start_date")
    early_theme_start = None
    if start_date is not None and earliest_start_date is not None:
        early_theme_start = start_date <= str(earliest_start_date) and after_leader is not True
    down_days = 0
    for index in range(len(rows) - 1, 0, -1):
        now, prior = number(rows[index].close_price), number(rows[index - 1].close_price)
        if now is None or prior is None or now >= prior:
            break
        down_days += 1
    previous_open = number(getattr(previous_bar, "open_price", None))
    previous_close = number(getattr(previous_bar, "close_price", None))
    half = (previous_open + previous_close) / 2 if previous_known and previous_open is not None and previous_close is not None else None
    solid = previous_failed == 0 and bool(previous_time) and previous_time[:4] <= "1130" if previous_known else None
    # This is a disclosed trailing-year range proxy, never an all-time low claim.
    weekly, monthly = defaultdict(list), defaultdict(list)
    for row in rows:
        day = getattr(row, "trade_date", None)
        value = number(getattr(row, "close_price", None))
        if day is not None and value is not None:
            weekly[day.isocalendar()[:2]].append(value)
            monthly[(day.year, day.month)].append(value)
    bottom = None
    if len(rows) >= 252 and len(weekly) >= 48 and len(monthly) >= 12 and close is not None:
        closes = [[values[-1] for values in grouped.values()] for grouped in (weekly, monthly)]
        bottom = all(max(values) > min(values) and close <= min(values) + .35 * (max(values) - min(values)) for values in closes)
    body = abs(close - opening) if close is not None and opening is not None else None
    stable = (close >= opening or (high > low and body <= .15 * (high - low))) if None not in (close, opening, high, low) else None
    breakout = close > pressure and expansion and close > opening if None not in (close, pressure, expansion, opening) else None
    return {
        "symbol": str(item.get("code") or "").split(".")[0], "name": item.get("name"),
        "close_price": close, "low_price": low, "high_price": high,
        "market_cap": number(item.get("market_cap")), "consecutive_limit_days": height,
        "theme_linkage": item.get("theme_linkage") if isinstance(item.get("theme_linkage"), bool) else None,
        "emotion_benchmark": item.get("emotion_benchmark") if isinstance(item.get("emotion_benchmark"), bool) else None,
        "first_limit_pioneer": height == 1 and own_time is not None and own_time == earliest_time and after_leader is not True,
        "early_theme_start": early_theme_start,
        "trend_intact": close >= ma20 if close is not None and ma20 is not None else None,
        "supplement": after_leader is True and independence is False and max_height >= 4 and 1 <= height <= max_height * .7,
        "supplement_started_after_leader": after_leader,
        "independent_theme_leadership": independence,
        "leader_height": max_height if max_height else None,
        "stock_start_date": start_date, "leader_established_date": established_date,
        "old_dragon": None, "cross_cycle": None,
        "first_volume_divergence": None,
        "divergence_low": low if failed is not None and failed > 0 else None,
        "yesterday_divergence": previous_failed > 0 if previous_failed is not None else None,
        "yesterday_strong": previous_failed == 0 if previous_failed is not None else None,
        "auction_pct": auction_pct,
        "auction_volume_strength": auction_ratio >= 1 if auction_ratio is not None else None,
        "open_volume_attack": None, "intraday_anchor": None,
        "weekly_monthly_bottom": bottom, "fundamentals_clear": None,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma75": ma75,
        "ma20_turning_up": ma20 > previous_ma20 if ma20 is not None and previous_ma20 is not None else None,
        "ma20_flat": abs(ma20 / previous_ma20 - 1) <= .003 if ma20 is not None and previous_ma20 else None,
        "ma5_cross_ma10": crossed(rows, 5, 10), "ma5_cross_ma20": crossed(rows, 5, 20),
        "ma5_near_cross": ma10 * .985 <= ma5 <= ma10 and average(rows[:-1], 5) < ma5 if ma5 is not None and ma10 and average(rows[:-1], 5) is not None else None,
        "cross_volume_expand": expansion, "volume_ratio_5d": ratio,
        "pullback_ma10_ma20": any(v is not None and low <= v * 1.015 and close >= v for v in (ma10, ma20)) if low is not None and close is not None else None,
        "volume_contract": contraction, "stabilizing_candle": stable, "bottom_volume_breakout": breakout,
        "drawdown_pct": drawdown,
        "extreme_low_volume": volume <= min(number(row.volume) or float("inf") for row in rows[-20:]) and ratio <= .5 if len(rows) >= 20 and volume is not None and ratio is not None else None,
        "support_recovered": None, "reversal_confirmed": None,
        "recent_solid_limit": solid,
        "holds_limit_candle_half": low >= half if low is not None and half is not None else None,
        "limit_candle_half": half,
        "renewed_volume_breakout": breakout,
        "pressure_price": pressure if close is not None and pressure is not None and pressure > close else None,
        "fake_breakout": high > pressure and close < pressure and expansion is False if None not in (high, close, pressure, expansion) else None,
        "high_volume_stall": ratio >= 1.8 and close >= peak * .9 and abs(number(getattr(latest, "change_pct", None)) or 0) < 1 if ratio is not None and close is not None and peak else None,
        "anchor_broken": low < half if low is not None and half is not None else None,
        "open_low": auction_pct < 0 if auction_pct is not None else None, "no_support": None,
        "decline_days": down_days if len(rows) >= 2 else None,
        "arc_days": None,
        "ma75_turning_up": ma75 > previous_ma75 if ma75 is not None and previous_ma75 is not None else None,
        "break_neckline": close > pressure if close is not None and pressure is not None else None,
        "fact_basis": {"daily_date": latest_date.isoformat() if latest_date else None, "current_daily": current, "history_rows": len(rows), "bottom_definition": "至少252根日线，周/月收盘位于近一年区间下35%（工程代理，非历史绝对底部）", "volume_definition": "当日成交量/前5日均量，放量≥1.5，缩量<0.8", "prior_limit_date": (previous or {}).get("trade_date"), "leadership_basis": "同日封板顺序仅为相关性观察，不能证明因果；独立领涨仅接受显式核验事实", "early_start_basis": "start_date与题材earliest_start_date的时序比较，且不得晚于leader_established_date"},
    }


def intraday_facts(timeline: list[dict]) -> dict:
    """Require distinct local troughs; three descending closes aren't support."""
    prices = [number(row.get("close_price", row.get("close", row.get("last_price", row.get("price"))))) for row in timeline]
    if len(prices) < 5 or any(value is None or value <= 0 for value in prices):
        return {}
    troughs = [prices[i] for i in range(1, len(prices) - 1) if prices[i] < prices[i - 1] and prices[i] <= prices[i + 1]]
    recent = troughs[-2:]
    drops = [(prices[i] / prices[i - 1] - 1) * 100 for i in range(1, len(prices))]
    volumes = [number(row.get("volume")) for row in timeline]
    down_volume = None
    if all(value is not None for value in volumes):
        down_volume = any(drops[i - 1] <= -3 and volumes[i] > volumes[i - 1] * 1.5 for i in range(1, len(prices)))
    return {
        "lows_rising": recent[1] > recent[0] if len(recent) == 2 else None,
        "two_pullbacks_hold": recent[1] >= recent[0] and prices[-1] > recent[-1] if len(recent) == 2 else None,
        "vertical_drop": any(drop <= -3 for drop in drops),
        "down_volume_expands": down_volume,
    }
