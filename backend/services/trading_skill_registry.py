"""Persistent registry and lifecycle rules for V5 trading skills.

The registry contains hypotheses, not trade instructions. Runtime calculators
may emit candidates and risk states, while promotion to ACTIVE is only allowed
after reproducible out-of-sample validation.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from sqlalchemy import select

from database import async_session
from models import RejectedTradingKnowledge, TradingSkillRegistry
from quant.trading_skills import STAGE_LABELS


SKILL_VERSION = "1.0.0"
LIFECYCLE_STATES = ("IDEA", "EXPERIMENTAL", "SHADOW", "ACTIVE", "DEGRADED", "DEPRECATED")
ALLOWED_TRANSITIONS = {
    "IDEA": {"EXPERIMENTAL", "DEPRECATED"},
    "EXPERIMENTAL": {"SHADOW", "DEPRECATED"},
    "SHADOW": {"ACTIVE", "DEGRADED", "DEPRECATED"},
    "ACTIVE": {"DEGRADED", "DEPRECATED"},
    "DEGRADED": {"SHADOW", "ACTIVE", "DEPRECATED"},
    "DEPRECATED": set(),
}


def _skill(
    skill_id: str,
    name: str,
    category: str,
    description: str,
    *,
    regimes: list[str],
    sectors: list[str],
    horizons: list[str],
    required: list[str],
    optional: list[str],
    data_level: str,
    entry: dict[str, Any],
    confirm: dict[str, Any],
    reject: dict[str, Any],
    exit_logic: dict[str, Any],
    initial_rating: str,
) -> dict[str, Any]:
    return {
        "skill_id": skill_id,
        "skill_name": name,
        "skill_version": SKILL_VERSION,
        "category": category,
        "description": description,
        "applicable_market_regimes": regimes,
        "applicable_sector_states": sectors,
        "applicable_horizons": horizons,
        "required_factors": required,
        "optional_factors": optional,
        "required_data_level": data_level,
        "entry_gate": entry,
        "confirm_gate": confirm,
        "reject_gate": reject,
        "exit_logic": exit_logic,
        "lifecycle_state": "EXPERIMENTAL",
        "validation_status": "REALTIME_SHADOW" if skill_id == "skill_09_auction_intraday_confirm" else "NOT_TESTED",
        "enabled": True,
        "definition": {
            "initial_logic_rating": initial_rating,
            "output_contract": ["stage", "confidence_pct", "evidence", "invalidation_conditions"],
            "direct_order": False,
            "point_in_time_required": True,
        },
    }


SKILL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    _skill(
        "skill_01_price_volume_efficiency", "量价效率", "price_volume",
        "判断单位成交投入推动价格的效率是在增强还是衰减。",
        regimes=["蓄势", "启势", "顺势", "盛势", "分势", "返势"],
        sectors=["启势", "顺势", "盛势", "强化", "修复"], horizons=["1d", "3d", "5d", "10d"],
        required=["r_1d", "amount_ratio_20", "close_location", "upper_wick_ratio"],
        optional=["relative_sector_1d", "relative_market_1d", "order_flow_imbalance"], data_level="DAILY",
        entry={"formula": "r_1d / log(1 + amount_ratio_20)", "states": ["EFFICIENT_UP", "INEFFICIENT_UP", "EFFICIENT_DOWN", "ABSORBED_DOWN"]},
        confirm={"rule": "eff_delta improves or observed absorption structure confirms"},
        reject={"rule": "missing amount or fewer than 20 prior sessions"},
        exit_logic={"rule": "efficiency delta reverses, pressure rises, or relative strength fails"}, initial_rating="A",
    ),
    _skill(
        "skill_02_absorption_pressure", "承接/抛压", "microstructure",
        "使用影线、收盘位置、低点恢复和相对强度刻画可观测承接与卖压。",
        regimes=["蓄势", "启势", "顺势", "分势", "返势"],
        sectors=["分歧", "修复", "启势", "顺势"], horizons=["1d", "3d", "5d"],
        required=["lower_wick_ratio", "upper_wick_ratio", "close_location", "recovery_from_low", "amount_z_60"],
        optional=["vwap_reclaim", "order_flow_imbalance", "aggressive_buy_ratio", "depth_imbalance"], data_level="DAILY_PLUS_INTRADAY",
        entry={"formula": "absorption_score_raw and pressure_score_raw mapped to 0-100"},
        confirm={"rule": "absorption exceeds pressure and relative strength does not weaken"},
        reject={"rule": "hard risk block, no valid OHLC range, or pressure dominates"},
        exit_logic={"rule": "support fails or pressure score overtakes absorption score"}, initial_rating="A",
    ),
    _skill(
        "skill_03_abnormal_turnover", "异常成交跟踪", "event_tracking",
        "记录异常成交事件成本锚，并在T+1/T+2/T+3/T+5跟踪价格、成交和Alpha保留。",
        regimes=["蓄势", "启势", "顺势", "盛势", "分势", "退势", "返势"],
        sectors=["全部"], horizons=["1d", "3d", "5d"],
        required=["amount_z_60", "amount_ratio_20", "shock_anchor"],
        optional=["turnover_percentile_120", "shock_vwap", "relative_sector_5d"], data_level="DAILY",
        entry={"rule": "amount_z_60 >= 2 OR amount_ratio_20 >= 1.8 OR turnover_percentile_120 >= 0.95"},
        confirm={"rule": "price retention, alpha retention and post-shock volume contraction"},
        reject={"rule": "event low fails or future confirmation is not yet observable"},
        exit_logic={"rule": "close loses shock low or excess return falls below calibrated limit"}, initial_rating="A",
    ),
    _skill(
        "skill_04_false_breakdown_reclaim", "假跌破回收", "price_structure",
        "识别关键支撑被短暂跌破后重新收回的结构，并等待后续承接确认。",
        regimes=["蓄势", "分势", "返势"], sectors=["分歧", "修复", "返势"], horizons=["3d", "5d", "10d"],
        required=["atr14", "support_level", "break_depth_atr", "reclaim_margin", "close_location"],
        optional=["anchored_vwap", "volume_shock_anchor", "relative_sector_1d", "next_day_vwap_hold"], data_level="DAILY",
        entry={"rule": "low < support AND close > support AND close_location passes calibrated threshold"},
        confirm={"rule": "next day does not break event low or relative strength remains positive"},
        reject={"rule": "close remains below support, relative strength collapses, or liquidity is insufficient"},
        exit_logic={"rule": "event low fails after reclaim or recovery structure is invalidated"}, initial_rating="A-",
    ),
    _skill(
        "skill_05_trend_reacceleration", "趋势二次启动", "trend_structure",
        "强趋势回调时跟踪成交和波动收敛、Alpha保留及再次增强。",
        regimes=["启势", "顺势", "盛势"], sectors=["启势", "顺势", "盛势", "强化"], horizons=["5d", "10d", "20d"],
        required=["return_20d", "pullback_days", "pullback_atr", "volume_contraction", "alpha_retention"],
        optional=["relative_sector_20d", "realized_volatility_contraction", "price_efficiency_delta"], data_level="DAILY",
        entry={"rule": "prior trend strong, 3-20 session controlled pullback, then structure break with volume re-expansion"},
        confirm={"rule": "sector confirms, relative strength turns up, and price efficiency improves"},
        reject={"rule": "deep pullback, expanding sell volume, Alpha collapse, or sector loses permission"},
        exit_logic={"rule": "pullback support fails or reacceleration loses efficiency and sector confirmation"}, initial_rating="A-",
    ),
    _skill(
        "skill_06_low_position_relaunch", "低位异动-收敛-再启动", "emergence",
        "在不过度拥挤的位置跟踪首次Alpha/成交异动、收敛和第二次启动。",
        regimes=["蓄势", "启势", "返势"], sectors=["萌芽", "修复", "启势", "强化"], horizons=["5d", "10d", "20d"],
        required=["position_120", "amount_z_60", "relative_sector_1d", "event_low"],
        optional=["alpha_1d_z", "sector_catalyst", "policy_event", "valuation_state"], data_level="DAILY",
        entry={"rule": "low position + first valid shock + contraction + second expansion"},
        confirm={"rule": "event low holds, volume/volatility contract, sector state improves"},
        reject={"rule": "ST, major financial risk, illiquidity, event low failure, or high-position pseudo-low-price"},
        exit_logic={"rule": "event low fails, Alpha collapses, or second launch loses sector confirmation"}, initial_rating="B+",
    ),
    _skill(
        "skill_07_breakout_quality", "突破质量", "price_structure",
        "估计突破结构有效概率，区分底层势确认与只有价格、热度的疑似假突破。",
        regimes=["启势", "顺势", "盛势"], sectors=["启势", "顺势", "盛势", "强化"], horizons=["3d", "5d", "10d"],
        required=["rolling_high_20", "amount_ratio_20", "price_volume_efficiency", "close_location"],
        optional=["sector_breadth", "sector_relative_strength", "stock_alpha", "crowding_score", "late_day_pressure"], data_level="DAILY",
        entry={"rule": "close exceeds prior 20/60-session high or verified structural pivot"},
        confirm={"rule": "capital continuity, sector breadth, Alpha and absorption jointly confirm"},
        reject={"rule": "price and heat rise without capital, breadth, Alpha or absorption confirmation"},
        exit_logic={"rule": "close falls below breakout level within validation window"}, initial_rating="A",
    ),
    _skill(
        "skill_08_behavior_imbalance", "行为失衡", "behavior_risk",
        "连接V5行为层，识别FOMO、恐慌、拥挤、假突破和行为过冲风险。",
        regimes=["盛势", "极势", "分势", "退势", "返势"], sectors=["全部"], horizons=["1d", "3d", "1w"],
        required=["return_acceleration", "turnover_acceleration", "crowding_score"],
        optional=["market_breadth", "failure_rate", "stock_correlation", "social_heat_z", "alpha_density_change"], data_level="MARKET_PLUS_DAILY",
        entry={"rule": "observable behavior factors form one-sided imbalance"},
        confirm={"rule": "price, turnover, breadth and Alpha deterioration jointly confirm risk state"},
        reject={"rule": "do not infer participant intent from price or order-book shape alone"},
        exit_logic={"rule": "imbalance rate reverses and breadth/Alpha recover"}, initial_rating="A-",
    ),
    _skill(
        "skill_09_auction_intraday_confirm", "竞价与分时确认", "timing_confirmation",
        "使用真实09:25竞价和开盘分钟线确认昨日候选；历史不足时只做前向Shadow。",
        regimes=["启势", "顺势", "盛势", "分势", "返势"], sectors=["启势", "顺势", "盛势", "强化", "修复"], horizons=["open", "15m", "1d"],
        required=["auction_gap", "auction_amount_ratio", "auction_relative_sector"],
        optional=["ret_5m", "ret_15m", "vwap_reclaim", "vwap_hold_minutes", "first_15m_drawdown", "first_15m_recovery"], data_level="AUCTION_INTRADAY",
        entry={"rule": "verified auction fields are available at or after 09:25"},
        confirm={"rule": "auction relative strength plus opening VWAP/relative-strength confirmation"},
        reject={"rule": "missing real auction observation, limit-price non-fill, or opening structure failure"},
        exit_logic={"rule": "opening confirmation fails; never substitute daily bars for missing auction history"}, initial_rating="B+",
    ),
)


REJECTED_KNOWLEDGE: tuple[dict[str, str], ...] = (
    {"knowledge_id": "reject_orderbook_number_codes", "claim": "盘口111/222/333/555/777等数字密码可预测涨跌", "rejection_reason": "缺乏可重复、可证伪的机制与样本外证据。", "category": "数字密码"},
    {"knowledge_id": "reject_promotional_win_rates", "claim": "未经完整样本披露的95%/96%/100%胜率", "rejection_reason": "宣传数字不进入先验；必须保存全样本、成本和样本外结果。", "category": "宣传胜率"},
    {"knowledge_id": "reject_candle_certainty", "claim": "某根K线或缩量阴线必涨/必跌", "rejection_reason": "单一形态不能脱离位置、市场、板块和量价上下文产生确定结论。", "category": "单因子确定性"},
    {"knowledge_id": "reject_unverifiable_intent", "claim": "托单必吸筹、压单必洗盘或主力故意砸盘", "rejection_reason": "交易行为不能证明参与者身份与意图，只保留可观测承接、抛压和撤单特征。", "category": "不可验证意图"},
    {"knowledge_id": "reject_magic_time", "claim": "固定神奇时间可独立构成买点", "rejection_reason": "时间窗口只定义可用信息集，不能创造统计优势。", "category": "固定时间"},
)


def skill_definitions() -> list[dict[str, Any]]:
    return deepcopy(list(SKILL_DEFINITIONS))


def rejected_knowledge_definitions() -> list[dict[str, str]]:
    return deepcopy(list(REJECTED_KNOWLEDGE))


def _public(row: TradingSkillRegistry) -> dict[str, Any]:
    stage_codes = (row.entry_gate or {}).get("states") or list(STAGE_LABELS)
    return {
        "skill_id": row.skill_id, "skill_name": row.skill_name, "skill_version": row.skill_version,
        "category": row.category, "description": row.description,
        "applicable_market_regimes": row.applicable_market_regimes or [],
        "applicable_sector_states": row.applicable_sector_states or [],
        "applicable_horizons": row.applicable_horizons or [],
        "required_factors": row.required_factors or [], "optional_factors": row.optional_factors or [],
        "required_data_level": row.required_data_level, "entry_gate": row.entry_gate or {},
        "confirm_gate": row.confirm_gate or {}, "reject_gate": row.reject_gate or {},
        "exit_logic": row.exit_logic or {}, "lifecycle_state": row.lifecycle_state,
        "stage_labels": {code: STAGE_LABELS.get(code, code) for code in stage_codes},
        "validation_status": row.validation_status, "sample_size": row.sample_size,
        "precision": row.precision, "recall": row.recall, "hit_rate": row.hit_rate,
        "avg_excess_return": row.avg_excess_return, "profit_loss_ratio": row.profit_loss_ratio,
        "max_drawdown": row.max_drawdown, "brier_score": row.brier_score,
        "last_backtest_at": row.last_backtest_at.isoformat() if row.last_backtest_at else None,
        "last_recalibrated_at": row.last_recalibrated_at.isoformat() if row.last_recalibrated_at else None,
        "enabled": row.enabled, "definition": row.definition or {},
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def ensure_trading_skill_registry() -> None:
    """Idempotently seed definitions while preserving measured validation fields."""
    async with async_session() as session:
        existing = {
            row.skill_id: row
            for row in (await session.execute(select(TradingSkillRegistry))).scalars().all()
        }
        definition_fields = {
            "skill_name", "skill_version", "category", "description", "applicable_market_regimes",
            "applicable_sector_states", "applicable_horizons", "required_factors", "optional_factors",
            "required_data_level", "entry_gate", "confirm_gate", "reject_gate", "exit_logic", "definition",
        }
        for definition in SKILL_DEFINITIONS:
            row = existing.get(definition["skill_id"])
            if row is None:
                session.add(TradingSkillRegistry(**definition))
                continue
            for field in definition_fields:
                setattr(row, field, deepcopy(definition[field]))
            if definition["skill_id"] == "skill_09_auction_intraday_confirm" and not row.sample_size:
                row.validation_status = "REALTIME_SHADOW"
            row.updated_at = datetime.utcnow()
        rejected = {
            row.knowledge_id: row
            for row in (await session.execute(select(RejectedTradingKnowledge))).scalars().all()
        }
        for definition in REJECTED_KNOWLEDGE:
            row = rejected.get(definition["knowledge_id"])
            if row is None:
                session.add(RejectedTradingKnowledge(**definition, source="V5 Skill Registry specification"))
            else:
                row.claim = definition["claim"]
                row.rejection_reason = definition["rejection_reason"]
                row.category = definition["category"]
        await session.commit()


async def list_registered_skills(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    await ensure_trading_skill_registry()
    async with async_session() as session:
        statement = select(TradingSkillRegistry).order_by(TradingSkillRegistry.skill_id)
        if enabled_only:
            statement = statement.where(TradingSkillRegistry.enabled.is_(True))
        rows = (await session.execute(statement)).scalars().all()
    return [_public(row) for row in rows]


async def get_registered_skill(skill_id: str) -> dict[str, Any] | None:
    await ensure_trading_skill_registry()
    async with async_session() as session:
        row = await session.get(TradingSkillRegistry, skill_id)
    return _public(row) if row else None


async def list_rejected_knowledge() -> list[dict[str, Any]]:
    await ensure_trading_skill_registry()
    async with async_session() as session:
        rows = (await session.execute(
            select(RejectedTradingKnowledge).order_by(RejectedTradingKnowledge.knowledge_id)
        )).scalars().all()
    return [{
        "knowledge_id": row.knowledge_id, "claim": row.claim,
        "rejection_reason": row.rejection_reason, "category": row.category,
        "source": row.source, "enabled": row.enabled,
    } for row in rows]


def next_lifecycle_state(current: str, metrics: dict[str, Any], windows: list[dict[str, Any]]) -> tuple[str, str]:
    """Apply the documented promotion gate without accepting headline win rate alone."""
    sample_size = int(metrics.get("sample_size") or 0)
    positive_expectancy = (metrics.get("avg_excess_return") or 0) > 0
    calibrated = metrics.get("brier_score") is not None and metrics["brier_score"] <= 0.25
    stable_windows = [item for item in windows if int(item.get("sample_size") or 0) >= 50]
    stable_positive = len(stable_windows) >= 2 and all((item.get("avg_excess_return") or 0) > 0 for item in stable_windows)
    recent = (metrics.get("decay") or {}).get("30d") or {}
    decayed = bool(
        current == "ACTIVE"
        and int(recent.get("sample_size") or 0) >= 30
        and ((recent.get("avg_excess_return") or 0) <= 0 or (recent.get("brier_score") or 0) > 0.30)
    )
    if decayed:
        return "DEGRADED", "30日滚动优势或校准显著恶化"
    if sample_size < 300:
        return current if current in {"IDEA", "EXPERIMENTAL"} else "SHADOW", "LOW_SAMPLE"
    if current in {"IDEA", "EXPERIMENTAL"} and positive_expectancy:
        return "SHADOW", "至少一个样本外阶段为正，进入前向观察"
    if current in {"SHADOW", "DEGRADED"} and positive_expectancy and calibrated and stable_positive:
        return "ACTIVE", "样本量、多个样本外窗口、期望值与校准同时通过"
    return current, "未满足下一生命周期的全部门槛"


async def apply_validation_metrics(
    skill_id: str,
    metrics: dict[str, Any],
    windows: list[dict[str, Any]],
    *,
    completed_at: datetime,
) -> tuple[dict[str, Any], str]:
    async with async_session() as session:
        row = await session.get(TradingSkillRegistry, skill_id)
        if row is None:
            raise KeyError(skill_id)
        target, reason = next_lifecycle_state(row.lifecycle_state, metrics, windows)
        if target != row.lifecycle_state and target not in ALLOWED_TRANSITIONS.get(row.lifecycle_state, set()):
            target = row.lifecycle_state
            reason = "生命周期转换不合法，保持原状态"
        row.lifecycle_state = target
        row.validation_status = "LOW_SAMPLE" if int(metrics.get("sample_size") or 0) < 300 else "VALIDATED"
        row.sample_size = int(metrics.get("sample_size") or 0)
        for field in ("precision", "recall", "hit_rate", "avg_excess_return", "profit_loss_ratio", "max_drawdown", "brier_score"):
            setattr(row, field, metrics.get(field))
        row.last_backtest_at = completed_at
        row.last_recalibrated_at = completed_at
        row.updated_at = completed_at
        await session.commit()
        await session.refresh(row)
        return _public(row), reason
