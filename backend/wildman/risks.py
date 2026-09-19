"""Pure risk facts used by the Wildman classic scanner.

This module classifies only explicit, point-in-time facts.  It never turns an
empty response into a clean bill of health and never treats positive EPS/ROE
as proof that a company has no financial or disclosure risk.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from typing import Any


ANNOUNCEMENT_LOOKBACK_DAYS = 365
MAJOR_ANNOUNCEMENT_TERMS = (
    "立案调查", "行政处罚", "监管措施", "重大违法", "退市风险警示", "退市风险提示",
    "终止上市", "暂停上市", "债务违约", "债务逾期", "破产清算",
    "被申请破产", "否定意见", "无法表示意见", "重大诉讼判决",
)
WARNING_ANNOUNCEMENT_TERMS = (
    "风险提示", "审计意见", "重大诉讼", "诉讼", "仲裁", "违规",
    "减持", "预亏", "亏损", "业绩下降", "商誉减值", "重大不确定性",
)
NEGATED_ANNOUNCEMENT_TERMS = (
    "不存在重大诉讼", "无重大诉讼", "未发生重大诉讼", "不存在重大违法",
    "无重大违法", "未发现重大违法", "解除风险警示", "撤销风险警示",
    "风险已解除", "风险解除", "诉讼已结案", "诉讼结案", "案件已结案",
    "终止减持计划", "减持计划终止", "减持完成", "减持计划实施完毕",
    "整改完成", "撤诉", "撤回起诉",
)

# These phrases negate only the risk they name.  They must not suppress an
# unrelated risk in the same disclosure, for example a completed reduction
# alongside a newly announced investigation.
_NEGATED_RISK_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("不存在重大诉讼", ("重大诉讼", "诉讼")),
    ("无重大诉讼", ("重大诉讼", "诉讼")),
    ("未发生重大诉讼", ("重大诉讼", "诉讼")),
    ("诉讼已结案", ("重大诉讼", "诉讼", "重大诉讼判决")),
    ("诉讼结案", ("重大诉讼", "诉讼", "重大诉讼判决")),
    ("案件已结案", ("重大诉讼", "诉讼", "重大诉讼判决")),
    ("不存在重大违法", ("重大违法",)),
    ("无重大违法", ("重大违法",)),
    ("未发现重大违法", ("重大违法",)),
    ("不存在退市风险提示", ("退市风险提示",)),
    ("不存在退市风险警示", ("退市风险警示",)),
    ("解除风险警示", ("退市风险警示", "退市风险提示", "风险提示")),
    ("撤销风险警示", ("退市风险警示", "退市风险提示", "风险提示")),
    ("风险已解除", ("退市风险警示", "退市风险提示", "风险提示")),
    ("风险解除", ("退市风险警示", "退市风险提示", "风险提示")),
    ("不存在债务违约", ("债务违约",)),
    ("无债务违约", ("债务违约",)),
    ("未发生债务违约", ("债务违约",)),
    ("未受到行政处罚", ("行政处罚",)),
    ("不存在行政处罚", ("行政处罚",)),
    ("未被立案调查", ("立案调查",)),
    ("未收到立案调查", ("立案调查",)),
    ("不存在立案调查", ("立案调查",)),
    ("终止减持计划", ("减持",)),
    ("减持计划终止", ("减持",)),
    ("减持完成", ("减持",)),
    ("减持计划实施完毕", ("减持",)),
    ("整改完成", ("整改",)),
    ("撤诉", ("诉讼", "仲裁")),
    ("撤回起诉", ("诉讼",)),
)


def normalize_code(value: Any) -> str:
    text = str(value or "").strip().upper().split(".", 1)[0]
    return text.zfill(6) if text else ""


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def review_window(target: date) -> tuple[date, date]:
    return target - timedelta(days=ANNOUNCEMENT_LOOKBACK_DAYS), target


def announcement_reasons(title: str, summary: str = "", category: str = "") -> list[str]:
    """Return only clear major-risk terms; routine warnings are not vetoes."""
    level, reasons = classify_announcement(title, summary, category)
    return reasons if level == "major" else []


def classify_announcement(title: str, summary: str = "", category: str = "") -> tuple[str, list[str]]:
    """Classify each disclosure clause with risk-specific negation handling."""
    text = " ".join(str(value or "") for value in (title, summary, category))
    clauses = [part.strip() for part in re.split(r"[，,；;。！？!?、\n]|但|然而|同时|另有|并且", text) if part.strip()]
    major: list[str] = []
    warning: list[str] = []
    negated = False
    for clause in clauses:
        clause_negated = any(term in clause for term in NEGATED_ANNOUNCEMENT_TERMS)
        major_terms = [
            term for term in MAJOR_ANNOUNCEMENT_TERMS
            if term in clause and not _risk_is_negated(clause, term)
        ]
        warning_terms = [
            term for term in WARNING_ANNOUNCEMENT_TERMS
            if term in clause and not _risk_is_negated(clause, term)
        ]
        major.extend(major_terms)
        warning.extend(warning_terms)
        negated = negated or (clause_negated and not major_terms and not warning_terms)
    major = list(dict.fromkeys(major))
    if major:
        return "major", major
    warning = list(dict.fromkeys(warning))
    if warning:
        return "warning", warning
    return "observed", ["公告明确否定、解除或完成状态，不作重大风险命中"] if negated else []


def _risk_is_negated(clause: str, risk_term: str) -> bool:
    """Apply a negation/completion only to its corresponding risk term."""
    return any(
        risk_term in related_terms and phrase in clause
        for phrase, related_terms in _NEGATED_RISK_PHRASES
    )


def financial_indicator_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    try:
        eps = float(row["eps"]) if row.get("eps") not in (None, "") else None
    except (TypeError, ValueError):
        eps = None
    try:
        roe = float(row["roe"]) if row.get("roe") not in (None, "") else None
    except (TypeError, ValueError):
        roe = None
    if eps is not None and eps < 0:
        reasons.append("NumCat finance_indicator EPS<0")
    if roe is not None and roe < 0:
        reasons.append("NumCat finance_indicator ROE<0")
    return reasons


def financial_indicator_warnings(row: dict[str, Any]) -> list[str]:
    """Return non-veto indicators whose meaning is sector dependent."""
    try:
        debt = float(row["debt_to_assets"]) if row.get("debt_to_assets") not in (None, "") else None
    except (TypeError, ValueError):
        debt = None
    if debt is not None and debt >= 80:
        return ["NumCat finance_indicator 资产负债率>=80%；行业口径未归一化，仅作warning，不作硬风险"]
    return []


def pit_financial_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    try:
        net_profit = float(row["net_profit"]) if row.get("net_profit") is not None else None
    except (TypeError, ValueError):
        net_profit = None
    if net_profit is not None and net_profit < 0:
        reasons.append("PIT已披露净利润<0")
    return reasons


def pit_financial_warnings(row: dict[str, Any]) -> list[str]:
    """Cash-flow negatives are warnings, with an explicit finance-sector note."""
    try:
        operating_cf = float(row["operating_cf"]) if row.get("operating_cf") is not None else None
    except (TypeError, ValueError):
        operating_cf = None
    if operating_cf is not None and operating_cf < 0:
        industry = str(row.get("industry") or row.get("sector") or "")
        if any(term in industry for term in ("银行", "保险", "证券", "金融")):
            return ["PIT已披露经营现金流<0；金融行业现金流口径特殊，仅作warning，不作硬风险"]
        return ["PIT已披露经营现金流<0；仅作warning，不作单项硬风险"]
    return []


__all__ = [
    "ANNOUNCEMENT_LOOKBACK_DAYS",
    "announcement_reasons",
    "classify_announcement",
    "financial_indicator_reasons",
    "financial_indicator_warnings",
    "normalize_code",
    "parse_date",
    "pit_financial_reasons",
    "pit_financial_warnings",
    "review_window",
]
