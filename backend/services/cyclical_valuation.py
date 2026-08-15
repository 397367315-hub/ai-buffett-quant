"""Shared, auditable valuation rules for cyclical A-share industries."""

from __future__ import annotations

import math
import statistics
from typing import Any, Iterable


CYCLE_SECTOR_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("gold_precious_metals", "黄金/贵金属", ("黄金", "贵金属", "白银")),
    ("nonferrous_metals", "有色/小金属", ("有色", "小金属", "铜", "铝", "铅锌", "稀土", "锂", "钴", "镍", "钨", "钼")),
    ("coal", "煤炭", ("煤炭", "焦煤", "焦炭", "煤化工")),
    ("oil_gas", "石油天然气", ("石油", "油气", "天然气", "炼化")),
    ("steel", "钢铁", ("钢铁", "普钢", "特钢", "铁矿")),
    ("basic_chemicals", "基础化工", ("基础化工", "化工原料", "氯碱", "化纤", "化肥", "农化", "纯碱", "聚氨酯")),
    ("cement_building_materials", "水泥/周期建材", ("水泥", "玻璃玻纤", "平板玻璃")),
    ("shipping", "航运", ("航运", "海运", "港口航运", "集运", "干散货", "油运")),
    ("breeding", "养殖", ("养殖", "生猪", "畜禽", "禽养殖", "猪肉", "鸡肉")),
    ("brokerage", "券商", ("证券", "券商", "资本市场服务")),
)

CYCLE_PHASE_LABELS = {
    "recovery": "复苏",
    "expansion": "扩张",
    "peak": "高位/峰值",
    "contraction": "收缩",
    "trough": "底部",
    "unknown": "待确认",
}


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _percentile_rank(value: float | None, values: Iterable[float]) -> float | None:
    clean = sorted(item for item in values if math.isfinite(item))
    if value is None or not clean:
        return None
    below = sum(item < value for item in clean)
    equal = sum(math.isclose(item, value, rel_tol=1e-9, abs_tol=1e-9) for item in clean)
    return round((below + equal * 0.5) / len(clean) * 100, 1)


def classify_cyclical_sector(*values: Any) -> dict:
    text = " ".join(str(value or "").strip() for value in values if str(value or "").strip())
    for key, label, terms in CYCLE_SECTOR_RULES:
        matched = [term for term in terms if term in text]
        if matched:
            return {
                "is_cyclical": True,
                "cyclical_sector": key,
                "cyclical_sector_label": label,
                "matched_terms": matched,
            }
    return {
        "is_cyclical": False,
        "cyclical_sector": None,
        "cyclical_sector_label": "非强周期行业",
        "matched_terms": [],
    }


def _series(history: list[dict], *keys: str) -> list[float]:
    values: list[float] = []
    for row in history:
        value = next((_number(row.get(key)) for key in keys if _number(row.get(key)) is not None), None)
        if value is not None:
            values.append(value)
    return values


