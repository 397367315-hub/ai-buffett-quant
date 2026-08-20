"""Unified V5 factor and causal-chain registry.

The registry is deliberately data-source agnostic.  Providers write observed
values into the same shape, while the forecast layer keeps the original
source, timestamps, TTL and quality visible to the user.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


FACTOR_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {"id": "market_breadth", "name": "市场宽度", "layer": "confirmation", "source": "MarketSentimentDaily/StockDailyBar", "source_level": "A", "ttl_minutes": 1440, "lead_score": 0.35, "chains": ["domestic_policy_risk_preference", "market_structure_transition"]},
    {"id": "market_amount_vs_ma20", "name": "全市场成交额相对20日均值", "layer": "confirmation", "source": "MarketSentimentDaily/StockDailyBar", "source_level": "A", "ttl_minutes": 1440, "lead_score": 0.4, "chains": ["domestic_policy_risk_preference", "market_structure_transition"]},
    {"id": "market_amount_percentile", "name": "全市场成交额历史分位", "layer": "confirmation", "source": "MarketSentimentDaily", "source_level": "A", "ttl_minutes": 1440, "lead_score": 0.4, "chains": ["market_structure_transition"]},
    {"id": "average_turnover", "name": "全市场平均换手率", "layer": "confirmation", "source": "MarketSentimentDaily/StockDailyBar", "source_level": "A", "ttl_minutes": 1440, "lead_score": 0.35, "chains": ["market_structure_transition"]},
    {"id": "failed_limit_rate", "name": "炸板率", "layer": "confirmation", "source": "MarketSentimentDaily", "source_level": "A", "ttl_minutes": 1440, "lead_score": 0.45, "chains": ["regulatory_short_sentiment", "market_structure_transition"]},
    {"id": "limit_up_down_balance", "name": "涨跌停结构", "layer": "confirmation", "source": "MarketSentimentDaily/StockDailyBar", "source_level": "A", "ttl_minutes": 1440, "lead_score": 0.4, "chains": ["regulatory_short_sentiment", "market_structure_transition"]},
    {"id": "market_state_score", "name": "V4市场状态评分", "layer": "confirmation", "source": "V4 evidence-bound workbench", "source_level": "A", "ttl_minutes": 60, "lead_score": 0.45, "chains": ["market_structure_transition"]},
    {"id": "structure_health", "name": "市场结构健康度", "layer": "propagation", "source": "V4 evidence-bound workbench", "source_level": "A", "ttl_minutes": 60, "lead_score": 0.55, "chains": ["market_structure_transition", "domestic_policy_risk_preference"]},
    {"id": "crowding_risk", "name": "高位拥挤风险", "layer": "propagation", "source": "V4 evidence-bound workbench", "source_level": "A", "ttl_minutes": 60, "lead_score": 0.65, "chains": ["long_rate_growth_valuation", "regulatory_short_sentiment", "market_structure_transition"]},
    {"id": "sector_flow_persistence", "name": "板块资金持续性", "layer": "propagation", "source": "IndustryFundFlowDaily", "source_level": "A", "ttl_minutes": 1440, "lead_score": 0.62, "chains": ["domestic_policy_risk_preference", "credit_cycle"]},
    {"id": "sector_breadth", "name": "板块扩散宽度", "layer": "propagation", "source": "IndustryFundFlowDaily", "source_level": "A", "ttl_minutes": 1440, "lead_score": 0.58, "chains": ["domestic_policy_risk_preference", "market_structure_transition"]},
    {"id": "alpha_density", "name": "Alpha候选密度", "layer": "propagation", "source": "V4 candidate/sector snapshots", "source_level": "A", "ttl_minutes": 60, "lead_score": 0.58, "chains": ["market_structure_transition"]},
    {"id": "northbound_flow", "name": "北向/互联互通资金", "layer": "leading", "source": "东方财富/缓存", "source_level": "A", "ttl_minutes": 1440, "lead_score": 0.7, "chains": ["currency_external_risk", "domestic_policy_risk_preference"]},
    {"id": "policy_support", "name": "国内政策边际", "layer": "leading", "source": "中国政府网/部委政策缓存", "source_level": "S", "ttl_minutes": 10080, "lead_score": 0.82, "chains": ["domestic_policy_risk_preference"]},
    {"id": "financial_pit_validation", "name": "PIT盈利验证", "layer": "leading", "source": "FinancialPITSnapshot/IndustryValidationSnapshot", "source_level": "A", "ttl_minutes": 10080, "lead_score": 0.75, "chains": ["credit_cycle", "industry_price_profit"]},
    {"id": "sp500_change", "name": "标普500隔夜变化", "layer": "leading", "source": "新浪财经全球行情", "source_level": "B", "ttl_minutes": 180, "lead_score": 0.63, "chains": ["long_rate_growth_valuation", "currency_external_risk"]},
    {"id": "nasdaq_change", "name": "纳斯达克隔夜变化", "layer": "leading", "source": "新浪财经全球行情", "source_level": "B", "ttl_minutes": 180, "lead_score": 0.7, "chains": ["long_rate_growth_valuation"]},
    {"id": "dxy_change", "name": "美元指数变化", "layer": "leading", "source": "新浪财经全球行情", "source_level": "B", "ttl_minutes": 180, "lead_score": 0.68, "chains": ["currency_external_risk", "long_rate_growth_valuation"]},
    {"id": "oil_change", "name": "原油变化", "layer": "leading", "source": "新浪财经全球行情", "source_level": "B", "ttl_minutes": 180, "lead_score": 0.6, "chains": ["oil_geopolitics_inflation", "industry_price_profit"]},
    {"id": "gold_change", "name": "黄金变化", "layer": "leading", "source": "新浪财经全球行情", "source_level": "B", "ttl_minutes": 180, "lead_score": 0.55, "chains": ["oil_geopolitics_inflation"]},
    {"id": "us10y_change", "name": "美国10年期收益率", "layer": "leading", "source": "FRED公开序列 DGS10", "source_level": "B", "ttl_minutes": 180, "lead_score": 0.9, "chains": ["long_rate_growth_valuation"]},
    {"id": "us2y_change", "name": "美国2年期收益率", "layer": "leading", "source": "FRED公开序列 DGS2", "source_level": "B", "ttl_minutes": 180, "lead_score": 0.82, "chains": ["long_rate_growth_valuation"]},
    {"id": "vix_change", "name": "VIX波动率", "layer": "leading", "source": "FRED公开序列 VIXCLS", "source_level": "B", "ttl_minutes": 180, "lead_score": 0.78, "chains": ["currency_external_risk", "oil_geopolitics_inflation"]},
    {"id": "credit_pulse", "name": "信用脉冲代理", "layer": "leading", "source": "商务部数据中心/中国人民银行社融增量", "source_level": "S", "ttl_minutes": 43200, "lead_score": 0.9, "chains": ["credit_cycle"]},
    {"id": "industry_price_signal", "name": "产业价格信号", "layer": "leading", "source": "东方财富企业商品价格指数", "source_level": "B", "ttl_minutes": 1440, "lead_score": 0.78, "chains": ["industry_price_profit"]},
    {"id": "capex_signal", "name": "宏观资本开支代理", "layer": "leading", "source": "东方财富/国家统计口径城镇固定资产投资（不等同上市公司CAPEX）", "source_level": "B", "ttl_minutes": 10080, "lead_score": 0.72, "chains": ["capex_equipment_orders"]},
    {"id": "fomo_behavior", "name": "疑似追涨行为", "layer": "propagation", "source": "A股宽度/成交/板块结构", "source_level": "A", "ttl_minutes": 60, "lead_score": 0.62, "chains": ["market_structure_transition", "regulatory_short_sentiment"]},
    {"id": "panic_behavior", "name": "恐慌行为扩散", "layer": "propagation", "source": "A股宽度/跌停/高位反馈", "source_level": "A", "ttl_minutes": 60, "lead_score": 0.68, "chains": ["market_structure_transition", "regulatory_short_sentiment"]},
    {"id": "false_breakout_risk", "name": "假突破风险", "layer": "propagation", "source": "价格/板块宽度/资金持续性", "source_level": "A", "ttl_minutes": 60, "lead_score": 0.64, "chains": ["market_structure_transition"]},
    {"id": "behavior_imbalance", "name": "行为失衡度", "layer": "confirmation", "source": "市场宽度/涨跌停/换手/Alpha", "source_level": "A", "ttl_minutes": 60, "lead_score": 0.6, "chains": ["market_structure_transition"]},
)


CAUSAL_CHAINS: tuple[dict[str, Any], ...] = (
    {"id": "long_rate_growth_valuation", "name": "长端利率→高估值科技", "direction": "defensive", "description": "贴现率变化通过海外科技、拥挤度和A股高位反馈传导。", "factor_ids": ["us10y_change", "us2y_change", "nasdaq_change", "sp500_change", "crowding_risk"], "nodes": ["长端利率", "贴现率", "海外科技", "A股科技拥挤", "高位反馈"], "beneficiaries": ["低估值", "防御", "现金流稳定"], "pressured": ["高估值科技", "高Beta成长"]},
    {"id": "oil_geopolitics_inflation", "name": "油价/地缘→通胀与成长估值", "direction": "defensive", "description": "油价和避险变化同时影响通胀预期、成长估值与能源盈利。", "factor_ids": ["oil_change", "gold_change", "vix_change"], "nodes": ["油价/地缘", "通胀预期", "全球利率压力", "成长估值", "能源盈利"], "beneficiaries": ["能源", "黄金"], "pressured": ["高久期成长", "航空物流"]},
    {"id": "domestic_policy_risk_preference", "name": "国内强政策→风险偏好修复", "direction": "offensive", "description": "政策边际、成交和宽度同步改善才视为传导，而不是把政策标题直接当信号。", "factor_ids": ["policy_support", "northbound_flow", "market_breadth", "market_amount_vs_ma20", "sector_breadth", "structure_health"], "nodes": ["政策边际", "流动性/财政", "风险偏好", "成交放大", "板块扩散"], "beneficiaries": ["宽基ETF", "券商", "政策产业链"], "pressured": ["无持续承接的后排题材"]},
    {"id": "regulatory_short_sentiment", "name": "监管变化→超短情绪", "direction": "defensive", "description": "监管或高位负反馈改变接力的风险收益边界。", "factor_ids": ["failed_limit_rate", "limit_up_down_balance", "crowding_risk"], "nodes": ["监管/交易规则", "接力意愿", "连板晋级", "高位负反馈", "流动性折价"], "beneficiaries": ["低波动", "防御"], "pressured": ["高位题材", "纯情绪接力"]},
    {"id": "credit_cycle", "name": "信用扩张→周期/金融", "direction": "offensive", "description": "信用边际改善需要行业资金和盈利PIT进一步确认。", "factor_ids": ["credit_pulse", "financial_pit_validation", "sector_flow_persistence", "market_amount_vs_ma20"], "nodes": ["社融/信贷", "信用脉冲", "需求预期", "周期金融", "盈利验证"], "beneficiaries": ["金融", "工业", "原材料"], "pressured": ["信用收缩敏感方向"]},
    {"id": "currency_external_risk", "name": "汇率→外部风险与产业", "direction": "defensive", "description": "汇率变化同时影响风险偏好、进口成本与出口收入，必须结合行业属性。", "factor_ids": ["dxy_change", "northbound_flow", "sp500_change", "vix_change"], "nodes": ["美元/人民币", "外部风险偏好", "人民币资产估值", "出口/进口成本"], "beneficiaries": ["出口链", "低外债"], "pressured": ["进口成本敏感", "外资偏好高估值"]},
    {"id": "industry_price_profit", "name": "产业价格→企业盈利", "direction": "offensive", "description": "产品价格改善只有经过盈利预期和板块相对强度才形成有效传导。", "factor_ids": ["industry_price_signal", "financial_pit_validation", "sector_flow_persistence"], "nodes": ["产品价格", "单位利润", "盈利预测", "机构关注", "板块Alpha"], "beneficiaries": ["上游资源", "供需改善行业"], "pressured": ["成本转嫁失败的下游"]},
    {"id": "capex_equipment_orders", "name": "产业资本开支→设备订单", "direction": "offensive", "description": "资本开支信号需要财报PIT和板块资金共同验证。", "factor_ids": ["capex_signal", "financial_pit_validation", "sector_flow_persistence"], "nodes": ["产业CAPEX", "设备采购", "上游订单", "收入", "利润"], "beneficiaries": ["设备", "零部件", "材料"], "pressured": ["资本开支下行的供应商"]},
    {"id": "market_structure_transition", "name": "市场结构量变→阶段切换", "direction": "state", "description": "成交、宽度、炸板率、拥挤和结构健康的持续变化用于识别量变是否接近质变。", "factor_ids": ["market_breadth", "market_amount_vs_ma20", "failed_limit_rate", "crowding_risk", "structure_health", "alpha_density"], "nodes": ["量能/宽度", "持续累积", "结构临界点", "阶段切换", "策略调整"], "beneficiaries": ["与当前阶段匹配的主线"], "pressured": ["逆阶段追涨"]},
)


def factor_definitions() -> list[dict[str, Any]]:
    return [deepcopy(item) for item in FACTOR_DEFINITIONS]


def causal_chains() -> list[dict[str, Any]]:
    return [deepcopy(item) for item in CAUSAL_CHAINS]


def factor_definition(factor_id: str) -> dict[str, Any] | None:
    return next((deepcopy(item) for item in FACTOR_DEFINITIONS if item["id"] == factor_id), None)


def causal_chain(chain_id: str) -> dict[str, Any] | None:
    return next((deepcopy(item) for item in CAUSAL_CHAINS if item["id"] == chain_id), None)
