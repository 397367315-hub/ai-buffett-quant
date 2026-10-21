"""版本化本地指标字典（02 §13.6 / DATA-006）。

每个 metric_id 记录：维度、单位、分母政策、窗口、公式、数据要求、舍入与适用范围、是否含 ST。
供应商 pct_chg 是百分数，内部 ratio 只除 100 一次；字典变动必须升 METRIC_DICTIONARY_VERSION，
并进入快照与规则指纹，不能用同名指标暗换定义。
"""
from __future__ import annotations

from .universe import CANDIDATE_POLICY, ENVIRONMENT_POLICY

METRIC_DICTIONARY_VERSION = "metric_dict_v1"

INDICES = [("000001.SH", "上证指数"), ("399001.SZ", "深证成指"), ("399006.SZ", "创业板指"),
           ("000300.SH", "沪深300"), ("000852.SH", "中证1000")]


def _m(metric_id, dimension, title, unit, formula, *, denominator=None, window="1 session", includes_st=None,
       data=None, scope="market", note=""):
    return {"metric_id": metric_id, "dimension": dimension, "title": title, "unit": unit, "formula": formula,
            "denominator_policy": denominator, "window": window, "includes_st": includes_st,
            "data_requirements": data or ["daily_bar"], "rounding": "ratio 12 位 HALF_UP；计数为整数",
            "scope": scope, "note": note, "dictionary_version": METRIC_DICTIONARY_VERSION}


