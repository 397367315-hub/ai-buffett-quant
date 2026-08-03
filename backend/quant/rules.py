"""Auditable rule catalogue and deterministic three-state evaluation."""

from __future__ import annotations

import math
from typing import Any


def _meta(
    label: str,
    value_type: str,
    unit: str,
    operators: list[str],
    default: Any,
    *,
    category: str,
    source_group: str,
    source: str,
    historical_support: str,
    note: str = "",
    **extra: Any,
) -> dict:
    return {
        "label": label,
        "value_type": value_type,
        "unit": unit,
        "operators": operators,
        "default": default,
        "category": category,
        "source_group": source_group,
        "source": source,
        "historical_support": historical_support,
        "note": note,
        **extra,
    }


NUMBER_OPERATORS = ["gt", "gte", "lt", "lte", "between"]

RULE_CATALOG: dict[str, dict] = {
    "turnover": _meta("换手率", "number", "%", NUMBER_OPERATORS, 3, category="技术面", source_group="quote", source="行情快照/历史日线", historical_support="partial"),
    "vol_ratio": _meta("量比", "number", "", NUMBER_OPERATORS, 1.2, category="技术面", source_group="quote", source="行情快照/成交量推导", historical_support="derived"),
    "change_pct": _meta("涨跌幅", "number", "%", NUMBER_OPERATORS, 2, category="技术面", source_group="quote", source="行情快照/历史日线", historical_support="yes"),
    "price": _meta("最新价", "number", "元", NUMBER_OPERATORS, 10, category="技术面", source_group="quote", source="行情快照/历史日线", historical_support="yes"),
    "above_ma": _meta("站上均线", "select", "", ["eq", "ne"], "MA20", category="技术面", source_group="technical", source="缓存日线计算", historical_support="yes", options=["MA5", "MA10", "MA20", "MA60"]),
    "below_ma": _meta("跌破均线", "select", "", ["eq", "ne"], "MA20", category="技术面", source_group="technical", source="缓存日线计算", historical_support="yes", options=["MA5", "MA10", "MA20", "MA60"]),
    "rsi": _meta("RSI(14)", "number", "", NUMBER_OPERATORS, 30, category="技术面", source_group="technical", source="缓存日线计算", historical_support="yes"),
    "macd": _meta("MACD柱", "number", "", NUMBER_OPERATORS, 0, category="技术面", source_group="technical", source="缓存日线计算", historical_support="yes"),
    "macd_golden_cross": _meta("MACD金叉", "boolean", "", ["eq", "ne"], True, category="技术面", source_group="technical", source="缓存日线计算", historical_support="yes"),
    "long_lower_shadow": _meta("长下影线", "boolean", "", ["eq", "ne"], True, category="技术面", source_group="technical", source="缓存日线计算", historical_support="yes"),

    "main_inflow": _meta("主力净流入", "number", "万元", NUMBER_OPERATORS, 0, category="资金面", source_group="quote", source="东方财富资金流", historical_support="partial"),
    "large_order_inflow_pct": _meta("大单净流入占比", "number", "%", NUMBER_OPERATORS, 3, category="资金面", source_group="quote", source="东方财富大单资金流", historical_support="no", note="仅使用大单口径，不用主力净流入占比替代。"),

    "pe_ttm": _meta("PE(TTM)", "number", "倍", NUMBER_OPERATORS, 30, category="基本面", source_group="quote", source="行情快照", historical_support="no", positive_only=True),
    "pb": _meta("PB", "number", "倍", NUMBER_OPERATORS, 3, category="基本面", source_group="quote", source="行情快照", historical_support="no", positive_only=True),
    "roe": _meta("ROE", "number", "%", NUMBER_OPERATORS, 15, category="基本面", source_group="financial", source="东方财富财务主表", historical_support="current_disclosure_only"),
    "gross_margin": _meta("毛利率", "number", "%", NUMBER_OPERATORS, 15, category="基本面", source_group="financial", source="东方财富财务主表", historical_support="current_disclosure_only"),
    "revenue_growth": _meta("营收增速", "number", "%", NUMBER_OPERATORS, 0, category="基本面", source_group="financial", source="东方财富财务主表", historical_support="current_disclosure_only"),
    "deducted_profit_growth": _meta("扣非净利润增速", "number", "%", NUMBER_OPERATORS, 0, category="基本面", source_group="financial", source="东方财富财务主表", historical_support="current_disclosure_only"),
    "ocf_to_profit": _meta("经营现金流/净利润", "number", "", NUMBER_OPERATORS, 0.8, category="基本面", source_group="financial", source="东方财富财务主表", historical_support="current_disclosure_only"),
    "debt_ratio": _meta("资产负债率", "number", "%", NUMBER_OPERATORS, 60, category="基本面", source_group="financial", source="东方财富财务主表", historical_support="current_disclosure_only", note="金融行业需使用更适合其资产负债结构的阈值。"),
    "receivable_to_revenue": _meta("应收/营收比", "number", "%", NUMBER_OPERATORS, 30, category="基本面", source_group="financial", source="东方财富财务主表", historical_support="current_disclosure_only"),
    "market_cap": _meta("总市值", "number", "亿元", NUMBER_OPERATORS, 500, category="基本面", source_group="quote", source="行情快照", historical_support="no"),
    "is_profitable": _meta("排除亏损/ST", "boolean", "", ["eq", "ne"], True, category="排雷", source_group="financial", source="财务主表+证券简称", historical_support="current_disclosure_only"),

    "below_fib": _meta("斐波那契支撑", "select", "", ["lt", "lte", "gt", "gte"], 0.618, category="估值面", source_group="technical", source="近60日日线计算", historical_support="yes", options=[0.382, 0.5, 0.618, 0.786]),
    "sector": _meta("所属板块", "multi-select", "", ["in", "not_in"], [], category="市场面", source_group="quote", source="东方财富行业/板块成分", historical_support="no"),
    "market_breadth": _meta("大盘涨跌比", "number", "", NUMBER_OPERATORS, 1, category="市场面", source_group="market", source="完整A股行情横截面推导", historical_support="current_snapshot_only", note="候选榜单不能代表全市场，只有完整快照时才执行。"),
    "sector_strength": _meta("板块强度排名", "number", "名", ["lt", "lte", "between"], 5, category="市场面", source_group="market", source="行业涨幅、上涨家数与资金流综合排名", historical_support="current_snapshot_only"),

    "no_lockup_expiry": _meta("距下次限售解禁", "number", "天", ["gt", "gte", "between"], 7, category="排雷", source_group="lockups", source="东方财富限售解禁表", historical_support="current_disclosure_only", note="无未来365日解禁记录时只声明大于365天，不推断更远日期。"),
    "holder_concentration": _meta("股东户数变化", "number", "%", NUMBER_OPERATORS, 0, category="排雷", source_group="shareholders", source="东方财富股东户数表", historical_support="current_disclosure_only"),
    "holder_decline_streak": _meta("股东户数连续下降", "number", "期", ["gt", "gte", "between"], 2, category="排雷", source_group="shareholders", source="东方财富股东户数表", historical_support="current_disclosure_only"),
}

