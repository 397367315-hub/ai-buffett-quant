"""Deterministic calculators for the nine V5 trading skills.

The calculators classify observable structures. They never emit broker orders,
"must buy" language, or unverifiable participant intent.
"""

from __future__ import annotations

import math
from typing import Any, Callable


# Keep the machine-readable stage for audits, but never require users to
# understand internal classifier codes in the research UI.
STAGE_LABELS: dict[str, str] = {
    "INSUFFICIENT_DATA": "数据不足，暂不能判断",
    "EFFICIENT_UP": "量价效率改善",
    "INEFFICIENT_UP": "上涨但量价效率下降",
    "EFFICIENT_DOWN": "下跌中的量价效率",
    "ABSORBED_DOWN": "下跌但承接较强",
    "ABSORPTION": "承接增强",
    "SELL_PRESSURE": "抛压增强",
    "BALANCED": "承接与抛压平衡",
    "VOLUME_SHOCK": "成交异常触发",
    "NO_RECENT_EVENT": "暂无近期异常成交",
    "DISTRIBUTION_RISK": "异常成交后失守风险",
    "TREND_CONFIRMATION": "异常成交后趋势确认",
    "PANIC_EXCHANGE": "恐慌放量换手",
    "NOISE": "异常成交未形成明确结构",
    "RECLAIM_PENDING_CONFIRM": "假跌破回收，待确认",
    "NO_RECLAIM": "未形成回收结构",
    "REACCEL_CONFIRMED": "二次启动确认",
    "REACCEL_FORMING": "二次启动形成",
    "BASE": "缩量整理",
    "PULLBACK": "趋势回调",
    "FAILED": "趋势回调失效",
    "NO_PRIOR_TREND": "前期趋势不足",
    "NONE": "未形成低位异动",
    "FIRST_SHOCK": "首次成交异动",
    "CONTRACTION": "异动后成交收敛",
    "SECOND_LAUNCH_CONFIRMED": "二次启动确认",
    "SECOND_LAUNCH_FORMING": "二次启动形成",
    "HIGH_QUALITY": "高质量突破",
    "MEDIUM": "中等质量突破",
    "FALSE_BREAKOUT_RISK": "疑似假突破风险",
    "LOW_QUALITY": "突破质量偏低",
    "NO_BREAKOUT": "尚未形成突破",
    "FOMO": "追涨行为风险",
    "PANIC": "恐慌行为风险",
    "FAKE_BREAKOUT": "假突破行为风险",
    "BEHAVIORAL_OVERSHOOT": "行为过冲",
    "WAIT": "等待竞价/分时数据",
    "CONFIRM": "竞价与分时确认",
    "WEAK_CONFIRM": "弱确认，仍需观察",
    "REJECT": "竞价/分时确认未通过",
    "PANIC_ABSORPTION_CANDIDATE": "恐慌吸收候选",
    "ALPHA_SEED_REFLEXIVITY": "Alpha萌芽反身性",
    "POSITIVE_REFLEXIVITY_CANDIDATE": "正向反身候选",
    "HIGH_LEVEL_REFLEXIVITY_DECAY": "高位反身性衰减",
    "NEGATIVE_REFLEXIVITY_ACCELERATION": "负向反身性加速",
    "POSITIVE_REFLEXIVITY": "正向反身性增强",
    "NEGATIVE_REFLEXIVITY": "负向反身性",
    "NEUTRAL": "反身性暂未形成",
}


def stage_label(stage: str | None) -> str:
    """Return a stable Chinese label while preserving unknown codes safely."""
    normalized = str(stage or "").strip()
    if not normalized:
        return "等待核验"
    return STAGE_LABELS.get(normalized, normalized.replace("_", " "))


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        output = float(value)
    except (TypeError, ValueError):
        return None
    return output if math.isfinite(output) else None


def _clamp(value: float, lower: float = 0, upper: float = 100) -> float:
    return max(lower, min(upper, value))


def _weighted(parts: list[tuple[float | None, float]]) -> float | None:
    valid = [(value, weight) for value, weight in parts if value is not None and weight > 0]
    if not valid:
        return None
    return sum(value * weight for value, weight in valid) / sum(weight for _, weight in valid)


def _ratio_score(value: float | None, low: float, high: float) -> float | None:
    if value is None or high <= low:
        return None
    return _clamp((value - low) / (high - low) * 100)