METRICS = {m["metric_id"]: m for m in [
    # 指数趋势（每个指数一行，symbol=指数代码；不进入个股候选）
    _m("index_pct_chg", "index_trend", "指数当日涨跌", "ratio", "close / pre_close − 1",
       data=["index_daily"], scope="per_index"),
    _m("index_ret_20", "index_trend", "指数 20 日涨跌", "ratio", "close / close[−20] − 1", window="20 sessions",
       data=["index_daily"], scope="per_index"),
    _m("index_close_to_ma20", "index_trend", "指数相对 20 日均线", "multiple", "close / mean(close, 20)",
       window="20 sessions", data=["index_daily"], scope="per_index"),
    # 宽度（环境分母）
    _m("breadth_member_count", "breadth", "环境样本成员数", "count", "当日有效成员数", denominator=ENVIRONMENT_POLICY,
       includes_st=True),
    _m("breadth_quoted_count", "breadth", "有有效报价的成员数", "count", "成员中 close 与 pre_close 均有值",
       denominator=ENVIRONMENT_POLICY, includes_st=True),
    _m("breadth_quote_coverage", "breadth", "报价覆盖率", "ratio", "quoted / members", denominator=ENVIRONMENT_POLICY,
       includes_st=True),
    _m("breadth_up_count", "breadth", "上涨家数", "count", "close > pre_close", denominator=ENVIRONMENT_POLICY,
       includes_st=True),
    _m("breadth_down_count", "breadth", "下跌家数", "count", "close < pre_close", denominator=ENVIRONMENT_POLICY,
       includes_st=True),
    _m("breadth_up_ratio", "breadth", "上涨占比", "ratio", "up / quoted", denominator=ENVIRONMENT_POLICY,
       includes_st=True),
    _m("breadth_median_pct_chg", "breadth", "涨跌幅中位数", "ratio", "median(close / pre_close − 1)",
       denominator=ENVIRONMENT_POLICY, includes_st=True),
    # 涨跌停与连板生态（不含 ST；ST 单列）
    _m("limit_up_count", "limit_ecology", "涨停家数（非 ST）", "count", "close = 涨停价",
       denominator=ENVIRONMENT_POLICY, includes_st=False, data=["daily_bar", "price_limit"]),
    _m("limit_down_count", "limit_ecology", "跌停家数（非 ST）", "count", "close = 跌停价",
       denominator=ENVIRONMENT_POLICY, includes_st=False, data=["daily_bar", "price_limit"]),
    _m("broken_board_count", "limit_ecology", "炸板家数（非 ST）", "count", "high = 涨停价 且 close < 涨停价",
       denominator=ENVIRONMENT_POLICY, includes_st=False, data=["daily_bar", "price_limit"]),
    _m("broken_board_ratio", "limit_ecology", "炸板率", "ratio", "broken / (limit_up + broken)",
       denominator=ENVIRONMENT_POLICY, includes_st=False, data=["daily_bar", "price_limit"]),
    _m("st_limit_up_count", "limit_ecology", "ST 涨停家数（单列）", "count", "ST 且 close = 涨停价",
       denominator=ENVIRONMENT_POLICY, includes_st=True, data=["daily_bar", "price_limit", "st_status"]),
    _m("max_consecutive_boards", "limit_ecology", "最高连板（连续交易日）", "count",
       "按连续交易日的连续涨停计数；停牌日不计入也不中断判定（停牌期间单列）",
       window="up to 20 sessions", denominator=ENVIRONMENT_POLICY, includes_st=False,
       data=["daily_history", "price_limit"],
       note="不使用供应商累计板数认定连续晋级"),
    _m("consecutive_board_count", "limit_ecology", "连板家数（≥2 板）", "count", "连续涨停 ≥ 2 的家数",
       window="up to 20 sessions", denominator=ENVIRONMENT_POLICY, includes_st=False,
       data=["daily_history", "price_limit"]),
    _m("prev_limit_up_member_count", "limit_ecology", "昨日涨停组成员数", "count",
       "上一完整交易日涨停（非 ST）的成员，在上一交易日固定", window="2 sessions",
       denominator=ENVIRONMENT_POLICY, includes_st=False, data=["daily_history", "price_limit"]),
    _m("prev_limit_up_quoted_count", "limit_ecology", "昨日涨停组今日有报价数", "count",
       "成员中今日有有效报价的个数；停牌/无报价计入缺失", window="2 sessions",
       denominator=ENVIRONMENT_POLICY, includes_st=False),
    _m("prev_limit_up_followthrough", "limit_ecology", "昨日涨停组今日平均涨跌", "ratio",
       "mean(今日 close / pre_close − 1) over 有报价成员；无报价成员不补 0", window="2 sessions",
       denominator=ENVIRONMENT_POLICY, includes_st=False,
       note="成员不因今日停牌、低流动性或表现差而事后重选"),
    # 题材扩散（行业代理，不是概念成员）
    _m("limit_up_industry_count", "theme_diffusion", "有涨停的行业数（行业代理）", "count",
       "按 stockbasic.industry 分组后有 ≥1 只涨停的行业数", denominator=ENVIRONMENT_POLICY, includes_st=False,
       data=["daily_bar", "price_limit", "industry_map"], note="行业代理，不是概念成员；涨停名单不代表题材全体"),
    _m("top_industry_limit_up_share", "theme_diffusion", "涨停最多行业的占比", "ratio",
       "该行业涨停数 / 全部涨停数", denominator=ENVIRONMENT_POLICY, includes_st=False,
       data=["daily_bar", "price_limit", "industry_map"]),
    _m("top_industry_member_up_ratio", "theme_diffusion", "涨停最多行业的全体成员上涨占比", "ratio",
       "该行业全部成员中上涨的比例（完整成员，非只看强股簇）", denominator=ENVIRONMENT_POLICY, includes_st=True,
       data=["daily_bar", "industry_map"], note="与涨停股簇分开计算，避免把强股簇当成整个题材"),
    # 流动性
    _m("total_amount_minor", "liquidity", "环境样本成交额", "money_minor", "sum(amount)",
       denominator=ENVIRONMENT_POLICY, includes_st=True),
    _m("amount_vs_20d_avg", "liquidity", "成交额 / 20 日均值", "multiple", "today / mean(前 20 个交易日)",
       window="21 sessions", denominator=ENVIRONMENT_POLICY, includes_st=True, data=["daily_history"]),
    _m("active_amount_count", "liquidity", "成交额 ≥ 1 亿元的家数", "count", "amount ≥ 100,000,000 元",
       denominator=ENVIRONMENT_POLICY, includes_st=True,
       note="只是计数；不得用它相对前日的比例判断日线是否完整"),
    # 波动与拥挤
    _m("abs_pct_chg_median", "volatility", "绝对涨跌幅中位数", "ratio", "median(|close / pre_close − 1|)",
       denominator=ENVIRONMENT_POLICY, includes_st=True),
    _m("big_move_share", "volatility", "大幅波动占比（|涨跌| ≥ 5%）", "ratio", "count(|r| ≥ 0.05) / quoted",
       denominator=ENVIRONMENT_POLICY, includes_st=True),
    _m("high_turnover_share", "volatility", "高换手占比（换手 ≥ 10%）", "ratio",
       "count(turnover ≥ 10%) / 有换手数据的成员", denominator=ENVIRONMENT_POLICY, includes_st=True,
       data=["valuation"]),
    # 候选池覆盖（与环境分母分开）
    _m("candidate_member_count", "coverage", "候选池成员数（沪深主板非 ST）", "count", "当日有效成员数",
       denominator=CANDIDATE_POLICY, includes_st=False),
    _m("candidate_quoted_count", "coverage", "候选池有报价数", "count", "成员中有有效报价的个数",
       denominator=CANDIDATE_POLICY, includes_st=False),
]}

DIMENSIONS = [
    ("index_trend", "指数趋势"), ("breadth", "市场宽度"), ("limit_ecology", "涨跌停与连板生态"),
    ("theme_diffusion", "题材扩散（行业代理）"), ("liquidity", "成交与流动性"), ("volatility", "波动与拥挤"),
    ("coverage", "数据覆盖"),
]
