"""Point-in-time technical indicators shared by scanning and backtesting."""

from __future__ import annotations

import math
from typing import Any


def number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalize_snapshot_stock(stock: dict) -> dict:
    """Map source-native quote units into the public strategy-rule units."""
    sector = str(stock.get("sector") or "").strip()
    sectors = [sector] if sector else []
    sectors.extend(str(item).strip() for item in stock.get("sectors", []) if str(item).strip())
    return {
        **stock,
        "code": str(stock.get("code") or ""),
        "name": str(stock.get("name") or ""),
        "price": number(stock.get("price")),
        "change_pct": number(stock.get("change_pct")),
        "turnover": number(stock.get("turnover")),
        "vol_ratio": number(stock.get("volume_ratio") if "volume_ratio" in stock else stock.get("vol_ratio")),
        "pe_ttm": number(stock.get("pe") if "pe" in stock else stock.get("pe_ttm")),
        "pb": number(stock.get("pb")),
        "roe": number(stock.get("roe")),
        # EastMoney returns yuan; visual rules use 100 million yuan and 10k yuan.
        "market_cap": (number(stock.get("market_cap")) or 0) / 1e8 if number(stock.get("market_cap")) is not None else None,
        "main_inflow": (number(stock.get("main_net_inflow")) or 0) / 1e4 if number(stock.get("main_net_inflow")) is not None else None,
        "sector": sector,
        "sectors": list(dict.fromkeys(sectors)),
    }


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    output = [values[0]]
    for value in values[1:]:
        output.append(alpha * value + (1 - alpha) * output[-1])
    return output


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(item, 0.0) for item in changes]
    losses = [max(-item, 0.0) for item in changes]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        average_gain = (average_gain * (period - 1) + gain) / period
        average_loss = (average_loss * (period - 1) + loss) / period
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    return 100 - 100 / (1 + average_gain / average_loss)


def enrich_with_indicators(stock: dict, bars: list[dict]) -> dict:
    """Add indicators using only bars available up to the supplied last row."""
    valid = []
    for bar in bars:
        close = number(bar.get("close") if "close" in bar else bar.get("close_price"))
        if close is None or close <= 0:
            continue
        valid.append({
            **bar,
            "close": close,
            "open": number(bar.get("open") if "open" in bar else bar.get("open_price")),
            "high": number(bar.get("high") if "high" in bar else bar.get("high_price")),
            "low": number(bar.get("low") if "low" in bar else bar.get("low_price")),
            "volume": number(bar.get("volume")),
            "turnover": number(bar.get("turnover")),
        })
    result = dict(stock)
    if not valid:
        return result
    closes = [item["close"] for item in valid]
    for period in (5, 10, 20, 60):
        result[f"ma{period}"] = sum(closes[-period:]) / period if len(closes) >= period else None
    result["rsi"] = _rsi(closes)
    if len(closes) >= 35:
        fast = _ema(closes, 12)
        slow = _ema(closes, 26)
        dif = [left - right for left, right in zip(fast, slow)]
        signal = _ema(dif, 9)
        result["macd"] = (dif[-1] - signal[-1]) * 2
    else:
        result["macd"] = None

    trailing = valid[-60:]
    highs = [item["high"] for item in trailing if item["high"] is not None]
    lows = [item["low"] for item in trailing if item["low"] is not None]
    if highs and lows:
        high, low = max(highs), min(lows)
        for level in (0.382, 0.5, 0.618, 0.786):
            result[f"fib_{str(level).replace('.', '_')}"] = high - (high - low) * level

    latest = valid[-1]
    if None not in (latest["open"], latest["high"], latest["low"]):
        body_low = min(latest["open"], latest["close"])
        lower_shadow = max(0.0, body_low - latest["low"])
        body = abs(latest["close"] - latest["open"])
        full_range = latest["high"] - latest["low"]
        result["long_lower_shadow"] = bool(
            full_range > 0 and lower_shadow / full_range >= 0.4 and lower_shadow >= max(body * 2, latest["close"] * 0.002)
        )
    else:
        result["long_lower_shadow"] = None

    volumes = [item["volume"] for item in valid if item["volume"] is not None and item["volume"] > 0]
    if result.get("vol_ratio") is None and len(volumes) >= 6:
        average = sum(volumes[-6:-1]) / 5
        result["vol_ratio"] = volumes[-1] / average if average else None
    if result.get("turnover") is None:
        result["turnover"] = latest.get("turnover")
    return result
