"""Read-only authority envelope for cross-module decision explanations.

Wildman remains the primary interpretation layer.  This module only reads
already persisted snapshots; it never refreshes sources, scans candidates, or
changes any of the Wildman rule results.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from math import isfinite
from typing import Any

from database import async_session
from models import MarketDataCache
from services.market_decision_contract import (
    WORKBENCH_CACHE_KEY,
    WORKBENCH_CACHE_PREFIX,
    WORKBENCH_CONTRACT_VERSION,
)
from services.wildman_service import CACHE_KEY as WILDMAN_CACHE_KEY
from wildman.rules import RULE_VERSION


VERSION = "wildman-authority-v1"
WILDMAN_DEFAULT_CACHE_KEY = f"{WILDMAN_CACHE_KEY}:s1:g1"
POSITIVE_CYCLES = {"启动/试错", "发酵/主升", "高潮"}
HARD_CYCLES = {"退潮", "冰点"}
VALID_CYCLES = POSITIVE_CYCLES | HARD_CYCLES | {"待确认"}


def _date(value: Any) -> str | None:
    raw = str(value or "")[:10]
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _position_cap(position_range: Any) -> float | None:
    text = str(position_range or "")
    if not text:
        return None
    values: list[float] = []
    token = ""
    for char in text:
        if char.isdigit() or char == ".":
            token += char
        elif token:
            values.append(float(token))
            token = ""
    if token:
        values.append(float(token))
    return max(values) if values else None


def _unknown(decision_date: str | None, reasons: list[str], *, primary: dict | None = None, secondary: dict | None = None) -> dict:
    return {
        "version": VERSION,
        "decision_date": decision_date,
        "primary": primary or {"source": "wildman", "cycle": None, "cycle_node": None, "position_range": None, "trade_date": None, "updated_at": None, "source_data_date": None, "mainline_summary": []},
        "secondary": secondary or {"source": "v4", "status": "unavailable", "action": None, "max_position_pct": None, "trade_date": None, "updated_at": None, "reason": "V4同日快照不可用"},
        "effective": {"status": "unknown", "max_position_pct": None, "label": "待核验", "reasons": reasons},
        "conflicts": [],
        "data_quality": {"source_dates": {"wildman": None, "v4": None}, "comparable": False, "reasons": reasons},
    }


def _mainline_summary(payload: dict) -> list[dict]:
    rows = []
    for row in payload.get("mainlines") or []:
        if not isinstance(row, dict):
            continue
        rows.append({
            "theme_id": row.get("theme_id"),
            "theme_name": row.get("theme_name"),
            "state": row.get("state"),
            "confirmed": row.get("confirmed"),
            "sequential_count": row.get("sequential_count"),
            "next_step": row.get("next_step"),
        })
    return rows[:20]


def build_authority(
    wildman: dict | None,
    v4: dict | None,
    *,
    decision_date: str | None = None,
) -> dict:
    """Combine same-day persisted snapshots without inventing conclusions."""
    requested = _date(decision_date)
    wildman = wildman if isinstance(wildman, dict) else None
    v4 = v4 if isinstance(v4, dict) else None
    wildman_date = _date((wildman or {}).get("trade_date"))
    v4_meta = (v4 or {}).get("meta") or {}
    v4_date = _date(v4_meta.get("decision_date"))
    target = requested or wildman_date or v4_date
    primary_cycle = (wildman or {}).get("cycle") or {}
    cycle = primary_cycle.get("cycle") if isinstance(primary_cycle, dict) else None
    cycle_node = primary_cycle.get("cycle_node") if isinstance(primary_cycle, dict) else None
    position_range = primary_cycle.get("position_range") if isinstance(primary_cycle, dict) else None
    primary = {
        "source": "wildman", "cycle": cycle, "cycle_node": cycle_node,
        "position_range": position_range, "trade_date": wildman_date,
        "updated_at": (wildman or {}).get("updated_at"),
        "source_data_date": ((wildman or {}).get("source_status") or {}).get("data_date"),
        "mainline_summary": _mainline_summary(wildman or {}),
    }
    market_way = (v4 or {}).get("market_way_v4") or {}
    truth = market_way.get("truth") or {}
    cognition = (v4 or {}).get("market_cognition") or {}
    permission = ((v4 or {}).get("decision_2026") or {}).get("trading_permission") or {}
    action = cognition.get("final_action")
    truth_status = str(truth.get("status") or "")
    if truth_status == "FAIL":
        secondary_cap = 0.0
    elif truth_status == "LIMITED":
        raw_cap = _number(permission.get("max_total_position_pct"))
        secondary_cap = min(raw_cap, 25.0) if raw_cap is not None else None
    else:
        secondary_cap = _number(permission.get("max_total_position_pct"))
    v4_valid = bool(v4 and v4.get("available") is True and v4_meta.get("contract_version") == WORKBENCH_CONTRACT_VERSION)
    action_valid = action in {"execute", "caution", "observe", "no_trade"}
    truth_valid = truth_status in {"PASS", "LIMITED", "FAIL"}
    v4_comparable = v4_valid and action_valid and truth_valid
    wildman_valid = bool(wildman and wildman.get("rule_version") == RULE_VERSION)
    comparable = bool(wildman_valid and v4_comparable and wildman_date and v4_date and wildman_date == v4_date and (not target or target == wildman_date))
    if not truth_valid:
        secondary_status = "unavailable"
        secondary_reason = "V4真值状态缺失或未知"
    elif not action_valid:
        secondary_status = "unavailable"
        secondary_reason = "V4最终行动缺失或无效"
    elif not comparable and v4_valid:
        secondary_status = "stale"
        secondary_reason = "V4快照存在，但与野人哥不是同日可比意见"
    elif v4_valid and truth_status == "FAIL":
        secondary_status = "stale"
        secondary_reason = "V4真值阻断"
    elif v4_valid and truth_status == "LIMITED":
        secondary_status = "disagreement"
        secondary_reason = "V4真值降级，参考上限已收紧"
    elif v4_valid:
        secondary_status = "aligned"
        secondary_reason = "V4同日快照可比"
    else:
        secondary_status = "unavailable"
        secondary_reason = "V4同日快照不可用"
    secondary = {
        "source": "v4", "status": secondary_status, "action": action,
        "max_position_pct": secondary_cap, "trade_date": v4_date,
        "updated_at": v4_meta.get("updated_at"),
        "reason": secondary_reason,
    }
    if not truth_valid or not action_valid:
        secondary["max_position_pct"] = None

    reasons: list[str] = []
    conflicts: list[dict] = []
    if not wildman:
        reasons.append("野人哥同日快照缺失")
    if not v4:
        reasons.append("V4同日快照缺失")
    if wildman and (wildman.get("rule_version") != RULE_VERSION):
        reasons.append("野人哥规则版本不匹配")
    if requested and (wildman_date != requested or v4_date != requested):
        reasons.append("请求日期与缓存日期不一致")
    if wildman and v4 and wildman_date != v4_date:
        reasons.append("野人哥与V4不是同一决策日，不直接比较")
    if v4 and v4_meta.get("contract_version") != WORKBENCH_CONTRACT_VERSION:
        reasons.append("V4契约版本不匹配")
    if not truth_valid:
        reasons.append("V4真值状态缺失或未知")
    if not action_valid:
        reasons.append("V4最终行动缺失或无效")
    if v4 and truth_status in {"FAIL", "LIMITED"}:
        reasons.append("V4真值层" + ("阻断" if truth_status == "FAIL" else "降级"))
    if comparable and cycle in POSITIVE_CYCLES and action in {"observe", "no_trade"}:
        conflicts.append({"scope": "market_action", "reason": "野人哥周期积极但V4不交易，风险优先暂缓积极建议", "primary_value": cycle, "secondary_value": action})
    if comparable and cycle and action and action not in {"no_trade", "observe"} and cycle in HARD_CYCLES:
        conflicts.append({"scope": "cycle", "reason": "野人哥周期风险优先，不能被V4看多升级", "primary_value": cycle, "secondary_value": action})
    if conflicts:
        secondary["status"] = "disagreement"
        secondary["reason"] = "V4与野人哥主周期/行动存在意见分歧"
    if not comparable:
        return _unknown(target, reasons or ["两个来源没有可比的同日快照"], primary=primary, secondary=secondary)

    primary_cap = _position_cap(position_range)
    primary_valid = cycle in VALID_CYCLES and primary_cap is not None
    if not primary_valid:
        reasons.append("野人哥周期或原始仓位区间缺失/非标准，不能生成统一上限")
        return {
            "version": VERSION, "decision_date": target, "primary": primary,
            "secondary": secondary,
            "effective": {"status": "unknown", "max_position_pct": None, "label": "待核验", "reasons": reasons},
            "conflicts": conflicts,
            "data_quality": {"source_dates": {"wildman": wildman_date, "v4": v4_date}, "comparable": comparable, "reasons": reasons},
        }
    effective_cap = primary_cap
    if secondary_cap is not None:
        effective_cap = secondary_cap if effective_cap is None else min(primary_cap, secondary_cap)
    if cycle in HARD_CYCLES or cycle in {None, "待确认"}:
        status, label = ("unknown", "待核验") if cycle in {None, "待确认"} else ("observe", "风险优先，观察/空仓")
        if cycle in {None, "待确认"}:
            # A bullish secondary opinion cannot manufacture a Wildman cap
            # when the primary cycle itself is unknown.
            effective_cap = None
        if cycle in HARD_CYCLES:
            effective_cap = min(effective_cap, 5.0) if effective_cap is not None else 0.0
            reasons.append(f"野人哥周期为{cycle}，不被V4看多升级")
    elif truth_status == "FAIL" or action in {"observe", "no_trade"}:
        status, label = "caution", "观点分歧，暂缓积极建议"
        reasons.append("V4不交易或真值阻断，保留野人哥主观点供人工判断")
    elif truth_status == "LIMITED":
        status, label = "caution", "真值降级，谨慎研究"
    else:
        status, label = "research", "野人哥主观点，V4补充"
    if effective_cap is not None and primary_cap is not None and effective_cap < primary_cap:
        reasons.append(f"V4有效上限{effective_cap:g}%低于野人哥周期上限{primary_cap:g}%")
    elif effective_cap is not None and primary_cap is not None and effective_cap == primary_cap:
        if secondary_cap is None:
            reasons.append(f"V4上限不可用，按野人哥主周期收紧至{primary_cap:g}%")
        elif primary_cap <= secondary_cap:
            reasons.append(f"按野人哥主周期收紧至{primary_cap:g}%")
    return {
        "version": VERSION, "decision_date": target, "primary": primary,
        "secondary": secondary,
        "effective": {"status": status, "max_position_pct": effective_cap, "label": label, "reasons": reasons},
        "conflicts": conflicts,
        "data_quality": {"source_dates": {"wildman": wildman_date, "v4": v4_date}, "comparable": True, "reasons": reasons},
    }


async def read_authority(decision_date: str | None = None) -> dict:
    """Read only the two persisted cache rows; no refresh or network access."""
    async with async_session() as session:
        wildman_row = await session.get(MarketDataCache, WILDMAN_DEFAULT_CACHE_KEY)
        target = _date(decision_date) or _date((wildman_row.payload if wildman_row else {}).get("trade_date"))
        v4_row = await session.get(MarketDataCache, f"{WORKBENCH_CACHE_PREFIX}{target}") if target else await session.get(MarketDataCache, WORKBENCH_CACHE_KEY)
    return build_authority(
        dict(wildman_row.payload) if wildman_row and isinstance(wildman_row.payload, dict) else None,
        dict(v4_row.payload) if v4_row and isinstance(v4_row.payload, dict) else None,
        decision_date=target,
    )
