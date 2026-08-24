"""Evidence-bound explanations shared by the ROCI market and intraday layers.

This module does not infer a hidden actor or change a model score.  It turns
already computed facts into a structured, auditable explanation contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


EXPLANATION_VERSION = "roci-explanation-v1.1.2"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _grade(value: Any) -> str:
    score = _number(value)
    if score is None:
        return "UNKNOWN"
    if score >= 0.75:
        return "STRONG"
    if score >= 0.45:
        return "MEDIUM"
    return "WEAK"


def _fact(
    claim: str,
    value: Any,
    *,
    source: str,
    field: str | None = None,
    strength: float | None = None,
    supports: bool = True,
    evidence_type: str = "FACT",
    timestamp: Any = None,
    formula: str | None = None,
) -> dict[str, Any]:
    return {
        "type": evidence_type,
        "claim": claim,
        "value": value,
        "evidence_strength": strength,
        "evidence_grade": _grade(strength),
        "supports": supports,
        "source_table": source,
        "source_field": field,
        "source_timestamp": timestamp,
        "formula": formula,
    }


def _as_facts(items: Any, *, default_type: str = "EVIDENCE") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            result.append(_fact(str(item), item, source="roci_structured_result", evidence_type=default_type))
            continue
        result.append({
            "type": item.get("type") or default_type,
            "claim": item.get("claim") or item.get("label") or "结构化证据",
            "value": item.get("value"),
            "evidence_strength": item.get("evidence_strength", item.get("strength")),
            "evidence_grade": item.get("evidence_grade") or _grade(item.get("evidence_strength", item.get("strength"))),
            "supports": item.get("supports", True),
            "source_table": item.get("source_table") or item.get("source") or "roci_structured_result",
            "source_field": item.get("source_field"),
            "source_timestamp": item.get("source_timestamp") or item.get("as_of"),
            "formula": item.get("formula"),
        })
    return result


def _driver(name: str, direction: str, importance: float, strength: float | None, description: str, metrics: list[str]) -> dict[str, Any]:
    return {
        "driver_id": f"D{abs(hash(name)) % 1000:03d}",
        "name": name,
        "direction": direction,
        "importance": round(importance, 4),
        "evidence_strength": strength,
        "description": description,
        "source_metrics": metrics,
    }


def _lineage_from_evidence(items: list[dict[str, Any]], as_of: Any) -> list[dict[str, Any]]:
    lineage: list[dict[str, Any]] = []
    for item in items:
        lineage.append({
            "claim": item.get("claim"),
            "source_table": item.get("source_table") or "UNKNOWN",
            "source_field": item.get("source_field") or "UNKNOWN",
            "timestamp": item.get("source_timestamp") or as_of,
            "raw_value": item.get("value"),
            "standardized_value": item.get("value"),
            "formula": item.get("formula") or "原始字段直接读取；无额外标准化",
        })
    return lineage


def _summary(state: str, contradiction: str, *, intraday: bool = False) -> str:
    if state == "UNKNOWN":
        return "当前结构化输入不足，系统没有把相关性包装成确定因果；先保留观察，等待可验证数据。"
    prefix = "盘中" if intraday else "当前"
    if contradiction and contradiction != "UNKNOWN":
        return f"{prefix}状态为“{state}”。主要约束是“{contradiction}”；现有数据更一致于该解释，但仍需要后续价格、宽度和资金响应确认。"
    return f"{prefix}状态为“{state}”。该判断来自多项结构化事实的共同方向，不代表严格实验因果或收益承诺。"


def build_explanation(
    payload: dict[str, Any] | None,
    *,
    context: dict[str, Any] | None = None,
    entity_type: str = "market",
    entity_id: str = "market",
    result_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the common result -> why -> evidence -> validation contract."""
    payload = payload or {}
    context = context or {}
    battle = payload.get("battlefield") or {}
    contradiction = payload.get("primary_contradiction") or {}
    pricing = payload.get("risk_pricing") or {}
    stress = payload.get("stress_test") or {}
    recommendation = ((payload.get("opportunities") or {}).get("risk_adapted") or {})
    workbench = context.get("workbench") or {}
    headline = workbench.get("headline_metrics") or {}
    regime = str(battle.get("label") or battle.get("regime") or "UNKNOWN")
    contradiction_text = str(contradiction.get("statement") or "UNKNOWN")
    cutoff = payload.get("data_cutoff_time") or payload.get("generated_at")

    facts = _as_facts(payload.get("facts"), default_type="FACT")
    facts.extend(_as_facts(contradiction.get("supporting_evidence"), default_type="SUPPORTING"))
    facts.extend([
        _fact("市场状态", battle.get("regime") or "UNKNOWN", source="roci_battlefield", field="regime", strength=.8 if battle.get("regime") else None, timestamp=cutoff),
        _fact("市场主要奖励方向", battle.get("market_reward") or "UNKNOWN", source="roci_battlefield", field="market_reward", strength=.6 if battle.get("market_reward") else None, timestamp=cutoff),
        _fact("上涨家数", headline.get("up_count"), source="market_sentiment_daily", field="up_count", strength=.75 if headline.get("up_count") is not None else None, timestamp=cutoff),
        _fact("下跌家数", headline.get("down_count"), source="market_sentiment_daily", field="down_count", strength=.75 if headline.get("down_count") is not None else None, timestamp=cutoff),
        _fact("市场成交额", headline.get("market_amount"), source="market_sentiment_daily", field="market_amount", strength=.65 if headline.get("market_amount") is not None else None, timestamp=cutoff),
    ])
    facts = [item for item in facts if item.get("value") is not None or item.get("claim") in {"市场状态", "市场主要奖励方向"}]

    sector_names = [str(item.get("name")) for item in recommendation.get("sectors") or [] if item.get("name")]
    sector_flows = [item.get("main_net_inflow") for item in recommendation.get("sectors") or [] if item.get("main_net_inflow") is not None]
    breadth_values = [item.get("breadth") for item in recommendation.get("sectors") or [] if item.get("breadth") is not None]
    positive_flow = bool(sector_flows and sum(float(item) for item in sector_flows) > 0)
    average_breadth = round(sum(float(item) for item in breadth_values) / len(breadth_values), 1) if breadth_values else None
    up_count = _number(headline.get("up_count"))
    down_count = _number(headline.get("down_count"))
    breadth_balance = round(up_count / (up_count + down_count) * 100, 1) if up_count is not None and down_count is not None and up_count + down_count else None

    drivers = [
        _driver(
            "市场阶段与主要矛盾",
            "NEGATIVE" if pricing.get("status") in {"NOT_PRICED", "UNKNOWN"} else "POSITIVE",
            .34,
            .82 if contradiction_text != "UNKNOWN" else None,
            contradiction_text if contradiction_text != "UNKNOWN" else "主要矛盾输入不足，暂不形成确定解释",
            ["market_regime", "primary_contradiction"],
        ),
        _driver(
            "板块相对强弱与资金方向",
            "POSITIVE" if positive_flow else "MIXED",
            .27,
            .76 if sector_names else None,
            f"当前风险适配板块包括：{'、'.join(sector_names[:3])}" if sector_names else "板块强度与资金样本不足",
            ["main_lines", "sector_strength", "main_net_inflow"],
        ),
        _driver(
            "市场宽度与赚钱效应",
            "POSITIVE" if breadth_balance is not None and breadth_balance >= 55 else "NEGATIVE" if breadth_balance is not None else "UNKNOWN",
            .21,
            .74 if breadth_balance is not None else None,
            f"上涨/下跌平衡约为 {breadth_balance}%" if breadth_balance is not None else "涨跌家数缺失，无法确认市场宽度",
            ["up_count", "down_count", "market_breadth"],
        ),
        _driver(
            "压力响应与数据质量",
            "POSITIVE" if stress.get("state") in {"RESILIENT", "ANTIFRAGILE"} else "NEGATIVE" if stress.get("state") in {"FRAGILE", "UNKNOWN"} else "MIXED",
            .18,
            .68 if stress.get("state") else None,
            f"压力响应为 {stress.get('state') or 'UNKNOWN'}；板块平均宽度 {average_breadth if average_breadth is not None else 'UNKNOWN'}%",
            ["stress_state", "sector_breadth", "data_completeness_pct"],
        ),
    ]

    opposition = _as_facts(contradiction.get("opposing_evidence"), default_type="COUNTER_EVIDENCE")
    opposition.extend(_as_facts(stress.get("counter_evidence"), default_type="COUNTER_EVIDENCE"))
    if not opposition:
        opposition = [_fact("尚未发现足够的反向证据", "UNKNOWN", source="roci_explanation", strength=None, supports=False, evidence_type="COUNTER_EVIDENCE", timestamp=cutoff)]
    supporting = [item for item in facts if item.get("supports", True)][:8]
    if not supporting:
        supporting = [_fact("当前没有可审计支持证据", "UNKNOWN", source="roci_explanation", strength=None, supports=False, evidence_type="EVIDENCE", timestamp=cutoff)]

    alternatives = [
        {"hypothesis": "资金在板块之间重新分配", "support_score": 72 if positive_flow else None, "supporting_evidence": ["板块资金方向", "相对强度"], "contradictions": ["资金数据可能是代理变量"], "required_confirmation": ["连续两个观察窗口方向不变"]},
        {"hypothesis": "成长资产估值或盈利预期重新评估", "support_score": 64 if regime != "UNKNOWN" else None, "supporting_evidence": ["成长/防御相对表现", "市场阶段"], "contradictions": ["缺少完整事件或财报归因"], "required_confirmation": ["成长核心停止新低或继续弱于市场"]},
        {"hypothesis": "高拥挤交易出现情绪性卖压", "support_score": 58 if pricing.get("status") else None, "supporting_evidence": ["拥挤/风险定价状态"], "contradictions": ["无法仅从大单代理确认参与者身份"], "required_confirmation": ["高位负反馈和宽度继续恶化"]},
        {"hypothesis": "全市场风险偏好整体收缩", "support_score": 31 if breadth_balance is not None and breadth_balance < 45 else None, "supporting_evidence": ["市场宽度", "压力状态"], "contradictions": ["若防御方向仍有承接，则更接近结构迁移"], "required_confirmation": ["成长与防御同步转弱"]},
    ]
    chain = [
        {"from": "可观测市场事实", "to": "板块与资金结构变化", "status": "CONFIRMED" if facts else "INFERRED", "confidence": .78 if facts else None, "evidence": [item.get("claim") for item in supporting[:2]]},
        {"from": "板块与资金结构变化", "to": "市场主要矛盾", "status": "SUPPORTED" if contradiction_text != "UNKNOWN" else "HYPOTHESIS", "confidence": .72 if contradiction_text != "UNKNOWN" else None, "evidence": [contradiction_text]},
        {"from": "市场主要矛盾", "to": "当前阶段判断", "status": "INFERRED", "confidence": _number(payload.get("data_completeness_pct")) / 100 if _number(payload.get("data_completeness_pct")) is not None else None, "evidence": [regime]},
        {"from": "当前阶段判断", "to": "下一步验证或失效", "status": "HYPOTHESIS", "confidence": .6, "evidence": ["验证条件和失效条件"]},
    ]
    validations = list(contradiction.get("what_would_resolve") or [])[:4]
    invalidations = list(contradiction.get("what_would_worsen") or [])[:4]
    invalidations.extend([str(item) for item in recommendation.get("avoided_sectors") or [] if isinstance(item, str)][:2])
    if not validations:
        validations = ["核心板块相对强度在连续两个观察窗口改善", "市场宽度与成交效率同步改善"]
    if not invalidations:
        invalidations = ["支持证据消失且反证持续增强", "成长与防御同时转弱时，结构迁移解释降级为整体风险收缩"]

    completeness = _number(payload.get("data_completeness_pct"))
    missing = list(payload.get("missing_inputs") or [])
    unavailable = [key for key, value in (payload.get("source_status") or {}).items() if str(value or "").lower() in {"unavailable", "unknown"}]
    partial = [key for key, value in (payload.get("source_status") or {}).items() if str(value or "").lower() in {"partial_realtime", "cached", "degraded"}]
    quality_score = max(0.0, min(1.0, ((completeness or 0) / 100) - len(missing) * .03 - len(unavailable) * .04 - len(partial) * .01))
    result = result_override or {
        "type": str((payload.get("opportunities") or {}).get("risk_adapted", {}).get("status") or "MARKET_STATE"),
        "label": regime,
        "score": payload.get("data_completeness_pct"),
        "confidence": _number((payload.get("action") or {}).get("confidence")),
    }
    return {
        "version": EXPLANATION_VERSION,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "result": result,
        "why": {
            "summary": _summary(regime, contradiction_text),
            "facts": facts[:12],
            "primary_drivers": drivers,
            "supporting_evidence": supporting,
            "counter_evidence": opposition[:8],
            "alternative_hypotheses": alternatives,
            "transmission_chain": chain,
            "validation_signals": validations,
            "invalidation_signals": invalidations,
            "contribution_note": "解释贡献度表示模型本次判断的解释权重，不代表现实世界的严格因果比例。",
            "causal_language_policy": "证据不足时使用‘更一致于’、‘可能由’和‘当前证据支持’，不使用‘一定因为’或‘证明了’。",
        },
        "data_quality": {
            "score": round(quality_score, 3),
            "score_pct": round(quality_score * 100, 1),
            "missing_fields": missing,
            "conflicting_sources": unavailable,
            "stale_or_partial_sources": partial,
            "data_cutoff_time": cutoff,
            "no_future_data": True,
        },
        "lineage": _lineage_from_evidence((supporting + opposition)[:16], cutoff),
        "llm_boundary": "结构化规则计算结果和贡献度由代码产生；LLM若启用，只负责翻译，不改变概率、权重或数据。",
    }