def _evidence(label: str, value: Any, condition: str, *, source: str = "PIT feature engine") -> dict[str, Any]:
    if isinstance(value, float):
        value = round(value, 4)
    return {"label": label, "value": value, "condition": condition, "source": source}


def _result(
    skill_id: str,
    skill_name: str,
    *,
    detected: bool,
    stage: str,
    score: float | None,
    evidence: list[dict[str, Any]],
    invalidation: list[str],
    missing: list[str] | None = None,
    signal_type: str = "WATCH",
    next_confirmation: list[str] | None = None,
    horizon: str = "3-5日",
    data_level: str = "DAILY",
) -> dict[str, Any]:
    bounded_score = round(_clamp(score), 1) if score is not None else None
    return {
        "skill_id": skill_id, "skill_name": skill_name, "detected": bool(detected),
        "stage": stage, "stage_label": stage_label(stage), "score": bounded_score, "confidence_pct": bounded_score,
        "signal_type": signal_type if detected else ("INSUFFICIENT_DATA" if missing else "NO_SIGNAL"),
        "horizon": horizon, "data_level": data_level, "evidence": evidence,
        "invalidation_conditions": invalidation, "next_confirmation": next_confirmation or [],
        "missing_factors": sorted(set(missing or [])), "direct_order": False,
        "language_boundary": "只输出候选、风险与确认条件，不构成买卖指令。",
    }


def price_volume_efficiency(features: dict[str, Any]) -> dict[str, Any]:
    required = ["r_1d", "amount_ratio_20", "close_location", "upper_wick_ratio"]
    missing = [key for key in required if _number(features.get(key)) is None]
    r1 = _number(features.get("r_1d"))
    efficiency = _number(features.get("raw_efficiency"))
    delta = _number(features.get("eff_delta"))
    amount_z = _number(features.get("amount_z_60"))
    close_location = _number(features.get("close_location"))
    upper = _number(features.get("upper_wick_ratio"))
    relative = _number(features.get("relative_sector_1d"))
    stage = "INSUFFICIENT_DATA"
    score: float | None = None
    detected = False
    if not missing and r1 is not None and efficiency is not None:
        stagnation = _weighted([
            (_ratio_score(amount_z, 0, 3), 0.30),
            (_clamp((0.02 - abs(r1)) / 0.02 * 100) if r1 is not None else None, 0.15),
            (_ratio_score(1 - close_location, 0.3, 0.9), 0.30),
            (_ratio_score(upper, 0.1, 0.6), 0.25),
        ])
        absorption = _weighted([
            (_ratio_score(amount_z, 0.5, 3), 0.25),
            (_ratio_score(-r1, 0, 0.08), 0.15),
            (_ratio_score(close_location, 0.45, 0.95), 0.30),
            (_ratio_score(relative, -0.01, 0.04), 0.30),
        ])
        if r1 > 0 and (stagnation or 0) >= 62:
            stage, score = "INEFFICIENT_UP", stagnation
        elif r1 > 0:
            improving = 50 + min(max(efficiency, -0.04), 0.04) / 0.04 * 30
            if delta is not None:
                improving += min(max(delta, -0.02), 0.02) / 0.02 * 20
            stage, score = "EFFICIENT_UP", _clamp(improving)
        elif r1 < 0 and (absorption or 0) >= 58:
            stage, score = "ABSORBED_DOWN", absorption
        else:
            stage = "EFFICIENT_DOWN"
            score = _weighted([
                (_ratio_score(-r1, 0, 0.08), 0.5),
                (_ratio_score(1 - close_location, 0.2, 0.9), 0.5),
            ])
        detected = bool(score is not None and score >= 55)
    return _result(
        "skill_01_price_volume_efficiency", "量价效率", detected=detected, stage=stage,
        score=score, missing=missing,
        evidence=[
            _evidence("1日收益", r1, "signed return"),
            _evidence("20日成交额比", _number(features.get("amount_ratio_20")), "相对前20日中位数"),
            _evidence("原始推动效率", efficiency, "return / log(1 + amount ratio)"),
            _evidence("效率变化", delta, "3日效率均值的变化"),
        ],
        invalidation=["效率变化转负且持续", "上影与放量滞涨同步增强", "相对板块强度转弱"],
        next_confirmation=["观察后续1-3日效率是否保持", "结合承接/抛压与板块许可复核"],
        signal_type="CANDIDATE" if stage in {"EFFICIENT_UP", "ABSORBED_DOWN"} else "RISK",
    )


