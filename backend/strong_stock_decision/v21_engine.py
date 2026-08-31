"""Pure V2.1 bridge engines for the strong-stock decision system.

The module has no database or network side effects.  It converts point-in-time
market/sector/stock observations into auditable Shadow decisions.  Missing
inputs remain ``None``/``UNKNOWN``; they are never replaced with zero.
"""

from __future__ import annotations

import math
from statistics import mean
from typing import Any, Iterable


MARKET_SECTOR_BRIDGE_VERSION = "MARKET_SECTOR_BRIDGE_V1"
STRONG_STOCK_V21_VERSION = "STRONG_STOCK_DECISION_V2_1"
EVOLUTION_VERSION = "EVOLUTION_ENGINE_V0_SHADOW"

REGIMES = ("TREND_ATTACK", "ROTATION_RANGE", "DEFENSIVE_FADE", "TRANSITION")
LIFECYCLES = (
    "HIDDEN", "PREHEAT", "STARTING", "ACCELERATING", "CLIMAX",
    "FIRST_DIVERGENCE", "RETURNING", "SECOND_STRENGTH", "FADING", "INVALID",
)


def _num(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _date(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:10] or None


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _round(value: Any, digits: int = 2) -> float | None:
    value = _num(value)
    return round(value, digits) if value is not None else None


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def _mean(values: Iterable[Any]) -> float | None:
    values = [value for value in (_num(item) for item in values) if value is not None]
    return mean(values) if values else None


def _change(current: Any, previous: Any) -> float | None:
    current, previous = _num(current), _num(previous)
    if current is None or previous in (None, 0):
        return None
    return current - previous


def _arrow(change: float | None) -> str:
    if change is None:
        return "UNKNOWN"
    if change > 1e-9:
        return "↑"
    if change < -1e-9:
        return "↓"
    return "→"


def _breadth(row: Any) -> float | None:
    explicit = _num(_get(row, "breadth"))
    if explicit is not None:
        return explicit if explicit <= 1 else explicit / 100
    up, down = _num(_get(row, "up_count")), _num(_get(row, "down_count"))
    if up is None or down is None or up + down <= 0:
        return None
    return up / (up + down)


def _evidence(text: str, feature: str, value: Any = None) -> dict[str, Any]:
    item: dict[str, Any] = {"text": text, "feature": feature, "type": "ENGINE_FEATURE"}
    if value is not None:
        item["value"] = _round(value) if isinstance(value, (int, float)) else value
    return item


class MarketRegimeEngine:
    """Classify the market from breadth, trend, liquidity and participation."""

    def evaluate(self, market: dict[str, Any] | None, sectors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        market = market or {}
        sectors = sectors or []
        up, down = _num(market.get("up_count")), _num(market.get("down_count"))
        breadth = _num(market.get("breadth_ratio"))
        if breadth is None and up is not None and down is not None and up + down:
            breadth = up / (up + down)
        turnover_activity = _num(market.get("turnover_activity"))
        trend_5d = _num(market.get("index_trend_5d"))
        ma20 = market.get("index_above_ma20")
        failed_rate = _num(market.get("failed_limit_rate"))
        # MarketSentimentDaily stores this headline as a percentage (for
        # example 25.8), while the engine uses a fraction (0.258).
        if failed_rate is not None and failed_rate > 1:
            failed_rate /= 100
        limit_down = _num(market.get("limit_down_count"))
        top10_overlap = _num(market.get("top10_overlap_1d"))
        churn = _num(market.get("sector_churn"))
        core_strength = _num(market.get("core_strength"))
        values = [breadth, turnover_activity, trend_5d, failed_rate, limit_down, top10_overlap, churn, core_strength]
        available = sum(item is not None for item in values)
        evidence: list[dict[str, Any]] = []
        counter: list[dict[str, Any]] = []
        if breadth is not None:
            evidence.append(_evidence(f"市场宽度为{breadth * 100:.1f}%", "breadth_ratio", breadth))
        if turnover_activity is not None:
            evidence.append(_evidence(f"成交活跃度为20日均值的{turnover_activity:.2f}倍", "turnover_activity", turnover_activity))
        if trend_5d is not None:
            evidence.append(_evidence(f"指数5日趋势为{trend_5d:+.2f}%", "index_trend_5d", trend_5d))
        if failed_rate is not None and failed_rate > 0.25:
            counter.append(_evidence(f"炸板/失败率偏高：{failed_rate * 100:.1f}%", "failed_limit_rate", failed_rate))
        if limit_down is not None and limit_down > 40:
            counter.append(_evidence(f"跌停数量偏高：{limit_down:.0f}", "limit_down_count", limit_down))
        if ma20 is False:
            counter.append(_evidence("指数未站上MA20", "index_above_ma20", False))
        attack = 0.0
        if breadth is not None: attack += 25 if breadth >= .58 else 10 if breadth >= .5 else 0
        if turnover_activity is not None: attack += 25 if turnover_activity >= 1.05 else 12 if turnover_activity >= .9 else 0
        if trend_5d is not None: attack += 20 if trend_5d > 1 else 10 if trend_5d > 0 else 0
        if top10_overlap is not None: attack += 15 if top10_overlap >= .5 else 5
        if core_strength is not None: attack += 15 if core_strength >= 60 else 5 if core_strength >= 45 else 0
        risk = 0.0
        if breadth is not None: risk += 30 if breadth < .35 else 15 if breadth < .45 else 0
        if failed_rate is not None: risk += 25 if failed_rate >= .35 else 12 if failed_rate >= .25 else 0
        if limit_down is not None: risk += 25 if limit_down >= 80 else 12 if limit_down >= 40 else 0
        if trend_5d is not None: risk += 20 if trend_5d < -2 else 10 if trend_5d < 0 else 0
        if len(sectors) and sum(1 for row in sectors if (_num(row.get("pct_change")) or 0) < 0) / len(sectors) > .7:
            risk += 15
        risk = _clamp(risk)
        if available < 3:
            regime = "TRANSITION"
        elif risk >= 60:
            regime = "DEFENSIVE_FADE"
        elif attack >= 68 and (ma20 is not False):
            regime = "TREND_ATTACK"
        elif turnover_activity is not None and turnover_activity >= .9 and (churn is None or churn >= .25):
            regime = "ROTATION_RANGE"
        else:
            regime = "TRANSITION"
        confidence = _clamp(35 + available * 7 + abs(attack - risk) * .18)
        bias = {
            "regime": regime,
            "text": {"TREND_ATTACK": "进攻趋势市，优先确认主线与A区", "ROTATION_RANGE": "高活跃震荡轮动市，快进快出并等待确认", "DEFENSIVE_FADE": "防守退潮市，C区风险优先", "TRANSITION": "过渡/混沌市，降低结论强度"}[regime],
            "position": "正常观察" if regime == "TREND_ATTACK" else "精选试错" if regime == "ROTATION_RANGE" else "降低仓位" if regime == "TRANSITION" else "防守/观望",
        }
        return {
            "regime": regime, "confidence": round(confidence, 1), "evidence": evidence,
            "counter_evidence": counter, "strategy_bias": bias,
            "scores": {"attack": round(attack, 1), "risk": round(risk, 1)},
            "data_quality": {"status": "COMPLETE" if available >= 6 else "PARTIAL" if available >= 3 else "DATA_INCOMPLETE", "available_fields": available, "missing_fields": [name for name, value in {"breadth_ratio": breadth, "turnover_activity": turnover_activity, "index_trend_5d": trend_5d, "failed_limit_rate": failed_rate, "limit_down_count": limit_down, "index_above_ma20": ma20}.items() if value is None]},
            "engine_version": MARKET_SECTOR_BRIDGE_VERSION,
        }


class SectorTrajectoryEngine:
    """Create comparable 1/3/5/10/20-session sector trajectories."""

    def build(self, rows: Iterable[Any]) -> dict[str, Any]:
        keys = ("trade_date", "rank", "pct_change", "relative_return_vs_market", "turnover", "turnover_share", "main_force_net_inflow", "main_force_inflow_ratio", "fund_continuity", "breadth", "limit_up_count", "limit_up_linkage", "core_strength")
        normalized = [dict(row) if isinstance(row, dict) else {key: getattr(row, key, None) for key in keys} for row in rows]
        ordered = sorted(normalized, key=lambda item: _date(item.get("trade_date")) or "")
        latest = ordered[-1] if ordered else {}
        trajectory: list[dict[str, Any]] = []
        for window in (1, 3, 5, 10, 20):
            sample = ordered[-window:] if len(ordered) >= window else ordered
            first = sample[0] if sample else {}
            trajectory.append({
                "window": f"{window}D", "sample_count": len(sample),
                "rank": latest.get("rank"), "rank_change": _change(first.get("rank"), latest.get("rank")),
                "pct_change": _round(latest.get("pct_change")),
                "relative_return_vs_market": _round(latest.get("relative_return_vs_market")),
                "turnover_share": _round(latest.get("turnover_share"), 4),
                "main_force_inflow_ratio": _round(latest.get("main_force_inflow_ratio"), 6),
                "breadth": _round(latest.get("breadth"), 4),
            })
        rank_values = [item.get("rank") for item in ordered[-5:]]
        flow_values = [item.get("main_force_inflow_ratio") for item in ordered[-5:]]
        breadth_values = [item.get("breadth") for item in ordered[-5:]]
        rank_delta = _change(rank_values[0], rank_values[-1]) if len(rank_values) >= 2 else None
        flow_delta = _change(flow_values[-1], flow_values[0]) if len(flow_values) >= 2 else None
        breadth_delta = _change(breadth_values[-1], breadth_values[0]) if len(breadth_values) >= 2 else None
        return {
            "latest": latest, "windows": trajectory, "sample_count": len(ordered),
            "signals": {"funds": _arrow(flow_delta), "rank": _arrow(rank_delta), "breadth": "扩大" if breadth_delta is not None and breadth_delta > .03 else "收缩" if breadth_delta is not None and breadth_delta < -.03 else "稳定" if breadth_delta is not None else "UNKNOWN", "core": "增强" if _num(latest.get("core_strength")) is not None and _num(latest.get("core_strength")) >= 60 else "UNKNOWN", "turnover_share": _arrow(_change(latest.get("turnover_share"), ordered[-2].get("turnover_share") if len(ordered) > 1 else None))},
            "data_quality": {"status": "COMPLETE" if len(ordered) >= 5 else "PARTIAL" if ordered else "DATA_INCOMPLETE", "missing_fields": [key for key in ("main_force_inflow_ratio", "turnover_share", "breadth", "core_strength") if _num(latest.get(key)) is None]},
        }


class SectorLifecycleEngine:
    """Multi-session state machine with hysteresis and risk overrides."""

    def evaluate(self, history: Iterable[Any], previous_state: str | None = None) -> dict[str, Any]:
        rows = sorted(list(history), key=lambda row: _date(_get(row, "trade_date")) or "")
        if len(rows) < 3:
            return {"state": "INVALID", "previous_state": previous_state, "confidence": 0, "evidence": [_evidence("板块历史少于3个交易日，不能判断生命周期", "history_count", len(rows))], "counter_evidence": [], "transition_reason": {"reason": "INSUFFICIENT_HISTORY"}, "data_quality": {"status": "DATA_INCOMPLETE", "missing_fields": ["three_or_more_sessions"]}}
        latest = rows[-1]
        prior = rows[-3:-1]
        latest_breadth = _breadth(latest)
        prior_breadth = _mean(_breadth(row) for row in prior)
        latest_rank = _num(_get(latest, "rank"))
        prior_rank = _mean(_num(_get(row, "rank")) for row in prior)
        latest_flow = _num(_get(latest, "main_force_inflow_ratio"))
        prior_flow = _mean(_num(_get(row, "main_force_inflow_ratio")) for row in prior)
        latest_rel = _num(_get(latest, "relative_return_vs_market"))
        rank_improving = latest_rank is not None and prior_rank is not None and latest_rank < prior_rank
        flow_improving = latest_flow is not None and prior_flow is not None and latest_flow > prior_flow
        breadth_improving = latest_breadth is not None and prior_breadth is not None and latest_breadth > prior_breadth + .03
        flow_falling = latest_flow is not None and prior_flow is not None and latest_flow < prior_flow
        breadth_falling = latest_breadth is not None and prior_breadth is not None and latest_breadth < prior_breadth - .03
        rel_positive = latest_rel is not None and latest_rel > 0
        recent_positive = sum((_num(_get(row, "relative_return_vs_market")) or 0) > 0 for row in rows[-5:]) >= 3
        strong_prior = previous_state in {"STARTING", "ACCELERATING", "CLIMAX", "SECOND_STRENGTH"} or (prior_rank is not None and prior_rank <= 10 and recent_positive)
        high_participation = latest_breadth is not None and latest_breadth >= .7
        linked = _num(_get(latest, "limit_up_linkage"))
        abnormal = _num(_get(latest, "turnover_share"))
        evidence: list[dict[str, Any]] = []
        counter: list[dict[str, Any]] = []
        if rank_improving: evidence.append(_evidence("近两期排名改善", "rank", latest_rank))
        if flow_improving: evidence.append(_evidence("归一化主力流入占比改善", "main_force_inflow_ratio", latest_flow))
        if breadth_improving: evidence.append(_evidence("板块上涨宽度连续扩大", "breadth", latest_breadth))
        if strong_prior and (flow_falling or breadth_falling): counter.append(_evidence("前期强势后出现资金或宽度收缩", "divergence", True))
        if not recent_positive: counter.append(_evidence("近5期相对强弱未形成持续优势", "relative_strength", recent_positive))
        if strong_prior and flow_falling and breadth_falling:
            state = "FADING"
        elif strong_prior and (flow_falling or breadth_falling):
            state = "FIRST_DIVERGENCE"
        elif previous_state == "FIRST_DIVERGENCE" and (rank_improving or flow_improving) and breadth_improving:
            state = "SECOND_STRENGTH"
        elif previous_state == "FIRST_DIVERGENCE" and (rank_improving or flow_improving):
            state = "RETURNING"
        elif high_participation and linked is not None and linked >= .65 and abnormal is not None and abnormal >= 1.3:
            state = "CLIMAX"
        elif previous_state in {"STARTING", "PREHEAT"} and rank_improving and flow_improving and breadth_improving:
            state = "ACCELERATING"
        elif rank_improving and flow_improving and breadth_improving and rel_positive:
            state = "STARTING"
        elif rank_improving and (flow_improving or breadth_improving or rel_positive):
            state = "PREHEAT"
        elif previous_state in {"PREHEAT", "STARTING", "ACCELERATING"} and not recent_positive:
            state = "HIDDEN"
        else:
            state = previous_state or "HIDDEN"
        confidence = _clamp(40 + len(evidence) * 10 + len(counter) * 3)
        return {"state": state, "previous_state": previous_state, "confidence": round(confidence, 1), "evidence": evidence, "counter_evidence": counter, "transition_reason": {"from": previous_state, "to": state, "multi_session": True}, "data_quality": {"status": "COMPLETE" if len(rows) >= 5 else "PARTIAL", "sample_count": len(rows), "missing_fields": [key for key, value in {"rank": latest_rank, "main_force_inflow_ratio": latest_flow, "breadth": latest_breadth, "relative_return_vs_market": latest_rel}.items() if value is None]}}


class SectorMigrationEngine:
    """Infer relative strength rotation; never claims order-level money flow."""

    def infer(self, current: Iterable[dict[str, Any]], previous: Iterable[dict[str, Any]] | None = None, limit: int = 8) -> dict[str, Any]:
        current = list(current)
        previous_by_id = {str(row.get("sector_id")): row for row in (previous or [])}
        out = sorted([row for row in current if (_num(row.get("relative_return_vs_market")) or 0) < 0 or (_num(row.get("main_force_inflow_ratio")) or 0) < 0], key=lambda row: _num(row.get("rank")) or 9999)[:limit]
        into = sorted([row for row in current if (_num(row.get("relative_return_vs_market")) or 0) > 0 and (_num(row.get("main_force_inflow_ratio")) or 0) > 0], key=lambda row: _num(row.get("rank")) or 9999)[:limit]
        paths = []
        for source in out:
            for target in into[:3]:
                source_prev, target_prev = previous_by_id.get(str(source.get("sector_id")), {}), previous_by_id.get(str(target.get("sector_id")), {})
                source_change = _change(source.get("rank"), source_prev.get("rank"))
                target_change = _change(target.get("rank"), target_prev.get("rank"))
                confidence = _clamp(35 + (15 if source_change is not None and source_change > 0 else 0) + (15 if target_change is not None and target_change < 0 else 0) + (15 if _num(target.get("breadth")) is not None and _num(target.get("breadth")) >= .55 else 0))
                paths.append({"source": {"id": source.get("sector_id"), "name": source.get("sector_name")}, "target": {"id": target.get("sector_id"), "name": target.get("sector_name")}, "confidence": round(confidence, 1), "inference_type": "RELATIVE_STRENGTH_INFERENCE", "evidence": [_evidence("源板块相对强弱/资金占比走弱", "source_strength", source.get("relative_return_vs_market")), _evidence("目标板块相对强弱/资金占比走强", "target_strength", target.get("relative_return_vs_market"))], "counter_evidence": [_evidence("没有逐笔资金目的地数据，不能确认真实账户迁移", "data_scope", "aggregate_only")], "invalidation": ["目标板块宽度不再扩散", "归一化主力流入占比转弱"]})
        return {"paths": paths[:limit * 3], "label": "板块迁徙推断", "description": "行情源仅提供板块聚合数据，连线表示相对强弱变化的推断，不是真实资金账户迁移。", "data_quality": {"status": "COMPLETE" if current else "DATA_INCOMPLETE", "source": "sector_aggregate_flow"}}


class ZoneOpportunityFusionEngine:
    """Fuse sector lifecycle with the existing V2.0 A/B/C geometry."""

    def fuse(self, candidates: Iterable[dict[str, Any]], market_regime: str = "TRANSITION", lifecycle_by_sector: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        lifecycle_by_sector = lifecycle_by_sector or {}
        result = []
        for item in candidates:
            row = dict(item)
            zone = str(row.get("zone") or "UNKNOWN")
            stage = str(row.get("zone_stage") or "UNKNOWN")
            sector_id = str(row.get("sector_id") or "UNKNOWN")
            lifecycle = str(row.get("sector_lifecycle") or lifecycle_by_sector.get(sector_id, {}).get("state") or "INVALID")
            risk = str(row.get("risk_state") or "")
            main_force = str(row.get("main_force_state") or "")
            consensus = str(row.get("three_books_consensus") or "")
            is_c = "C_" in stage or "风险C区" in zone or any(term in risk for term in ("C_FORMED", "C_DEEPENING", "C_EXIT"))
            is_invalid = stage in {"A_INVALID", "B_INVALID", "C_EXIT"}
            severe_conflict = any(term in consensus for term in ("严重冲突", "强冲突"))
            main_force_weak = any(term in main_force for term in ("流出", "转弱", "减弱", "偏空"))
            is_a_discover = (lifecycle == "PREHEAT" and stage == "A_PREPARE") or (lifecycle == "STARTING" and stage == "A_FORMING")
            is_a_confirm = lifecycle in {"STARTING", "ACCELERATING"} and stage == "A_ACTIVE"
            is_b = (lifecycle == "RETURNING" and stage == "B_SMALL_A_FORMING") or (lifecycle == "SECOND_STRENGTH" and stage == "B_REATTACK")
            lifecycle_risk = lifecycle == "FADING" or (lifecycle == "CLIMAX" and stage == "A_LATE")
            if is_c or is_invalid or severe_conflict or (lifecycle == "FADING" and stage.startswith("A_")):
                pool, priority = "RISK_EXCLUDE", "EXCLUDE"
            elif is_a_confirm:
                pool = "A_CONFIRM"
                priority = "P1" if market_regime == "TREND_ATTACK" and not main_force_weak else "P2"
            elif is_a_discover:
                pool = "A_DISCOVERY"
                priority = "P1" if market_regime in {"TREND_ATTACK", "ROTATION_RANGE"} and not main_force_weak else "P2"
            elif is_b:
                pool = "B_REATTACK"
                priority = "P1" if market_regime != "DEFENSIVE_FADE" and not main_force_weak else "P2"
            else:
                pool, priority = "WATCH", "WATCH"
            if lifecycle_risk and priority in {"P1", "P2"}:
                priority = "WATCH"
            if market_regime == "DEFENSIVE_FADE" and priority == "P1":
                priority = "P2"
            evidence = list(row.get("evidence") or [])
            if lifecycle != "INVALID": evidence.append(f"板块生命周期：{lifecycle}")
            if zone != "UNKNOWN": evidence.append(f"V2.0交易区：{zone}/{stage}")
            missing = list(row.get("missing_confirmation") or [])
            if pool in {"A_DISCOVERY", "A_CONFIRM", "B_REATTACK"}:
                missing.extend(["后续价格与成交确认", "板块宽度和核心股跟随"])
            counter = list(row.get("counter_evidence") or [])
            if is_c: counter.append("风险C区优先级高于攻击信号")
            if main_force_weak: counter.append("主力状态转弱，候选优先级已下调")
            if severe_conflict: counter.append("三书出现严重冲突，强制进入风险淘汰")
            next_confirmation = list(dict.fromkeys(list(row.get("next_confirmation") or []) + missing))
            result.append({**row, "sector_lifecycle": lifecycle, "opportunity_pool": pool, "priority": priority, "opportunity_rank_score": _clamp(50 + (20 if priority == "P1" else 8 if priority == "P2" else 0) - (80 if pool == "RISK_EXCLUDE" else 0)), "evidence": evidence, "missing_confirmation": list(dict.fromkeys(missing)), "counter_evidence": counter, "next_confirmation": next_confirmation, "next_step": "等待确认条件出现" if next_confirmation else "继续观察结构变化", "invalidation": list(dict.fromkeys(list(row.get("invalidation") or []) + ["交易区进入C区", "板块生命周期转为FADING", "主力/量价证据连续转弱"]))})
        priority_order = {"P1": 0, "P2": 1, "WATCH": 2, "EXCLUDE": 3}
        return sorted(result, key=lambda row: (priority_order.get(row["priority"], 3), -float(row.get("opportunity_rank_score") or 0)))


class EvolutionEngine:
    """Generate proposal metadata only; promotion always requires approval."""

    MIN_TOTAL_SAMPLES = 200
    MIN_ENVIRONMENT_SAMPLES = 50

    def propose(self, outcomes: Iterable[dict[str, Any]], *, target_engine: str = STRONG_STOCK_V21_VERSION) -> dict[str, Any]:
        rows = list(outcomes)
        successes = sum(row.get("result_state") in {"SUCCESS", "PARTIAL_SUCCESS"} for row in rows)
        sample = len(rows)
        status = "WAITING_APPROVAL" if sample >= self.MIN_TOTAL_SAMPLES else "INSUFFICIENT_SAMPLE"
        return {"proposal_code": f"{target_engine}-{sample}", "target_engine": target_engine, "current_rule": {"layer": "ENGINE_FEATURE", "version": target_engine}, "proposed_rule": {}, "sample_size": sample, "old_metrics": {"success_rate": round(successes / sample * 100, 2) if sample else None}, "new_shadow_metrics": {}, "risk_notes": ["BOOK_RULE不可自动修改", "需要walk-forward、样本外和分市场环境验证"], "status": status, "version": EVOLUTION_VERSION}


class PostMarketDecisionOrchestrator:
    """Named facade used by integrations that expect the documented chain."""

    def __init__(self) -> None:
        self.market = MarketRegimeEngine()
        self.trajectory = SectorTrajectoryEngine()
        self.lifecycle = SectorLifecycleEngine()
        self.migration = SectorMigrationEngine()
        self.fusion = ZoneOpportunityFusionEngine()
        self.evolution = EvolutionEngine()
