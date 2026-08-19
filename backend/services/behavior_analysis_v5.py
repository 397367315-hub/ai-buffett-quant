"""Observable market-behavior and psychology layer for V5.

It describes conditions that can induce participant behavior.  It does not
infer an unobservable actor's intent and never labels a move as manipulation.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from sqlalchemy import desc, select

from database import async_session
from models import BehaviorSnapshotV5
from services.factor_registry_v5 import factor_definition


def _num(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _avg(values: list[float | None]) -> float | None:
    values = [value for value in values if value is not None and math.isfinite(value)]
    return sum(values) / len(values) if values else None


def _state(score: float | None, inverse: bool = False) -> str:
    if score is None:
        return "数据不足"
    if inverse:
        score = 100 - score
    if score >= 75:
        return "极高"
    if score >= 55:
        return "高"
    if score >= 30:
        return "中"
    return "低"


def _psychology(fomo: float | None, panic: float | None, imbalance: float | None, previous: str | None) -> tuple[str, str]:
    if panic is not None and panic >= 75:
        current = "恐慌"
    elif fomo is not None and fomo >= 82:
        current = "亢奋"
    elif fomo is not None and fomo >= 62:
        current = "追逐"
    elif fomo is not None and fomo >= 45:
        current = "相信"
    elif panic is not None and panic >= 55:
        current = "怀疑"
    elif imbalance is not None and imbalance < 24:
        current = "冷漠"
    else:
        current = "试探"
    return current, f"{previous} → {current}" if previous and previous != current else "状态暂未迁移"


class BehaviorAnalysisV5Service:
    async def _previous(self, target: date | None) -> BehaviorSnapshotV5 | None:
        if target is None:
            return None
        try:
            async with async_session() as session:
                return (await session.execute(select(BehaviorSnapshotV5).where(
                    BehaviorSnapshotV5.behavior_date < target,
                ).order_by(desc(BehaviorSnapshotV5.behavior_date), desc(BehaviorSnapshotV5.generated_at)).limit(1))).scalar_one_or_none()
        except Exception:
            return None

    @staticmethod
    def _factor_map(factors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {str(item.get("id")): item for item in factors}

    @staticmethod
    def _value(factor_map: dict[str, dict[str, Any]], key: str) -> float | None:
        return _num((factor_map.get(key) or {}).get("value"))

    @staticmethod
    def _observed_score(components: list[tuple[float | None, float]]) -> tuple[float | None, float]:
        observed = [(value, weight) for value, weight in components if value is not None]
        if not observed:
            return None, 0.0
        weight = sum(item[1] for item in observed)
        return sum(item[0] * item[1] for item in observed) / weight * 100, weight / sum(item[1] for item in components) * 100

    @staticmethod
    def _behavior_factor(factor_id: str, score: float | None, previous_score: float | None, now: datetime, data_date: str | None, reason: str | None = None) -> dict[str, Any]:
        definition = factor_definition(factor_id) or {"id": factor_id, "name": factor_id, "layer": "propagation", "source": "behavior_layer", "source_level": "A", "ttl_minutes": 60, "lead_score": 0.6, "chains": ["market_structure_transition"]}
        signal = _clamp(-(score - 50) / 50) if score is not None else None
        delta = score - previous_score if score is not None and previous_score is not None else None
        if score is None:
            state = "unavailable"
        elif score >= 70:
            state = "risk_rising"
        elif score <= 30:
            state = "contained"
        else:
            state = "watch"
        return {
            "id": factor_id, "name": definition["name"], "layer": definition["layer"], "state": state,
            "value": round(score, 2) if score is not None else None,
            "zscore": None, "percentile": None, "delta": round(delta, 2) if delta is not None else None,
            "acceleration": None, "freshness": 1.0 if score is not None else None, "reliability": 0.92 if score is not None else 0.0,
            "lead_score": definition["lead_score"], "causal_chain_ids": list(definition.get("chains") or []),
            "source": definition["source"], "source_level": definition["source_level"], "event_time": now.isoformat() if score is not None else None,
            "publish_time": now.isoformat() if score is not None else None, "available_time": now.isoformat() if score is not None else None,
            "ingested_at": now.isoformat(), "updated_at": now.isoformat() if score is not None else None, "ttl_minutes": definition["ttl_minutes"],
            "data_date": data_date, "quality_score": 92.0 if score is not None else 0.0, "observed": score is not None,
            "missing_reason": reason if score is None else None, "signal": signal, "direction_score": signal,
        }

    async def evaluate(self, workbench: dict[str, Any], factors: list[dict[str, Any]], sectors: list[dict[str, Any]], now: datetime, target: date | None) -> dict[str, Any]:
        factor_map = self._factor_map(factors)
        headline = workbench.get("headline_metrics") or {}
        state = workbench.get("market_state") or {}
        structure = workbench.get("structure_health") or {}
        crowding = workbench.get("crowding_risk") or {}
        candidates = [item for item in workbench.get("candidates") or [] if not item.get("stale")]
        breadth = self._value(factor_map, "market_breadth")
        amount_ratio = self._value(factor_map, "market_amount_vs_ma20")
        failed = self._value(factor_map, "failed_limit_rate")
        crowding_value = self._value(factor_map, "crowding_risk")
        structure_value = self._value(factor_map, "structure_health")
        sector_breadth = self._value(factor_map, "sector_breadth")
        persistence = self._value(factor_map, "sector_flow_persistence")
        market_score = self._value(factor_map, "market_state_score")
        limit_down = _num(headline.get("limit_down"))
        limit_up = _num(headline.get("limit_up"))
        limit_pressure = limit_down / (limit_up + limit_down) if limit_down is not None and limit_up is not None and limit_down + limit_up else None
        price_acceleration = _clamp((market_score - 55) / 35) if market_score is not None else None
        flow_acceleration = _clamp((amount_ratio or 0) / 25) if amount_ratio is not None else None
        crowding_component = _clamp((crowding_value - 45) / 50) if crowding_value is not None else None
        breadth_up = _clamp((breadth - 50) / 45) if breadth is not None else None
        fomo, fomo_coverage = self._observed_score([(price_acceleration, 0.25), (flow_acceleration, 0.2), (breadth_up, 0.2), (crowding_component, 0.2), (_clamp(len(candidates) / 12), 0.15) if candidates else (None, 0.15)])
        panic_breadth = _clamp((50 - breadth) / 50) if breadth is not None else None
        panic_volume = _clamp((amount_ratio or 0) / 30) if amount_ratio is not None else None
        panic_limit = _clamp(limit_pressure) if limit_pressure is not None else None
        panic_structure = _clamp((55 - structure_value) / 55) if structure_value is not None else None
        panic, panic_coverage = self._observed_score([(panic_breadth, 0.30), (panic_volume, 0.15), (panic_limit, 0.25), (panic_structure, 0.20), (crowding_component, 0.10)])
        false_breakout_components = [
            _clamp((market_score - 60) / 30) if market_score is not None else None,
            _clamp((50 - (sector_breadth or 50)) / 50) if sector_breadth is not None else None,
            _clamp((50 - (persistence or 50)) / 50) if persistence is not None else None,
            _clamp((60 - (structure_value or 60)) / 60) if structure_value is not None else None,
        ]
        false_breakout, false_coverage = self._observed_score([(value, 0.3 if index == 0 else 0.233) for index, value in enumerate(false_breakout_components)])
        consensus, consensus_coverage = self._observed_score([(crowding_component, 0.35), (price_acceleration, 0.20), (flow_acceleration, 0.20), (_clamp((breadth - 50) / 50) if breadth is not None else None, 0.25)])
        imbalance_components = [
            (_clamp(abs((breadth - 50) / 50)) if breadth is not None else None, 0.18),
            (_clamp((failed or 0) / 35) if failed is not None else None, 0.14),
            (crowding_component, 0.16),
            (_clamp(abs(amount_ratio or 0) / 30) if amount_ratio is not None else None, 0.12),
            (_clamp(abs((sector_breadth - 50) / 50)) if sector_breadth is not None else None, 0.12),
            (_clamp(abs((structure_value - 50) / 50)) if structure_value is not None else None, 0.16),
            (_clamp(abs((limit_pressure or 0.5) - 0.5) * 2) if limit_pressure is not None else None, 0.12),
        ]
        imbalance, imbalance_coverage = self._observed_score(imbalance_components)
        previous = await self._previous(target)
        previous_payload = previous.payload if previous and isinstance(previous.payload, dict) else {}
        previous_scores = previous_payload.get("scores") or {}
        psychology, transition = _psychology(fomo, panic, imbalance, previous.market_psychology_state if previous else None)
        price_support = market_score is not None
        capital_support = persistence is not None and amount_ratio is not None
        sector_support = sector_breadth is not None
        alpha_support = bool(candidates)
        fundamental_support = bool((workbench.get("market_way_v4") or {}).get("national_direction_radar"))
        if price_support and capital_support and sector_support and alpha_support and fundamental_support:
            structure_quality = "真实势形成"
        elif price_support or capital_support:
            structure_quality = "疑似短期行为驱动"
        else:
            structure_quality = "证据不足"
        scores = {"fomo": fomo, "panic": panic, "false_breakout": false_breakout, "consensus": consensus, "imbalance": imbalance}
        signals = [
            {"id": "fomo", "label": "疑似追涨行为形成", "state": _state(fomo), "score": round(fomo, 1) if fomo is not None else None, "coverage_pct": round(fomo_coverage, 1), "evidence": ["价格/成交/市场宽度共同改善" if price_acceleration is not None and flow_acceleration is not None else "价格与成交证据不完整", "高位一致性或候选密度正在变化"], "rule": "价格加速、成交放大、宽度/候选扩散和拥挤共同观察；缺少社交热度时不补造"},
            {"id": "panic", "label": "恐慌行为扩散", "state": _state(panic), "score": round(panic, 1) if panic is not None else None, "coverage_pct": round(panic_coverage, 1), "evidence": ["市场宽度/跌停压力/结构健康度联合观察"], "rule": "不能把单日下跌直接称为踩踏，需看到宽度、跌停、成交和高位反馈共同恶化"},
            {"id": "false_breakout", "label": "假突破风险", "state": _state(false_breakout), "score": round(false_breakout, 1) if false_breakout is not None else None, "coverage_pct": round(false_coverage, 1), "evidence": ["价格强度与板块宽度/资金持续性对比"], "rule": "价格强于底层势时标记风险，不推断任何参与者意图"},
            {"id": "high_consensus", "label": "高位一致性风险", "state": _state(consensus), "score": round(consensus, 1) if consensus is not None else None, "coverage_pct": round(consensus_coverage, 1), "evidence": ["拥挤、价格强度和成交异常联合观察"], "rule": "强势仍在但边际风险可能快速累积"},
            {"id": "panic_excess", "label": "恐慌过度观察", "state": "观察" if breadth is not None and breadth < 15 and (limit_down or 0) > (limit_up or 0) else "未触发", "score": round(panic, 1) if panic is not None else None, "coverage_pct": round(panic_coverage, 1), "evidence": ["极端宽度与跌停扩散需要持续确认"], "rule": "只进入观察阶段，不直接判断底部"},
        ]
        behavior_factors = [
            self._behavior_factor("fomo_behavior", fomo, _num(previous_scores.get("fomo")), now, target.isoformat() if target else None, "缺少可核验社交热度" if fomo_coverage < 70 else None),
            self._behavior_factor("panic_behavior", panic, _num(previous_scores.get("panic")), now, target.isoformat() if target else None),
            self._behavior_factor("false_breakout_risk", false_breakout, _num(previous_scores.get("false_breakout")), now, target.isoformat() if target else None),
            self._behavior_factor("behavior_imbalance", imbalance, _num(previous_scores.get("imbalance")), now, target.isoformat() if target else None),
        ]
        payload = {
            "market_psychology_state": psychology, "psychology_transition": transition, "behavior_imbalance_score": round(imbalance, 1) if imbalance is not None else None,
            "behavior_imbalance_level": _state(imbalance), "crowding_state": _state(consensus), "panic_state": _state(panic), "fomo_state": _state(fomo), "false_breakout_risk": _state(false_breakout),
            "bias_signals": signals, "factors": behavior_factors, "scores": scores,
            "structure_quality": structure_quality,
            "induction_validation": {"price": price_support, "capital": capital_support, "sector_breadth": sector_support, "alpha": alpha_support, "industry_or_event": fundamental_support, "rule": "价格→资金→板块宽度→核心/中军→Alpha→产业/事件；未全部确认则不升级为真实势"},
            "missing_inputs": ["社交/媒体热度" if fomo_coverage < 70 else None, "个股相关性/波动率" if structure_value is None else None, "核心/中军分层" if not sectors else None],
            "data_cutoff_time": now.isoformat(),
            "method": "可观测价格、成交、宽度、涨跌停、拥挤、板块和Alpha代理；不判断不可验证的主观操控意图。",
        }
        payload["missing_inputs"] = [item for item in payload["missing_inputs"] if item]
        if target:
            try:
                async with async_session() as session:
                    row = (await session.execute(select(BehaviorSnapshotV5).where(
                        BehaviorSnapshotV5.behavior_date == target,
                        BehaviorSnapshotV5.phase == str(workbench.get("market_way_v4", {}).get("phase") or "current"),
                    ))).scalar_one_or_none()
                    values = {
                        "market_psychology_state": psychology, "psychology_transition": transition, "behavior_imbalance": payload["behavior_imbalance_score"],
                        "crowding_state": payload["crowding_state"], "panic_state": payload["panic_state"], "fomo_state": payload["fomo_state"], "false_breakout_risk": payload["false_breakout_risk"],
                        "payload": payload, "data_cutoff_time": now, "generated_at": now,
                    }
                    if row is None:
                        session.add(BehaviorSnapshotV5(behavior_date=target, phase=str(workbench.get("market_way_v4", {}).get("phase") or "current"), **values))
                    else:
                        for key, value in values.items():
                            setattr(row, key, value)
                    await session.commit()
            except Exception as exc:
                print(f"V5 behavior persistence failed: {type(exc).__name__}")
        return payload


behavior_analysis_v5_service = BehaviorAnalysisV5Service()