def absorption_pressure(features: dict[str, Any]) -> dict[str, Any]:
    required = ["lower_wick_ratio", "upper_wick_ratio", "close_location", "recovery_from_low"]
    missing = [key for key in required if _number(features.get(key)) is None]
    lower = _number(features.get("lower_wick_ratio"))
    upper = _number(features.get("upper_wick_ratio"))
    location = _number(features.get("close_location"))
    recovery = _number(features.get("recovery_from_low"))
    relative = _number(features.get("relative_sector_1d"))
    amount_z = _number(features.get("amount_z_60"))
    turnover_ratio = _number(features.get("turnover_ratio_20"))
    absorption = _weighted([
        (_ratio_score(lower, 0, 0.55), 1), (_ratio_score(location, 0.25, 0.95), 1),
        (_ratio_score(recovery, 0.1, 1), 1), (_ratio_score(relative, -0.03, 0.05), 1),
        (_ratio_score((amount_z or 0) * (recovery or 0), 0, 2.5) if amount_z is not None else None, 1),
        (_ratio_score(1 - (upper or 0), 0.35, 1) if upper is not None else None, 1),
    ])
    pressure = _weighted([
        (_ratio_score(upper, 0, 0.55), 1), (_ratio_score(amount_z, 0, 3), 1),
        (_ratio_score(1 - location, 0.15, 0.9) if location is not None else None, 1),
        (_ratio_score(turnover_ratio, 0.8, 2.5), 1),
        (_ratio_score(-(relative or 0), -0.02, 0.05) if relative is not None else None, 1),
    ])
    if missing:
        stage, score, detected = "INSUFFICIENT_DATA", None, False
    elif (absorption or 0) >= 62 and (absorption or 0) >= (pressure or 0) + 8:
        stage, score, detected = "ABSORPTION", absorption, True
    elif (pressure or 0) >= 62 and (pressure or 0) > (absorption or 0):
        stage, score, detected = "SELL_PRESSURE", pressure, True
    else:
        stage, score, detected = "BALANCED", max(absorption or 0, pressure or 0), False
    return _result(
        "skill_02_absorption_pressure", "承接/抛压", detected=detected, stage=stage,
        score=score, missing=missing,
        evidence=[
            _evidence("承接分", absorption, "影线、收盘位置、恢复与相对强度联合"),
            _evidence("抛压分", pressure, "上影、异常成交、换手与弱相对强度联合"),
            _evidence("低点恢复", recovery, "close-low relative to previous close-low"),
        ],
        invalidation=["关键支撑失守", "抛压分超过承接分", "相对板块强度继续走弱"],
        next_confirmation=["有分钟线时核验VWAP收复和站稳时长", "观察次日是否不破事件低点"],
        signal_type="CANDIDATE" if stage == "ABSORPTION" else "RISK",
        data_level="DAILY_PLUS_INTRADAY",
    )


