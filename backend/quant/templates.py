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


BUILTIN_STRATEGIES = [
    {
        "id": "strat_builtin_short_v1",
        "name": "短线强势确认策略",
        "description": "只在市场和行业同步偏强时，选择现金流为正且负债可控的非ST盈利股，用趋势、量能和主力资金共同确认，控制追高与解禁风险。",
        "builtin": True,
        "horizon": "short",
        "target_win_rate": [70, 90],
        "validation_note": "70%-90%仅为验证目标，需至少60笔完成交易、滚动样本外回测和模拟盘确认，不承诺收益。",
        "active": True,
        "scan_schedule": "daily",
        "filter": {"logic": "AND", "rules": [
            {"type": "market_cap", "operator": "between", "value": [50, 2000]},
            {"type": "pe_ttm", "operator": "between", "value": [1, 60]},
            {"type": "turnover", "operator": "between", "value": [2, 12]},
            {"type": "ocf_to_profit", "operator": "gt", "value": 0},
            {"type": "debt_ratio", "operator": "lt", "value": 70},
            {"type": "is_profitable", "operator": "eq", "value": True},
            {"type": "no_lockup_expiry", "operator": "gte", "value": 30},
            {"type": "market_breadth", "operator": "gt", "value": 1},
            {"type": "sector_strength", "operator": "lte", "value": 10},
        ]},
        "entry": {"logic": "AND", "rules": [
            {"type": "above_ma", "operator": "eq", "value": "MA5"},
            {"type": "above_ma", "operator": "eq", "value": "MA20"},
            {"type": "rsi", "operator": "between", "value": [50, 70]},
            {"type": "macd", "operator": "gt", "value": 0},
            {"type": "vol_ratio", "operator": "between", "value": [1.2, 3.5]},
            {"type": "main_inflow", "operator": "gt", "value": 0},
            {"type": "change_pct", "operator": "between", "value": [0.5, 5]},
        ]},
        "exit": {
            "stop_loss_pct": 4,
            "take_profit_pct": 8,
            "max_holding_days": 8,
            "rules": [{"type": "below_ma", "operator": "eq", "value": "MA10"}],
        },
        "position": {**DEFAULT_POSITION, "max_holdings": 4, "max_position_pct": 20},
    },
    {
        "id": "strat_builtin_long_v1",
        "name": "长期质量成长趋势策略",
        "description": "先筛选盈利质量、现金流、成长和估值均合格的公司，再用长期趋势和市场宽度确认入场。",
        "builtin": True,
        "horizon": "long",
        "target_win_rate": [70, 90],
        "validation_note": "70%-90%仅为验证目标，需跨牛熊周期、至少60笔完成交易并做滚动样本外验证，不承诺收益。",
        "active": True,
        "scan_schedule": "daily",
        "filter": {"logic": "AND", "rules": [
            {"type": "market_cap", "operator": "gt", "value": 100},
            {"type": "pe_ttm", "operator": "between", "value": [1, 35]},
            {"type": "pb", "operator": "between", "value": [0.01, 5]},
            {"type": "roe", "operator": "gte", "value": 12},
            {"type": "gross_margin", "operator": "gte", "value": 20},
            {"type": "revenue_growth", "operator": "gt", "value": 0},
            {"type": "deducted_profit_growth", "operator": "gt", "value": 0},
            {"type": "ocf_to_profit", "operator": "gte", "value": 0.8},
            {"type": "debt_ratio", "operator": "lt", "value": 60},
            {"type": "receivable_to_revenue", "operator": "lt", "value": 30},
            {"type": "is_profitable", "operator": "eq", "value": True},
            {"type": "no_lockup_expiry", "operator": "gte", "value": 60},
        ]},
        "entry": {"logic": "AND", "rules": [
            {"type": "above_ma", "operator": "eq", "value": "MA20"},
            {"type": "above_ma", "operator": "eq", "value": "MA60"},
            {"type": "rsi", "operator": "between", "value": [45, 68]},
            {"type": "macd", "operator": "gt", "value": 0},
            {"type": "market_breadth", "operator": "gt", "value": 1},
        ]},
        "exit": {
            "stop_loss_pct": 12,
            "take_profit_pct": 35,
            "max_holding_days": 180,
            "rules": [{"type": "below_ma", "operator": "eq", "value": "MA60"}],
        },
        "position": {**DEFAULT_POSITION, "max_holdings": 5, "max_position_pct": 20},
    },
]

TEMPLATES.extend(BUILTIN_STRATEGIES)


def list_templates() -> list[dict]:
    return copy.deepcopy(TEMPLATES)