TECHNICAL_RULE_TYPES = {
    "above_ma", "below_ma", "below_fib", "rsi", "macd", "macd_golden_cross", "long_lower_shadow",
}

FIELD_MAP = {
    "turnover": "turnover",
    "vol_ratio": "vol_ratio",
    "change_pct": "change_pct",
    "price": "price",
    "main_inflow": "main_inflow",
    "large_order_inflow_pct": "large_order_inflow_pct",
    "pe_ttm": "pe_ttm",
    "pb": "pb",
    "roe": "roe",
    "gross_margin": "gross_margin",
    "revenue_growth": "revenue_growth",
    "deducted_profit_growth": "deducted_profit_growth",
    "ocf_to_profit": "ocf_to_profit",
    "debt_ratio": "debt_ratio",
    "receivable_to_revenue": "receivable_to_revenue",
    "market_cap": "market_cap",
    "rsi": "rsi",
    "macd": "macd",
    "macd_golden_cross": "macd_golden_cross",
    "long_lower_shadow": "long_lower_shadow",
    "market_breadth": "market_breadth",
    "sector_strength": "sector_rank",
    "no_lockup_expiry": "lockup_days",
    "holder_concentration": "holder_change_pct",
    "holder_decline_streak": "holder_decline_streak",
}


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def validate_rule(rule: dict) -> None:
    rule_type = str(rule.get("type") or "")
    meta = RULE_CATALOG.get(rule_type)
    if meta is None:
        raise ValueError(f"不支持的规则类型: {rule_type}")
    operator = str(rule.get("operator") or "")
    if operator not in meta["operators"]:
        raise ValueError(f"规则 {rule_type} 不支持操作符 {operator}")
    value = rule.get("value")
    if rule_type == "sector":
        if not isinstance(value, list) or not any(str(item).strip() for item in value):
            raise ValueError("板块规则至少选择一个板块")
    elif operator == "between":
        if not isinstance(value, list) or len(value) != 2 or any(_number(item) is None for item in value):
            raise ValueError(f"规则 {rule_type} 的区间值必须包含两个数字")
        if float(value[0]) > float(value[1]):
            raise ValueError(f"规则 {rule_type} 的区间下限不能大于上限")
    elif rule_type in {"above_ma", "below_ma"}:
        if str(value).upper() not in meta["options"]:
            raise ValueError(f"规则 {rule_type} 的均线参数无效")
    elif rule_type == "below_fib":
        if _number(value) not in meta["options"]:
            raise ValueError("斐波那契参数无效")
    elif meta["value_type"] == "number" and _number(value) is None:
        raise ValueError(f"规则 {rule_type} 的阈值必须是数字")
    elif meta["value_type"] == "boolean" and not isinstance(value, bool):
        raise ValueError(f"规则 {rule_type} 的值必须是布尔值")