def abnormal_turnover(features: dict[str, Any]) -> dict[str, Any]:
    required = ["amount_z_60", "amount_ratio_20", "shock_anchor"]
    # A trigger can be valid with either z-score or amount ratio, so only fail
    # when both independent turnover measurements are absent.
    missing = []
    if _number(features.get("amount_z_60")) is None and _number(features.get("amount_ratio_20")) is None:
        missing.append("amount_z_60/amount_ratio_20")
    shock_anchor = _number(features.get("shock_anchor"))
    if shock_anchor is None:
        missing.append("shock_anchor")
    age = _number(features.get("shock_age"))
    retention = _number(features.get("price_retention"))
    contraction = _number(features.get("volume_contraction_after_shock"))
    close = _number(features.get("close"))
    shock_low = _number(features.get("shock_low"))
    location = _number(features.get("close_location"))
    r1 = _number(features.get("r_1d"))
    if shock_anchor is None or age is None or age > 5:
        stage, score, detected = "NO_RECENT_EVENT", None, False
    elif age == 0:
        stage = "VOLUME_SHOCK"
        score = _weighted([
            (_ratio_score(_number(features.get("shock_amount_z")), 1.5, 4), 0.6),
            (_ratio_score(_number(features.get("amount_ratio_20")), 1.5, 3), 0.4),
        ])
        detected = True
    elif close is not None and shock_low is not None and close < shock_low:
        stage, score, detected = "DISTRIBUTION_RISK", _ratio_score(-min(retention or 0, 0), 0, 0.12), True
    elif retention is not None and retention > 0 and (contraction is None or contraction < 0.85):
        stage, score, detected = "TREND_CONFIRMATION", _weighted([
            (_ratio_score(retention, 0, 0.12), 0.6),
            (_ratio_score(1 - contraction, 0, 0.7) if contraction is not None else None, 0.4),
        ]), True
    elif r1 is not None and r1 < 0 and (location or 0) >= 0.65:
        stage, score, detected = "PANIC_EXCHANGE", _ratio_score(location, 0.5, 1), True
    else:
        stage, score, detected = "NOISE", 40.0, False
    return _result(
        "skill_03_abnormal_turnover", "异常成交跟踪", detected=detected, stage=stage,
        score=score, missing=missing,
        evidence=[
            _evidence("事件距今天数", age, "0/T+1/T+2/T+3/T+5"),
            _evidence("事件成本锚", shock_anchor, "日线典型价格(high+low+close)/3"),
            _evidence("价格保留率", retention, "close / shock anchor - 1"),
            _evidence("事件后成交收敛", contraction, "current amount / shock amount"),
        ],
        invalidation=["收盘跌破异常成交日低点", "5日超额收益转负", "事件后成交放大但价格效率下降"],
        next_confirmation=["在T+1/T+2/T+3/T+5继续记录保留率", "结合Alpha与板块同步性分类"],
        signal_type="CANDIDATE" if stage in {"TREND_CONFIRMATION", "PANIC_EXCHANGE"} else "RISK" if stage == "DISTRIBUTION_RISK" else "WATCH",
    )


def false_breakdown_reclaim(features: dict[str, Any]) -> dict[str, Any]:
    required = ["atr14", "support_level", "break_depth_atr", "reclaim_margin", "close_location"]
    missing = [key for key in required if _number(features.get(key)) is None]
    depth = _number(features.get("break_depth_atr"))
    margin = _number(features.get("reclaim_margin"))
    location = _number(features.get("close_location"))
    relative = _number(features.get("relative_sector_1d"))
    recovery = _number(features.get("recovery_ratio"))
    candidate = bool(
        not missing and depth is not None and depth > 0.08
        and margin is not None and margin > 0 and location is not None and location >= 0.55
        and (relative is None or relative >= -0.005)
    )
    score = _weighted([
        (_ratio_score(depth, 0.08, 0.8), 0.20), (_ratio_score(margin, 0, 0.8), 0.25),
        (_ratio_score(location, 0.5, 0.95), 0.25), (_ratio_score(recovery, 0.5, 1.2), 0.15),
        (_ratio_score(relative, -0.01, 0.04), 0.15),
    ]) if candidate else None
    return _result(
        "skill_04_false_breakdown_reclaim", "假跌破回收", detected=candidate,
        stage="RECLAIM_PENDING_CONFIRM" if candidate else "INSUFFICIENT_DATA" if missing else "NO_RECLAIM",
        score=score, missing=missing,
        evidence=[
            _evidence("关键支撑", _number(features.get("support_level")), "前20/60日可观察低点"),
            _evidence("跌破深度/ATR", depth, "support-low divided by ATR14"),
            _evidence("收回幅度/ATR", margin, "close-support divided by ATR14"),
            _evidence("相对板块", relative, "stock return minus sector return"),
        ],
        invalidation=["再次跌破事件低点", "次日相对强度为负且未收复VWAP", "板块许可被撤销"],
        next_confirmation=["次日不破事件低点", "次日相对强度或VWAP站稳至少一项成立"],
        signal_type="CANDIDATE", horizon="3-10日",
    )


