"""Locked terminology and registry for the three-book decision system.

The names below are BOOK_RULE names.  Numerical thresholds used by the
detectors live in the engine configuration and are explicitly labelled
ENGINE_FEATURE; they are not presented as rules from the books.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from sqlalchemy import select

from database import async_session
from models import BookSkillRegistry, SkillRuleDefinition


BOOK_RULE_VERSION = "three-books-v1.0"
V2_BOOK_RULE_VERSION = "three-books-v2.0"
ENGINE_VERSION = "STRONG_STOCK_DECISION_V2"
SIGNAL_STATUSES = ("NOT_FOUND", "POSSIBLE", "FORMING", "CONFIRMED", "WEAKENING", "INVALID")
ACTIONS = ("NO_TRADE", "WATCH", "READY", "CONFIRMING", "HOLD", "RISK", "EXIT")

STATE_LABELS = {
    "S0": "无明显机会", "S1": "风险观察", "S2": "量时空出现机会",
    "S3": "量形态出现主力身影", "S4": "量价异动", "S5": "均线归位",
    "S6": "强势A区", "S7": "强势B区", "S8": "大形态构建",
    "S9": "蓄势之星", "S10": "调整之星", "S11": "止跌之星",
    "S12": "补仓之星", "S13": "攻击之星", "S14": "强势运行",
    "S15": "风险C区", "S16": "明显遇顶", "S17": "明显见顶", "S18": "退出",
}


def _definition(
    skill_id: str,
    book: str,
    chapter: str,
    name: str,
    description: str,
    required: list[str],
    *,
    section: str | None = None,
    annotations: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "skill_id": skill_id,
        "book": book,
        "chapter": chapter,
        "section": section or name,
        "original_name": name,
        "description": description,
        "required_features": required,
        "prerequisite": ["point_in_time_daily_bars", "valid_ohlcv"],
        "positive_evidence": ["符合书内结构名称的可观测价格、成交和位置证据"],
        "negative_evidence": ["结构缺失、数据不足或风险信号优先"],
        "invalidation": ["关键结构失守", "后续确认失败", "数据截面不完整"],
        "chart_annotations": annotations or [name],
        "book_rule_version": BOOK_RULE_VERSION,
        "enabled": True,
    }


BOOK_SKILL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    _definition("HQS_001", "猎取强势股", "量时空", "量时空提供机会", "量、时间和价格空间共同形成可观察机会。", ["volume", "time_phase", "price_space"]),
    _definition("HQS_002", "猎取强势股", "量时空", "量时空大压风险", "量价位置和时间阶段共同显示压力扩大的风险。", ["volume", "time_phase", "price_space", "volatility"]),
    _definition("HQS_003", "猎取强势股", "量形态", "量形态选股", "以成交量形态筛查候选，不把单日放量当作结论。", ["volume_sequence", "volume_ratio", "price_response"]),
    _definition("HQS_004", "猎取强势股", "量形态", "量行为跟随主力", "观察成交、价格反馈和承接是否持续符合跟随主力的条件。", ["up_down_volume", "pullback_volume", "price_retention"], annotations=["主力身影"]),
    _definition("HQS_005", "猎取强势股", "量价", "量价异动", "识别量先、价先或量价同步异动及其持续性。", ["volume_event", "price_event", "event_persistence"]),
    _definition("HQS_006", "猎取强势股", "均线", "均线归位", "观察均线方向、位置、聚合和展开。", ["ma5", "ma10", "ma20", "ma60"]),
    _definition("HQS_007", "猎取强势股", "量价均线", "量价异动让均线归位", "解释量价异动推动股价重心和均线发生改变的事件链。", ["volume_event", "ma_slope", "price_ma_distance"]),
    _definition("HQS_008", "猎取强势股", "最佳交易区", "强势A区", "趋势、量价、主力和结构共同支持的强势交易区。", ["trend", "volume", "ma_alignment", "main_force"]),
    _definition("HQS_009", "猎取强势股", "最佳交易区", "强势B区", "强势结构中的调整、重新转强或待确认区域。", ["trend", "pullback", "support", "ma_alignment"]),
    _definition("HQS_010", "猎取强势股", "最佳交易区", "风险C区", "结构、位置或卖出风险占主导的区域。", ["drawdown", "resistance", "pressure", "top_risk"]),
    _definition("HQS_011", "猎取强势股", "经典盈利模式", "强势结点盈利模式", "检测强势结构中的结点形成及后续确认。", ["trend", "zone", "volume", "support"]),
    _definition("HQS_012", "猎取强势股", "经典盈利模式", "单日强硬洗盘盈利模式", "检测单日强硬回撤后关键结构是否保留和修复。", ["prior_strength", "one_day_pullback", "volume", "recovery"]),
    _definition("HQS_013", "猎取强势股", "经典盈利模式", "缺口盈利模式", "检测缺口位置、成交、承接、回补和失效。", ["gap", "gap_volume", "gap_retention"]),
    _definition("HQS_014", "猎取强势股", "经典盈利模式", "强势循环低点盈利模式", "检测强势趋势中的回调、循环低点和下一轮攻击。", ["trend", "pullback", "cycle_low", "next_attack"]),
    _definition("HQS_015", "猎取强势股", "卖对股票", "明显见顶卖出策略", "观察明显见顶结构并优先进入风险层。", ["top_structure", "price_failure", "volume_pressure"]),
    _definition("HQS_016", "猎取强势股", "卖对股票", "明显遇顶卖出策略", "观察遇阻、冲高回落和承接恶化。", ["resistance", "upper_wick", "relative_weakness"]),
    _definition("HQS_017", "猎取强势股", "卖对股票", "C区卖出策略", "风险C区成立时给出退出观察条件。", ["risk_zone", "invalidation", "pressure"]),
    _definition("HQS_018", "猎取强势股", "量能体叠加术", "量能体叠加术", "汇总量时空、主力、量价、均线、区间、形态和风险证据。", ["all_engine_signals"]),
    _definition("HQS_019", "猎取强势股", "量能体叠加术", "量能体叠加与题材互证", "将量能体与板块、题材、事件和政策进行独立互证。", ["volume_energy", "sector", "theme", "event"]),
    _definition("HQS_020", "猎取强势股", "案例", "学习经典案例及交易策略", "将正例、反例和形似失败案例纳入望星空对照。", ["case_library", "feature_snapshot", "outcome"]),
    _definition("BXDT_001", "暴涨大形态", "成交量形态", "三阳控三阴", "综合相对量、价格反馈和前后连续性识别三阳控三阴。", ["three_positive_bars", "three_negative_bars", "volume_feedback"], annotations=["三阳控三阴"]),
    _definition("BXDT_002", "暴涨大形态", "均线形态", "均线形态", "识别均线构建、形成、强化和破坏。", ["ma_alignment", "ma_slope", "ma_spread"], annotations=["均线形态"]),
    _definition("BXDT_003", "暴涨大形态", "三角形形态", "三角形形态", "识别边界、高低点变化、收敛和突破有效性。", ["swing_highs", "swing_lows", "convergence", "breakout"], annotations=["三角形"]),
    _definition("BXDT_004", "暴涨大形态", "箱体形态", "箱体形态", "识别箱体边界、持续时间、触碰、突破和假突破。", ["range_high", "range_low", "range_duration", "breakout"], annotations=["箱体"]),
    _definition("BXDT_005", "暴涨大形态", "颈位形态", "颈位形态", "识别颈位价格、攻击、突破、回踩和失效。", ["neckline", "attacks", "retest", "invalidation"], annotations=["颈位"]),
    _definition("BXDT_006", "暴涨大形态", "顺上形态", "顺上形态", "均线、高低点、成交和趋势连续性共同向上。", ["higher_highs", "higher_lows", "ma_alignment", "volume"], annotations=["顺上"]),
    _definition("BXDT_007", "暴涨大形态", "底部形态", "趋势底部", "判断趋势底部是否形成，不自动等价于可以买入。", ["downtrend", "base", "reclaim", "follow_through"], annotations=["趋势底部"]),
    _definition("BXDT_008", "暴涨大形态", "底部形态", "资金底部", "判断资金与价格反馈是否形成底部证据。", ["flow", "volume", "price_base", "breadth"], annotations=["资金底部"]),
    _definition("BXDT_009", "暴涨大形态", "高级形态", "三度行大道", "登记并识别多段结构推进，进入历史案例和Shadow观察。", ["multi_leg_structure", "case_match"]),
    _definition("BXDT_010", "暴涨大形态", "高级形态", "巅峰超越", "登记高级形态候选和历史标注，未经回测不参与总决策。", ["breakout", "prior_peak", "case_match"]),
    _definition("BXZX_001", "暴涨之星", "蓄势之星", "诱空蓄势星线", "识别蓄势阶段的诱空蓄势结构。", ["base", "down_probe", "reclaim", "volume"], annotations=["诱空蓄势"]),
    _definition("BXZX_002", "暴涨之星", "蓄势之星", "逼空蓄势星线", "识别蓄势阶段的逼空蓄势结构。", ["base", "up_pressure", "close_strength", "volume"], annotations=["逼空蓄势"]),
    _definition("BXZX_003", "暴涨之星", "调整之星", "缓冲调整星线", "识别强势结构中的缓冲调整。", ["prior_strength", "controlled_pullback", "support"], annotations=["缓冲调整"]),
    _definition("BXZX_004", "暴涨之星", "调整之星", "震荡调整星线", "识别强势结构中的震荡调整。", ["prior_strength", "range", "support", "volume_contraction"], annotations=["震荡调整"]),
    _definition("BXZX_005", "暴涨之星", "止跌之星", "同步止跌星线", "个股、指数和板块同步止跌的结构。", ["stock_reclaim", "market_reclaim", "sector_reclaim"], annotations=["同步止跌"]),
    _definition("BXZX_006", "暴涨之星", "止跌之星", "背离止跌星线", "个股相对指数或板块出现可观察的背离止跌。", ["relative_strength", "low_recovery", "sector_compare"], annotations=["背离止跌"]),
    _definition("BXZX_007", "暴涨之星", "补仓之星", "借势补仓星线", "识别借势补仓星线；只展示条件，不自动执行补仓。", ["strong_trend", "pullback", "trend_reclaim"], annotations=["借势补仓"]),
    _definition("BXZX_008", "暴涨之星", "补仓之星", "借风补仓星线", "识别借风补仓星线；只展示条件，不自动执行补仓。", ["sector_wind", "stock_reclaim", "volume"], annotations=["借风补仓"]),
    _definition("BXZX_009", "暴涨之星", "攻击之星", "突破攻击星线", "识别突破攻击候选并等待量时空、主力、均线和区间确认。", ["breakout", "close_strength", "volume_confirmation"], annotations=["突破攻击"]),
    _definition("BXZX_010", "暴涨之星", "攻击之星", "反转攻击星线", "识别反转攻击候选并等待后续确认。", ["downtrend", "reversal", "reclaim", "volume"], annotations=["反转攻击"]),
    _definition("BXZX_011", "暴涨之星", "经典之星", "见底经典星线", "识别见底经典星线并保持风险边界。", ["base", "reclaim", "follow_through"], annotations=["见底经典"]),
    _definition("BXZX_012", "暴涨之星", "经典之星", "现顶经典星线", "识别现顶经典星线，直接进入风险层。", ["top_structure", "failure", "pressure"], annotations=["现顶经典"]),
    _definition("BXZX_013", "暴涨之星", "案例对照", "望星空案例对照", "比较正例、反例和形似失败案例，避免只见形不见意。", ["case_library", "similarity", "differences"], annotations=["望星空"]),
)


def _v2_definition(
    skill_id: str,
    book: str,
    chapter: str,
    name: str,
    description: str,
    required: list[str],
    *,
    parent_skill_id: str | None = None,
    knowledge_layer: str = "BOOK_RULE",
    annotations: list[str] | None = None,
) -> dict[str, Any]:
    """Create the expanded V2 registry without changing the V1 contract.

    ``BOOK_SKILL_DEFINITIONS`` is intentionally kept at its historical size
    because existing API consumers and replay jobs rely on those IDs.  V2
    definitions are additive and carry the extra provenance needed by the
    three-book decision page.
    """
    return {
        "skill_id": skill_id,
        "parent_skill_id": parent_skill_id,
        "book": book,
        "chapter": chapter,
        "section": name,
        "original_name": name,
        "knowledge_layer": knowledge_layer,
        "description": description,
        "required_features": required,
        "prerequisite": ["point_in_time_daily_bars", "valid_ohlcv"],
        "positive_evidence": ["可观察价格、成交、均线、位置或板块证据"],
        "negative_evidence": ["结构缺失、反证增强、确认失败或数据截面不完整"],
        "invalidation": ["关键结构失守", "后续确认失败", "反证占据主导"],
        "chart_annotations": annotations or [name],
        "book_rule_version": V2_BOOK_RULE_VERSION,
        "enabled": True,
    }


# Expanded V2 Skill Registry.  It is additive: the original 43 V1 records
# remain available through BOOK_SKILL_DEFINITIONS for old callers.
V2_BOOK_SKILL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    _v2_definition("HQS_RISK_001", "猎取强势股", "量时空大压", "明显顶部压力", "历史顶部、成交密集和价格响应共同形成的顶部压力。", ["historical_top", "volume_cluster", "price_distance"], annotations=["历史顶部", "顶部成交密集区"]),
    _v2_definition("HQS_RISK_002", "猎取强势股", "量时空大压", "明显趋势压力", "高低点、均线角度和反弹失败共同形成的趋势压力。", ["lower_highs", "lower_lows", "ma_slope", "failed_rebound"], annotations=["趋势压力"]),
    _v2_definition("HQS_RISK_003", "猎取强势股", "量时空大压", "巨量下跳缺口压力", "巨量向下跳空且尚未收复的价格压力。", ["down_gap", "gap_volume", "recovery"], annotations=["巨量下跳缺口"]),
    _v2_definition("HQS_RISK_004", "猎取强势股", "量时空大压", "前暴跌起始区压力", "历史暴跌起点及其成交密集区再次成为供给边界。", ["crash_origin", "origin_zone", "price_distance"], annotations=["前暴跌起始区"]),
    _v2_definition("HQS_PULL_001", "猎取强势股", "主力拉升意图", "拉高出货", "高位放量而价格响应减弱时的可观察行为假设。", ["high_position", "volume", "upper_wick", "price_failure"], annotations=["拉高出货"]),
    _v2_definition("HQS_PULL_002", "猎取强势股", "主力拉升意图", "拉高建仓", "中低位价格推进、承接和量能持续时的可观察行为假设。", ["low_mid_position", "volume", "price_response", "support"], annotations=["拉高建仓"]),
    _v2_definition("HQS_PULL_003", "猎取强势股", "主力拉升意图", "拉高试盘", "价格先行试探供给边界、尚未完成确认的行为假设。", ["breakout_probe", "volume", "follow_through"], annotations=["拉高试盘"]),
    _v2_definition("HQS_PULL_003A", "猎取强势股", "主力拉升意图", "建仓可行性试盘", "试探抛压和承接是否允许进一步建仓。", ["probe", "support", "negative_energy"], parent_skill_id="HQS_PULL_003", annotations=["建仓可行性试盘"]),
    _v2_definition("HQS_PULL_003B", "猎取强势股", "主力拉升意图", "正式拉升前试盘", "突破前的量价试探，等待后续放量保持。", ["probe", "prior_base", "breakout_hold"], parent_skill_id="HQS_PULL_003", annotations=["正式拉升前试盘"]),
    _v2_definition("HQS_VOL_001", "猎取强势股", "量形态", "量形态选股增强", "异常量、持续量变化和价格反馈的组合观察。", ["volume_sequence", "price_response", "follow_through"], parent_skill_id="HQS_003"),
    _v2_definition("HQS_VOL_002", "猎取强势股", "量形态", "主力量行为完整展开", "压价、负能量释放、缩量承接和重新攻击的过程记录。", ["pressure", "contraction", "recovery", "attack"], parent_skill_id="HQS_004"),
    _v2_definition("HQS_PRICE_001", "猎取强势股", "量价异动", "量异动", "成交量相对基线的异常变化及其持续时间。", ["volume_baseline", "volume_shock", "duration"], parent_skill_id="HQS_005"),
    _v2_definition("HQS_PRICE_002", "猎取强势股", "量价异动", "价异动", "价格重心、波动和突破相对基线的异常变化。", ["price_baseline", "price_shock", "duration"], parent_skill_id="HQS_005"),
    _v2_definition("HQS_PRICE_003", "猎取强势股", "量价异动", "量价同步异动", "量异动和价异动在同一时间窗口互相响应。", ["volume_shock", "price_shock", "price_response"], parent_skill_id="HQS_005"),
    _v2_definition("HQS_BUY_001", "猎取强势股", "买点等级", "强势买点", "量价、主力、均线和强势区共同确认的观察级买点。", ["zone_a", "attack", "volume", "confirmation"], annotations=["强势买点"]),
    _v2_definition("HQS_BUY_002", "猎取强势股", "买点等级", "经典买点", "经典形态或星线完成必要确认的观察级买点。", ["classic_pattern", "confirmation", "invalidation"], annotations=["经典买点"]),
    _v2_definition("HQS_BUY_003", "猎取强势股", "买点等级", "一般买点", "部分条件具备但互证不足的观察点。", ["partial_confirmation", "risk"], annotations=["一般买点"]),
    _v2_definition("HQS_BUY_004", "猎取强势股", "买点等级", "臆想买点", "只有单一形状或主观叙事、缺少可验证互证的情形。", ["counter_evidence", "missing_confirmation"], annotations=["臆想买点"]),
    _v2_definition("BXDT_VOL_001", "暴涨大形态", "成交量形态", "三阳控三阴", "综合阳量群、阴量群、位置和价格破坏程度判断量能控制。", ["positive_volume_group", "negative_volume_group", "price_damage", "position"], annotations=["三阳控三阴"]),
    _v2_definition("BXDT_MA_001", "暴涨大形态", "均线归位", "均线密集", "均线距离收窄、方向尚未形成一致趋势。", ["ma_spread", "ma_slope"], annotations=["均线密集"]),
    _v2_definition("BXDT_MA_002", "暴涨大形态", "均线归位", "均线穿越", "短中期均线发生交叉或排列切换。", ["ma_cross", "ma_order"], annotations=["均线穿越"]),
    _v2_definition("BXDT_MA_003", "暴涨大形态", "均线归位", "均线翘头", "均线下行后斜率改善并开始向上。", ["ma_slope_change", "ma_recovery"], annotations=["均线翘头"]),
    _v2_definition("BXDT_MA_004", "暴涨大形态", "均线归位", "均线发散", "均线排列形成且距离扩大。", ["ma_order", "ma_spread", "ma_slope"], annotations=["均线发散"]),
    _v2_definition("BXDT_MA_005", "暴涨大形态", "均线归位", "均线顺畅", "均线排列、角度和距离均保持顺畅。", ["ma_order", "ma_slope", "ma_distance"], annotations=["均线顺畅"]),
    _v2_definition("BXDT_TRI_001", "暴涨大形态", "三角形态", "平顶三角形", "上沿近似水平、低点逐步抬高的收敛结构。", ["upper_line", "lower_slope", "touch_count"], annotations=["平顶三角形"]),
    _v2_definition("BXDT_TRI_002", "暴涨大形态", "三角形态", "平底三角形", "下沿近似水平、高点逐步降低的收敛结构。", ["upper_slope", "lower_line", "touch_count"], annotations=["平底三角形"]),
    _v2_definition("BXDT_TRI_003", "暴涨大形态", "三角形态", "收敛三角形", "上下边界同时向中轴收敛的结构。", ["upper_slope", "lower_slope", "range_contraction"], annotations=["收敛三角形"]),
    _v2_definition("BXDT_BOX_001", "暴涨大形态", "箱体形态", "箱体形态", "上沿、下沿、中轴、测试和突破生命周期完整记录。", ["range_high", "range_low", "touch_count", "breakout"], annotations=["箱体上沿", "箱体下沿"]),
    _v2_definition("BXDT_NECK_001", "暴涨大形态", "颈位形态", "多底颈位", "多个底部由共同供给边界连接形成的颈位。", ["multiple_lows", "neckline", "touch_count"], annotations=["多底颈位"]),
    _v2_definition("BXDT_NECK_002", "暴涨大形态", "颈位形态", "圆弧底颈位", "弧形底部逐步抬升并接近颈位的结构。", ["curved_base", "neckline", "duration"], annotations=["圆弧底颈位"]),
    _v2_definition("BXDT_NECK_003", "暴涨大形态", "颈位形态", "V形底颈位", "快速下探后快速收复形成的颈位结构。", ["v_reversal", "neckline", "reclaim"], annotations=["V形底颈位"]),
    _v2_definition("BXDT_NECK_004", "暴涨大形态", "颈位形态", "颈位支撑", "突破后的颈位回踩并转化为支撑。", ["neckline", "retest", "support"], annotations=["颈位支撑"]),
    _v2_definition("BXDT_UP_001", "暴涨大形态", "顺上形态", "缓慢顺上", "斜率温和、重心持续抬升的顺上结构。", ["ma_slope", "higher_lows", "moderate_speed"], annotations=["缓慢顺上"]),
    _v2_definition("BXDT_UP_002", "暴涨大形态", "顺上形态", "大波段后再顺上", "大波段后经过结构整理再次顺上。", ["prior_large_leg", "pullback", "recovery"], annotations=["大波段后再顺上"]),
    _v2_definition("BXDT_UP_003", "暴涨大形态", "顺上形态", "小幅波段后再顺上", "小幅波段整理后再次顺上。", ["prior_small_leg", "shallow_pullback", "recovery"], annotations=["小幅波段后再顺上"]),
    _v2_definition("BXDT_BOTTOM_001", "暴涨大形态", "趋势底部", "长下影K线见底", "低位长下影和低点回收形成的底部候选。", ["lower_wick", "low_position", "reclaim"], annotations=["长下影K线见底"]),
    _v2_definition("BXDT_BOTTOM_002", "暴涨大形态", "趋势底部", "诱空大阴K线见底", "下探大阴后关键位置守住并出现后续回收。", ["large_bear", "support", "follow_through"], annotations=["诱空大阴K线见底"]),
    _v2_definition("BXDT_BOTTOM_003", "暴涨大形态", "趋势底部", "巨量大阳K线见底", "低位巨量大阳并得到后续价格保持。", ["large_bull", "volume_shock", "follow_through"], annotations=["巨量大阳K线见底"]),
    _v2_definition("BXDT_BOTTOM_004", "暴涨大形态", "趋势底部", "阴阳并肩组合K线见底", "阴阳相邻、低点守住并逐步收复的组合。", ["bear_bull_pair", "support", "reclaim"], annotations=["阴阳并肩组合K线见底"]),
    _v2_definition("BXDT_BOTTOM_005", "暴涨大形态", "趋势底部", "“单”字形反转组合K线见底", "窄幅整理后出现方向反转的组合候选。", ["range_base", "reversal", "confirmation"], annotations=["单字形反转"]),
    _v2_definition("BXDT_BOTTOM_006", "暴涨大形态", "趋势底部", "晨星平台K线见底", "下跌后小实体平台与阳线回收的组合。", ["downtrend", "small_body", "bull_reclaim"], annotations=["晨星平台"]),
    _v2_definition("BXDT_BOTTOM_007", "暴涨大形态", "趋势底部", "双重底", "两个相近低点及其颈位组成的底部结构。", ["two_lows", "neckline", "breakout"], annotations=["双重底"]),
    _v2_definition("BXDT_BOTTOM_008", "暴涨大形态", "趋势底部", "多重底", "三个或以上相近低点形成的底部结构。", ["multiple_lows", "neckline", "duration"], annotations=["多重底"]),
    _v2_definition("BXDT_BOTTOM_009", "暴涨大形态", "趋势底部", "头肩底", "左肩、头部、右肩和颈位的结构候选。", ["shoulders", "head", "neckline"], annotations=["头肩底"]),
    _v2_definition("BXDT_CAPITAL_001", "暴涨大形态", "资金底部", "量能筑底", "低位量能逐步稳定、承接改善的资金底部。", ["low_position", "volume_base", "support"], annotations=["量能筑底"]),
    _v2_definition("BXDT_CAPITAL_002", "暴涨大形态", "资金底部", "均线底", "均线下行转走平、聚合后改善的资金底部。", ["ma_flatten", "ma_convergence", "recovery"], annotations=["均线底"]),
    _v2_definition("BXDT_CAPITAL_003", "暴涨大形态", "资金底部", "下行转走平", "均线斜率由负转为接近零。", ["ma_slope_change"], parent_skill_id="BXDT_CAPITAL_002", annotations=["下行转走平"]),
    _v2_definition("BXDT_CAPITAL_004", "暴涨大形态", "资金底部", "平行转翘头上行", "均线走平后斜率转正。", ["ma_slope_change", "ma_recovery"], parent_skill_id="BXDT_CAPITAL_002", annotations=["平行转翘头上行"]),
    _v2_definition("BXDT_CAPITAL_005", "暴涨大形态", "资金底部", "空头转多头", "均线排列从空头向多头切换。", ["ma_order_change", "price_reclaim"], parent_skill_id="BXDT_CAPITAL_002", annotations=["空头转多头"]),
    _v2_definition("BXDT_CAPITAL_006", "暴涨大形态", "资金底部", "分散转聚合再发散", "均线先收敛后重新向同向展开。", ["ma_spread_path"], parent_skill_id="BXDT_CAPITAL_002", annotations=["分散转聚合再发散"]),
    _v2_definition("BXDT_3D_001", "暴涨大形态", "三度", "厚度", "结构持续时间、量能积累、均线基础和主力布局的厚度。", ["duration", "volume_accumulation", "ma_base", "main_force"], annotations=["厚度"]),
    _v2_definition("BXDT_3D_002", "暴涨大形态", "三度", "力度", "价格推进、量能释放、攻击和相对强度的力度。", ["price_progress", "volume_release", "attack", "relative_strength"], annotations=["力度"]),
    _v2_definition("BXDT_3D_003", "暴涨大形态", "三度", "速度", "上涨速度、突破速度和量价加速度。", ["return_speed", "breakout_speed", "acceleration"], annotations=["速度"]),
    _v2_definition("BXDT_3D_MODE_001", "暴涨大形态", "三度模式", "精准强势短线盈利模式", "三度结构进入短线观察的组合模式，未验证前保持Shadow。", ["thickness", "strength", "speed", "confirmation"], knowledge_layer="EMPIRICAL_LAYER", annotations=["精准强势短线盈利模式"]),
    _v2_definition("BXDT_3D_MODE_002", "暴涨大形态", "三度模式", "强势波段盈利模式", "三度结构进入波段观察的组合模式，未验证前保持Shadow。", ["thickness", "strength", "speed", "trend"], knowledge_layer="EMPIRICAL_LAYER", annotations=["强势波段盈利模式"]),
    _v2_definition("BXDT_PEAK_001", "暴涨大形态", "巅峰超越", "巅峰超越", "历史巅峰接近、触及、突破和保持的生命周期。", ["historical_peak", "breakout", "hold", "volume"], annotations=["历史巅峰"]),
    _v2_definition("BXZX_CLASSIC_BOTTOM_001", "暴涨之星", "经典之星", "定海神针见底", "低位长下影经典见底星线，必须结合后续确认。", ["lower_wick", "low_position", "follow_through"], annotations=["定海神针"]),
    _v2_definition("BXZX_CLASSIC_BOTTOM_002", "暴涨之星", "经典之星", "倒锤头星线见底", "下跌背景中的倒锤头结构及收复确认。", ["downtrend", "inverted_hammer", "reclaim"], annotations=["倒锤头星线"]),
    _v2_definition("BXZX_CLASSIC_BOTTOM_003", "暴涨之星", "经典之星", "孕线见底", "下跌后的孕线收缩和方向确认。", ["inside_body", "downtrend", "follow_through"], annotations=["孕线见底"]),
    _v2_definition("BXZX_CLASSIC_BOTTOM_004", "暴涨之星", "经典之星", "启明星见底", "大阴、小实体、阳线回收的三段式见底候选。", ["morning_star", "low_position", "reclaim"], annotations=["启明星见底"]),
    _v2_definition("BXZX_CLASSIC_BOTTOM_005", "暴涨之星", "经典之星", "揉搓星见底", "上下试探后低位收敛并出现方向确认。", ["two_sided_probe", "base", "confirmation"], annotations=["揉搓星见底"]),
    _v2_definition("BXZX_CLASSIC_BOTTOM_006", "暴涨之星", "经典之星", "平排星线见底", "低位连续小实体平排并出现后续回收。", ["small_bodies", "low_position", "reclaim"], annotations=["平排星线见底"]),
    _v2_definition("BXZX_CLASSIC_TOP_001", "暴涨之星", "经典之星", "射击之星", "高位上影和价格失败形成的现顶候选。", ["upper_wick", "high_position", "failure"], annotations=["射击之星"]),
    _v2_definition("BXZX_CLASSIC_TOP_002", "暴涨之星", "经典之星", "吊颈星线", "高位长下影但收盘弱、后续失守确认的现顶候选。", ["high_position", "lower_wick", "failure"], annotations=["吊颈星线"]),
    _v2_definition("BXZX_CLASSIC_TOP_003", "暴涨之星", "经典之星", "孕线现顶", "高位收缩并向下破坏的现顶候选。", ["inside_body", "high_position", "breakdown"], annotations=["孕线现顶"]),
    _v2_definition("BXZX_CLASSIC_TOP_004", "暴涨之星", "经典之星", "黄昏之星现顶", "上涨后大阳、小实体、阴线破坏的三段式现顶候选。", ["evening_star", "high_position", "breakdown"], annotations=["黄昏之星"]),
)


# Exact V2 identifiers from the specification.  A few early development
# builds used descriptive aliases (for example HQS_PULL_*); those aliases are
# retained above for compatibility, while these records make the documented
# API and the registry use the canonical skill IDs.
V2_CANONICAL_SKILL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    _v2_definition("HQS_001", "猎取强势股", "量时空", "量时空提供机会", "量、时间和空间共同提供机会的观察层。", ["quantity", "time", "space"]),
    _v2_definition("HQS_002", "猎取强势股", "量时空", "量时空大压风险", "量、时间和空间共同形成压力的观察层。", ["quantity", "time", "space", "pressure"]),
    _v2_definition("HQS_003", "猎取强势股", "量形态", "量形态选股", "异常量、持续量和价格反馈的组合观察。", ["volume_sequence", "price_response"]),
    _v2_definition("HQS_004", "猎取强势股", "量行为", "量行为跟随主力", "只根据可观察成交和价格行为描述主力身影。", ["up_down_volume", "price_response"]),
    _v2_definition("HQS_005", "猎取强势股", "量价异动", "量价异动", "量异动、价异动和同步关系。", ["volume_shock", "price_shock"]),
    _v2_definition("HQS_006", "猎取强势股", "均线归位", "均线归位", "均线排列、角度和距离的综合观察。", ["ma_order", "ma_slope", "ma_distance"]),
    _v2_definition("HQS_007", "猎取强势股", "量价异动", "量价异动让均线归位", "量价变化推动均线结构改善的观察。", ["volume_shock", "ma_recovery"]),
    _v2_definition("HQS_008", "猎取强势股", "最佳交易区", "强势A区", "趋势与量价共振的强势区域。", ["zone_a", "ma_order", "price_progress"]),
    _v2_definition("HQS_009", "猎取强势股", "最佳交易区", "强势B区", "调整或重新转强的区域。", ["zone_b", "support", "reattack"]),
    _v2_definition("HQS_010", "猎取强势股", "最佳交易区", "风险C区", "风险优先的结构区域。", ["zone_c", "trend_damage", "sell_risk"]),
    _v2_definition("HQS_011", "猎取强势股", "经典盈利模式", "强势结点盈利模式", "均线结点结合位置、量和主力证据。", ["ma_node", "zone", "volume"]),
    _v2_definition("HQS_012", "猎取强势股", "经典盈利模式", "单日强硬洗盘盈利模式", "形态与位置二维核验的回撤观察。", ["wash_shape", "zone", "recovery"]),
    _v2_definition("HQS_013", "猎取强势股", "经典盈利模式", "缺口盈利模式", "缺口环境、位置和后续保持。", ["gap", "volume", "hold"]),
    _v2_definition("HQS_014", "猎取强势股", "经典盈利模式", "强势循环低点盈利模式", "循环级别、回调和支撑的观察。", ["cycle", "support", "main_force"]),
    _v2_definition("HQS_015", "猎取强势股", "卖对股票", "明显见顶卖出策略", "多项顶部证据共同出现时的风险观察。", ["top_evidence", "price_failure"]),
    _v2_definition("HQS_016", "猎取强势股", "卖对股票", "明显遇顶卖出策略", "接近历史压力但不等同于已经见顶。", ["historical_top", "resistance"]),
    _v2_definition("HQS_017", "猎取强势股", "卖对股票", "C区卖出策略", "直接读取风险C区状态机。", ["zone_c", "risk_state"]),
    _v2_definition("HQS_018", "猎取强势股", "量能体叠加术", "量能体叠加术", "按基础层、图表层、题材层和风险冲突形成路径。", ["stacking_path"]),
    _v2_definition("HQS_019", "猎取强势股", "量能体叠加术", "量能体叠加与题材互证", "量能体与题材热点的互证观察。", ["stacking", "theme"]),
    _v2_definition("HQS_020", "猎取强势股", "望星空", "学习经典案例及交易策略", "正反案例和形似失败案例的对照入口。", ["case_library"]),
    _v2_definition("HQS_MAIN_001", "猎取强势股", "主力拉升意图", "拉高出货", "高位放量而价格响应减弱的可观察假设。", ["high_position", "volume", "price_failure"]),
    _v2_definition("HQS_MAIN_002", "猎取强势股", "主力拉升意图", "拉高建仓", "中低位推进、承接和量能持续的可观察假设。", ["low_mid_position", "support", "price_progress"]),
    _v2_definition("HQS_MAIN_003", "猎取强势股", "主力拉升意图", "拉高试盘", "突破前试探供给边界的可观察假设。", ["breakout_probe", "follow_through"]),
    _v2_definition("HQS_MAIN_004", "猎取强势股", "主力拉升意图", "建仓可行性试盘", "试探抛压和承接是否允许进一步建仓。", ["probe", "support"], parent_skill_id="HQS_MAIN_003"),
    _v2_definition("HQS_MAIN_005", "猎取强势股", "主力拉升意图", "正式拉升前试盘", "突破前量价试探并等待放量保持。", ["probe", "breakout_hold"], parent_skill_id="HQS_MAIN_003"),
    _v2_definition("HQS_WASH_001", "猎取强势股", "单日强硬洗盘", "中大阴线实体洗盘", "中大阴线与后续收回的二维观察。", ["bear_body", "recovery"]),
    _v2_definition("HQS_WASH_002", "猎取强势股", "单日强硬洗盘", "长上影线形态洗盘", "长上影与后续收回的二维观察。", ["upper_wick", "recovery"]),
    _v2_definition("HQS_WASH_003", "猎取强势股", "单日强硬洗盘", "黑太阳形态洗盘", "黑太阳与后续收回的二维观察。", ["bear_body", "recovery"]),
    _v2_definition("HQS_GAP_001", "猎取强势股", "缺口盈利模式", "拔升缺口", "上行缺口及其后续保持。", ["up_gap", "volume", "hold"]),
    _v2_definition("HQS_GAP_002", "猎取强势股", "缺口盈利模式", "平台跳空突破", "平台上沿的跳空突破。", ["up_gap", "range", "breakout"]),
    _v2_definition("HQS_GAP_003", "猎取强势股", "缺口盈利模式", "拐点跳空突破", "下跌转折处的跳空突破。", ["up_gap", "reversal"]),
    _v2_definition("HQS_GAP_004", "猎取强势股", "缺口盈利模式", "缺口支撑", "缺口回踩后转为支撑。", ["gap", "retest", "support"]),
    _v2_definition("HQS_CYCLE_001", "猎取强势股", "强势循环低点", "波段循环低点", "较大级别回调后的循环低点观察。", ["swing_cycle", "support"]),
    _v2_definition("HQS_CYCLE_002", "猎取强势股", "强势循环低点", "局部循环低点", "局部回调后的循环低点观察。", ["local_cycle", "support"]),
    _v2_definition("HQS_THEME_TYPE_001", "猎取强势股", "题材类型", "主流题材", "影响范围广、持续性待验证的题材分类。", ["theme_context"]),
    _v2_definition("HQS_THEME_TYPE_002", "猎取强势股", "题材类型", "一般题材", "行业、区域或公司层面的题材分类。", ["theme_context"]),
    _v2_definition("HQS_THEME_TYPE_003", "猎取强势股", "题材类型", "空穴来风的题材", "缺乏可核验来源的题材传闻分类。", ["theme_source"]),
    _v2_definition("HQS_THEME_001", "猎取强势股", "热点等级", "强势热点", "价格、资金和持续性共同较强的热点。", ["theme_change", "theme_flow"]),
    _v2_definition("HQS_THEME_002", "猎取强势股", "热点等级", "局部热点", "局部强化但扩散有限的热点。", ["theme_change"]),
    _v2_definition("HQS_THEME_003", "猎取强势股", "热点等级", "一般热点", "刺激存在但强度或持续性不足的热点。", ["theme_change"]),
    _v2_definition("BXZX_001", "暴涨之星", "蓄势之星", "诱空蓄势星线", "前置基础、向下试探、止跌和星线共同观察。", ["prior_base", "down_probe", "star"]),
    _v2_definition("BXZX_002", "暴涨之星", "蓄势之星", "逼空蓄势星线", "强势位置横住、星线收缩和持有力量观察。", ["prior_up", "high_position", "star"]),
    _v2_definition("BXZX_003", "暴涨之星", "调整之星", "缓冲调整星线", "价缓量缩并得到均线支撑的调整观察。", ["slow_pullback", "volume_shrink", "support"]),
    _v2_definition("BXZX_004", "暴涨之星", "调整之星", "震荡调整星线", "震荡上下沿、时间和量能变化观察。", ["range", "star", "volume"]),
    _v2_definition("BXZX_005", "暴涨之星", "止跌之星", "同步止跌星线", "价格收敛、实体缩小与成交量同步衰竭。", ["price_contraction", "volume_contraction"]),
    _v2_definition("BXZX_006", "暴涨之星", "止跌之星", "背离止跌星线", "价格仍弱而成交量收缩的量价背离观察。", ["price_down", "volume_down"]),
    _v2_definition("BXZX_007", "暴涨之星", "补仓之星", "借势补仓星线", "地势与趋势共同支持时的观察信号。", ["landform", "trend"]),
    _v2_definition("BXZX_008", "暴涨之星", "补仓之星", "借风补仓星线", "消息/题材与价格结构共同支持时的观察信号。", ["news", "theme", "support"]),
    _v2_definition("BXZX_009", "暴涨之星", "攻击之星", "突破攻击星线", "关键位突破、星线和后续保持联合观察。", ["breakout", "star", "hold"]),
    _v2_definition("BXZX_010", "暴涨之星", "攻击之星", "反转攻击星线", "下跌、止跌、反转受阻和再次攻击的联合观察。", ["downtrend", "reversal", "star"]),
    _v2_definition("BXZX_013", "暴涨之星", "望星空", "望星空案例对照", "成功、失败和形似案例的历史对照入口。", ["case_library"]),
)

V2_BOOK_SKILL_DEFINITIONS = V2_BOOK_SKILL_DEFINITIONS + V2_CANONICAL_SKILL_DEFINITIONS


async def ensure_book_skill_registry() -> None:
    """Idempotently persist the locked registry without touching other skills."""
    async with async_session() as session:
        for definition in (*BOOK_SKILL_DEFINITIONS, *V2_BOOK_SKILL_DEFINITIONS):
            row = await session.get(BookSkillRegistry, definition["skill_id"])
            # The JSON registry carries provenance fields that intentionally
            # do not belong to the legacy table. Keep the table write explicit
            # so adding V2 metadata cannot break startup on existing schemas.
            book_fields = {
                "book", "chapter", "section", "original_name", "description",
                "required_features", "prerequisite", "positive_evidence",
                "negative_evidence", "invalidation", "chart_annotations",
                "book_rule_version", "enabled",
            }
            values = {key: deepcopy(value) for key, value in definition.items() if key in book_fields}
            values["updated_at"] = datetime.utcnow()
            if row is None:
                session.add(BookSkillRegistry(skill_id=definition["skill_id"], **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            if definition.get("book_rule_version") == V2_BOOK_RULE_VERSION:
                rule = (await session.execute(
                    select(SkillRuleDefinition).where(
                        SkillRuleDefinition.skill_id == definition["skill_id"],
                        SkillRuleDefinition.version == V2_BOOK_RULE_VERSION,
                    )
                )).scalar_one_or_none()
                rule_values = {
                    "prerequisite_json": deepcopy(definition.get("prerequisite") or []),
                    "positive_rule_json": deepcopy(definition.get("positive_evidence") or []),
                    "negative_rule_json": deepcopy(definition.get("negative_evidence") or []),
                    "confirmation_json": deepcopy(definition.get("next_confirmation") or ["后续价格、成交和结构继续确认"]),
                    "invalidation_json": deepcopy(definition.get("invalidation") or []),
                    "required_timeframes_json": ["1d"],
                    "engine_feature_json": deepcopy(definition.get("required_features") or []),
                    "enabled": bool(definition.get("enabled", True)),
                    "updated_at": datetime.utcnow(),
                }
                if rule is None:
                    session.add(SkillRuleDefinition(skill_id=definition["skill_id"], version=V2_BOOK_RULE_VERSION, **rule_values))
                else:
                    for key, value in rule_values.items():
                        setattr(rule, key, value)
        await session.commit()


def list_book_skills(*, book: str | None = None) -> list[dict[str, Any]]:
    values = [deepcopy(item) for item in BOOK_SKILL_DEFINITIONS]
    if book:
        values = [item for item in values if item["book"] == book]
    return values


def list_v2_book_skills(*, book: str | None = None) -> list[dict[str, Any]]:
    values = [deepcopy(item) for item in V2_BOOK_SKILL_DEFINITIONS]
    if book:
        values = [item for item in values if item["book"] == book]
    return values


def skill_definition(skill_id: str) -> dict[str, Any] | None:
    for item in (*BOOK_SKILL_DEFINITIONS, *V2_BOOK_SKILL_DEFINITIONS):
        if item["skill_id"] == skill_id:
            return deepcopy(item)
    return None
