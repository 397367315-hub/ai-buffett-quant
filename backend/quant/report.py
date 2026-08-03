"""Compact, serialisable reports for quantitative scans."""

from __future__ import annotations

from typing import Iterable


def build_feature_coverage_report(
    coverage: dict,
    requested_fields: Iterable[str],
    warnings: list[str] | None = None,
) -> dict:
    total = int(coverage.get("total") or 0)
    datasets = {}
    for key, label in (
        ("financial", "财务报告"),
        ("shareholders", "股东户数"),
        ("lockups", "限售解禁"),
        ("sector_strength", "板块强度"),
    ):
        count = int(coverage.get(key) or 0)
        datasets[key] = {
            "label": label,
            "covered": count,
            "total": total,
            "coverage_pct": round(count / total * 100, 1) if total else 0.0,
        }
    datasets["market_breadth"] = {
        "label": "大盘涨跌比",
        "available": bool(coverage.get("market_breadth")),
    }
    return {
        "requested_fields": sorted(set(requested_fields)),
        "datasets": datasets,
        "warnings": list(warnings or []),
        "missing_policy": "字段缺失记为数据不足，不记为0，也不视为规则通过。",
    }


def build_rule_audit(*groups: dict) -> dict:
    details = [item for group in groups for item in group.get("details", [])]
    counts = {
        status: sum(item.get("status") == status for item in details)
        for status in ("passed", "failed", "unavailable", "invalid")
    }
    return {
        "counts": counts,
        "details": details,
        "complete": counts["unavailable"] == 0 and counts["invalid"] == 0,
    }