def trend_reacceleration(features: dict[str, Any]) -> dict[str, Any]:
    required = ["return_20d", "pullback_days", "pullback_atr", "volume_contraction"]
    missing = [key for key in required if _number(features.get(key)) is None]
    trend = _number(features.get("return_20d"))
    days = _number(features.get("pullback_days"))
    depth = _number(features.get("pullback_atr"))
    contraction = _number(features.get("volume_contraction"))
    vol_contraction = _number(features.get("volatility_contraction"))
    efficiency_delta = _number(features.get("eff_delta"))
    close = _number(features.get("close"))
    pullback_high = _number(features.get("recent_pullback_high"))
    relative = _number(features.get("relative_sector_1d"))
    sector_state = str(features.get("sector_state") or "")
    original_trend = bool(trend is not None and trend >= 0.06)
    quality_pullback = bool(
        original_trend and days is not None and 2 <= days <= 20 and depth is not None and depth <= 3.5
        and contraction is not None and contraction <= 0.90
        and (vol_contraction is None or vol_contraction <= 1.05)
    )
    restarted = bool(
        quality_pullback and close is not None and pullback_high is not None and close > pullback_high
        and (_number(features.get("amount_ratio_20")) or 0) > 1.05
        and (relative is None or relative > 0) and (efficiency_delta is None or efficiency_delta > 0)
    )
    if restarted:
        stage = "REACCEL_CONFIRMED" if sector_state in {"启势", "顺势", "盛势", "强化"} else "REACCEL_FORMING"
    elif quality_pullback:
        stage = "BASE" if contraction is not None and contraction < 0.75 else "PULLBACK"
    elif original_trend:
        stage = "FAILED" if depth is not None and depth > 3.5 else "PULLBACK"
    else:
        stage = "NO_PRIOR_TREND"
    detected = stage in {"BASE", "REACCEL_FORMING", "REACCEL_CONFIRMED"}
    score = _weighted([
        (_ratio_score(trend, 0.04, 0.25), 0.20),
        (_ratio_score(1 - contraction, 0, 0.6) if contraction is not None else None, 0.20),
        (_ratio_score(3.5 - depth, 0, 3.5) if depth is not None else None, 0.15),
        (_ratio_score(_number(features.get("amount_ratio_20")), 0.9, 2.0), 0.15),
        (_ratio_score(relative, -0.02, 0.05), 0.15),
        (_ratio_score(efficiency_delta, -0.01, 0.02), 0.15),
    ]) if detected else None
    return _result(
        "skill_05_trend_reacceleration", "趋势二次启动", detected=detected, stage=stage,
        score=score, missing=missing,
        evidence=[
            _evidence("20日趋势收益", trend, "原趋势强度"), _evidence("回调天数", days, "搜索空间2-20日"),
            _evidence("回调深度/ATR", depth, "峰值与现价距离"),
            _evidence("回调成交收敛", contraction, "回调均额/前段推动均额"),
            _evidence("效率变化", efficiency_delta, "再启动质量"),
        ],
        invalidation=["回调超过校准ATR阈值", "成交与波动在下跌中扩张", "Alpha或板块许可失效"],
        next_confirmation=["突破回调结构高点", "成交重新扩张且相对板块为正"],
        signal_type="CANDIDATE", horizon="5-20日",
    )


