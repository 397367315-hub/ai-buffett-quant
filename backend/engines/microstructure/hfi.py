"""Hidden Fund Index composition.

HFI is an observable-flow composite, not an account-identity detector and not
a standalone trading signal.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


DEFAULT_WEIGHTS = {
    "active_flow": 0.25,
    "absorption": 0.20,
    "split": 0.15,
    "imbalance": 0.15,
    "replenishment": 0.10,
    "vwap": 0.10,
    "impact": 0.05,
}


def clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def signed_strength(value: float | None, denominator: float | None = None) -> float | None:
    """Normalize a signed amount or score to -1..1."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if denominator and denominator > 0:
        return clamp(number / denominator)
    return clamp(number)


def score_label(value: float | None) -> str:
    if value is None:
        return "暂无样本"
    if value >= 0.35:
        return "偏多"
    if value <= -0.35:
        return "偏空"
    return "中性"


def build_hfi(
    components: Mapping[str, float | None],
    *,
    weights: Mapping[str, float] | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    configured = {**DEFAULT_WEIGHTS, **(weights or {})}
    usable: dict[str, float] = {}
    for key, value in components.items():
        try:
            numeric = float(value) if value is not None else None
        except (TypeError, ValueError):
            numeric = None
        try:
            weight = float(configured.get(key, 0))
        except (TypeError, ValueError):
            weight = 0.0
        if numeric is not None and math.isfinite(numeric) and weight > 0:
            usable[key] = clamp(numeric)
            configured[key] = weight
    total_weight = sum(float(configured[key]) for key in usable)
    if total_weight <= 0:
        return {
            "value": None,
            "label": "暂无样本",
            "available": False,
            "coverage_pct": 0.0,
            "confidence": 0.0,
            "components": {},
        }
    raw = sum(usable[key] * float(configured[key]) for key in usable) / total_weight
    component_payload = {
        key: {
            "normalized": round(value, 4),
            "weight": float(configured[key]),
            "contribution": round(value * float(configured[key]) / total_weight, 4),
        }
        for key, value in usable.items()
    }
    positive_weights = []
    for value in configured.values():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = 0.0
        if numeric > 0:
            positive_weights.append(numeric)
    coverage = total_weight / sum(positive_weights) * 100 if positive_weights else 0.0
    effective_confidence = min(float(confidence if confidence is not None else 0.0), coverage)
    return {
        "value": round(raw * 100, 2),
        "label": score_label(raw),
        "available": True,
        "coverage_pct": round(coverage, 1),
        "confidence": round(max(0.0, min(100.0, effective_confidence)), 1),
        "components": component_payload,
    }
