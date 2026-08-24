"""Pure Shadow detectors for the ROCI V1.1.2 intraday skills.

These detectors only label observable structure.  They deliberately return
``UNKNOWN`` when a required series is absent and never feed an ACTION or a
formal weekly probability.
"""

from __future__ import annotations

from typing import Any


SKILL_META: tuple[tuple[str, str, str], ...] = (
    ("ROCI-S090", "竞价偏差识别", "竞价实际相对盘前预期和周度剧本"),
    ("ROCI-S091", "开盘15分钟资金方向", "开盘阶段成交结构和承接"),
    ("ROCI-S092", "盘中广度变化", "上涨占比、中位数和指数背离"),
    ("ROCI-S093", "盘中领导力", "核心、跟随和板块宽度"),
    ("ROCI-S094", "盘中承接与抛压", "跌速、量能和低点回收"),
    ("ROCI-S095", "盘中搬家识别", "来源战场、目的战场和迁移持续性"),
    ("ROCI-S096", "盘中剧本验证", "盘中事实对周度剧本的支持或反对"),
    ("ROCI-S097", "盘中异常转折", "市场状态、领导力和成交性质切换"),
)


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _observed(value: Any) -> bool:
    return value is not None and value != "UNKNOWN" and value != "INSUFFICIENT_DATA"


def _fact(label: str, value: Any, source: str, *, supports: bool | None = None) -> dict[str, Any]:
    item = {"type": "FACT", "label": label, "value": value, "source": source}
    if supports is not None:
        item["supports"] = supports
    return item


def _base(skill_id: str, name: str, state: Any, *, score: float | None, confidence: float | None, reasons: list[dict[str, Any]], missing: list[str] | None = None) -> dict[str, Any]:
    observed = _observed(state)
    return {
        "skill_id": skill_id,
        "name": name,
        "status": "SHADOW",
        "triggered": bool(observed and score is not None),
        "score": round(score, 1) if score is not None else None,
        "confidence": round(confidence, 1) if confidence is not None else None,
        "evidence": reasons,
        "state": state if observed else "UNKNOWN",
        "availability": {"available": bool(observed), "missing": missing or [], "reason": "盘中结构可观测" if observed else "关键盘中输入缺失"},
        "shadow_excluded_from_action": True,
        "note": "Shadow 观察标签，不参与正式 ACTION 或周度概率。",
    }


def _state_transition(current: dict[str, Any], previous: dict[str, Any]) -> list[dict[str, Any]]:
    changes = []
    old_states = previous.get("states") or {}
    for key in ("market_state", "breadth_state", "volume_state", "leadership_state", "migration_state", "data_status"):
        old = old_states.get(key)
        new = (current.get("states") or {}).get(key)
        if old and new and old != new:
            changes.append({"field": key, "from": old, "to": new})
    return changes