def low_position_relaunch(features: dict[str, Any]) -> dict[str, Any]:
    required = ["position_120", "amount_z_60", "shock_age", "shock_low"]
    missing = [key for key in required if _number(features.get(key)) is None]
    position = _number(features.get("position_120"))
    amount_z = _number(features.get("amount_z_60"))
    age = _number(features.get("shock_age"))
    close = _number(features.get("close"))
    shock_low = _number(features.get("shock_low"))
    shock_high = _number(features.get("shock_high"))
    contraction = _number(features.get("volume_contraction_after_shock"))
    relative = _number(features.get("relative_sector_1d"))
    amount_ratio = _number(features.get("amount_ratio_20"))
    low_position = position is not None and position <= 0.42
    event_holds = close is not None and shock_low is not None and close >= shock_low
    if not low_position:
        stage = "NONE"
    elif age == 0 and amount_z is not None and amount_z >= 1.8 and (relative is None or relative >= 0):
        stage = "FIRST_SHOCK"
    elif age is not None and 1 <= age <= 15 and event_holds and contraction is not None and contraction <= 0.80:
        stage = "CONTRACTION"
        if close is not None and shock_high is not None and close > shock_high and (amount_ratio or 0) > 1.2 and (relative is None or relative > 0):
            stage = "SECOND_LAUNCH_CONFIRMED" if str(features.get("sector_state") or "") in {"启势", "顺势", "强化"} else "SECOND_LAUNCH_FORMING"
    elif age is not None and event_holds:
        stage = "FIRST_SHOCK"
    else:
        stage = "FAILED" if age is not None else "NONE"
    detected = stage in {"FIRST_SHOCK", "CONTRACTION", "SECOND_LAUNCH_FORMING", "SECOND_LAUNCH_CONFIRMED"}
    score = _weighted([
        (_ratio_score(0.5 - position, 0, 0.5) if position is not None else None, 0.25),
        (_ratio_score(amount_z, 1.5, 4), 0.20),
        (_ratio_score(1 - contraction, 0, 0.7) if contraction is not None else None, 0.20),
        (_ratio_score(relative, -0.02, 0.05), 0.15),
        (_ratio_score(amount_ratio, 0.8, 2.2), 0.20),
    ]) if detected else None
    return _result(
        "skill_06_low_position_relaunch", "低位异动-收敛-再启动", detected=detected,
        stage=stage, score=score, missing=missing,
        evidence=[
            _evidence("120日位置", position, "(close-low120)/(high120-low120)"),
            _evidence("首次异动成交Z", amount_z, "前60日基线"),
            _evidence("事件后成交收敛", contraction, "当前成交额/异动日成交额"),
            _evidence("事件低点保持", event_holds, "close >= event low"),
        ],
        invalidation=["跌破首次异动低点", "Alpha快速衰减", "高位伪低价或重大财务风险"],
        next_confirmation=["收敛期间量与波动继续下降", "第二次放量突破事件高点并获得板块确认"],
        signal_type="CANDIDATE", horizon="5-20日",
    )


def breakout_quality(features: dict[str, Any]) -> dict[str, Any]:
    required = ["close", "rolling_high_20", "amount_ratio_20", "close_location"]
    missing = [key for key in required if _number(features.get(key)) is None]
    close = _number(features.get("close"))
    high20 = _number(features.get("rolling_high_20"))
    high60 = _number(features.get("rolling_high_60"))
    breakout_level = max(value for value in (high20, high60) if value is not None) if any(value is not None for value in (high20, high60)) else None
    triggered = bool(close is not None and breakout_level is not None and close > breakout_level)
    breakout_return = close / breakout_level - 1 if triggered and breakout_level else None
    amount_ratio = _number(features.get("amount_ratio_20"))
    location = _number(features.get("close_location"))
    sector_strength = _number(features.get("sector_strength"))
    sector_breadth = _number(features.get("sector_breadth"))
    relative = _number(features.get("relative_sector_1d"))
    crowding = _number(features.get("crowding_score"))
    upper = _number(features.get("upper_wick_ratio"))
    score = _weighted([
        (_ratio_score(breakout_return, 0, 0.06), 0.10),
        (_ratio_score(amount_ratio, 0.9, 2.5), 0.18),
        (_ratio_score(location, 0.45, 0.95), 0.15),
        (_ratio_score(sector_strength, 35, 80), 0.12),
        (_ratio_score(sector_breadth, 35, 80), 0.10),
        (_ratio_score(relative, -0.01, 0.05), 0.15),
        (_ratio_score(100 - crowding, 20, 80) if crowding is not None else None, 0.10),
        (_ratio_score(1 - upper, 0.4, 1) if upper is not None else None, 0.10),
    ]) if triggered else None
    if not triggered:
        stage = "INSUFFICIENT_DATA" if missing else "NO_BREAKOUT"
    elif (score or 0) >= 72:
        stage = "HIGH_QUALITY"
    elif (score or 0) >= 56:
        stage = "MEDIUM"
    elif (amount_ratio or 0) < 1.05 or (relative is not None and relative <= 0) or (crowding or 0) >= 80:
        stage = "FALSE_BREAKOUT_RISK"
    else:
        stage = "LOW_QUALITY"
    return _result(
        "skill_07_breakout_quality", "突破质量", detected=triggered, stage=stage,
        score=score, missing=missing,
        evidence=[
            _evidence("突破参考位", breakout_level, "前20/60日高点，不含当日"),
            _evidence("突破幅度", breakout_return, "close/breakout level-1"),
            _evidence("成交扩张", amount_ratio, "当日成交额/前20日中位数"),
            _evidence("板块相对强度", relative, "个股收益-板块收益"),
            _evidence("拥挤风险", crowding, "V5行为层"),
        ],
        invalidation=["未来3日收盘重新跌破突破位", "板块宽度不扩散", "Alpha未增强且尾盘抛压上升"],
        next_confirmation=["资金持续、板块宽度和Alpha至少两项继续增强", "回踩突破位不破"],
        signal_type="CANDIDATE" if stage in {"HIGH_QUALITY", "MEDIUM"} else "RISK",
        horizon="3-10日",
    )


