"""Summarise three-book stock evidence for a point-in-time candidate pool.

The labels describe research evidence, not a buy permission.  A market scan
is only the input universe; it never becomes a three-book conclusion by itself.
"""

from __future__ import annotations

from typing import Any


ACTIVE = {"POSSIBLE", "FORMING", "CONFIRMED"}
PATTERN_PREFIXES = (
    "BXDT_TRI_", "BXDT_BOX_", "BXDT_NECK_", "BXDT_UP_",
    "BXDT_BOTTOM_", "BXDT_CAPITAL_", "BXDT_PEAK_",
)


def _signal_brief(signal: dict[str, Any]) -> dict[str, Any]:
    return {
        "skill_id": signal.get("skill_id"),
        "name": signal.get("name"),
        "status": signal.get("status"),
        "lifecycle": signal.get("lifecycle"),
        "book_verification": signal.get("book_verification"),
        "next_confirmation": (signal.get("next_confirmation") or [])[:2],
        "invalidation": (signal.get("invalidation") or [])[:2],
    }


def unknown_review(decision_date: str, reasons: list[str], *, source_date: str | None = None, risk_priority: bool = False) -> dict[str, Any]:
    return {
        "status": "UNVERIFIED",
        "label": "风险待核验" if risk_priority else "三书待核验",
        "decision_date": decision_date,
        "source_date": source_date,
        "source": "local_daily_bars",
        "risk_priority": False,
        "risk_observation": risk_priority,
        "hunter": {"zone": None, "status": "UNKNOWN", "evidence": None},
        "big_pattern": {"status": "UNKNOWN", "signals": []},
        "star": {"status": "UNKNOWN", "positive": [], "risk": []},
        "three_books": "UNKNOWN",
        "reasons": reasons,
        "data_quality": {"status": "UNVERIFIED", "reasons": reasons},
    }


def summarize_candidate_review(
    v2: dict[str, Any] | None,
    hunter_evidence: dict[str, Any] | None,
    *,
    decision_date: str,
    selection_date: str | None,
    bar_count: int,
) -> dict[str, Any]:
    """Classify same-day research evidence without inventing book confirmation."""
    reasons: list[str] = []
    if not selection_date:
        reasons.append("初筛来源日期未标注")
    elif selection_date != decision_date:
        reasons.append("初筛与三书日线不是同一交易日")
    if not v2 or v2.get("trade_date") != decision_date:
        reasons.append("三书日线未覆盖目标交易日")
    if bar_count < 60:
        # MA60 is an engine feature, not a numerical rule from the books.
        reasons.append("本地日线少于系统 MA60 计算所需样本")
    zone = str((v2.get("zones") or {}).get("zone") or "") if v2 else ""
    risk_priority = bool(zone == "风险C区" or (v2.get("sell") or {}).get("risk_priority") == "RISK") if v2 else False
    evidence_quality = (hunter_evidence or {}).get("data_quality") or {}
    v2_quality = (v2 or {}).get("data_quality") or {}
    if evidence_quality.get("point_in_time") is not True:
        reasons.append("三书证据缺少经过验证的点时数据日期")
    if v2_quality.get("price_basis") != "OHLC":
        reasons.append("三书日线不是完整OHLC口径")
    if reasons:
        return unknown_review(decision_date, reasons, source_date=(v2 or {}).get("trade_date"), risk_priority=risk_priority)

    zone = zone or "未形成明确交易区"
    signals = [row for row in v2.get("signals") or [] if isinstance(row, dict)]
    patterns = [_signal_brief(row) for row in signals if row.get("status") in ACTIVE and str(row.get("skill_id") or "").startswith(PATTERN_PREFIXES) and not str(row.get("skill_id") or "").startswith("BXDT_PEAK_")]
    pattern_risks = [_signal_brief(row) for row in signals if (row.get("status") in ACTIVE or row.get("status") == "INVALID") and str(row.get("skill_id") or "").startswith("BXDT_PEAK_")]
    star_risks = [_signal_brief(row) for row in signals if (row.get("status") in ACTIVE or row.get("status") == "INVALID") and str(row.get("skill_id") or "").startswith("BXZX_CLASSIC_TOP_")]
    positive_stars = [_signal_brief(row) for row in signals if row.get("status") in ACTIVE and str(row.get("skill_id") or "").startswith("BXZX_") and not str(row.get("skill_id") or "").startswith("BXZX_CLASSIC_TOP_")]
    book_risk = bool(zone == "风险C区" or (v2.get("sell") or {}).get("risk_priority") == "RISK" or star_risks)
    hunter_status = "RISK" if book_risk else "STRUCTURE" if zone in {"强势A区", "强势B区"} else "WATCH"
    if book_risk:
        status, label = "RISK", "书籍风险优先"
    elif hunter_status == "STRUCTURE":
        status, label = "STRUCTURE_CANDIDATE", "强势结构研究候选"
    else:
        status, label = "INITIAL_WATCH", "初筛观察"

    if zone == "强势B区":
        reasons.append("B区仍需等待小A点与后续价量确认")
    if patterns:
        reasons.append("大形态仅记录结构与后续验证，不单独形成买点")
    if positive_stars:
        reasons.append("星线需前置位置和后续价量确认")
    if book_risk:
        reasons.append("C区或顶部证据压过正向形态")
    if pattern_risks:
        reasons.append("接近历史高点仅作风险观察，不能作为正向大形态")
    if not patterns and not positive_stars:
        reasons.append("当前未观察到大形态或星线正向证据")

    pattern_status = "OBSERVED" if patterns else "NOT_OBSERVED"
    star_status = "RISK" if star_risks else "OBSERVED" if positive_stars else "NOT_OBSERVED"
    return {
        "status": status,
        "label": label,
        "decision_date": decision_date,
        "source_date": v2.get("trade_date"),
        "source": "local_daily_bars+three_book_v2_shadow",
        "risk_priority": book_risk,
        "hunter": {"zone": zone, "status": hunter_status, "evidence": hunter_evidence},
        "big_pattern": {"status": pattern_status, "signals": patterns[:5], "risk": pattern_risks[:5]},
        "star": {"status": star_status, "positive": positive_stars[:5], "risk": star_risks[:5]},
        "three_books": "RESEARCH_CONVERGENCE" if hunter_status == "STRUCTURE" and patterns and positive_stars else "PARTIAL_RESEARCH",
        "reasons": reasons,
        "data_quality": {
            "status": "AVAILABLE",
            "bar_count": bar_count,
            "price_basis": (v2.get("data_quality") or {}).get("price_basis"),
            "same_day": True,
            "numeric_thresholds": "ENGINE_FEATURE",
        },
    }