def validate_group(group: dict, *, allow_empty: bool = True) -> None:
    logic = str(group.get("logic") or "AND").upper()
    if logic not in {"AND", "OR"}:
        raise ValueError("规则组合仅支持 AND 或 OR")
    rules = group.get("rules") or []
    if not allow_empty and not rules:
        raise ValueError("至少需要一条规则")
    for rule in rules:
        validate_rule(rule)


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    if operator in {"in", "not_in"}:
        if actual is None or actual == []:
            return False
        expected_values = {str(item).strip() for item in (expected if isinstance(expected, list) else [expected])}
        actual_values = {str(item).strip() for item in (actual if isinstance(actual, (list, tuple, set)) else [actual])}
        matched = bool(actual_values & expected_values)
        return matched if operator == "in" else not matched
    if operator in {"eq", "ne"} and isinstance(actual, bool):
        result = actual is bool(expected)
        return result if operator == "eq" else not result
    left = _number(actual)
    if left is None:
        return False
    if operator == "between":
        lower, upper = (_number(item) for item in expected)
        return lower is not None and upper is not None and lower <= left <= upper
    right = _number(expected)
    if right is None:
        return False
    return {
        "gt": left > right,
        "gte": left >= right,
        "lt": left < right,
        "lte": left <= right,
        "eq": left == right,
        "ne": left != right,
    }.get(operator, False)


def _rule_actual(rule: dict, stock: dict) -> tuple[Any, Any]:
    rule_type = rule["type"]
    expected = rule.get("value")
    if rule_type == "sector":
        sectors = stock.get("sectors") or [stock.get("sector")]
        return [item for item in sectors if item], expected
    if rule_type in {"above_ma", "below_ma"}:
        period = str(expected).lower()
        price = _number(stock.get("price"))
        moving_average = _number(stock.get(period))
        if price is None or moving_average is None:
            return None, True
        is_true = price > moving_average if rule_type == "above_ma" else price < moving_average
        return is_true, True
    if rule_type == "below_fib":
        level = str(expected).replace(".", "_")
        return stock.get("price"), stock.get(f"fib_{level}")
    if rule_type == "is_profitable":
        explicit = stock.get("is_profitable_non_st")
        if isinstance(explicit, bool):
            return explicit, expected
        name = str(stock.get("name") or "").upper()
        profit = _number(stock.get("net_profit"))
        pe = _number(stock.get("pe_ttm") if "pe_ttm" in stock else stock.get("pe"))
        if profit is None and pe is None:
            return None, expected
        return bool("ST" not in name and "退" not in name and (profit > 0 if profit is not None else pe > 0)), expected
    return stock.get(FIELD_MAP[rule_type]), expected


