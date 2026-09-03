"""V2 three-book analysis engine.

The engine is deliberately pure: it only consumes a point-in-time context and
returns JSON-compatible research output.  It does not fetch data, write the
database, or change the legacy V1 ACTION.  This makes replay and later
out-of-sample validation possible without introducing look-ahead behaviour.
"""

from __future__ import annotations

from datetime import date, datetime
from math import isfinite
from statistics import mean, pstdev
from typing import Any, Iterable

from .registry import SIGNAL_STATUSES, V2_BOOK_SKILL_DEFINITIONS


V2_ENGINE_VERSION = "STRONG_STOCK_DECISION_V2"
PATTERN_LIFECYCLES = (
    "NOT_FOUND",
    "SEED",
    "FORMING",
    "MATURE",
    "TESTING",
    "BREAKOUT",
    "CONFIRMED",
    "WEAKENING",
    "FAILED",
)


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _round(value: Any, digits: int = 2) -> float | None:
    number = _num(value)
    return round(number, digits) if number is not None else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _avg(values: Iterable[Any]) -> float | None:
    clean = [number for number in (_num(value) for value in values) if number is not None]
    return mean(clean) if clean else None


def _pct(current: Any, previous: Any) -> float | None:
    now, old = _num(current), _num(previous)
    if now is None or old in (None, 0):
        return None
    return (now / old - 1.0) * 100.0


def _date_text(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "")[:10]
    return text or None


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    aliases = {
        "open": "open_price",
        "close": "close_price",
        "high": "high_price",
        "low": "low_price",
        "volume": "volume",
        "amount": "amount",
        "turnover": "turnover",
        "change_pct": "change_pct",
        "trade_date": "trade_date",
    }
    return getattr(row, aliases.get(key, key), default)


