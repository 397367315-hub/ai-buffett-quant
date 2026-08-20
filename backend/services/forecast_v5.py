"""V5 multi-factor forward forecast service.

This module is intentionally deterministic.  It consumes the existing truth
layer, market caches and macro dashboard, then produces a replayable model
snapshot.  It does not ask an LLM to invent probabilities and it never turns
an unavailable field into a neutral number.
"""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, func, select

from database import async_session
from models import (
    CausalChainActivationV5,
    DataQualityEvent,
    ForecastSnapshotV5,
    IndustryFundFlowDaily,
    IndustryValidationSnapshot,
    MarketBoard,
    MarketDataCache,
    MarketSentimentDaily,
    StockDailyBar,
    TruthDataConflict,
)
from services.data_collector import is_a_share_market_session, shanghai_now
from services.factor_registry_v5 import (
    CAUSAL_CHAINS,
    FACTOR_DEFINITIONS,
    causal_chain,
    causal_chains,
    factor_definitions,
)
from services.behavior_analysis_v5 import behavior_analysis_v5_service
from services.macro_dashboard import macro_dashboard_service
from services.market_decision_workbench import market_decision_workbench_service


V5_VERSION = "a-share-forecast-v5.0.0"
MODEL_VERSION = "markov-rule-ensemble-v1.0"
CALIBRATION_VERSION = "calibration-not-yet-qualified-v1"
CACHE_KEY = "forecast_v5_dashboard"

HORIZONS: tuple[dict[str, Any], ...] = (
    {"id": "short_1_3d", "label": "未来1-3个交易日", "sessions": 3, "weights": {"leading": 0.30, "propagation": 0.38, "confirmation": 0.32}},
    {"id": "week_1w", "label": "未来约1周", "sessions": 5, "weights": {"leading": 0.32, "propagation": 0.48, "confirmation": 0.20}},
    {"id": "month_1m", "label": "未来约1个月", "sessions": 20, "weights": {"leading": 0.48, "propagation": 0.38, "confirmation": 0.14}},
    {"id": "quarter_1q", "label": "未来约1季度", "sessions": 60, "weights": {"leading": 0.62, "propagation": 0.28, "confirmation": 0.10}},
)

STATE_PRIORS = {
    "S1": 0.64,
    "S2": 0.58,
    "S3": 0.48,
    "S4": 0.35,
    "S5": 0.27,
    "S0": 0.45,
}

HISTORICAL_REGIMES: tuple[dict[str, Any], ...] = (
    {
        "id": "regime_2015_deleveraging",
        "label": "2015杠杆牛市→去杠杆踩踏",
        "period": "2015",
        "vector": {"crowding_risk": 0.85, "market_amount_vs_ma20": 0.75, "market_breadth": -0.25, "northbound_flow": -0.20, "failed_limit_rate": 0.50},
        "path": "高杠杆与高换手在流动性收缩后转为被动卖出和连续负反馈。",
        "lesson": "历史案例只说明杠杆、拥挤和流动性可能形成非线性风险，不能直接复制走势。",
    },
    {
        "id": "regime_2016_circuit_breaker",
        "label": "2016熔断机制冲击",
        "period": "2016-01",
        "vector": {"crowding_risk": 0.55, "market_amount_vs_ma20": -0.25, "market_breadth": -0.80, "failed_limit_rate": 0.70, "structure_health": -0.55},
        "path": "脆弱市场叠加新交易规则预期，流动性真空放大了恐慌。",
        "lesson": "制度和交易规则变化本身可能成为核心变量，必须单独监控。",
    },
    {
        "id": "regime_2020_covid",
        "label": "2020新冠开市冲击",
        "period": "2020-02",
        "vector": {"crowding_risk": 0.20, "market_amount_vs_ma20": -0.35, "market_breadth": -0.90, "oil_change": -0.60, "vix_change": 0.90},
        "path": "外生冲击导致预期快速重定价，政策和流动性响应后才逐步修复。",
        "lesson": "外生事件不能只看跌幅，还要观察政策响应速度与流动性恢复。",
    },
    {
        "id": "regime_2021_long_rate",
        "label": "2021长端利率上行与高估值成长压缩",
        "period": "2021",
        "vector": {"us10y_change": 0.75, "nasdaq_change": -0.35, "crowding_risk": 0.65, "market_breadth": -0.15},
        "path": "全球贴现率抬升，亚洲成长资产和高久期估值承压。",
        "lesson": "长端利率是高久期成长资产的重要外部变量，但仍需观察A股自身传导。",
    },
    {
        "id": "regime_2024_small_cap_pressure",
        "label": "2024小微盘/量化流动性压力",
        "period": "2024-02",
        "vector": {"crowding_risk": 0.88, "market_amount_vs_ma20": -0.45, "market_breadth": -0.65, "structure_health": -0.60, "northbound_flow": -0.15},
        "path": "小微盘拥挤、流动性不足和资金风格偏移造成大小盘非线性分化。",
        "lesson": "政策支持大盘与微盘拥挤同时出现时，风格冲击可能快于指数。",
    },
    {
        "id": "regime_2024_policy_repair",
        "label": "2024-09-24强政策共振行情",
        "period": "2024-09-24",
        "vector": {"policy_support": 0.90, "market_amount_vs_ma20": 0.90, "market_breadth": 0.85, "sector_breadth": 0.70, "structure_health": 0.65},
        "path": "政策、低预期、成交和宽度同步修复，风险偏好快速扩散。",
        "lesson": "政策行情必须继续验证能否传导为盈利和持续资金。",
    },
    {
        "id": "regime_2026_08_19_resonance",
        "label": "2026-08-19多因子风险共振",
        "period": "2026-08-19",
        "vector": {"us10y_change": 0.70, "nasdaq_change": -0.55, "crowding_risk": 0.78, "failed_limit_rate": 0.60, "market_breadth": -0.72, "structure_health": -0.55},
        "path": "全球长端利率、海外科技、A股拥挤和高位负反馈同时指向防御。",
        "lesson": "该案例用于检验提前量，不允许用收盘结果倒推盘前因子。",
    },
)


