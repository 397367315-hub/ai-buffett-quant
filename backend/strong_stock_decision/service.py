"""Auditable, read-only strong-stock decision engine.

This module intentionally keeps the three-book vocabulary in the public
payload.  The numerical tests are configurable engineering features used to
recognise a book rule; they are not claimed to be thresholds from the books.
The module runs in SHADOW mode and never writes to existing strategy scores.
"""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from statistics import mean, pstdev
from typing import Any, Iterable

from sqlalchemy import desc, select

from config import settings
from database import async_session
from models import (
    ConceptBoard,
    ConceptFundFlowDaily,
    IndustryFundFlowDaily,
    MainForceEvidence,
    MainForceState,
    MarketBoard,
    PatternAnnotation,
    BigPatternInstance,
    BuyPointState,
    SellRiskState,
    StockCharacterState,
    StockDailyBar,
    StockFundFlowDaily,
    StockSkillSignal,
    StockUniverseSnapshot,
    StrongCaseLibrary,
    StrongDecisionState,
    ThemeState,
    ThreeBooksConsensus,
    ThreeDegreeState,
    TradingZoneGeometry,
    StarInstance,
)
from services.data_collector import collector, normalize_stock_code, shanghai_now

from .registry import (
    ACTIONS,
    BOOK_SKILL_DEFINITIONS,
    ENGINE_VERSION,
    SIGNAL_STATUSES,
    STATE_LABELS,
    skill_definition,
)
from .v2_engine import V2_ENGINE_VERSION, build_v2


# These values are ENGINE_FEATURE configuration.  They are intentionally
# exposed in the response so a later backtest can reproduce the run.
ENGINE_CONFIG = {
    "minimum_daily_bars": 60,
    "volume_shock_ratio": 1.50,
    "volume_contraction_ratio": 0.82,
    "near_high_pct": 4.0,
    "controlled_pullback_pct": 12.0,
    "box_width_pct": 18.0,
    "triangle_contraction_pct": 0.72,
    "breakout_margin_pct": 0.5,
    "risk_drawdown_pct": 15.0,
}

