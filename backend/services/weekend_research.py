"""Persistent AI weekend research orchestration (V3.0).

The module reuses the verified market workbench and individual-stock decision
profiles.  It stores compact research conclusions and evidence references, not
duplicate quote/history data.
"""

from __future__ import annotations

import asyncio
import json
import math
import uuid
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select

from database import async_session
from models import (
    ResearchHypothesis,
    ResearchJudgment,
    ResearchMarketCase,
    ResearchSession,
)
from services.data_collector import shanghai_now
from services.ai_service import ai_service
from services.market_decision_workbench import market_decision_workbench_service
from services.stock_essence_decision import CONTRACT_VERSION as STOCK_PROFILE_VERSION
from services.stock_essence_decision import stock_essence_decision_service


RESEARCH_VERSION = "weekend-research-v3.0.0"
MARKET_DATA_VERSION = "market-workbench-v2.0"
STRATEGY_VERSION = "adaptive-strategy+overnight-v2"
PROMPT_VERSION = "weekend-research-evidence-v3"
MODEL_VERSION = "structured-multi-agent-v3"
SESSION_STATUSES = {
    "DRAFT", "RUNNING", "COMPLETED", "REVIEWING", "VALIDATING", "ARCHIVED", "FAILED",
}
REVIEW_ACTIONS = {"APPROVE", "MODIFY", "REJECT"}
VALIDATION_RESULTS = {"CORRECT", "PARTIAL", "WRONG", "UNVERIFIABLE"}


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _round(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return min(upper, max(lower, value))


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _unique(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))


def _average(values: list[float | None]) -> float | None:
    observed = [value for value in values if value is not None]
    return sum(observed) / len(observed) if observed else None


def _confidence(score: float | None) -> str:
    if score is None:
        return "低"
    return "高" if score >= 80 else "中" if score >= 60 else "低"


def _topic_matches(topic: str | None, *values: Any) -> bool:
    query = str(topic or "").strip().lower()
    if not query:
        return False
    fields = [str(value or "").strip().lower() for value in values]
    fields = [value for value in fields if value]
    if any(field in query or query in field for field in fields):
        return True
    haystack = " ".join(fields)
    return any(token in haystack for token in query.split() if token)


def _session_dict(row: ResearchSession, *, include_report: bool = True) -> dict:
    payload = {
        "id": row.id,
        "mode": row.mode,
        "topic": row.topic,
        "status": row.status,
        "stage": row.stage,
        "progress": row.progress,
        "as_of_date": row.as_of_date.isoformat() if row.as_of_date else None,
        "source_data_date": row.source_data_date.isoformat() if row.source_data_date else None,
        "versions": {
            "market_data": row.market_data_version,
            "fundamental_data": row.fundamental_data_version,
            "strategy": row.strategy_version,
            "model": row.model_version,
            "prompt": row.prompt_version,
            "research": row.research_version,
        },
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }
    report = dict(row.report) if isinstance(row.report, dict) else {}
    if include_report:
        payload["report"] = report
    else:
        conclusion = report.get("conclusion") or {}
        payload["summary"] = {
            "market_state": conclusion.get("market_state"),
            "principal_conflict": conclusion.get("principal_conflict"),
            "action": conclusion.get("action"),
            "candidate_count": len(report.get("candidates") or []),
            "sector_count": len(report.get("sectors") or []),
            "data_completeness_pct": (report.get("data_quality") or {}).get("completeness_pct"),
        }
    return payload


def _judgment_dict(row: ResearchJudgment) -> dict:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "target_type": row.target_type,
        "target_key": row.target_key,
        "ai_judgment": dict(row.ai_judgment) if isinstance(row.ai_judgment, dict) else {},
        "action": row.action,
        "user_judgment": row.user_judgment,
        "reason": row.reason,
        "validation_status": row.validation_status,
        "validation_result": row.validation_result,
        "correct_party": row.correct_party,
        "validated_at": row.validated_at.isoformat() if row.validated_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _hypothesis_dict(row: ResearchHypothesis) -> dict:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "key": row.hypothesis_key,
        "scope": row.scope,
        "target": row.target,
        "title": row.title,
        "statement": row.statement,
        "nature": row.nature,
        "horizon": row.horizon,
        "evidence": list(row.evidence) if isinstance(row.evidence, list) else [],
        "falsification": list(row.falsification) if isinstance(row.falsification, list) else [],
        "due_date": row.due_date.isoformat() if row.due_date else None,
        "status": row.status,
        "actual_result": row.actual_result,
        "validation_result": row.validation_result,
        "error_type": row.error_type,
        "validated_at": row.validated_at.isoformat() if row.validated_at else None,
    }


def _candidate_seeds(workbench: dict, topic: str | None = None) -> list[dict]:
    rows: list[dict] = []
    daily = (workbench.get("daily_short_term_recommendations") or {}).get("candidates") or []
    rows.extend({**item, "research_source": "daily_recommendation"} for item in daily)
    rows.extend({**item, "research_source": item.get("source") or "workbench"} for item in workbench.get("candidates") or [])
    for line in workbench.get("main_lines") or []:
        leader = line.get("leader") or {}
        if leader.get("code"):
            rows.append({
                "code": leader.get("code"),
                "name": leader.get("name"),
                "sector": line.get("name"),
                "score": line.get("strength_score"),
                "why_selected": [line.get("evidence"), f"板块生命周期：{line.get('lifecycle') or '待核验'}"],
                "why_not_full": line.get("risk_flags") or ["仍需个股本质决策核验"],
                "abandon_conditions": ["板块退潮", "个股Alpha转负", "关键结构失效"],
                "research_source": "sector_leader",
            })

    by_code: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("code") or row.get("stock_code") or "").strip()
        if len(code) != 6 or not code.isdigit():
            continue
        current = by_code.get(code, {})
        merged = {**current, **{key: value for key, value in row.items() if value not in (None, "", "-")}}
        merged["code"] = code
        merged["name"] = str(merged.get("name") or merged.get("stock_name") or code)
        merged["sector"] = str(merged.get("sector") or merged.get("industry") or "未分类")
        merged["why_selected"] = _unique([
            *(current.get("why_selected") or []), *(row.get("why_selected") or []), row.get("reason"),
        ])[:5]
        merged["why_not_full"] = _unique([
            *(current.get("why_not_full") or []), *(row.get("why_not_full") or []),
            *(row.get("risk_flags") or []),
        ])[:5]
        merged["abandon_conditions"] = _unique([
            *(current.get("abandon_conditions") or []), *(row.get("abandon_conditions") or []),
            *(row.get("invalidation_conditions") or []),
        ])[:5]
        by_code[code] = merged

    def sort_key(item: dict) -> tuple[int, float]:
        matched = int(_topic_matches(topic, item.get("code"), item.get("name"), item.get("sector")))
        return matched, _number(item.get("score")) or 0.0

    return sorted(by_code.values(), key=sort_key, reverse=True)


