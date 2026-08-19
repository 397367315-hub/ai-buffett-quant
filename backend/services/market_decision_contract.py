"""Shared contract and execution-gate rules for market decisions."""

from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any


WORKBENCH_CACHE_KEY = "market_decision_workbench_latest_v4_0_0"
WORKBENCH_CACHE_PREFIX = "market_decision_workbench_v4_0_0:"
WORKBENCH_CONTRACT_VERSION = "market-workbench-v4.0.0"
SCORE_VERSION = "market-state-v2.0.0"
CANDIDATE_SCORE_VERSION = "workbench-candidate-v2.0.0"
FINAL_ACTIONS = ("execute", "caution", "observe", "no_trade")
MARKET_EXECUTION_GATE_VERSION = "market-execution-gate-v1.0.0"


def adaptive_strategy_key(strategy_id: str, requires_auction_confirmation: bool) -> str:
    if requires_auction_confirmation or "auction" in str(strategy_id).lower():
        return "auction_confirmation"
    return "tail_1455"


def evaluate_market_execution_gate(
    payload: dict[str, Any] | None,
    *,
    decision_date: date,
    strategy_id: str,
    requires_auction_confirmation: bool,
) -> dict[str, Any]:
    """Fail closed unless a same-day, current-contract decision permits simulation."""
    strategy_key = adaptive_strategy_key(strategy_id, requires_auction_confirmation)
    result: dict[str, Any] = {
        "version": MARKET_EXECUTION_GATE_VERSION,
        "available": False,
        "blocked": True,
        "fail_closed": True,
        "decision_date": decision_date.isoformat(),
        "contract_version": None,
        "strategy_id": strategy_id,
        "strategy_key": strategy_key,
        "final_action": None,
        "weight_pct": None,
        "reason": "市场决策快照不可用，按失败关闭规则不建立新模拟仓位",
    }
    if not isinstance(payload, dict) or payload.get("available") is not True:
        return result

    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    contract_version = str(meta.get("contract_version") or "")
    payload_date = str(meta.get("decision_date") or "")
    result["contract_version"] = contract_version or None
    if contract_version != WORKBENCH_CONTRACT_VERSION:
        result["reason"] = "市场决策契约已过期，按失败关闭规则不建立新模拟仓位"
        return result
    if payload_date != decision_date.isoformat():
        result["reason"] = "市场决策日与策略信号日不一致，不使用跨日结论建立模拟仓位"
        return result

    market_way = payload.get("market_way_v4") if isinstance(payload.get("market_way_v4"), dict) else {}
    truth = market_way.get("truth") if isinstance(market_way.get("truth"), dict) else {}
    truth_status = str(truth.get("status") or "")
    result["truth_status"] = truth_status or None
    result["truth_completeness_pct"] = truth.get("completeness_pct")
    if not truth_status:
        legacy_action = str((payload.get("market_cognition") or {}).get("final_action") or "")
        result["reason"] = (
            "V4真值层缺失，按失败关闭规则不建立新模拟仓位；市场工作台输出不交易"
            if legacy_action == "no_trade"
            else "V4真值层缺失，按失败关闭规则不建立新模拟仓位"
        )
        return result
    if truth_status == "FAIL" or int((truth.get("pit_guard") or {}).get("rejected_count") or 0) > 0:
        result["reason"] = "V4真值层未通过或存在未来数据，按失败关闭规则不建立新模拟仓位"
        return result

    cognition = payload.get("market_cognition") if isinstance(payload.get("market_cognition"), dict) else {}
    final_action = str(cognition.get("final_action") or "")
    result["final_action"] = final_action or None
    if final_action not in FINAL_ACTIONS:
        result["reason"] = "市场最终行动缺失或无效，按失败关闭规则不建立新模拟仓位"
        return result

    adaptive = payload.get("adaptive_strategy_weights") if isinstance(payload.get("adaptive_strategy_weights"), dict) else {}
    matching = next(
        (
            item for item in adaptive.get("weights") or []
            if isinstance(item, dict) and str(item.get("strategy_id") or "") == strategy_key
        ),
        None,
    )
    try:
        weight_pct = float(matching.get("weight_pct")) if matching is not None else None
    except (TypeError, ValueError):
        weight_pct = None
    if weight_pct is not None and not isfinite(weight_pct):
        weight_pct = None
    result["weight_pct"] = weight_pct
    if weight_pct is None:
        result["reason"] = "策略动态权重缺失，按失败关闭规则不建立新模拟仓位"
        return result

    result["available"] = True
    if final_action == "no_trade":
        result["reason"] = "市场工作台输出不交易，候选保留观察但不建立模拟仓位"
        return result
    if final_action == "observe":
        result["reason"] = "市场工作台仅允许观察，候选不建立模拟仓位"
        return result
    if weight_pct <= 0:
        result["reason"] = "对应策略动态权重为0，候选保留观察但不建立模拟仓位"
        return result

    result["blocked"] = False
    result["reason"] = f"市场工作台允许{final_action}，对应策略动态权重 {weight_pct:g}%"
    return result
