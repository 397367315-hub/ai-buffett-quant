"""Rule catalogue and deterministic evaluation for stock strategy filters."""

from __future__ import annotations

import math
from typing import Any


RULE_CATALOG: dict[str, dict] = {
    "turnover": {"label": "换手率", "value_type": "number", "unit": "%", "operators": ["gt", "gte", "lt", "lte", "between"], "default": 3},
    "vol_ratio": {"label": "量比", "value_type": "number", "unit": "", "operators": ["gt", "gte", "lt", "lte", "between"], "default": 1.2},
    "change_pct": {"label": "涨跌幅", "value_type": "number", "unit": "%", "operators": ["gt", "gte", "lt", "lte", "between"], "default": 2},
    "price": {"label": "最新价", "value_type": "number", "unit": "元", "operators": ["gt", "gte", "lt", "lte", "between"], "default": 10},
    "main_inflow": {"label": "主力净流入", "value_type": "number", "unit": "万元", "operators": ["gt", "gte", "lt", "lte", "between"], "default": 0},
    "pe_ttm": {"label": "PE(TTM)", "value_type": "number", "unit": "倍", "operators": ["gt", "gte", "lt", "lte", "between"], "default": 30, "positive_only": True},
    "pb": {"label": "PB", "value_type": "number", "unit": "倍", "operators": ["gt", "gte", "lt", "lte", "between"], "default": 3, "positive_only": True},
    "roe": {"label": "ROE", "value_type": "number", "unit": "%", "operators": ["gt", "gte", "lt", "lte", "between"], "default": 8},
    "market_cap": {"label": "总市值", "value_type": "number", "unit": "亿元", "operators": ["gt", "gte", "lt", "lte", "between"], "default": 500},
    "sector": {"label": "所属板块", "value_type": "multi-select", "unit": "", "operators": ["in", "not_in"], "default": []},
    "above_ma": {"label": "站上均线", "value_type": "select", "unit": "", "operators": ["eq", "ne"], "default": "MA20", "options": ["MA5", "MA10", "MA20", "MA60"]},
    "below_ma": {"label": "跌破均线", "value_type": "select", "unit": "", "operators": ["eq", "ne"], "default": "MA20", "options": ["MA5", "MA10", "MA20", "MA60"]},
    "below_fib": {"label": "斐波那契支撑", "value_type": "select", "unit": "", "operators": ["lt", "lte", "gt", "gte"], "default": 0.618, "options": [0.382, 0.5, 0.618, 0.786]},
    "rsi": {"label": "RSI(14)", "value_type": "number", "unit": "", "operators": ["gt", "gte", "lt", "lte", "between"], "default": 30},
    "macd": {"label": "MACD柱", "value_type": "number", "unit": "", "operators": ["gt", "gte", "lt", "lte", "between"], "default": 0},
    "long_lower_shadow": {"label": "长下影线", "value_type": "boolean", "unit": "", "operators": ["eq", "ne"], "default": True},
}

TECHNICAL_RULE_TYPES = {"above_ma", "below_ma", "below_fib", "rsi", "macd", "long_lower_shadow"}

FIELD_MAP = {
    "turnover": "turnover",
    "vol_ratio": "vol_ratio",
    "change_pct": "change_pct",
    "price": "price",
    "main_inflow": "main_inflow",
    "pe_ttm": "pe_ttm",
    "pb": "pb",
    "roe": "roe",
    "market_cap": "market_cap",
    "rsi": "rsi",
    "macd": "macd",
    "long_lower_shadow": "long_lower_shadow",
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


def evaluate_rules(rules: list[dict], stock: dict, logic: str = "AND") -> tuple[bool, list[str], list[str]]:
    if not rules:
        return True, [], []
    passed: list[str] = []
    failed: list[str] = []
    for rule in rules:
        label = describe_rule(rule)
        try:
            validate_rule(rule)
            actual, expected = _rule_actual(rule, stock)
            meta = RULE_CATALOG[rule["type"]]
            if meta.get("positive_only") and (_number(actual) is None or float(actual) <= 0):
                matched = False
            else:
                matched = _compare(actual, rule["operator"], expected)
        except (KeyError, TypeError, ValueError):
            matched = False
        (passed if matched else failed).append(label)
    result = not failed if logic.upper() == "AND" else bool(passed)
    return result, passed, failed


def static_group_can_match(group: dict, stock: dict) -> bool:
    """Cheap prefilter where technical rules are treated as unresolved."""
    rules = group.get("rules") or []
    static_rules = [rule for rule in rules if rule.get("type") not in TECHNICAL_RULE_TYPES]
    has_technical = len(static_rules) != len(rules)
    if not rules:
        return True
    if group.get("logic", "AND").upper() == "AND":
        return evaluate_rules(static_rules, stock, "AND")[0]
    return evaluate_rules(static_rules, stock, "OR")[0] or has_technical


def public_rule_catalog() -> list[dict]:
    return [{"type": key, **value} for key, value in RULE_CATALOG.items()]