def _market_autopsy(workbench: dict) -> dict:
    state = workbench.get("market_state") or {}
    structure = workbench.get("structure_health") or {}
    alignment = workbench.get("volume_price_alignment") or {}
    crowding = workbench.get("crowding_risk") or {}
    cognition = workbench.get("market_cognition") or {}
    headline = workbench.get("headline_metrics") or {}
    dimensions = {str(item.get("id")): item for item in state.get("dimensions") or []}
    safety = _number((dimensions.get("risk") or {}).get("score"))
    risk_score = _average([
        100 - safety if safety is not None else None,
        _number(crowding.get("score")),
    ])
    health = _number(structure.get("score"))
    attack = _average([_number(state.get("score")), health])
    facts = _unique([
        *(cognition.get("facts") or []),
        *(alignment.get("evidence") or []),
        *(structure.get("evidence") or []),
    ])[:8]
    quantitative_changes = cognition.get("quantitative_changes") or []
    change_text = "；".join(
        (
            f"{item.get('label') or item.get('id')}：{item.get('evidence') or item.get('status')}"
            if isinstance(item, dict) else str(item)
        )
        for item in quantitative_changes
        if item
    ) or "当前没有形成可确认的连续量变信号"
    alignment_status = str(alignment.get("status") or "unknown")
    breadth = headline.get("up_down_ratio")
    answers = [
        {"question": "指数为什么涨/跌？", "answer": (workbench.get("ai_judgement") or {}).get("market_summary") or "指数证据不足", "nature": "INFERENCE"},
        {"question": "成交量是否支持？", "answer": "量价匹配" if alignment_status == "aligned" else "量价背离，成交承接不足" if alignment_status == "divergent" else "量价数据不足", "nature": "INFERENCE"},
        {"question": "是普涨还是抱团？", "answer": f"上涨/下跌家数比为 {breadth}" if breadth is not None else "市场宽度待核验", "nature": "FACT"},
        {"question": "市场宽度是否改善？", "answer": "；".join((dimensions.get("breadth") or {}).get("evidence") or ["宽度证据不足"])[0:180], "nature": "FACT"},
        {"question": "高位股是否健康？", "answer": "拥挤风险偏高" if (_number(crowding.get("score")) or 0) >= 70 else "未确认高位拥挤恶化" if crowding.get("score") is not None else "高位股样本不足", "nature": "INFERENCE"},
        {"question": "低位股是否跟随？", "answer": "结构扩散需结合板块宽度继续验证" if health is None else ("结构扩散较健康" if health >= 65 else "后排扩散不足"), "nature": "INFERENCE"},
        {"question": "本周最大的结构变化是什么？", "answer": change_text, "nature": "INFERENCE"},
    ]
    return {
        "market_state": state.get("state_label") or "不可判定",
        "state_code": state.get("state_code"),
        "market_health": _round(health),
        "attack_intensity": _round(attack),
        "risk_level": _round(risk_score),
        "headline_metrics": headline,
        "facts": facts,
        "answers": answers,
        "structural_change": change_text,
        "one_line": (
            f"市场处于{state.get('state_label') or '数据待齐'}，"
            f"结构健康度{_round(health) if health is not None else '--'}，"
            f"当前行动为{cognition.get('action_label') or '观察'}。"
        ),
    }


def _conflict_research(workbench: dict) -> dict:
    cognition = workbench.get("market_cognition") or {}
    principal = cognition.get("principal_contradiction") or {}
    dominant = cognition.get("dominant_aspect") or {}
    hypothesis = cognition.get("practice_hypothesis") or {}
    dimensions = sorted(
        [item for item in (workbench.get("market_state") or {}).get("dimensions") or [] if item.get("observed")],
        key=lambda item: _number(item.get("score")) or 101,
    )
    secondary = [
        {"name": item.get("label"), "score": item.get("score"), "evidence": item.get("evidence") or []}
        for item in dimensions[1:3]
    ]
    return {
        "surface": (workbench.get("ai_judgement") or {}).get("market_summary"),
        "facts": (cognition.get("facts") or [])[:6],
        "principal": principal.get("statement") or "数据不足，暂不确定主要矛盾",
        "principal_evidence": principal.get("evidence") or [],
        "dominant_aspect": dominant.get("statement") or "多空主导待核验",
        "dominant_direction": dominant.get("direction") or "unknown",
        "secondary": secondary,
        "stage": cognition.get("stage") or {},
        "validation": {
            "statement": hypothesis.get("statement"),
            "window": hypothesis.get("validation_window") or "T+1/T+3/T+5",
            "falsification": hypothesis.get("falsification") or [],
        },
        "confidence_pct": principal.get("confidence_pct"),
    }


