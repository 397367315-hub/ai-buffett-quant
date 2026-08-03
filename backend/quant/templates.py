"""Auditable starter strategies from the quantitative module PRD."""

from __future__ import annotations

import copy


DEFAULT_POSITION = {
    "method": "equal_weight",
    "max_holdings": 5,
    "max_position_pct": 20,
    "fixed_amount": None,
}

TEMPLATES = [
    {
        "id": "tpl_liang_value",
        "name": "梁文锋式价值策略",
        "description": "估值、盈利质量、现金流和财务排雷同时通过，再等待趋势与资金确认。负债率使用通用60%阈值，金融行业应另设策略。",
        "active": True,
        "scan_schedule": "daily",
        "filter": {"logic": "AND", "rules": [
            {"type": "pe_ttm", "operator": "between", "value": [1, 30]},
            {"type": "pb", "operator": "between", "value": [0.01, 2]},
            {"type": "roe", "operator": "gte", "value": 15},
            {"type": "gross_margin", "operator": "gte", "value": 15},
            {"type": "revenue_growth", "operator": "gt", "value": 0},
            {"type": "deducted_profit_growth", "operator": "gt", "value": 0},
            {"type": "ocf_to_profit", "operator": "gte", "value": 0.8},
            {"type": "debt_ratio", "operator": "lt", "value": 60},
            {"type": "receivable_to_revenue", "operator": "lt", "value": 30},
            {"type": "sector", "operator": "in", "value": ["电力", "光伏设备", "风电设备"]},
            {"type": "is_profitable", "operator": "eq", "value": True},
        ]},
        "entry": {"logic": "AND", "rules": [
            {"type": "main_inflow", "operator": "gt", "value": 0},
            {"type": "above_ma", "operator": "eq", "value": "MA20"},
            {"type": "change_pct", "operator": "gt", "value": 1},
            {"type": "macd_golden_cross", "operator": "eq", "value": True},
            {"type": "market_breadth", "operator": "gt", "value": 1},
        ]},
        "exit": {"stop_loss_pct": 5, "take_profit_pct": 15, "max_holding_days": 30, "rules": []},
        "position": DEFAULT_POSITION,
    },
    {
        "id": "tpl_trend",
        "name": "趋势跟随策略",
        "description": "只在大盘与行业同步偏强时，选择基本质量合格且放量形成MACD金叉的股票。",
        "active": True,
        "scan_schedule": "daily",
        "filter": {"logic": "AND", "rules": [
            {"type": "market_cap", "operator": "gt", "value": 100},
            {"type": "turnover", "operator": "gt", "value": 1},
            {"type": "roe", "operator": "gt", "value": 10},
            {"type": "gross_margin", "operator": "gt", "value": 15},
            {"type": "sector_strength", "operator": "lte", "value": 5},
            {"type": "market_breadth", "operator": "gt", "value": 1},
            {"type": "is_profitable", "operator": "eq", "value": True},
        ]},
        "entry": {"logic": "AND", "rules": [
            {"type": "above_ma", "operator": "eq", "value": "MA5"},
            {"type": "above_ma", "operator": "eq", "value": "MA20"},
            {"type": "macd_golden_cross", "operator": "eq", "value": True},
            {"type": "vol_ratio", "operator": "gt", "value": 1.5},
            {"type": "main_inflow", "operator": "gt", "value": 0},
        ]},
        "exit": {"stop_loss_pct": 8, "take_profit_pct": 20, "max_holding_days": 25, "rules": [
            {"type": "below_ma", "operator": "eq", "value": "MA10"},
        ]},
        "position": DEFAULT_POSITION,
    },
    {
        "id": "tpl_rebound",
        "name": "超跌反弹策略",
        "description": "在小市值、现金流为正且近期无解禁压力的股票中，寻找支撑位长下影与放量修复。",
        "active": True,
        "scan_schedule": "daily",
        "filter": {"logic": "AND", "rules": [
            {"type": "market_cap", "operator": "lt", "value": 200},
            {"type": "below_fib", "operator": "lt", "value": 0.786},
            {"type": "roe", "operator": "gt", "value": 6},
            {"type": "ocf_to_profit", "operator": "gt", "value": 0},
            {"type": "no_lockup_expiry", "operator": "gt", "value": 7},
            {"type": "is_profitable", "operator": "eq", "value": True},
        ]},
        "entry": {"logic": "AND", "rules": [
            {"type": "long_lower_shadow", "operator": "eq", "value": True},
            {"type": "vol_ratio", "operator": "gt", "value": 1.2},
            {"type": "sector_strength", "operator": "lte", "value": 10},
        ]},
        "exit": {"stop_loss_pct": 5, "take_profit_pct": 10, "max_holding_days": 3, "rules": []},
        "position": {**DEFAULT_POSITION, "max_holdings": 4},
    },
    {
        "id": "tpl_financial_safety",
        "name": "财务排雷策略",
        "description": "纯基本面质量筛选。财务字段均按最新已披露报告取值，历史回测会标为研究级而非严格点时回测。",
        "active": True,
        "scan_schedule": "daily",
        "filter": {"logic": "AND", "rules": [
            {"type": "roe", "operator": "gt", "value": 15},
            {"type": "gross_margin", "operator": "gt", "value": 20},
            {"type": "revenue_growth", "operator": "gt", "value": 5},
            {"type": "deducted_profit_growth", "operator": "gt", "value": 5},
            {"type": "ocf_to_profit", "operator": "gt", "value": 1},
            {"type": "debt_ratio", "operator": "lt", "value": 50},
            {"type": "receivable_to_revenue", "operator": "lt", "value": 20},
            {"type": "pe_ttm", "operator": "between", "value": [1, 30]},
            {"type": "holder_decline_streak", "operator": "gte", "value": 2},
        ]},
        "entry": {"logic": "AND", "rules": [
            {"type": "is_profitable", "operator": "eq", "value": True},
        ]},
        "exit": {"stop_loss_pct": 10, "take_profit_pct": 30, "max_holding_days": 120, "rules": [
            {"type": "gross_margin", "operator": "lt", "value": 15},
            {"type": "deducted_profit_growth", "operator": "lt", "value": 0},
            {"type": "ocf_to_profit", "operator": "lt", "value": 0},
        ]},
        "position": DEFAULT_POSITION,
    },
]


def list_templates() -> list[dict]:
    return copy.deepcopy(TEMPLATES)
