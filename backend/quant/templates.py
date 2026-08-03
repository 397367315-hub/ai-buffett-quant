"""Auditable starter strategies exposed by the visual strategy builder."""

from __future__ import annotations

import copy


TEMPLATES = [
    {
        "id": "tpl_value",
        "name": "低估值质量策略",
        "description": "盈利、估值和资金参与同时满足后等待放量确认。",
        "active": True,
        "scan_schedule": "daily",
        "filter": {"logic": "AND", "rules": [
            {"type": "pe_ttm", "operator": "between", "value": [1, 25]},
            {"type": "pb", "operator": "lte", "value": 2.5},
            {"type": "roe", "operator": "gte", "value": 8},
            {"type": "market_cap", "operator": "lte", "value": 800},
        ]},
        "entry": {"logic": "AND", "rules": [
            {"type": "turnover", "operator": "between", "value": [1, 8]},
            {"type": "vol_ratio", "operator": "gte", "value": 1.2},
            {"type": "main_inflow", "operator": "gt", "value": 0},
        ]},
        "exit": {"stop_loss_pct": 6, "take_profit_pct": 18, "max_holding_days": 30, "rules": []},
        "position": {"method": "equal_weight", "max_holdings": 5, "max_position_pct": 20, "fixed_amount": None},
    },
    {
        "id": "tpl_trend",
        "name": "趋势跟随策略",
        "description": "在可交易的涨幅区间内寻找放量且站上中期均线的股票。",
        "active": True,
        "scan_schedule": "daily",
        "filter": {"logic": "AND", "rules": [
            {"type": "turnover", "operator": "between", "value": [2, 15]},
            {"type": "change_pct", "operator": "between", "value": [1, 7]},
            {"type": "vol_ratio", "operator": "gte", "value": 1.3},
        ]},
        "entry": {"logic": "AND", "rules": [
            {"type": "above_ma", "operator": "eq", "value": "MA20"},
            {"type": "macd", "operator": "gt", "value": 0},
            {"type": "main_inflow", "operator": "gt", "value": 0},
        ]},
        "exit": {"stop_loss_pct": 5, "take_profit_pct": 15, "max_holding_days": 20, "rules": [
            {"type": "below_ma", "operator": "eq", "value": "MA20"},
        ]},
        "position": {"method": "equal_weight", "max_holdings": 5, "max_position_pct": 20, "fixed_amount": None},
    },
    {
        "id": "tpl_rebound",
        "name": "超跌反弹策略",
        "description": "从非亏损、流动性正常的股票中寻找 RSI 超卖和斐波那契支撑。",
        "active": True,
        "scan_schedule": "daily",
        "filter": {"logic": "AND", "rules": [
            {"type": "pe_ttm", "operator": "between", "value": [1, 80]},
            {"type": "turnover", "operator": "between", "value": [1, 12]},
            {"type": "change_pct", "operator": "between", "value": [-8, 1]},
        ]},
        "entry": {"logic": "AND", "rules": [
            {"type": "rsi", "operator": "lte", "value": 35},
            {"type": "below_fib", "operator": "lte", "value": 0.618},
            {"type": "long_lower_shadow", "operator": "eq", "value": True},
        ]},
        "exit": {"stop_loss_pct": 5, "take_profit_pct": 12, "max_holding_days": 15, "rules": []},
        "position": {"method": "equal_weight", "max_holdings": 4, "max_position_pct": 20, "fixed_amount": None},
    },
]


def list_templates() -> list[dict]:
    return copy.deepcopy(TEMPLATES)