def _sector_research(workbench: dict, seeds: list[dict]) -> list[dict]:
    output = []
    for line in (workbench.get("main_lines") or [])[:5]:
        name = str(line.get("name") or "未分类")
        members = [item for item in seeds if str(item.get("sector") or "") == name]
        leader = dict(line.get("leader") or {})
        roles = []
        if leader.get("code"):
            roles.append({"role": "核心龙头", **leader})
        for index, member in enumerate(members[:3]):
            if str(member.get("code")) == str(leader.get("code")):
                continue
            roles.append({
                "role": "趋势核心" if index == 0 else "补涨" if index == 1 else "跟风",
                "code": member.get("code"),
                "name": member.get("name"),
                "score": member.get("score"),
            })
        lifecycle = str(line.get("lifecycle") or "观察")
        strengthening = lifecycle in {"启动", "强化", "扩散", "修复"}
        output.append({
            "rank": line.get("rank"),
            "name": name,
            "classification": line.get("classification"),
            "lifecycle": lifecycle,
            "direction": "强化" if strengthening else "弱化" if lifecycle in {"分化预警", "退潮"} else "观察",
            "strength_score": line.get("strength_score"),
            "breadth": line.get("breadth"),
            "change_pct": line.get("change_pct"),
            "main_net_inflow": line.get("main_net_inflow"),
            "roles": roles,
            "evidence": [line.get("evidence")],
            "risk_flags": line.get("risk_flags") or [],
            "stage_switch": {
                "strengthen_if": ["成交与板块宽度同步扩大", "核心股保持Alpha且后排跟随"],
                "weaken_if": ["核心股负反馈扩大", "板块宽度跌破50%", "资金连续转为净流出"],
            },
            "why_core": (
                f"{leader.get('name') or '当前龙头'}同时位于板块强度、领涨表现和结构辨识度前列；"
                "角色仍需以后续Alpha和资金持续性验证。"
            ),
            "nature": "INFERENCE",
        })
    return output


def _research_class(profile: dict, sector_lifecycle: str) -> tuple[str, str]:
    expectation = profile.get("expectation_gap") or {}
    catalysts = profile.get("catalysts") or {}
    if expectation.get("state") == "正预期差":
        return "D", "预期差"
    if catalysts.get("net_direction") == "positive" and catalysts.get("highest_grade") in {"A", "B"}:
        return "E", "事件催化"
    if sector_lifecycle == "启动":
        return "B", "趋势启动"
    if sector_lifecycle in {"分化预警", "修复"}:
        return "C", "分歧修复"
    return "A", "趋势延续"


def _stock_research(seed: dict, profile: dict | None, sector_lifecycle: str) -> dict:
    profile = profile or {}
    company = profile.get("company") or {}
    fundamentals = profile.get("fundamentals") or {}
    valuation = profile.get("valuation") or {}
    capital = profile.get("capital_impact") or {}
    attribution = profile.get("attribution") or {}
    alpha = profile.get("alpha") or {}
    role = profile.get("sector_role") or {}
    dependency = profile.get("sector_dependency") or {}
    catalysts = profile.get("catalysts") or {}
    expectation = profile.get("expectation_gap") or {}
    emotion = profile.get("emotion") or {}
    risk = profile.get("risk_reward") or {}
    strategy = profile.get("strategy_fit") or {}
    decision = profile.get("decision") or {}
    audit = profile.get("data_audit") or {}
    code = str(company.get("stock_code") or seed.get("code") or "")
    name = str(company.get("stock_name") or seed.get("name") or code)
    sector = str(company.get("industry") or seed.get("sector") or "未分类")
    class_code, class_label = _research_class(profile, sector_lifecycle)
    state = str(decision.get("state") or "OBSERVE")
    research_status = {
        "EXECUTE": "TRIGGER_WAIT",
        "CAUTION": "WATCH",
        "OBSERVE": "RESEARCH",
        "AVOID": "NO_TRADE",
        "NO_TRADE": "NO_TRADE",
    }.get(state, "RESEARCH")
    alpha_score = _number(alpha.get("score") or attribution.get("individual_alpha_score"))
    rr = _number(risk.get("risk_reward_ratio"))
    quality = fundamentals.get("earnings_quality")
    advantages = _unique([
        "盈利质量高" if quality == "高" else None,
        f"个股Alpha评分{alpha_score}" if alpha_score is not None else None,
        f"板块独立性{dependency.get('independence_level')}" if dependency.get("independence_level") else None,
        *(seed.get("why_selected") or []),
    ])
    risks = _unique([
        "盈利质量低" if quality == "低" else None,
        f"风险收益比仅{rr}" if rr is not None and rr < 1.5 else None,
        f"估值状态：{valuation.get('state')}" if valuation.get("state") else None,
        f"情绪{emotion.get('level')}" if emotion.get("level") in {"偏热", "过热"} else None,
        *(decision.get("reasons") or []), *(seed.get("why_not_full") or []),
    ])
    trigger = _unique([
        "市场主要矛盾不再恶化",
        f"{sector}板块维持{sector_lifecycle}" if sector_lifecycle else None,
        "个股Alpha、资金与价格结构同步确认",
    ])
    invalidation = _unique([
        *(decision.get("invalidation_conditions") or []), *(seed.get("abandon_conditions") or []),
    ])[:6]
    evidence = list(profile.get("evidence") or [])
    coverage = _number(audit.get("public_source_coverage_pct"))
    return {
        "code": code,
        "name": name,
        "sector": sector,
        "research_class": class_code,
        "research_class_label": class_label,
        "research_status": research_status,
        "decision_state": state,
        "decision_label": decision.get("label") or "观察",
        "score": seed.get("score"),
        "company": {
            "main_business": company.get("main_business"),
            "core_products": company.get("core_products") or [],
            "industry": company.get("industry"),
            "total_market_cap": company.get("total_market_cap"),
            "circulating_market_cap": company.get("circulating_market_cap"),
            "free_float_market_cap": company.get("free_float_market_cap"),
            "current_price": company.get("current_price") or seed.get("price"),
        },
        "earnings": {
            "quality": quality,
            "quality_score": fundamentals.get("earnings_quality_score"),
            "state": fundamentals.get("earnings_state"),
            "sustainability": fundamentals.get("earnings_sustainability"),
            "operating_vs_non_recurring": fundamentals.get("operating_vs_non_recurring"),
            "metrics": fundamentals.get("metrics") or {},
        },
        "valuation": {
            "state": valuation.get("state"),
            "pe_ttm": valuation.get("current_pe_ttm"),
            "pe_percentile_3y": valuation.get("pe_percentile_3y"),
            "industry_pe_percentile": valuation.get("industry_pe_percentile"),
            "is_cyclical": valuation.get("is_cyclical"),
            "cycle_phase": valuation.get("cycle_phase_label"),
            "pe_inversion_risk": valuation.get("pe_inversion_risk"),
            "risk": risk.get("valuation_risk"),
        },
        "capital_impact": capital,
        "attribution": attribution,
        "alpha": alpha,
        "sector_role": role,
        "sector_dependency": dependency,
        "catalysts": catalysts,
        "expectation_gap": expectation,
        "emotion": emotion,
        "risk_reward": risk,
        "strategy_fit": strategy,
        "why_research": advantages[:4] or ["进入结构化候选池，等待更多证据"],
        "main_advantage": advantages[0] if advantages else "尚未形成可核验优势",
        "main_risk": risks[0] if risks else "未触发已观测硬风险，但仍需市场验证",
        "trigger_conditions": trigger,
        "invalidation_conditions": invalidation or ["板块退潮", "个股结构失效"],
        "evidence_chain": evidence,
        "data_completeness_pct": coverage or 0,
        "confidence": _confidence(coverage),
        "source_data_date": (profile.get("meta") or {}).get("data_date") or seed.get("data_date"),
        "nature": "INFERENCE",
    }