# Read-only metadata for the engineering thresholds. These values describe
# the current Shadow implementation; they are not book rules and cannot
# change runtime ACTION until validation promotes them.
_ENGINE_CONFIG_BOUNDS = {
    "minimum_daily_bars": (20, 400),
    "volume_shock_ratio": (1.0, 5.0),
    "volume_contraction_ratio": (0.4, 1.0),
    "near_high_pct": (1.0, 20.0),
    "controlled_pullback_pct": (3.0, 35.0),
    "box_width_pct": (5.0, 40.0),
    "triangle_contraction_pct": (0.4, 0.95),
    "breakout_margin_pct": (0.0, 5.0),
    "risk_drawdown_pct": (5.0, 40.0),
}
ENGINE_CONFIG_METADATA = {
    key: {
        "feature_name": key,
        "default_value": value,
        "min_value": _ENGINE_CONFIG_BOUNDS[key][0],
        "max_value": _ENGINE_CONFIG_BOUNDS[key][1],
        "market_regime": "ALL",
        "market_cap_bucket": "ALL",
        "timeframe": "1d",
        "source": "ENGINE_FEATURE:strong_stock_decision",
        "version": "v2.0",
        "knowledge_layer": "ENGINE_FEATURE",
    }
    for key, value in ENGINE_CONFIG.items()
}


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _round(value: Any, digits: int = 2) -> float | None:
    number = _finite(value)
    return round(number, digits) if number is not None else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _avg(values: Iterable[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    return mean(clean) if clean else None


def _ma(values: list[float | None], window: int) -> float | None:
    if len(values) < window:
        return None
    return _avg(values[-window:])


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1.0) * 100.0


def _max_drawdown_pct(values: Iterable[float | None], entry: float | None = None) -> float | None:
    """Return the worst peak-to-trough percentage in a forward window.

    The entry price is included as the first observation so a drawdown that
    starts immediately after a signal is not hidden by the first future close.
    This is an outcome statistic only; it never participates in signal
    calculation.
    """
    clean = [value for value in values if value is not None and value > 0]
    if entry is not None and entry > 0:
        clean.insert(0, entry)
    if len(clean) < 2:
        return None
    peak = clean[0]
    worst = 0.0
    for value in clean[1:]:
        peak = max(peak, value)
        worst = min(worst, (value / peak - 1.0) * 100.0)
    return worst


def _status(status: str, confidence: float | None, *, evidence: list[dict[str, Any]] | None = None,
            next_confirmation: list[str] | None = None, invalidation: list[str] | None = None,
            conflicts: list[str] | None = None, annotations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if status not in SIGNAL_STATUSES:
        status = "NOT_FOUND"
    return {
        "status": status,
        "confidence": _round(confidence, 1),
        "evidence": evidence or [],
        "conflicts": conflicts or [],
        "next_confirmation": next_confirmation or [],
        "invalidation": invalidation or [],
        "chart_annotations": annotations or [],
    }


def _evidence(text: str, *, feature: str | None = None, value: Any = None,
              evidence_type: str = "ENGINE_FEATURE") -> dict[str, Any]:
    item: dict[str, Any] = {"type": evidence_type, "text": text}
    if feature:
        item["feature"] = feature
    if value is not None:
        item["value"] = _round(value) if isinstance(value, (int, float)) else value
    return item


def _bar_value(row: Any, key: str) -> float | None:
    if isinstance(row, dict):
        return _finite(row.get(key))
    mapping = {
        "open": "open_price", "close": "close_price", "high": "high_price",
        "low": "low_price", "volume": "volume", "amount": "amount",
        "turnover": "turnover", "change_pct": "change_pct",
    }
    return _finite(getattr(row, mapping.get(key, key), None))


def _bar_date(row: Any) -> date | None:
    value = row.get("trade_date") if isinstance(row, dict) else getattr(row, "trade_date", None)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _normalise_bar(row: Any) -> dict[str, Any]:
    return {
        "trade_date": _bar_date(row),
        "open": _bar_value(row, "open"),
        "close": _bar_value(row, "close"),
        "high": _bar_value(row, "high"),
        "low": _bar_value(row, "low"),
        "volume": _bar_value(row, "volume"),
        "amount": _bar_value(row, "amount"),
        "turnover": _bar_value(row, "turnover"),
        "change_pct": _bar_value(row, "change_pct"),
        "name": (row.get("stock_name") if isinstance(row, dict) else getattr(row, "stock_name", None)) or "",
        "source": (row.get("source") if isinstance(row, dict) else getattr(row, "source", None)) or "stock_daily_bars",
    }


def _flow_date(row: Any) -> date | None:
    """Read the two date names used by the cache and upstream flow feeds."""
    value = row.get("trade_date") if isinstance(row, dict) else getattr(row, "trade_date", None)
    if value is None and isinstance(row, dict):
        value = row.get("date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _merge_flow_rows(
    cached_rows: Iterable[Any],
    remote_rows: Iterable[Any],
    *,
    latest: date | None = None,
) -> list[Any]:
    """Merge cached and remote flow history without inventing missing values.

    Remote history wins for the same date because it is the fresher source;
    rows after the point-in-time cutoff are discarded so replay stays causal.
    """
    merged: dict[date, Any] = {}
    for row in list(cached_rows) + list(remote_rows):
        row_date = _flow_date(row)
        if row_date is None or (latest is not None and row_date > latest):
            continue
        merged[row_date] = row
    return [merged[row_date] for row_date in sorted(merged)]


def _shape_arrays(features: dict[str, Any]) -> tuple[list[float | None], list[float | None], str]:
    """Return conservative shape bounds and disclose when close is the proxy.

    Public feeds occasionally omit high/low while still returning valid close
    history. Using close as a bound is mathematically conservative (it never
    claims an unseen intraday extreme); every caller labels this basis and caps
    confidence instead of presenting the proxy as complete OHLC data.
    """
    closes = features.get("closes") or []
    highs = features.get("highs") or []
    lows = features.get("lows") or []
    shaped_highs: list[float | None] = []
    shaped_lows: list[float | None] = []
    used_proxy = False
    for index, close in enumerate(closes):
        high = highs[index] if index < len(highs) else None
        low = lows[index] if index < len(lows) else None
        if high is None and close is not None:
            high = close
            used_proxy = True
        if low is None and close is not None:
            low = close
            used_proxy = True
        shaped_highs.append(high)
        shaped_lows.append(low)
    basis = "CLOSE_PROXY" if used_proxy else "OHLC"
    return shaped_highs, shaped_lows, basis


def _series_features(bars: list[dict[str, Any]]) -> dict[str, Any]:
    if not bars:
        return {
            "count": 0,
            "close": None,
            "prior_close": None,
            "closes": [],
            "opens": [],
            "highs": [],
            "lows": [],
            "volumes": [],
            "amounts": [],
            "changes": [],
            "ma5": None,
            "ma10": None,
            "ma20": None,
            "ma30": None,
            "ma60": None,
            "volume_ratio": None,
            "amount_ratio": None,
            "close_location": None,
            "high20_prior": None,
            "low20_prior": None,
            "range_high": None,
            "range_low": None,
            "position120": None,
            "returns5": None,
            "returns20": None,
            "volatility20": None,
            "upper_wick": None,
            "lower_wick": None,
            "up_volume": 0,
            "down_volume": 0,
        }
    closes = [row["close"] for row in bars]
    opens = [row["open"] for row in bars]
    highs = [row["high"] for row in bars]
    lows = [row["low"] for row in bars]
    volumes = [row["volume"] for row in bars]
    amounts = [row["amount"] for row in bars]
    changes: list[float | None] = []
    for index, row in enumerate(bars):
        change = row.get("change_pct")
        if change is None and index:
            change = _pct_change(row["close"], bars[index - 1]["close"])
        changes.append(change)
    close = closes[-1] if closes else None
    prior_close = closes[-2] if len(closes) > 1 else None
    ma5, ma10, ma20, ma30, ma60 = (_ma(closes, window) for window in (5, 10, 20, 30, 60))
    vol20 = _avg(volumes[-21:-1]) if len(volumes) >= 21 else _avg(volumes[:-1])
    amount20 = _avg(amounts[-21:-1]) if len(amounts) >= 21 else _avg(amounts[:-1])
    volume_ratio = close_location = None
    if volumes[-1] is not None and vol20 not in (None, 0):
        volume_ratio = volumes[-1] / vol20
    if close is not None and highs[-20:] and lows[-20:]:
        high20 = max(value for value in highs[-20:] if value is not None) if any(value is not None for value in highs[-20:]) else None
        low20 = min(value for value in lows[-20:] if value is not None) if any(value is not None for value in lows[-20:]) else None
        if high20 is not None and low20 is not None and high20 > low20:
            close_location = (close - low20) / (high20 - low20) * 100
    high20_prior = None
    if len(highs) > 20 and any(value is not None for value in highs[-21:-1]):
        high20_prior = max(value for value in highs[-21:-1] if value is not None)
    low20_prior = None
    if len(lows) > 20 and any(value is not None for value in lows[-21:-1]):
        low20_prior = min(value for value in lows[-21:-1] if value is not None)
    range_high = max(value for value in highs[-20:] if value is not None) if any(value is not None for value in highs[-20:]) else None
    range_low = min(value for value in lows[-20:] if value is not None) if any(value is not None for value in lows[-20:]) else None
    position120 = None
    if close is not None and len(closes) >= 20:
        lookback = [value for value in closes[-120:] if value is not None]
        if lookback and max(lookback) > min(lookback):
            position120 = (close - min(lookback)) / (max(lookback) - min(lookback)) * 100
    returns5 = _pct_change(close, closes[-6]) if len(closes) >= 6 else None
    returns20 = _pct_change(close, closes[-21]) if len(closes) >= 21 else None
    volatility20 = None
    clean_changes = [value for value in changes[-20:] if value is not None]
    if len(clean_changes) >= 5:
        volatility20 = pstdev(clean_changes)
    upper_wick = lower_wick = None
    if all(value is not None for value in (opens[-1], highs[-1], lows[-1], close)):
        body_high = max(opens[-1], close)
        body_low = min(opens[-1], close)
        span = highs[-1] - lows[-1]
        if span > 0:
            upper_wick = (highs[-1] - body_high) / span * 100
            lower_wick = (body_low - lows[-1]) / span * 100
    up_volume = sum((volumes[index] or 0) for index, change in enumerate(changes) if change is not None and change > 0)
    down_volume = sum((volumes[index] or 0) for index, change in enumerate(changes) if change is not None and change < 0)
    return {
        "count": len(bars), "close": close, "prior_close": prior_close, "closes": closes,
        "opens": opens, "highs": highs, "lows": lows, "volumes": volumes, "amounts": amounts,
        "changes": changes, "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma30": ma30, "ma60": ma60,
        "volume_ratio": volume_ratio, "amount_ratio": (amounts[-1] / amount20 if amounts[-1] is not None and amount20 not in (None, 0) else None),
        "close_location": close_location, "high20_prior": high20_prior, "low20_prior": low20_prior,
        "range_high": range_high, "range_low": range_low, "position120": position120,
        "returns5": returns5, "returns20": returns20, "volatility20": volatility20,
        "upper_wick": upper_wick, "lower_wick": lower_wick, "up_volume": up_volume, "down_volume": down_volume,
    }


def _feature_availability(features: dict[str, Any], *, flow: list[dict[str, Any]], sector: dict[str, Any] | None) -> dict[str, bool]:
    return {
        "daily_bars": features.get("count", 0) > 0,
        "long_daily_bars": features.get("count", 0) >= ENGINE_CONFIG["minimum_daily_bars"],
        "volume": features.get("volume_ratio") is not None,
        "ma": features.get("ma20") is not None,
        "flow": bool(flow),
        "sector": sector is not None,
        "theme": sector is not None,
    }


def _is_valid_ohlcv(features: dict[str, Any]) -> bool:
    return bool(features.get("count") and features.get("close") and features.get("close") > 0)


def _quantity_time_space(features: dict[str, Any]) -> dict[str, Any]:
    if not features.get("long_daily_bars"):
        return {"opportunity": None, "risk": None, "time_state": "UNKNOWN", "space_state": "UNKNOWN", "quantity_state": "UNKNOWN", "evidence": [_evidence("至少需要配置的历史日线长度", feature="minimum_daily_bars")], "status": "UNKNOWN"}
    vr, ret5, ret20, pos, close, ma20 = (features.get(key) for key in ("volume_ratio", "returns5", "returns20", "position120", "close", "ma20"))
    opportunity = 50.0
    risk = 35.0
    evidence: list[dict[str, Any]] = []
    if ret20 is not None and ret20 > 8:
        opportunity += 18; evidence.append(_evidence("近20日价格推进为正，时间阶段偏启势/顺势", feature="returns20", value=ret20))
    if vr is not None and 1.0 <= vr <= 2.5:
        opportunity += 12; evidence.append(_evidence("当前成交量相对20日均量有序放大", feature="volume_ratio", value=vr))
    if ret5 is not None and ret5 < -5:
        risk += 15; evidence.append(_evidence("近5日回撤使量时空压力上升", feature="returns5", value=ret5))
    if pos is not None and pos > 88:
        risk += 18; evidence.append(_evidence("价格处于近120日高位空间，需防大压风险", feature="position120", value=pos))
    if close is not None and ma20 is not None and close < ma20:
        risk += 12; evidence.append(_evidence("收盘位于MA20下方，趋势空间尚未修复", feature="close_vs_ma20", value=close - ma20))
    time_state = "盛势" if ret20 is not None and ret20 > 25 else "顺势" if ret20 is not None and ret20 > 8 else "分势" if ret5 is not None and ret5 < -4 else "蓄势"
    space_state = "高位" if pos is not None and pos > 80 else "中位" if pos is not None and pos > 35 else "低位"
    quantity_state = "放量" if vr is not None and vr >= 1.5 else "正常" if vr is not None else "UNKNOWN"
    return {"opportunity": _round(_clamp(opportunity)), "risk": _round(_clamp(risk)), "time_state": time_state, "space_state": space_state, "quantity_state": quantity_state, "evidence": evidence, "status": "AVAILABLE"}


def _main_force(features: dict[str, Any]) -> dict[str, Any]:
    if not features.get("long_daily_bars"):
        return {"state": "不明显", "direction": "暂不明确", "persistence": "减弱", "confidence": None, "evidence": [_evidence("日线样本不足，不能还原主力身影", feature="long_daily_bars")], "volume_pattern": "UNKNOWN", "price_pattern": "UNKNOWN", "turnover_pattern": "UNKNOWN"}
    up, down = features.get("up_volume", 0), features.get("down_volume", 0)
    ratio = up / down if down else (2.0 if up else 0.0)
    vr = features.get("volume_ratio") or 0
    ret5 = features.get("returns5") or 0
    state = "明显" if ratio > 1.45 and ret5 > 0 else "较明显" if ratio > 1.15 else "中性"
    direction = "偏多" if ratio > 1.15 and ret5 >= 0 else "偏空" if ratio < 0.8 or ret5 < -6 else "暂不明确"
    persistence = "增强" if vr >= 1.25 and ret5 > 0 else "持续" if ratio > 1.0 else "减弱"
    evidence = [
        _evidence("上涨日成交量与下跌日成交量的可观测结构", feature="up_down_volume_ratio", value=ratio),
        _evidence("当前价格对成交变化的反馈", feature="returns5", value=ret5),
    ]
    if features.get("lower_wick") is not None and features["lower_wick"] > 35:
        evidence.append(_evidence("低点回收形成承接证据，不推断参与者意图", feature="lower_wick", value=features["lower_wick"]))
    return {"state": state, "direction": direction, "persistence": persistence, "confidence": _round(_clamp(45 + abs(ratio - 1) * 25)), "evidence": evidence, "volume_pattern": "上涨量/下跌量结构", "price_pattern": "价格反馈与低点回收", "turnover_pattern": "成交量连续性"}


def _volume_price_ma(features: dict[str, Any]) -> dict[str, Any]:
    vr = features.get("volume_ratio")
    ret = features.get("changes", [])[-1] if features.get("changes") else None
    if vr is None or features.get("ma20") is None:
        return {"event": "UNKNOWN", "ma_state": "UNKNOWN", "推动": "UNKNOWN", "status": "UNKNOWN", "evidence": [_evidence("量价或均线数据不足", feature="volume_price_ma")]}
    event = "量价同步异动" if vr >= 1.5 and ret is not None and abs(ret) >= 2 else "量先异动" if vr >= 1.5 else "价先异动" if ret is not None and abs(ret) >= 2 else "无明显异动"
    mas = [features.get("ma5"), features.get("ma10"), features.get("ma20"), features.get("ma60")]
    if all(value is not None for value in mas) and mas[0] > mas[1] > mas[2] > mas[3]:
        ma_state = "均线展开"
    elif all(value is not None for value in mas) and max(mas) - min(mas) < features["close"] * 0.04:
        ma_state = "均线聚合"
    else:
        ma_state = "均线归位中" if features.get("close", 0) > features.get("ma20", 0) else "均线未归位"
    push = "量价异动正在推动均线归位" if event != "无明显异动" and ma_state in {"均线归位中", "均线展开"} else "暂未观察到推动链"
    return {"event": event, "ma_state": ma_state, "推动": push, "status": "AVAILABLE", "evidence": [_evidence(event, feature="volume_ratio", value=vr), _evidence(ma_state, feature="ma_alignment")]}


def _decision_score(
    qts: dict[str, Any],
    main_force: dict[str, Any],
    volume_ma: dict[str, Any],
    zone: dict[str, Any],
) -> dict[str, Any]:
    """Build one auditable score from the same values shown in the UI.

    Each component is kept on a 0-100 scale. Missing inputs stay missing and
    the final value is normalised by the weight of available components; a
    partial score therefore never turns an unavailable feed into a zero.
    """
    event_scores = {
        "量价同步异动": 82.0,
        "量先异动": 68.0,
        "价先异动": 60.0,
        "无明显异动": 48.0,
    }
    ma_scores = {
        "均线展开": 85.0,
        "均线归位中": 72.0,
        "均线聚合": 58.0,
        "均线未归位": 35.0,
    }
    zone_scores = {
        "强势A区": 88.0,
        "强势B区": 68.0,
        "风险C区": 25.0,
    }
    main_confidence = _finite(main_force.get("confidence"))
    components = [
        {
            "key": "risk_control",
            "label": "风险控制",
            "value": _clamp(100.0 - qts["risk"]) if _finite(qts.get("risk")) is not None else None,
            "weight": 0.22,
            "basis": "100 - 量时空风险",
        },
        {
            "key": "quantity_time_space",
            "label": "量时空",
            "value": _clamp(qts["opportunity"]) if _finite(qts.get("opportunity")) is not None else None,
            "weight": 0.28,
            "basis": "量时空机会分",
        },
        {
            "key": "main_force",
            "label": "主力证据",
            "value": _clamp(main_confidence) if main_confidence is not None else None,
            "weight": 0.16,
            "basis": "上涨/下跌成交量结构的证据置信度",
        },
        {
            "key": "volume_price",
            "label": "量价异动",
            "value": event_scores.get(volume_ma.get("event")),
            "weight": 0.12,
            "basis": "量价事件映射分",
        },
        {
            "key": "moving_average",
            "label": "均线归位",
            "value": ma_scores.get(volume_ma.get("ma_state")),
            "weight": 0.12,
            "basis": "均线状态映射分",
        },
        {
            "key": "trading_zone",
            "label": "A/B/C区",
            "value": zone_scores.get(zone.get("zone")),
            "weight": 0.10,
            "basis": "交易区状态映射分",
        },
    ]
    available = [item for item in components if item["value"] is not None]
    available_weight = sum(float(item["weight"]) for item in available)
    value = (
        sum(float(item["value"]) * float(item["weight"]) for item in available) / available_weight
        if available_weight > 0
        else None
    )
    for item in components:
        item["available"] = item["value"] is not None
        item["value"] = _round(item["value"], 1)
    return {
        "value": _round(value, 1),
        "status": "AVAILABLE" if len(available) == len(components) else "PARTIAL" if available else "UNAVAILABLE",
        "method": "可用组件按权重归一化",
        "components": components,
        "available_count": len(available),
        "component_count": len(components),
        "coverage_pct": _round(available_weight * 100.0, 1),
        "note": "评分只用于结构排序参考，不代表收益概率；不可用数据不会以0分代替。",
    }


def _zone(features: dict[str, Any], qts: dict[str, Any], main_force: dict[str, Any]) -> dict[str, Any]:
    if qts.get("status") != "AVAILABLE":
        return {"zone": "未形成明确交易区", "confidence": None, "reasons": ["量时空数据不足"], "risk_points": [], "next_confirmation": [], "invalidation": []}
    close, ma5, ma10, ma20, ma60 = (features.get(key) for key in ("close", "ma5", "ma10", "ma20", "ma60"))
    ret5, ret20 = features.get("returns5"), features.get("returns20")
    risk = qts.get("risk") or 0
    reasons: list[str] = []
    if all(value is not None for value in (close, ma5, ma10, ma20, ma60)) and close > ma5 > ma10 > ma20 > ma60 and (ret20 or 0) > 5 and risk < 65:
        zone = "强势A区"; reasons = ["股价与均线呈顺上排列", "近20日价格推进为正", "量时空机会高于压力"]
    elif all(value is not None for value in (close, ma20)) and close >= ma20 * 0.96 and (ret20 or 0) > 0 and risk < 78:
        zone = "强势B区"; reasons = ["强势结构尚未完全破坏", "当前处于调整或重新转强观察区"]
    elif (close is not None and ma20 is not None and close < ma20) or risk >= 78 or (ret5 is not None and ret5 < -15):
        zone = "风险C区"; reasons = ["趋势、位置或回撤压力占主导"]
    else:
        zone = "未形成明确交易区"; reasons = ["现有证据不足以定义A/B/C区"]
    risk_points = ["若关键均线和结构支撑失守，交易区降级"] if zone != "风险C区" else ["风险C区优先于攻击信号", "等待重新收复关键结构"]
    return {"zone": zone, "confidence": _round(_clamp(55 + (15 if main_force.get("direction") == "偏多" else 0))), "reasons": reasons, "risk_points": risk_points, "next_confirmation": ["成交保持并出现价格跟随", "板块/题材出现同向互证"], "invalidation": ["关键结构失守", "成交放大但价格不能推进", "卖出类风险信号增强"]}


def _pattern(signal_id: str, name: str, status: str, confidence: float | None, evidence: list[dict[str, Any]], *, next_confirmation: list[str] | None = None, invalidation: list[str] | None = None, conflicts: list[str] | None = None, annotation: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"skill_id": signal_id, "name": name, **_status(status, confidence, evidence=evidence, next_confirmation=next_confirmation, invalidation=invalidation, conflicts=conflicts, annotations=[annotation] if annotation else [])}


def _big_patterns(features: dict[str, Any], qts: dict[str, Any], flow: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    changes = features.get("changes", [])
    volumes = features.get("volumes", [])
    closes = features.get("closes", [])
    highs, lows, shape_basis = _shape_arrays(features)
    enough = features.get("long_daily_bars")
    shape_cap = 48 if shape_basis == "CLOSE_PROXY" else 100
    if enough and len(changes) >= 6:
        positive = sum(1 for value in changes[-6:-3] if value is not None and value > 0)
        negative = sum(1 for value in changes[-3:] if value is not None and value < 0)
        prior_vol = _avg(volumes[-6:-3]); latter_vol = _avg(volumes[-3:])
        s = "CONFIRMED" if positive >= 2 and negative >= 2 and prior_vol is not None and latter_vol is not None and prior_vol > latter_vol else "POSSIBLE" if positive >= 2 and negative >= 1 else "NOT_FOUND"
        out["BXDT_001"] = _pattern("BXDT_001", "三阳控三阴", s, 72 if s == "CONFIRMED" else 48 if s == "POSSIBLE" else None, [_evidence("近期阳线与阴线序列及量能反馈", feature="three_positive_three_negative", value=f"{positive}/{negative}"), _evidence("前后成交量相对关系", feature="volume_feedback", value=_pct_change(latter_vol, prior_vol))], annotation={"type": "三阳控三阴", "date": str(features.get("bars_end") or "")})
    else:
        out["BXDT_001"] = _pattern("BXDT_001", "三阳控三阴", "NOT_FOUND", None, [_evidence("历史日线或涨跌序列不足，暂不形成该形态", feature="three_positive_three_negative", value=len(changes))])
    ma_values = [features.get(key) for key in ("ma5", "ma10", "ma20", "ma60")]
    ma_ok = all(value is not None for value in ma_values)
    ma_order = ma_ok and ma_values[0] > ma_values[1] > ma_values[2] > ma_values[3]
    out["BXDT_002"] = _pattern("BXDT_002", "均线形态", "CONFIRMED" if ma_order else "FORMING" if ma_ok else "NOT_FOUND", 80 if ma_order else 52 if ma_ok else None, [_evidence("均线方向与位置", feature="ma_alignment", value=ma_values)], annotation={"type": "均线形态"})
    high_left = [v for v in highs[-20:-10] if v is not None]
    high_right = [v for v in highs[-10:] if v is not None]
    low_left = [v for v in lows[-20:-10] if v is not None]
    low_right = [v for v in lows[-10:] if v is not None]
    if enough and high_left and high_right and low_left and low_right:
        first_high = max(high_left); second_high = max(high_right); first_low = min(low_left); second_low = min(low_right)
        old_width = first_high - first_low; new_width = second_high - second_low
        triangle = old_width > 0 and new_width / old_width <= ENGINE_CONFIG["triangle_contraction_pct"]
        triangle_confidence = min(58, shape_cap) if triangle else None
        out["BXDT_003"] = _pattern("BXDT_003", "三角形形态", "FORMING" if triangle else "NOT_FOUND", triangle_confidence, [_evidence("高低点区间收敛", feature="convergence_ratio", value=new_width / old_width if old_width else None), _evidence("形态边界数据口径", feature="price_basis", value=shape_basis)], annotation={"type": "三角形", "upper_boundary": second_high, "lower_boundary": second_low, "price_basis": shape_basis})
        box_width = new_width / max(second_low, 0.01) * 100
        box = box_width <= ENGINE_CONFIG["box_width_pct"]
        shape_high20_prior = max(value for value in highs[-21:-1] if value is not None) if len(highs) > 20 and any(value is not None for value in highs[-21:-1]) else None
        breakout = features.get("close") is not None and shape_high20_prior is not None and features["close"] > shape_high20_prior * (1 + ENGINE_CONFIG["breakout_margin_pct"] / 100)
        box_status = "CONFIRMED" if box and breakout and shape_basis == "OHLC" else "FORMING" if box else "NOT_FOUND"
        box_confidence = 76 if box_status == "CONFIRMED" else 55 if box else None
        if box_confidence is not None:
            box_confidence = min(box_confidence, shape_cap)
        out["BXDT_004"] = _pattern("BXDT_004", "箱体形态", box_status, box_confidence, [_evidence("箱体宽度", feature="box_width_pct", value=box_width), _evidence("是否向上突破", feature="breakout", value=breakout), _evidence("形态边界数据口径", feature="price_basis", value=shape_basis)], annotation={"type": "箱体", "upper_boundary": second_high, "lower_boundary": second_low, "price_basis": shape_basis})
        neckline = first_high
        attack = sum(1 for value in highs[-10:] if value is not None and value >= neckline * 0.98)
        neckline_confidence = min(54, shape_cap) if attack else None
        out["BXDT_005"] = _pattern("BXDT_005", "颈位形态", "FORMING" if attack else "NOT_FOUND", neckline_confidence, [_evidence("前高作为颈位并被重复攻击", feature="neckline_attacks", value=attack), _evidence("颈位价格数据口径", feature="price_basis", value=shape_basis)], annotation={"type": "颈位", "key_price": neckline, "price_basis": shape_basis})
    else:
        for sid, name in (("BXDT_003", "三角形形态"), ("BXDT_004", "箱体形态"), ("BXDT_005", "颈位形态")):
            out[sid] = _pattern(sid, name, "NOT_FOUND", None, [_evidence("历史日线或高低点字段不足，暂不形成该形态", feature="long_daily_bars", value=bool(enough)), _evidence("可用形态价格口径", feature="price_basis", value=shape_basis)])
    higher_highs = len(highs) >= 10 and all((highs[i] is None or highs[i - 1] is None or highs[i] >= highs[i - 1]) for i in range(len(highs) - 4, len(highs)))
    higher_lows = len(lows) >= 10 and all((lows[i] is None or lows[i - 1] is None or lows[i] >= lows[i - 1]) for i in range(len(lows) - 4, len(lows)))
    trend_up = higher_highs and higher_lows and (features.get("ma20") or 0) < (features.get("close") or 0)
    out["BXDT_006"] = _pattern("BXDT_006", "顺上形态", "CONFIRMED" if trend_up else "FORMING" if ma_ok and (features.get("close") or 0) > (features.get("ma20") or float("inf")) else "NOT_FOUND", 78 if trend_up else 50 if ma_ok else None, [_evidence("高低点、均线和价格连续性", feature="higher_highs_higher_lows", value=bool(trend_up))], annotation={"type": "顺上"})
    ret20 = features.get("returns20")
    base = features.get("volatility20") is not None and features.get("volatility20") < 4.5
    reclaim = features.get("close") is not None and features.get("ma20") is not None and features["close"] > features["ma20"]
    out["BXDT_007"] = _pattern("BXDT_007", "趋势底部", "CONFIRMED" if (ret20 or 0) < -8 and base and reclaim else "FORMING" if (ret20 or 0) < -8 and base else "NOT_FOUND", 68 if base and reclaim else 45 if base else None, [_evidence("下跌后波动收敛和收复", feature="base_reclaim", value=bool(base and reclaim))], annotation={"type": "趋势底部"})
    flow_net = _avg([_finite(row.get("main_net_inflow")) for row in flow[-5:]]) if flow else None
    out["BXDT_008"] = _pattern("BXDT_008", "资金底部", "CONFIRMED" if flow_net is not None and flow_net > 0 and base else "FORMING" if flow_net is not None and base else "NOT_FOUND", 70 if flow_net is not None and flow_net > 0 and base else 45 if flow_net is not None else None, [_evidence("板块/个股资金流与价格底部的同日证据", feature="flow_net", value=flow_net)])
    out["BXDT_009"] = _pattern("BXDT_009", "三度行大道", "POSSIBLE" if trend_up and features.get("returns20", 0) > 15 else "NOT_FOUND", 42 if trend_up else None, [_evidence("高级形态仅作候选观察，尚未使用回测确认", evidence_type="BOOK_RULE_EVIDENCE")])
    shape_high20_prior = max(value for value in highs[-21:-1] if value is not None) if len(highs) > 20 and any(value is not None for value in highs[-21:-1]) else None
    peak_break = features.get("close") is not None and shape_high20_prior is not None and features["close"] > shape_high20_prior
    out["BXDT_010"] = _pattern("BXDT_010", "巅峰超越", "POSSIBLE" if peak_break else "NOT_FOUND", min(44, shape_cap) if peak_break else None, [_evidence("突破前高的候选证据，未经回测不参与总决策", feature="prior_peak_break", value=peak_break), _evidence("前高数据口径", feature="price_basis", value=shape_basis)])
    return out


def _stars(features: dict[str, Any], zone: dict[str, Any], big: dict[str, dict[str, Any]], main_force: dict[str, Any], sector: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    ret5, ret20, vr = features.get("returns5"), features.get("returns20"), features.get("volume_ratio")
    close = features.get("close")
    shape_highs, _shape_lows, shape_basis = _shape_arrays(features)
    high20 = features.get("high20_prior")
    if high20 is None and len(shape_highs) > 20 and any(value is not None for value in shape_highs[-21:-1]):
        high20 = max(value for value in shape_highs[-21:-1] if value is not None)
    lower, upper = features.get("lower_wick"), features.get("upper_wick")
    downtrend = (ret20 or 0) < -8
    base = features.get("volatility20") is not None and features["volatility20"] < 5.0
    reclaim = close is not None and features.get("ma20") is not None and close >= features["ma20"]
    # The two accumulation star lines are deliberately conservative: they
    # remain possible until later price/volume confirmation appears.
    out["BXZX_001"] = _pattern("BXZX_001", "诱空蓄势星线", "FORMING" if downtrend and base and lower is not None and lower > 25 else "NOT_FOUND", 55 if downtrend and base else None, [_evidence("下探后收回且波动收敛", feature="lower_wick", value=lower)], annotation={"type": "诱空蓄势"})
    out["BXZX_002"] = _pattern("BXZX_002", "逼空蓄势星线", "FORMING" if base and ret5 is not None and ret5 > 2 and (vr or 0) < 1.3 else "NOT_FOUND", 52 if base else None, [_evidence("蓄势区内价格保持强度", feature="returns5", value=ret5)], annotation={"type": "逼空蓄势"})
    controlled = zone.get("zone") in {"强势A区", "强势B区"} and ret5 is not None and -8 <= ret5 <= 1
    out["BXZX_003"] = _pattern("BXZX_003", "缓冲调整星线", "POSSIBLE" if controlled else "NOT_FOUND", 50 if controlled else None, [_evidence("强势区内回撤幅度受控", feature="returns5", value=ret5)], annotation={"type": "缓冲调整"})
    out["BXZX_004"] = _pattern("BXZX_004", "震荡调整星线", "FORMING" if zone.get("zone") == "强势B区" and base else "NOT_FOUND", 54 if zone.get("zone") == "强势B区" and base else None, [_evidence("强势B区内横向整理", feature="zone", value=zone.get("zone"))], annotation={"type": "震荡调整"})
    sync_stop = lower is not None and lower > 25 and reclaim
    relative_stop = sync_stop and (features.get("returns5") or 0) > -3
    out["BXZX_005"] = _pattern("BXZX_005", "同步止跌星线", "POSSIBLE" if sync_stop else "NOT_FOUND", 49 if sync_stop else None, [_evidence("个股低点回收并收复均线", feature="reclaim", value=reclaim)], annotation={"type": "同步止跌"})
    out["BXZX_006"] = _pattern("BXZX_006", "背离止跌星线", "POSSIBLE" if relative_stop else "NOT_FOUND", 46 if relative_stop else None, [_evidence("个股相对近期走势保持", feature="relative_strength_proxy", value=relative_stop)], annotation={"type": "背离止跌"})
    borrow_trend = zone.get("zone") == "强势B区" and main_force.get("direction") == "偏多"
    out["BXZX_007"] = _pattern("BXZX_007", "借势补仓星线", "POSSIBLE" if borrow_trend else "NOT_FOUND", 45 if borrow_trend else None, [_evidence("强势趋势与主力证据同时存在", feature="zone_main_force", value=borrow_trend)], annotation={"type": "借势补仓"})
    sector_wind = borrow_trend and sector is not None
    out["BXZX_008"] = _pattern("BXZX_008", "借风补仓星线", "POSSIBLE" if sector_wind else "NOT_FOUND", 45 if sector_wind else None, [_evidence("板块互证数据可用且结构未破坏", feature="sector_confirmation", value=sector_wind)], annotation={"type": "借风补仓"})
    breakout = high20 is not None and close is not None and close > high20 * (1 + ENGINE_CONFIG["breakout_margin_pct"] / 100) and (vr or 0) >= 1.3
    possible_breakout = high20 is not None and close is not None and close >= high20 * 0.98
    attack_status = "CONFIRMED" if breakout and shape_basis == "OHLC" else "POSSIBLE" if breakout or possible_breakout else "NOT_FOUND"
    attack_confidence = 78 if breakout and shape_basis == "OHLC" else 52 if possible_breakout else None
    if attack_confidence is not None and shape_basis == "CLOSE_PROXY":
        attack_confidence = min(attack_confidence, 48)
    out["BXZX_009"] = _pattern("BXZX_009", "突破攻击星线", attack_status, attack_confidence, [_evidence("收盘与前高关系", feature="prior_high_break", value=_pct_change(close, high20)), _evidence("成交量确认", feature="volume_ratio", value=vr), _evidence("前高数据口径", feature="price_basis", value=shape_basis)], next_confirmation=["板块宽度和主力证据继续增强", "回踩不回到箱体内部"], invalidation=["突破后收盘重新跌回颈位/箱体"], annotation={"type": "突破攻击", "key_price": high20, "price_basis": shape_basis})
    reversal = downtrend and reclaim and (vr or 0) >= 1.2
    out["BXZX_010"] = _pattern("BXZX_010", "反转攻击星线", "POSSIBLE" if reversal else "NOT_FOUND", 56 if reversal else None, [_evidence("下行后收复均线并出现量能响应", feature="reversal_reclaim", value=reversal)], annotation={"type": "反转攻击"})
    bottom_classic = (big.get("BXDT_007") or {}).get("status") in {"FORMING", "CONFIRMED"} and reclaim
    out["BXZX_011"] = _pattern("BXZX_011", "见底经典星线", "POSSIBLE" if bottom_classic else "NOT_FOUND", 51 if bottom_classic else None, [_evidence("趋势底部候选与收复条件同时存在", feature="bottom_reclaim", value=bottom_classic)], annotation={"type": "见底经典"})
    top_classic = (zone.get("zone") == "风险C区" and (upper or 0) > 30) or ((ret20 or 0) > 20 and (upper or 0) > 35 and (ret5 or 0) < 0)
    out["BXZX_012"] = _pattern("BXZX_012", "现顶经典星线", "CONFIRMED" if top_classic and zone.get("zone") == "风险C区" else "POSSIBLE" if top_classic else "NOT_FOUND", 80 if top_classic and zone.get("zone") == "风险C区" else 55 if top_classic else None, [_evidence("高位与上影/回落压力", feature="upper_wick", value=upper)], conflicts=["突破攻击星线" ] if top_classic else [], annotation={"type": "现顶经典"})
    out["BXZX_013"] = _pattern("BXZX_013", "望星空案例对照", "POSSIBLE" if any(item.get("status") in {"POSSIBLE", "FORMING", "CONFIRMED"} for item in out.values()) else "NOT_FOUND", 40 if out else None, [_evidence("当前结构可进入正例、反例和形似失败案例对照", evidence_type="BOOK_RULE_EVIDENCE")], annotation={"type": "望星空"})
    return out


def _profit_and_sell(features: dict[str, Any], zone: dict[str, Any], big: dict[str, dict[str, Any]], stars: dict[str, dict[str, Any]], main_force: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    strong = zone.get("zone") in {"强势A区", "强势B区"}
    support = features.get("ma20") is not None and features.get("close") is not None and features["close"] >= features["ma20"] * 0.96
    pullback = features.get("returns5") is not None and -10 <= features["returns5"] <= 1
    out["HQS_011"] = _pattern("HQS_011", "强势结点盈利模式", "POSSIBLE" if strong and support and pullback else "NOT_FOUND", 51 if strong and support and pullback else None, [_evidence("交易区、支撑和回调共同存在", feature="zone_support_pullback", value=strong and support and pullback)])
    hard_wash = strong and features.get("returns5") is not None and features["returns5"] < -4 and (features.get("volume_ratio") or 0) < 1.4 and support
    out["HQS_012"] = _pattern("HQS_012", "单日强硬洗盘盈利模式", "POSSIBLE" if hard_wash else "NOT_FOUND", 49 if hard_wash else None, [_evidence("单日/短期回撤但关键结构未破坏", feature="controlled_hard_pullback", value=hard_wash)])
    closes = features.get("closes", []); opens = features.get("opens", [])
    gap = len(closes) > 1 and closes[-2] is not None and opens[-1] is not None and opens[-1] > closes[-2] * 1.02
    out["HQS_013"] = _pattern("HQS_013", "缺口盈利模式", "POSSIBLE" if gap and strong else "NOT_FOUND", 46 if gap and strong else None, [_evidence("向上缺口与交易区关系", feature="gap_pct", value=_pct_change(opens[-1], closes[-2]) if gap else None)])
    cycle_low = strong and support and pullback and main_force.get("direction") == "偏多"
    out["HQS_014"] = _pattern("HQS_014", "强势循环低点盈利模式", "POSSIBLE" if cycle_low else "NOT_FOUND", 50 if cycle_low else None, [_evidence("强势趋势、回调、支撑和主力证据", feature="cycle_low", value=cycle_low)])
    top = stars.get("BXZX_012", {}).get("status") in {"POSSIBLE", "CONFIRMED"}
    resistance = features.get("upper_wick") is not None and features["upper_wick"] > 30 and (features.get("returns5") or 0) < 0
    out["HQS_015"] = _pattern("HQS_015", "明显见顶卖出策略", "CONFIRMED" if top and zone.get("zone") == "风险C区" else "POSSIBLE" if top else "NOT_FOUND", 78 if top and zone.get("zone") == "风险C区" else 52 if top else None, [_evidence("现顶经典星线与风险C区优先", feature="top_risk", value=top)], conflicts=["突破攻击星线"] if top else [])
    out["HQS_016"] = _pattern("HQS_016", "明显遇顶卖出策略", "POSSIBLE" if resistance else "NOT_FOUND", 54 if resistance else None, [_evidence("上影与短期回落形成遇顶候选", feature="upper_wick", value=features.get("upper_wick"))])
    c_exit = zone.get("zone") == "风险C区"
    out["HQS_017"] = _pattern("HQS_017", "C区卖出策略", "CONFIRMED" if c_exit else "NOT_FOUND", 82 if c_exit else None, [_evidence("当前最佳交易区为风险C区", feature="zone", value=zone.get("zone"))])
    return out


def _topic_confirmation(sector: dict[str, Any] | None, flow: list[dict[str, Any]]) -> dict[str, Any]:
    if sector is None and not flow:
        return {"status": "UNKNOWN", "volume_energy": "UNKNOWN", "theme": "UNKNOWN", "reasons": ["没有可核验板块或题材数据"], "source": "unavailable"}
    net = _avg([_finite(item.get("main_net_inflow")) for item in flow[-5:]]) if flow else None
    theme = "强" if sector and (_finite(sector.get("change_pct")) or 0) > 1 and (net or 0) >= 0 else "弱" if sector else "UNKNOWN"
    volume_energy = "强" if net is not None and net > 0 else "中" if flow else "UNKNOWN"
    status = "成立" if volume_energy == "强" and theme == "强" else "不足" if volume_energy != "UNKNOWN" and theme != "UNKNOWN" else "UNKNOWN"
    return {"status": status, "volume_energy": volume_energy, "theme": theme, "reasons": ["板块价格/资金与个股量能分别核验，不覆盖风险C区"], "source": "IndustryFundFlowDaily+StockDailyBar"}


def _stack(signals: dict[str, dict[str, Any]], topic: dict[str, Any], zone: dict[str, Any]) -> dict[str, Any]:
    positive_ids = ["HQS_001", "HQS_004", "HQS_006", "HQS_008", "BXDT_004", "BXZX_009"]
    risk_ids = ["HQS_002", "HQS_010", "HQS_015", "HQS_016", "HQS_017", "BXZX_012"]
    positive = [signals[item]["name"] for item in positive_ids if item in signals and signals[item]["status"] in {"POSSIBLE", "FORMING", "CONFIRMED"}]
    confirmed = [signals[item]["name"] for item in positive_ids if item in signals and signals[item]["status"] == "CONFIRMED"]
    risks = [signals[item]["name"] for item in risk_ids if item in signals and signals[item]["status"] in {"POSSIBLE", "FORMING", "CONFIRMED"}]
    observed = any(item.get("status") != "NOT_FOUND" for item in signals.values())
    level = "很强" if len(confirmed) >= 4 and not risks else "强" if len(positive) >= 3 and not risks else "中" if positive else "弱" if observed else "UNKNOWN"
    if risks:
        level = "中" if level == "很强" else level
    return {"level": level, "confirmed": confirmed, "possible": [item for item in positive if item not in confirmed], "risks": risks, "topic": topic, "conflicts": [f"{item}与攻击类信号冲突，风险优先" for item in risks if "卖出" in item or "风险C区" in item or "现顶" in item]}


def _state_and_action(zone: dict[str, Any], big: dict[str, dict[str, Any]], stars: dict[str, dict[str, Any]], sell: dict[str, dict[str, Any]], stack: dict[str, Any], qts: dict[str, Any], main_force: dict[str, Any]) -> dict[str, Any]:
    risk_top = sell.get("HQS_015", {}).get("status") == "CONFIRMED" or sell.get("HQS_017", {}).get("status") == "CONFIRMED" or stars.get("BXZX_012", {}).get("status") == "CONFIRMED"
    risk_possible = risk_top or zone.get("zone") == "风险C区" or bool(stack.get("risks"))
    attack = stars.get("BXZX_009", {}).get("status") in {"POSSIBLE", "CONFIRMED"} or stars.get("BXZX_010", {}).get("status") == "POSSIBLE"
    if qts.get("status") != "AVAILABLE":
        code, action = "S0", "NO_TRADE"
    elif risk_top:
        code, action = ("S17", "EXIT") if stars.get("BXZX_012", {}).get("status") == "CONFIRMED" or sell.get("HQS_015", {}).get("status") == "CONFIRMED" else ("S15", "RISK")
    elif zone.get("zone") == "风险C区":
        code, action = "S15", "RISK"
    elif attack and not risk_possible:
        code, action = ("S13", "READY") if stars.get("BXZX_009", {}).get("status") == "CONFIRMED" else ("S13", "CONFIRMING")
    elif zone.get("zone") == "强势A区":
        code, action = "S6", "HOLD"
    elif zone.get("zone") == "强势B区":
        code, action = "S7", "WATCH"
    elif qts.get("opportunity", 0) and qts["opportunity"] > 60:
        code, action = "S2", "WATCH"
    else:
        code, action = "S1", "WATCH"
    next_confirmation = ["下一交易日价格继续得到成交量和板块宽度确认", "突破后不重新跌回关键结构"] if action in {"READY", "CONFIRMING", "WATCH"} else ["重新收复MA20/关键支撑", "风险信号减弱"]
    invalidation = ["关键结构失守", "量价异动失败", "板块/题材互证消失"] if action not in {"EXIT", "RISK"} else ["风险C区继续扩大", "明显见顶或现顶经典星线确认"]
    return {"state_code": code, "state_name": STATE_LABELS[code], "action": action, "primary_skill": "现顶经典星线" if risk_top else "突破攻击星线" if attack else zone.get("zone"), "secondary_skills": stack.get("confirmed", []) + stack.get("possible", []), "risk_skills": stack.get("risks", []), "next_confirmation": next_confirmation, "invalidation": invalidation, "reason": {"risk_priority": risk_possible, "quantity_time_space": qts, "main_force": main_force, "zone": zone.get("zone"), "stack_level": stack.get("level")}}


class StrongStockDecisionService:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, str | None], tuple[float, dict[str, Any]]] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Multiple panels can request the same flow snapshot concurrently.
        # Serialise writes per symbol to avoid same-day unique-key races.
        self._flow_cache_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.cache_seconds = 90

    @staticmethod
    def _enabled() -> bool:
        return bool(settings.feature_strong_stock_decision)

    @staticmethod
    def _v2_enabled() -> bool:
        return bool(getattr(settings, "feature_strong_stock_decision_v2", True))

    @staticmethod
    def _v2_disabled_envelope() -> dict[str, Any]:
        return {
            "module_id": V2_ENGINE_VERSION,
            "engine_version": V2_ENGINE_VERSION,
            "mode": "SHADOW",
            "status": "DISABLED",
            "enabled": False,
            "signals": [],
            "data_quality": {
                "status": "DISABLED",
                "note": "FEATURE_STRONG_STOCK_DECISION_V2 已关闭；V1 结果继续可用。",
            },
        }

    def rule_config(self) -> dict[str, Any]:
        """Expose threshold provenance without enabling runtime tuning."""
        return {
            "module_id": V2_ENGINE_VERSION,
            "version": "v2.0",
            "status": "SHADOW_ONLY",
            "runtime_applied": False,
            "editable": False,
            "configs": deepcopy(list(ENGINE_CONFIG_METADATA.values())),
            "runtime_defaults": deepcopy(ENGINE_CONFIG),
            "note": "参数仅用于审计和回测登记；未通过样本外验证前不改变正式ACTION。",
        }

    async def registry(self, book: str | None = None) -> dict[str, Any]:
        values = [deepcopy(item) for item in BOOK_SKILL_DEFINITIONS if not book or item["book"] == book]
        from .registry import list_v2_book_skills
        v2_values = list_v2_book_skills(book=book)
        return {"module_id": "STRONG_STOCK_DECISION_V1", "engine_version": ENGINE_VERSION, "v2_engine_version": V2_ENGINE_VERSION, "mode": "SHADOW", "enabled": self._enabled(), "v2_enabled": self._v2_enabled(), "skills": values, "v2_skills": v2_values, "books": ["猎取强势股", "暴涨大形态", "暴涨之星"], "signal_statuses": list(SIGNAL_STATUSES), "actions": list(ACTIONS), "state_labels": deepcopy(STATE_LABELS), "engine_config": deepcopy(ENGINE_CONFIG), "terminology_policy": {"main_force_label": "主力", "locked_terms": True, "unverifiable_intent_policy": "只描述可观察证据"}}

    async def _load_context(self, symbol: str, as_of: date | None) -> dict[str, Any]:
        code = normalize_stock_code(symbol)
        remote_source = None
        remote_flow_source = None
        remote_flow_task: asyncio.Task[Any] | None = None
        async with async_session() as session:
            query = select(StockDailyBar).where(StockDailyBar.stock_code == code)
            if as_of:
                query = query.where(StockDailyBar.trade_date <= as_of)
            query = query.order_by(desc(StockDailyBar.trade_date)).limit(400)
            rows = list(reversed((await session.execute(query)).scalars().all()))
            latest = _bar_date(rows[-1]) if rows else as_of
            flow_rows = []
            if latest:
                flow_query = select(StockFundFlowDaily).where(
                    StockFundFlowDaily.stock_code == code,
                    StockFundFlowDaily.trade_date <= latest,
                ).order_by(desc(StockFundFlowDaily.trade_date)).limit(120)
                flow_rows = list(reversed((await session.execute(flow_query)).scalars().all()))
                # Start the public historical flow fallback while the other
                # context work is in flight.  It is used only when the local
                # cache is empty/sparse and is still filtered by the replay
                # cutoff below.
                if len(flow_rows) < 5:
                    remote_flow_task = asyncio.create_task(collector.fetch_stock_fund_flow(code))
            universe = None
            if latest:
                universe = (await session.execute(select(StockUniverseSnapshot).where(StockUniverseSnapshot.stock_code == code, StockUniverseSnapshot.trade_date <= latest).order_by(desc(StockUniverseSnapshot.trade_date)).limit(1))).scalar_one_or_none()
            sector = None
            sector_flow = []
            if universe and universe.industry:
                sector = {"name": universe.industry, "market_cap": universe.market_cap, "data_date": latest.isoformat() if latest else None}
                board_codes = list((await session.execute(select(MarketBoard.code).where(MarketBoard.board_type == "industry", MarketBoard.name == universe.industry))).scalars().all())
                if board_codes:
                    sector_query = select(IndustryFundFlowDaily).where(
                        IndustryFundFlowDaily.board_code.in_(board_codes),
                        IndustryFundFlowDaily.trade_date <= latest,
                    ).order_by(desc(IndustryFundFlowDaily.trade_date)).limit(120)
                    sector_flow = list(reversed((await session.execute(sector_query)).scalars().all()))
            name = (rows[-1].stock_name if rows else None) or (universe.stock_name if universe else None) or code
        if len(rows) < ENGINE_CONFIG["minimum_daily_bars"]:
            # Tencent is a documented fallback for a cold local cache. A
            # partial local cache is enriched in the same way so the engine
            # does not silently downgrade a valid symbol to NO_TRADE.
            try:
                remote = await asyncio.wait_for(collector.fetch_stock_price_history(code, days=365), timeout=18)
                remote_rows = remote.get("history") or []
                if as_of:
                    remote_rows = [row for row in remote_rows if _bar_date(row) and _bar_date(row) <= as_of]
                existing_by_date = {_bar_date(row): row for row in rows if _bar_date(row)}
                for remote_row in remote_rows:
                    remote_date = _bar_date(remote_row)
                    if remote_date and remote_date not in existing_by_date:
                        existing_by_date[remote_date] = remote_row
                rows = [existing_by_date[key] for key in sorted(existing_by_date)]
                name = remote.get("name") or name
                remote_source = remote.get("source") or "tencent"
            except Exception as exc:
                if not rows:
                    if remote_flow_task is not None and not remote_flow_task.done():
                        remote_flow_task.cancel()
                    return {"symbol": code, "name": name, "bars": [], "flow": [], "sector_flow": [], "sector": sector, "quote": None, "quote_is_realtime": False, "source_status": {"daily_bars": "unavailable", "reason": type(exc).__name__}}
        if rows:
            latest = as_of or _bar_date(rows[-1]) or latest
        if remote_flow_task is None and latest is not None and len(flow_rows) < 5:
            remote_flow_task = asyncio.create_task(collector.fetch_stock_fund_flow(code))
        if remote_flow_task is not None:
            try:
                remote_flow = await asyncio.wait_for(remote_flow_task, timeout=10)
                merged_flow = _merge_flow_rows(flow_rows, remote_flow or [], latest=latest)
                if remote_flow:
                    remote_flow_source = "eastmoney_stock_flow"
                    flow_rows = merged_flow
                    await self._cache_stock_flow(code, remote_flow or [], name=name, cutoff=latest)
                elif not flow_rows and merged_flow:
                    remote_flow_source = "eastmoney_stock_flow"
                    flow_rows = merged_flow
            except Exception as exc:
                remote_flow_source = f"fallback_error:{type(exc).__name__}"
        bars = [_normalise_bar(row) for row in rows if _bar_date(row)]
        source = remote_source or (bars[0].get("source") if bars else "stock_daily_bars")
        quote = None
        quote_is_realtime = False
        quote_source = None
        if not as_of:
            try:
                quote_payload = await asyncio.wait_for(collector.fetch_stock_quotes([code]), timeout=8)
                quote = next((item for item in quote_payload.get("stocks") or [] if str(item.get("code")) == code), None)
                quote_is_realtime = bool(quote_payload.get("is_realtime"))
                quote_source = quote_payload.get("source")
            except Exception:
                quote = None
        if quote:
            name = quote.get("name") or name
            if not sector and quote.get("sector"):
                sector = {
                    "name": str(quote["sector"]),
                    "market_cap": quote.get("market_cap"),
                    "data_date": latest.isoformat() if latest else None,
                    "source": quote.get("quote_source") or "quote",
                }
        # A daily close is a valid cached quote outside trading hours.  Keep
        # it explicitly non-realtime so the UI can show a truthful badge while
        # still rendering price, change and risk panels without a blank state.
        if quote is None and bars:
            last_bar = bars[-1]
            previous_bar = bars[-2] if len(bars) > 1 else {}
            quote = {
                "code": code,
                "name": name,
                "price": last_bar.get("close"),
                "change_pct": last_bar.get("change_pct"),
                "previous_close": previous_bar.get("close"),
                "quote_source": "stock_daily_bars_cache",
                "quote_timestamp": None,
            }
            quote_source = "stock_daily_bars_cache"
        return {
            "symbol": code,
            "name": name,
            "bars": bars,
            "flow": [_normalise_flow(row) for row in flow_rows],
            "sector_flow": [_normalise_flow(row) for row in sector_flow],
            "sector": sector,
            "quote": quote,
            "quote_is_realtime": quote_is_realtime,
            "source_status": {
                "daily_bars": "available" if bars else "unavailable",
                "daily_bars_source": source,
                "stock_flow": "available" if flow_rows else "unavailable",
                "stock_flow_source": remote_flow_source or ("stock_fund_flow_daily" if flow_rows else None),
                "stock_flow_rows": len(flow_rows),
                "sector": "available" if sector else "unavailable",
                "sector_flow": "available" if sector_flow else "unavailable",
                "quote": "available" if quote else "unavailable",
                "quote_source": quote_source or (quote.get("quote_source") if quote else None),
            },
        }

    async def _cache_stock_flow(
        self,
        code: str,
        rows: Iterable[Any],
        *,
        name: str,
        cutoff: date | None,
    ) -> None:
        """Persist verified remote flow rows for non-session reads.

        This is deliberately best-effort: a database outage must never turn a
        valid read-only decision into an error.  Existing rows are updated by
        date and no synthetic flow values are written.
        """
        candidates: dict[date, dict[str, Any]] = {}
        for row in rows:
            row_date = _flow_date(row)
            if row_date is None or (cutoff is not None and row_date > cutoff):
                continue
            get = row.get if isinstance(row, dict) else lambda key, default=None: getattr(row, key, default)
            candidates[row_date] = {
                "stock_name": name,
                "trade_date": row_date,
                "main_net_inflow": int(round(_finite(get("main_net_inflow")))) if _finite(get("main_net_inflow")) is not None else None,
                "super_large_net_inflow": int(round(_finite(get("super_large_net_inflow")))) if _finite(get("super_large_net_inflow")) is not None else None,
                "large_net_inflow": int(round(_finite(get("large_net_inflow")))) if _finite(get("large_net_inflow")) is not None else None,
                "medium_net_inflow": int(round(_finite(get("medium_net_inflow")))) if _finite(get("medium_net_inflow")) is not None else None,
                "small_net_inflow": int(round(_finite(get("small_net_inflow")))) if _finite(get("small_net_inflow")) is not None else None,
                "close_price": _finite(get("close_price")),
                "change_pct": _finite(get("change_pct")),
            }
        if not candidates:
            return
        async with self._flow_cache_locks[code]:
            try:
                async with async_session() as session:
                    existing_rows = list((await session.execute(
                        select(StockFundFlowDaily).where(
                            StockFundFlowDaily.stock_code == code,
                            StockFundFlowDaily.trade_date.in_(list(candidates)),
                        )
                    )).scalars().all())
                    existing = {row.trade_date: row for row in existing_rows}
                    fields = (
                        "stock_name", "main_net_inflow", "super_large_net_inflow",
                        "large_net_inflow", "medium_net_inflow", "small_net_inflow",
                        "close_price", "change_pct",
                    )
                    for trade_date, values in candidates.items():
                        target = existing.get(trade_date)
                        if target is None:
                            session.add(StockFundFlowDaily(stock_code=code, **values))
                        else:
                            for field in fields:
                                value = values.get(field)
                                if value is not None:
                                    setattr(target, field, value)
                    await session.commit()
            except Exception as exc:
                print(f"Strong stock flow cache write failed: {type(exc).__name__}")

    def _build(self, context: dict[str, Any], *, persistable: bool = True) -> dict[str, Any]:
        bars = context.get("bars") or []
        features = _series_features(bars)
        features["long_daily_bars"] = features.get("count", 0) >= ENGINE_CONFIG["minimum_daily_bars"]
        features["bars_end"] = bars[-1].get("trade_date").isoformat() if bars and bars[-1].get("trade_date") else None
        qts = _quantity_time_space(features)
        main_force = _main_force(features)
        volume_ma = _volume_price_ma(features)
        zone = _zone(features, qts, main_force)
        composite_score = _decision_score(qts, main_force, volume_ma, zone)
        # Individual-stock flow belongs to the stock pattern layer; sector
        # flow is kept for the independent topic confirmation layer.
        big = _big_patterns(features, qts, context.get("flow") or context.get("sector_flow") or [])
        stars = _stars(features, zone, big, main_force, context.get("sector"))
        sell = _profit_and_sell(features, zone, big, stars, main_force)
        topic = _topic_confirmation(context.get("sector"), context.get("sector_flow") or context.get("flow") or [])
        signals: dict[str, dict[str, Any]] = {}
        # Base skills are emitted even when not matched, making the UI and
        # backtest contract complete and preventing hidden data gaps.
        base_signal_map = {
            "HQS_001": ("量时空提供机会", "CONFIRMED" if qts.get("opportunity", 0) is not None and qts.get("opportunity", 0) >= 65 else "POSSIBLE" if qts.get("opportunity") is not None else "NOT_FOUND", qts.get("opportunity")),
            "HQS_002": ("量时空大压风险", "CONFIRMED" if qts.get("risk", 0) is not None and qts.get("risk", 0) >= 75 else "POSSIBLE" if qts.get("risk") is not None and qts.get("risk") >= 60 else "NOT_FOUND", qts.get("risk")),
            "HQS_003": ("量形态选股", "CONFIRMED" if (features.get("volume_ratio") or 0) >= 1.5 and (features.get("returns5") or 0) > 0 else "POSSIBLE" if features.get("volume_ratio") is not None else "NOT_FOUND", features.get("volume_ratio")),
            "HQS_004": ("量行为跟随主力", "CONFIRMED" if main_force.get("direction") == "偏多" and main_force.get("state") in {"明显", "较明显"} else "POSSIBLE" if main_force.get("direction") != "暂不明确" else "NOT_FOUND", main_force.get("confidence")),
            "HQS_005": ("量价异动", "CONFIRMED" if volume_ma.get("event") == "量价同步异动" else "POSSIBLE" if volume_ma.get("event") in {"量先异动", "价先异动"} else "NOT_FOUND", 65 if volume_ma.get("event") != "无明显异动" and volume_ma.get("event") != "UNKNOWN" else None),
            "HQS_006": ("均线归位", "CONFIRMED" if volume_ma.get("ma_state") in {"均线展开", "均线归位中"} else "POSSIBLE" if volume_ma.get("ma_state") == "均线聚合" else "NOT_FOUND", 65 if volume_ma.get("ma_state") != "UNKNOWN" else None),
            "HQS_007": ("量价异动让均线归位", "CONFIRMED" if volume_ma.get("推动") == "量价异动正在推动均线归位" else "POSSIBLE" if volume_ma.get("event") not in {"UNKNOWN", "无明显异动"} else "NOT_FOUND", 62 if volume_ma.get("推动") != "UNKNOWN" else None),
            "HQS_008": ("强势A区", "CONFIRMED" if zone.get("zone") == "强势A区" else "NOT_FOUND", zone.get("confidence")),
            "HQS_009": ("强势B区", "CONFIRMED" if zone.get("zone") == "强势B区" else "NOT_FOUND", zone.get("confidence")),
            "HQS_010": ("风险C区", "CONFIRMED" if zone.get("zone") == "风险C区" else "NOT_FOUND", zone.get("confidence")),
            "HQS_018": ("量能体叠加术", "NOT_FOUND", None),
            "HQS_019": ("量能体叠加与题材互证", "CONFIRMED" if topic.get("status") == "成立" else "POSSIBLE" if topic.get("status") == "不足" else "NOT_FOUND", 70 if topic.get("status") == "成立" else 45 if topic.get("status") == "不足" else None),
            "HQS_020": ("学习经典案例及交易策略", "POSSIBLE" if bars else "NOT_FOUND", 40 if bars else None),
        }
        for sid, (name, status, confidence) in base_signal_map.items():
            signals[sid] = _pattern(sid, name, status, confidence, [_evidence(f"{name}由当前可观测数据计算", feature="engine_output", value=status)])
        signals.update(big); signals.update(stars); signals.update(sell)
        stack = _stack(signals, topic, zone)
        stack_status = "CONFIRMED" if stack["level"] in {"强", "很强"} else "POSSIBLE" if stack["level"] in {"中", "弱"} else "NOT_FOUND"
        signals["HQS_018"] = _pattern("HQS_018", "量能体叠加术", stack_status, {"很强": 82, "强": 72, "中": 55, "弱": 35}.get(stack["level"]), [_evidence("量时空、主力、量价、交易区、大形态和暴涨之星的叠加结果", feature="stack_level", value=stack["level"])], conflicts=stack.get("conflicts"))
        decision = _state_and_action(zone, big, stars, sell, stack, qts, main_force)
        source_status = dict(context.get("source_status") or {})
        feature_availability = _feature_availability(
            features,
            flow=context.get("flow") or [],
            sector=context.get("sector"),
        )
        feature_availability.update({
            "sector_flow": bool(context.get("sector_flow")),
            "quote": bool(context.get("quote")),
        })
        completeness_parts = list(feature_availability.values())
        completeness = _round(sum(1 for item in completeness_parts if item) / len(completeness_parts) * 100, 1)
        missing_features = [key for key, available in feature_availability.items() if not available]
        trade_date = bars[-1].get("trade_date") if bars else None
        result = {
            "module_id": "STRONG_STOCK_DECISION_V1", "engine_version": ENGINE_VERSION, "mode": "SHADOW",
            "symbol": context.get("symbol"), "name": context.get("name"), "trade_date": trade_date.isoformat() if isinstance(trade_date, date) else trade_date,
            "data_cutoff_time": shanghai_now().replace(tzinfo=None).isoformat(), "data_completeness_pct": completeness,
            "source_status": source_status,
            "quote": context.get("quote"),
            "sector": context.get("sector"),
            "is_realtime": bool(context.get("quote_is_realtime")),
            "cache_used": False,
            "feature_availability": feature_availability,
            "missing_features": missing_features,
            "data_health": {
                "status": "COMPLETE" if not missing_features else "PARTIAL",
                "available_count": sum(1 for item in completeness_parts if item),
                "required_count": len(completeness_parts),
                "missing_features": missing_features,
                "note": "缺失字段保持为空，不以估算值替代；可用数据仍用于对应层级的研究观察。",
            },
            "decision": decision, "composite_score": composite_score, "quantity_time_space": qts, "main_force": main_force, "volume_price_ma": volume_ma,
            "best_trading_zone": zone, "big_patterns": list(big.values()), "rising_stars": list(stars.values()),
            "profit_patterns": [signals[sid] for sid in ("HQS_011", "HQS_012", "HQS_013", "HQS_014")],
            "sell_signals": [signals[sid] for sid in ("HQS_015", "HQS_016", "HQS_017")],
            "volume_energy_stacking": stack, "topic_confirmation": topic,
            "signals": list(signals.values()), "bars": [{**row, "trade_date": row["trade_date"].isoformat() if isinstance(row.get("trade_date"), date) else row.get("trade_date")} for row in bars[-180:]],
            "engine_features": {key: value for key, value in features.items() if key not in {"closes", "opens", "highs", "lows", "volumes", "amounts", "changes"}},
            "terminology_policy": "BOOK_RULE名称保持原书术语；ENGINE_FEATURE仅用于识别，不等同于书中阈值。",
        }
        result["explanation"] = self._explanation(result)
        result["timeline"] = self._timeline(result)
        # V2 is an additive Shadow layer. Keep the legacy payload and its
        # ACTION intact while exposing the richer three-book research result
        # for the new page and the V2 API.
        result["legacy_module_id"] = result.get("module_id")
        result["v2_engine_version"] = V2_ENGINE_VERSION
        result["v2_enabled"] = self._v2_enabled()
        if not self._v2_enabled():
            result["v2"] = self._v2_disabled_envelope()
            result["v2_signals"] = []
        else:
            try:
                v2_context = dict(context)
                v2_context["data_cutoff_time"] = result["data_cutoff_time"]
                v2 = build_v2(v2_context, legacy=result)
                result["v2"] = v2
                result["v2_signals"] = v2.get("signals") or []
            except Exception as exc:
                # A new research layer must never take the existing decision page
                # offline. Return an explicit unavailable envelope for diagnosis.
                result["v2"] = {
                    "module_id": V2_ENGINE_VERSION,
                    "engine_version": V2_ENGINE_VERSION,
                    "mode": "SHADOW",
                    "status": "UNAVAILABLE",
                    "enabled": True,
                    "error_type": type(exc).__name__,
                    "signals": [],
                    "data_quality": {"status": "UNAVAILABLE", "note": "V2计算异常，旧版结果仍可用。"},
                }
                result["v2_signals"] = []
        return result

    @staticmethod
    def _explanation(result: dict[str, Any]) -> dict[str, Any]:
        decision = result.get("decision") or {}
        signals = result.get("signals") or []
        confirmed = [item["name"] for item in signals if item.get("status") == "CONFIRMED"]
        possible = [item["name"] for item in signals if item.get("status") in {"POSSIBLE", "FORMING"}]
        risks = decision.get("risk_skills") or []
        return {
            "当前判断": f"当前属于：{decision.get('state_name', '无明显机会')}；最佳交易区：{(result.get('best_trading_zone') or {}).get('zone', '未形成明确交易区')}；最终ACTION：{decision.get('action', 'NO_TRADE')}（Shadow观察，不是交易指令）",
            "因": [item.get("text") for item in (result.get("quantity_time_space") or {}).get("evidence", [])[:3]] + [item.get("text") for item in (result.get("main_force") or {}).get("evidence", [])[:2]],
            "书中技能互证": {"已成立": confirmed[:12], "疑似或构建": possible[:12], "风险": risks},
            "果": f"当前形成{decision.get('state_name', '未知状态')}，量能体叠加为{(result.get('volume_energy_stacking') or {}).get('level', 'UNKNOWN')}。",
            "下一步": decision.get("next_confirmation") or [],
            "失效": decision.get("invalidation") or [],
            "规则边界": "系统只描述可观测的主力身影、成交、价格和结构，不自由编造主力意图；风险C区和卖出类信号优先于攻击信号。",
        }

    @staticmethod
    def _timeline(result: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for signal in result.get("signals") or []:
            if signal.get("status") in {"POSSIBLE", "FORMING", "CONFIRMED", "WEAKENING", "INVALID"}:
                rows.append({"date": result.get("trade_date"), "type": "SIGNAL", "skill_id": signal.get("skill_id"), "name": signal.get("name"), "status": signal.get("status"), "confidence": signal.get("confidence"), "evidence": signal.get("evidence")})
        rows.sort(key=lambda item: (item.get("date") or "", item.get("skill_id") or ""))
        return rows

    async def evaluate(self, symbol: str, *, as_of: date | None = None, force: bool = False, persist: bool = True) -> dict[str, Any]:
        if not self._enabled():
            return {"module_id": "STRONG_STOCK_DECISION_V1", "enabled": False, "status": "DISABLED", "message": "FEATURE_STRONG_STOCK_DECISION 已关闭"}
        code = normalize_stock_code(symbol)
        cache_key = (code, as_of.isoformat() if as_of else None)
        now = datetime.now().timestamp()
        cached = self._cache.get(cache_key)
        if not force and cached and now - cached[0] < self.cache_seconds:
            output = deepcopy(cached[1]); output["cache_used"] = True; return output
        async with self._locks[code]:
            cached = self._cache.get(cache_key)
            if not force and cached and datetime.now().timestamp() - cached[0] < self.cache_seconds:
                output = deepcopy(cached[1]); output["cache_used"] = True; return output
            context = await self._load_context(code, as_of)
            output = self._build(context)
            self._cache[cache_key] = (datetime.now().timestamp(), deepcopy(output))
            if persist and output.get("trade_date"):
                await self._persist(output)
            return output

    async def _persist(self, result: dict[str, Any]) -> None:
        try:
            trade_date = date.fromisoformat(str(result["trade_date"])[:10])
            async with async_session() as session:
                decision = result["decision"]
                session.add(StrongDecisionState(symbol=result["symbol"], trade_date=trade_date, state_code=decision["state_code"], state_name=decision["state_name"], primary_skill=decision.get("primary_skill"), secondary_skills_json=decision.get("secondary_skills") or [], risk_skills_json=decision.get("risk_skills") or [], action=decision["action"], reason_json=decision.get("reason") or {}, next_confirmation_json=decision.get("next_confirmation") or [], invalidation_json=decision.get("invalidation") or [], mode="SHADOW", engine_version=ENGINE_VERSION))
                mf = result["main_force"]
                session.add(MainForceEvidence(symbol=result["symbol"], trade_date=trade_date, main_force_state=mf.get("state") or "不明显", main_force_direction=mf.get("direction") or "暂不明确", main_force_persistence=mf.get("persistence") or "减弱", volume_pattern=mf.get("volume_pattern"), price_pattern=mf.get("price_pattern"), turnover_pattern=mf.get("turnover_pattern"), evidence_json=mf.get("evidence") or [], confidence=mf.get("confidence")))
                for signal in result.get("signals") or []:
                    session.add(StockSkillSignal(symbol=result["symbol"], trade_date=trade_date, trade_time=datetime.combine(trade_date, datetime.min.time()), skill_id=signal["skill_id"], status=signal["status"], confidence=signal.get("confidence"), evidence_json=signal.get("evidence") or [], invalidation_json=signal.get("invalidation") or [], next_confirmation_json=signal.get("next_confirmation") or [], source_interval="DAILY", engine_version=ENGINE_VERSION))
                    for annotation in signal.get("chart_annotations") or []:
                        if not isinstance(annotation, dict):
                            continue
                        session.add(PatternAnnotation(symbol=result["symbol"], pattern_type=str(annotation.get("type") or signal.get("name")), start_time=datetime.combine(trade_date, datetime.min.time()), end_time=datetime.combine(trade_date, datetime.min.time()), upper_boundary=annotation.get("upper_boundary"), lower_boundary=annotation.get("lower_boundary"), key_price=annotation.get("key_price"), annotation_json=annotation))
                v2 = result.get("v2") or {}
                if not self._v2_enabled() or v2.get("status") in {"DISABLED", "UNAVAILABLE"}:
                    await session.commit()
                    return
                # V2 persistence is additive and intentionally best-effort.
                # These snapshots make the Shadow layer replayable while the
                # legacy tables above remain untouched for existing clients.
                v2_time = datetime.combine(trade_date, datetime.min.time())
                v2_main = v2.get("main_force") or {}
                session.add(MainForceState(
                    symbol=result["symbol"], trade_time=v2_time, timeframe="1d",
                    main_force_presence=v2_main.get("presence") or v2_main.get("state") or "不明显",
                    main_force_direction=v2_main.get("direction") or "暂不明确",
                    main_force_stage=v2_main.get("stage") or "样本不足",
                    main_force_intent=v2_main.get("intent") or "暂不判断",
                    main_force_continuity=v2_main.get("continuity") or "未知",
                    evidence_json=v2_main.get("evidence") or [],
                ))
                v2_zone = v2.get("zones") or {}
                session.add(TradingZoneGeometry(
                    symbol=result["symbol"], trade_time=v2_time,
                    zone=v2_zone.get("zone") or "未形成明确交易区",
                    zone_stage=v2_zone.get("stage") or "UNKNOWN",
                    zone_start=v2_time,
                    zone_upper=v2_zone.get("upper"), zone_lower=v2_zone.get("lower"),
                    short_attack_line=v2_zone.get("short_attack_line"),
                    mid_long_cost_line=v2_zone.get("mid_long_cost_line"),
                    small_a_point=v2_zone.get("small_a_point"),
                    invalidation_price=v2_zone.get("invalidation_price"),
                    geometry_json=v2_zone.get("geometry") or {},
                ))
                for pattern in (v2.get("big_patterns") or []):
                    if pattern.get("status") == "NOT_FOUND":
                        continue
                    annotation = (pattern.get("chart_annotations") or [{}])[0]
                    session.add(BigPatternInstance(
                        symbol=result["symbol"], pattern_skill_id=pattern.get("skill_id") or "UNKNOWN",
                        subtype=pattern.get("subtype") or pattern.get("name"), start_time=v2_time,
                        end_time=v2_time, stage=pattern.get("lifecycle") or pattern.get("status") or "NOT_FOUND",
                        upper_boundary=annotation.get("upper_boundary"), lower_boundary=annotation.get("lower_boundary"),
                        key_price=annotation.get("key_price"), breakout_price=pattern.get("metrics", {}).get("breakout_price"),
                        retest_price=pattern.get("metrics", {}).get("retest_price"), evidence_json=pattern.get("evidence") or [],
                    ))
                for star in (v2.get("stars") or []):
                    if star.get("status") == "NOT_FOUND":
                        continue
                    session.add(StarInstance(
                        symbol=result["symbol"], star_skill_id=star.get("skill_id") or "UNKNOWN", trade_time=v2_time,
                        status=star.get("status") or "NOT_FOUND", pre_context_json={"subtype": star.get("subtype"), "mechanism": star.get("mechanism")},
                        star_body_json=star.get("metrics") or {}, volume_json={"engine_features": star.get("engine_features") or []},
                        ma_json={"zone": v2_zone.get("zone")}, main_force_json={"direction": v2_main.get("direction"), "stage": v2_main.get("stage")},
                        confirmation_json=star.get("next_confirmation") or [], invalidation_json=star.get("invalidation") or [],
                    ))
                degrees = v2.get("three_degree") or {}
                session.add(ThreeDegreeState(
                    symbol=result["symbol"], trade_time=v2_time,
                    thickness_state=(degrees.get("thickness") or {}).get("state") or "未知",
                    strength_state=(degrees.get("strength") or {}).get("state") or "未知",
                    speed_state=(degrees.get("speed") or {}).get("state") or "未知",
                    thickness_evidence_json=(degrees.get("thickness") or {}).get("evidence") or [],
                    strength_evidence_json=(degrees.get("strength") or {}).get("evidence") or [],
                    speed_evidence_json=(degrees.get("speed") or {}).get("evidence") or [],
                ))
                character = v2.get("stock_character") or {}
                session.add(StockCharacterState(
                    symbol=result["symbol"], trade_time=v2_time,
                    character_summary=character.get("summary") or "有效历史样本不足",
                    feature_json=character.get("features") or {},
                    historical_samples=int(character.get("historical_samples") or 0),
                    confidence=character.get("confidence"),
                ))
                theme = v2.get("theme") or {}
                session.add(ThemeState(
                    symbol=result["symbol"], trade_time=v2_time, theme_name=theme.get("theme_name"),
                    theme_type=theme.get("theme_type") or "未知", hotspot_level=theme.get("hotspot_level") or "未知",
                    theme_stage=theme.get("theme_stage") or "未接入", evidence_json=theme.get("evidence") or [],
                ))
                buy = v2.get("buy_point") or {}
                session.add(BuyPointState(
                    symbol=result["symbol"], trade_time=v2_time, buy_level=buy.get("level") or "臆想买点",
                    matched_skills_json=buy.get("matched_skills") or [], missing_evidence_json=buy.get("missing_evidence") or [],
                    counter_evidence_json=buy.get("counter_evidence") or [],
                ))
                sell = v2.get("sell") or {}
                session.add(SellRiskState(
                    symbol=result["symbol"], trade_time=v2_time,
                    obvious_top_state=(sell.get("obvious_top") or {}).get("state") or "NOT_FOUND",
                    meet_top_state=(sell.get("meet_top") or {}).get("state") or "NOT_FOUND",
                    c_zone_state=(sell.get("c_zone") or {}).get("state") or "NOT_FOUND",
                    classic_top_state=(sell.get("classic_top") or {}).get("state") or "NOT_FOUND",
                    risk_evidence_json=(sell.get("signals") or []),
                ))
                consensus = v2.get("consensus") or {}
                session.add(ThreeBooksConsensus(
                    symbol=result["symbol"], trade_time=v2_time,
                    hunter_state_json=consensus.get("hunter") or {}, big_pattern_state_json=consensus.get("big_pattern") or {},
                    star_state_json=consensus.get("star") or {}, consensus_level=consensus.get("level") or "冲突",
                    conflicts_json=consensus.get("conflicts") or [], dominant_signal=consensus.get("dominant_side") or "NEUTRAL",
                ))
                for signal in (v2.get("signals") or []):
                    if signal.get("status") == "NOT_FOUND":
                        continue
                    session.add(StockSkillSignal(
                        symbol=result["symbol"], trade_date=trade_date, trade_time=v2_time,
                        skill_id=signal.get("skill_id") or "UNKNOWN", status=signal.get("status") or "NOT_FOUND",
                        confidence=signal.get("confidence"), evidence_json=signal.get("evidence") or [],
                        invalidation_json=signal.get("invalidation") or [], next_confirmation_json=signal.get("next_confirmation") or [],
                        source_interval="DAILY", engine_version=V2_ENGINE_VERSION,
                    ))
                await session.commit()
        except Exception as exc:
            # A storage problem must not change the read-only calculation.
            print(f"Strong stock decision persistence failed: {type(exc).__name__}")

    async def intraday(self, symbol: str, *, force: bool = False) -> dict[str, Any]:
        result = await self.evaluate(symbol, force=force, persist=False)
        if result.get("status") == "DISABLED":
            return result
        code = normalize_stock_code(symbol)
        try:
            minute = await asyncio.wait_for(collector.fetch_stock_minute_trends(code, days=1), timeout=12)
        except Exception as exc:
            minute = {"bars": [], "source": "unavailable", "warning": type(exc).__name__}
        bars = minute.get("bars") or []
        last = bars[-1] if bars else {}
        attack = result.get("decision", {}).get("primary_skill") in {"突破攻击星线", "反转攻击星线"}
        breakout_confirmed = bool(attack and last.get("close") and result.get("engine_features", {}).get("high20_prior") and last["close"] > result["engine_features"]["high20_prior"])
        events = []
        if breakout_confirmed:
            events.append({"event": "INTRADAY_BREAKOUT", "status": "OBSERVED", "text": "盘中价格暂时确认突破，仍需收盘和板块跟随"})
        elif attack:
            events.append({"event": "INTRADAY_BREAKOUT_FAILED", "status": "WATCH", "text": "盘中尚未确认攻击结构"})
        if result.get("main_force", {}).get("direction") == "偏多":
            events.append({"event": "MAIN_FORCE_STRENGTHENING", "status": "WATCH", "text": "主力身影的成交证据保持，不能推断主力意图"})
        return {"symbol": code, "trade_date": result.get("trade_date"), "source": minute.get("source"), "is_realtime": bool(minute.get("is_realtime")), "data_status": "AVAILABLE" if bars else "INSUFFICIENT_DATA", "latest_bar_at": minute.get("latest_bar_at"), "bar_count": len(bars), "events": events, "bars": bars[-240:], "daily_context": {"state": result.get("decision"), "zone": result.get("best_trading_zone"), "main_force": result.get("main_force")}, "next_confirmation": result.get("decision", {}).get("next_confirmation") or [], "invalidation": result.get("decision", {}).get("invalidation") or []}

    async def timeline(self, symbol: str, *, limit: int = 100) -> dict[str, Any]:
        code = normalize_stock_code(symbol)
        async with async_session() as session:
            rows = list((await session.execute(select(StockSkillSignal).where(StockSkillSignal.symbol == code).order_by(desc(StockSkillSignal.created_at)).limit(max(limit * 3, 100)))).scalars().all())
            states = list((await session.execute(select(StrongDecisionState).where(StrongDecisionState.symbol == code).order_by(desc(StrongDecisionState.created_at)).limit(limit))).scalars().all())
        return {"symbol": code, "signals": [{"skill_id": row.skill_id, "status": row.status, "confidence": row.confidence, "trade_date": row.trade_date.isoformat(), "evidence": row.evidence_json or [], "created_at": row.created_at.isoformat() if row.created_at else None} for row in rows[:limit]], "states": [{"state_code": row.state_code, "state_name": row.state_name, "action": row.action, "trade_date": row.trade_date.isoformat(), "primary_skill": row.primary_skill, "mode": row.mode} for row in states]}

    async def research_history(
        self,
        symbol: str,
        *,
        limit: int = 80,
        start: date | None = None,
        end: date | None = None,
    ) -> dict[str, Any]:
        """Build a causal V2 state timeline from historical daily bars.

        Every point is evaluated with bars and auxiliary flow rows available
        on that date only.  The endpoint is intentionally separate from the
        persisted signal timeline: it remains useful on a fresh deployment,
        and it does not depend on how often a user happened to open a page.
        """
        code = normalize_stock_code(symbol)
        limit = max(1, min(int(limit or 80), 180))
        context = await self._load_context(code, end)
        bars = context.get("bars") or []
        indexed: list[tuple[int, date]] = []
        for index, row in enumerate(bars):
            row_date = _bar_date(row)
            if row_date is None:
                continue
            if start is not None and row_date < start:
                continue
            if end is not None and row_date > end:
                continue
            indexed.append((index, row_date))

        # Keep enough lookback for the long moving averages while returning a
        # bounded payload. If the symbol has fewer bars, expose the latest
        # point with an explicit insufficient-data status instead of hiding it.
        eligible = [item for item in indexed if item[0] + 1 >= ENGINE_CONFIG["minimum_daily_bars"]]
        if not eligible and indexed:
            eligible = [indexed[-1]]
        selected = eligible[-limit:]
        points: list[dict[str, Any]] = []
        pressure_history: list[dict[str, Any]] = []
        main_force_history: list[dict[str, Any]] = []
        evolution: list[dict[str, Any]] = []

        def rows_until(rows: Iterable[Any], cutoff: date) -> list[Any]:
            return [row for row in rows if (_flow_date(row) is not None and _flow_date(row) <= cutoff)]

        for index, cutoff in selected:
            point_context = dict(context)
            point_context["bars"] = bars[: index + 1]
            point_context["flow"] = rows_until(context.get("flow") or [], cutoff)
            point_context["sector_flow"] = rows_until(context.get("sector_flow") or [], cutoff)
            point_context["quote"] = None
            point_context["quote_is_realtime"] = False
            point_context["data_cutoff_time"] = f"{cutoff.isoformat()}T15:00:00"
            point = self._build(point_context, persistable=False)
            v2 = point.get("v2") or {}
            risk = v2.get("risk") or {}
            main_force = v2.get("main_force") or {}
            zones = v2.get("zones") or {}
            active = [
                item.get("skill_id")
                for item in v2.get("signals") or []
                if item.get("status") in {"POSSIBLE", "FORMING", "CONFIRMED"}
            ]
            risk_signals = {item.get("skill_id"): item for item in risk.get("signals") or []}
            pressure_history.append({
                "date": cutoff.isoformat(),
                "overall_score": _round(risk.get("overall_score"), 1),
                "top": _round((risk_signals.get("HQS_RISK_001") or {}).get("confidence"), 1),
                "trend": _round((risk_signals.get("HQS_RISK_002") or {}).get("confidence"), 1),
                "gap": _round((risk_signals.get("HQS_RISK_003") or {}).get("confidence"), 1),
                "crash_origin": _round((risk_signals.get("HQS_RISK_004") or {}).get("confidence"), 1),
            })
            main_force_history.append({
                "date": cutoff.isoformat(),
                "presence": main_force.get("presence"),
                "direction": main_force.get("direction"),
                "stage": main_force.get("stage"),
                "intent": main_force.get("intent"),
                "continuity": main_force.get("continuity"),
                "confidence": _round(main_force.get("confidence"), 1),
            })
            evolution.append({
                "date": cutoff.isoformat(),
                "state_name": point.get("decision", {}).get("state_name"),
                "action": point.get("decision", {}).get("action"),
                "zone": zones.get("zone"),
                "zone_stage": zones.get("stage"),
                "ma_stage": (v2.get("moving_average") or {}).get("stage"),
                "risk_score": _round(risk.get("overall_score"), 1),
                "active_skill_count": len(active),
            })
            points.append({
                "date": cutoff.isoformat(),
                "state_name": point.get("decision", {}).get("state_name"),
                "action": point.get("decision", {}).get("action"),
                "zone": zones.get("zone"),
                "zone_stage": zones.get("stage"),
                "ma_stage": (v2.get("moving_average") or {}).get("stage"),
                "risk_score": _round(risk.get("overall_score"), 1),
                "opportunity_score": _round((v2.get("quantity_time_space") or {}).get("opportunity"), 1),
                "main_force": main_force.get("direction"),
                "main_force_stage": main_force.get("stage"),
                "active_skill_count": len(active),
                "active_skills": active[:12],
            })

        latest_v2 = (self._build({**context, "quote": None, "quote_is_realtime": False}, persistable=False).get("v2") or {}) if bars else {}
        return {
            "symbol": code,
            "name": context.get("name"),
            "status": "AVAILABLE" if points else "INSUFFICIENT_DATA",
            "point_count": len(points),
            "first_date": points[0]["date"] if points else None,
            "last_date": points[-1]["date"] if points else None,
            "points": points,
            "evolution": evolution,
            "pressure_history": pressure_history,
            "main_force_history": main_force_history,
            "latest_pressure_map": (latest_v2.get("risk") or {}).get("pressure_map") or [],
            "data_quality": (latest_v2.get("data_quality") or {"bar_count": len(bars), "status": "INSUFFICIENT_DATA"}),
            "source_status": context.get("source_status") or {},
            "method": "每个日期只使用该日期及之前的日线和资金流；后续数据只在回测结果统计中使用。",
            "note": "历史轨迹用于复盘结构变化，不代表未来收益或交易指令。",
        }

    async def cases(self, symbol: str) -> dict[str, Any]:
        code = normalize_stock_code(symbol)
        async with async_session() as session:
            rows = list((await session.execute(select(StrongCaseLibrary).where(StrongCaseLibrary.symbol == code).order_by(desc(StrongCaseLibrary.end_date)).limit(50))).scalars().all())
        return {"symbol": code, "cases": [{"id": row.id, "book": row.book, "skill_id": row.skill_id, "start_date": row.start_date.isoformat(), "end_date": row.end_date.isoformat(), "case_type": row.case_type, "feature_snapshot": row.feature_snapshot_json or {}, "outcome": row.outcome_json or {}, "notes": row.notes} for row in rows], "note": "V1 默认不把未经标注的历史形态伪装成正例；可通过回放和回测逐步积累案例。"}

    async def case_library_status(self, symbol: str | None = None) -> dict[str, Any]:
        """Return labelled case inventory without fabricating historical labels."""
        async with async_session() as session:
            query = select(StrongCaseLibrary)
            if symbol:
                query = query.where(StrongCaseLibrary.symbol == normalize_stock_code(symbol))
            rows = list((await session.execute(query.order_by(desc(StrongCaseLibrary.end_date)).limit(500))).scalars().all())
        by_type: dict[str, int] = {}
        for row in rows:
            by_type[row.case_type] = by_type.get(row.case_type, 0) + 1
        return {"symbol": normalize_stock_code(symbol) if symbol else None, "status": "AVAILABLE", "total": len(rows), "by_case_type": by_type, "success_cases": by_type.get("SUCCESS", 0) + by_type.get("POSITIVE", 0), "failure_cases": by_type.get("FAILURE", 0) + by_type.get("NEGATIVE", 0), "look_alike_cases": by_type.get("LOOK_ALIKE", 0), "note": "只有人工或规则明确标注的案例才进入成功/失败统计。"}

    async def wang_xing_kong(self, symbol: str, *, as_of: date | None = None) -> dict[str, Any]:
        """Compare the current point-in-time snapshot with labelled cases."""
        result = await self.evaluate(symbol, as_of=as_of, persist=False)
        code = normalize_stock_code(symbol)
        current = result.get("engine_features") or {}
        active_skills = {item.get("skill_id") for item in result.get("signals") or [] if item.get("status") in {"POSSIBLE", "FORMING", "CONFIRMED"}}
        async with async_session() as session:
            rows = list((await session.execute(select(StrongCaseLibrary).order_by(desc(StrongCaseLibrary.end_date)).limit(200))).scalars().all())
        matches: list[dict[str, Any]] = []
        for row in rows:
            snapshot = row.feature_snapshot_json or {}
            comparable: list[float] = []
            for key in ("returns5", "returns20", "volume_ratio", "position120", "ma20"):
                left, right = _finite(current.get(key)), _finite(snapshot.get(key))
                if left is not None and right is not None:
                    comparable.append(max(0.0, 1.0 - min(abs(left - right) / max(abs(right), 1.0), 1.0)))
            skill_match = 1.0 if row.skill_id in active_skills else 0.0
            similarity = (sum(comparable) / len(comparable) * 0.75 + skill_match * 0.25) if comparable else skill_match * 0.25
            matches.append({"id": row.id, "book": row.book, "skill_id": row.skill_id, "symbol": row.symbol, "case_type": row.case_type, "start_date": row.start_date.isoformat(), "end_date": row.end_date.isoformat(), "similarity": _round(similarity * 100, 1), "notes": row.notes, "outcome": row.outcome_json or {}, "feature_snapshot": snapshot})
        matches.sort(key=lambda item: item["similarity"] or 0, reverse=True)
        success_types = {"SUCCESS", "POSITIVE"}
        failure_types = {"FAILURE", "NEGATIVE"}
        success = [item for item in matches if item["case_type"] in success_types][:5]
        failure = [item for item in matches if item["case_type"] in failure_types][:5]
        look_alike = [item for item in matches if item["case_type"] not in (success_types | failure_types)][:5]
        return {"symbol": code, "status": "AVAILABLE" if rows else "NO_LABELLED_CASES", "current_trade_date": result.get("trade_date"), "success_cases": success, "failure_cases": failure, "look_alike_cases": look_alike, "closest_success": success[0] if success else None, "closest_failure": failure[0] if failure else None, "why_similar": ["技能状态有重合", "价格、量能、位置特征接近"], "why_different": ["案例标签、市场阶段和外部环境可能不同"], "note": "历史相似度仅作结构参考，不代表未来重复。"}

    async def backtest(self, symbol: str, *, skill_id: str | None = None, start: date | None = None, end: date | None = None, horizons: list[int] | None = None) -> dict[str, Any]:
        code = normalize_stock_code(symbol)
        definition = skill_definition(skill_id) if skill_id else None
        if skill_id and not definition:
            return {"status": "NOT_FOUND", "skill_id": skill_id}
        context = await self._load_context(code, end)
        bars = context.get("bars") or []
        horizons = [value for value in (horizons or [1, 3, 5, 10, 20]) if value in {1, 3, 5, 10, 20}]
        start = start or (bars[0].get("trade_date") if bars else None)
        end = end or (bars[-1].get("trade_date") if bars else None)
        observations: list[dict[str, Any]] = []
        for index in range(max(60, 1), len(bars)):
            current_date = bars[index].get("trade_date")
            if not isinstance(current_date, date) or (start and current_date < start) or (end and current_date > end):
                continue
            partial = dict(context); partial["bars"] = bars[:index + 1]
            result = self._build(partial, persistable=False)
            # V2 skills are emitted in the additive V2 payload. Looking only
            # at the legacy list used to silently include every date when a
            # V2 skill id was requested, which made the resulting statistics
            # look more precise than the actual signal sample.
            signal = next((item for item in (result.get("v2") or {}).get("signals", []) if item.get("skill_id") == skill_id), None) if skill_id else None
            if skill_id and (signal is None or signal.get("status") not in {"POSSIBLE", "FORMING", "CONFIRMED"}):
                continue
            close = bars[index].get("close")
            outcomes: dict[str, float | None] = {}
            outcome_details: dict[str, dict[str, float | None]] = {}
            for horizon in horizons:
                future_rows = bars[index + 1:index + horizon + 1]
                future_close = bars[index + horizon].get("close") if index + horizon < len(bars) else None
                forward_highs = [_finite(row.get("high")) for row in future_rows]
                forward_lows = [_finite(row.get("low")) for row in future_rows]
                forward_closes = [_finite(row.get("close")) for row in future_rows]
                return_pct = _round(_pct_change(future_close, close)) if future_close is not None else None
                mfe = _round(max(((value / close - 1.0) * 100.0 for value in forward_highs if value is not None), default=None)) if close not in (None, 0) else None
                mae = _round(min(((value / close - 1.0) * 100.0 for value in forward_lows if value is not None), default=None)) if close not in (None, 0) else None
                drawdown = _round(_max_drawdown_pct(forward_closes, close))
                outcomes[f"t_plus_{horizon}"] = return_pct
                outcome_details[f"t_plus_{horizon}"] = {"return": return_pct, "mfe": mfe, "mae": mae, "max_drawdown": drawdown}
            observations.append({"trade_date": current_date.isoformat(), "status": signal.get("status") if signal else result.get("decision", {}).get("state_name"), "skill_id": skill_id, "outcomes": outcomes, "outcome_details": outcome_details})
        metrics: dict[str, Any] = {}
        for horizon in horizons:
            values = [item["outcomes"].get(f"t_plus_{horizon}") for item in observations if item["outcomes"].get(f"t_plus_{horizon}") is not None]
            wins = [value for value in values if value > 0]
            details = [item["outcome_details"].get(f"t_plus_{horizon}") or {} for item in observations]
            positive = [value for value in values if value is not None and value > 0]
            negative = [value for value in values if value is not None and value < 0]
            avg_gain = _avg(positive)
            avg_loss = _avg(negative)
            detail_mfe = [item.get("mfe") for item in details if item.get("mfe") is not None]
            detail_mae = [item.get("mae") for item in details if item.get("mae") is not None]
            detail_drawdown = [item.get("max_drawdown") for item in details if item.get("max_drawdown") is not None]
            metrics[f"t_plus_{horizon}"] = {
                "sample_size": len(values),
                "win_rate": _round(len(wins) / len(values) * 100 if values else None),
                "average_return": _round(_avg(values)),
                "median_return": _round(sorted(values)[len(values) // 2] if values else None),
                "mfe": _round(_avg(detail_mfe)),
                "mae": _round(_avg(detail_mae)),
                "max_drawdown": _round(min(detail_drawdown) if detail_drawdown else None),
                "profit_loss_ratio": _round(avg_gain / abs(avg_loss)) if avg_gain is not None and avg_loss not in (None, 0) else None,
            }

        false_breakout = None
        if skill_id and any(token in skill_id for token in ("BREAK", "BXZX_009", "ATTACK")):
            breakout_rows = [item for item in observations if item["outcomes"].get("t_plus_3") is not None]
            false_rows = [item for item in breakout_rows if (item["outcomes"].get("t_plus_3") or 0) <= 0]
            false_breakout = {
                "sample_size": len(breakout_rows),
                "count": len(false_rows),
                "rate": _round(len(false_rows) / len(breakout_rows) * 100 if breakout_rows else None),
                "definition": "信号后第3个交易日收盘收益不为正，仅作回顾性假突破近似统计。",
            }
        status_counts: dict[str, int] = {}
        for item in observations:
            status = str(item.get("status") or "UNKNOWN")
            status_counts[status] = status_counts.get(status, 0) + 1
        status_total = len(observations)
        return {
            "status": "COMPLETED",
            "mode": "SHADOW",
            "symbol": code,
            "skill_id": skill_id,
            "skill_name": definition["original_name"] if definition else "全链路",
            "book_rule_version": definition["book_rule_version"] if definition else None,
            "engine_version": ENGINE_VERSION,
            "start": start.isoformat() if isinstance(start, date) else None,
            "end": end.isoformat() if isinstance(end, date) else None,
            "metrics": metrics,
            "status_distribution": status_counts,
            "confirmation_rate": _round(status_counts.get("CONFIRMED", 0) / status_total * 100 if status_total else None),
            "failure_rate": _round((status_counts.get("INVALID", 0) + status_counts.get("WEAKENING", 0)) / status_total * 100 if status_total else None),
            "false_breakout": false_breakout,
            "observations": observations[-300:],
            "method": "只使用每个截面之前的日线；未来窗口仅用于结果统计，不进入信号计算。MFE/MAE按未来窗口最高/最低价回顾计算。",
            "promotion": "V2保持SHADOW，未通过样本外验证不得进入ACTIVE；统计值不是未来收益概率。",
            "validation_gate": {"status": "SHADOW_ONLY", "action_impact": "DISABLED_UNTIL_VALIDATED", "minimum_sample_size": 100, "requires_out_of_sample": True},
        }


def _normalise_flow(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        flow_date = row.get("trade_date") or row.get("date")
        return {"trade_date": flow_date.isoformat() if isinstance(flow_date, (date, datetime)) else flow_date, "main_net_inflow": _finite(row.get("main_net_inflow")), "change_pct": _finite(row.get("change_pct")), "source": row.get("source") or "cache"}
    flow_date = getattr(row, "trade_date", None)
    return {"trade_date": flow_date.isoformat() if isinstance(flow_date, (date, datetime)) else flow_date, "main_net_inflow": _finite(getattr(row, "main_net_inflow", None)), "change_pct": _finite(getattr(row, "change_pct", None)), "source": getattr(row, "source", None) or "cache"}


strong_stock_decision_service = StrongStockDecisionService()