def build_shadow_skill_outputs(payload: dict[str, Any], previous: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return S090-S097 labels from one intraday snapshot and its predecessor."""
    previous = previous or {}
    states = payload.get("states") or {}
    breadth = payload.get("breadth") or {}
    turnover = payload.get("turnover") or {}
    indexes = payload.get("indexes") or {}
    migration = payload.get("migration") or {}
    scenario = payload.get("scenario_validation") or {}
    data_status = str(payload.get("data_status") or "UNKNOWN")
    data_ok = data_status in {"REALTIME", "PARTIAL_REALTIME", "CACHED"}
    changes = _state_transition(payload, previous)
    output: list[dict[str, Any]] = []

    # S090: no auction feed is currently exposed by every provider.  Preserve
    # UNKNOWN instead of inferring a gap from the index opening price.
    auction = payload.get("auction")
    auction_state = (auction or {}).get("state") if isinstance(auction, dict) else None
    output.append(_base(
        "ROCI-S090", "竞价偏差识别", auction_state,
        score=_num((auction or {}).get("score")) if isinstance(auction, dict) else None,
        confidence=65 if _observed(auction_state) else None,
        reasons=[_fact("竞价数据", auction_state or "UNKNOWN", "auction_feed", supports=_observed(auction_state))],
        missing=[] if _observed(auction_state) else ["auction", "weekly_scenario"],
    ))

    opening_state = (payload.get("opening") or {}).get("state") if isinstance(payload.get("opening"), dict) else None
    output.append(_base(
        "ROCI-S091", "开盘15分钟资金方向", opening_state,
        score=58 if _observed(opening_state) else None,
        confidence=55 if _observed(opening_state) else None,
        reasons=[_fact("开盘阶段状态", opening_state or "UNKNOWN", "roci_intraday_snapshots", supports=_observed(opening_state)), _fact("数据状态", data_status, "roci_intraday_snapshots")],
        missing=[] if _observed(opening_state) else ["minute_bars", "opening_structure"],
    ))

    breadth_state = states.get("breadth_state")
    breadth_score = _num(payload.get("breadth_ratio"))
    output.append(_base(
        "ROCI-S092", "盘中广度变化", breadth_state,
        score=breadth_score if breadth_score is not None else (60 if _observed(breadth_state) else None),
        confidence=72 if breadth_score is not None else 55 if _observed(breadth_state) else None,
        reasons=[_fact("广度状态", breadth_state or "UNKNOWN", "market_sentiment_daily", supports=_observed(breadth_state)), _fact("上涨占比", breadth_score if breadth_score is not None else "UNKNOWN", "market_sentiment_daily")],
        missing=[] if _observed(breadth_state) else ["breadth", "median_return_velocity"],
    ))

    leadership_state = states.get("leadership_state")
    leadership = payload.get("leadership") or []
    output.append(_base(
        "ROCI-S093", "盘中领导力", leadership_state,
        score=70 if leadership else None,
        confidence=68 if leadership else None,
        reasons=[_fact("领导力状态", leadership_state or "UNKNOWN", "market_decision_workbench.main_lines", supports=bool(leadership)), _fact("候选方向数", len(leadership), "market_decision_workbench.main_lines")],
        missing=[] if leadership else ["sector_leadership", "sector_breadth"],
    ))

    volume_state = states.get("volume_state")
    absorption_state = payload.get("absorption_state")
    output.append(_base(
        "ROCI-S094", "盘中承接与抛压", absorption_state,
        score=65 if _observed(absorption_state) else None,
        confidence=52 if _observed(absorption_state) else None,
        reasons=[_fact("成交性质", volume_state or "UNKNOWN", "roci_intraday_snapshots"), _fact("承接/抛压状态", absorption_state or "UNKNOWN", "roci_intraday_snapshots", supports=_observed(absorption_state))],
        missing=[] if _observed(absorption_state) else ["minute_bars", "equal_weight", "low_recovery"],
    ))

    migration_state = migration.get("state")
    source = migration.get("source_sectors") or []
    destination = migration.get("destination_sectors") or []
    output.append(_base(
        "ROCI-S095", "盘中搬家识别", migration_state,
        score=_num(migration.get("intensity")),
        confidence=60 if source and destination else None,
        reasons=[_fact("来源战场", source or "UNKNOWN", "market_decision_workbench.main_lines"), _fact("目的战场", destination or "UNKNOWN", "market_decision_workbench.main_lines"), _fact("迁移持续性", migration.get("persistence") or "UNKNOWN", "roci_intraday_snapshots")],
        missing=[] if source and destination else ["sector_history", "fund_flow"],
    ))

    scenario_state = scenario.get("state") or states.get("scenario_validation_state")
    output.append(_base(
        "ROCI-S096", "盘中剧本验证", scenario_state,
        score=60 if _observed(scenario_state) else None,
        confidence=60 if _observed(scenario_state) else None,
        reasons=[_fact("剧本验证状态", scenario_state or "UNKNOWN", "roci_intraday_snapshots"), _fact("盘中建议", scenario.get("intraday_probability_suggestion") or "UNKNOWN", "roci_intraday_snapshots")],
        missing=[] if _observed(scenario_state) else ["weekly_scenario", "validation"],
    ))

    reversal = "UNKNOWN"
    if changes:
        fields = {item["field"] for item in changes}
        if "breadth_state" in fields and "market_state" in fields:
            reversal = "INDEX_BREADTH_REVERSAL"
        elif "leadership_state" in fields:
            reversal = "LEADER_FAILURE"
        elif "volume_state" in fields:
            reversal = "VOLUME_DRYUP" if states.get("volume_state") == "VOLUME_DRYUP" else "LATE_DAY_RISK_OFF"
    output.append(_base(
        "ROCI-S097", "盘中异常转折", reversal,
        score=75 if reversal != "UNKNOWN" else None,
        confidence=62 if reversal != "UNKNOWN" else None,
        reasons=[_fact("状态变化数", len(changes), "roci_intraday_snapshots", supports=bool(changes)), _fact("变化字段", [item["field"] for item in changes] or "UNKNOWN", "roci_intraday_snapshots")],
        missing=[] if changes else ["state_history", "intraday_evidence"],
    ))
    return output