def _scenarios(workbench: dict) -> list[dict]:
    action = str((workbench.get("market_cognition") or {}).get("final_action") or "observe")
    return [
        {
            "key": "continued_attack",
            "name": "继续进攻",
            "support": "中" if action == "execute" else "低",
            "conditions": ["成交量放大", "核心板块强化", "高位股负反馈停止", "市场宽度改善"],
            "action": "提高进攻权重，优先研究核心趋势股；仍需日内与14:55规则确认。",
            "invalidation": ["量价再次背离", "主线宽度下降"],
            "nature": "FORECAST",
        },
        {
            "key": "range_divergence",
            "name": "震荡分歧",
            "support": "高" if action in {"caution", "observe"} else "中",
            "conditions": ["指数稳定", "量能不足", "板块快速轮动"],
            "action": "降低交易频率，提高盈利质量、Alpha和风险收益比门槛。",
            "invalidation": ["成交与宽度同步显著改善", "指数结构破坏"],
            "nature": "FORECAST",
        },
        {
            "key": "risk_release",
            "name": "风险释放",
            "support": "高" if action == "no_trade" else "中",
            "conditions": ["指数结构破坏", "核心板块退潮", "市场宽度恶化", "高位负反馈扩大"],
            "action": "停止高风险策略，提高现金权重；研究仍继续，不因风险提示停止观察。",
            "invalidation": ["市场结构和成交同步修复"],
            "nature": "FORECAST",
        },
    ]


def _build_hypotheses(report: dict, data_date: date | None) -> list[dict]:
    due = (data_date + timedelta(days=7)).isoformat() if data_date else None
    conflict = report.get("conflicts") or {}
    output = [{
        "key": "market-principal-conflict",
        "scope": "market",
        "target": "A股市场",
        "title": "主要矛盾是否缓解",
        "statement": (conflict.get("validation") or {}).get("statement") or conflict.get("principal") or "市场结构等待验证",
        "horizon": "T+5",
        "evidence": conflict.get("principal_evidence") or [],
        "falsification": (conflict.get("validation") or {}).get("falsification") or [],
        "due_date": due,
    }]
    for sector in (report.get("sectors") or [])[:3]:
        output.append({
            "key": f"sector-{sector.get('rank')}",
            "scope": "sector",
            "target": sector.get("name"),
            "title": f"{sector.get('name')}生命周期验证",
            "statement": f"{sector.get('name')}当前处于{sector.get('lifecycle')}，后续方向倾向{sector.get('direction')}。",
            "horizon": "T+5",
            "evidence": sector.get("evidence") or [],
            "falsification": (sector.get("stage_switch") or {}).get("weaken_if") or [],
            "due_date": due,
        })
    for stock in (report.get("candidates") or [])[:5]:
        output.append({
            "key": f"stock-{stock.get('code')}",
            "scope": "stock",
            "target": stock.get("code"),
            "title": f"{stock.get('name')}研究逻辑验证",
            "statement": f"若触发条件成立，{stock.get('name')}的{stock.get('research_class_label')}逻辑仍值得跟踪；未满足时保持{stock.get('research_status')}。",
            "horizon": "T+5",
            "evidence": stock.get("why_research") or [],
            "falsification": stock.get("invalidation_conditions") or [],
            "due_date": due,
        })
    return output