def _normalise_bars(raw_bars: Iterable[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_bars or []):
        close = _num(_get(raw, "close"))
        if close is None or close <= 0:
            continue
        trade_date = _date_text(_get(raw, "trade_date")) or f"unknown-{index:04d}"
        rows.append(
            {
                "trade_date": trade_date,
                "open": _num(_get(raw, "open")),
                "close": close,
                "high": _num(_get(raw, "high")),
                "low": _num(_get(raw, "low")),
                "volume": _num(_get(raw, "volume")),
                "amount": _num(_get(raw, "amount")),
                "turnover": _num(_get(raw, "turnover")),
                "change_pct": _num(_get(raw, "change_pct")),
            }
        )
    return rows


def _ma(values: list[float | None], window: int) -> float | None:
    if len(values) < window:
        return None
    return _avg(values[-window:])


def _slope(values: list[float | None], window: int = 5) -> float | None:
    clean = [value for value in values[-window:] if value is not None]
    if len(clean) < 2 or clean[0] == 0:
        return None
    return (clean[-1] / clean[0] - 1.0) * 100.0 / max(len(clean) - 1, 1)


def _shape_arrays(features: dict[str, Any]) -> tuple[list[float | None], list[float | None], str]:
    highs: list[float | None] = []
    lows: list[float | None] = []
    used_proxy = False
    for close, high, low in zip(features["closes"], features["highs"], features["lows"]):
        if high is None and close is not None:
            high = close
            used_proxy = True
        if low is None and close is not None:
            low = close
            used_proxy = True
        highs.append(high)
        lows.append(low)
    return highs, lows, "CLOSE_PROXY" if used_proxy else "OHLC"


def _series_features(bars: list[dict[str, Any]]) -> dict[str, Any]:
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
            change = _pct(row["close"], bars[index - 1]["close"])
        changes.append(change)

    close = closes[-1] if closes else None
    previous_close = closes[-2] if len(closes) > 1 else None
    mas = {f"ma{window}": _ma(closes, window) for window in (5, 10, 20, 30, 60)}
    volume_base = _avg(volumes[-21:-1] if len(volumes) >= 21 else volumes[:-1])
    amount_base = _avg(amounts[-21:-1] if len(amounts) >= 21 else amounts[:-1])
    volume_ratio = volumes[-1] / volume_base if volumes and volumes[-1] is not None and volume_base not in (None, 0) else None
    amount_ratio = amounts[-1] / amount_base if amounts and amounts[-1] is not None and amount_base not in (None, 0) else None
    highs_shaped, lows_shaped, price_basis = _shape_arrays({"closes": closes, "highs": highs, "lows": lows})

    lookback = [value for value in closes[-120:] if value is not None]
    high120 = max(lookback) if lookback else None
    low120 = min(lookback) if lookback else None
    position120 = None
    if close is not None and high120 is not None and low120 is not None and high120 > low120:
        position120 = (close - low120) / (high120 - low120) * 100.0

    high20 = [value for value in highs_shaped[-20:] if value is not None]
    low20 = [value for value in lows_shaped[-20:] if value is not None]
    high20_prior = [value for value in highs_shaped[-21:-1] if value is not None]
    low20_prior = [value for value in lows_shaped[-21:-1] if value is not None]
    high20_value = max(high20) if high20 else None
    low20_value = min(low20) if low20 else None
    close_location = None
    if close is not None and high20_value is not None and low20_value is not None and high20_value > low20_value:
        close_location = (close - low20_value) / (high20_value - low20_value) * 100.0

    last_open, last_high, last_low = opens[-1] if opens else None, highs_shaped[-1] if highs_shaped else None, lows_shaped[-1] if lows_shaped else None
    upper_wick = lower_wick = body_ratio = None
    if all(value is not None for value in (last_open, close, last_high, last_low)) and last_high >= last_low:
        body_high, body_low = max(last_open, close), min(last_open, close)
        span = last_high - last_low
        if span > 0:
            upper_wick = (last_high - body_high) / span * 100.0
            lower_wick = (body_low - last_low) / span * 100.0
            body_ratio = abs(close - last_open) / span * 100.0

    clean_changes = [value for value in changes if value is not None]
    positive_count = sum(1 for value in changes[-20:] if value is not None and value > 0)
    negative_count = sum(1 for value in changes[-20:] if value is not None and value < 0)
    up_volume = sum((volumes[index] or 0) for index, value in enumerate(changes) if value is not None and value > 0)
    down_volume = sum((volumes[index] or 0) for index, value in enumerate(changes) if value is not None and value < 0)
    recent_up_volume = sum((volumes[index] or 0) for index, value in enumerate(changes) if index >= max(0, len(changes) - 20) and value is not None and value > 0)
    recent_down_volume = sum((volumes[index] or 0) for index, value in enumerate(changes) if index >= max(0, len(changes) - 20) and value is not None and value < 0)
    return {
        "count": len(bars),
        "closes": closes,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "volumes": volumes,
        "amounts": amounts,
        "changes": changes,
        "close": close,
        "previous_close": previous_close,
        **mas,
        "ma_slopes": {f"ma{window}": _slope([_ma(closes[:index + 1], window) for index in range(len(closes))], 5) for window in (5, 10, 20, 30, 60)},
        "volume_base": volume_base,
        "amount_base": amount_base,
        "volume_ratio": volume_ratio,
        "amount_ratio": amount_ratio,
        "amount_ratio": amount_ratio,
        "highs_shaped": highs_shaped,
        "lows_shaped": lows_shaped,
        "price_basis": price_basis,
        "high120": high120,
        "low120": low120,
        "position120": position120,
        "high20": high20_value,
        "low20": low20_value,
        "high20_prior": max(high20_prior) if high20_prior else None,
        "low20_prior": min(low20_prior) if low20_prior else None,
        "close_location": close_location,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "body_ratio": body_ratio,
        "returns3": _pct(close, closes[-4]) if len(closes) >= 4 else None,
        "returns5": _pct(close, closes[-6]) if len(closes) >= 6 else None,
        "returns20": _pct(close, closes[-21]) if len(closes) >= 21 else None,
        "volatility20": pstdev([value for value in changes[-20:] if value is not None]) if len([value for value in changes[-20:] if value is not None]) >= 5 else None,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "up_volume": up_volume,
        "down_volume": down_volume,
        "recent_up_volume": recent_up_volume,
        "recent_down_volume": recent_down_volume,
        "bars_end": bars[-1]["trade_date"] if bars else None,
    }


def _evidence(text: str, feature: str | None = None, value: Any = None, evidence_type: str = "ENGINE_FEATURE") -> dict[str, Any]:
    item: dict[str, Any] = {"type": evidence_type, "text": text}
    if feature:
        item["feature"] = feature
    if value is not None:
        item["value"] = _round(value) if isinstance(value, (int, float)) else value
    return item


def _status_for(score: float | None, *, confirmed: float = 72, forming: float = 48, possible: float = 28) -> str:
    if score is None:
        return "NOT_FOUND"
    if score >= confirmed:
        return "CONFIRMED"
    if score >= forming:
        return "FORMING"
    if score >= possible:
        return "POSSIBLE"
    return "NOT_FOUND"


def _lifecycle(status: str, explicit: str | None = None) -> str:
    if explicit in PATTERN_LIFECYCLES:
        return explicit
    return {
        "NOT_FOUND": "NOT_FOUND",
        "POSSIBLE": "SEED",
        "FORMING": "FORMING",
        "CONFIRMED": "CONFIRMED",
        "WEAKENING": "WEAKENING",
        "INVALID": "FAILED",
    }.get(status, "FORMING")


def _definition(skill_id: str) -> dict[str, Any]:
    for item in V2_BOOK_SKILL_DEFINITIONS:
        if item["skill_id"] == skill_id:
            return item
    return {
        "skill_id": skill_id,
        "parent_skill_id": None,
        "book": "工程扩展",
        "chapter": "未分类",
        "section": skill_id,
        "original_name": skill_id,
        "knowledge_layer": "ENGINE_FEATURE",
        "description": "工程扩展观察信号。",
        "required_features": [],
        "chart_annotations": [],
    }


def _signal(
    skill_id: str,
    status: str = "NOT_FOUND",
    confidence: float | None = None,
    *,
    subtype: str | None = None,
    lifecycle: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
    counter_evidence: list[dict[str, Any]] | None = None,
    next_confirmation: list[str] | None = None,
    invalidation: list[str] | None = None,
    conflicts: list[str] | None = None,
    chart_annotations: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
    mechanism: str | None = None,
) -> dict[str, Any]:
    definition = _definition(skill_id)
    if status not in SIGNAL_STATUSES:
        status = "NOT_FOUND"
    return {
        "skill_id": skill_id,
        "parent_skill_id": definition.get("parent_skill_id"),
        "original_name": definition.get("original_name") or skill_id,
        "name": definition.get("original_name") or skill_id,
        "book": definition.get("book"),
        "chapter": definition.get("chapter"),
        "section": definition.get("section"),
        "knowledge_layer": definition.get("knowledge_layer", "BOOK_RULE"),
        "status": status,
        "lifecycle": _lifecycle(status, lifecycle),
        "subtype": subtype or definition.get("original_name"),
        "confidence": _round(_clamp(confidence), 1) if confidence is not None else None,
        "mechanism": mechanism or definition.get("description"),
        "forming_mechanism": mechanism or definition.get("description"),
        "evidence": evidence or [],
        "counter_evidence": counter_evidence or [],
        "prerequisites": definition.get("required_features") or [],
        "next_confirmation": next_confirmation or [],
        "invalidation": invalidation or [],
        "conflicts": conflicts or [],
        "chart_annotations": chart_annotations or [],
        "engine_features": definition.get("required_features") or [],
        "source_case_ids": [],
        "metrics": metrics or {},
        "engine_version": V2_ENGINE_VERSION,
    }


def _safe_status_signal(skill_id: str, score: float | None, **kwargs: Any) -> dict[str, Any]:
    return _signal(skill_id, _status_for(score), score, **kwargs)


def _risk(features: dict[str, Any]) -> dict[str, Any]:
    count = features["count"]
    if count < 20:
        return {
            "overall_score": None,
            "overall_state": "数据不足",
            "priority": "UNKNOWN",
            "chart_risk_score": None,
            "systemic_risk": {"state": "未接入", "scope": "背景许可，不冒充三书技术结论"},
            "investor_risk": {"state": "纪律自检", "items": ["是否追涨", "是否只看单一指标", "是否预设失效"]},
            "signals": [_signal(skill_id, evidence=[_evidence("至少需要20根日线才能计算量时空大压", "daily_bars", count)]) for skill_id in ("HQS_RISK_001", "HQS_RISK_002", "HQS_RISK_003", "HQS_RISK_004")],
            "pressure_map": [],
            "note": "风险层不以缺失数据替换为低风险。",
        }

    close = features["close"]
    high120, low120 = features["high120"], features["low120"]
    position = features["position120"]
    volume_ratio = features["volume_ratio"] or 0
    return5, return20 = features["returns5"] or 0, features["returns20"] or 0
    ma20_slope = features["ma_slopes"].get("ma20")
    ma60_slope = features["ma_slopes"].get("ma60")
    upper_wick = features["upper_wick"] or 0
    top_distance = ((high120 - close) / close * 100.0) if close and high120 else None
    top_score = 0.0
    if position is not None:
        top_score += _clamp((position - 68) * 1.5, 0, 28)
    if top_distance is not None and top_distance < 8:
        top_score += 20
    if volume_ratio >= 1.3:
        top_score += 15
    if upper_wick >= 25:
        top_score += 18
    if return5 < 0:
        top_score += 10

    recent_highs = [value for value in features["highs_shaped"][-12:] if value is not None]
    prior_highs = [value for value in features["highs_shaped"][-24:-12] if value is not None]
    recent_lows = [value for value in features["lows_shaped"][-12:] if value is not None]
    prior_lows = [value for value in features["lows_shaped"][-24:-12] if value is not None]
    high_down = bool(recent_highs and prior_highs and max(recent_highs) < max(prior_highs) * 0.995)
    low_down = bool(recent_lows and prior_lows and max(recent_lows) < max(prior_lows) * 0.995)
    failed_rebound = return20 > 0 and return5 < -2
    trend_score = (30 if high_down else 0) + (25 if low_down else 0) + (20 if (ma20_slope or 0) < 0 else 0) + (15 if close < (features["ma20"] or close) else 0) + (10 if failed_rebound else 0)

    gap_index = None
    for index in range(len(features["closes"]) - 1, max(0, len(features["closes"]) - 61), -1):
        if index <= 0:
            continue
        opening = features["opens"][index]
        previous = features["closes"][index - 1]
        if opening is not None and previous and opening < previous * 0.98:
            ratio = features["volumes"][index] / (features["volume_base"] or features["volumes"][index] or 1)
            if ratio >= 1.25:
                gap_index = index
                break
    gap_score = 0.0
    gap_info: dict[str, Any] = {}
    if gap_index is not None:
        opening, previous, current = features["opens"][gap_index], features["closes"][gap_index - 1], features["closes"][-1]
        gap_pct = _pct(opening, previous)
        recovered = current is not None and opening is not None and current >= opening
        gap_score = 75 if not recovered else 35
        gap_info = {"event_date": _date_text(features["bars_end"]) if gap_index == len(features["closes"]) - 1 else None, "gap_pct": _round(gap_pct), "recovered": recovered}

    crash_index = None
    for index, change in enumerate(features["changes"]):
        if index >= 5 and change is not None and change <= -7:
            crash_index = index
    crash_score = 0.0
    crash_info: dict[str, Any] = {}
    if crash_index is not None:
        platform = [value for value in features["highs_shaped"][max(0, crash_index - 5):crash_index] if value is not None]
        origin = max(platform) if platform else features["closes"][crash_index - 1]
        distance = abs(close - origin) / close * 100.0 if close and origin else None
        if distance is not None and distance <= 15:
            crash_score = 72
        elif distance is not None and distance <= 28:
            crash_score = 45
        crash_info = {"crash_date": _date_text(features["bars_end"]) if crash_index == len(features["closes"]) - 1 else None, "origin_price": _round(origin), "distance_pct": _round(distance), "platform_high": _round(max(platform) if platform else None)}

    scores = {
        "HQS_RISK_001": _clamp(top_score),
        "HQS_RISK_002": _clamp(trend_score),
        "HQS_RISK_003": _clamp(gap_score),
        "HQS_RISK_004": _clamp(crash_score),
    }
    descriptions = {
        "HQS_RISK_001": "当前价格接近历史高位/成交密集区，价格响应出现顶部压力证据。",
        "HQS_RISK_002": "高低点、均线斜率或反弹失败显示趋势压力正在形成。",
        "HQS_RISK_003": "近期出现放量向下跳空，且缺口尚未完成收复。",
        "HQS_RISK_004": "历史暴跌前平台再次接近，作为供给边界观察。",
    }
    signals: list[dict[str, Any]] = []
    for skill_id, score in scores.items():
        evidence = [_evidence(descriptions[skill_id], "pressure_score", score)]
        if skill_id == "HQS_RISK_001":
            evidence.extend([_evidence("近120日位置", "position120", position), _evidence("距历史高位", "distance_to_high_pct", top_distance), _evidence("形态价格口径", "price_basis", features["price_basis"])])
            annotation = {"type": "历史顶部/成交密集区", "key_price": high120, "price_basis": features["price_basis"]}
        elif skill_id == "HQS_RISK_002":
            evidence.extend([_evidence("高点下降", "lower_highs", high_down), _evidence("低点下降", "lower_lows", low_down), _evidence("MA20斜率", "ma20_slope", ma20_slope)])
            annotation = {"type": "趋势压力", "key_price": features["ma20"]}
        elif skill_id == "HQS_RISK_003":
            evidence.extend([_evidence("向下缺口", "gap", bool(gap_index)), _evidence("缺口信息", "gap_info", gap_info)])
            annotation = {"type": "巨量下跳缺口", **gap_info}
        else:
            evidence.extend([_evidence("历史暴跌起始区", "crash_origin", crash_info.get("origin_price")), _evidence("当前距起始区", "origin_distance_pct", crash_info.get("distance_pct"))])
            annotation = {"type": "前暴跌起始区", **crash_info}
        signals.append(_signal(skill_id, _status_for(score), score, evidence=evidence, counter_evidence=[_evidence("若价格有效收复压力且成交/宽度跟随，压力状态降级", "counter_condition")], next_confirmation=["观察下一交易日价格是否继续受压", "确认成交与价格是否同向响应"], invalidation=["有效突破并保持关键压力位", "反证持续占据主导"], chart_annotations=[annotation], metrics={"pressure_score": _round(score), "price_basis": features["price_basis"]}))
    chart_score = max(scores.values()) if scores else None
    state = "高" if chart_score is not None and chart_score >= 72 else "中" if chart_score is not None and chart_score >= 45 else "低"
    pressure_map = [{"type": "历史顶部", "price": high120, "distance_pct": _round(top_distance)}, {"type": "前暴跌起始区", **crash_info}]
    return {
        "overall_score": _round(chart_score, 1),
        "overall_state": state,
        "priority": "RISK" if chart_score is not None and chart_score >= 72 else "WATCH",
        "chart_risk_score": _round(chart_score, 1),
        "systemic_risk": {"state": "未接入", "scope": "大势风险需由市场模块提供；本层不虚构政策/经济判断"},
        "investor_risk": {"state": "纪律自检", "items": ["追涨冲动", "只看单一指标", "不看位置", "不设失效", "频繁改计划"]},
        "signals": signals,
        "pressure_map": pressure_map,
        "metrics": {"position120": _round(position), "return5": _round(return5), "return20": _round(return20), "ma20_slope": _round(ma20_slope), "ma60_slope": _round(ma60_slope), "volume_ratio": _round(volume_ratio), "price_basis": features["price_basis"]},
        "note": "风险优先级高于攻击类信号；工程分数不是收益概率。",
    }


def _quantity_time_space(features: dict[str, Any], risk: dict[str, Any]) -> dict[str, Any]:
    if features["count"] < 20:
        return {"status": "INSUFFICIENT_DATA", "opportunity": None, "risk": None, "time": {"state": "未知", "evidence": []}, "space": {"state": "未知", "evidence": []}, "quantity": {"state": "未知", "evidence": []}, "risk_sources": ["日线样本不足"]}
    ret20, ret5, position, volume_ratio = features["returns20"], features["returns5"], features["position120"], features["volume_ratio"]
    time_state = "盛势" if ret20 is not None and ret20 > 25 else "顺势" if ret20 is not None and ret20 > 8 else "分势" if ret5 is not None and ret5 < -4 else "蓄势"
    space_state = "高位" if position is not None and position >= 80 else "中位" if position is not None and position >= 35 else "低位"
    quantity_state = "放量" if volume_ratio is not None and volume_ratio >= 1.5 else "温和" if volume_ratio is not None and volume_ratio >= 0.85 else "缩量" if volume_ratio is not None else "未知"
    opportunity = 50.0 + (18 if ret20 is not None and ret20 > 8 else 0) + (12 if volume_ratio is not None and 1 <= volume_ratio <= 2.5 else 0) + (8 if features["close"] >= (features["ma20"] or features["close"]) else 0)
    pressure = risk.get("overall_score")
    risk_score = 35.0 + (18 if ret5 is not None and ret5 < -5 else 0) + (15 if space_state == "高位" else 0) + (pressure or 0) * 0.35
    return {
        "status": "AVAILABLE",
        "opportunity": _round(_clamp(opportunity), 1),
        "risk": _round(_clamp(risk_score), 1),
        "time": {"state": time_state, "evidence": [_evidence("近20日价格推进", "returns20", ret20), _evidence("近5日节奏", "returns5", ret5)]},
        "space": {"state": space_state, "evidence": [_evidence("近120日价格位置", "position120", position), _evidence("距离历史高位", "distance_to_high_pct", ((features["high120"] - features["close"]) / features["close"] * 100) if features["high120"] and features["close"] else None)]},
        "quantity": {"state": quantity_state, "evidence": [_evidence("成交量相对基线", "volume_ratio", volume_ratio), _evidence("成交额相对基线", "amount_ratio", features["amount_ratio"])]},
        "risk_sources": [item["original_name"] for item in risk.get("signals", []) if item.get("status") in {"POSSIBLE", "FORMING", "CONFIRMED"}],
        "metrics": {"returns5": _round(ret5), "returns20": _round(ret20), "position120": _round(position), "volume_ratio": _round(volume_ratio), "price_basis": features["price_basis"]},
    }


def _main_force(features: dict[str, Any]) -> dict[str, Any]:
    if features["count"] < 20:
        return {"presence": "不明显", "state": "不明显", "direction": "暂不明确", "stage": "样本不足", "intent": "暂不判断", "intent_subtype": None, "continuity": "未知", "confidence": None, "evidence": [_evidence("日线样本不足，不能还原主力身影", "daily_bars", features["count"])], "counter_evidence": [], "behavior_path": [], "three_yin_three_yang": {}, "volume_pattern": "UNKNOWN", "price_pattern": "UNKNOWN", "turnover_pattern": "UNKNOWN"}
    up, down = features["recent_up_volume"], features["recent_down_volume"]
    ratio = up / down if down else (2.0 if up else 0.0)
    ret5, ret20 = features["returns5"] or 0, features["returns20"] or 0
    vr = features["volume_ratio"] or 0
    close, ma20 = features["close"], features["ma20"]
    position = features["position120"] or 50
    upper = features["upper_wick"] or 0
    support = close is not None and ma20 is not None and close >= ma20 * 0.96
    high_position_distribution = position >= 72 and vr >= 1.35 and (ret5 <= 1 or upper >= 28)
    low_mid_accumulation = position < 72 and ratio > 1.05 and ret20 > 0 and support
    probe = bool(features["high20_prior"] and close and close >= features["high20_prior"] * 0.97 and vr >= 1.2)
    if high_position_distribution:
        intent, intent_subtype = "拉高出货", None
    elif low_mid_accumulation:
        intent, intent_subtype = "拉高建仓", None
    elif probe:
        intent = "拉高试盘"
        intent_subtype = "正式拉升前试盘" if support and ratio >= 1.0 else "建仓可行性试盘"
    else:
        intent, intent_subtype = "暂不判断", None
    direction = "偏多" if ratio > 1.12 and ret5 >= -1 else "偏空" if ratio < 0.82 or ret5 < -6 else "暂不明确"
    presence = "明显" if (ratio > 1.4 and ret5 > 0) or low_mid_accumulation else "较明显" if ratio > 1.1 else "中性"
    negative_release = (features["negative_count"] >= 3 and down > 0 and (features["recent_down_volume"] / max(up, 1)) < 1.25)
    contraction = len(features["volumes"]) >= 4 and (_avg(features["volumes"][-3:]) or 0) < (_avg(features["volumes"][-8:-3]) or _avg(features["volumes"][-3:]) or 1) * 0.85
    attack = vr >= 1.25 and ret5 > 0 and close is not None and ma20 is not None and close >= ma20
    if attack:
        stage = "重新攻击"
    elif contraction and support:
        stage = "缩量承接"
    elif negative_release:
        stage = "负能量释放"
    elif ret5 < -3:
        stage = "主力压价观察"
    else:
        stage = "横盘/蓄势"
    confidence = _clamp(42 + abs(ratio - 1) * 28 + (10 if support else 0) + (8 if vr >= 1.2 else 0))
    path = [
        {"step": "主力压价", "status": "OBSERVED" if ret5 < -3 else "WATCH", "evidence": ["价格短期回撤"]},
        {"step": "成交量是否缩小", "status": "OBSERVED" if contraction else "WATCH", "evidence": [f"缩量={contraction}"]},
        {"step": "负能量是否释放", "status": "OBSERVED" if negative_release else "WATCH", "evidence": [f"阴量结构={features['negative_count']}天"]},
        {"step": "是否再次带量拉升", "status": "OBSERVED" if attack else "WATCH", "evidence": [f"量比={_round(vr)}"]},
    ]
    return {
        "presence": presence,
        "state": presence,
        "direction": direction,
        "stage": stage,
        "intent": intent,
        "intent_subtype": intent_subtype,
        "continuity": "增强" if attack else "持续" if ratio > 1 else "减弱",
        "confidence": _round(confidence, 1),
        "evidence": [_evidence("上涨日与下跌日成交量结构", "up_down_volume_ratio", ratio), _evidence("价格对成交变化的反馈", "returns5", ret5), _evidence("成交量相对基线", "volume_ratio", vr), _evidence("关键位置承接", "support", support)],
        "counter_evidence": [_evidence("高位放量但价格推进不足，需防价格强于底层势", "high_position_distribution", high_position_distribution), _evidence("成交结构不能单独证明参与者身份", "observability_boundary", True)],
        "behavior_path": path,
        "three_yin_three_yang": {"positive_count": sum(1 for value in features["changes"][-6:-3] if value is not None and value > 0), "negative_count": sum(1 for value in features["changes"][-3:] if value is not None and value < 0), "up_volume": _round(up), "down_volume": _round(down), "price_damage": _round(ret5)},
        "volume_pattern": "阳量/阴量对照",
        "price_pattern": "价格响应与低点回收",
        "turnover_pattern": "成交量连续性",
        "metrics": {"up_down_volume_ratio": _round(ratio), "support": support, "negative_release": negative_release, "contraction": contraction, "attack": attack},
    }


def _volume_price(features: dict[str, Any]) -> dict[str, Any]:
    if features["count"] < 20:
        return {"status": "INSUFFICIENT_DATA", "volume": {}, "price": {}, "synchronised": {}, "persistence": {}, "failure": {}, "explanation": "日线样本不足"}
    changes = features["changes"]
    latest_change = changes[-1] if changes else None
    volume_ratio = features["volume_ratio"]
    volume_score = _clamp((volume_ratio - 1) * 55) if volume_ratio is not None else None
    price_score = _clamp(abs(latest_change or 0) * 18) if latest_change is not None else None
    sync_score = min(volume_score or 0, price_score or 0) + (18 if (volume_ratio or 0) >= 1.5 and abs(latest_change or 0) >= 2 else 0)
    volume_event = "量异动" if volume_score is not None and volume_score >= 45 else "量正常" if volume_score is not None else "未知"
    price_event = "价异动" if price_score is not None and price_score >= 36 else "价正常" if price_score is not None else "未知"
    sync_event = "量价同步异动" if sync_score >= 65 else "量价尚未同步"
    abnormal_indices = [index for index, value in enumerate(features["volumes"]) if value is not None and features["volume_base"] not in (None, 0) and value / features["volume_base"] >= 1.5]
    start_index = abnormal_indices[-1] if abnormal_indices else None
    if start_index is not None:
        while start_index > 0:
            prior = features["volumes"][start_index - 1]
            if prior is None or prior / (features["volume_base"] or prior or 1) < 1.5:
                break
            start_index -= 1
    persistence_days = len(features["volumes"]) - start_index if start_index is not None else 0
    failure = bool(volume_ratio and volume_ratio >= 1.5 and (latest_change or 0) < 0)
    evidence = [_evidence("成交量相对历史基线", "volume_ratio", volume_ratio), _evidence("最新价格变化", "change_pct", latest_change), _evidence("异动持续天数", "persistence_days", persistence_days)]
    return {
        "status": "AVAILABLE",
        "volume": {"status": _status_for(volume_score), "event": volume_event, "baseline": _round(features["volume_base"]), "ratio": _round(volume_ratio), "amplitude": _round((volume_ratio - 1) * 100 if volume_ratio is not None else None), "start_date": features["bars_end"] if start_index == len(features["volumes"]) - 1 else None},
        "price": {"status": _status_for(price_score), "event": price_event, "baseline": "前一收盘/20日价格重心", "amplitude": _round(latest_change)},
        "synchronised": {"status": _status_for(sync_score), "event": sync_event, "price_response": "跟随" if (latest_change or 0) > 0 and (volume_ratio or 0) >= 1.5 else "不协调" if failure else "未定"},
        "persistence": {"days": persistence_days, "status": "持续" if persistence_days >= 2 else "单日" if persistence_days else "未观察"},
        "failure": {"status": "FORMING" if failure else "NOT_FOUND", "reason": "放量但价格未跟随" if failure else "暂无异动失败证据"},
        "explanation": "量先发生变化，价格重心开始响应，但需继续观察持续性。" if volume_event == "量异动" and price_event != "价异动" else "量与价在同一窗口发生明显变化。" if sync_event == "量价同步异动" else "当前未形成可确认的量价同步异动。",
        "evidence": evidence,
        "metrics": {"volume_score": _round(volume_score), "price_score": _round(price_score), "sync_score": _round(sync_score), "price_basis": features["price_basis"]},
    }


def _ma_stage(features: dict[str, Any]) -> str:
    values = [features.get(f"ma{window}") for window in (5, 10, 20, 30, 60)]
    if any(value is None for value in values):
        return "数据不足"
    close = features["close"] or 0
    spread = (max(values) - min(values)) / close * 100 if close else 0
    slopes = [features["ma_slopes"].get(f"ma{window}") or 0 for window in (5, 10, 20, 30, 60)]
    bullish = values[0] > values[1] > values[2] > values[3] > values[4]
    bearish = values[0] < values[1] < values[2] < values[3] < values[4]
    prior_values = []
    for window in (5, 10, 20, 30, 60):
        series = [_ma(features["closes"][:index + 1], window) for index in range(len(features["closes"]))]
        prior_values.append(series[-2] if len(series) >= 2 else None)
    cross = all(value is not None for value in prior_values) and ((prior_values[0] <= prior_values[1] and values[0] > values[1]) or (prior_values[0] >= prior_values[1] and values[0] < values[1]))
    prior_slope = _avg([_slope([_ma(features["closes"][:index + 1], window) for index in range(len(features["closes"]) - 6)], 5) for window in (5, 10, 20)])
    current_slope = _avg(slopes[:3])
    if spread <= 2.5:
        return "均线密集"
    if cross:
        return "均线穿越"
    if prior_slope is not None and current_slope is not None and prior_slope < 0 <= current_slope:
        return "均线翘头"
    if bullish and current_slope is not None and current_slope > 0 and spread > 4:
        return "均线发散"
    if bullish and all(slope > 0 for slope in slopes):
        return "均线顺畅"
    if bearish and all(slope < 0 for slope in slopes):
        return "均线错位"
    return "均线归位中"


def _moving_average(features: dict[str, Any]) -> dict[str, Any]:
    stage = _ma_stage(features)
    values = {f"ma{window}": _round(features.get(f"ma{window}")) for window in (5, 10, 20, 30, 60)}
    close = features["close"]
    spread = None
    clean = [value for value in values.values() if value is not None]
    if clean and close:
        spread = (max(clean) - min(clean)) / close * 100
    stage_score = {"均线密集": 48, "均线穿越": 58, "均线翘头": 68, "均线发散": 78, "均线顺畅": 88}.get(stage)
    slope_state = "向上" if (features["ma_slopes"].get("ma20") or 0) > 0 else "向下" if (features["ma_slopes"].get("ma20") or 0) < 0 else "走平"
    evolution: list[dict[str, Any]] = []
    start = max(20, features["count"] - 7)
    for index in range(start, features["count"]):
        snapshot = {**features, "closes": features["closes"][:index + 1], "count": index + 1}
        snapshot.update({f"ma{window}": _ma(snapshot["closes"], window) for window in (5, 10, 20, 30, 60)})
        snapshot["ma_slopes"] = {f"ma{window}": _slope([_ma(snapshot["closes"][:idx + 1], window) for idx in range(len(snapshot["closes"]))], 5) for window in (5, 10, 20, 30, 60)}
        evolution.append({"date": features["bars_end"] if index == features["count"] - 1 else None, "stage": _ma_stage(snapshot)})
    reverse = "顺畅 → 发散收窄 → 翘头失效 → 穿越 → 错位" if stage in {"均线错位", "均线穿越"} else "尚未观察到明确反向退化"
    signals = []
    for skill_id, name in (("BXDT_MA_001", "均线密集"), ("BXDT_MA_002", "均线穿越"), ("BXDT_MA_003", "均线翘头"), ("BXDT_MA_004", "均线发散"), ("BXDT_MA_005", "均线顺畅")):
        status = "CONFIRMED" if stage == name else "POSSIBLE" if stage in {"均线归位中", "均线穿越"} and name in {"均线穿越", "均线翘头"} else "NOT_FOUND"
        signals.append(_signal(skill_id, status, stage_score if stage == name else 38 if status == "POSSIBLE" else None, evidence=[_evidence(f"当前均线阶段为{stage}", "ma_stage", stage), _evidence("均线距离", "ma_spread_pct", spread), _evidence("MA20斜率", "ma20_slope", features["ma_slopes"].get("ma20"))], counter_evidence=[_evidence("均线交叉本身不等于强势结点", "cross_is_not_node", True)], next_confirmation=["观察排列、角度和距离是否同步保持"], invalidation=["短中长期均线重新错位", "价格跌破关键成本线"], chart_annotations=[{"type": name, "key_price": features.get("ma20")}], metrics={"ma_spread_pct": _round(spread), "slope_state": slope_state}))
    return {"status": "AVAILABLE" if stage != "数据不足" else "INSUFFICIENT_DATA", "stage": stage, "phase": stage, "slope_state": slope_state, "distance_pct": _round(spread), "values": values, "evolution": evolution, "reverse_degradation": reverse, "signals": signals, "metrics": {"ma_slopes": {key: _round(value, 3) for key, value in features["ma_slopes"].items()}, "price_basis": features["price_basis"]}}


def _zones(features: dict[str, Any], risk: dict[str, Any], main_force: dict[str, Any], ma: dict[str, Any]) -> dict[str, Any]:
    close = features.get("close")
    ma5, ma10, ma20, ma60 = (features.get(f"ma{window}") for window in (5, 10, 20, 60))
    ret20, ret5, pressure = features["returns20"] or 0, features["returns5"] or 0, risk.get("overall_score") or 0
    bull_order = all(value is not None for value in (close, ma5, ma10, ma20, ma60)) and close > ma5 > ma10 > ma20 > ma60
    if close is None or ma20 is None:
        zone, stage = "未形成明确交易区", "UNKNOWN"
    elif close < ma20 or pressure >= 78 or ret5 < -15:
        zone, stage = "风险C区", "C_DEEPENING" if ret5 < -6 else "C_WARNING"
    elif bull_order and ret20 > 8 and pressure < 65:
        zone, stage = "强势A区", "A_LATE" if (features["position120"] or 0) >= 85 else "A_ACTIVE"
    elif close >= ma20 * 0.96 and ret20 >= 0:
        zone, stage = "强势B区", "B_REATTACK" if ret5 > 1 and (features["volume_ratio"] or 0) >= 1.15 else "B_SMALL_A_FORMING" if ret5 >= -4 else "B_ACTIVE"
    else:
        zone, stage = "未形成明确交易区", "UNKNOWN"
    recent_high = max([value for value in features["highs_shaped"][-20:] if value is not None], default=None)
    recent_low = min([value for value in features["lows_shaped"][-20:] if value is not None], default=None)
    short_line = ma10 or ma5
    cost_line = ma60 or ma20
    small_a = close if zone == "强势B区" and stage == "B_SMALL_A_FORMING" else None
    zone_start = features["bars_end"] if zone != "未形成明确交易区" else None
    reasons = {
        "强势A区": ["股价与均线呈顺上排列", "近20日价格推进为正", "量时空压力未占主导"],
        "强势B区": ["关键结构尚未完全破坏", "回调或重新转强正在观察"],
        "风险C区": ["趋势、位置或回撤压力占主导", "风险C区优先于攻击类信号"],
        "未形成明确交易区": ["现有证据不足以定义A/B/C区"],
    }[zone]
    zone_signals = []
    for skill_id, name in (("HQS_008", "强势A区"), ("HQS_009", "强势B区"), ("HQS_010", "风险C区")):
        active = zone == name
        zone_signals.append(_signal(skill_id, "CONFIRMED" if active else "NOT_FOUND", 82 if active else None, subtype=stage if active else None, evidence=[_evidence(f"当前区域：{zone}", "zone", zone), _evidence("区域阶段", "zone_stage", stage)], next_confirmation=["成交和价格继续同向", "板块/题材出现互证"], invalidation=["价格跌破关键结构", "成交放大但价格不能推进"], chart_annotations=[{"type": name, "upper_boundary": recent_high, "lower_boundary": recent_low, "short_attack_line": short_line, "mid_long_cost_line": cost_line, "small_a_point": small_a}], metrics={"zone_stage": stage, "upper": _round(recent_high), "lower": _round(recent_low)}))
    return {
        "zone": zone,
        "stage": stage,
        "zone_start": zone_start,
        "upper": _round(recent_high),
        "lower": _round(recent_low),
        "short_attack_line": _round(short_line),
        "mid_long_cost_line": _round(cost_line),
        "small_a_point": _round(small_a),
        "invalidation_price": _round(ma20 if zone != "风险C区" else recent_low),
        "reasons": reasons,
        "risk_points": ["风险C区优先于攻击之星", "跌破中长期成本线后重新评估"],
        "next_confirmation": ["价格保持在短期攻击线之上", "成交量与价格推进同步", "板块/题材继续互证"],
        "invalidation": ["关键结构失守", "风险压力继续增强", "卖出风险进入确认"],
        "geometry": {"upper_boundary": _round(recent_high), "lower_boundary": _round(recent_low), "short_attack_line": _round(short_line), "mid_long_cost_line": _round(cost_line), "small_a_point": _round(small_a)},
        "signals": zone_signals,
    }


def _window_slopes(values: list[float | None], size: int = 30) -> tuple[float | None, float | None]:
    window = [value for value in values[-size:] if value is not None]
    if len(window) < 6:
        return None, None
    half = max(3, len(window) // 2)
    return _pct(window[half - 1], window[0]), _pct(window[-1], window[half])


def _pattern_data(features: dict[str, Any], main_force: dict[str, Any], zones: dict[str, Any], ma: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    highs, lows = features["highs_shaped"], features["lows_shaped"]
    changes, volumes, closes = features["changes"], features["volumes"], features["closes"]
    enough = features["count"] >= 30
    high_left, high_right = [value for value in highs[-30:-15] if value is not None], [value for value in highs[-15:] if value is not None]
    low_left, low_right = [value for value in lows[-30:-15] if value is not None], [value for value in lows[-15:] if value is not None]
    old_high, new_high = max(high_left) if high_left else None, max(high_right) if high_right else None
    old_low, new_low = min(low_left) if low_left else None, min(low_right) if low_right else None
    upper_slope = _pct(new_high, old_high) if old_high else None
    lower_slope = _pct(new_low, old_low) if old_low else None
    old_width, new_width = (old_high - old_low if old_high is not None and old_low is not None else None), (new_high - new_low if new_high is not None and new_low is not None else None)
    contraction = new_width / old_width if old_width not in (None, 0) and new_width is not None else None
    current = features["close"]
    breakout_up = current is not None and old_high is not None and current > old_high * 1.005
    breakout_down = current is not None and old_low is not None and current < old_low * 0.995
    touch_upper = sum(1 for value in high_right if new_high and abs(value - new_high) / new_high <= 0.025)
    touch_lower = sum(1 for value in low_right if new_low and abs(value - new_low) / new_low <= 0.025)
    triangle_common = {"upper_line": _round(new_high), "lower_line": _round(new_low), "upper_slope": _round(upper_slope, 3), "lower_slope": _round(lower_slope, 3), "touch_count_upper": touch_upper, "touch_count_lower": touch_lower, "duration": min(features["count"], 30), "range_contraction": _round(contraction), "volume_contraction": _round((_avg(volumes[-10:]) or 0) / max(_avg(volumes[-25:-10]) or 1, 1))}
    triangle_score = 0.0 if not enough or contraction is None else _clamp((1 - contraction) * 160 + min(touch_upper, touch_lower) * 8)
    triangle_type = "平顶三角形" if (upper_slope is not None and abs(upper_slope) < 3 and (lower_slope or 0) > 2) else "平底三角形" if (lower_slope is not None and abs(lower_slope) < 3 and (upper_slope or 0) < -2) else "收敛三角形" if (upper_slope or 0) < 0 and (lower_slope or 0) > 0 else None
    triangle_id = {"平顶三角形": "BXDT_TRI_001", "平底三角形": "BXDT_TRI_002", "收敛三角形": "BXDT_TRI_003"}.get(triangle_type)
    patterns: list[dict[str, Any]] = []
    triangle_scores = {skill_id: (triangle_score if skill_id == triangle_id else 0) for skill_id in ("BXDT_TRI_001", "BXDT_TRI_002", "BXDT_TRI_003")}
    for skill_id, score in triangle_scores.items():
        subtype = triangle_type if skill_id == triangle_id else None
        lifecycle = "BREAKOUT" if subtype and breakout_up else "FORMING" if subtype else "NOT_FOUND"
        patterns.append(_signal(skill_id, _status_for(score), score if score else None, subtype=subtype, lifecycle=lifecycle, evidence=[_evidence("高低点边界与收敛关系", "triangle_geometry", triangle_common), _evidence("形态价格口径", "price_basis", features["price_basis"])], counter_evidence=[_evidence("高低点未形成足够重复测试", "touch_count", min(touch_upper, touch_lower))], next_confirmation=["突破后保持在上沿外", "成交量由收缩转为有效放大"], invalidation=["跌破下沿", "区间重新扩张并失去收敛"], chart_annotations=[{"type": subtype or "三角形观察", **triangle_common, "price_basis": features["price_basis"]}], metrics=triangle_common))

    box_width = (new_high - new_low) / current * 100 if new_high is not None and new_low is not None and current else None
    box_score = _clamp(90 - (box_width or 100) * 3) if box_width is not None and box_width <= 18 else 0
    box_stage = "BOX_BREAK_UP" if breakout_up else "BOX_BREAK_DOWN" if breakout_down else "BOX_TEST_UPPER" if current and new_high and current >= new_high * 0.96 else "BOX_TEST_LOWER" if current and new_low and current <= new_low * 1.04 else "BOX_BALANCE"
    box_metrics = {"upper_boundary": _round(new_high), "lower_boundary": _round(new_low), "midline": _round((new_high + new_low) / 2 if new_high is not None and new_low is not None else None), "duration": min(features["count"], 30), "upper_test_count": touch_upper, "lower_test_count": touch_lower, "box_width_pct": _round(box_width), "lifecycle": box_stage, "breakout_quality": "待确认" if breakout_up or breakout_down else "未突破", "price_basis": features["price_basis"]}
    patterns.append(_signal("BXDT_BOX_001", _status_for(box_score), box_score if box_score else None, subtype=box_stage, lifecycle="BREAKOUT" if "BREAK" in box_stage else "MATURE" if box_score >= 48 else "NOT_FOUND", evidence=[_evidence("箱体上下沿与中轴", "box_geometry", box_metrics)], next_confirmation=["突破后不快速回箱", "突破方向得到量价跟随"], invalidation=["有效跌破箱体下沿", "突破失败快速回箱"], chart_annotations=[{"type": "箱体", **box_metrics}], metrics=box_metrics))

    neckline = old_high
    low_count = 0
    if low_right:
        local_min = min(low_right)
        low_count = sum(1 for value in low_right if abs(value - local_min) / max(local_min, 0.01) <= 0.05)
    neck_score = _clamp(35 + low_count * 14 + (15 if current and neckline and current >= neckline * 0.95 else 0)) if neckline else 0
    neck_type = "多底颈位" if low_count >= 3 else "圆弧底颈位" if low_count >= 2 and (lower_slope or 0) > 0 else "V形底颈位" if features["returns5"] is not None and features["returns5"] > 5 and (features["returns20"] or 0) < -5 else None
    neck_id = {"多底颈位": "BXDT_NECK_001", "圆弧底颈位": "BXDT_NECK_002", "V形底颈位": "BXDT_NECK_003"}.get(neck_type)
    for skill_id, name in (("BXDT_NECK_001", "多底颈位"), ("BXDT_NECK_002", "圆弧底颈位"), ("BXDT_NECK_003", "V形底颈位")):
        score = neck_score if skill_id == neck_id else 0
        patterns.append(_signal(skill_id, _status_for(score), score if score else None, subtype=name if score else None, lifecycle="BREAKOUT" if score and breakout_up else "FORMING" if score else "NOT_FOUND", evidence=[_evidence("底部次数与颈位价格", "neckline", neckline), _evidence("相近低点数量", "multiple_low_count", low_count)], next_confirmation=["颈位突破并保持", "回踩颈位不重新跌破"], invalidation=["颈位下方收盘", "底部结构重新破坏"], chart_annotations=[{"type": name, "key_price": neckline, "price_basis": features["price_basis"]}], metrics={"neckline": _round(neckline), "low_count": low_count}))
    support_score = 68 if neck_id and breakout_up else 0
    patterns.append(_signal("BXDT_NECK_004", _status_for(support_score), support_score if support_score else None, subtype="颈位支撑" if support_score else None, lifecycle="CONFIRMED" if support_score else "NOT_FOUND", evidence=[_evidence("突破后颈位转为支撑", "neckline_support", bool(support_score))], chart_annotations=[{"type": "颈位支撑", "key_price": neckline}], metrics={"neckline": _round(neckline)}))

    higher_lows = bool(low_right and low_left and min(low_right) > min(low_left) * 0.995)
    up_score = 72 if higher_lows and (features["ma20"] or 0) < (current or 0) else 0
    prior_return = _pct(old_high, old_low) if old_high and old_low else None
    for skill_id, name, score in (("BXDT_UP_001", "缓慢顺上", up_score if up_score and abs(features["returns20"] or 0) < 18 else 0), ("BXDT_UP_002", "大波段后再顺上", up_score if up_score and abs(features["returns20"] or 0) >= 18 else 0), ("BXDT_UP_003", "小幅波段后再顺上", up_score if up_score and abs(features["returns20"] or 0) < 18 and (features["returns5"] or 0) > 2 else 0)):
        patterns.append(_signal(skill_id, _status_for(score), score if score else None, subtype=name if score else None, evidence=[_evidence("高低点重心与均线方向", "higher_lows", higher_lows), _evidence("前波段幅度", "prior_leg_return", prior_return)], next_confirmation=["均线排列和重心继续抬升", "量能恢复而非单日脉冲"], invalidation=["高低点重新下降", "价格跌回中长期成本线"], chart_annotations=[{"type": name}], metrics={"higher_lows": higher_lows, "prior_leg_return": _round(prior_return)}))

    # Trend and capital bottom sub-library. Each subtype remains a research
    # signal; a detected candle is never converted directly into a buy order.
    last_change = changes[-1] if changes else None
    last_open = features["opens"][-1] if features["opens"] else None
    last_high = highs[-1] if highs else None
    last_low = lows[-1] if lows else None
    lower_wick = features["lower_wick"] or 0
    low_position = (features["position120"] or 50) < 35
    long_lower = lower_wick >= 35 and low_position
    large_bear = last_change is not None and last_change <= -4 and low_position
    large_bull = last_change is not None and last_change >= 4 and low_position and (features["volume_ratio"] or 0) >= 1.5
    prior_bear = len(changes) >= 2 and changes[-2] is not None and changes[-2] < 0
    pair = prior_bear and last_change is not None and last_change > 0 and low_position
    small_body = (features["body_ratio"] or 100) <= 28
    morning = len(changes) >= 3 and changes[-3] is not None and changes[-3] < -2 and (features["body_ratio"] or 100) < 35 and last_change is not None and last_change > 1
    low_values = [value for value in lows[-20:] if value is not None]
    two_lows = len(low_values) >= 8 and abs(min(low_values[: len(low_values) // 2]) - min(low_values[len(low_values) // 2 :])) / max(min(low_values), 0.01) < 0.08
    multiple_lows = len(low_values) >= 12 and two_lows
    head_shoulder = multiple_lows and (features["returns20"] or 0) < 0
    bottom_specs = [
        ("BXDT_BOTTOM_001", "长下影K线见底", long_lower, 60 if long_lower else 0, {"lower_wick": lower_wick}),
        ("BXDT_BOTTOM_002", "诱空大阴K线见底", large_bear and support_or_reclaim(features), 62 if large_bear and support_or_reclaim(features) else 0, {"change_pct": last_change}),
        ("BXDT_BOTTOM_003", "巨量大阳K线见底", large_bull, 72 if large_bull else 0, {"change_pct": last_change, "volume_ratio": features["volume_ratio"]}),
        ("BXDT_BOTTOM_004", "阴阳并肩组合K线见底", pair, 62 if pair else 0, {"pair": pair}),
        ("BXDT_BOTTOM_005", "“单”字形反转组合K线见底", small_body and low_position and (features["returns5"] or 0) > 0, 52 if small_body and low_position else 0, {"body_ratio": features["body_ratio"]}),
        ("BXDT_BOTTOM_006", "晨星平台K线见底", morning, 65 if morning else 0, {"morning_star": morning}),
        ("BXDT_BOTTOM_007", "双重底", two_lows and not multiple_lows, 58 if two_lows and not multiple_lows else 0, {"low_count": 2 if two_lows else 0}),
        ("BXDT_BOTTOM_008", "多重底", multiple_lows, 62 if multiple_lows else 0, {"low_count": 3 if multiple_lows else 0}),
        ("BXDT_BOTTOM_009", "头肩底", head_shoulder, 52 if head_shoulder else 0, {"head_shoulder": head_shoulder}),
    ]
    for skill_id, name, matched, score, metrics in bottom_specs:
        patterns.append(_signal(skill_id, _status_for(score), score if score else None, subtype=name if matched else None, lifecycle="CONFIRMED" if score >= 72 else "FORMING" if matched else "NOT_FOUND", evidence=[_evidence("趋势底部候选与位置/后续价格条件", "bottom_candidate", matched), _evidence("价格数据口径", "price_basis", features["price_basis"])], counter_evidence=[_evidence("单根K线不能独立确认底部", "single_candle_boundary", True)], next_confirmation=["后续收盘继续守住关键低点", "成交和价格出现跟随确认"], invalidation=["跌破候选低点", "后续反弹失败并创出新低"], chart_annotations=[{"type": name, "key_price": features["low20"], "price_basis": features["price_basis"]}], metrics=metrics))

    volume_base_score = 62 if low_position and (features["volume_ratio"] or 0) < 1.1 and (features["returns5"] or 0) > -3 else 0
    ma_flat = abs(features["ma_slopes"].get("ma20") or 99) < 0.15
    ma_recovery = (features["ma_slopes"].get("ma20") or -99) > 0
    capital_specs = [
        ("BXDT_CAPITAL_001", "量能筑底", volume_base_score, {"low_position": low_position, "volume_ratio": features["volume_ratio"]}),
        ("BXDT_CAPITAL_002", "均线底", 64 if ma_flat or ma_recovery else 0, {"ma_flat": ma_flat, "ma_recovery": ma_recovery}),
        ("BXDT_CAPITAL_003", "下行转走平", 60 if ma_flat else 0, {"ma20_slope": features["ma_slopes"].get("ma20")}),
        ("BXDT_CAPITAL_004", "平行转翘头上行", 68 if ma_recovery and ma_flat else 0, {"ma_recovery": ma_recovery}),
        ("BXDT_CAPITAL_005", "空头转多头", 72 if all(value is not None for value in (features["ma5"], features["ma10"], features["ma20"])) and features["ma5"] > features["ma10"] > features["ma20"] else 0, {"ma_order": ma["stage"]}),
        ("BXDT_CAPITAL_006", "分散转聚合再发散", 58 if ma["stage"] in {"均线发散", "均线翘头"} else 0, {"ma_stage": ma["stage"]}),
    ]
    for skill_id, name, score, metrics in capital_specs:
        patterns.append(_signal(skill_id, _status_for(score), score if score else None, subtype=name if score else None, evidence=[_evidence("资金底部独立于趋势底部判断", "capital_bottom", bool(score)), _evidence("量能/均线证据", "metrics", metrics)], next_confirmation=["量能承接继续改善", "均线角度与距离同步改善"], invalidation=["低点再次失守", "量价重新恶化"], chart_annotations=[{"type": name, "key_price": features["low20"]}], metrics=metrics))

    # Three degrees are intentionally exposed separately.
    duration = _clamp(features["count"] / 2)
    thickness = _clamp(duration * 0.45 + (20 if ma["stage"] in {"均线密集", "均线发散", "均线顺畅"} else 5) + (15 if main_force["continuity"] in {"持续", "增强"} else 0))
    strength = _clamp((features["returns20"] or 0) * 1.5 + (features["volume_ratio"] or 0) * 12 + (20 if main_force["direction"] == "偏多" else 0))
    speed = _clamp(abs(features["returns5"] or 0) * 5 + abs(features["returns3"] or 0) * 4 + (20 if (features["volume_ratio"] or 0) >= 1.5 else 0))
    degree = {
        "thickness": {"value": _round(thickness, 1), "state": "厚" if thickness >= 70 else "中" if thickness >= 45 else "薄", "change": "增强" if duration > 45 else "积累中", "evidence": ["结构持续时间", "均线基础", "主力连续性"]},
        "strength": {"value": _round(strength, 1), "state": "强" if strength >= 70 else "中" if strength >= 45 else "弱", "change": "增强" if features["returns5"] and features["returns5"] > 0 else "减弱", "evidence": ["价格推进", "量能释放", "主力攻击"]},
        "speed": {"value": _round(speed, 1), "state": "快" if speed >= 70 else "中" if speed >= 40 else "慢", "change": "加速" if features["returns3"] and features["returns3"] > (features["returns5"] or 0) else "平稳", "evidence": ["上涨速度", "突破速度", "量价加速度"]},
    }
    for skill_id, key in (("BXDT_3D_001", "thickness"), ("BXDT_3D_002", "strength"), ("BXDT_3D_003", "speed")):
        item = degree[key]
        patterns.append(_signal(skill_id, _status_for(item["value"]), item["value"], subtype=item["state"], evidence=[_evidence("三度分量独立计算", key, item["value"]), _evidence("变化方向", f"{key}_change", item["change"])], next_confirmation=["继续观察该分量是否保持", "与其他书中结构进行互证"], invalidation=["对应证据反向破坏"], metrics=item))
    mode_short = _clamp((thickness + strength + speed) / 3)
    mode_swing = _clamp((thickness * 0.5 + strength * 0.35 + speed * 0.15))
    patterns.append(_signal("BXDT_3D_MODE_001", _status_for(mode_short), mode_short, subtype="精准强势短线盈利模式", evidence=[_evidence("三度组合仅作为Shadow观察", "mode_score", mode_short)], next_confirmation=["完成样本外验证后再评估ACTION影响"], invalidation=["回测未通过或样本不足"], metrics={"mode_score": _round(mode_short), "validation_status": "NOT_TESTED", "action_impact": "DISABLED_UNTIL_VALIDATED"}))
    patterns.append(_signal("BXDT_3D_MODE_002", _status_for(mode_swing), mode_swing, subtype="强势波段盈利模式", evidence=[_evidence("三度组合仅作为Shadow观察", "mode_score", mode_swing)], next_confirmation=["完成样本外验证后再评估ACTION影响"], invalidation=["回测未通过或样本不足"], metrics={"mode_score": _round(mode_swing), "validation_status": "NOT_TESTED", "action_impact": "DISABLED_UNTIL_VALIDATED"}))

    peak = features["high120"]
    peak_distance = _pct(current, peak) if current and peak else None
    peak_score = 82 if peak_distance is not None and peak_distance >= -1 and (features["volume_ratio"] or 0) >= 1.2 else 62 if peak_distance is not None and peak_distance >= -8 else 25 if peak_distance is not None else 0
    peak_state = "PEAK_HOLD" if peak_score >= 82 and current and peak and current >= peak else "PEAK_BREAK" if peak_score >= 82 else "PEAK_APPROACH" if peak_score >= 62 else "PEAK_FAR"
    patterns.append(_signal("BXDT_PEAK_001", _status_for(peak_score), peak_score if peak_score else None, subtype=peak_state, lifecycle="BREAKOUT" if peak_state == "PEAK_BREAK" else "TESTING" if peak_state == "PEAK_TEST" else "FORMING" if peak_state == "PEAK_APPROACH" else "NOT_FOUND", evidence=[_evidence("历史巅峰与当前价格距离", "peak_distance_pct", peak_distance), _evidence("突破后的量能", "volume_ratio", features["volume_ratio"])], next_confirmation=["突破后至少保持一个完整收盘周期", "量能和核心板块同步确认"], invalidation=["突破失败重新跌回巅峰下方", "高位放量滞涨"], chart_annotations=[{"type": "历史巅峰", "key_price": peak}], metrics={"historical_peak": _round(peak), "distance_pct": _round(peak_distance), "state": peak_state}))
    detail = {"triangle": triangle_common, "triangle_type": triangle_type, "box": box_metrics, "neckline": _round(neckline), "up": {"higher_lows": higher_lows}, "bottom": {"low_position": low_position}, "capital": {"volume_base_score": _round(volume_base_score), "ma_flat": ma_flat}, "three_degree": degree, "peak": {"state": peak_state, "price": _round(peak)}}
    return patterns, detail


def support_or_reclaim(features: dict[str, Any]) -> bool:
    close, ma20 = features.get("close"), features.get("ma20")
    return bool(close is not None and (ma20 is None or close >= ma20 * 0.94))


def _stars(features: dict[str, Any], zones: dict[str, Any], main_force: dict[str, Any], theme: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if features["count"] < 20:
        return ([_signal(skill_id, evidence=[_evidence("日线样本不足，星线必须结合上下文", "daily_bars", features["count"])]) for skill_id in ("BXZX_001", "BXZX_002", "BXZX_003", "BXZX_004", "BXZX_005", "BXZX_006", "BXZX_007", "BXZX_008", "BXZX_009", "BXZX_010")], {})
    body, upper, lower = features["body_ratio"] or 0, features["upper_wick"] or 0, features["lower_wick"] or 0
    ret5, ret20 = features["returns5"] or 0, features["returns20"] or 0
    vr, position = features["volume_ratio"] or 0, features["position120"] or 50
    small = body <= 35
    prior_down = ret20 < -3
    prior_up = ret20 > 5
    support = zones["zone"] in {"强势A区", "强势B区"}
    range5 = max(features["highs_shaped"][-5:]) - min(features["lows_shaped"][-5:]) if features["highs_shaped"][-5:] and features["lows_shaped"][-5:] else None
    range20 = max(features["highs_shaped"][-20:]) - min(features["lows_shaped"][-20:]) if features["highs_shaped"][-20:] and features["lows_shaped"][-20:] else None
    range_contracted = bool(range5 is not None and range20 not in (None, 0) and range5 / range20 < 0.35)
    volume_shrinking = len(features["volumes"]) >= 5 and (_avg(features["volumes"][-3:]) or 0) < (_avg(features["volumes"][-8:-3]) or _avg(features["volumes"][-3:]) or 1) * 0.85
    sync_bottom = prior_down and small and volume_shrinking and lower >= 20
    divergence_bottom = prior_down and (ret5 < -2) and volume_shrinking
    breakout = bool(features["high20_prior"] and features["close"] and features["close"] > features["high20_prior"] * 1.005 and vr >= 1.15)
    reversal = prior_down and features["close"] is not None and features["ma20"] is not None and features["close"] > features["ma20"] and ret5 > 2
    induced = support and prior_down and small and volume_shrinking and lower > 25
    squeeze = support and prior_up and small and range_contracted and features["close_location"] is not None and features["close_location"] > 55
    buffer = support and ret5 <= 1 and ret5 >= -8 and volume_shrinking and small
    shock_range = support and range_contracted and small
    sector_positive = bool(theme and theme.get("hotspot_level") in {"强势热点", "局部热点"})
    configs = [
        ("BXZX_001", "诱空蓄势星线", induced, 66 if induced else 0, "蓄势", {"induced_drop": ret5, "star_count": 1}),
        ("BXZX_002", "逼空蓄势星线", squeeze, 62 if squeeze else 0, "蓄势", {"high_position": position, "range_contracted": range_contracted}),
        ("BXZX_003", "缓冲调整星线", buffer, 68 if buffer else 0, "调整", {"pullback": ret5, "volume_shrinking": volume_shrinking, "body_ratio": body}),
        ("BXZX_004", "震荡调整星线", shock_range, 58 if shock_range else 0, "调整", {"range5": range5, "range20": range20}),
        ("BXZX_005", "同步止跌星线", sync_bottom, 70 if sync_bottom else 0, "止跌", {"price_contraction": small, "volume_contraction": volume_shrinking}),
        ("BXZX_006", "背离止跌星线", divergence_bottom, 66 if divergence_bottom else 0, "止跌", {"price_change": ret5, "volume_contraction": volume_shrinking, "external_context": "需板块/指数数据进一步比较"}),
        ("BXZX_007", "借势补仓星线", support and (ret5 or 0) > 0 and (features["ma20"] or 0) < (features["close"] or 0), 55 if support else 0, "补仓", {"landform": zones["zone"], "trend": "个股趋势可观察"}),
        ("BXZX_008", "借风补仓星线", sector_positive and support and ret5 > 0, 60 if sector_positive and support and ret5 > 0 else 0, "补仓", {"theme": theme.get("theme_name") if theme else None, "hotspot": theme.get("hotspot_level") if theme else None}),
        ("BXZX_009", "突破攻击星线", breakout, 78 if breakout else 0, "攻击", {"breakout_level": features["high20_prior"], "volume_ratio": vr, "close_hold": breakout}),
        ("BXZX_010", "反转攻击星线", reversal, 72 if reversal else 0, "攻击", {"reversal_level": features["ma20"], "prior_down": prior_down}),
    ]
    output: list[dict[str, Any]] = []
    for skill_id, name, matched, score, category, metrics in configs:
        status = _status_for(score)
        if matched and category == "攻击" and zones["zone"] == "风险C区":
            status = "POSSIBLE"
            score = min(score, 48)
        output.append(_signal(skill_id, status, score if score else None, subtype=name if matched else None, lifecycle="CONFIRMED" if status == "CONFIRMED" else "FORMING" if matched else "NOT_FOUND", evidence=[_evidence("星线本体与前置趋势、位置、量、均线、主力共同核验", "context_gate", matched), _evidence("星线实体/影线", "body_upper_lower", {"body": body, "upper": upper, "lower": lower}), _evidence("位置", "position120", position)], counter_evidence=[_evidence("只凭单根K线形状不能确认星线", "single_candle_only", True), _evidence("风险C区会压低攻击星线优先级", "risk_zone_priority", zones["zone"] == "风险C区")], next_confirmation=["下一交易日价格继续确认", "成交和板块/题材出现跟随"], invalidation=["关键支撑失守", "星线后续未能保持关键位", "反证占据主导"], conflicts=["风险C区 + 攻击之星：风险优先"] if category == "攻击" and zones["zone"] == "风险C区" else [], chart_annotations=[{"type": name, "key_price": features["close"], "trade_date": features["bars_end"]}], metrics=metrics))

    classic_specs = [
        ("BXZX_CLASSIC_BOTTOM_001", "定海神针见底", lower >= 45 and lower > upper and (features["position120"] or 50) < 35 and support_or_reclaim(features), 70),
        ("BXZX_CLASSIC_BOTTOM_002", "倒锤头星线见底", upper >= 45 and body <= 32 and prior_down and (features["position120"] or 50) < 45, 58),
        ("BXZX_CLASSIC_BOTTOM_003", "孕线见底", len(features["closes"]) >= 2 and abs(features["closes"][-1] - (features["opens"][-1] or features["closes"][-1])) < abs(features["closes"][-2] - (features["opens"][-2] or features["closes"][-2])) * 0.65 and prior_down, 56),
        ("BXZX_CLASSIC_BOTTOM_004", "启明星见底", sync_bottom and ret5 > 0, 68),
        ("BXZX_CLASSIC_BOTTOM_005", "揉搓星见底", upper >= 25 and lower >= 25 and low_position(features), 55),
        ("BXZX_CLASSIC_BOTTOM_006", "平排星线见底", range_contracted and small and (features["position120"] or 50) < 40, 52),
        ("BXZX_CLASSIC_TOP_001", "射击之星", upper >= 45 and body <= 35 and position >= 72 and ret5 <= 1, 70),
        ("BXZX_CLASSIC_TOP_002", "吊颈星线", lower >= 40 and body <= 35 and position >= 72 and ret5 <= 0, 58),
        ("BXZX_CLASSIC_TOP_003", "孕线现顶", small and position >= 72 and ret5 < 0, 56),
        ("BXZX_CLASSIC_TOP_004", "黄昏之星", prior_up and small and ret5 < -1 and position >= 65, 68),
    ]
    for skill_id, name, matched, score in classic_specs:
        status = _status_for(score if matched else 0)
        output.append(_signal(skill_id, status, score if matched else None, subtype=name if matched else None, lifecycle="CONFIRMED" if status == "CONFIRMED" else "FORMING" if matched else "NOT_FOUND", evidence=[_evidence("经典星线本体", "classic_body", matched), _evidence("前置趋势/位置门槛", "context_gate", {"prior_up": prior_up, "prior_down": prior_down, "position120": position}), _evidence("影线比例", "upper_lower_wick", {"upper": upper, "lower": lower})], counter_evidence=[_evidence("经典星线必须等待后续确认", "follow_through_required", True)], next_confirmation=["后续价格不能立即破坏关键位", "成交与均线/主力证据继续配合"], invalidation=["跌破关键低点", "高位星线后继续创新高并有效保持"], chart_annotations=[{"type": name, "key_price": features["close"]}], metrics={"position120": position, "body_ratio": body, "upper_wick": upper, "lower_wick": lower}))
    strongest = max((item for item in output if item["status"] != "NOT_FOUND"), key=lambda item: item.get("confidence") or 0, default=None)
    return output, {"strongest": strongest, "context_gate": {"prior_trend": "上涨" if prior_up else "下跌" if prior_down else "中性", "position": "高位" if position >= 72 else "中低位", "volume": "放量" if vr >= 1.5 else "正常/缩量", "zone": zones["zone"]}}


def low_position(features: dict[str, Any]) -> bool:
    return (features.get("position120") or 50) < 45


def _theme(context: dict[str, Any], features: dict[str, Any]) -> dict[str, Any]:
    sector = context.get("sector") or {}
    flow = context.get("sector_flow") or []
    name = sector.get("name") or sector.get("industry")
    change = _num(sector.get("change_pct"))
    flows = [_num(item.get("main_net_inflow")) for item in flow if isinstance(item, dict)]
    recent_flow = _avg(flows[-5:]) if flows else None
    if not name and not flow:
        return {"status": "UNKNOWN", "theme_name": None, "theme_type": "空穴来风的题材", "hotspot_level": "未知", "theme_stage": "未接入", "early_layout": "未知", "evidence": [_evidence("没有可核验板块/题材数据", "sector_data", False)], "volume_energy_verified": False, "source": "unavailable"}
    theme_type = "主流题材" if change is not None and change >= 2 and recent_flow is not None and recent_flow > 0 else "一般题材" if name else "空穴来风的题材"
    hotspot = "强势热点" if change is not None and change >= 2 and (recent_flow or 0) > 0 else "局部热点" if change is not None and change > 0 else "一般热点"
    stage = "二次发酵" if change is not None and change > 1 and features["returns5"] and features["returns5"] > 0 else "首次发酵" if change is not None and change > 0 else "衰退/待观察"
    return {"status": "AVAILABLE", "theme_name": name, "theme_type": theme_type, "hotspot_level": hotspot, "theme_stage": stage, "early_layout": "可观察但不可证明" if recent_flow is not None and recent_flow > 0 else "未发现", "evidence": [_evidence("板块价格变化", "sector_change_pct", change), _evidence("近5日板块主力流入均值", "sector_flow_5d", recent_flow), _evidence("题材数据来源", "source", sector.get("source") or "context")], "volume_energy_verified": bool(recent_flow is not None and recent_flow > 0), "source": sector.get("source") or "sector_context", "metrics": {"sector_change_pct": _round(change), "recent_flow": _round(recent_flow)}}


def _profit_patterns(features: dict[str, Any], zones: dict[str, Any], main_force: dict[str, Any], patterns: list[dict[str, Any]], stars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active_names = {item["name"] for item in patterns if item.get("status") in {"POSSIBLE", "FORMING", "CONFIRMED"}}
    strong_zone = zones["zone"] in {"强势A区", "强势B区"}
    support = support_or_reclaim(features)
    pullback = features["returns5"] is not None and -10 <= features["returns5"] <= 1
    node = strong_zone and support and pullback and features["ma5"] is not None and features["ma20"] is not None
    out = [_signal("HQS_011", "FORMING" if node else "NOT_FOUND", 56 if node else None, subtype="短中期均线结点" if node else None, evidence=[_evidence("均线结点同时结合位置、量和主力", "node_gate", node), _evidence("均线交叉不等于强势结点", "cross_is_not_node", True)], next_confirmation=["结点后重新出现价格推进", "成交量和主力证据跟随"], invalidation=["结点下方结构失守"], metrics={"node_type": "短中期均线结点" if node else None})]
    wash_base = strong_zone and features["returns5"] is not None and features["returns5"] < -4 and (features["volume_ratio"] or 0) < 1.5 and support
    wash_types = [
        ("HQS_WASH_001", "中大阴线实体洗盘", wash_base and (features["changes"][-1] or 0) < -3),
        ("HQS_WASH_002", "长上影线形态洗盘", wash_base and (features["upper_wick"] or 0) >= 35),
        ("HQS_WASH_003", "黑太阳形态洗盘", wash_base and (features["body_ratio"] or 0) >= 55 and (features["changes"][-1] or 0) < 0),
    ]
    for skill_id, name, matched in wash_types:
        out.append(_signal(skill_id, "FORMING" if matched else "NOT_FOUND", 52 if matched else None, subtype=name if matched else None, evidence=[_evidence("形态与位置二维核验", "wash_location_gate", matched), _evidence("关键位置", "zone", zones["zone"])], next_confirmation=["后续大阳线收回关键位", "结构不被洗盘日破坏"], invalidation=["回收失败并跌破支撑"], metrics={"location": zones["zone"], "recovery_required": True}))
    gap = len(features["closes"]) > 1 and features["opens"][-1] is not None and features["closes"][-2] is not None and features["opens"][-1] > features["closes"][-2] * 1.02
    gap_types = [("HQS_GAP_001", "拔升缺口", gap and strong_zone), ("HQS_GAP_002", "平台跳空突破", gap and strong_zone and zones["stage"] in {"A_ACTIVE", "B_REATTACK"}), ("HQS_GAP_003", "拐点跳空突破", gap and (features["returns20"] or 0) < 0), ("HQS_GAP_004", "缺口支撑", gap and support and (features["close"] or 0) >= (features["opens"][-1] or 0))]
    for skill_id, name, matched in gap_types:
        out.append(_signal(skill_id, "FORMING" if matched else "NOT_FOUND", 58 if matched else None, subtype=name if matched else None, evidence=[_evidence("缺口前环境、位置和后续保持", "gap_context", matched), _evidence("缺口幅度", "gap_pct", _pct(features["opens"][-1], features["closes"][-2]) if gap else None)], next_confirmation=["缺口不被快速回补", "后续成交与价格继续推进"], invalidation=["缺口快速回补", "回补后跌破支撑"], metrics={"gap": gap}))
    cycle_base = strong_zone and support and pullback and main_force.get("direction") == "偏多"
    for skill_id, name, matched in (("HQS_CYCLE_001", "波段循环低点", cycle_base and abs(features["returns20"] or 0) >= 8), ("HQS_CYCLE_002", "局部循环低点", cycle_base and abs(features["returns20"] or 0) < 8)):
        out.append(_signal(skill_id, "FORMING" if matched else "NOT_FOUND", 54 if matched else None, subtype=name if matched else None, evidence=[_evidence("回调幅度、时间、均线支撑和主力", "cycle_gate", matched)], next_confirmation=["新的攻击结构形成", "价格不跌破循环低点"], invalidation=["循环低点失守", "主力方向转弱"], metrics={"cycle_level": name}))
    return out


def _stock_character(features: dict[str, Any]) -> dict[str, Any]:
    changes = [value for value in features["changes"] if value is not None]
    if len(changes) < 20:
        return {"status": "INSUFFICIENT_DATA", "summary": "有效历史样本不足", "engineering_label": "未知", "historical_samples": 0, "confidence": None, "features": {}}
    large_up = sum(1 for value in changes if value >= 4)
    large_down = sum(1 for value in changes if value <= -4)
    volatility = pstdev(changes)
    upper_count = sum(1 for value in features["highs_shaped"][-60:] if value is not None)
    continuation = 0
    continuation_samples = 0
    for index, change in enumerate(features["changes"][:-1]):
        if change is not None and change >= 3:
            continuation_samples += 1
            following = features["changes"][index + 1]
            continuation += 1 if following is not None and following > 0 else 0
    continuation_rate = continuation / continuation_samples * 100 if continuation_samples else None
    label = "活跃弹性" if volatility >= 3.2 or large_up >= 5 else "易冲高回落" if (features["upper_wick"] or 0) > 35 or large_down > large_up else "趋势稳定"
    return {"status": "AVAILABLE", "summary": f"工程标签：{label}；不作为书内新增术语", "engineering_label": label, "historical_samples": len(changes), "confidence": _round(_clamp(40 + min(len(changes), 60) * 0.5), 1), "features": {"volatility": _round(volatility, 3), "large_up_days": large_up, "large_down_days": large_down, "up_day_continuation_rate": _round(continuation_rate), "upper_shadow_observation_count": upper_count, "return5": _round(features["returns5"]), "return20": _round(features["returns20"])}, "historical_behaviour": ["历史强势行情持续性", "涨幅后的延续", "历史波动弹性", "回调习惯", "突破回落习惯"], "current_consistency": "待当前结构与历史样本继续对照"}


def _buy_point(features: dict[str, Any], zones: dict[str, Any], main_force: dict[str, Any], stars: list[dict[str, Any]], patterns: list[dict[str, Any]], risk: dict[str, Any]) -> dict[str, Any]:
    active_stars = [item for item in stars if item.get("status") in {"POSSIBLE", "FORMING", "CONFIRMED"}]
    active_patterns = [item for item in patterns if item.get("status") in {"POSSIBLE", "FORMING", "CONFIRMED"}]
    has_attack = any(item["skill_id"] in {"BXZX_009", "BXZX_010"} and item["status"] in {"FORMING", "CONFIRMED"} for item in active_stars)
    has_classic = any(item["skill_id"].startswith("BXZX_CLASSIC") and item["status"] in {"FORMING", "CONFIRMED"} for item in active_stars)
    danger = (risk.get("overall_score") or 0) >= 72 or zones["zone"] == "风险C区"
    if danger:
        level = "臆想买点" if active_stars or active_patterns else "一般买点"
    elif has_attack and zones["zone"] in {"强势A区", "强势B区"} and main_force.get("direction") == "偏多":
        level = "强势买点"
    elif has_classic:
        level = "经典买点"
    elif zones["zone"] in {"强势A区", "强势B区"} and main_force.get("direction") == "偏多":
        level = "一般买点"
    else:
        level = "臆想买点"
    requirements = {"强势买点": ["盘面吻合", "位置协调", "主力方向偏多", "量价和形态互证"], "经典买点": ["经典位置", "经典形态", "后续确认"], "一般买点": ["量价未明显透支", "健康调整", "再次进攻"], "臆想买点": ["不能只看急拉、缩量、下影或金叉", "补齐位置、主力、量和大盘证据"]}
    levels = []
    for name in ("强势买点", "经典买点", "一般买点", "臆想买点"):
        active = level == name
        levels.append({"name": name, "status": "CONFIRMED" if active else "NOT_FOUND", "matched": requirements[name] if active else [], "missing": [] if active else requirements[name]})
    return {"level": level, "is_imagined": level == "臆想买点", "levels": levels, "matched_skills": [item["skill_id"] for item in active_stars + active_patterns], "missing_evidence": ["大势许可", "板块宽度", "后续确认"] if level in {"强势买点", "经典买点"} else requirements[level], "counter_evidence": ["风险C区优先", "单一K线/指标不能形成买点"], "note": "买点等级是研究分类，不是交易指令。"}


def _sell(risk: dict[str, Any], zones: dict[str, Any], stars: list[dict[str, Any]], features: dict[str, Any]) -> dict[str, Any]:
    top = next((item for item in risk.get("signals", []) if item["skill_id"] == "HQS_RISK_001"), {})
    top_score = top.get("confidence") or 0
    classic_top = [item for item in stars if item["skill_id"].startswith("BXZX_CLASSIC_TOP") and item.get("status") in {"POSSIBLE", "FORMING", "CONFIRMED"}]
    obvious = top_score >= 75 and (features["upper_wick"] or 0) >= 25 and (features["returns5"] or 0) <= 0
    meet = top_score >= 48
    c_zone = zones["zone"] == "风险C区"
    return {"obvious_top": {"state": "TOP_CONFIRMED" if obvious else "TOP_FORMING" if top_score >= 48 else "TOP_WARNING" if top_score >= 28 else "NOT_FOUND", "evidence": ["高位放量/滞涨", "上影或价格推进效率下降"]}, "meet_top": {"state": "REJECTED_BY_TOP" if meet and (features["returns5"] or 0) < 0 else "APPROACHING_TOP" if meet else "NOT_FOUND", "evidence": ["历史顶部/密集成交区", f"压力分数={_round(top_score)}"]}, "c_zone": {"state": "C_EXIT" if c_zone and (risk.get("overall_score") or 0) >= 78 else "C_DEEPENING" if c_zone else "NOT_FOUND", "evidence": zones.get("reasons", [])}, "classic_top": {"state": "CONFIRMED" if classic_top else "NOT_FOUND", "matched": [item["name"] for item in classic_top]}, "risk_priority": "RISK" if c_zone or obvious or classic_top else "WATCH", "signals": [_signal("HQS_015", "CONFIRMED" if obvious else "POSSIBLE" if top_score >= 48 else "NOT_FOUND", top_score if top_score >= 28 else None, evidence=[_evidence("明显见顶需要多项证据共同出现", "top_score", top_score)], conflicts=["攻击星线不能抵消明显见顶"] if obvious else []), _signal("HQS_016", "FORMING" if meet else "NOT_FOUND", top_score if meet else None, evidence=[_evidence("遇顶不等于已经见顶", "historical_pressure", meet)]), _signal("HQS_017", "CONFIRMED" if c_zone else "NOT_FOUND", 82 if c_zone else None, evidence=[_evidence("读取A/B/C状态机", "zone", zones["zone"])])], "note": "明显遇顶与明显见顶分开；风险C区优先于题材和攻击信号。"}


def _stacking(zones: dict[str, Any], main_force: dict[str, Any], ma: dict[str, Any], patterns: list[dict[str, Any]], stars: list[dict[str, Any]], theme: dict[str, Any], character: dict[str, Any], buy: dict[str, Any], sell: dict[str, Any]) -> dict[str, Any]:
    def node(name: str, status: str, evidence: str, detail: Any = None) -> dict[str, Any]:
        return {"name": name, "status": status, "evidence": evidence, "detail": detail}

    base = [
        node("量时空风险过滤", "RISK" if sell["risk_priority"] == "RISK" else "PASS", "风险C区/量时空压力优先", zones["zone"]),
        node("量形态还原主力身影", "PASS" if main_force["direction"] != "暂不明确" else "WATCH", "上涨/下跌量能、压价、缩量、攻击路径", main_force["stage"]),
        node("均线归位", "PASS" if ma["status"] == "AVAILABLE" else "WATCH", "排列、角度、距离", ma["stage"]),
        node("个股股性", "PASS" if character["status"] == "AVAILABLE" else "WATCH", "历史行为工程层", character.get("engineering_label")),
    ]
    second = [
        node("大形态 + 暴涨之星", "PASS" if any(item["status"] in {"FORMING", "CONFIRMED"} for item in patterns + stars) else "WATCH", "图表结构与上下文联合", [item["name"] for item in patterns + stars if item["status"] in {"FORMING", "CONFIRMED"}][:8]),
        node("题材互证", "PASS" if theme.get("volume_energy_verified") else "WATCH", "题材类型、热点等级、板块资金", theme.get("hotspot_level")),
        node("买点等级", "RISK" if buy["is_imagined"] else "PASS", "买点必须有多层证据", buy["level"]),
        node("风险冲突", "RISK" if sell["risk_priority"] == "RISK" else "PASS", "明确风险不被平均抵消", sell["risk_priority"]),
    ]
    flat = base + second
    risks = [item["name"] for item in flat if item["status"] == "RISK"]
    passed = [item["name"] for item in flat if item["status"] == "PASS"]
    level = "很强" if len(passed) >= 6 and not risks else "强" if len(passed) >= 4 and not risks else "中" if passed else "弱"
    return {"level": level, "path": [{"stage": "量能体基础", "nodes": base}, {"stage": "图表与题材", "nodes": second}], "confirmed": passed, "risks": risks, "possible": [item["name"] for item in flat if item["status"] == "WATCH"], "conflicts": [f"{item}与攻击/买点冲突，风险优先" for item in risks], "note": "叠加路径比单一总分更重要。"}


def _consensus(zones: dict[str, Any], patterns: list[dict[str, Any]], stars: list[dict[str, Any]], main_force: dict[str, Any], theme: dict[str, Any], sell: dict[str, Any], legacy: dict[str, Any] | None) -> dict[str, Any]:
    big_active = [item for item in patterns if item["book"] == "暴涨大形态" and item["status"] in {"POSSIBLE", "FORMING", "CONFIRMED"}]
    star_active = [item for item in stars if item["status"] in {"POSSIBLE", "FORMING", "CONFIRMED"}]
    hunter = "风险C区" if zones["zone"] == "风险C区" else "强势A/B区" if zones["zone"] in {"强势A区", "强势B区"} else "观察"
    big = big_active[0]["name"] if big_active else "未形成明确大形态"
    star = star_active[0]["name"] if star_active else "未形成明确星线"
    risk_present = sell["risk_priority"] == "RISK" or zones["zone"] == "风险C区"
    positive_present = zones["zone"] in {"强势A区", "强势B区"} or main_force.get("direction") == "偏多"
    if risk_present and positive_present:
        level, dominant = "冲突", "RISK"
    elif risk_present:
        level, dominant = "部分一致", "RISK"
    elif positive_present and big_active and star_active:
        level, dominant = "一致", "OPPORTUNITY"
    elif positive_present or big_active or star_active:
        level, dominant = "部分一致", "WATCH"
    else:
        level, dominant = "冲突", "NEUTRAL"
    conflicts: list[dict[str, Any]] = []
    if zones["zone"] == "风险C区" and any(item["skill_id"] in {"BXZX_009", "BXZX_010"} for item in star_active):
        conflicts.append({"code": "ZONE_ATTACK", "text": "风险C区 + 攻击之星：风险优先"})
    if sell["classic_top"]["state"] == "CONFIRMED" and theme.get("hotspot_level") in {"强势热点", "局部热点"}:
        conflicts.append({"code": "TOP_THEME", "text": "现顶经典星线 + 题材利好：题材不能抵消顶部风险"})
    if risk_present and any(item["skill_id"].startswith("BXZX_CLASSIC_BOTTOM") for item in star_active):
        conflicts.append({"code": "PRESSURE_BOTTOM", "text": "压力与见底星线并存，等待后续确认"})
    return {"level": level, "dominant_side": dominant, "status": "CONFLICT_FOUND" if conflicts or level == "冲突" else "CONSENSUS", "hunter": {"state": hunter, "main_force": main_force.get("direction")}, "big_pattern": {"state": big, "active_count": len(big_active)}, "star": {"state": star, "active_count": len(star_active)}, "theme": {"state": theme.get("hotspot_level"), "type": theme.get("theme_type")}, "conflicts": conflicts, "priority_rule": "明确风险 > 明确机会", "legacy_action": (legacy or {}).get("action"), "note": "三书互证不做简单平均，冲突显式输出。"}


def _buy_signals(buy: dict[str, Any]) -> list[dict[str, Any]]:
    levels = buy.get("levels") or []
    output = []
    ids = {"强势买点": "HQS_BUY_001", "经典买点": "HQS_BUY_002", "一般买点": "HQS_BUY_003", "臆想买点": "HQS_BUY_004"}
    for item in levels:
        name = item["name"]
        active = item["status"] == "CONFIRMED"
        output.append(_signal(ids[name], "CONFIRMED" if active else "NOT_FOUND", 75 if active and name != "臆想买点" else 35 if active else None, subtype=name, evidence=[_evidence("买点等级由多层证据联合决定", "buy_level", name), _evidence("当前是否命中", "active", active)], counter_evidence=[_evidence("单一指标不能形成买点", "single_indicator", True)], next_confirmation=["继续等待后续价格/成交确认"], invalidation=["关键结构失守"], metrics={"matched": item.get("matched"), "missing": item.get("missing")}))
    return output


def _explanation(result: dict[str, Any]) -> dict[str, Any]:
    risk = result["risk"]
    qts = result["quantity_time_space"]
    consensus = result["consensus"]
    zones = result["zones"]
    active_signals = [item["name"] for item in result["signals"] if item.get("status") in {"POSSIBLE", "FORMING", "CONFIRMED"}]
    risk_names = [item["name"] for item in result["signals"] if item.get("status") in {"POSSIBLE", "FORMING", "CONFIRMED"} and item.get("skill_id", "").startswith("HQS_RISK")]
    active_stars = [item for item in result["signals"] if item.get("skill_id", "").startswith("BXZX_") and item.get("status") in {"POSSIBLE", "FORMING", "CONFIRMED"}]
    classic_bottom = [item for item in result["signals"] if item.get("skill_id", "").startswith("BXZX_CLASSIC_BOTTOM") and item.get("status") in {"POSSIBLE", "FORMING", "CONFIRMED"}]
    classic_top = [item for item in result["signals"] if item.get("skill_id", "").startswith("BXZX_CLASSIC_TOP") and item.get("status") in {"POSSIBLE", "FORMING", "CONFIRMED"}]
    why_not: list[str] = []
    if zones.get("zone") != "风险C区":
        why_not.append(f"未进入风险C区：当前交易区为{zones.get('zone', '未形成明确交易区')}，尚未满足风险区的组合条件。")
    else:
        why_not.append("风险C区已出现：风险证据优先，攻击类形态不能单独升级为行动依据。")
    if not active_stars:
        why_not.append("未形成可确认的攻击星线：还缺少星线形态与量价、位置、后续确认的联合证据。")
    else:
        why_not.append("攻击星线仍处于候选或构建状态：需要后续成交、价格和板块响应确认。")
    if not classic_top:
        why_not.append("经典顶部尚未形成完整组合：当前没有足够的高位、滞涨和压力证据共同确认。")
    if not classic_bottom:
        why_not.append("经典底部尚未形成完整组合：超跌或单日反弹不能替代止跌、承接和结构修复证据。")
    if risk_names:
        why_not.append("当前风险证据占优，因此不采用未经验证的攻击或买点解释。")
    return {
        "current_judgement": f"当前区域：{result['zones']['zone']}；量时空：{qts.get('time', {}).get('state', '未知')} / {qts.get('space', {}).get('state', '未知')} / {qts.get('quantity', {}).get('state', '未知')}；三书互证：{consensus['level']}。",
        "main_contradiction": "风险压力占主导" if risk_names else "机会证据仍需量价与后续确认",
        "evidence": active_signals[:16],
        "risk_first": risk_names[:8],
        "next_step": result["zones"].get("next_confirmation") or [],
        "invalidation": result["zones"].get("invalidation") or [],
        "why_not": why_not[:6],
        "data_basis": {"BOOK_RULE": sum(1 for item in result["signals"] if item.get("knowledge_layer") == "BOOK_RULE"), "ENGINE_FEATURE": sum(1 for item in result["signals"] if item.get("knowledge_layer") == "ENGINE_FEATURE"), "EMPIRICAL_LAYER": sum(1 for item in result["signals"] if item.get("knowledge_layer") == "EMPIRICAL_LAYER")},
        "limitations": ["主力一词仅表示可观察的量价身影，不证明不可验证的参与者意图", "工程置信度不是收益概率", "缺失高低价时使用CLOSE_PROXY并降低形态置信度"],
        "plain_language": "先看风险和位置，再看量、均线、主力和形态；任何单一星线或题材都不能越过风险冲突矩阵。",
    }


def _timeline(bars: list[dict[str, Any]], signals: list[dict[str, Any]], zones: dict[str, Any], ma: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_date = bars[-1]["trade_date"] if bars else None
    for signal in signals:
        if signal.get("status") in {"POSSIBLE", "FORMING", "CONFIRMED", "WEAKENING", "INVALID"}:
            rows.append({"date": current_date, "type": "SIGNAL", "skill_id": signal["skill_id"], "name": signal["name"], "status": signal["status"], "lifecycle": signal.get("lifecycle"), "confidence": signal.get("confidence"), "evidence": signal.get("evidence", [])[:3]})
    rows.append({"date": current_date, "type": "ZONE", "name": zones["zone"], "stage": zones["stage"], "geometry": zones["geometry"]})
    rows.append({"date": current_date, "type": "MA", "name": ma["stage"], "evolution": ma["evolution"][-5:]})
    return rows


def build_v2(context: dict[str, Any], legacy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a complete V2 payload from a point-in-time context."""
    bars = _normalise_bars(context.get("bars") or [])
    features = _series_features(bars)
    risk = _risk(features)
    qts = _quantity_time_space(features, risk)
    main_force = _main_force(features)
    ma = _moving_average(features)
    raw_v2_zone = _zones(features, risk, main_force, ma)
    legacy_zone = (legacy or {}).get("best_trading_zone") or {}
    legacy_zone_name = str(legacy_zone.get("zone") or "").strip()
    canonical_zone_names = {"强势A区", "强势B区", "风险C区", "未形成明确交易区"}
    if legacy_zone_name in canonical_zone_names:
        # V2 extends the V1 decision and must not publish a contradictory
        # trading-zone conclusion. Keep V2 geometry as supplemental research
        # data while using the legacy state machine as the canonical label.
        zones = {**raw_v2_zone, **legacy_zone}
        zones["geometry"] = raw_v2_zone.get("geometry") or legacy_zone.get("geometry") or {}
        zones["stage"] = (
            legacy_zone.get("stage")
            or legacy_zone.get("zone_stage")
            or {
                "强势A区": "A_ACTIVE",
                "强势B区": "B_ACTIVE",
                "风险C区": "C_WARNING",
                "未形成明确交易区": "UNKNOWN",
            }[legacy_zone_name]
        )
        canonical_zone_by_skill = {
            "HQS_008": "强势A区",
            "HQS_009": "强势B区",
            "HQS_010": "风险C区",
        }
        zones["signals"] = [
            {
                **signal,
                "status": "CONFIRMED" if canonical_zone_by_skill.get(signal.get("skill_id")) == legacy_zone_name else "NOT_FOUND",
                "confidence": signal.get("confidence") or 82 if canonical_zone_by_skill.get(signal.get("skill_id")) == legacy_zone_name else None,
                "evidence": [
                    _evidence("统一采用V1交易区状态机结论", "canonical_zone", legacy_zone_name),
                    *list(signal.get("evidence") or []),
                ],
            }
            for signal in raw_v2_zone.get("signals") or []
        ]
        zone_comparison = {
            "canonical_source": "V1统一交易区状态机",
            "canonical_zone": legacy_zone_name,
            "raw_v2_zone": raw_v2_zone.get("zone"),
            "different": legacy_zone_name != raw_v2_zone.get("zone"),
            "explanation": "V2保留自己的几何计算用于审计，但页面与后续买卖/风险逻辑统一采用V1主结论。",
        }
    else:
        zones = raw_v2_zone
        zone_comparison = {
            "canonical_source": "V2交易区计算",
            "canonical_zone": raw_v2_zone.get("zone"),
            "raw_v2_zone": raw_v2_zone.get("zone"),
            "different": False,
            "explanation": "V1没有可用的标准交易区结论，暂由V2计算结果承担研究展示。",
        }
    theme = _theme(context, features)
    patterns, pattern_detail = _pattern_data(features, main_force, zones, ma)
    stars, star_detail = _stars(features, zones, main_force, theme)
    character = _stock_character(features)
    profits = _profit_patterns(features, zones, main_force, patterns, stars)
    sell = _sell(risk, zones, stars, features)
    buy = _buy_point(features, zones, main_force, stars, patterns, risk)
    stacking = _stacking(zones, main_force, ma, patterns, stars, theme, character, buy, sell)

    # Start with every registered V2 skill so the UI can distinguish an
    # observed absence from a missing implementation. Specific modules then
    # replace the corresponding default records.
    signal_map = {definition["skill_id"]: _signal(definition["skill_id"]) for definition in V2_BOOK_SKILL_DEFINITIONS}
    for item in risk["signals"] + zones["signals"] + ma["signals"] + patterns + stars + profits + sell["signals"] + _buy_signals(buy):
        signal_map[item["skill_id"]] = item
    # Exact V2 names from the specification are aliases of the existing
    # explainable intent records; retaining both keeps old clients stable.
    intent_map = {"HQS_MAIN_001": ("拉高出货", "拉高出货"), "HQS_MAIN_002": ("拉高建仓", "拉高建仓"), "HQS_MAIN_003": ("拉高试盘", "拉高试盘"), "HQS_MAIN_004": ("建仓可行性试盘", "建仓可行性试盘"), "HQS_MAIN_005": ("正式拉升前试盘", "正式拉升前试盘")}
    for skill_id, (name, subtype) in intent_map.items():
        matched = main_force.get("intent") == name or main_force.get("intent_subtype") == subtype
        signal_map[skill_id] = _signal(skill_id, "CONFIRMED" if matched else "POSSIBLE" if main_force.get("intent") != "暂不判断" else "NOT_FOUND", main_force.get("confidence") if matched else 34 if main_force.get("intent") != "暂不判断" else None, subtype=subtype if matched else None, evidence=main_force.get("evidence", []) + [_evidence("程序化主力逻辑只陈述可观察证据", "observability_boundary", True)], counter_evidence=main_force.get("counter_evidence", []), next_confirmation=["观察后续成交与价格是否继续验证该假设"], invalidation=["后续证据反向破坏", "价格结构失守"], metrics={"intent": main_force.get("intent"), "intent_subtype": main_force.get("intent_subtype")})
    for skill_id, name in (("HQS_VOL_001", "量形态选股增强"), ("HQS_VOL_002", "主力量行为完整展开"), ("HQS_PRICE_001", "量异动"), ("HQS_PRICE_002", "价异动"), ("HQS_PRICE_003", "量价同步异动")):
        if skill_id == "HQS_VOL_001":
            score = (features["volume_ratio"] or 0) * 35
            evidence = [_evidence("异常量、持续量变化和价格反馈", "volume_ratio", features["volume_ratio"])]
        elif skill_id == "HQS_VOL_002":
            score = main_force.get("confidence")
            evidence = main_force.get("evidence", [])
        else:
            score = {"HQS_PRICE_001": (features["volume_ratio"] or 0) * 35, "HQS_PRICE_002": abs(features["changes"][-1] or 0) * 18 if features["changes"] else 0, "HQS_PRICE_003": ((features["volume_ratio"] or 0) * 25 + abs(features["changes"][-1] or 0) * 12) if features["changes"] else 0}[skill_id]
            evidence = [_evidence(name, "volume_price", score)]
        signal_map[skill_id] = _signal(skill_id, _status_for(_clamp(score or 0)), _clamp(score or 0) if score else None, evidence=evidence, next_confirmation=["观察异动持续性和价格响应"], invalidation=["异动失败或价格不能跟随"], metrics={"score": _round(score), "price_basis": features["price_basis"]})
    # Canonical Hunter skills are emitted in addition to the descriptive
    # aliases above.  This keeps the V2 registry IDs directly queryable.
    core_scores = {
        "HQS_001": qts.get("opportunity"),
        "HQS_002": qts.get("risk"),
        "HQS_003": (features["volume_ratio"] or 0) * 35 if features["volume_ratio"] is not None else None,
        "HQS_004": main_force.get("confidence"),
        "HQS_005": (features["volume_ratio"] or 0) * 25 + abs(features["changes"][-1] or 0) * 12 if features["changes"] else None,
        "HQS_006": {"均线密集": 48, "均线穿越": 58, "均线翘头": 68, "均线发散": 78, "均线顺畅": 88}.get(ma.get("stage")),
        "HQS_007": 65 if (features["volume_ratio"] or 0) >= 1.5 and (features["close"] or 0) >= (features["ma20"] or features["close"] or 0) else 0,
    }
    core_evidence = {
        "HQS_001": qts.get("time", {}).get("evidence", []) + qts.get("space", {}).get("evidence", []),
        "HQS_002": risk.get("signals", []),
        "HQS_003": [_evidence("量形态、持续性和价格反馈", "volume_ratio", features["volume_ratio"])],
        "HQS_004": main_force.get("evidence", []),
        "HQS_005": [_evidence("量价同步分数", "sync_score", core_scores["HQS_005"])],
        "HQS_006": ma.get("signals", [])[:2],
        "HQS_007": [_evidence("量价变化与均线归位关系", "ma_recovery", core_scores["HQS_007"])],
    }
    for skill_id, score in core_scores.items():
        score = _clamp(score) if score is not None else None
        signal_map[skill_id] = _signal(skill_id, _status_for(score), score, evidence=core_evidence.get(skill_id) or [], next_confirmation=["后续成交、价格和结构继续验证"], invalidation=["关键结构失守", "量价响应失败"], metrics={"score": _round(score), "price_basis": features["price_basis"]})
    # The four documented profit-pattern IDs are top-level summaries of their
    # more specific subtype observations.
    profit_aliases = {
        "HQS_011": ("HQS_011", "强势结点盈利模式"),
        "HQS_012": ("HQS_WASH_001", "单日强硬洗盘盈利模式"),
        "HQS_013": ("HQS_GAP_001", "缺口盈利模式"),
        "HQS_014": ("HQS_CYCLE_001", "强势循环低点盈利模式"),
    }
    for skill_id, (source_id, fallback_name) in profit_aliases.items():
        source = next((item for item in profits if item["skill_id"] == source_id), None)
        if source:
            signal_map[skill_id] = _signal(skill_id, source.get("status", "NOT_FOUND"), source.get("confidence"), subtype=source.get("subtype") or fallback_name, evidence=source.get("evidence") or [], counter_evidence=source.get("counter_evidence") or [], next_confirmation=source.get("next_confirmation") or [], invalidation=source.get("invalidation") or [], conflicts=source.get("conflicts") or [], chart_annotations=source.get("chart_annotations") or [], metrics=source.get("metrics") or {})
        else:
            signal_map[skill_id] = _signal(skill_id, "NOT_FOUND", subtype=fallback_name, evidence=[_evidence("当前未形成该盈利模式", "pattern", False)])
    profits.extend(signal_map[item].copy() for item in ("HQS_012", "HQS_013", "HQS_014") if item in signal_map)
    signal_map["HQS_018"] = _signal("HQS_018", "CONFIRMED" if stacking["level"] in {"强", "很强"} else "POSSIBLE" if stacking["level"] in {"中", "弱"} else "NOT_FOUND", {"很强": 82, "强": 72, "中": 55, "弱": 35}.get(stacking["level"]), evidence=[_evidence("量能体叠加路径", "stacking_level", stacking["level"])], conflicts=stacking["conflicts"], metrics={"path_nodes": len(stacking["confirmed"]) + len(stacking["possible"])})
    signal_map["HQS_019"] = _signal("HQS_019", "CONFIRMED" if theme.get("volume_energy_verified") else "POSSIBLE" if theme.get("status") == "AVAILABLE" else "NOT_FOUND", 70 if theme.get("volume_energy_verified") else 42 if theme.get("status") == "AVAILABLE" else None, evidence=theme.get("evidence", []), next_confirmation=["板块资金与个股量能继续同向"], invalidation=["板块资金转弱", "题材热点衰退"], metrics={"theme_type": theme.get("theme_type"), "hotspot_level": theme.get("hotspot_level")})
    signal_map["HQS_020"] = _signal("HQS_020", "POSSIBLE" if bars else "NOT_FOUND", 38 if bars else None, evidence=[_evidence("可进入望星空和历史案例对照", "case_library", bool(bars))], next_confirmation=["完成正反案例标注后比较"], invalidation=["没有可比历史样本"])
    # Theme registry signals are separate from the detailed theme object.
    for skill_id, name in (("HQS_THEME_TYPE_001", "主流题材"), ("HQS_THEME_TYPE_002", "一般题材"), ("HQS_THEME_TYPE_003", "空穴来风的题材"), ("HQS_THEME_001", "强势热点"), ("HQS_THEME_002", "局部热点"), ("HQS_THEME_003", "一般热点")):
        active = theme.get("theme_type") == name or theme.get("hotspot_level") == name
        signal_map[skill_id] = _signal(skill_id, "CONFIRMED" if active else "NOT_FOUND", 70 if active else None, evidence=theme.get("evidence", []), next_confirmation=["板块强度、资金和持续性继续确认"], invalidation=["热点等级下降", "题材证据消失"], metrics={"theme_type": theme.get("theme_type"), "hotspot_level": theme.get("hotspot_level")})
    signals = list(signal_map.values())
    consensus = _consensus(zones, patterns, stars, main_force, theme, sell, legacy.get("decision") if legacy else None)
    # Add consensus conflicts to the relevant signals without mutating the
    # legacy result. The conflict matrix is explicit and risk-dominant.
    if consensus["conflicts"]:
        for item in signals:
            if item["skill_id"] in {"HQS_010", "HQS_015", "HQS_017", "BXZX_009", "BXZX_010"}:
                item["conflicts"].extend(conflict["text"] for conflict in consensus["conflicts"] if conflict["text"] not in item["conflicts"])
    timeline = _timeline(bars, signals, zones, ma)
    explanation = _explanation({"risk": risk, "quantity_time_space": qts, "zones": zones, "consensus": consensus, "signals": signals})
    empirical = {"validation_status": "NOT_TESTED", "sample_size": 0, "win_rate": None, "expected_return": None, "action_impact": "DISABLED_UNTIL_VALIDATED", "method": "尚未完成样本外验证；Shadow层不改变旧ACTION"}
    annotations = [annotation for item in signals for annotation in item.get("chart_annotations", [])]
    legacy_decision = (legacy or {}).get("decision") or {}
    return {
        "module_id": V2_ENGINE_VERSION,
        "engine_version": V2_ENGINE_VERSION,
        "mode": "SHADOW",
        "symbol": context.get("symbol"),
        "name": context.get("name"),
        "trade_date": features.get("bars_end"),
        "legacy_module_id": (legacy or {}).get("module_id", "STRONG_STOCK_DECISION_V1"),
        "legacy_action": legacy_decision.get("action"),
        "action": legacy_decision.get("action", "NO_TRADE"),
        "state_code": legacy_decision.get("state_code", "S0"),
        "state_name": legacy_decision.get("state_name", "无明显机会"),
        "risk": risk,
        "quantity_time_space": qts,
        "main_force": main_force,
        "volume_price": _volume_price(features),
        "moving_average": ma,
        "zones": zones,
        "zone_comparison": zone_comparison,
        "three_degree": pattern_detail["three_degree"],
        "big_patterns": patterns,
        "stars": stars,
        "profit_patterns": profits,
        "stock_character": character,
        "stacking": stacking,
        "theme": theme,
        "buy_point": buy,
        "sell": sell,
        "wang_xing_kong": {"status": "AVAILABLE" if bars else "INSUFFICIENT_DATA", "success_cases": [], "failure_cases": [], "closest_success": None, "closest_failure": None, "why_similar": [], "why_different": [], "note": "未标注案例不伪装成正例；历史相似度将在案例库补齐后计算。"},
        "consensus": consensus,
        "timeline": timeline,
        "explanation": explanation,
        "annotations": annotations,
        "empirical_layer": empirical,
        "signals": signals,
        "engine_features": {key: value for key, value in features.items() if key not in {"closes", "opens", "highs", "lows", "volumes", "amounts", "changes", "highs_shaped", "lows_shaped"}},
        "source_status": context.get("source_status") or {},
        "data_cutoff_time": context.get("data_cutoff_time"),
        "data_quality": {"bar_count": features["count"], "price_basis": features["price_basis"], "status": "AVAILABLE" if bars else "INSUFFICIENT_DATA", "note": "字段缺失保持透明，不用估算值伪装完整数据。"},
    }


__all__ = ["V2_ENGINE_VERSION", "build_v2"]
