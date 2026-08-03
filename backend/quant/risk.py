"""Independent financial, event and market risk checks for research output."""

from __future__ import annotations

import math
from typing import Any


CRITICAL_ANNOUNCEMENT_TERMS = (
    "退市风险",
    "终止上市",
    "立案调查",
    "财务造假",
    "资金占用",
    "无法表示意见",
    "否定意见",
    "债务逾期",
)

FINANCIAL_SECTOR_TERMS = ("银行", "证券", "保险", "金融")


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def assess_stock_risk(stock: dict, announcements: list[dict] | None = None) -> dict:
    """Apply non-compensating risk checks without treating missing data as safe."""
    hard_blocks: list[str] = []
    warnings: list[str] = []
    evidence: list[str] = []
    missing: list[str] = []
    penalty = 0.0

    name = str(stock.get("name") or "")
    normalized_name = name.upper()
    if "ST" in normalized_name or "退" in name:
        hard_blocks.append("证券简称包含ST或退市风险标记")

    net_profit = _number(stock.get("net_profit"))
    if net_profit is None:
        missing.append("最新披露净利润")
    elif net_profit <= 0:
        hard_blocks.append("最新披露净利润不为正")
    else:
        evidence.append("最新披露净利润为正")

    gross_margin = _number(stock.get("gross_margin"))
    if gross_margin is None:
        missing.append("毛利率")
    elif gross_margin < 10:
        warnings.append(f"毛利率仅 {gross_margin:.1f}%")
        penalty += 12

    deducted_growth = _number(stock.get("deducted_profit_growth"))
    if deducted_growth is None:
        missing.append("扣非净利润增速")
    elif deducted_growth < 0:
        warnings.append(f"扣非净利润增速 {deducted_growth:.1f}%")
        penalty += 10

    ocf_to_profit = _number(stock.get("ocf_to_profit"))
    if ocf_to_profit is None:
        missing.append("经营现金流/净利润")
    elif ocf_to_profit < 0:
        warnings.append(f"经营现金流/净利润 {ocf_to_profit:.2f}，现金流为负")
        penalty += 18
    elif ocf_to_profit < 0.8:
        warnings.append(f"经营现金流/净利润 {ocf_to_profit:.2f}，低于0.8")
        penalty += 8
    else:
        evidence.append(f"经营现金流/净利润 {ocf_to_profit:.2f}")

    debt_ratio = _number(stock.get("debt_ratio"))
    sector_text = f"{stock.get('sector') or ''}{name}"
    is_financial_sector = any(term in sector_text for term in FINANCIAL_SECTOR_TERMS)
    if debt_ratio is None:
        missing.append("资产负债率")
    elif is_financial_sector:
        evidence.append(f"资产负债率 {debt_ratio:.1f}%，金融行业不套用普通企业阈值")
    elif debt_ratio > 85:
        warnings.append(f"资产负债率 {debt_ratio:.1f}%")
        penalty += 12
    elif debt_ratio <= 60:
        evidence.append(f"资产负债率 {debt_ratio:.1f}%")

    receivable_ratio = _number(stock.get("receivable_to_revenue"))
    if receivable_ratio is None:
        missing.append("应收/营收比")
    elif receivable_ratio > 50:
        warnings.append(f"应收/营收比 {receivable_ratio:.1f}%")
        penalty += 15

    lockup_days = _number(stock.get("lockup_days"))
    if lockup_days is None:
        missing.append("未来限售解禁日程")
    elif lockup_days <= 7:
        ratio = _number(stock.get("lockup_ratio_pct"))
        detail = f"未来 {int(lockup_days)} 天内有限售解禁"
        if ratio is not None:
            detail += f"，占总股本约 {ratio:.2f}%"
        hard_blocks.append(detail)
    elif lockup_days <= 30:
        warnings.append(f"距下次限售解禁 {int(lockup_days)} 天")
        penalty += 8
    else:
        evidence.append(f"未来7天内无已披露限售解禁（覆盖到{stock.get('lockup_coverage_end') or '未知'}）")

    holder_change = _number(stock.get("holder_change_pct"))
    if holder_change is None:
        missing.append("股东户数变化")
    elif holder_change > 10:
        warnings.append(f"股东户数增加 {holder_change:.1f}%")
        penalty += 8
    elif holder_change < 0:
        evidence.append(f"股东户数下降 {abs(holder_change):.1f}%")

    critical_sources: list[dict] = []
    for item in announcements or []:
        title = str(item.get("title") or "")
        matched = [term for term in CRITICAL_ANNOUNCEMENT_TERMS if term in title]
        if not matched:
            continue
        hard_blocks.append(f"重大公告风险：{title}")
        critical_sources.append(item)

    missing_data_penalty = min(40.0, len(missing) * 5.0)
    score = max(0.0, 100.0 - penalty - missing_data_penalty - len(hard_blocks) * 35)
    level = "高" if hard_blocks or score < 45 else "中" if score < 72 or warnings or missing else "低"
    return {
        "score": round(score, 1),
        "risk_level": level,
        "hard_blocked": bool(hard_blocks),
        "hard_blocks": list(dict.fromkeys(hard_blocks)),
        "warnings": list(dict.fromkeys(warnings)),
        "evidence": list(dict.fromkeys(evidence)),
        "missing": list(dict.fromkeys(missing)),
        "missing_data_penalty": missing_data_penalty,
        "critical_sources": critical_sources,
        "principle": "重大风险为否决项，不能由技术面或资金面高分抵消。",
    }