def build_research_report(
    workbench: dict,
    profiles: dict[str, dict],
    selected_seeds: list[dict],
    excluded_seeds: list[dict],
    *,
    mode: str,
    topic: str | None,
    generated_at: str | None = None,
) -> dict:
    """Build a deterministic, evidence-bound report from verified snapshots."""
    autopsy = _market_autopsy(workbench)
    conflicts = _conflict_research(workbench)
    all_seeds = [*selected_seeds, *excluded_seeds]
    sectors = _sector_research(workbench, all_seeds)
    lifecycle_by_sector = {str(item.get("name")): str(item.get("lifecycle") or "观察") for item in sectors}
    candidates = [
        _stock_research(seed, profiles.get(str(seed.get("code"))), lifecycle_by_sector.get(str(seed.get("sector")), "观察"))
        for seed in selected_seeds
    ]
    exclusions = [{
        "code": item.get("code"),
        "name": item.get("name"),
        "sector": item.get("sector"),
        "reason": "；".join(_unique(item.get("why_not_full") or [])) or "综合证据排序未进入本轮核心研究池",
        "nature": "INFERENCE",
    } for item in excluded_seeds[:5]]
    scenarios = _scenarios(workbench)
    meta = workbench.get("meta") or {}
    market_coverage = _number(meta.get("coverage_pct")) or 0.0
    stock_coverages = [_number(item.get("data_completeness_pct")) for item in candidates]
    completeness = _average([market_coverage, *stock_coverages]) or market_coverage
    action = (workbench.get("market_cognition") or {}).get("action_label") or "观察"
    conclusion = {
        "market_state": autopsy.get("market_state"),
        "principal_conflict": conflicts.get("principal"),
        "dominant_aspect": conflicts.get("dominant_aspect"),
        "next_week_focus": ((conflicts.get("validation") or {}).get("statement") or "观察成交、宽度和主线是否同步改善"),
        "action": action,
        "statement": f"当前市场处于{autopsy.get('market_state')}，主要矛盾是{conflicts.get('principal')}。下周以条件验证为主，系统行动为{action}。",
        "nature": "INFERENCE",
    }
    topic_text = str(topic or "").strip()
    topic_result = None
    if topic_text:
        matched_sectors = [
            item for item in sectors
            if _topic_matches(topic_text, item.get("name"), item.get("classification"))
        ]
        matched_stocks = [
            item for item in candidates
            if _topic_matches(topic_text, item.get("code"), item.get("name"), item.get("sector"))
        ]
        topic_result = {
            "question": topic_text,
            "facts": _unique([fact for item in matched_sectors for fact in item.get("evidence") or []])[:6],
            "inference": (
                f"已匹配{len(matched_sectors)}个板块、{len(matched_stocks)}只研究股票；"
                "结论仅由当前报告证据形成。"
            ),
            "counter_evidence": _unique([risk for item in matched_sectors for risk in item.get("risk_flags") or []])[:5],
            "uncertainty": "没有匹配到的公开事实不会由AI补造；需要通过下一交易周行情继续验证。",
            "nature": "INFERENCE",
        }
    report = {
        "meta": {
            "research_version": RESEARCH_VERSION,
            "contract_version": meta.get("contract_version") or MARKET_DATA_VERSION,
            "mode": mode,
            "topic": topic_text or None,
            "generated_at": generated_at or shanghai_now().isoformat(),
            "source_data_date": meta.get("decision_date"),
            "source": meta.get("source"),
            "is_realtime": bool(meta.get("is_realtime")),
            "scope": "周末/非交易时段深度研究与下一交易周准备",
        },
        "conclusion": conclusion,
        "market_autopsy": autopsy,
        "conflicts": conflicts,
        "sectors": sectors,
        "candidates": candidates,
        "exclusions": exclusions,
        "scenarios": scenarios,
        "topic_research": topic_result,
        "data_quality": {
            "completeness_pct": _round(completeness),
            "confidence": _confidence(completeness),
            "missing_fields": (workbench.get("audit") or {}).get("missing_fields") or [],
            "stale_components": (workbench.get("audit") or {}).get("stale_components") or [],
            "policy": "缺失数据显式标记并降低置信度，不猜测、不填造。",
        },
        "agent_runs": [
            {"agent": "MarketAgent", "status": "completed", "output": "市场尸检"},
            {"agent": "ConflictAgent", "status": "completed", "output": "主要矛盾与阶段"},
            {"agent": "SectorAgent", "status": "completed", "output": f"{len(sectors)}个重点板块"},
            {"agent": "FundamentalAgent", "status": "completed", "output": f"{len(candidates)}只个股本质画像"},
            {"agent": "AttributionAgent", "status": "completed", "output": "Beta/Alpha/资金归因"},
            {"agent": "ExpectationAgent", "status": "completed", "output": "预期差与催化分级"},
            {"agent": "EmotionAgent", "status": "completed", "output": "情绪状态变量"},
            {"agent": "RiskAgent", "status": "completed", "output": "风险收益与失效条件"},
            {"agent": "ScenarioAgent", "status": "completed", "output": "三情景条件系统"},
            {"agent": "ReportAgent", "status": "completed", "output": "事实/推断/预测分层报告"},
        ],
        "guardrails": [
            "研究候选池不是股票推荐",
            "周末研究不能替代14:55技术条件与次日竞价确认",
            "AI解释不能修改结构化行情、财务或交易数据",
            "观望和不交易是一等结论",
        ],
    }
    report["hypotheses"] = _build_hypotheses(report, _as_date(meta.get("decision_date")))
    return report


async def _ai_report_synthesis(report: dict) -> dict:
    """Let the language model explain, never replace, the structured result."""
    structured = {
        "conclusion": report.get("conclusion"),
        "market_autopsy": report.get("market_autopsy"),
        "conflicts": report.get("conflicts"),
        "sectors": (report.get("sectors") or [])[:5],
        "candidates": [{
            "code": item.get("code"),
            "name": item.get("name"),
            "class": item.get("research_class_label"),
            "status": item.get("research_status"),
            "advantage": item.get("main_advantage"),
            "risk": item.get("main_risk"),
            "trigger": item.get("trigger_conditions"),
            "invalidation": item.get("invalidation_conditions"),
        } for item in (report.get("candidates") or [])[:5]],
        "scenarios": report.get("scenarios"),
        "data_quality": report.get("data_quality"),
    }
    system_prompt = (
        "你是A股周末研究中心的ReportAgent。只能解释输入中的结构化事实和推断，"
        "不得新增行情、财务、资金、概率或股票，不得修改市场行动、候选状态和评分。"
        "禁止使用必涨、稳赚、确定买入等表述。用中文输出四段：本周本质、主要矛盾、"
        "下周验证、风险边界。总字数不超过500字，明确研究候选不等于推荐。"
    )
    try:
        narrative = await asyncio.wait_for(
            ai_service.generate(
                json.dumps(structured, ensure_ascii=False, default=str)[:18000],
                system_prompt=system_prompt,
            ),
            timeout=22,
        )
    except Exception as exc:
        return {"available": False, "status": type(exc).__name__, "narrative": None}
    if not narrative or narrative.startswith("[AI服务"):
        return {"available": False, "status": "provider_unavailable", "narrative": None}
    return {
        "available": True,
        "status": "completed",
        "narrative": narrative.strip(),
        "nature": "INFERENCE",
        "guard": "AI只解释结构化报告，不修改评分、状态或交易数据",
    }


