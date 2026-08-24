"""Deterministic ROCI engines.

Each function returns facts, inferences and source claims separately.  The
functions are intentionally conservative: absent inputs produce UNKNOWN,
never a made-up midpoint.
"""

from __future__ import annotations

import math
from statistics import mean, median
from typing import Any


UNKNOWN = "UNKNOWN"
ACTIONS = ("ATTACK", "PROBE", "HOLD", "WAIT", "DEFEND", "REDUCE", "EXIT", "NO_TRADE")


def num(value: Any) -> float | None:
    if value in (None, "", "-", "--") or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 1)


def evidence(label: str, value: Any, source: str, evidence_type: str = "FACT", *, supports: bool = True) -> dict[str, Any]:
    return {"type": evidence_type, "label": label, "value": value, "source": source, "supports": supports}


def _headline(ctx: dict[str, Any]) -> dict[str, Any]:
    return (ctx.get("workbench") or {}).get("headline_metrics") or {}


def _state(ctx: dict[str, Any]) -> dict[str, Any]:
    return (ctx.get("workbench") or {}).get("market_state") or {}


def _structure(ctx: dict[str, Any]) -> dict[str, Any]:
    return (ctx.get("workbench") or {}).get("structure_health") or {}


def _crowding(ctx: dict[str, Any]) -> dict[str, Any]:
    return (ctx.get("workbench") or {}).get("crowding_risk") or {}


def _forecast_behavior(ctx: dict[str, Any]) -> dict[str, Any]:
    return (ctx.get("forecast") or {}).get("behavior") or {}