def behavior_imbalance(features: dict[str, Any]) -> dict[str, Any]:
    acceleration = _number(features.get("return_acceleration"))
    turnover_acceleration = _number(features.get("turnover_acceleration"))
    crowding = _number(features.get("crowding_score"))
    imbalance = _number(features.get("behavior_imbalance_score"))
    fomo = _number(features.get("fomo_score"))
    panic = _number(features.get("panic_score"))
    location = _number(features.get("close_location"))
    relative = _number(features.get("relative_sector_1d"))
    observed = [value for value in (acceleration, turnover_acceleration, crowding, imbalance, fomo, panic) if value is not None]
    missing = [] if len(observed) >= 3 else ["至少3项行为因子"]
    local_fomo = _weighted([
        (_ratio_score(acceleration, 0, 0.05), 0.30),
        (_ratio_score(turnover_acceleration, 1, 3), 0.25),
        (_ratio_score(crowding, 50, 95), 0.25), (_ratio_score(fomo, 45, 90), 0.20),
    ])
    local_panic = _weighted([
        (_ratio_score(-acceleration, 0, 0.05) if acceleration is not None else None, 0.25),
        (_ratio_score(turnover_acceleration, 1, 3), 0.20),
        (_ratio_score(panic, 45, 90), 0.30),
        (_ratio_score(1 - location, 0.3, 0.9) if location is not None else None, 0.25),
    ])
    false_breakout = _weighted([
        (_ratio_score(crowding, 65, 95), 0.35),
        (_ratio_score(-relative, -0.02, 0.05) if relative is not None else None, 0.25),
        (_ratio_score(1 - location, 0.25, 0.8) if location is not None else None, 0.20),
        (_ratio_score(imbalance, 55, 90), 0.20),
    ])
    candidates = {"FOMO": local_fomo, "PANIC": local_panic, "FAKE_BREAKOUT": false_breakout, "BEHAVIORAL_OVERSHOOT": imbalance}
    stage, score = max(candidates.items(), key=lambda item: item[1] if item[1] is not None else -1)
    if score is None or score < 58 or missing:
        stage = "BALANCED" if not missing else "INSUFFICIENT_DATA"
        detected = False
    else:
        detected = True
    return _result(
        "skill_08_behavior_imbalance", "行为失衡", detected=detected, stage=stage,
        score=score, missing=missing,
        evidence=[
            _evidence("收益加速度", acceleration, "今日收益减昨日收益"),
            _evidence("换手加速度", turnover_acceleration, "当前换手/前5日中位数"),
            _evidence("市场行为失衡", imbalance, "V5行为层"),
            _evidence("拥挤", crowding, "位置与一致性风险"),
        ],
        invalidation=["行为失衡变化率回落", "市场宽度和Alpha重新扩散", "价格重新得到资金持续性确认"],
        next_confirmation=["观察1-3日状态迁移，不把短线行为外推到月度", "与价格位置和底层势共同判断"],
        signal_type="RISK", horizon="1-3日", data_level="MARKET_PLUS_DAILY",
    )