class WeekendResearchService:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    async def _update(self, session_id: str, **values: Any) -> None:
        async with async_session() as session:
            row = await session.get(ResearchSession, session_id)
            if row is None:
                return
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = datetime.utcnow()
            await session.commit()

    def _schedule(self, session_id: str) -> None:
        current = self._tasks.get(session_id)
        if current and not current.done():
            return
        task = asyncio.create_task(self._run(session_id), name=f"weekend-research-{session_id}")
        self._tasks[session_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(session_id, None))

    async def start(self, *, mode: str = "quick", topic: str | None = None) -> dict:
        normalized_mode = str(mode or "quick").lower()
        if normalized_mode not in {"quick", "deep", "topic"}:
            raise ValueError("研究模式必须是 quick、deep 或 topic")
        topic_text = str(topic or "").strip() or None
        if normalized_mode == "topic" and not topic_text:
            raise ValueError("专题研究需要填写研究问题")
        async with async_session() as session:
            active = (await session.execute(
                select(ResearchSession)
                .where(ResearchSession.status.in_(["DRAFT", "RUNNING"]))
                .order_by(desc(ResearchSession.created_at))
                .limit(1)
            )).scalar_one_or_none()
        if active:
            self._schedule(active.id)
            return _session_dict(active, include_report=False)
        now = shanghai_now()
        session_id = f"wr_{uuid.uuid4().hex[:20]}"
        row = ResearchSession(
            id=session_id,
            mode=normalized_mode,
            topic=topic_text,
            status="DRAFT",
            stage="等待研究任务",
            progress=0,
            as_of_date=now.date(),
            market_data_version=MARKET_DATA_VERSION,
            fundamental_data_version=STOCK_PROFILE_VERSION,
            strategy_version=STRATEGY_VERSION,
            model_version=MODEL_VERSION,
            prompt_version=PROMPT_VERSION,
            research_version=RESEARCH_VERSION,
            report={},
        )
        async with async_session() as session:
            session.add(row)
            await session.commit()
        self._schedule(session_id)
        return _session_dict(row, include_report=False)

    async def resume_incomplete_runs(self) -> None:
        try:
            async with async_session() as session:
                rows = list((await session.execute(
                    select(ResearchSession).where(ResearchSession.status.in_(["DRAFT", "RUNNING"]))
                )).scalars().all())
                for row in rows:
                    row.status = "DRAFT"
                    row.stage = "服务恢复后继续研究"
                    row.error = None
                await session.commit()
            for row in rows:
                self._schedule(row.id)
        except Exception as exc:
            print(f"Weekend research resume failed: {type(exc).__name__}")

    async def _load_profile(self, seed: dict, as_of: date | None) -> tuple[str, dict | None]:
        code = str(seed.get("code") or "")
        try:
            profile = await asyncio.wait_for(
                stock_essence_decision_service.get(code, as_of=as_of, force=False),
                timeout=55,
            )
            return code, profile
        except Exception as exc:
            print(f"Weekend stock profile {code} failed: {type(exc).__name__}")
            return code, None

    async def _run(self, session_id: str) -> None:
        try:
            async with async_session() as session:
                row = await session.get(ResearchSession, session_id)
                if row is None:
                    return
                mode, topic = row.mode, row.topic
            await self._update(session_id, status="RUNNING", stage="读取最近完整交易日", progress=5, error=None)
            workbench = await asyncio.wait_for(
                market_decision_workbench_service.get(force=False), timeout=40,
            )
            source_date = _as_date((workbench.get("meta") or {}).get("decision_date"))
            await self._update(
                session_id,
                stage="市场尸检与主要矛盾",
                progress=25,
                source_data_date=source_date,
            )
            all_seeds = _candidate_seeds(workbench, topic)
            limit = 3 if mode == "quick" else 5
            selected = all_seeds[:limit]
            excluded = all_seeds[limit:limit + 5]
            await self._update(session_id, stage="板块生命周期与候选池", progress=38)

            profiles: dict[str, dict] = {}
            if selected:
                semaphore = asyncio.Semaphore(3)

                async def load_limited(seed: dict) -> tuple[str, dict | None]:
                    async with semaphore:
                        return await self._load_profile(seed, source_date)

                tasks = [asyncio.create_task(load_limited(seed)) for seed in selected]
                completed = 0
                for future in asyncio.as_completed(tasks):
                    code, profile = await future
                    if profile:
                        profiles[code] = profile
                    completed += 1
                    await self._update(
                        session_id,
                        stage=f"个股本质分析 {completed}/{len(selected)}",
                        progress=38 + round(completed / len(selected) * 42),
                    )

            await self._update(session_id, stage="情景推演与证据链", progress=86)
            report = build_research_report(
                workbench,
                profiles,
                selected,
                excluded,
                mode=mode,
                topic=topic,
            )
            if mode in {"deep", "topic"}:
                await self._update(session_id, stage="AI综合解读", progress=93)
                report["ai_synthesis"] = await _ai_report_synthesis(report)
            else:
                report["ai_synthesis"] = {
                    "available": False,
                    "status": "quick_mode_structured_only",
                    "narrative": None,
                }
            await self._persist_report(session_id, report, source_date)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Weekend research run failed: {type(exc).__name__}: {exc}")
            await self._update(
                session_id,
                status="FAILED",
                stage="研究失败，可重新发起",
                error=f"{type(exc).__name__}: {str(exc)[:240]}",
            )

    async def _persist_report(self, session_id: str, report: dict, source_date: date | None) -> None:
        async with async_session() as session:
            row = await session.get(ResearchSession, session_id)
            if row is None:
                return
            for item in report.get("hypotheses") or []:
                hypothesis = (await session.execute(select(ResearchHypothesis).where(
                    ResearchHypothesis.session_id == session_id,
                    ResearchHypothesis.hypothesis_key == item["key"],
                ))).scalar_one_or_none()
                values = {
                    "scope": item["scope"],
                    "target": item.get("target"),
                    "title": item["title"],
                    "statement": item["statement"],
                    "nature": "FORECAST",
                    "horizon": item.get("horizon") or "T+5",
                    "evidence": item.get("evidence") or [],
                    "falsification": item.get("falsification") or [],
                    "due_date": _as_date(item.get("due_date")),
                }
                if hypothesis:
                    for key, value in values.items():
                        setattr(hypothesis, key, value)
                else:
                    session.add(ResearchHypothesis(
                        session_id=session_id,
                        hypothesis_key=item["key"],
                        **values,
                    ))
            row.report = report
            row.source_data_date = source_date
            row.market_data_version = str(
                (report.get("meta") or {}).get("contract_version") or MARKET_DATA_VERSION
            )
            row.status = "COMPLETED"
            row.stage = "研究完成，等待用户审阅"
            row.progress = 100
            row.error = None
            row.completed_at = datetime.utcnow()
            row.updated_at = datetime.utcnow()
            await session.commit()

    async def get(self, session_id: str) -> dict | None:
        async with async_session() as session:
            row = await session.get(ResearchSession, session_id)
            if row is None:
                return None
            judgments = list((await session.execute(
                select(ResearchJudgment)
                .where(ResearchJudgment.session_id == session_id)
                .order_by(ResearchJudgment.updated_at.desc())
            )).scalars().all())
            hypotheses = list((await session.execute(
                select(ResearchHypothesis)
                .where(ResearchHypothesis.session_id == session_id)
                .order_by(ResearchHypothesis.id.asc())
            )).scalars().all())
        payload = _session_dict(row)
        payload["judgments"] = [_judgment_dict(item) for item in judgments]
        payload["hypotheses"] = [_hypothesis_dict(item) for item in hypotheses]
        return payload

    async def latest(self) -> dict | None:
        async with async_session() as session:
            row = (await session.execute(
                select(ResearchSession).order_by(desc(ResearchSession.created_at)).limit(1)
            )).scalar_one_or_none()
        return await self.get(row.id) if row else None

    async def list(self, *, limit: int = 30, status: str | None = None) -> list[dict]:
        statement = select(ResearchSession)
        if status:
            normalized = status.upper()
            if normalized not in SESSION_STATUSES:
                raise ValueError("研究状态无效")
            statement = statement.where(ResearchSession.status == normalized)
        statement = statement.order_by(desc(ResearchSession.created_at)).limit(max(1, min(limit, 100)))
        async with async_session() as session:
            rows = list((await session.execute(statement)).scalars().all())
        return [_session_dict(row, include_report=False) for row in rows]

    @staticmethod
    def _ai_judgment(report: dict, target_type: str, target_key: str) -> dict:
        if target_type == "market":
            return dict(report.get("conclusion") or {})
        collection_key = {
            "sector": "sectors", "stock": "candidates", "scenario": "scenarios",
        }.get(target_type)
        if collection_key:
            for item in report.get(collection_key) or []:
                keys = {str(item.get("code") or ""), str(item.get("name") or ""), str(item.get("key") or "")}
                if target_key in keys:
                    return dict(item)
        return {"statement": "原AI判断未在报告中找到", "nature": "INFERENCE"}

    async def save_judgment(self, session_id: str, payload: dict) -> dict:
        target_type = str(payload.get("target_type") or "").lower()
        target_key = str(payload.get("target_key") or "").strip()
        action = str(payload.get("action") or "").upper()
        if target_type not in {"market", "sector", "stock", "scenario"} or not target_key:
            raise ValueError("审阅对象无效")
        if action not in REVIEW_ACTIONS:
            raise ValueError("审阅动作必须是 APPROVE、MODIFY 或 REJECT")
        async with async_session() as session:
            research = await session.get(ResearchSession, session_id)
            if research is None:
                raise LookupError("研究记录不存在")
            report = dict(research.report) if isinstance(research.report, dict) else {}
            row = (await session.execute(select(ResearchJudgment).where(
                ResearchJudgment.session_id == session_id,
                ResearchJudgment.target_type == target_type,
                ResearchJudgment.target_key == target_key,
            ))).scalar_one_or_none()
            values = {
                "ai_judgment": self._ai_judgment(report, target_type, target_key),
                "action": action,
                "user_judgment": str(payload.get("user_judgment") or "").strip() or None,
                "reason": str(payload.get("reason") or "").strip() or None,
                "validation_status": "PENDING",
                "updated_at": datetime.utcnow(),
            }
            if row:
                for key, value in values.items():
                    setattr(row, key, value)
            else:
                row = ResearchJudgment(
                    session_id=session_id,
                    target_type=target_type,
                    target_key=target_key,
                    **values,
                )
                session.add(row)
            research.status = "REVIEWING"
            research.updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(row)
        return _judgment_dict(row)

    async def validate_judgment(self, session_id: str, judgment_id: int, payload: dict) -> dict:
        result = str(payload.get("validation_result") or "").upper()
        if result not in VALIDATION_RESULTS:
            raise ValueError("验证结果必须是 CORRECT、PARTIAL、WRONG 或 UNVERIFIABLE")
        correct_party = str(payload.get("correct_party") or "").upper() or None
        if correct_party and correct_party not in {"AI", "USER", "BOTH", "NEITHER"}:
            raise ValueError("正确方必须是 AI、USER、BOTH 或 NEITHER")
        async with async_session() as session:
            row = await session.get(ResearchJudgment, judgment_id)
            if row is None or row.session_id != session_id:
                raise LookupError("用户判断不存在")
            row.validation_status = "VALIDATED"
            row.validation_result = str(payload.get("actual_result") or result).strip()
            row.correct_party = correct_party
            row.validated_at = datetime.utcnow()
            research = await session.get(ResearchSession, session_id)
            if research:
                research.status = "VALIDATING"
            await session.commit()
            await session.refresh(row)
        return _judgment_dict(row)

    async def validate_hypothesis(self, hypothesis_id: int, payload: dict) -> dict:
        result = str(payload.get("result") or "").upper()
        if result not in VALIDATION_RESULTS:
            raise ValueError("验证结果必须是 CORRECT、PARTIAL、WRONG 或 UNVERIFIABLE")
        actual = str(payload.get("actual_result") or "").strip()
        if not actual:
            raise ValueError("请填写真实市场结果")
        error_type = str(payload.get("error_type") or "").strip() or ("逻辑问题" if result == "WRONG" else None)
        lesson = str(payload.get("lesson") or "").strip() or None
        correct_party = str(payload.get("correct_party") or "").upper() or None
        if correct_party and correct_party not in {"AI", "USER", "BOTH", "NEITHER"}:
            raise ValueError("正确方必须是 AI、USER、BOTH 或 NEITHER")
        async with async_session() as session:
            row = await session.get(ResearchHypothesis, hypothesis_id)
            if row is None:
                raise LookupError("研究假设不存在")
            row.status = result
            row.actual_result = actual
            row.validation_result = result
            row.error_type = error_type
            row.validated_at = datetime.utcnow()
            research = await session.get(ResearchSession, row.session_id)
            report = dict(research.report) if research and isinstance(research.report, dict) else {}
            if research:
                research.status = "VALIDATING"
            target_type = row.scope
            target_key = "market" if row.scope == "market" else str(row.target or "")
            judgment = (await session.execute(select(ResearchJudgment).where(
                ResearchJudgment.session_id == row.session_id,
                ResearchJudgment.target_type == target_type,
                ResearchJudgment.target_key == target_key,
            ))).scalar_one_or_none()
            if judgment:
                judgment.validation_status = "VALIDATED"
                judgment.validation_result = actual
                judgment.correct_party = correct_party
                judgment.validated_at = datetime.utcnow()
            case = (await session.execute(select(ResearchMarketCase).where(
                ResearchMarketCase.hypothesis_id == row.id
            ))).scalar_one_or_none()
            case_values = {
                "session_id": row.session_id,
                "case_type": row.scope,
                "title": row.title,
                "summary": actual,
                "market_context": {
                    "source_data_date": (report.get("meta") or {}).get("source_data_date"),
                    "market_state": (report.get("conclusion") or {}).get("market_state"),
                    "original_statement": row.statement,
                    "evidence": row.evidence or [],
                },
                "outcome": result,
                "error_attribution": error_type,
                "lesson": lesson,
                "tags": [row.scope, row.horizon, error_type] if error_type else [row.scope, row.horizon],
                "case_date": shanghai_now().date(),
            }
            if case:
                for key, value in case_values.items():
                    setattr(case, key, value)
            else:
                session.add(ResearchMarketCase(hypothesis_id=row.id, **case_values))
            await session.commit()
            await session.refresh(row)
        return _hypothesis_dict(row)

    async def create_hypothesis(self, payload: dict) -> dict:
        session_id = str(payload.get("session_id") or "").strip()
        statement = str(payload.get("statement") or "").strip()
        if not session_id or not statement:
            raise ValueError("研究记录和假设内容不能为空")
        scope = str(payload.get("scope") or "market").lower()
        if scope not in {"market", "sector", "stock"}:
            raise ValueError("假设范围必须是 market、sector 或 stock")
        async with async_session() as session:
            research = await session.get(ResearchSession, session_id)
            if research is None:
                raise LookupError("研究记录不存在")
            row = ResearchHypothesis(
                session_id=session_id,
                hypothesis_key=f"user-{uuid.uuid4().hex[:12]}",
                scope=scope,
                target=str(payload.get("target") or "").strip() or None,
                title=str(payload.get("title") or "用户研究假设").strip()[:200],
                statement=statement,
                nature="FORECAST",
                horizon=str(payload.get("horizon") or "T+5").upper()[:20],
                evidence=_unique(list(payload.get("evidence") or [])),
                falsification=_unique(list(payload.get("falsification") or [])),
                due_date=_as_date(payload.get("due_date")),
                status="PENDING",
            )
            session.add(row)
            research.status = "VALIDATING"
            await session.commit()
            await session.refresh(row)
        return _hypothesis_dict(row)

    async def get_hypothesis(self, hypothesis_id: int) -> dict | None:
        async with async_session() as session:
            row = await session.get(ResearchHypothesis, hypothesis_id)
        return _hypothesis_dict(row) if row else None

    async def cases(self, *, limit: int = 50) -> list[dict]:
        async with async_session() as session:
            rows = list((await session.execute(
                select(ResearchMarketCase)
                .order_by(desc(ResearchMarketCase.case_date), desc(ResearchMarketCase.id))
                .limit(max(1, min(limit, 200)))
            )).scalars().all())
        return [{
            "id": row.id,
            "session_id": row.session_id,
            "hypothesis_id": row.hypothesis_id,
            "case_type": row.case_type,
            "title": row.title,
            "summary": row.summary,
            "market_context": dict(row.market_context) if isinstance(row.market_context, dict) else {},
            "outcome": row.outcome,
            "error_attribution": row.error_attribution,
            "lesson": row.lesson,
            "tags": list(row.tags) if isinstance(row.tags, list) else [],
            "case_date": row.case_date.isoformat() if row.case_date else None,
        } for row in rows]

    async def get_case(self, case_id: int) -> dict | None:
        async with async_session() as session:
            row = await session.get(ResearchMarketCase, case_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "session_id": row.session_id,
            "hypothesis_id": row.hypothesis_id,
            "case_type": row.case_type,
            "title": row.title,
            "summary": row.summary,
            "market_context": dict(row.market_context) if isinstance(row.market_context, dict) else {},
            "outcome": row.outcome,
            "error_attribution": row.error_attribution,
            "lesson": row.lesson,
            "tags": list(row.tags) if isinstance(row.tags, list) else [],
            "case_date": row.case_date.isoformat() if row.case_date else None,
        }

    async def archive(self, session_id: str) -> dict:
        await self._update(session_id, status="ARCHIVED", stage="已归档")
        result = await self.get(session_id)
        if result is None:
            raise LookupError("研究记录不存在")
        return result

    async def insights(self) -> dict:
        async with async_session() as session:
            hypotheses = list((await session.execute(select(ResearchHypothesis))).scalars().all())
            judgments = list((await session.execute(select(ResearchJudgment))).scalars().all())
            cases = list((await session.execute(select(ResearchMarketCase))).scalars().all())
        validated = [item for item in hypotheses if item.status in VALIDATION_RESULTS]
        scored = [item for item in validated if item.status != "UNVERIFIABLE"]
        correct = sum(item.status == "CORRECT" for item in scored)
        partial = sum(item.status == "PARTIAL" for item in scored)
        error_counts = Counter(item.error_type for item in validated if item.error_type)
        party_counts = Counter(item.correct_party for item in judgments if item.correct_party)
        return {
            "hypotheses": {
                "total": len(hypotheses),
                "pending": sum(item.status == "PENDING" for item in hypotheses),
                "validated": len(validated),
                "accuracy_pct": _round((correct + partial * 0.5) / len(scored) * 100, 1) if scored else None,
                "results": dict(Counter(item.status for item in validated)),
            },
            "judgments": {
                "total": len(judgments),
                "actions": dict(Counter(item.action for item in judgments)),
                "correct_party": dict(party_counts),
            },
            "errors": [{"type": key, "count": value} for key, value in error_counts.most_common()],
            "case_count": len(cases),
            "knowledge_memory": [
                {
                    "pattern": key,
                    "observations": value,
                    "guidance": f"未来遇到相似研究时优先检查{key}证据。",
                }
                for key, value in error_counts.most_common(8)
            ],
        }


weekend_research_service = WeekendResearchService()
