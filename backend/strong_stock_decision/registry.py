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
from models import BookSkillRegistry


BOOK_RULE_VERSION = "three-books-v1.0"
ENGINE_VERSION = "STRONG_STOCK_DECISION_V1"
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


async def ensure_book_skill_registry() -> None:
    """Idempotently persist the locked registry without touching other skills."""
    async with async_session() as session:
        for definition in BOOK_SKILL_DEFINITIONS:
            row = await session.get(BookSkillRegistry, definition["skill_id"])
            values = {key: deepcopy(value) for key, value in definition.items() if key != "skill_id"}
            values["updated_at"] = datetime.utcnow()
            if row is None:
                session.add(BookSkillRegistry(skill_id=definition["skill_id"], **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
        await session.commit()


def list_book_skills(*, book: str | None = None) -> list[dict[str, Any]]:
    values = [deepcopy(item) for item in BOOK_SKILL_DEFINITIONS]
    if book:
        values = [item for item in values if item["book"] == book]
    return values


def skill_definition(skill_id: str) -> dict[str, Any] | None:
    for item in BOOK_SKILL_DEFINITIONS:
        if item["skill_id"] == skill_id:
            return deepcopy(item)
    return None