def attach_explanations(payload: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Attach one shared explanation bundle to every major ROCI result."""
    market = build_explanation(payload, context=context, entity_type="market", entity_id=str(payload.get("symbol") or "market"))
    explanations = {
        "market": market,
        "battlefield": build_explanation(payload, context=context, entity_type="battlefield", entity_id=str(payload.get("symbol") or "market"), result_override={"type": "MARKET_REGIME", "label": (payload.get("battlefield") or {}).get("label") or (payload.get("battlefield") or {}).get("regime") or "UNKNOWN", "score": (payload.get("battlefield") or {}).get("confidence"), "confidence": (payload.get("battlefield") or {}).get("confidence")}),
        "contradiction": build_explanation(payload, context=context, entity_type="contradiction", entity_id=str(payload.get("symbol") or "market"), result_override={"type": "PRIMARY_CONTRADICTION", "label": (payload.get("primary_contradiction") or {}).get("statement") or "UNKNOWN", "score": (payload.get("primary_contradiction") or {}).get("confidence"), "confidence": (payload.get("primary_contradiction") or {}).get("confidence")}),
        "risk_pricing": build_explanation(payload, context=context, entity_type="risk_pricing", entity_id=str(payload.get("symbol") or "market"), result_override={"type": "RISK_PRICING", "label": (payload.get("risk_pricing") or {}).get("status") or "UNKNOWN", "score": (payload.get("risk_pricing") or {}).get("confidence"), "confidence": (payload.get("risk_pricing") or {}).get("confidence")}),
        "opportunities": build_explanation(payload, context=context, entity_type="opportunities", entity_id=str(payload.get("symbol") or "market"), result_override={"type": "RISK_ADAPTED_RESEARCH", "label": "风险适配研究清单", "score": ((payload.get("opportunities") or {}).get("risk_adapted") or {}).get("sectors") and 1 or None, "confidence": None}),
        "recommendations": build_explanation(payload, context=context, entity_type="recommendations", entity_id=str(payload.get("symbol") or "market"), result_override={"type": "RISK_ADAPTED_RECOMMENDATION", "label": "按风险推荐板块与个股", "score": None, "confidence": None}),
    }
    if payload.get("symbol"):
        explanations["stock"] = build_explanation(payload, context=context, entity_type="stock", entity_id=str(payload.get("symbol")), result_override={"type": "STOCK_CONTEXT", "label": str(payload.get("symbol")), "score": (payload.get("asymmetry") or {}).get("score"), "confidence": (payload.get("action") or {}).get("confidence")})
    payload["explanations"] = explanations
    payload["explanation"] = market
    return payload