def describe_rule(rule: dict) -> str:
    meta = RULE_CATALOG.get(str(rule.get("type"))) or {"label": str(rule.get("type")), "unit": ""}
    operator = {
        "gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "=", "ne": "!=",
        "in": "属于", "not_in": "不属于", "between": "介于",
    }.get(str(rule.get("operator")), str(rule.get("operator")))
    value = rule.get("value")
    if isinstance(value, list):
        value = " ~ ".join(map(str, value)) if rule.get("operator") == "between" else "、".join(map(str, value))
    return f"{meta['label']} {operator} {value}{meta.get('unit', '')}".strip()


def evaluate_rule(rule: dict, stock: dict) -> dict:
    label = describe_rule(rule)
    try:
        validate_rule(rule)
        actual, expected = _rule_actual(rule, stock)
        meta = RULE_CATALOG[rule["type"]]
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "type": str(rule.get("type") or ""), "label": label, "status": "invalid",
            "actual": None, "expected": rule.get("value"), "reason": str(exc),
        }
    unavailable = actual is None or actual == []
    if unavailable:
        matched = False
        status = "unavailable"
        reason = "数据源未覆盖或当前时点字段缺失"
    elif meta.get("positive_only") and (_number(actual) is None or float(actual) <= 0):
        matched = False
        status = "failed"
        reason = "该指标必须为正数"
    else:
        matched = _compare(actual, rule["operator"], expected)
        status = "passed" if matched else "failed"
        reason = "满足规则" if matched else "未达到阈值"
    feature_meta = (stock.get("_feature_meta") or {}).get(meta.get("source_group")) or {}
    return {
        "type": rule["type"],
        "label": label,
        "status": status,
        "matched": matched,
        "actual": actual,
        "expected": expected,
        "reason": reason,
        "source": feature_meta.get("source") or meta.get("source"),
        "report_date": feature_meta.get("report_date"),
        "disclosed_at": feature_meta.get("disclosed_at"),
        "as_of": feature_meta.get("as_of"),
    }


def evaluate_rules_detailed(rules: list[dict], stock: dict, logic: str = "AND") -> dict:
    if not rules:
        return {"matched": True, "passed": [], "failed": [], "unavailable": [], "details": []}
    details = [evaluate_rule(rule, stock) for rule in rules]
    passed = [item["label"] for item in details if item["status"] == "passed"]
    failed = [item["label"] for item in details if item["status"] in {"failed", "invalid"}]
    unavailable = [item["label"] for item in details if item["status"] == "unavailable"]
    matched = not failed and not unavailable if logic.upper() == "AND" else bool(passed)
    return {
        "matched": matched,
        "passed": passed,
        "failed": failed,
        "unavailable": unavailable,
        "details": details,
    }


def evaluate_rules(rules: list[dict], stock: dict, logic: str = "AND") -> tuple[bool, list[str], list[str]]:
    result = evaluate_rules_detailed(rules, stock, logic)
    return result["matched"], result["passed"], result["failed"] + result["unavailable"]


def static_group_can_match(group: dict, stock: dict) -> bool:
    """Cheap prefilter where history-derived technical rules are unresolved."""
    rules = group.get("rules") or []
    static_rules = [rule for rule in rules if rule.get("type") not in TECHNICAL_RULE_TYPES]
    has_technical = len(static_rules) != len(rules)
    if not rules:
        return True
    result = evaluate_rules_detailed(static_rules, stock, group.get("logic", "AND"))
    if group.get("logic", "AND").upper() == "AND":
        return result["matched"]
    return result["matched"] or has_technical


def public_rule_catalog() -> list[dict]:
    return [{"type": key, **value} for key, value in RULE_CATALOG.items()]