def _bars(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    return list((ctx.get("daily") or {}).get("bars") or [])


def _market_stress_bars(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Build an auditable equal-weight market series from existing PIT bars."""
    cached_market_bars = (ctx.get("daily") or {}).get("market_bars") or []
    if cached_market_bars:
        return list(cached_market_bars)
    grouped = (ctx.get("daily") or {}).get("bars_by_code") or {}
    by_date: dict[str, list[dict[str, Any]]] = {}
    for stock_bars in grouped.values():
        for item in stock_bars or []:
            trade_date = str(item.get("trade_date") or "")
            change = num(item.get("change_pct"))
            if trade_date and change is not None:
                by_date.setdefault(trade_date, []).append(item)
    synthetic_close = 100.0
    result: list[dict[str, Any]] = []
    for trade_date in sorted(by_date):
        rows = by_date[trade_date]
        changes = [num(item.get("change_pct")) for item in rows]
        changes = [item for item in changes if item is not None]
        if not changes:
            continue
        equal_weight_change = float(mean(changes))
        synthetic_close *= max(0.01, 1 + equal_weight_change / 100)
        volumes = [num(item.get("amount")) or num(item.get("volume")) for item in rows]
        result.append({
            "trade_date": trade_date,
            "close": round(synthetic_close, 6),
            "change_pct": round(equal_weight_change, 4),
            "median_change_pct": round(float(median(changes)), 4),
            "down_ratio": round(sum(item < 0 for item in changes) / len(changes), 4),
            "sample_size": len(changes),
            "volume": sum(item for item in volumes if item is not None) or None,
            "source": "stock_daily_bars_equal_weight",
        })
    return result


def completeness(ctx: dict[str, Any]) -> tuple[float, list[str]]:
    wb = ctx.get("workbench") or {}
    forecast = ctx.get("forecast") or {}
    checks = [
        bool(wb.get("market_state")),
        bool(wb.get("headline_metrics")),
        bool(wb.get("structure_health")),
        bool(wb.get("crowding_risk")),
        bool(forecast.get("timeline")),
        bool((ctx.get("daily") or {}).get("data_date")),
    ]
    missing = [
        label for value, label in zip(checks, ("market_state", "breadth", "structure", "crowding", "forecast", "daily_bars")) if not value
    ]
    return round(sum(checks) / len(checks) * 100, 1), missing


def battlefield(ctx: dict[str, Any]) -> dict[str, Any]:
    headline = _headline(ctx)
    state = _state(ctx)
    structure = _structure(ctx)
    crowding = _crowding(ctx)
    up, down = num(headline.get("up_count")), num(headline.get("down_count"))
    breadth = up / (up + down) * 100 if up is not None and down is not None and up + down else None
    score = num(state.get("score"))
    structure_score = num(structure.get("score"))
    limit_up, limit_down = num(headline.get("limit_up")), num(headline.get("limit_down"))
    if breadth is None and score is None:
        regime = UNKNOWN
    elif (score is not None and score >= 75) and (breadth is None or breadth >= 58):
        regime = "STRONG_OFFENSE"
    elif (score is not None and score >= 58) and (breadth is None or breadth >= 50):
        regime = "NORMAL_OFFENSE"
    elif breadth is not None and breadth <= 22 and limit_down is not None and limit_down > (limit_up or 0) * 1.6:
        regime = "CAPITULATION"
    elif (score is not None and score <= 38) or (structure_score is not None and structure_score <= 35):
        regime = "DEFENSIVE"
    elif score is not None and score >= 48 and structure_score is not None and structure_score >= 48:
        regime = "RECOVERY"
    else:
        regime = "MIXED"
    facts = []
    if breadth is not None:
        facts.append(evidence("上涨家数占比", round(breadth, 1), "market_decision_workbench", "FACT"))
    if score is not None:
        facts.append(evidence("市场状态评分", score, "market_decision_workbench", "FACT"))
    if structure_score is not None:
        facts.append(evidence("结构健康度", structure_score, "market_decision_workbench", "FACT"))
    if not facts:
        facts.append(evidence("战场状态", UNKNOWN, "available_data", "FACT", supports=False))
    reward = "市场宽度与核心板块同步改善" if regime in {"STRONG_OFFENSE", "NORMAL_OFFENSE", "RECOVERY"} else "防御、流动性与风险边界优先"
    penalty = "高位拥挤和后排负反馈" if num(crowding.get("score")) is not None and num(crowding.get("score")) >= 65 else "证据不足，暂不定义明确惩罚方向"
    return {"regime": regime, "label": {"STRONG_OFFENSE": "强进攻", "NORMAL_OFFENSE": "常态进攻", "MIXED": "混合分歧", "DEFENSIVE": "防御", "CAPITULATION": "风险释放", "RECOVERY": "修复", UNKNOWN: "未知"}.get(regime, regime), "facts": facts, "market_reward": reward, "market_penalty": penalty, "history": [], "confidence": clamp(len(facts) / 3 * 100)}


def forces(ctx: dict[str, Any], battle: dict[str, Any]) -> dict[str, Any]:
    wb = ctx.get("workbench") or {}
    headline = _headline(ctx)
    structure = _structure(ctx)
    crowding = _crowding(ctx)
    lines = list(wb.get("main_lines") or [])
    result: list[dict[str, Any]] = []
    breadth = num(headline.get("up_count"))
    down = num(headline.get("down_count"))
    breadth_pct = breadth / (breadth + down) * 100 if breadth is not None and down is not None and breadth + down else None
    if breadth_pct is not None:
        result.append({"force_id": "breadth", "scope": "market", "name": "市场宽度", "side": "ALLY" if breadth_pct >= 52 else "ENEMY", "strength": clamp(abs(breadth_pct - 50) * 2), "direction": "UP" if breadth_pct >= 52 else "DOWN", "confidence": 90, "persistence": 50, "relevance": 90, "evidence": [evidence("上涨/下跌家数", f"{breadth_pct:.1f}%", "market_decision_workbench")], "skills": ["ROCI-S027", "ROCI-S046"]})
    else:
        result.append({"force_id": "breadth", "scope": "market", "name": "市场宽度", "side": "NEUTRAL", "strength": None, "direction": UNKNOWN, "confidence": 0, "persistence": None, "relevance": 90, "evidence": [evidence("上涨/下跌家数", UNKNOWN, "available_data", supports=False)], "skills": ["ROCI-S004", "ROCI-S037"]})
    structure_score = num(structure.get("score"))
    result.append({"force_id": "structure", "scope": "market", "name": "板块扩散与结构", "side": "ALLY" if structure_score is not None and structure_score >= 58 else "ENEMY" if structure_score is not None and structure_score < 42 else "CONVERTIBLE" if structure_score is not None else "NEUTRAL", "strength": structure_score, "direction": "UP" if structure_score is not None and structure_score >= 58 else "DOWN" if structure_score is not None and structure_score < 42 else "FLAT" if structure_score is not None else UNKNOWN, "confidence": num(structure.get("coverage_pct")) or 0, "persistence": 50, "relevance": 90, "evidence": [evidence("结构健康度", structure_score if structure_score is not None else UNKNOWN, "market_decision_workbench", supports=structure_score is not None)], "skills": ["ROCI-S041", "ROCI-S045"]})
    crowd_score = num(crowding.get("score"))
    result.append({"force_id": "crowding", "scope": "market", "name": "高位拥挤", "side": "ENEMY" if crowd_score is not None and crowd_score >= 65 else "CONVERTIBLE" if crowd_score is not None else "NEUTRAL", "strength": crowd_score, "direction": "UP" if crowd_score is not None and crowd_score >= 65 else "FLAT" if crowd_score is not None else UNKNOWN, "confidence": num(crowding.get("coverage_pct")) or 0, "persistence": 50, "relevance": 80, "evidence": [evidence("拥挤风险", crowd_score if crowd_score is not None else UNKNOWN, "market_decision_workbench", supports=crowd_score is not None)], "skills": ["ROCI-S065"]})
    if lines:
        result.append({"force_id": "mainline", "scope": "sector", "name": "主线板块扩散", "side": "ALLY", "strength": num(lines[0].get("strength_score")), "direction": "UP", "confidence": 70, "persistence": 60, "relevance": 85, "evidence": [evidence("当前主线", lines[0].get("name"), "topic_strength")], "skills": ["ROCI-S009", "ROCI-S018"]})
    else:
        result.append({"force_id": "mainline", "scope": "sector", "name": "主线板块扩散", "side": "NEUTRAL", "strength": None, "direction": UNKNOWN, "confidence": 0, "persistence": None, "relevance": 85, "evidence": [evidence("主线", UNKNOWN, "available_data", supports=False)], "skills": ["ROCI-S004"]})
    # This is a relevance-weighted view, not a 76-Skill vote or a raw sum.
    observed = [item for item in result if item.get("strength") is not None]
    effective = round(mean((item["strength"] or 0) * (item.get("confidence") or 0) / 100 * (item.get("persistence") or 50) / 100 * (item.get("relevance") or 50) / 100 for item in observed), 1) if observed else None
    return {"forces": result, "effective_advantage": effective, "method": "strength × confidence × persistence × relevance_to_primary_contradiction"}


def contradiction(ctx: dict[str, Any], battle: dict[str, Any], force_map: dict[str, Any]) -> dict[str, Any]:
    wb = ctx.get("workbench") or {}
    structure = _structure(ctx)
    crowding = _crowding(ctx)
    state = _state(ctx)
    structure_score, crowd_score, market_score = num(structure.get("score")), num(crowding.get("score")), num(state.get("score"))
    candidates: list[tuple[str, float | None, str, list[str], list[str]]] = [
        ("板块扩散能否继续", abs((structure_score or 50) - 50) if structure_score is not None else None, "主线扩散与后排承接之间的关系决定趋势是否可持续。", ["结构健康度和主线排名"], ["缺少板块宽度或后排数据"]),
        ("趋势延续能否抵消高位供应", abs((crowd_score or 50) - 50) if crowd_score is not None else None, "市场方向与高位拥挤共同决定短期风险补偿。", ["市场状态、拥挤风险"], ["供应和位置数据不足"]),
        ("风险释放后是否出现承接", abs((50 - (market_score or 50))) if market_score is not None else None, "压力后的相对响应决定风险是否可能转化为机会。", ["市场状态变化"], ["缺少连续压力响应"]),
    ]
    available = [item for item in candidates if item[1] is not None]
    if not available:
        statement, key, reason, supporting, opposing, confidence = "主要矛盾未知：关键市场宽度、结构或状态数据不足", "UNKNOWN", "无法从缺失数据中选择主要约束。", [], ["市场状态数据不足"], 0.0
    else:
        chosen = max(available, key=lambda item: item[1] or 0)
        statement, key, reason, supporting, opposing = chosen
        confidence = clamp(min(90, 45 + (chosen[1] or 0)))
    resolve = ["市场宽度扩大且核心/中军同步确认", "资金持续而非单日脉冲", "结构健康度连续改善"]
    worsen = ["高位负反馈扩大", "主线宽度收缩", "成交放大但价格和相对强度恶化"]
    return {"statement": statement, "candidate_key": key, "why": reason, "confidence": confidence, "secondary_risks": ["高位拥挤" if crowd_score is not None and crowd_score >= 65 else "拥挤数据待补", "流动性与时间成本"], "supporting_evidence": [evidence(item, item, "market_decision_workbench") for item in supporting], "opposing_evidence": [evidence(item, item, "data_quality", "FACT", supports=False) for item in opposing], "what_would_resolve": resolve, "what_would_worsen": worsen, "status": "OBSERVING" if key == "UNKNOWN" else "ACTIVE_HYPOTHESIS"}


def risk_pricing(ctx: dict[str, Any], battle: dict[str, Any], contradiction_result: dict[str, Any]) -> dict[str, Any]:
    crowd = num(_crowding(ctx).get("score"))
    structure = num(_structure(ctx).get("score"))
    explicit = ctx.get("risk_pricing") or {}
    if isinstance(explicit, dict) and explicit.get("status") in {"NOT_PRICED", "PARTIALLY_PRICED", "MOSTLY_PRICED", "OVERPRICED_NEGATIVE"}:
        return {
            "status": explicit["status"],
            "risks": list(explicit.get("risks") or []),
            "summary": explicit.get("summary") or "风险定价沿用可追溯的既有风险响应记录。",
            "confidence": num(explicit.get("confidence")) or 0,
            "evidence": list(explicit.get("evidence") or []),
        }
    if crowd is None and structure is None:
        return {"status": UNKNOWN, "risks": [], "summary": "风险定价未知：没有足够的价格、结构和拥挤证据。", "confidence": 0}
    risks = []
    if crowd is not None:
        risks.append({"risk": "高位拥挤与边际兑现", "state": "NOT_PRICED" if crowd >= 75 else "PARTIALLY_PRICED" if crowd >= 55 else "MOSTLY_PRICED", "value": crowd, "evidence": [evidence("拥挤风险评分", crowd, "market_decision_workbench")]})
    if structure is not None:
        risks.append({"risk": "板块结构恶化", "state": "NOT_PRICED" if structure < 35 else "PARTIALLY_PRICED" if structure < 52 else "MOSTLY_PRICED", "value": structure, "evidence": [evidence("结构健康度", structure, "market_decision_workbench")]})
    if not risks:
        return {"status": UNKNOWN, "risks": [], "summary": "风险定价未知：当前只有方向状态，缺少可定价的风险响应项。", "confidence": 0}
    states = {item["state"] for item in risks}
    overall = (
        "OVERPRICED_NEGATIVE" if "OVERPRICED_NEGATIVE" in states
        else "NOT_PRICED" if "NOT_PRICED" in states
        else "PARTIALLY_PRICED" if "PARTIALLY_PRICED" in states
        else "MOSTLY_PRICED"
    )
    return {"status": overall, "risks": risks, "summary": "风险定价不是事件标签，而是价格响应、结构和拥挤的联合判断。", "confidence": clamp(len(risks) / 3 * 100)}


def stress_test(ctx: dict[str, Any]) -> dict[str, Any]:
    bars = _bars(ctx)
    scope = "stock"
    if not bars:
        bars = _market_stress_bars(ctx)
        scope = "market"
    if len(bars) < 5:
        return {"state": UNKNOWN, "scope": scope, "events": [], "summary": "压力测试未知：日线样本不足，不能重建压力后的响应。", "confidence": 0}
    events = []
    for index in range(1, len(bars)):
        change = num(bars[index].get("change_pct"))
        down_ratio = num(bars[index].get("down_ratio"))
        is_pressure = change is not None and (
            change <= (-1.5 if scope == "market" else -3.0)
            or (scope == "market" and down_ratio is not None and down_ratio >= 0.75)
        )
        if not is_pressure:
            continue
        later = [num(item.get("close")) for item in bars[index + 1:index + 4]]
        later = [item for item in later if item is not None]
        current = num(bars[index].get("close"))
        recovery = ((later[-1] / current) - 1) * 100 if later and current else None
        relative = recovery if recovery is not None else None
        state = "ANTIFRAGILE" if recovery is not None and recovery >= 3 else "RESILIENT" if recovery is not None and recovery >= 0 else "FRAGILE" if recovery is not None else UNKNOWN
        source = "stock_daily_bars_equal_weight" if scope == "market" else "stock_daily_bars"
        event_name = "市场宽度压力/普跌" if scope == "market" else "大阴线/放量分歧"
        pressure_evidence = [evidence("压力日跌幅", change, source)]
        if scope == "market":
            pressure_evidence.extend([
                evidence("下跌样本占比", round((down_ratio or 0) * 100, 1), source, supports=down_ratio is not None),
                evidence("有效股票样本", bars[index].get("sample_size"), source),
            ])
        pressure_evidence.append(evidence("后续恢复", recovery if recovery is not None else UNKNOWN, source, supports=recovery is not None))
        events.append({"event": event_name, "date": bars[index].get("trade_date"), "severity": abs(change or 0), "actual_response": f"压力日等权涨跌 {change:.2f}%" if scope == "market" else f"压力日涨跌 {change:.2f}%", "relative_response": relative, "recovery_speed": recovery, "post_stress_followthrough": recovery, "state": state, "evidence": pressure_evidence})
    events = events[-5:]
    if not events:
        return {"state": UNKNOWN, "scope": scope, "events": [], "summary": "观察窗口内没有符合阈值的压力事件，无法据此证明韧性。", "confidence": 0}
    states = [item["state"] for item in events if item["state"] != UNKNOWN]
    if not states:
        return {"state": UNKNOWN, "scope": scope, "events": events, "summary": "已发现压力事件，但后续验证窗口尚未完成。", "confidence": 0}
    overall = "ANTIFRAGILE" if states.count("ANTIFRAGILE") >= max(1, len(states) // 2 + 1) else "RESILIENT" if states.count("FRAGILE") == 0 else "FRAGILE"
    return {"state": overall, "scope": scope, "events": events, "summary": "基于可见压力日及其后续响应，不能代表未来压力结果。", "confidence": clamp(len(states) / 5 * 100)}


def expectation_gap(ctx: dict[str, Any]) -> dict[str, Any]:
    forecast = ctx.get("forecast") or {}
    behavior = _forecast_behavior(ctx)
    timeline = forecast.get("timeline") or []
    state = _state(ctx)
    if not timeline and not state:
        return {"status": UNKNOWN, "expected_strength": None, "actual_strength": None, "surprise_score": None, "evidence": [evidence("预期差", UNKNOWN, "forecast_v5", supports=False)]}
    expected = num((timeline[0] if timeline else {}).get("probability")) or num((timeline[0] if timeline else {}).get("confidence_pct"))
    actual = num(state.get("score"))
    surprise = actual - expected if actual is not None and expected is not None else None
    return {"status": "OPPORTUNITY" if surprise is not None and surprise >= 10 else "RISK" if surprise is not None and surprise <= -10 else "NO_CLEAR_GAP" if surprise is not None else UNKNOWN, "expected_strength": expected, "actual_strength": actual, "surprise_score": round(surprise, 1) if surprise is not None else None, "surprise_direction": "positive" if surprise is not None and surprise > 0 else "negative" if surprise is not None and surprise < 0 else UNKNOWN, "persistence": "UNKNOWN", "evidence": [evidence("V5前瞻时间线", expected if expected is not None else UNKNOWN, "forecast_v5", supports=expected is not None), evidence("当前市场状态", actual if actual is not None else UNKNOWN, "market_decision_workbench", supports=actual is not None)], "note": "预期差需要连续时点验证，单个截面不升级为行动。"}


def supply_absorption(ctx: dict[str, Any]) -> dict[str, Any]:
    bars = _bars(ctx)
    if len(bars) < 3:
        return {"status": UNKNOWN, "supply": None, "demand": None, "absorption": None, "liquidity": None, "evidence": [evidence("日线样本", len(bars), "stock_daily_bars", supports=False)]}
    recent = bars[-3:]
    changes = [num(item.get("change_pct")) for item in recent]
    volumes = [num(item.get("volume")) for item in recent]
    changes = [item for item in changes if item is not None]
    volumes = [item for item in volumes if item is not None]
    supply = clamp(sum(max(0, -item) for item in changes) / max(len(changes), 1) * 12)
    demand = clamp(sum(max(0, item) for item in changes) / max(len(changes), 1) * 12)
    absorption = clamp(50 + demand - supply)
    return {"status": "ABSORPTION" if absorption >= 60 else "SUPPLY_PRESSURE" if absorption <= 40 else "BALANCED", "supply": supply, "demand": demand, "aggression": demand, "absorption": absorption, "liquidity": 70 if volumes else None, "overhead_pressure": supply, "support_response": absorption, "evidence": [evidence("近3日收益", changes, "stock_daily_bars"), evidence("成交量观测", "available" if volumes else UNKNOWN, "stock_daily_bars", supports=bool(volumes))], "language_policy": "仅描述供应、承接和相对响应，不推断参与者意图。"}


def opportunities(ctx: dict[str, Any], battle: dict[str, Any], contradiction_result: dict[str, Any], risk: dict[str, Any], stress: dict[str, Any], asymmetry: dict[str, Any] | None = None) -> dict[str, Any]:
    wb = ctx.get("workbench") or {}
    candidates = []
    for item in (wb.get("candidates") or [])[:20]:
        code = item.get("code")
        if code:
            candidates.append({"code": code, "name": item.get("name") or code, "sector": item.get("sector") or "未分类", "score": num(item.get("score")), "source": item.get("source") or "market_decision_workbench", "evidence": [evidence("现有系统候选", item.get("score"), "market_decision_workbench")]})
    patterns = []
    for pattern in ctx.get("pattern_definitions") or []:
        pattern = dict(pattern)
        name = pattern.get("name")
        triggered = False
        score = None
        if name in {"逆板块抗跌", "弱转强"} and candidates and battle.get("regime") in {"RECOVERY", "NORMAL_OFFENSE", "MIXED"}:
            triggered, score = True, 55.0
        elif name in {"趋势加速", "平台突破"} and battle.get("regime") in {"STRONG_OFFENSE", "NORMAL_OFFENSE"}:
            triggered, score = True, 50.0
        elif name in {"超跌启动", "反核"} and battle.get("regime") in {"CAPITULATION", "RECOVERY"}:
            triggered, score = True, 48.0
        patterns.append({"pattern_id": pattern.get("id"), "name": name, "category": pattern.get("category"), "source": pattern.get("source"), "status": pattern.get("status", "DETECT_ONLY"), "definition": pattern.get("definition"), "detection_rule": pattern.get("rule") or {}, "triggered": triggered, "score": score, "confidence": 35 if triggered else 0, "candidates": candidates[:5] if triggered else [], "evidence": [evidence("战场生态", battle.get("regime"), "roci_battlefield", "INFERENCE", supports=battle.get("regime") != UNKNOWN)], "action_policy": "SHADOW/DETECT_ONLY 不参与最终 ACTION" if pattern.get("status") in {"SHADOW", "DETECT_ONLY"} else "需独立验证"})
    return {"patterns": patterns, "candidates": candidates, "categories": ["妖股", "反脆弱", "突破", "机会迁徙", "十全武功"], "note": "机会库是结构观察与验证入口，不是荐股榜。", "shadow_count": sum(item.get("status") == "SHADOW" for item in patterns)}


def _weighted_observed_score(values: list[tuple[float | None, float]]) -> tuple[float | None, float]:
    """Score only observed factors and report how much of the model was covered."""
    observed = [(value, weight) for value, weight in values if value is not None and weight > 0]
    total_weight = sum(weight for _, weight in values if weight > 0)
    observed_weight = sum(weight for _, weight in observed)
    if not observed or total_weight <= 0 or observed_weight <= 0:
        return None, 0.0
    score = sum(float(value) * weight for value, weight in observed) / observed_weight
    return clamp(score), round(observed_weight / total_weight * 100, 1)


def _board_profile(code: str) -> tuple[str, bool]:
    normalized = str(code or "").split(".")[0].zfill(6)
    if normalized.startswith(("688", "689")):
        return "科创板", True
    if normalized.startswith(("300", "301")):
        return "创业板", True
    if normalized.startswith(("4", "8", "92")):
        return "北交所", True
    return "沪深主板", False


def risk_adapted_recommendations(
    ctx: dict[str, Any],
    battle: dict[str, Any],
    risk: dict[str, Any],
    stress: dict[str, Any],
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rank sectors and research candidates after applying the current risk regime.

    This is a research shortlist, not another trade signal. It consumes the
    existing PIT workbench output and never fills a missing factor with 50.
    """
    wb = ctx.get("workbench") or {}
    sector_rows = [dict(item) for item in (wb.get("main_lines") or []) if item.get("name")]
    daily = wb.get("daily_short_term_recommendations") or {}
    stock_rows = [dict(item) for item in (daily.get("candidates") or []) if item.get("code")]
    regime = battle.get("regime") or UNKNOWN
    pricing = risk.get("status") or UNKNOWN
    stress_state = stress.get("state") or UNKNOWN

    if regime in {"DEFENSIVE", "CAPITULATION"} or pricing == "NOT_PRICED" or stress_state == "FRAGILE":
        posture, posture_label = "DEFENSIVE", "防御优先"
        sector_weights = {"strength": .12, "breadth": .22, "flow": .10, "risk": .24, "profit": .20, "capital": .05, "trend": .07}
        stock_weights = {"market_fit": .08, "sector_strength": .10, "capital": .08, "profitability": .24, "risk_safety": .32, "volume_ratio": .05, "trend": .13}
        minimum_risk_safety = 68.0
    elif regime in {"STRONG_OFFENSE", "NORMAL_OFFENSE"} and pricing in {"MOSTLY_PRICED", "OVERPRICED_NEGATIVE"} and stress_state != "FRAGILE":
        posture, posture_label = "OFFENSIVE", "进攻筛选"
        sector_weights = {"strength": .25, "breadth": .14, "flow": .20, "risk": .10, "profit": .07, "capital": .15, "trend": .09}
        stock_weights = {"market_fit": .08, "sector_strength": .22, "capital": .20, "profitability": .10, "risk_safety": .15, "volume_ratio": .10, "trend": .15}
        minimum_risk_safety = 45.0
    else:
        posture, posture_label = "BALANCED", "平衡观察"
        sector_weights = {"strength": .20, "breadth": .20, "flow": .15, "risk": .20, "profit": .10, "capital": .10, "trend": .05}
        stock_weights = {"market_fit": .10, "sector_strength": .18, "capital": .15, "profitability": .18, "risk_safety": .25, "volume_ratio": .06, "trend": .08}
        minimum_risk_safety = 55.0

    if not sector_rows:
        return {
            "status": UNKNOWN,
            "posture": posture,
            "posture_label": posture_label,
            "sectors": [],
            "stocks": [],
            "avoided_sectors": [],
            "missing_inputs": ["main_lines"],
            "note": "缺少可审计板块强度、宽度和资金记录，系统没有生成板块或个股推荐。",
        }

    stocks_by_sector: dict[str, list[dict[str, Any]]] = {}
    for item in stock_rows:
        stocks_by_sector.setdefault(str(item.get("sector") or "未分类"), []).append(item)

    known_flows = sorted(
        ((str(item["name"]), num(item.get("main_net_inflow"))) for item in sector_rows),
        key=lambda pair: (pair[1] is not None, pair[1] or 0),
        reverse=True,
    )
    observed_flows = [item for item in known_flows if item[1] is not None]
    flow_scores: dict[str, float] = {}
    for index, (name, value) in enumerate(observed_flows):
        if len(observed_flows) == 1:
            flow_scores[name] = 100.0 if (value or 0) > 0 else 0.0
        else:
            flow_scores[name] = round(100 - index / (len(observed_flows) - 1) * 100, 1)

    ranked_sectors: list[dict[str, Any]] = []
    avoided_sectors: list[dict[str, Any]] = []
    for row in sector_rows:
        name = str(row.get("name"))
        related_stocks = stocks_by_sector.get(name) or []
        risk_values = [num((item.get("score_breakdown") or {}).get("risk_safety")) for item in related_stocks]
        profit_values = [num((item.get("score_breakdown") or {}).get("profitability")) for item in related_stocks]
        capital_values = [num((item.get("score_breakdown") or {}).get("capital")) for item in related_stocks]
        trend_values = [num((item.get("score_breakdown") or {}).get("trend")) for item in related_stocks]
        risk_values = [item for item in risk_values if item is not None]
        profit_values = [item for item in profit_values if item is not None]
        capital_values = [item for item in capital_values if item is not None]
        trend_values = [item for item in trend_values if item is not None]
        components = {
            "strength": num(row.get("strength_score")),
            "breadth": num(row.get("breadth")),
            "flow": flow_scores.get(name),
            "risk": round(mean(risk_values), 1) if risk_values else None,
            "profit": round(mean(profit_values), 1) if profit_values else None,
            "capital": round(mean(capital_values), 1) if capital_values else None,
            "trend": round(mean(trend_values), 1) if trend_values else None,
        }
        fit_score, coverage = _weighted_observed_score([(components[key], weight) for key, weight in sector_weights.items()])
        flow = num(row.get("main_net_inflow"))
        breadth = num(row.get("breadth"))
        risk_flags = [str(item) for item in (row.get("risk_flags") or [])]
        exclusion_reasons = []
        if row.get("lifecycle") in {"退潮", "衰退", "风险释放"}:
            exclusion_reasons.append(f"生命周期为{row.get('lifecycle')}")
        if breadth is not None and breadth < 40:
            exclusion_reasons.append(f"板块宽度仅{breadth:.1f}%")
        if flow is not None and flow < 0:
            exclusion_reasons.append("板块资金净流出")
        exclusion_reasons.extend(risk_flags)
        leader = row.get("leader") or {}
        item = {
            "rank": None,
            "name": name,
            "classification": row.get("classification") or UNKNOWN,
            "lifecycle": row.get("lifecycle") or UNKNOWN,
            "fit_score": fit_score,
            "data_coverage_pct": coverage,
            "strength_score": components["strength"],
            "breadth": breadth,
            "main_net_inflow": flow,
            "stock_candidate_count": len(related_stocks),
            "leader": {"code": leader.get("code"), "name": leader.get("name")},
            "risk_level": "LOW" if (components["risk"] or 0) >= 75 and not exclusion_reasons else "HIGH" if exclusion_reasons else "MEDIUM",
            "status": "优先研究" if fit_score is not None and fit_score >= 72 and not exclusion_reasons else "跟踪观察",
            "reasons": [
                f"风险生态：{posture_label}",
                f"板块强度{components['strength']:.1f}、宽度{breadth:.1f}%" if components["strength"] is not None and breadth is not None else "板块强度或宽度缺失",
                "资金净流入" if flow is not None and flow > 0 else "资金方向待确认" if flow is None else "资金净流出",
                f"板块内{len(related_stocks)}只个股通过盈利/风险初筛" if related_stocks else "板块内暂无完整个股质量样本",
            ],
            "risk_flags": exclusion_reasons,
            "components": components,
            "source": "market_decision_workbench.main_lines+daily_short_term_recommendations",
        }
        (avoided_sectors if exclusion_reasons else ranked_sectors).append(item)

    ranked_sectors.sort(key=lambda item: (item.get("fit_score") is not None, item.get("fit_score") or 0, item.get("stock_candidate_count") or 0), reverse=True)
    for index, item in enumerate(ranked_sectors[:5], 1):
        item["rank"] = index
    selected_sectors = ranked_sectors[:5]
    selected_by_name = {item["name"]: item for item in selected_sectors}
    avoided_sectors.sort(key=lambda item: item.get("fit_score") or 0, reverse=True)

    leader_codes = {
        str((item.get("leader") or {}).get("code") or "").split(".")[0].zfill(6)
        for item in sector_rows
        if (item.get("leader") or {}).get("code")
    }
    ranked_stocks: list[dict[str, Any]] = []
    excluded_stock_count = 0
    for row in stock_rows:
        sector = str(row.get("sector") or "未分类")
        sector_item = selected_by_name.get(sector)
        if not sector_item:
            excluded_stock_count += 1
            continue
        breakdown = row.get("score_breakdown") or {}
        factor_values = {key: num(breakdown.get(key)) for key in stock_weights}
        base_score, factor_coverage = _weighted_observed_score([(factor_values[key], weight) for key, weight in stock_weights.items()])
        sector_fit = num(sector_item.get("fit_score"))
        combined_values = [(base_score, .8), (sector_fit, .2)]
        fit_score, _ = _weighted_observed_score(combined_values)
        risk_safety = factor_values.get("risk_safety")
        if fit_score is None or fit_score < 60 or risk_safety is None or risk_safety < minimum_risk_safety:
            excluded_stock_count += 1
            continue
        code = str(row.get("code") or "").split(".")[0].zfill(6)
        board, requires_permission = _board_profile(code)
        profitability = row.get("profitability") or {}
        is_leader = code in leader_codes
        if is_leader:
            role = "板块领导股"
        elif (factor_values.get("profitability") or 0) >= 72 and risk_safety >= 70:
            role = "质量优先"
        elif (factor_values.get("capital") or 0) >= 78 and (factor_values.get("sector_strength") or 0) >= 70:
            role = "资金共振"
        else:
            role = "潜力观察"
        reasons = [f"{sector}风险适配分{sector_fit:.1f}"]
        reasons.extend(str(item) for item in (row.get("reasons") or [])[:3])
        ranked_stocks.append({
            "rank": None,
            "code": code,
            "name": row.get("name") or code,
            "sector": sector,
            "board": board,
            "requires_special_permission": requires_permission,
            "role": role,
            "fit_score": fit_score,
            "source_score": num(row.get("score")),
            "confidence_pct": num(row.get("confidence_pct")),
            "data_coverage_pct": factor_coverage,
            "risk_level": "LOW" if risk_safety >= 78 else "MEDIUM" if risk_safety >= 60 else "HIGH",
            "risk_safety": risk_safety,
            "profitability_score": factor_values.get("profitability"),
            "capital_score": factor_values.get("capital"),
            "volume_ratio": num(row.get("volume_ratio")),
            "profitability": {
                "status": profitability.get("status") or UNKNOWN,
                "roe": num(profitability.get("roe")),
                "pe": num(profitability.get("pe")),
                "disclosed_at": profitability.get("disclosed_at"),
            },
            "reasons": reasons,
            "risk": row.get("risk") or "没有可用的个股风险说明",
            "invalidation_conditions": list(row.get("invalidation_conditions") or []),
            "data_date": row.get("data_date") or daily.get("data_date"),
            "is_realtime": bool(row.get("is_realtime")),
            "source": row.get("source") or daily.get("source") or "market_decision_workbench",
            "action_policy": "仅研究观察，需人工确认" if (decision or {}).get("action") not in {"ATTACK", "PROBE"} else "已进入人工确认层，不自动下单",
        })
    ranked_stocks.sort(key=lambda item: (item.get("fit_score") or 0, item.get("risk_safety") or 0, item.get("confidence_pct") or 0), reverse=True)
    for index, item in enumerate(ranked_stocks[:12], 1):
        item["rank"] = index

    action_name = (decision or {}).get("action") or UNKNOWN
    return {
        "status": "AVAILABLE" if selected_sectors else UNKNOWN,
        "posture": posture,
        "posture_label": posture_label,
        "market_regime": regime,
        "risk_pricing": pricing,
        "stress_state": stress_state,
        "action_alignment": action_name,
        "execution_policy": "仅研究观察，当前ACTION未开放执行确认" if action_name not in {"ATTACK", "PROBE"} else "可进入人工确认，不连接券商、不自动下单",
        "sectors": selected_sectors,
        "stocks": ranked_stocks[:12],
        "avoided_sectors": avoided_sectors[:5],
        "default_board_filter": {"exclude_star_market": True, "exclude_gem": True, "exclude_bse": True},
        "excluded_stock_count": excluded_stock_count,
        "data_date": daily.get("data_date") or ((wb.get("meta") or {}).get("decision_date")),
        "is_realtime": bool((wb.get("meta") or {}).get("is_realtime")) and any(item.get("is_realtime") for item in ranked_stocks),
        "source": daily.get("source") or "market_decision_workbench+financial_pit_cache+stock_daily_bars",
        "method": "先按战场生态调整权重，再联合板块强度、宽度、资金和板块内盈利/风险质量；个股联合板块适配、盈利质量、风险安全、资金、量比和趋势。缺失因子不填中性值。",
        "note": "这是风险适配研究清单，不是收益承诺或自动买卖指令。",
        "missing_inputs": [] if stock_rows else ["daily_short_term_recommendations"],
    }


def asymmetry(ctx: dict[str, Any], battle: dict[str, Any], risk: dict[str, Any], stock: dict[str, Any] | None = None) -> dict[str, Any]:
    bars = list((stock or {}).get("bars") or _bars(ctx))
    if len(bars) < 3:
        return {"status": UNKNOWN, "score": None, "invalidation_distance": None, "expected_upside": None, "expected_downside": None, "estimated_win_probability": None, "reward_risk_ratio": None, "liquidity_risk": None, "gap_risk": None, "tail_risk": None, "time_cost": None, "evidence": [evidence("赔率样本", len(bars), "stock_daily_bars", supports=False)]}
    closes = [num(item.get("close")) for item in bars]
    closes = [item for item in closes if item is not None]
    if len(closes) < 3:
        return {"status": UNKNOWN, "score": None, "evidence": [evidence("收盘价", UNKNOWN, "stock_daily_bars", supports=False)]}
    current = closes[-1]
    recent_low = min(closes[-5:])
    recent_high = max(closes[-5:])
    downside = (current - recent_low) / current * 100 if current else None
    upside = (recent_high - current) / current * 100 if current else None
    rr = upside / downside if upside is not None and downside and downside > 0 else None
    liq = 30 if not any(num(item.get("volume")) for item in bars[-5:]) else 15
    tail = 70 if battle.get("regime") in {"DEFENSIVE", "CAPITULATION"} else 35
    score = clamp((rr or 0) * 18 + (100 - liq) * 0.25 + (100 - tail) * 0.2)
    return {"status": "FAVORABLE" if score >= 60 else "UNFAVORABLE" if score < 40 else "MIXED", "score": score, "invalidation_distance": round(downside, 2) if downside is not None else None, "expected_upside": round(upside, 2) if upside is not None else None, "expected_downside": round(downside, 2) if downside is not None else None, "estimated_win_probability": None, "reward_risk_ratio": round(rr, 2) if rr is not None else None, "liquidity_risk": liq, "gap_risk": 40 if ctx.get("microstructure") else None, "tail_risk": tail, "time_cost": 50, "evidence": [evidence("近5日高低区间", {"high": recent_high, "low": recent_low}, "stock_daily_bars"), evidence("估计胜率", "未验证", "validation_registry", "FACT", supports=False)], "note": "未完成 PIT、成本、样本外验证前，不显示胜率，不把赔率分数当作预测。"}


def cognitive_risk(ctx: dict[str, Any], *, completeness_pct: float | None = None, missing_inputs: list[str] | None = None, skill_runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Separate observable model/data risks from optional user-behaviour inputs."""
    missing = list(missing_inputs or [])
    if completeness_pct is None:
        completeness_pct = 0.0 if not ctx else 100.0
    source_status = ctx.get("source_status") or {}
    unavailable = [key for key, value in source_status.items() if value not in {"available", "cached"}]
    model_risks: list[dict[str, Any]] = []
    if completeness_pct < 40 or missing:
        model_risks.append({"risk": "数据完整度不足", "risk_type": "DATA_QUALITY", "severity": "HIGH" if completeness_pct < 40 else "MEDIUM", "value": completeness_pct, "evidence": [evidence("缺失输入", missing or UNKNOWN, "roci_completeness", supports=bool(missing))]})
    if unavailable:
        model_risks.append({"risk": "适配器来源不可用", "risk_type": "SOURCE_UNAVAILABLE", "severity": "HIGH", "value": unavailable, "evidence": [evidence("来源状态", unavailable, "roci_adapters", supports=False)]})
    unvalidated = [item.get("skill_id") for item in (skill_runs or []) if item.get("triggered") and item.get("status") in {"SHADOW", "DETECT_ONLY", "HYPOTHESIS"}]
    if unvalidated:
        model_risks.append({"risk": "未验证 Skill 仅能观察", "risk_type": "UNVALIDATED_SKILL", "severity": "MEDIUM", "value": unvalidated, "evidence": [evidence("未验证触发数", len(unvalidated), "roci_skill_registry", supports=False)]})
    if ctx.get("cache_used"):
        model_risks.append({"risk": "当前依赖缓存快照", "risk_type": "CACHE_DEPENDENCY", "severity": "LOW", "value": ctx.get("cache_age_seconds"), "evidence": [evidence("缓存年龄秒数", ctx.get("cache_age_seconds", UNKNOWN), "roci_cache")]})

    # Human-risk observations are accepted only when explicitly supplied by a
    # user/journal adapter.  The engine never infers FOMO or revenge trading
    # from a price chart.
    human_input = ctx.get("human_risk") or ctx.get("user_behavior") or {}
    human_risks: list[dict[str, Any]] = []
    for key, label in (("fomo", "FOMO追涨"), ("revenge_trading", "报复性交易"), ("overconfidence", "连续盈利后膨胀"), ("loss_chasing", "连续亏损后加码"), ("anchoring", "成本锚定"), ("sunk_cost", "沉没成本"), ("out_of_regime", "模式外交易"), ("forced_trading", "天天必须交易")):
        value = human_input.get(key) if isinstance(human_input, dict) else None
        if value not in (None, "", False, 0):
            human_risks.append({"risk": label, "risk_type": key.upper(), "severity": "MEDIUM", "value": value, "evidence": [evidence(label, value, "user_behavior", "FACT")]})

    severities = [item["severity"] for item in (*model_risks, *human_risks)]
    level = "EXTREME" if severities.count("HIGH") >= 2 else "HIGH" if "HIGH" in severities else "MEDIUM" if "MEDIUM" in severities else "LOW" if severities else UNKNOWN
    return {"level": level, "human_risks": human_risks, "model_risks": model_risks, "unknown_human_risk": not bool(human_input), "evidence": [item for risk in (*model_risks, *human_risks) for item in risk.get("evidence", [])], "policy": "不从价格图表推断人的意图；未验证模型不参与 ACTION。"}


def action(
    battle: dict[str, Any],
    contradiction_result: dict[str, Any],
    risk: dict[str, Any],
    stress: dict[str, Any],
    asym: dict[str, Any],
    completeness_pct: float,
    *,
    active_confirmation_count: int = 0,
    has_active_confirmation: bool = False,
) -> dict[str, Any]:
    shadow_excluded = ["ROCI-S067至ROCI-S076"]
    active_confirmation_count = max(active_confirmation_count, 1 if has_active_confirmation else 0)
    if completeness_pct < 40:
        chosen, reason = "NO_TRADE", "关键数据完整度不足，无法定义风险补偿和失效条件。"
    elif battle.get("regime") in {"DEFENSIVE", "CAPITULATION"} and asym.get("status") != "FAVORABLE":
        chosen, reason = "DEFEND", "当前战场偏防御，且尚未观察到足够的风险转化证据。"
    elif contradiction_result.get("candidate_key") == "UNKNOWN":
        chosen, reason = "WAIT", "主要矛盾无法从当前证据中确定，等待宽度、结构和资金确认。"
    elif asym.get("status") == "UNFAVORABLE":
        chosen, reason = "NO_TRADE", "当前无法得到风险补偿，且赔率结构不利。"
    elif (
        battle.get("regime") in {"STRONG_OFFENSE", "NORMAL_OFFENSE", "RECOVERY"}
        and risk.get("status") in {"MOSTLY_PRICED", "PARTIALLY_PRICED"}
        and stress.get("state") in {"RESILIENT", "ANTIFRAGILE"}
        and asym.get("status") == "FAVORABLE"
        and active_confirmation_count >= 2
    ):
        chosen, reason = "ATTACK", "生态、风险定价、压力响应和至少两个独立 ACTIVE Skill 同时确认。"
    elif asym.get("status") == "FAVORABLE" and risk.get("status") in {"MOSTLY_PRICED", "PARTIALLY_PRICED"} and active_confirmation_count >= 1:
        chosen, reason = "PROBE", "机会结构和风险边界可定义，但仍需独立 ACTIVE 证据确认；Shadow 不参与动作。"
    elif stress.get("state") == "ANTIFRAGILE" and asym.get("status") == "FAVORABLE":
        chosen, reason = "PROBE", "压力后的恢复证据较好，但需要下一验证窗口确认。"
    elif battle.get("regime") in {"STRONG_OFFENSE", "NORMAL_OFFENSE", "RECOVERY"}:
        chosen, reason = "HOLD", "战场环境尚可，但当前快照不足以升级为主动进攻。"
    else:
        chosen, reason = "WAIT", "方向存在观察价值，但时机、赔率或确认仍不充分。"
    return {"action": chosen, "reason": reason, "confidence": clamp(min(completeness_pct, num(contradiction_result.get("confidence")) or 0)), "risk_budget": 25 if chosen == "ATTACK" else 10 if chosen == "PROBE" else 0, "active_confirmation_count": active_confirmation_count, "invalidations": contradiction_result.get("what_would_worsen") or [], "next_checks": contradiction_result.get("what_would_resolve") or [], "shadow_excluded": shadow_excluded, "evidence": [evidence("战场生态", battle.get("regime"), "roci_battlefield", "INFERENCE"), evidence("主要矛盾", contradiction_result.get("statement"), "roci_contradiction", "INFERENCE"), evidence("赔率状态", asym.get("status"), "roci_asymmetry", "INFERENCE"), evidence("独立ACTIVE确认", active_confirmation_count, "roci_skill_registry", "FACT")], "disclaimer": "ROCI 是研究与模拟工具，不连接券商，不构成买卖指令。"}
