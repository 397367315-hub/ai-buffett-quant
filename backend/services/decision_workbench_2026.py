"""Unified 2026 research and trading decision layer.

This module only interprets observed workbench evidence. Missing data remains
missing, and generated condition states never place real orders.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import date, datetime
from typing import Any

from sqlalchemy import desc, select

from database import async_session
from models import DecisionWorkbenchSnapshot, ResearchMarketCase
from services.data_collector import shanghai_now
from services.market_decision_contract import WORKBENCH_CONTRACT_VERSION


DECISION_2026_VERSION = "a-share-decision-workbench-2026-v1.0.0"
SNAPSHOT_PHASES = {
    "auction_0925": "次日竞价最终确认",
    "morning_1040": "10:40早盘状态冻结",
    "midday_1142": "午间AI战术研究",
    "hypothesis_1330": "13:30反证检查",
    "hypothesis_1400": "14:00反证检查",
    "tail_1440": "14:40尾盘决策窗口",
    "tail_1455": "14:55技术执行确认",
    "close_review": "盘后验证与错误归因",
    "manual": "人工研究快照",
}
# Intraday windows need a timestamp-verified live quote.  A close review still
# belongs to the current trade date, but it runs after the market closes and
# therefore legitimately uses the verified end-of-day snapshot.
SAME_DAY_PHASES = set(SNAPSHOT_PHASES) - {"manual"}
REALTIME_PHASES = SAME_DAY_PHASES - {"close_review"}


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return min(high, max(low, value))


def _scale(value: float | None, low: float, high: float) -> float | None:
    if value is None or high <= low:
        return None
    return _clamp((value - low) / (high - low) * 100)


def _average(values: list[float | None]) -> float | None:
    observed = [value for value in values if value is not None and math.isfinite(value)]
    return sum(observed) / len(observed) if observed else None


def _round(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _unique(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value not in (None, "")))


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _dimension_map(payload: dict) -> dict[str, dict]:
    return {
        str(item.get("id")): item
        for item in (payload.get("market_state") or {}).get("dimensions") or []
        if isinstance(item, dict)
    }


def _market_regime(payload: dict) -> dict:
    state = payload.get("market_state") or {}
    structure = payload.get("structure_health") or {}
    crowding = payload.get("crowding_risk") or {}
    code = str(state.get("state_code") or "S0")
    structure_score = _number(structure.get("score"))
    crowding_score = _number(crowding.get("score"))
    if code == "S1" and (structure_score is None or structure_score >= 65):
        label = "趋势强化"
    elif code in {"S1", "S2"}:
        label = "趋势初期分歧"
    elif code == "S3" and crowding_score is not None and crowding_score >= 65:
        label = "高位分歧"
    elif code == "S3":
        label = "震荡"
    elif code in {"S4", "S5"}:
        label = "退潮"
    else:
        label = "数据待齐"
    return {
        "code": code,
        "label": label,
        "score": _number(state.get("score")),
        "structure_score": structure_score,
        "crowding_score": crowding_score,
        "evidence": _unique([
            *((structure.get("evidence") or [])[:2]),
            *((crowding.get("evidence") or [])[:2]),
        ]),
    }


def _opportunity_density(payload: dict) -> dict:
    dimensions = _dimension_map(payload)
    market_score = _number((payload.get("market_state") or {}).get("score"))
    sector_scores = [
        _number(item.get("strength_score"))
        for item in (payload.get("main_lines") or [])[:3]
    ]
    sector_score = _average(sector_scores)
    recommendations = (payload.get("daily_short_term_recommendations") or {}).get("candidates") or []
    candidates = payload.get("candidates") or []
    independent = 0
    observed_alpha = 0
    sector_change = {
        str(item.get("name")): _number(item.get("change_pct"))
        for item in payload.get("main_lines") or []
    }
    for item in [*recommendations, *candidates]:
        change = _number(item.get("change_pct"))
        sector = sector_change.get(str(item.get("sector") or ""))
        if change is None or sector is None:
            continue
        observed_alpha += 1
        if change - sector >= 1.5:
            independent += 1
    alpha_score = _clamp(independent / max(observed_alpha, 1) * 180) if observed_alpha else None
    capital_score = _number((dimensions.get("capital") or {}).get("score"))
    volume_price_score = _number((payload.get("volume_price_alignment") or {}).get("score"))
    funding_score = _average([capital_score, volume_price_score])
    risk_safety = _number((dimensions.get("risk") or {}).get("score"))
    coverage = _number((payload.get("market_state") or {}).get("coverage_pct"))
    factors = [
        ("market", "市场环境", market_score, 20),
        ("sector", "核心板块", sector_score, 20),
        ("alpha", "Alpha机会", alpha_score, 20),
        ("funding", "资金持续性", funding_score, 15),
        ("risk", "风险安全", risk_safety, 15),
        ("coverage", "数据覆盖", coverage, 10),
    ]
    observed = [(value, weight) for _, _, value, weight in factors if value is not None]
    observed_weight = sum(weight for _, weight in observed)
    score = (
        sum(float(value) * weight for value, weight in observed) / observed_weight
        if observed_weight >= 50 else None
    )
    rounded = _round(score, 1)
    label = (
        "高质量机会密集" if rounded is not None and rounded >= 75
        else "存在选择性机会" if rounded is not None and rounded >= 55
        else "机会稀疏" if rounded is not None and rounded >= 35
        else "不适合强行交易" if rounded is not None
        else "不可计算"
    )
    return {
        "score": rounded,
        "label": label,
        "coverage_pct": round(observed_weight, 1),
        "candidate_count": len(candidates),
        "independent_alpha_count": independent,
        "factors": [
            {"id": key, "label": label_text, "score": _round(value, 1), "weight": weight, "observed": value is not None}
            for key, label_text, value, weight in factors
        ],
        "method": "市场20%+板块20%+Alpha20%+资金15%+风险15%+数据覆盖10%；缺失项不填默认分。",
    }


def _trading_permission(payload: dict, opportunity: dict) -> dict:
    final_action = str((payload.get("market_cognition") or {}).get("final_action") or "no_trade")
    coverage = _number((payload.get("market_state") or {}).get("coverage_pct")) or 0.0
    density = _number(opportunity.get("score"))
    structure = _number((payload.get("structure_health") or {}).get("score"))
    crowding = _number((payload.get("crowding_risk") or {}).get("score"))
    reasons: list[str] = []
    if coverage < 60:
        code, label = "BLOCK", "禁止主动交易"
        reasons.append(f"市场证据覆盖率仅{coverage:.0f}%")
    elif final_action == "no_trade":
        code, label = "BLOCK", "禁止主动交易"
        reasons.append("市场认知闸门为不交易")
    elif final_action == "execute" and density is not None and density >= 70:
        code, label = "ALLOW", "允许进攻"
        reasons.append("市场、板块与机会密度形成正向共振")
    elif final_action in {"execute", "caution"} and density is not None and density >= 48:
        code, label = "CAUTION", "谨慎参与"
        reasons.append("只允许证据完整的高质量机会")
    else:
        code, label = "OBSERVE", "观察为主"
        reasons.append("机会密度或结构承接不足，等待进一步确认")
    if structure is not None and structure < 50:
        reasons.append(f"结构健康度{structure:.1f}偏低")
    if crowding is not None and crowding >= 71:
        reasons.append(f"高位拥挤风险{crowding:.1f}")
    position_cap = {"ALLOW": 60, "CAUTION": 35, "OBSERVE": 15, "BLOCK": 0}[code]
    return {
        "code": code,
        "label": label,
        "allows_new_position": code in {"ALLOW", "CAUTION"},
        "max_total_position_pct": position_cap,
        "reasons": _unique(reasons),
        "rule": "市场许可优先于个股；观察和禁止状态不生成主动执行建议。",
    }


def _dynamic_weights(regime: str) -> dict:
    weights = {
        "market": 20, "sector": 15, "fundamental": 20, "alpha": 15,
        "funding": 10, "technical": 10, "emotion": 5, "event": 5,
    }
    if regime == "趋势强化":
        weights.update({
            "market": 17, "sector": 20, "fundamental": 12, "alpha": 20,
            "funding": 14, "technical": 12, "emotion": 3, "event": 2,
        })
    elif regime in {"震荡", "趋势初期分歧"}:
        weights.update({"market": 15, "sector": 12, "fundamental": 18, "alpha": 23, "funding": 10, "technical": 10, "emotion": 5, "event": 7})
    elif regime in {"高位分歧", "退潮"}:
        weights.update({"market": 22, "sector": 12, "fundamental": 20, "alpha": 14, "funding": 10, "technical": 8, "emotion": 9, "event": 5})
    return {"regime": regime, "weights": weights, "version": "dynamic-weight-2026-v1"}


def _fund_behaviour(change: float | None, volume_ratio: float | None, inflow: float | None) -> dict:
    if change is None:
        return {"code": "UNOBSERVED", "label": "资金行为待核验", "supports_price": None}
    if inflow is not None and inflow > 0 and change > 0 and (volume_ratio is None or volume_ratio >= 1.2):
        return {"code": "ACCUMULATION", "label": "量价资金共振", "supports_price": True}
    if inflow is not None and inflow > 0 and change <= 0:
        return {"code": "ABSORPTION", "label": "资金流入但价格未确认", "supports_price": False}
    if inflow is not None and inflow < 0 and change > 0:
        return {"code": "EMOTION", "label": "价涨资流出", "supports_price": False}
    if inflow is not None and inflow < 0 and change <= 0:
        return {"code": "DISTRIBUTION", "label": "价格资金同步走弱", "supports_price": False}
    if change > 0 and volume_ratio is not None and volume_ratio < 1.0:
        return {"code": "LOW_VOLUME_RISE", "label": "缩量上涨", "supports_price": False}
    return {"code": "NEUTRAL", "label": "中性观察", "supports_price": None}


def _quote_profitability(item: dict, sector: str) -> tuple[float | None, dict]:
    """Use the verified quote snapshot when a PIT row is not in the candidate.

    The quote fields are deliberately labelled as a proxy.  They keep a
    candidate in the research loop, while the decision layer still exposes
    that a disclosed financial statement was not available for this symbol.
    """
    roe = _number(item.get("roe"))
    pe = _number(item.get("pe"))
    parts: list[float] = []
    evidence: list[str] = []
    if roe is not None:
        parts.append(_scale(roe, 0, 25) or 0.0)
        evidence.append(f"行情快照ROE {roe:.1f}%")
    if pe is not None and pe > 0:
        parts.append(85.0 if pe <= 20 else 68.0 if pe <= 40 else 42.0 if pe <= 80 else 20.0)
        evidence.append(f"行情快照PE {pe:.1f}")
    if not parts:
        return None, {"status": "unavailable", "roe": roe, "pe": pe, "source": "quote_snapshot"}
    cycle = any(term in sector for term in ("煤", "钢", "有色", "化工", "航运", "养殖"))
    return _round(_average(parts), 1), {
        "status": "quote_proxy",
        "roe": roe,
        "pe": pe,
        "source": "eastmoney_or_tencent_quote",
        "evidence": evidence,
        "note": "周期行业PE仅作辅助，需结合盈利周期" if cycle else "未替代公告日财务PIT，仅作研究代理",
    }


def _candidate_decisions(payload: dict, permission: dict, dynamic: dict) -> list[dict]:
    lines = {str(item.get("name")): item for item in payload.get("main_lines") or []}
    daily = {
        str(item.get("code")): item
        for item in (payload.get("daily_short_term_recommendations") or {}).get("candidates") or []
    }
    merged: dict[str, dict] = {}
    for item in payload.get("candidates") or []:
        code = str(item.get("code") or "")
        if code:
            merged[code] = dict(item)
    for code, item in daily.items():
        merged[code] = {**dict(item), **merged.get(code, {})}

    market_score = _number((payload.get("market_state") or {}).get("score"))
    output = []
    for code, item in list(merged.items())[:16]:
        recommendation = daily.get(code) or {}
        sector = str(item.get("sector") or recommendation.get("sector") or "未分类")
        line = lines.get(sector) or {}
        change = _number(item.get("change_pct") if item.get("change_pct") is not None else recommendation.get("change_pct"))
        sector_change = _number(line.get("change_pct"))
        alpha = change - sector_change if change is not None and sector_change is not None else None
        alpha_score = _scale(alpha, -3, 5)
        breakdown = {**(recommendation.get("score_breakdown") or {}), **(item.get("score_breakdown") or {})}
        profitability = recommendation.get("profitability") or {}
        fundamental_score = _number(breakdown.get("profitability"))
        if fundamental_score is None:
            fundamental_score, quote_view = _quote_profitability(item, sector)
            if quote_view.get("status") == "quote_proxy":
                profitability = {**quote_view, **profitability}
        sector_score = _number(breakdown.get("sector_strength")) or _number(line.get("strength_score"))
        funding_score = _number(breakdown.get("capital"))
        technical_score = _number(breakdown.get("trend"))
        risk_score = _number(breakdown.get("risk_safety"))
        if risk_score is None:
            risk_score = _number((( _dimension_map(payload).get("risk") or {}).get("score")))
        volume_ratio = _number(recommendation.get("volume_ratio"))
        if volume_ratio is None:
            volume_ratio = _number(item.get("volume_ratio"))
        inflow = _number(recommendation.get("main_net_inflow"))
        if inflow is None:
            inflow = _number(item.get("main_net_inflow"))
        fund = _fund_behaviour(change, volume_ratio, inflow)
        emotion_parts = [
            funding_score,
            alpha_score,
            _scale(volume_ratio, 0.7, 3.0),
            technical_score,
            None,
            risk_score,
        ]
        emotion_observed = [value for value in emotion_parts if value is not None]
        emotion_score = _average(emotion_parts)
        emotion_label = (
            "过热" if emotion_score is not None and emotion_score >= 85
            else "偏热" if emotion_score is not None and emotion_score >= 70
            else "正常" if emotion_score is not None and emotion_score >= 45
            else "偏冷" if emotion_score is not None
            else "不可计算"
        )
        pe = _number(profitability.get("pe"))
        valuation_score = None
        if pe is not None and pe > 0:
            valuation_score = 85.0 if pe <= 20 else 68.0 if pe <= 40 else 42.0 if pe <= 80 else 20.0
        state_dimensions = {
            "market": market_score,
            "sector": sector_score,
            "fundamental": fundamental_score,
            "alpha": alpha_score,
            "funding": funding_score,
            "technical": technical_score,
            "emotion": _round(emotion_score, 1),
            "event": None,
        }
        weighted = [
            (state_dimensions[key], weight)
            for key, weight in dynamic["weights"].items()
            if state_dimensions.get(key) is not None
        ]
        observed_weight = sum(weight for _, weight in weighted)
        state_score = (
            sum(float(value) * weight for value, weight in weighted) / observed_weight
            if observed_weight >= 55 else None
        )
        state_label = (
            "健康" if state_score is not None and state_score >= 75
            else "改善" if state_score is not None and state_score >= 62
            else "中性" if state_score is not None and state_score >= 48
            else "恶化" if state_score is not None and state_score >= 35
            else "高风险" if state_score is not None
            else "不可计算"
        )
        same_day = not bool(item.get("stale")) and str(item.get("data_date") or "") == str((payload.get("meta") or {}).get("decision_date") or "")
        conditions = [
            {"key": "market", "label": "市场许可", "passed": permission["code"] in {"ALLOW", "CAUTION"}, "observed": True},
            {"key": "sector", "label": "板块结构", "passed": sector_score is not None and sector_score >= 60, "observed": sector_score is not None},
            {"key": "alpha", "label": "个股Alpha", "passed": alpha is not None and alpha >= 1.0, "observed": alpha is not None},
            {"key": "funding", "label": "资金行为", "passed": fund["supports_price"] is True, "observed": fund["supports_price"] is not None},
            {"key": "risk", "label": "风险安全", "passed": risk_score is not None and risk_score >= 55, "observed": risk_score is not None},
            {"key": "freshness", "label": "同日数据", "passed": same_day, "observed": bool(item.get("data_date"))},
            {"key": "structure", "label": "策略/价格结构", "passed": bool(item.get("execution_eligible")), "observed": bool(item.get("execution_eligible"))},
        ]
        observed_conditions = [row for row in conditions if row["observed"]]
        passed = sum(row["passed"] for row in observed_conditions)
        necessary = {row["key"]: row for row in conditions}
        all_required = all(necessary[key]["observed"] and necessary[key]["passed"] for key in ("market", "sector", "risk", "freshness"))
        if all_required and necessary["structure"]["passed"] and passed == len(observed_conditions):
            execution_level = "EXECUTE"
            execution_label = "三级：允许执行"
        elif passed >= 4 and permission["code"] in {"ALLOW", "CAUTION"}:
            execution_level = "PREPARE"
            execution_label = "二级：准备条件单"
        elif passed >= 2:
            execution_level = "ALERT"
            execution_label = "一级：进入观察区"
        else:
            execution_level = "EXCLUDE"
            execution_label = "排除"
        failed = [row["label"] for row in conditions if row["observed"] and not row["passed"]]
        unavailable = [row["label"] for row in conditions if not row["observed"]]
        unavailable_labels = {
            "资金行为": "资金行为尚未形成可比证据，不作为通过条件",
            "风险安全": "风险安全字段尚未形成独立证据，不作为通过条件",
            "个股Alpha": "同日板块对照不足，Alpha不作通过条件",
            "策略/价格结构": "尚未经过对应策略触发确认",
            "同日数据": "数据日期未与研究日对齐",
            "板块结构": "板块结构证据不足，不作通过条件",
        }
        why_not = _unique([
            *(item.get("why_not_full") or []),
            *([str(recommendation.get("risk"))] if recommendation.get("risk") else []),
            *(f"{label}未通过" for label in failed),
            *(unavailable_labels.get(label, f"{label}暂无可验证证据") for label in unavailable),
        ])
        output.append({
            "code": code,
            "name": str(item.get("name") or recommendation.get("name") or code),
            "sector": sector,
            "price": _number(item.get("price") if item.get("price") is not None else recommendation.get("price")),
            "change_pct": change,
            "score": _round(state_score, 1),
            "state_label": state_label,
            "data_coverage_pct": round(observed_weight, 1),
            "beta_alpha": {
                "market_beta_pct": None,
                "sector_beta_pct": _round(sector_change, 2),
                "individual_alpha_pct": _round(alpha, 2),
                "alpha_score": _round(alpha_score, 1),
                "detachment": "显著Alpha" if alpha is not None and alpha >= 2 else "抗跌Alpha" if alpha is not None and alpha >= 1 and (sector_change or 0) < 0 else "板块Beta为主" if alpha is not None else "板块对照未形成",
                "method": "同日个股涨幅减所属板块涨幅；同口径指数日收益缺失时市场Beta保持为空。",
            },
            "fundamental": {"score": fundamental_score, **profitability},
            "valuation": {"pe": pe, "score": valuation_score, "note": "周期行业需结合盈利周期，不能因低PE直接判定便宜" if any(term in sector for term in ("煤", "钢", "有色", "化工", "航运")) else "按可用PE作辅助判断"},
            "fund_behaviour": fund,
            "emotion": {
                "score": _round(emotion_score, 1),
                "label": emotion_label,
                "coverage_pct": round(len(emotion_observed) / 6 * 100, 1),
                "dimensions": {
                    "main_funds": _round(funding_score, 1), "relative_strength": _round(alpha_score, 1),
                    "participation": _round(_scale(volume_ratio, 0.7, 3.0), 1), "trend": _round(technical_score, 1),
                    "drawdown_repair": None, "downside_risk": _round(risk_score, 1),
                },
                "boundary": "情绪只描述热度，不直接产生买入信号。",
            },
            "trade_structure": {
                "label": "放量突破" if change is not None and change > 2 and volume_ratio is not None and volume_ratio >= 1.2 else "缩量回踩" if change is not None and -2 <= change <= 0 and volume_ratio is not None and volume_ratio < 1 else "高位分歧" if emotion_score is not None and emotion_score >= 85 else "结构待确认",
                "technical_score": _round(technical_score, 1),
                "single_pattern_trigger_forbidden": True,
            },
            "execution": {
                "level": execution_level,
                "label": execution_label,
                "conditions": conditions,
                "passed_count": passed,
                "observed_count": len(observed_conditions),
                "real_broker_order": False,
            },
            "why_strong": _unique([*(item.get("why_selected") or []), *(recommendation.get("reasons") or [])])[:5],
            "why_not_buy": why_not[:7] or ["必要条件均满足，但仍需人工最终确认"],
            "trigger_conditions": _unique([
                *(f"{row['label']}保持通过" for row in conditions if row["passed"]),
                *((item.get("why_selected") or [])[:2]),
            ])[:6],
            "invalidation_conditions": _unique([
                *(item.get("abandon_conditions") or []),
                *(recommendation.get("invalidation_conditions") or []),
                "市场交易许可降级为禁止主动交易",
                "板块进入退潮或个股Alpha消失",
            ])[:8],
            "detail_href": f"/pro/stock?code={code}",
            "source": str(item.get("source") or recommendation.get("source") or "workbench"),
        })
    output.sort(key=lambda row: (_number(row.get("score")) or -1, row["code"]), reverse=True)
    return output


def _decision_windows(payload: dict, now: datetime) -> list[dict]:
    meta = payload.get("meta") or {}
    data_date = _parse_date(meta.get("decision_date"))
    live_today = bool(meta.get("is_realtime")) and data_date == now.date() and now.weekday() < 5
    minute = now.hour * 60 + now.minute
    definitions = [
        ("auction_0925", "09:25", "竞价最终确认", 9 * 60 + 25),
        ("morning", "09:30-10:40", "早盘决策窗口", 9 * 60 + 30),
        ("morning_1040", "10:40", "状态冻结", 10 * 60 + 40),
        ("midday_1142", "11:42", "午间AI研究", 11 * 60 + 42),
        ("hypothesis", "13:00-14:40", "主动寻找反证", 13 * 60),
        ("tail_1440", "14:40-14:55", "尾盘决策窗口", 14 * 60 + 40),
        ("tail_1455", "14:55", "技术执行确认", 14 * 60 + 55),
        ("close_review", "15:50", "盘后复盘学习", 15 * 60 + 50),
    ]
    rows = []
    for key, time_label, label, start in definitions:
        if not live_today:
            status = "历史参考" if data_date else "等待数据"
        elif minute < start:
            status = "等待"
        elif key == "morning" and minute <= 10 * 60 + 40:
            status = "进行中"
        elif key == "hypothesis" and minute <= 14 * 60 + 40:
            status = "进行中"
        elif key == "tail_1440" and minute <= 14 * 60 + 55:
            status = "进行中"
        else:
            status = "窗口已到"
        rows.append({
            "id": key,
            "time": time_label,
            "label": label,
            "status": status,
            "immutable_after_capture": key in SNAPSHOT_PHASES and key != "manual",
        })
    return rows


def _strategy_lifecycle(payload: dict) -> list[dict]:
    adaptive = {
        str(item.get("strategy_id")): _number(item.get("weight_pct"))
        for item in (payload.get("adaptive_strategy_weights") or {}).get("weights") or []
    }
    rows = []
    for item in payload.get("strategy_health") or []:
        metrics = item.get("metrics") or {}
        samples = int(_number(metrics.get("sample_count")) or 0)
        state = str(item.get("state") or "RECOVERY")
        if state in {"REDUCE", "SUSPENDED"}:
            stage = "DECAY" if state == "REDUCE" else "SUSPENDED"
        elif samples < 5:
            stage = "FORWARD_OBSERVATION"
        elif samples < 20:
            stage = "PAPER_VALIDATION"
        else:
            stage = "ACTIVE"
        strategy_id = str(item.get("id") or "")
        matched_weight = adaptive.get(strategy_id)
        rows.append({
            "id": strategy_id,
            "name": item.get("name"),
            "stage": stage,
            "health_state": state,
            "health_score": item.get("health_score"),
            "sample_count": samples,
            "win_rate_pct": metrics.get("win_rate_pct"),
            "profit_factor": metrics.get("profit_factor"),
            "max_drawdown_amount": metrics.get("max_drawdown_amount"),
            "weight_pct": matched_weight,
            "degradation_detected": state in {"REDUCE", "SUSPENDED"},
            "missing": _unique([*(item.get("missing") or []), "按市场状态分层表现", "参数敏感度"]),
            "rule": "样本不足仅观察；前向期望、回撤或连续亏损恶化时自动降权，不承诺未来胜率。",
        })
    return rows


def build_decision_2026(payload: dict, *, now: datetime | None = None) -> dict:
    now = now or shanghai_now()
    regime = _market_regime(payload)
    opportunity = _opportunity_density(payload)
    permission = _trading_permission(payload, opportunity)
    dynamic = _dynamic_weights(regime["label"])
    candidates = _candidate_decisions(payload, permission, dynamic)
    global_no_buy = _unique([
        *permission["reasons"],
        *((payload.get("risk") or {}).get("market") or []),
        *(f"{item['name']}：{item['why_not_buy'][0]}" for item in candidates[:3] if item.get("why_not_buy")),
    ])
    sectors = []
    for item in payload.get("main_lines") or []:
        lifecycle = str(item.get("lifecycle") or "观察")
        sectors.append({
            **item,
            "permission": "允许研究" if lifecycle in {"启动", "扩散", "强化"} and (_number(item.get("breadth")) or 0) >= 50 else "谨慎" if lifecycle not in {"退潮"} else "排除",
            "internal_structure": "板块强化" if lifecycle in {"扩散", "强化"} and (_number(item.get("breadth")) or 0) >= 65 else "龙头抱团/扩散不足" if (_number(item.get("breadth")) or 0) < 65 else "等待确认",
        })
    return {
        "version": DECISION_2026_VERSION,
        "positioning": "AI辅助研究与决策系统，不构成投资建议",
        "market_regime": regime,
        "trading_permission": permission,
        "opportunity_density": opportunity,
        "sector_map": sectors,
        "dynamic_weights": dynamic,
        "candidate_decisions": candidates,
        "decision_windows": _decision_windows(payload, now),
        "conditional_orders": {
            "alert": [row for row in candidates if row["execution"]["level"] == "ALERT"],
            "prepare": [row for row in candidates if row["execution"]["level"] == "PREPARE"],
            "execute": [row for row in candidates if row["execution"]["level"] == "EXECUTE"],
            "rule": "价格+成交量+板块+市场+风险共同确认；单一价格或评分不得触发执行。",
            "real_broker_order": False,
        },
        "why_not_buy": {
            "reasons": global_no_buy[:10] or ["未触发禁止条件，仍需人工最终判断"],
            "candidate_count": sum(bool(row.get("why_not_buy")) for row in candidates),
            "principle": "高质量股票不等于当前时点值得执行。",
        },
        "exit_engine": {
            "logic_failure": ["板块由强化转为退潮", "个股Alpha转负或持续衰减"],
            "market_deterioration": ["交易许可降级", "市场宽度和成交同步恶化", "高位负反馈扩大"],
            "overheating": ["放量滞涨", "高位巨量", "资金边际流出", "情绪过热"],
            "fixed_stop_is_only_backstop": True,
        },
        "strategy_lifecycle": _strategy_lifecycle(payload),
        "final_questions": [
            {"question": "现在是什么市场？", "answer": regime["label"]},
            {"question": "现在什么板块值得研究？", "answer": "、".join(item["name"] for item in sectors if item["permission"] == "允许研究") or "暂无完成结构确认的板块"},
            {"question": "这只股票为什么强？", "answer": candidates[0]["why_strong"][0] if candidates and candidates[0]["why_strong"] else "当前没有证据完整的个股"},
            {"question": "是真的强还是Beta带动？", "answer": candidates[0]["beta_alpha"]["detachment"] if candidates else "待形成候选"},
            {"question": "风险收益比是否值得？", "answer": permission["label"] + "；执行前仍需个股价格结构核验"},
            {"question": "判断错了如何退出？", "answer": "板块退潮、Alpha消失或交易许可降级即视为原逻辑失效"},
        ],
        "snapshot_registry": {"latest": [], "count": 0, "immutable_windows": True},
        "boundaries": [
            "情绪指数只描述热度，不产生买入信号",
            "10:40、14:40、14:55只是不同信息集下的决策窗口",
            "条件单只减少执行错误，不连接券商",
            "缺失数据保持为空，不按默认分通过",
        ],
    }


def _snapshot_payload(payload: dict) -> dict:
    clean = deepcopy(payload)
    decision = clean.get("decision_2026") or {}
    if isinstance(decision, dict):
        decision["snapshot_registry"] = {"latest": [], "count": 0, "immutable_windows": True}
    return json.loads(json.dumps(clean, ensure_ascii=False, default=str))


def _snapshot_hash(payload: dict) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _snapshot_dict(row: DecisionWorkbenchSnapshot, *, include_payload: bool = False) -> dict:
    result = {
        "id": row.id,
        "decision_date": row.decision_date.isoformat(),
        "source_data_date": row.source_data_date.isoformat(),
        "phase": row.phase,
        "phase_label": row.phase_label,
        "contract_version": row.contract_version,
        "snapshot_hash": row.snapshot_hash,
        "is_realtime": bool(row.is_realtime),
        "evidence": row.evidence or [],
        "user_judgment": row.user_judgment,
        "validation_status": row.validation_status,
        "validation_result": row.validation_result,
        "captured_at": row.captured_at.isoformat() + "Z" if row.captured_at else None,
        "validated_at": row.validated_at.isoformat() + "Z" if row.validated_at else None,
    }
    if include_payload:
        result["payload"] = row.payload
    return result


class DecisionWorkbench2026Service:
    async def list_snapshots(self, *, limit: int = 30, phase: str | None = None) -> list[dict]:
        async with async_session() as session:
            statement = select(DecisionWorkbenchSnapshot)
            if phase:
                statement = statement.where(DecisionWorkbenchSnapshot.phase == phase)
            rows = list((await session.execute(
                statement.order_by(desc(DecisionWorkbenchSnapshot.captured_at)).limit(max(1, min(limit, 100)))
            )).scalars().all())
        return [_snapshot_dict(row) for row in rows]

    async def get_snapshot(self, snapshot_id: int) -> dict:
        async with async_session() as session:
            row = await session.get(DecisionWorkbenchSnapshot, snapshot_id)
        if row is None:
            raise LookupError("决策快照不存在")
        return _snapshot_dict(row, include_payload=True)

    async def decorate(self, payload: dict) -> dict:
        result = deepcopy(payload)
        decision = result.get("decision_2026")
        if not isinstance(decision, dict):
            decision = build_decision_2026(result)
            result["decision_2026"] = decision
        history = await self.list_snapshots(limit=16)
        decision["snapshot_registry"] = {
            "latest": history,
            "count": len(history),
            "immutable_windows": True,
        }
        return result

    async def capture(
        self,
        phase: str,
        *,
        force: bool = True,
        user_judgment: str | None = None,
    ) -> dict:
        if phase not in SNAPSHOT_PHASES:
            raise ValueError(f"不支持的决策窗口：{phase}")
        from services.market_decision_workbench import market_decision_workbench_service

        payload = await market_decision_workbench_service.get(force=force)
        meta = payload.get("meta") or {}
        decision_date = _parse_date(meta.get("decision_date"))
        if decision_date is None:
            raise RuntimeError("工作台缺少决策日，不能冻结快照")
        now = shanghai_now()
        if phase in SAME_DAY_PHASES and decision_date != now.date():
            raise RuntimeError("决策数据不是当前交易日，不能冻结为盘中快照")
        if phase in REALTIME_PHASES and not bool(meta.get("is_realtime")):
            raise RuntimeError("当前不是已核验盘中数据，不能冻结为实时决策快照")
        clean = _snapshot_payload(payload)
        digest = _snapshot_hash(clean)
        decision = clean.get("decision_2026") or {}
        permission = decision.get("trading_permission") or {}
        opportunity = decision.get("opportunity_density") or {}
        evidence = _unique([
            f"交易许可：{permission.get('label') or '不可判定'}",
            f"机会密度：{opportunity.get('score') if opportunity.get('score') is not None else '--'}",
            f"市场状态：{(decision.get('market_regime') or {}).get('label') or '--'}",
        ])
        async with async_session() as session:
            existing = (await session.execute(select(DecisionWorkbenchSnapshot).where(
                DecisionWorkbenchSnapshot.decision_date == decision_date,
                DecisionWorkbenchSnapshot.phase == phase,
                DecisionWorkbenchSnapshot.contract_version == WORKBENCH_CONTRACT_VERSION,
            ))).scalar_one_or_none()
            if existing is not None:
                if user_judgment is not None:
                    existing.user_judgment = user_judgment.strip() or None
                    await session.commit()
                    await session.refresh(existing)
                return {**_snapshot_dict(existing, include_payload=True), "created": False, "immutable": True}
            row = DecisionWorkbenchSnapshot(
                decision_date=decision_date,
                source_data_date=decision_date,
                phase=phase,
                phase_label=SNAPSHOT_PHASES[phase],
                contract_version=WORKBENCH_CONTRACT_VERSION,
                snapshot_hash=digest,
                is_realtime=bool(meta.get("is_realtime")),
                payload=clean,
                evidence=evidence,
                user_judgment=(user_judgment or "").strip() or None,
                captured_at=datetime.utcnow(),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return {**_snapshot_dict(row, include_payload=True), "created": True, "immutable": True}

    async def validate(self, decision_date: date | None = None) -> dict:
        async with async_session() as session:
            if decision_date is None:
                decision_date = (await session.execute(
                    select(DecisionWorkbenchSnapshot.decision_date)
                    .order_by(desc(DecisionWorkbenchSnapshot.decision_date)).limit(1)
                )).scalar_one_or_none()
            if decision_date is None:
                return {"status": "PENDING", "message": "尚无可验证的决策快照", "errors": []}
            rows = list((await session.execute(select(DecisionWorkbenchSnapshot).where(
                DecisionWorkbenchSnapshot.decision_date == decision_date,
                DecisionWorkbenchSnapshot.contract_version == WORKBENCH_CONTRACT_VERSION,
            ).order_by(DecisionWorkbenchSnapshot.captured_at))).scalars().all())
            by_phase = {row.phase: row for row in rows}
            morning = by_phase.get("morning_1040")
            late = by_phase.get("close_review") or by_phase.get("tail_1455") or by_phase.get("tail_1440")
            if morning is None or late is None:
                return {
                    "status": "PENDING",
                    "message": "需要10:40冻结快照和14:55/盘后快照后才能归因",
                    "decision_date": decision_date.isoformat(),
                    "available_phases": list(by_phase),
                    "errors": [],
                }
            morning_decision = (morning.payload or {}).get("decision_2026") or {}
            late_decision = (late.payload or {}).get("decision_2026") or {}
            rank = {"BLOCK": 0, "OBSERVE": 1, "CAUTION": 2, "ALLOW": 3}
            morning_permission = str((morning_decision.get("trading_permission") or {}).get("code") or "BLOCK")
            late_permission = str((late_decision.get("trading_permission") or {}).get("code") or "BLOCK")
            errors: list[dict] = []
            if rank.get(morning_permission, 0) - rank.get(late_permission, 0) >= 2:
                errors.append({
                    "type": "市场判断错误",
                    "evidence": f"10:40许可{morning_permission}，尾盘/盘后降为{late_permission}",
                    "lesson": "提高宽度、资金衰减和高位负反馈在早盘判断中的权重。",
                })
            late_sectors = {str(item.get("name")): item for item in late_decision.get("sector_map") or []}
            for item in (morning_decision.get("sector_map") or [])[:3]:
                later = late_sectors.get(str(item.get("name")))
                if item.get("lifecycle") in {"启动", "扩散", "强化"} and later and later.get("lifecycle") == "退潮":
                    errors.append({
                        "type": "板块判断错误",
                        "evidence": f"{item.get('name')}由{item.get('lifecycle')}转为退潮",
                        "lesson": "不能只看龙头，需提高后排宽度与资金撤退证据权重。",
                    })
            late_candidates = {str(item.get("code")): item for item in late_decision.get("candidate_decisions") or []}
            for item in (morning_decision.get("candidate_decisions") or [])[:5]:
                alpha = _number((item.get("beta_alpha") or {}).get("individual_alpha_pct"))
                later = late_candidates.get(str(item.get("code")))
                later_alpha = _number(((later or {}).get("beta_alpha") or {}).get("individual_alpha_pct"))
                if alpha is not None and alpha >= 1.5 and later_alpha is not None and later_alpha <= 0:
                    errors.append({
                        "type": "Alpha判断错误",
                        "evidence": f"{item.get('name')} Alpha由{alpha:+.2f}%降至{later_alpha:+.2f}%",
                        "lesson": "增加Alpha持续时间和资金确认，单点超额不能视为持续Alpha。",
                    })
            outcome = "ERROR" if errors else "CONFIRMED"
            result = {
                "status": "COMPLETED",
                "outcome": outcome,
                "decision_date": decision_date.isoformat(),
                "morning_snapshot_id": morning.id,
                "late_snapshot_id": late.id,
                "errors": errors,
                "message": "发现需进入错误数据库的偏差" if errors else "未发现达到归因阈值的判断偏差",
            }
            morning.validation_status = outcome
            morning.validation_result = result
            morning.validated_at = datetime.utcnow()
            for error in errors:
                title = f"{decision_date.isoformat()} {error['type']}"
                existing_case = (await session.execute(select(ResearchMarketCase).where(
                    ResearchMarketCase.case_date == decision_date,
                    ResearchMarketCase.case_type == "decision_error",
                    ResearchMarketCase.title == title,
                ))).scalar_one_or_none()
                if existing_case is None:
                    session.add(ResearchMarketCase(
                        case_type="decision_error",
                        title=title,
                        summary=error["evidence"],
                        market_context={
                            "morning_snapshot_id": morning.id,
                            "late_snapshot_id": late.id,
                            "contract_version": WORKBENCH_CONTRACT_VERSION,
                        },
                        outcome="ERROR",
                        error_attribution=error["type"],
                        lesson=error["lesson"],
                        tags=["2026决策工作台", morning.phase, late.phase],
                        case_date=decision_date,
                    ))
            await session.commit()
        return result


decision_workbench_2026_service = DecisionWorkbench2026Service()