def _num(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _pct(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _avg(values: list[float | None]) -> float | None:
    observed = [float(value) for value in values if value is not None and math.isfinite(value)]
    return sum(observed) / len(observed) if observed else None


def _zscore(value: float | None, values: list[float]) -> float | None:
    if value is None or len(values) < 3:
        return None
    mean = sum(values) / len(values)
    variance = sum((item - mean) ** 2 for item in values) / len(values)
    deviation = math.sqrt(variance)
    return round((value - mean) / deviation, 3) if deviation > 1e-9 else 0.0


def _percentile(value: float | None, values: list[float]) -> float | None:
    if value is None or not values:
        return None
    return round(sum(item <= value for item in values) / len(values) * 100, 1)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except (TypeError, ValueError):
        return None


def _age_minutes(timestamp: datetime | None, now: datetime) -> float | None:
    if timestamp is None:
        return None
    return max(0.0, (now.replace(tzinfo=None) - timestamp.replace(tzinfo=None)).total_seconds() / 60)


def _source_quality(level: str, observed: bool) -> float:
    if not observed:
        return 0.0
    return {"S": 1.0, "A": 0.92, "B": 0.78, "C": 0.58}.get(level, 0.45)


def _factor_state(signal: float | None) -> str:
    if signal is None:
        return "unavailable"
    if signal >= 0.18:
        return "improving"
    if signal <= -0.18:
        return "weakening"
    return "stable"


def _phase(now: datetime) -> str:
    minutes = now.hour * 60 + now.minute
    if minutes < 9 * 60 + 20:
        return "pre_market"
    if minutes < 9 * 60 + 30:
        return "auction_0925"
    if minutes < 10 * 60 + 40:
        return "morning_1040"
    if minutes < 11 * 60 + 35:
        return "midday_1130"
    if minutes < 13 * 60 + 30:
        return "midday"
    if minutes < 14 * 60 + 40:
        return "afternoon_1330"
    if minutes < 15 * 60 + 10:
        return "close_1500"
    return "after_close"


def _trend_label(score: float | None) -> str:
    if score is None:
        return "数据不足"
    if score >= 0.24:
        return "风险偏好增强"
    if score <= -0.24:
        return "风险偏好收缩"
    return "多空分歧"


class ForecastV5Service:
    _lock = asyncio.Lock()
    _memory: dict[str, Any] | None = None

    async def _cache(self) -> dict[str, Any]:
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, CACHE_KEY)
            return dict(row.payload) if row and isinstance(row.payload, dict) else {}
        except Exception:
            return {}

    async def _sentiment_history(self, limit: int = 120) -> list[dict[str, Any]]:
        try:
            async with async_session() as session:
                rows = list((await session.execute(
                    select(MarketSentimentDaily).order_by(desc(MarketSentimentDaily.trade_date)).limit(limit)
                )).scalars().all())
            rows.reverse()
            return [{
                "date": row.trade_date.isoformat(),
                "market_amount": row.market_amount,
                "amount_count": row.amount_count,
                "stock_count": row.stock_count,
                "up_count": row.up_count,
                "down_count": row.down_count,
                "average_turnover": row.average_turnover,
                "failed_limit_rate": row.failed_limit_rate,
                "limit_up_count": row.limit_up_count,
                "limit_down_count": row.limit_down_count,
            } for row in rows]
        except Exception:
            return []

    async def _sector_rows(self, target: date | None) -> list[dict[str, Any]]:
        if target is None:
            return []
        try:
            async with async_session() as session:
                latest = (await session.execute(select(func.max(IndustryFundFlowDaily.trade_date)).where(
                    IndustryFundFlowDaily.trade_date <= target,
                ))).scalar_one_or_none()
                if latest is None:
                    return []
                rows = (await session.execute(
                    select(IndustryFundFlowDaily, MarketBoard.name, MarketBoard.source)
                    .outerjoin(MarketBoard, (MarketBoard.board_type == "industry") & (MarketBoard.code == IndustryFundFlowDaily.board_code))
                    .where(IndustryFundFlowDaily.trade_date <= latest, IndustryFundFlowDaily.trade_date >= latest - timedelta(days=32))
                    .order_by(desc(IndustryFundFlowDaily.trade_date), desc(IndustryFundFlowDaily.main_net_inflow))
                )).all()
        except Exception:
            return []
        output = []
        for row, name, source in rows:
            output.append({
                "code": str(row.board_code),
                "name": str(name or row.board_code),
                "date": row.trade_date.isoformat(),
                "change_pct": _num(row.change_pct),
                "main_net_inflow": _num(row.main_net_inflow),
                "breadth": (
                    _num(row.up_count) / (_num(row.up_count) + _num(row.down_count)) * 100
                    if _num(row.up_count) is not None and _num(row.down_count) is not None and (_num(row.up_count) + _num(row.down_count)) > 0 else None
                ),
                "source": str(source or "IndustryFundFlowDaily"),
            })
        return output

    async def _macro(self, cached: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await asyncio.wait_for(macro_dashboard_service.dashboard(), timeout=10)
            if isinstance(result, dict):
                return result
        except Exception:
            pass
        return dict(cached.get("macro_dashboard") or {})

    def _factor(
        self,
        definition: dict[str, Any],
        *,
        value: float | None,
        signal: float | None,
        series: list[float],
        observed_at: datetime | None,
        now: datetime,
        data_date: str | None,
        source_override: str | None = None,
        level_override: str | None = None,
        missing_reason: str | None = None,
    ) -> dict[str, Any]:
        delta = None
        acceleration = None
        if len(series) >= 2:
            delta = series[-1] - series[-2]
        if len(series) >= 3:
            acceleration = (series[-1] - series[-2]) - (series[-2] - series[-3])
        age = _age_minutes(observed_at, now)
        ttl = int(definition["ttl_minutes"])
        freshness = None if age is None else round(max(0.0, min(1.0, 1 - age / ttl)), 3)
        observed = value is not None and signal is not None
        source_level = level_override or str(definition.get("source_level") or "B")
        reliability = _source_quality(source_level, observed)
        if freshness is not None:
            reliability = round(reliability * (0.55 + freshness * 0.45), 3)
        return {
            "id": definition["id"],
            "name": definition["name"],
            "layer": definition["layer"],
            "state": _factor_state(signal),
            "value": _pct(value, 4),
            "zscore": _zscore(signal, series),
            "percentile": _percentile(signal, series),
            "delta": _pct(delta, 4),
            "acceleration": _pct(acceleration, 4),
            "freshness": freshness,
            "reliability": reliability,
            "lead_score": definition["lead_score"],
            "causal_chain_ids": list(definition.get("chains") or []),
            "source": source_override or definition["source"],
            "source_level": source_level,
            "event_time": observed_at.isoformat() if observed_at else None,
            "publish_time": observed_at.isoformat() if observed_at else None,
            "available_time": observed_at.isoformat() if observed_at else None,
            "ingested_at": now.isoformat(),
            "updated_at": observed_at.isoformat() if observed_at else None,
            "ttl_minutes": ttl,
            "data_date": data_date,
            "quality_score": round(reliability * 100, 1),
            "observed": observed,
            "missing_reason": missing_reason if not observed else None,
            "signal": signal,
            "direction_score": signal,
        }

    @staticmethod
    def _latest_market(workbench: dict[str, Any]) -> tuple[str | None, datetime | None]:
        meta = workbench.get("meta") or {}
        target = str(meta.get("decision_date") or "")[:10] or None
        updated = _parse_time(meta.get("updated_at") or meta.get("calculated_at"))
        return target, updated

    def _build_factor_values(
        self,
        workbench: dict[str, Any],
        macro: dict[str, Any],
        history: list[dict[str, Any]],
        sectors: list[dict[str, Any]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        target_date, market_updated = self._latest_market(workbench)
        headline = workbench.get("headline_metrics") or {}
        state = workbench.get("market_state") or {}
        structure = workbench.get("structure_health") or {}
        crowding = workbench.get("crowding_risk") or {}
        alignment = workbench.get("volume_price_alignment") or {}
        v4 = workbench.get("market_way_v4") or {}
        capital = v4.get("capital_migration") or {}
        radar = v4.get("national_direction_radar") or {}
        market_updated = market_updated or now
        history_field = lambda key: [_num(item.get(key)) for item in history if _num(item.get(key)) is not None]
        latest_history = history[-1] if history else {}
        current_amount = _num(headline.get("market_amount")) or _num(latest_history.get("market_amount"))
        amount_values = history_field("market_amount")
        ma20 = sum(amount_values[-20:]) / 20 if len(amount_values) >= 20 else None
        amount_vs_ma20 = (current_amount / ma20 - 1) * 100 if current_amount is not None and ma20 else None
        up = _num(headline.get("up_count")) or _num(latest_history.get("up_count"))
        down = _num(headline.get("down_count")) or _num(latest_history.get("down_count"))
        breadth = up / (up + down) * 100 if up is not None and down is not None and up + down else None
        failed = _num(headline.get("failed_limit_rate"))
        if failed is None:
            failed = _num(latest_history.get("failed_limit_rate"))
        lt_up = _num(headline.get("limit_up")) or _num(latest_history.get("limit_up_count"))
        lt_down = _num(headline.get("limit_down")) or _num(latest_history.get("limit_down_count"))
        limit_balance = (lt_up - lt_down) / (lt_up + lt_down) * 100 if lt_up is not None and lt_down is not None and lt_up + lt_down else None
        market_score = _num(state.get("score"))
        structure_score = _num(structure.get("score"))
        crowding_score = _num(crowding.get("score"))
        turnover = _num(latest_history.get("average_turnover"))
        breadth_series = [item / (item + other) * 100 for item, other in zip(history_field("up_count"), history_field("down_count")) if item + other]
        amount_ratio_series = [((item / (sum(amount_values[max(0, index - 19):index + 1]) / min(20, index + 1)) - 1) * 100) for index, item in enumerate(amount_values) if index >= 4 and sum(amount_values[max(0, index - 19):index + 1])]
        macro_items = {str(item.get("key")): item for item in (macro.get("global_markets") or []) if isinstance(item, dict)}
        policy = macro.get("policy") or {}
        outlook = macro.get("a_share_outlook") or {}
        policy_signal = _clamp((_num(outlook.get("score")) or 0) / 100) if outlook.get("score") is not None else None
        radar_scores = [_num(item.get("score")) for item in radar.get("directions") or []]
        if policy_signal is None and radar_scores:
            policy_signal = _clamp(((_avg(radar_scores) or 50) - 50) / 50)
        positive_flows = [item for item in sectors if item.get("main_net_inflow") is not None]
        positive_flow_ratio = (
            sum((_num(item.get("main_net_inflow")) or 0) > 0 for item in positive_flows) / len(positive_flows) * 100
            if positive_flows else None
        )
        sector_breadth = _avg([_num(item.get("breadth")) for item in sectors])
        sector_persistence = positive_flow_ratio
        north = (macro.get("domestic_liquidity") or {}).get("northbound") or {}
        north_value = _num(north.get("net_inflow"))
        pit_items = [item for item in radar.get("directions") or [] if item.get("industry_validation", {}).get("status") in {"VERIFIED_IMPROVING", "SAMPLE_IMPROVING"}]
        pit_signal = _clamp((len(pit_items) / max(len(radar.get("directions") or []), 1) - 0.25) / 0.5) if radar.get("directions") else None
        risk_state = str((capital or {}).get("risk_appetite") or "")
        alpha_candidates = [item for item in workbench.get("candidates") or [] if not item.get("stale")]
        alpha_signal = _clamp((len(alpha_candidates) - 5) / 10) if alpha_candidates else None
        observed_at = market_updated
        macro_indicators = macro.get("macro_indicators") or {}

        def indicator(key: str) -> dict[str, Any] | None:
            item = macro_indicators.get(key)
            return item if isinstance(item, dict) and item.get("available") else None

        def indicator_source(item: dict[str, Any] | None) -> str | None:
            if not item:
                return None
            source = str(item.get("source") or "")
            return f"{source} · 缓存" if item.get("cache_used") else source

        def indicator_time(item: dict[str, Any] | None) -> datetime | None:
            return _parse_time(item.get("source_time")) if item else None

        us10y = macro_items.get("us10y") or {}
        us2y = macro_items.get("us2y") or {}
        vix = macro_items.get("vix") or {}
        credit_pulse = indicator("credit_pulse")
        industry_price = indicator("industry_price")
        capex = indicator("capex")
        us10y_change = _num(us10y.get("change_pct"))
        us2y_change = _num(us2y.get("change_pct"))
        vix_change = _num(vix.get("change_pct"))
        credit_value = _num((credit_pulse or {}).get("value"))
        industry_yoy = _num((industry_price or {}).get("yoy_pct"))
        industry_mom = _num((industry_price or {}).get("mom_pct"))
        capex_yoy = _num((capex or {}).get("yoy_pct"))
        capex_mom = _num((capex or {}).get("mom_pct"))
        definitions = {item["id"]: item for item in FACTOR_DEFINITIONS}
        values: dict[str, tuple[float | None, float | None, list[float], datetime | None, str | None, str | None]] = {
            "market_breadth": (breadth, _clamp((breadth - 50) / 50) if breadth is not None else None, breadth_series, observed_at, None, None),
            "market_amount_vs_ma20": (amount_vs_ma20, _clamp((amount_vs_ma20 or 0) / 25) if amount_vs_ma20 is not None else None, amount_ratio_series, observed_at, None, None),
            "market_amount_percentile": (_percentile(current_amount, amount_values), _clamp((_percentile(current_amount, amount_values) - 50) / 50) if _percentile(current_amount, amount_values) is not None else None, amount_ratio_series, observed_at, None, None),
            "average_turnover": (turnover, _clamp((turnover - 2) / 4) if turnover is not None else None, history_field("average_turnover"), observed_at, None, None),
            "failed_limit_rate": (failed, _clamp(-(failed or 0) / 35) if failed is not None else None, [-item / 35 for item in history_field("failed_limit_rate")], observed_at, None, None),
            "limit_up_down_balance": (limit_balance, _clamp((limit_balance or 0) / 100) if limit_balance is not None else None, [], observed_at, None, None),
            "market_state_score": (market_score, _clamp((market_score - 50) / 50) if market_score is not None else None, [], observed_at, None, None),
            "structure_health": (structure_score, _clamp((structure_score - 50) / 50) if structure_score is not None else None, [], observed_at, None, None),
            "crowding_risk": (crowding_score, _clamp(-(crowding_score - 50) / 50) if crowding_score is not None else None, [], observed_at, None, None),
            "sector_flow_persistence": (sector_persistence, _clamp((sector_persistence - 50) / 50) if sector_persistence is not None else None, [], observed_at, None, None),
            "sector_breadth": (sector_breadth, _clamp((sector_breadth - 50) / 50) if sector_breadth is not None else None, [], observed_at, None, None),
            "alpha_density": (float(len(alpha_candidates)) if alpha_candidates else None, alpha_signal, [], observed_at, None, None),
            "northbound_flow": (north_value, _clamp(north_value / 2_000_000_000) if north_value is not None else None, [], _parse_time(north.get("date")) or observed_at, None, None),
            "policy_support": (_num(outlook.get("score")), policy_signal, [], _parse_time(macro.get("updated_at")) or observed_at, None, "国内政策快照暂未返回可量化评分" if not policy_signal else None),
            "financial_pit_validation": (float(len(pit_items)) if radar.get("directions") else None, pit_signal, [], observed_at, None, "PIT产业验证样本不足" if pit_signal is None else None),
            "sp500_change": (_num((macro_items.get("sp500") or {}).get("change_pct")), _clamp((_num((macro_items.get("sp500") or {}).get("change_pct")) or 0) / 3) if (macro_items.get("sp500") or {}).get("change_pct") is not None else None, [], _parse_time((macro_items.get("sp500") or {}).get("source_time")) or _parse_time(macro.get("updated_at")), None, "新浪全球行情暂未返回"),
            "nasdaq_change": (_num((macro_items.get("nasdaq") or {}).get("change_pct")), _clamp((_num((macro_items.get("nasdaq") or {}).get("change_pct")) or 0) / 4) if (macro_items.get("nasdaq") or {}).get("change_pct") is not None else None, [], _parse_time((macro_items.get("nasdaq") or {}).get("source_time")) or _parse_time(macro.get("updated_at")), None, "新浪全球行情暂未返回"),
            "dxy_change": (_num((macro_items.get("dxy") or {}).get("change_pct")), _clamp(-(_num((macro_items.get("dxy") or {}).get("change_pct")) or 0) / 2) if (macro_items.get("dxy") or {}).get("change_pct") is not None else None, [], _parse_time((macro_items.get("dxy") or {}).get("source_time")) or _parse_time(macro.get("updated_at")), None, "新浪全球行情暂未返回"),
            "oil_change": (_num((macro_items.get("oil") or {}).get("change_pct")), _clamp(-(_num((macro_items.get("oil") or {}).get("change_pct")) or 0) / 4) if (macro_items.get("oil") or {}).get("change_pct") is not None else None, [], _parse_time((macro_items.get("oil") or {}).get("source_time")) or _parse_time(macro.get("updated_at")), None, "新浪全球行情暂未返回"),
            "gold_change": (_num((macro_items.get("gold") or {}).get("change_pct")), _clamp(-(_num((macro_items.get("gold") or {}).get("change_pct")) or 0) / 4) if (macro_items.get("gold") or {}).get("change_pct") is not None else None, [], _parse_time((macro_items.get("gold") or {}).get("source_time")) or _parse_time(macro.get("updated_at")), None, "新浪全球行情暂未返回"),
            "us10y_change": (
                _num(us10y.get("value")), _clamp(-us10y_change / 0.15), [], _parse_time(us10y.get("source_time")),
                f"{us10y.get('source')} · {'缓存' if us10y.get('cache_used') else '最新发布'}", None,
            ) if us10y_change is not None else (None, None, [], None, None, "FRED DGS10当前没有可核验的日变化值"),
            "us2y_change": (
                _num(us2y.get("value")), _clamp(-us2y_change / 0.15), [], _parse_time(us2y.get("source_time")),
                f"{us2y.get('source')} · {'缓存' if us2y.get('cache_used') else '最新发布'}", None,
            ) if us2y_change is not None else (None, None, [], None, None, "FRED DGS2当前没有可核验的日变化值"),
            "vix_change": (
                _num(vix.get("value")), _clamp(-vix_change / 12), [], _parse_time(vix.get("source_time")),
                f"{vix.get('source')} · {'缓存' if vix.get('cache_used') else '最新发布'}", None,
            ) if vix_change is not None else (None, None, [], None, None, "FRED VIXCLS当前没有可核验的日变化值"),
            "credit_pulse": (
                credit_value, _clamp(credit_value / 10), [], indicator_time(credit_pulse), indicator_source(credit_pulse), None,
            ) if credit_value is not None else (None, None, [], None, None, "社融信用脉冲代理当前没有可核验的最新发布值"),
            "industry_price_signal": (
                industry_yoy, _clamp(((industry_yoy or 0) * 0.7 + (industry_mom or 0) * 0.3) / 10), [], indicator_time(industry_price), indicator_source(industry_price), None,
            ) if industry_yoy is not None else (None, None, [], None, None, "企业商品价格指数当前没有可核验的最新发布值"),
            "capex_signal": (
                capex_yoy, _clamp(((capex_yoy or 0) * 0.7 + (capex_mom or 0) * 0.3) / 15), [], indicator_time(capex), indicator_source(capex), None,
            ) if capex_yoy is not None else (None, None, [], None, None, "城镇固定资产投资宏观代理当前没有可核验的最新发布值"),
        }
        result = []
        for factor_id, definition in definitions.items():
            value, signal, series, observed, source_override, missing_reason = values.get(factor_id, (None, None, [], None, None, "未配置"))
            result.append(self._factor(
                definition, value=value, signal=signal, series=[item for item in series if item is not None], observed_at=observed,
                now=now, data_date=target_date, source_override=source_override, missing_reason=missing_reason,
            ))
        return result

    @staticmethod
    def _data_health(factors: list[dict[str, Any]], macro: dict[str, Any], now: datetime) -> dict[str, Any]:
        total_weight = sum(float(item.get("lead_score") or 0.5) for item in factors)
        observed_weight = sum(float(item.get("lead_score") or 0.5) for item in factors if item.get("observed"))
        fresh_weight = sum(float(item.get("lead_score") or 0.5) for item in factors if item.get("observed") and (item.get("freshness") or 0) >= 0.25)
        completeness = round(observed_weight / total_weight * 100, 1) if total_weight else 0.0
        fresh_pct = round(fresh_weight / total_weight * 100, 1) if total_weight else 0.0
        if completeness >= 95:
            level = "正常"
        elif completeness >= 85:
            level = "可预测但提示"
        elif completeness >= 70:
            level = "降低置信度"
        else:
            level = "禁止高置信度预测"
        missing = [{
            "factor_id": item["id"], "name": item["name"], "source": item["source"], "source_level": item["source_level"],
            "action": item.get("missing_reason") or "等待下一次有时间戳的提供方数据",
        } for item in factors if not item.get("observed")]
        stale = [{"factor_id": item["id"], "name": item["name"], "freshness": item.get("freshness"), "updated_at": item.get("updated_at")} for item in factors if item.get("observed") and (item.get("freshness") or 0) < 0.25]
        sources = sorted({str(item.get("source")) for item in factors if item.get("observed") and item.get("source")})
        return {
            "completeness_pct": completeness,
            "fresh_coverage_pct": fresh_pct,
            "level": level,
            "high_confidence_allowed": completeness >= 85,
            "confidence_ceiling_pct": round(min(88.0, completeness), 1),
            "observed_factor_count": sum(bool(item.get("observed")) for item in factors),
            "total_factor_count": len(factors),
            "missing_factors": missing,
            "stale_factors": stale,
            "sources": sources,
            "macro_source_status": macro.get("source_status") or {},
            "rule": "95%以上正常；85-95%提示；70-85%降低置信度；低于70%禁止高置信度预测。缺失字段不以0或中性值替代。",
            "data_cutoff_time": max((item.get("updated_at") or "" for item in factors if item.get("updated_at")), default=None),
            "checked_at": now.isoformat(),
        }

    @staticmethod
    def _chain_activation(chain: dict[str, Any], factors_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
        observed = [factors_by_id[item] for item in chain["factor_ids"] if item in factors_by_id and factors_by_id[item].get("observed")]
        missing = [item for item in chain["factor_ids"] if item not in factors_by_id or not factors_by_id[item].get("observed")]
        if not observed:
            return {**deepcopy(chain), "activation_pct": None, "status": "数据不足", "observed_factor_count": 0, "missing_factor_ids": missing, "evidence": [], "model_note": "链条没有可用观测，不计算激活度。"}
        support = []
        evidence = []
        for factor in observed:
            signal = _num(factor.get("signal"))
            # The registry keeps the public factor fields stable.  signal is
            # an internal normalized contribution and is not shown as a raw fact.
            if signal is None:
                signal = _num(factor.get("direction_score"))
            if signal is None:
                signal = 0.0
            if chain["direction"] == "defensive":
                signal = -signal
            support.append((signal + 1) / 2 * (factor.get("reliability") or 0))
            evidence.append(f"{factor['name']}：{factor['state']}")
        activation = sum(support) / max(len(support), 1) * 100
        if len(observed) < max(2, math.ceil(len(chain["factor_ids"]) * 0.5)):
            activation = min(activation, 55.0)
            status = "部分激活"
        elif activation >= 67:
            status = "激活"
        elif activation >= 52:
            status = "部分激活"
        else:
            status = "未激活"
        return {
            **deepcopy(chain), "activation_pct": _pct(activation, 1), "status": status,
            "observed_factor_count": len(observed), "missing_factor_ids": missing, "evidence": evidence[:6],
            "model_note": "激活度综合方向、变化、可靠度与新鲜度；不是收益概率。",
        }

    @staticmethod
    def _resonance(factors: list[dict[str, Any]]) -> dict[str, Any]:
        # Keep the normalized signal internal to the model response.  The
        # public factor contract still exposes value/zscore/delta/acceleration.
        factor_map = {item["id"]: item for item in factors}
        for factor in factor_map.values():
            value = _num(factor.get("value"))
            if factor["id"] in {"market_breadth", "sector_breadth"}:
                factor["direction_score"] = _clamp((value - 50) / 50) if value is not None else None
            elif factor["id"] in {"failed_limit_rate", "crowding_risk"}:
                factor["direction_score"] = _clamp(-(value - 50) / 50) if value is not None else None
            else:
                factor["direction_score"] = _num(factor.get("direction_score"))
            factor["signal"] = factor["direction_score"]
        activations = [ForecastV5Service._chain_activation(chain, factor_map) for chain in CAUSAL_CHAINS]
        defensive = [_num(item.get("activation_pct")) for item in activations if item["direction"] == "defensive"]
        offensive = [_num(item.get("activation_pct")) for item in activations if item["direction"] == "offensive"]
        defensive_score = _avg(defensive)
        offensive_score = _avg(offensive)
        if defensive_score is None and offensive_score is None:
            preference = "mixed"
            label = "风险偏好数据不足"
        elif (defensive_score or 0) > (offensive_score or 0) + 8:
            preference = "risk_preference_contraction"
            label = "风险偏好收缩"
        elif (offensive_score or 0) > (defensive_score or 0) + 8:
            preference = "risk_preference_enhancement"
            label = "风险偏好增强"
        else:
            preference = "mixed"
            label = "风险偏好多空分歧"
        active = sorted([item for item in activations if item.get("activation_pct") is not None], key=lambda item: item["activation_pct"], reverse=True)
        return {
            "chains": activations,
            "defensive_resonance_pct": _pct(defensive_score, 1),
            "offensive_resonance_pct": _pct(offensive_score, 1),
            "resonance_formation_pct": _pct(max(defensive_score or 0, offensive_score or 0), 1),
            "risk_preference": preference,
            "risk_preference_label": label,
            "active_chain_ids": [item["id"] for item in active if item.get("status") in {"激活", "部分激活"}],
            "method": "因果链方向一致性 × 因子可靠度 × 数据新鲜度；共振形成度不代表指数涨跌概率。",
        }

    @staticmethod
    def _weighted_signal(factors: list[dict[str, Any]], horizon: dict[str, Any]) -> tuple[float | None, dict[str, Any]]:
        weights = horizon["weights"]
        numerator = 0.0
        denominator = 0.0
        trace = []
        for item in factors:
            if not item.get("observed"):
                continue
            signal = _num(item.get("signal"))
            if signal is None:
                signal = _num(item.get("direction_score"))
            if signal is None:
                continue
            layer_weight = float(weights.get(item.get("layer"), 0.1))
            reliability = float(item.get("reliability") or 0.0)
            lead = float(item.get("lead_score") or 0.5)
            contribution_weight = layer_weight * reliability * (0.6 + lead * 0.4)
            numerator += signal * contribution_weight
            denominator += contribution_weight
            trace.append({"factor_id": item["id"], "weight": round(contribution_weight, 4), "signal": round(signal, 4)})
        return (numerator / denominator if denominator else None), {"weights": weights, "observed_factor_count": len(trace), "factor_trace": sorted(trace, key=lambda item: abs(item["signal"] * item["weight"]), reverse=True)[:8]}

    @staticmethod
    def _scenario(horizon: dict[str, Any], name: str, probability: float, trigger: list[str], factors: list[str], beneficiaries: list[str], pressured: list[str], verify: list[str], invalidation: list[str]) -> dict[str, Any]:
        return {
            "id": name,
            "label": {"main": "主情景", "upside": "向上情景", "downside": "向下情景"}.get(name, name),
            "probability_pct": _pct(probability, 1),
            "horizon": horizon["id"],
            "trigger_conditions": trigger,
            "key_factors": factors,
            "benefited_sectors": beneficiaries,
            "pressured_sectors": pressured,
            "verification_points": verify,
            "invalidation_points": invalidation,
        }

    def _forecast_for_horizon(self, horizon: dict[str, Any], state_code: str, factors: list[dict[str, Any]], resonance: dict[str, Any], sector_forecasts: list[dict[str, Any]], health: dict[str, Any]) -> dict[str, Any]:
        score, trace = self._weighted_signal(factors, horizon)
        prior = STATE_PRIORS.get(state_code, STATE_PRIORS["S0"])
        defensive = (_num(resonance.get("defensive_resonance_pct")) or 50) / 100
        offensive = (_num(resonance.get("offensive_resonance_pct")) or 50) / 100
        signal = score or 0.0
        up = 0.25 + (prior - 0.45) * 0.9 + signal * 0.14 + (offensive - defensive) * 0.06
        down = 0.25 - (prior - 0.45) * 0.78 - signal * 0.12 + (defensive - offensive) * 0.06
        up = max(0.08, min(0.74, up))
        down = max(0.08, min(0.74, down))
        total = up + down
        if total > 0.88:
            up *= 0.88 / total
            down *= 0.88 / total
        neutral = max(0.04, 1 - up - down)
        total = up + down + neutral
        up, down, neutral = up / total, down / total, neutral / total
        probabilities = {"upside": up * 100, "main": neutral * 100, "downside": down * 100}
        if up >= max(neutral, down):
            main_label = "风险偏好增强并尝试扩散"
        elif down >= max(up, neutral):
            main_label = "风险偏好收缩并向防御迁移"
        else:
            main_label = "结构性震荡与板块分化"
        top_factors = [item["factor_id"] for item in trace["factor_trace"][:5]]
        strong_sectors = [item["name"] for item in sector_forecasts[:3] if item.get("state") in {"启势", "强化"}]
        weak_sectors = [item["name"] for item in sector_forecasts[:3] if item.get("state") in {"退潮", "分歧"}]
        beneficiaries = strong_sectors or (["防御与低波动方向"] if down >= up else ["待板块资金确认"])
        pressured = weak_sectors or (["高拥挤、高Beta方向"] if down >= up else ["尚未形成明确受损板块"])
        common_verify = ["下一观察窗口复核市场宽度与成交额是否同向", "观察核心板块资金是否连续而非单日异动"]
        common_invalidation = ["市场宽度、成交额和核心板块资金同步反向", "高位负反馈或政策/海外事件改变现有传导链"]
        scenarios = [
            self._scenario(horizon, "main", max(up, neutral, down), ["当前状态转移先验与主要因子方向保持"], top_factors[:4], beneficiaries, pressured, common_verify, common_invalidation),
            self._scenario(horizon, "upside", up, ["成交额相对均值改善且宽度扩散", "核心板块资金连续为正"], top_factors[:4], strong_sectors or ["政策支持与产业验证方向"], weak_sectors or ["缺乏承接的后排题材"], ["连续两个观察窗口宽度改善", "板块龙头、中军、后排同步"], ["成交放大但宽度不扩散", "龙头冲高回落并出现负反馈"]),
            self._scenario(horizon, "downside", down, ["炸板率/拥挤升高或海外风险因子继续恶化"], [item["factor_id"] for item in trace["factor_trace"][:4]], ["低估值、防御、现金流稳定方向"], pressured, ["宽度持续恶化", "资金迁徙持续两次以上观察"], ["风险偏好快速修复且防御链被反向验证"]),
        ]
        confidence = min(float(health.get("confidence_ceiling_pct") or 0), 88.0)
        if health.get("completeness_pct", 0) < 70:
            confidence = min(confidence, 49.0)
        elif health.get("completeness_pct", 0) < 85:
            confidence = min(confidence, 64.0)
        return {
            "id": horizon["id"], "label": horizon["label"], "sessions": horizon["sessions"],
            "direction_score": _pct(score, 4), "state": main_label,
            "main_scenario": scenarios[0], "scenarios": scenarios,
            "probabilities": {key: _pct(value, 1) for key, value in probabilities.items()},
            "trigger_conditions": scenarios[0]["trigger_conditions"], "key_factors": top_factors,
            "benefited_sectors": beneficiaries, "pressured_sectors": pressured,
            "verification_points": common_verify, "invalidation_points": common_invalidation,
            "model": {"model_version": MODEL_VERSION, "calibration_version": CALIBRATION_VERSION, "transition_prior": round(prior, 4), "signal_score": _pct(score, 4), "trace": trace, "probability_source": "Markov状态先验 + 分层因子Ensemble；非LLM生成"},
            "confidence_pct": _pct(confidence, 1),
        }

    @staticmethod
    def _sector_forecasts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row["code"]].append(row)
        output = []
        for code, items in grouped.items():
            items.sort(key=lambda item: item["date"], reverse=True)
            latest = items[0]
            flow_values = [_num(item.get("main_net_inflow")) for item in items[:20]]
            flow_values = [item for item in flow_values if item is not None]
            positive_days = sum(item > 0 for item in flow_values)
            persistence = positive_days / len(flow_values) * 100 if flow_values else None
            change = _num(latest.get("change_pct"))
            breadth = _num(latest.get("breadth"))
            flow = _num(latest.get("main_net_inflow"))
            if change is None and flow is None:
                state = "数据不足"
            elif (change or 0) > 0 and (flow or 0) > 0 and (breadth or 0) >= 55 and (persistence or 0) >= 55:
                state = "强化"
            elif (change or 0) > 0 and (flow or 0) > 0:
                state = "启势"
            elif (change or 0) < 0 and (flow or 0) < 0:
                state = "退潮"
            else:
                state = "分歧"
            output.append({
                "code": code, "name": latest["name"], "state": state,
                "latest_change_pct": _pct(change, 2), "latest_main_net_inflow": flow,
                "breadth_pct": _pct(breadth, 1), "flow_persistence_pct": _pct(persistence, 1),
                "source": latest["source"], "data_date": latest["date"],
                "horizon_states": {item["id"]: state for item in HORIZONS},
                "stage_probabilities": {"启势": _pct(100 if state == "启势" else 25, 1), "强化": _pct(100 if state == "强化" else 20, 1), "分歧": _pct(100 if state == "分歧" else 35, 1), "退潮": _pct(100 if state == "退潮" else 10, 1)},
                "reason": f"最新涨跌幅{change:+.2f}%、主力净流入{flow:+.0f}、资金连续性{persistence:.1f}%" if change is not None and flow is not None and persistence is not None else "板块资金或宽度字段不完整，暂不扩大结论",
                "risk": "单日资金异动不等于主线确认" if persistence is None or persistence < 55 else "需继续验证龙头、中军与后排同步",
            })
        return sorted(output, key=lambda item: (item.get("latest_main_net_inflow") is not None, item.get("latest_main_net_inflow") or -10**30), reverse=True)[:16]

    @staticmethod
    def _alpha_seeds(workbench: dict[str, Any], sectors: list[dict[str, Any]], behavior: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        sector_by_name = {item["name"]: item for item in sectors}
        candidates = []
        for item in (workbench.get("candidates") or []) + ((workbench.get("daily_short_term_recommendations") or {}).get("items") or []):
            code = str(item.get("code") or "")
            if not code or any(row.get("code") == code for row in candidates):
                continue
            sector = str(item.get("sector") or item.get("industry") or "待核验板块")
            sector_item = sector_by_name.get(sector)
            score = _num(item.get("score"))
            stale = bool(item.get("stale"))
            if score is None:
                stage = "A1"
            elif score >= 78 and not stale:
                stage = "A3"
            elif score >= 60 and not stale:
                stage = "A2"
            else:
                stage = "A1"
            candidates.append({
                "code": code, "name": str(item.get("name") or code), "sector": sector,
                "alpha_stage": stage, "sector_stage": sector_item.get("state") if sector_item else "待核验",
                "horizon_states": {h["id"]: "观察" if stale else "待确认" for h in HORIZONS},
                "key_factors": [str(item.get("strategy") or "候选池因子")],
                "largest_risk": "候选样本可能跨日或板块传导未确认" if stale else "高分不等于买入，需等待市场与板块确认",
                "confirmation_conditions": ["所属板块资金连续为正", "个股相对板块Alpha继续增强", "财务/公告PIT没有反证"],
                "invalidation_conditions": ["板块资金转负", "相对板块强度跌破观察阈值", "出现重大公告或流动性反向证据"],
                "score": _pct(score, 1), "source": "V4候选/每日短期研究候选", "is_recommendation": False,
                "behavior_state": (behavior or {}).get("market_psychology_state") or "数据不足",
                "behavior_transition": (behavior or {}).get("psychology_transition") or "状态待核验",
                "crowding_state": (behavior or {}).get("crowding_state") or "数据不足",
                "fomo_risk": (behavior or {}).get("fomo_state") or "数据不足",
                "panic_risk": (behavior or {}).get("panic_state") or "数据不足",
            })
        return candidates[:12]

    @staticmethod
    def _turning_points(factors: list[dict[str, Any]], resonance: dict[str, Any]) -> dict[str, list[str]]:
        improving = [item["name"] for item in factors if item.get("state") == "improving"][:6]
        weakening = [item["name"] for item in factors if item.get("state") == "weakening"][:6]
        return {
            "increase_offensive_probability": ["成交额相对20日均值转正且市场宽度连续扩大", "核心板块资金持续为正并出现龙头/中军/后排扩散", *improving[:2]],
            "increase_defensive_probability": ["炸板率和高位拥挤同步上升", "海外风险因子继续恶化且A股宽度不修复", *weakening[:2]],
            "falsify_current_path": ["下一观察窗口数据与当前主要因子方向相反", "关键数据源发生冲突且无法按来源等级解决", f"当前共振结构被{resonance.get('risk_preference_label', '新数据')}推翻"],
        }

    @staticmethod
    def _similar(vector: dict[str, float | None], case: dict[str, Any]) -> dict[str, Any]:
        pairs = []
        same = []
        different = []
        for key, expected in (case.get("vector") or {}).items():
            actual = _num(vector.get(key))
            if actual is None:
                different.append(f"{key}当前缺少观测")
                continue
            distance = abs(actual - float(expected))
            pairs.append(max(0.0, 1 - min(distance, 2) / 2))
            if distance <= 0.35:
                same.append(key)
            else:
                different.append(key)
        similarity = sum(pairs) / len(pairs) * 100 if pairs else 0.0
        return {"case_id": case["id"], "label": case["label"], "period": case["period"], "similarity_pct": _pct(similarity, 1), "similar_factors": same, "different_factors": different, "historical_path": case["path"], "do_not_copy_reason": case["lesson"], "observed_dimensions": len(pairs)}

    async def _persist(self, payload: dict[str, Any], target: date, phase: str, now: datetime) -> None:
        health = payload.get("data_health") or {}
        try:
            async with async_session() as session:
                row = (await session.execute(select(ForecastSnapshotV5).where(
                    ForecastSnapshotV5.forecast_date == target,
                    ForecastSnapshotV5.phase == phase,
                    ForecastSnapshotV5.forecast_version == V5_VERSION,
                ))).scalar_one_or_none()
                values = {
                    "model_version": MODEL_VERSION, "data_cutoff_time": _parse_time(payload.get("data_cutoff_time")) or now,
                    "data_completeness_pct": float(health.get("completeness_pct") or 0), "confidence_ceiling_pct": float(health.get("confidence_ceiling_pct") or 0),
                    "payload": payload, "generated_at": now, "updated_at": now,
                }
                if row is None:
                    session.add(ForecastSnapshotV5(forecast_date=target, phase=phase, forecast_version=V5_VERSION, **values))
                else:
                    for key, value in values.items():
                        setattr(row, key, value)
                for chain in (payload.get("resonance") or {}).get("chains") or []:
                    activation = (await session.execute(select(CausalChainActivationV5).where(
                        CausalChainActivationV5.forecast_date == target,
                        CausalChainActivationV5.phase == phase,
                        CausalChainActivationV5.chain_id == chain["id"],
                        CausalChainActivationV5.forecast_version == V5_VERSION,
                    ))).scalar_one_or_none()
                    chain_values = {"activation_pct": chain.get("activation_pct"), "direction": chain.get("direction") or "state", "payload": chain, "generated_at": now}
                    if activation is None:
                        session.add(CausalChainActivationV5(forecast_date=target, phase=phase, chain_id=chain["id"], forecast_version=V5_VERSION, **chain_values))
                    else:
                        for key, value in chain_values.items():
                            setattr(activation, key, value)
                await session.commit()
        except Exception as exc:
            print(f"V5 forecast persistence failed: {type(exc).__name__}")

    async def build(self, *, force: bool = False) -> dict[str, Any]:
        now = shanghai_now().replace(tzinfo=None)
        phase = _phase(now)
        cached = await self._cache()
        cached_date = str(cached.get("forecast_date") or "")[:10]
        cache_time = _parse_time(cached.get("generated_at"))
        cache_seconds = 90 if is_a_share_market_session(now) else 900
        if not force and cached and cached_date and cache_time and str(cached.get("phase") or "") == phase and (now - cache_time).total_seconds() <= cache_seconds:
            return {**cached, "cache_used": True}
        async with self._lock:
            cached = await self._cache()
            if not force and cached and str(cached.get("phase") or "") == phase and _parse_time(cached.get("generated_at")) and (now - (_parse_time(cached.get("generated_at")) or now)).total_seconds() <= cache_seconds:
                return {**cached, "cache_used": True}
            workbench = await market_decision_workbench_service.get(force=force)
            macro = await self._macro(cached)
            history = await self._sentiment_history()
            target_raw, workbench_updated = self._latest_market(workbench)
            target = date.fromisoformat(target_raw) if target_raw else (date.fromisoformat(history[-1]["date"]) if history else now.date())
            sectors_raw = await self._sector_rows(target)
            factors = self._build_factor_values(workbench, macro, history, sectors_raw, now)
            sectors = self._sector_forecasts(sectors_raw)
            behavior = await behavior_analysis_v5_service.evaluate(workbench, factors, sectors, now, target)
            factors.extend(behavior.get("factors") or [])
            # Convert the private normalized signal to a response field only
            # while computing the model; it is not a raw market fact.
            for factor in factors:
                factor["direction_score"] = _clamp(((_num(factor.get("value")) or 50) - 50) / 50) if factor["id"] in {"market_breadth", "sector_breadth", "market_state_score", "structure_health"} and factor.get("value") is not None else factor.get("direction_score")
                factor["signal"] = factor.get("direction_score")
            health = self._data_health(factors, macro, now)
            resonance = self._resonance(factors)
            state_code = str((workbench.get("market_state") or {}).get("state_code") or "S0")
            forecasts = [self._forecast_for_horizon(item, state_code, factors, resonance, sectors, health) for item in HORIZONS]
            seeds = self._alpha_seeds(workbench, sectors, behavior)
            vector = {item["id"]: item.get("signal") for item in factors}
            analogs = sorted([self._similar(vector, case) for case in HISTORICAL_REGIMES], key=lambda item: item.get("similarity_pct") or 0, reverse=True)[:5]
            payload = {
                "version": V5_VERSION, "model_version": MODEL_VERSION, "calibration_version": CALIBRATION_VERSION,
                "generated_at": now.isoformat(), "forecast_date": target.isoformat(), "phase": phase,
                "data_cutoff_time": health.get("data_cutoff_time") or (workbench_updated.isoformat() if workbench_updated else now.isoformat()),
                "cache_used": False, "data_health": health,
                "risk_preference": {"state": resonance["risk_preference"], "label": resonance["risk_preference_label"], "evidence": resonance.get("active_chain_ids") or []},
                "behavior": behavior,
                "timeline": forecasts,
                "scenarios": {item["id"]: item["scenarios"] for item in forecasts},
                "resonance": resonance,
                "factors": {"all": factors, "leading": [item for item in factors if item["layer"] == "leading"], "propagation": [item for item in factors if item["layer"] == "propagation"], "confirmation": [item for item in factors if item["layer"] == "confirmation"]},
                "sector_forecasts": sectors,
                "alpha_seeds": seeds,
                "historical_analogs": analogs,
                "turning_points": self._turning_points(factors, resonance),
                "audit": {"no_future_data": True, "probability_source": "Markov状态先验 + 分层因子Ensemble + 完整度置信上限", "llm_role": "只负责解释、事件抽取和反证生成，不直接产生概率", "sources": health.get("sources") or [], "missing_policy": health.get("rule")},
            }
            for factor in payload["factors"]["all"]:
                factor.pop("signal", None)
                factor.pop("direction_score", None)
            for category in ("leading", "propagation", "confirmation"):
                for factor in payload["factors"][category]:
                    factor.pop("signal", None)
                    factor.pop("direction_score", None)
            await self._persist(payload, target, phase, now)
            try:
                async with async_session() as session:
                    row = await session.get(MarketDataCache, CACHE_KEY)
                    if row is None:
                        session.add(MarketDataCache(key=CACHE_KEY, payload=payload))
                    else:
                        row.payload = payload
                    await session.commit()
            except Exception as exc:
                print(f"V5 forecast cache write failed: {type(exc).__name__}")
            self._memory = payload
            return payload

    async def dashboard(self, *, force: bool = False) -> dict[str, Any]:
        return await self.build(force=force)

    async def factors(self, *, kind: str = "market", factor_id: str | None = None, force: bool = False) -> dict[str, Any]:
        data = await self.build(force=force)
        factors = data.get("factors") or {}
        values = factors.get("all") or []
        if kind in {"leading", "propagation", "confirmation"}:
            values = factors.get(kind) or []
        if factor_id:
            values = [item for item in values if item.get("id") == factor_id]
        return {"version": V5_VERSION, "data_cutoff_time": data.get("data_cutoff_time"), "kind": kind, "factors": values, "count": len(values), "data_health": data.get("data_health")}

    async def sectors(self, *, force: bool = False) -> dict[str, Any]:
        data = await self.build(force=force)
        return {"version": V5_VERSION, "data_cutoff_time": data.get("data_cutoff_time"), "sectors": data.get("sector_forecasts") or [], "data_health": data.get("data_health")}

    async def stocks(self, *, symbol: str | None = None, force: bool = False) -> dict[str, Any]:
        data = await self.build(force=force)
        items = data.get("alpha_seeds") or []
        if symbol:
            normalized = str(symbol).upper().replace(".SH", "").replace(".SZ", "")
            items = [item for item in items if str(item.get("code")) == normalized or str(item.get("code")).upper() == str(symbol).upper()]
        return {"version": V5_VERSION, "data_cutoff_time": data.get("data_cutoff_time"), "stocks": items, "count": len(items), "data_health": data.get("data_health")}

    async def chains(self, *, chain_id: str | None = None, active: bool = False, force: bool = False) -> dict[str, Any]:
        if not active and not force and chain_id is None:
            return {"version": V5_VERSION, "chains": causal_chains(), "count": len(CAUSAL_CHAINS)}
        data = await self.build(force=force)
        chains = (data.get("resonance") or {}).get("chains") or []
        if chain_id:
            chains = [item for item in chains if item.get("id") == chain_id]
        if active:
            chains = [item for item in chains if item.get("status") in {"激活", "部分激活"}]
        return {"version": V5_VERSION, "chains": chains, "count": len(chains), "data_cutoff_time": data.get("data_cutoff_time"), "resonance": data.get("resonance")}

    async def history(self, *, similar: bool = False, force: bool = False) -> dict[str, Any]:
        data = await self.build(force=force)
        if similar:
            return {"version": V5_VERSION, "current_forecast_date": data.get("forecast_date"), "analogs": data.get("historical_analogs") or [], "method": "多维因子向量距离；历史案例仅作先验，不复制后续走势。"}
        return {"version": V5_VERSION, "regimes": [deepcopy(item) for item in HISTORICAL_REGIMES], "count": len(HISTORICAL_REGIMES), "point_in_time_rule": "历史案例必须使用当时已可获得数据，不能用结果反推因子。"}

    async def health(self, *, force: bool = False) -> dict[str, Any]:
        data = await self.build(force=force)
        return {"version": V5_VERSION, "data_health": data.get("data_health"), "forecast": {"forecast_date": data.get("forecast_date"), "phase": data.get("phase"), "generated_at": data.get("generated_at"), "model_version": data.get("model_version")}, "sources": (data.get("audit") or {}).get("sources") or []}

    async def conflicts(self, limit: int = 100) -> dict[str, Any]:
        try:
            async with async_session() as session:
                rows = list((await session.execute(select(TruthDataConflict).order_by(desc(TruthDataConflict.detected_at)).limit(limit))).scalars().all())
                quality = list((await session.execute(select(DataQualityEvent).where(DataQualityEvent.status == "OPEN").order_by(desc(DataQualityEvent.detected_at)).limit(limit))).scalars().all())
            return {"conflicts": [{"id": row.id, "type": row.conflict_type, "fact_key": row.fact_key, "trade_date": row.research_trade_date.isoformat(), "sources": row.source_keys, "values": row.conflicting_values, "resolution": row.resolution, "status": row.status, "detected_at": row.detected_at.isoformat() if row.detected_at else None} for row in rows], "quality_events": [{"component": row.component, "event_type": row.event_type, "severity": row.severity, "trade_date": row.research_trade_date.isoformat() if row.research_trade_date else None, "message": row.message, "action": row.acquisition_action, "detected_at": row.detected_at.isoformat() if row.detected_at else None} for row in quality]}
        except Exception as exc:
            return {"conflicts": [], "quality_events": [], "error": type(exc).__name__}

    async def freshness(self, *, force: bool = False) -> dict[str, Any]:
        data = await self.build(force=force)
        factors = (data.get("factors") or {}).get("all") or []
        return {"version": V5_VERSION, "checked_at": data.get("generated_at"), "factors": [{"id": item["id"], "name": item["name"], "updated_at": item.get("updated_at"), "ttl_minutes": item.get("ttl_minutes"), "freshness": item.get("freshness"), "quality_score": item.get("quality_score"), "observed": item.get("observed"), "source": item.get("source")} for item in factors], "data_health": data.get("data_health")}


forecast_v5_service = ForecastV5Service()