def build_cyclical_valuation(
    *,
    sector_names: Iterable[Any],
    current_pe: float | None,
    current_pb: float | None,
    market_cap: float | None,
    fundamentals: dict,
) -> dict:
    classification = classify_cyclical_sector(*sector_names)
    if not classification["is_cyclical"]:
        return {
            **classification,
            "cycle_phase": "not_applicable",
            "cycle_phase_label": "不适用",
            "cycle_confidence": 100.0,
            "valuation_method": "TTM PE + 三年历史分位 + 行业横向分位",
            "pe_inversion_risk": False,
            "cycle_evidence": [],
            "cycle_warnings": [],
        }

    metrics = fundamentals.get("metrics") or {}
    history = [row for row in fundamentals.get("cycle_history") or [] if isinstance(row, dict)]
    current_profit = _number(metrics.get("net_profit_ttm"))
    current_margin = _number(metrics.get("gross_margin_pct"))
    current_roe = _number(metrics.get("roe_pct"))
    profit_growth = _number(metrics.get("net_profit_growth_pct"))
    cash_to_profit = _number(metrics.get("operating_cashflow_to_profit"))

    profits = _series(history, "net_profit_ttm", "net_profit")
    margins = _series(history, "gross_margin_pct", "gross_margin")
    roes = _series(history, "roe_pct", "roe")
    if current_profit is not None and (not profits or not math.isclose(profits[-1], current_profit, rel_tol=1e-9, abs_tol=1e-6)):
        profits.append(current_profit)
    if current_margin is not None and (not margins or not math.isclose(margins[-1], current_margin, rel_tol=1e-9, abs_tol=1e-6)):
        margins.append(current_margin)
    if current_roe is not None and (not roes or not math.isclose(roes[-1], current_roe, rel_tol=1e-9, abs_tol=1e-6)):
        roes.append(current_roe)

    profit_percentile = _percentile_rank(current_profit, profits)
    margin_percentile = _percentile_rank(current_margin, margins)
    roe_percentile = _percentile_rank(current_roe, roes)
    positive_profits = [value for value in profits if value > 0]
    normalized_profit = statistics.median(positive_profits) if len(positive_profits) >= 2 else None
    normalized_pe = None
    if market_cap is not None and market_cap > 0 and normalized_profit is not None and normalized_profit > 0:
        normalized_pe = market_cap / normalized_profit
    elif (
        current_pe is not None and current_pe > 0
        and current_profit is not None and current_profit > 0
        and normalized_profit is not None and normalized_profit > 0
    ):
        normalized_pe = current_pe * current_profit / normalized_profit

    phase = "unknown"
    if current_profit is not None and current_profit <= 0:
        phase = "recovery" if (profit_growth or 0) > 0 else "trough" if (profit_percentile or 0) <= 30 else "contraction"
    elif profit_percentile is not None:
        if profit_percentile >= 80 and (profit_growth is None or profit_growth <= 20 or (margin_percentile or 0) >= 70):
            phase = "peak"
        elif profit_percentile >= 55 and (profit_growth or 0) > 0:
            phase = "expansion"
        elif profit_percentile <= 30 and (profit_growth or 0) > 0:
            phase = "recovery"
        elif profit_percentile <= 25 and (profit_growth is None or profit_growth <= 0):
            phase = "trough"
        elif (profit_growth or 0) < 0:
            phase = "contraction"
        else:
            phase = "expansion"

    normalized_gap = normalized_pe / current_pe if normalized_pe is not None and current_pe is not None and current_pe > 0 else None
    pe_inversion_risk = bool(
        current_pe is not None and current_pe > 0
        and (
            (profit_percentile is not None and profit_percentile >= 75 and normalized_gap is not None and normalized_gap >= 1.25)
            or (profit_percentile is not None and profit_percentile >= 85 and (margin_percentile or 0) >= 70)
        )
    )
    if pe_inversion_risk and (profit_percentile or 0) >= 85:
        phase = "peak"

    if current_pb is None:
        pb_roe_signal = "PB/ROE数据不足"
    elif current_pb <= 1.5 and phase in {"trough", "recovery"} and (current_roe is None or current_roe <= 12):
        pb_roe_signal = "低PB + 低位ROE，观察盈利拐点"
    elif current_pb >= 3 and phase in {"peak", "contraction"}:
        pb_roe_signal = "PB不低且盈利处于高位/回落，资产端无安全垫"
    elif current_roe is not None and current_roe >= 12 and phase in {"recovery", "expansion"}:
        pb_roe_signal = "ROE处于修复/扩张阶段"
    else:
        pb_roe_signal = "PB/ROE处于中性区间"

    warnings: list[str] = []
    evidence: list[str] = []
    if profit_percentile is not None:
        evidence.append(f"TTM利润处历史样本{profit_percentile:.1f}%分位")
    if margin_percentile is not None:
        evidence.append(f"毛利率处历史样本{margin_percentile:.1f}%分位")
    if normalized_pe is not None:
        evidence.append(f"以历史正利润中位数计算的标准化PE为{normalized_pe:.2f}")
    evidence.append(f"PB/ROE交叉信号：{pb_roe_signal}")
    if pe_inversion_risk:
        warnings.append("低TTM PE对应周期盈利高位，存在PE反向陷阱；不按普通低估加分")
    if phase in {"trough", "recovery"} and (current_pe is None or current_pe <= 0):
        warnings.append("周期底部PE失真，改用PB/ROE、经营现金流和标准化利润观察")
    if len(profits) < 3:
        warnings.append("可比盈利历史少于3期，周期阶段置信度受限")
    if classification["cyclical_sector"] == "brokerage":
        warnings.append("券商需结合成交额、两融和资本市场周期，制造业毛利率阈值不适用")

    confidence = 40.0
    confidence += min(len(profits), 5) * 6
    confidence += min(len(margins), 4) * 3
    confidence += min(len(roes), 4) * 3
    confidence += 6 if normalized_pe is not None else 0
    confidence = min(confidence, 95.0)

    value_score = 50.0
    if normalized_pe is not None:
        value_score += 12 if normalized_pe <= 15 else 5 if normalized_pe <= 25 else -10 if normalized_pe > 40 else 0
    if phase == "recovery":
        value_score += 12
    elif phase == "expansion":
        value_score += 4
    elif phase == "contraction":
        value_score -= 12
    elif phase == "peak":
        value_score -= 15
    if pe_inversion_risk:
        value_score = min(value_score - 15, 30)
    if cash_to_profit is not None:
        value_score += 5 if cash_to_profit >= 0.8 else -8
    value_score = max(0.0, min(100.0, value_score))

    state = {
        "peak": "周期高位：警惕低PE反向陷阱" if pe_inversion_risk else "周期高位：按标准化利润审慎估值",
        "expansion": "周期扩张：当前PE需与盈利中枢交叉验证",
        "recovery": "周期复苏：观察PB/ROE与现金流确认",
        "contraction": "周期收缩：低PE不构成安全边际",
        "trough": "周期底部观察：PE暂不作为核心依据",
        "unknown": "周期阶段待确认：不采用单一PE结论",
    }[phase]
    return {
        **classification,
        "cycle_phase": phase,
        "cycle_phase_label": CYCLE_PHASE_LABELS[phase],
        "cycle_confidence": round(confidence, 1),
        "profit_cycle_percentile": profit_percentile,
        "margin_cycle_percentile": margin_percentile,
        "roe_cycle_percentile": roe_percentile,
        "normalized_profit": _round(normalized_profit, 0),
        "normalized_pe": _round(normalized_pe),
        "normalized_pe_to_current_ratio": _round(normalized_gap, 3),
        "pb_roe_signal": pb_roe_signal,
        "pe_inversion_risk": pe_inversion_risk,
        "valuation_method": "周期股：标准化盈利PE + PB/ROE + 现金流 + 周期阶段",
        "cycle_state": state,
        "long_term_value_score": round(value_score, 1),
        "cycle_evidence": evidence,
        "cycle_warnings": warnings,
        "cycle_sample_count": len(profits),
    }


def cycle_guard_from_stock(stock: dict) -> dict:
    """Read cycle metadata consistently in FQE and stock-selection agents."""
    classification = classify_cyclical_sector(stock.get("industry"), stock.get("sector"))
    normalized_pe = _number(stock.get("normalized_pe"))
    phase = str(stock.get("cycle_phase") or "unknown")
    if phase not in CYCLE_PHASE_LABELS:
        phase = "unknown"
    inversion = bool(stock.get("pe_inversion_risk"))
    return {
        **classification,
        "cycle_phase": phase,
        "cycle_phase_label": CYCLE_PHASE_LABELS[phase],
        "normalized_pe": normalized_pe,
        "pe_inversion_risk": inversion,
        "cycle_data_available": bool(normalized_pe is not None or phase != "unknown" or inversion),
    }
