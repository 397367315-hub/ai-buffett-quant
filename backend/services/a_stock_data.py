"""A-stock-data skill compatibility layer.

The ClawHub ``a-stock-data`` skill documents AkShare field names and common
technical indicators. It is an instruction bundle rather than a production
data endpoint, so the application applies its conventions to the verified
daily bars already collected through the regional proxy, Tencent and FTShare.

Keeping the calculations local also avoids a second, un-audited network path
from the overseas Render service to mainland market websites.
"""

from __future__ import annotations

import math
from typing import Any


A_STOCK_DATA_SKILL = {
    "slug": "a-stock-data",
    "version": "1.1.0",
    "source": "ClawHub skill instructions / AkShare field contract",
    "history_adjustment": "qfq",
    "realtime_note": "行情源可能延迟1-5分钟，必须保留来源和更新时间",
    "code_rule": "沪600/601/603/605/688/689/900，深000/001/002/003/300，北4/8/92",
}


def _number(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def _rsi(values: list[float], period: int) -> float | None:
    if len(values) <= period:
        return None
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    window = changes[-period:]
    gains = sum(max(change, 0) for change in window) / period
    losses = sum(abs(min(change, 0)) for change in window) / period
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def calculate_indicators(history: list[dict]) -> dict:
    """Calculate the skill's common indicators without TA-Lib.

    Missing volume, high or low fields remain ``None``. No zero is substituted
    because zero would look like a valid market observation to the selector.
    """
    bars = []
    for row in history:
        close = _number(row.get("close"))
        if close is None or close <= 0:
            continue
        bars.append({
            "close": close,
            "high": _number(row.get("high")),
            "low": _number(row.get("low")),
            "volume": _number(row.get("volume")),
        })
    closes = [bar["close"] for bar in bars]
    highs = [bar["high"] for bar in bars]
    lows = [bar["low"] for bar in bars]
    volumes = [bar["volume"] for bar in bars]
    ma5 = _sma(closes, 5)
    ma10 = _sma(closes, 10)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)

    fast = _ema_series(closes, 12)
    slow = _ema_series(closes, 26)
    macd_dif = fast[-1] - slow[-1] if len(closes) >= 26 else None
    dif_series = [fast[index] - slow[index] for index in range(len(closes))]
    dea_series = _ema_series(dif_series, 9) if dif_series else []
    macd_dea = dea_series[-1] if len(closes) >= 26 and len(dea_series) >= 9 else None
    macd_hist = macd_dif - macd_dea if macd_dif is not None and macd_dea is not None else None

    rsi6 = _rsi(closes, 6)
    rsi14 = _rsi(closes, 14)
    rsi24 = _rsi(closes, 24)

    kdj_k = kdj_d = kdj_j = None
    if len(bars) >= 9:
        k_value, d_value = 50.0, 50.0
        contiguous_window = False
        for index in range(8, len(bars)):
            high_window = highs[index - 8:index + 1]
            low_window = lows[index - 8:index + 1]
            close = closes[index]
            if any(value is None for value in (*high_window, *low_window)) or close is None:
                contiguous_window = False
                continue
            if not contiguous_window:
                k_value, d_value = 50.0, 50.0
            highest = max(high_window)
            lowest = min(low_window)
            rsv = 50.0 if highest == lowest else (close - lowest) / (highest - lowest) * 100
            k_value = (2 * k_value + rsv) / 3
            d_value = (2 * d_value + k_value) / 3
            contiguous_window = True
        if contiguous_window:
            kdj_k, kdj_d, kdj_j = k_value, d_value, 3 * k_value - 2 * d_value

    boll_middle = ma20
    boll_upper = boll_lower = None
    if len(closes) >= 20 and boll_middle is not None:
        window = closes[-20:]
        deviation = math.sqrt(sum((value - boll_middle) ** 2 for value in window) / 20)
        boll_upper = boll_middle + deviation * 2
        boll_lower = boll_middle - deviation * 2

    latest_volume_window = volumes[-5:] if len(volumes) >= 5 else []
    volume_ma5 = (
        _sma(latest_volume_window, 5)
        if len(latest_volume_window) == 5 and all(value is not None and value >= 0 for value in latest_volume_window)
        else None
    )
    latest_volume = volumes[-1] if volumes and volumes[-1] is not None and volumes[-1] >= 0 else None
    volume_ratio = latest_volume / volume_ma5 if latest_volume is not None and volume_ma5 else None

    return {
        "ma5": _round(ma5, 2),
        "ma10": _round(ma10, 2),
        "ma20": _round(ma20, 2),
        "ma60": _round(ma60, 2),
        "macd": {
            "dif": _round(macd_dif),
            "dea": _round(macd_dea),
            "hist": _round(macd_hist),
        },
        "rsi": {
            "rsi6": _round(rsi6, 2),
            "rsi14": _round(rsi14, 2),
            "rsi24": _round(rsi24, 2),
        },
        "kdj": {
            "k": _round(kdj_k, 2),
            "d": _round(kdj_d, 2),
            "j": _round(kdj_j, 2),
        },
        "boll": {
            "upper": _round(boll_upper, 2),
            "middle": _round(boll_middle, 2),
            "lower": _round(boll_lower, 2),
        },
        "volume": {
            "ma5": _round(volume_ma5, 2),
            "ratio": _round(volume_ratio, 2),
        },
        "history_points": len(closes),
        "contract": A_STOCK_DATA_SKILL,
    }