def auction_intraday_confirm(features: dict[str, Any]) -> dict[str, Any]:
    required = ["auction_gap", "auction_amount_ratio"]
    missing = [key for key in required if _number(features.get(key)) is None]
    if not features.get("auction_observed"):
        missing.append("verified_09_25_auction_observation")
    gap = _number(features.get("auction_gap"))
    amount_ratio = _number(features.get("auction_amount_ratio"))
    relative_sector = _number(features.get("auction_relative_sector"))
    ret5 = _number(features.get("ret_5m"))
    ret15 = _number(features.get("ret_15m"))
    vwap_reclaim = features.get("vwap_reclaim") if isinstance(features.get("vwap_reclaim"), bool) else None
    hold = _number(features.get("vwap_hold_minutes"))
    drawdown = _number(features.get("first_15m_drawdown"))
    if missing:
        stage, score, detected = "WAIT", None, False
    else:
        auction_score = _weighted([
            (_ratio_score(gap, -0.01, 0.05), 0.30),
            (_ratio_score(amount_ratio, 1, 5), 0.35),
            (_ratio_score(relative_sector, -0.015, 0.03), 0.35),
        ])
        opening_score = _weighted([
            (_ratio_score(ret5, -0.02, 0.04), 0.25), (_ratio_score(ret15, -0.03, 0.06), 0.25),
            (100.0 if vwap_reclaim else 0.0 if vwap_reclaim is not None else None, 0.25),
            (_ratio_score(hold, 0, 15), 0.15), (_ratio_score(drawdown, -0.06, 0), 0.10),
        ])
        score = _weighted([(auction_score, 0.55), (opening_score, 0.45)])
        if opening_score is None:
            stage = "WEAK_CONFIRM" if (auction_score or 0) >= 62 else "WAIT"
        elif (score or 0) >= 70:
            stage = "CONFIRM"
        elif (score or 0) >= 55:
            stage = "WEAK_CONFIRM"
        else:
            stage = "REJECT"
        detected = stage in {"CONFIRM", "WEAK_CONFIRM"}
    return _result(
        "skill_09_auction_intraday_confirm", "竞价与分时确认", detected=detected,
        stage=stage, score=score, missing=missing,
        evidence=[
            _evidence("竞价高开幅度", gap, "auction price / previous close - 1", source="09:25 auction snapshot"),
            _evidence("竞价金额/量比", amount_ratio, "真实竞价字段", source="09:25 auction snapshot"),
            _evidence("竞价相对板块", relative_sector, "auction gap - sector preopen return"),
            _evidence("开盘15分钟收益", ret15, "真实分钟线"),
            _evidence("VWAP收复", vwap_reclaim, "开盘分钟线"),
        ],
        invalidation=["竞价或开盘结构不满足确认条件", "涨跌停导致不可成交", "开盘相对板块和市场同步转弱"],
        next_confirmation=["缺历史竞价时保持实时Shadow，不用日K替代", "09:30-10:40继续核验VWAP与最大不利波动"],
        signal_type="CONFIRMATION", horizon="竞价至10:40", data_level="AUCTION_INTRADAY",
    )


def behavior_reflexivity(features: dict[str, Any]) -> dict[str, Any]:
    """Skill 10 adapter shared by live scans and the PIT validator.

    The full daily diagnosis is attached by the runtime scanner.  The lazy
    import avoids a module cycle because the full calculator reuses Skill 02
    from this file.  Historical validation receives a conservative adapter
    when only the legacy feature vector is available.
    """
    from quant.reflexivity_skill import basic_reflexivity_result

    return basic_reflexivity_result(features)


SKILL_CALCULATORS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "skill_01_price_volume_efficiency": price_volume_efficiency,
    "skill_02_absorption_pressure": absorption_pressure,
    "skill_03_abnormal_turnover": abnormal_turnover,
    "skill_04_false_breakdown_reclaim": false_breakdown_reclaim,
    "skill_05_trend_reacceleration": trend_reacceleration,
    "skill_06_low_position_relaunch": low_position_relaunch,
    "skill_07_breakout_quality": breakout_quality,
    "skill_08_behavior_imbalance": behavior_imbalance,
    "skill_09_auction_intraday_confirm": auction_intraday_confirm,
    "skill_10_behavior_reflexivity": behavior_reflexivity,
}


def evaluate_skill(skill_id: str, features: dict[str, Any]) -> dict[str, Any]:
    calculator = SKILL_CALCULATORS.get(skill_id)
    if calculator is None:
        raise KeyError(f"未知交易Skill：{skill_id}")
    return calculator(features)


def evaluate_all_skills(features: dict[str, Any], skill_ids: list[str] | None = None) -> list[dict[str, Any]]:
    selected = skill_ids or list(SKILL_CALCULATORS)
    return [evaluate_skill(skill_id, features) for skill_id in selected if skill_id in SKILL_CALCULATORS]
