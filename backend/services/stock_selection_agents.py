"""Source-backed multi-agent workflow for real-time A-share stock selection.

The agents are deterministic on purpose: ranking and risk controls remain
inspectable even when an external LLM is unavailable. Every recommendation
contains the individual agent outputs that produced it.
"""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from datetime import timedelta
from typing import Awaitable, Callable

from sqlalchemy import select

from config import settings
from database import async_session
from models import MarketDataCache, StockDailyBar
from services.data_collector import as_float, as_int, is_a_share_market_session, normalize_board_code, shanghai_now
from services.macro_policy_news import macro_policy_news_collector
from services.quant_scorer import MarketRegime
from services.research_protocol import research_protocol
from services.data_collector import collector
from services.a_stock_data import A_STOCK_DATA_SKILL, calculate_indicators
from services.cyclical_valuation import cycle_guard_from_stock
from services.history_cache import history_cache
from services.horizon_analysis import (
    HORIZON_CONFIG,
    VALID_HORIZONS,
    combined_agent_weights,
    horizon_potential_analyzer,
)
from services.stock_features import (
    FINANCIAL_FIELDS,
    LOCKUP_FIELDS,
    SHAREHOLDER_FIELDS,
    stock_feature_service,
)
from quant.risk import assess_stock_risk


VALID_SELECTION_MODES = {"quick", "full"}
VALID_RISK_PROFILES = {"conservative", "balanced", "aggressive"}

PROFILE_CONFIG = {
    "conservative": {
        "label": "稳健",
        "weights": {"technical": 0.22, "fundamental": 0.28, "capital": 0.16, "safety": 0.26, "news": 0.08},
        "position_cap": 12,
        "stop_loss": 0.05,
    },
    "balanced": {
        "label": "均衡",
        "weights": {"technical": 0.28, "fundamental": 0.20, "capital": 0.26, "safety": 0.18, "news": 0.08},
        "position_cap": 18,
        "stop_loss": 0.07,
    },
    "aggressive": {
        "label": "进取",
        "weights": {"technical": 0.32, "fundamental": 0.14, "capital": 0.32, "safety": 0.14, "news": 0.08},
        "position_cap": 25,
        "stop_loss": 0.09,
    },
}

SELECTION_FACTOR_DEFAULTS = {
    "enabled": False,
    "preset": "off",
    "use_change_pct": True,
    "change_pct": [-3.0, 8.0],
    "use_volume_ratio": True,
    "volume_ratio_min": 1.2,
    "use_turnover": True,
    "turnover_pct": [1.0, 15.0],
    "use_market_cap": True,
    "market_cap_yi": [30.0, 3000.0],
    "use_pe": False,
    "pe_max": 80.0,
    "use_roe": False,
    "roe_min": 0.0,
    "use_main_inflow": False,
    "main_net_inflow_yi_min": 0.0,
    "require_profitable": False,
    "exclude_star_market": False,
    "exclude_gem": False,
    "exclude_bse": True,
}

SELECTION_FACTOR_PRESETS = {
    "off": {**SELECTION_FACTOR_DEFAULTS},
    "short": {
        **SELECTION_FACTOR_DEFAULTS,
        "enabled": True,
        "preset": "short",
        "change_pct": [0.0, 7.0],
        "volume_ratio_min": 1.2,
        "turnover_pct": [2.0, 15.0],
        "market_cap_yi": [30.0, 1200.0],
        "use_main_inflow": True,
        "main_net_inflow_yi_min": 0.0,
    },
    "long": {
        **SELECTION_FACTOR_DEFAULTS,
        "enabled": True,
        "preset": "long",
        "change_pct": [-4.0, 6.0],
        "use_volume_ratio": False,
        "turnover_pct": [0.3, 10.0],
        "market_cap_yi": [50.0, 5000.0],
        "use_pe": True,
        "pe_max": 50.0,
        "use_roe": True,
        "roe_min": 8.0,
        "require_profitable": True,
    },
}

SELECTION_FACTOR_SCHEMA = [
    {"key": "change_pct", "label": "当日涨跌幅", "type": "range", "min": -20, "max": 20, "step": 0.5, "unit": "%", "toggle": "use_change_pct"},
    {"key": "volume_ratio_min", "label": "最低量比（严格大于）", "type": "number", "min": 0, "max": 20, "step": 0.1, "toggle": "use_volume_ratio", "comparison": "gt"},
    {"key": "turnover_pct", "label": "换手率", "type": "range", "min": 0, "max": 100, "step": 0.5, "unit": "%", "toggle": "use_turnover"},
    {"key": "market_cap_yi", "label": "总市值", "type": "range", "min": 0, "max": 100000, "step": 10, "unit": "亿元", "toggle": "use_market_cap"},
    {"key": "pe_max", "label": "最高PE(TTM)", "type": "number", "min": 0, "max": 1000, "step": 1, "toggle": "use_pe"},
    {"key": "roe_min", "label": "最低ROE", "type": "number", "min": -100, "max": 200, "step": 0.5, "unit": "%", "toggle": "use_roe"},
    {"key": "main_net_inflow_yi_min", "label": "最低主力净流入", "type": "number", "min": -1000, "max": 1000, "step": 0.1, "unit": "亿元", "toggle": "use_main_inflow"},
]


def _clamp(value: float, lower: float = 0, upper: float = 100) -> float:
    return max(lower, min(upper, value))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = _mean(values)
    return math.sqrt(sum((value - average) ** 2 for value in values) / len(values))


def _optional_number(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalise_sector(value: object) -> str:
    """Keep a user-selected sector exact, bounded, and safe for matching."""
    return " ".join(str(value or "").split())[:60]


def _factor_number(value: object, key: str, lower: float, upper: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} 必须是数字") from exc
    if not math.isfinite(number) or not lower <= number <= upper:
        raise ValueError(f"{key} 必须在 {lower:g} 到 {upper:g} 之间")
    return round(number, 4)


def normalize_selection_factors(raw: dict | None) -> dict:
    """Validate one user-controlled factor set before it reaches the ranking pipeline."""
    if raw is None:
        return dict(SELECTION_FACTOR_DEFAULTS)
    if not isinstance(raw, dict):
        raise ValueError("factor_filters 必须是对象")

    preset = str(raw.get("preset") or "custom").strip().lower()
    if preset not in {*SELECTION_FACTOR_PRESETS, "custom"}:
        raise ValueError("factor_filters.preset 仅支持 off、short、long 或 custom")
    base = dict(SELECTION_FACTOR_PRESETS.get(preset, SELECTION_FACTOR_DEFAULTS))
    allowed = set(SELECTION_FACTOR_DEFAULTS)
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"factor_filters 包含未知字段：{', '.join(unknown)}")
    base.update(raw)
    base["preset"] = preset

    boolean_keys = {
        "enabled", "use_change_pct", "use_volume_ratio", "use_turnover",
        "use_market_cap", "use_pe", "use_roe", "use_main_inflow",
        "require_profitable", "exclude_star_market", "exclude_gem", "exclude_bse",
    }
    for key in boolean_keys:
        if not isinstance(base.get(key), bool):
            raise ValueError(f"factor_filters.{key} 必须是布尔值")

    ranges = {
        "change_pct": (-20.0, 20.0),
        "turnover_pct": (0.0, 100.0),
        "market_cap_yi": (0.0, 100000.0),
    }
    for key, (lower, upper) in ranges.items():
        values = base.get(key)
        if not isinstance(values, (list, tuple)) or len(values) != 2:
            raise ValueError(f"factor_filters.{key} 必须是包含上下限的数组")
        start = _factor_number(values[0], f"factor_filters.{key}[0]", lower, upper)
        end = _factor_number(values[1], f"factor_filters.{key}[1]", lower, upper)
        if start > end:
            raise ValueError(f"factor_filters.{key} 下限不能高于上限")
        base[key] = [start, end]

    number_limits = {
        "volume_ratio_min": (0.0, 20.0),
        "pe_max": (0.0, 1000.0),
        "roe_min": (-100.0, 200.0),
        "main_net_inflow_yi_min": (-1000.0, 1000.0),
    }
    for key, (lower, upper) in number_limits.items():
        base[key] = _factor_number(base.get(key), f"factor_filters.{key}", lower, upper)
    return base


class StockSelectionAgentService:
    """Runs a visible research pipeline over verified real-time market data."""

    _HISTORY_LOOKBACK_DAYS = 420
    _QUICK_ANALYSIS_LIMIT = 45
    _FULL_ANALYSIS_LIMIT = 80

    @staticmethod
    async def _candidate_snapshot(
        cache_key: str,
        fetcher: Callable[[], Awaitable[dict]],
        *,
        prefer_cache: bool = False,
    ) -> dict:
        """Persist verified candidates and reuse them when an upstream is unavailable."""
        cached: dict = {}
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, cache_key)
            if row and isinstance(row.payload, dict):
                cached = dict(row.payload)
        except Exception:
            cached = {}

        if prefer_cache and cached.get("stocks"):
            return {
                **cached,
                "source": "cache",
                "upstream_source": cached.get("source") or "eastmoney",
                "is_realtime": False,
                "source_updated_at": cached.get("source_updated_at") or cached.get("cached_at"),
                "cache_used": True,
                "cache_reason": "market_closed",
            }

        source_failed = False
        try:
            result = await fetcher()
        except Exception:
            result = {}
            source_failed = True
        if isinstance(result, dict) and result.get("stocks"):
            if result.get("data_date"):
                payload = {**result, "cached_at": shanghai_now().isoformat()}
                try:
                    async with async_session() as session:
                        row = await session.get(MarketDataCache, cache_key)
                        if row is None:
                            session.add(MarketDataCache(key=cache_key, payload=payload))
                        else:
                            row.payload = payload
                        await session.commit()
                except Exception:
                    pass
            return result
        if cached.get("stocks") and (source_failed or result.get("error")):
            return {
                **cached,
                "source": "cache",
                "upstream_source": cached.get("source") or "eastmoney",
                "is_realtime": False,
                "source_updated_at": cached.get("source_updated_at") or cached.get("cached_at"),
                "cache_used": True,
                "cache_reason": "upstream_unavailable",
            }
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _is_market_session() -> bool:
        return is_a_share_market_session()

    @staticmethod
    def _apply_factor_filters(stocks: list[dict], config: dict) -> tuple[list[dict], dict]:
        before_count = len(stocks)
        if not config.get("enabled"):
            return list(stocks), {
                "enabled": False,
                "config": config,
                "before_count": before_count,
                "matched_count": before_count,
                "rejected_count": 0,
                "rejection_counts": {},
            }

        selected: list[dict] = []
        rejection_counts: dict[str, int] = defaultdict(int)

        def require_number(stock: dict, key: str, reason: str) -> tuple[float | None, list[str]]:
            value = _optional_number(stock.get(key))
            return value, [] if value is not None else [f"{reason}_missing"]

        for stock in stocks:
            reasons: list[str] = []
            code = str(stock.get("code") or "")
            if config["exclude_star_market"] and code.startswith(("688", "689")):
                reasons.append("star_market")
            if config["exclude_gem"] and code.startswith(("300", "301", "302")):
                reasons.append("gem")
            if config["exclude_bse"] and (code.startswith(("4", "8", "920"))):
                reasons.append("bse")

            if config["use_change_pct"]:
                value, missing = require_number(stock, "change_pct", "change_pct")
                reasons.extend(missing)
                if value is not None and not config["change_pct"][0] <= value <= config["change_pct"][1]:
                    reasons.append("change_pct")
            if config["use_volume_ratio"]:
                value, missing = require_number(stock, "volume_ratio", "volume_ratio")
                reasons.extend(missing)
                if value is not None and value <= config["volume_ratio_min"]:
                    reasons.append("volume_ratio")
            if config["use_turnover"]:
                value, missing = require_number(stock, "turnover", "turnover")
                reasons.extend(missing)
                if value is not None and not config["turnover_pct"][0] <= value <= config["turnover_pct"][1]:
                    reasons.append("turnover")
            if config["use_market_cap"]:
                raw_value, missing = require_number(stock, "market_cap", "market_cap")
                reasons.extend(missing)
                value = raw_value / 1e8 if raw_value is not None else None
                if value is not None and not config["market_cap_yi"][0] <= value <= config["market_cap_yi"][1]:
                    reasons.append("market_cap")
            pe = _optional_number(stock.get("pe"))
            if config["require_profitable"] and (pe is None or pe <= 0):
                reasons.append("profitable")
            if config["use_pe"]:
                if pe is None:
                    reasons.append("pe_missing")
                elif pe <= 0 or pe > config["pe_max"]:
                    reasons.append("pe")
            if config["use_roe"]:
                value, missing = require_number(stock, "roe", "roe")
                reasons.extend(missing)
                if value is not None and value < config["roe_min"]:
                    reasons.append("roe")
            if config["use_main_inflow"]:
                raw_value, missing = require_number(stock, "main_net_inflow", "main_inflow")
                reasons.extend(missing)
                value = raw_value / 1e8 if raw_value is not None else None
                if value is not None and value < config["main_net_inflow_yi_min"]:
                    reasons.append("main_inflow")

            if reasons:
                for reason in set(reasons):
                    rejection_counts[reason] += 1
            else:
                selected.append(stock)

        return selected, {
            "enabled": True,
            "config": config,
            "before_count": before_count,
            "matched_count": len(selected),
            "rejected_count": before_count - len(selected),
            "rejection_counts": dict(sorted(rejection_counts.items())),
        }

    async def _load_histories(self, stock_codes: list[str]) -> dict[str, list[dict]]:
        """Load cached daily bars in one query instead of issuing one request per stock."""
        if not stock_codes:
            return {}
        cutoff = shanghai_now().date() - timedelta(days=self._HISTORY_LOOKBACK_DAYS)
        try:
            async with async_session() as session:
                rows = (await session.execute(
                    select(StockDailyBar)
                    .where(
                        StockDailyBar.stock_code.in_(stock_codes),
                        StockDailyBar.trade_date >= cutoff,
                    )
                    .order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
                )).scalars().all()
        except Exception as exc:
            print(f"Stock selection history load failed: {type(exc).__name__}")
            return {}

        histories: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            if row.close_price is None or row.close_price <= 0:
                continue
            histories[row.stock_code].append({
                "date": row.trade_date.isoformat(),
                "close": float(row.close_price),
                "high": float(row.high_price) if row.high_price is not None else None,
                "low": float(row.low_price) if row.low_price is not None else None,
                "volume": int(row.volume) if row.volume is not None else None,
                "amount": int(row.amount) if row.amount is not None else None,
            })
        return dict(histories)

    async def _refresh_candidate_histories(
        self,
        candidates: list[dict],
        *,
        source: str,
        data_date: str | None,
    ) -> dict:
        """Top up the short end of cached daily bars before technical scoring."""
        if source != "eastmoney" or not data_date:
            return {"status": "skipped", "expected_date": data_date, "failed": []}
        return await history_cache.refresh_recent_stock_histories(candidates, data_date)

    @staticmethod
    def _rsi(closes: list[float], period: int = 14) -> float | None:
        if len(closes) <= period:
            return None
        deltas = [closes[index] - closes[index - 1] for index in range(1, len(closes))][-period:]
        average_gain = _mean([max(delta, 0) for delta in deltas])
        average_loss = _mean([abs(min(delta, 0)) for delta in deltas])
        if average_loss == 0:
            return 100.0
        relative_strength = average_gain / average_loss
        return round(100 - 100 / (1 + relative_strength), 1)

    @staticmethod
    def _max_drawdown(closes: list[float]) -> float:
        peak = 0.0
        maximum = 0.0
        for close in closes:
            peak = max(peak, close)
            if peak:
                maximum = max(maximum, (peak - close) / peak * 100)
        return round(maximum, 1)

    @staticmethod
    def _preliminary_priority(stock: dict) -> float:
        flow_score = _clamp(as_int(stock.get("main_net_inflow")) / 1e8 * 2, -20, 35)
        volume_score = _clamp(as_float(stock.get("volume_ratio")) * 5, 0, 20)
        change_score = _clamp(as_float(stock.get("change_pct")) * 3, -15, 20)
        return flow_score + volume_score + change_score + len(stock.get("selection_sources") or []) * 5

    def _technical_agent(self, stock: dict, history: list[dict]) -> dict:
        price = as_float(stock.get("price"))
        closes = [as_float(row.get("close")) for row in history if as_float(row.get("close")) > 0]
        if price > 0 and (not closes or abs(closes[-1] - price) > 0.0001):
            closes.append(price)

        history_points = len(history)
        ma5 = _mean(closes[-5:]) if len(closes) >= 5 else None
        ma10 = _mean(closes[-10:]) if len(closes) >= 10 else None
        ma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
        ma60 = _mean(closes[-60:]) if len(closes) >= 60 else None
        rsi = self._rsi(closes)
        volume_ratio = _optional_number(stock.get("volume_ratio"))
        change_pct = as_float(stock.get("change_pct"))
        indicator_history = [*history]
        if price > 0 and (not indicator_history or abs(as_float(indicator_history[-1].get("close")) - price) > 0.0001):
            current_bar = {"close": price}
            current_high = _optional_number(stock.get("high"))
            current_low = _optional_number(stock.get("low"))
            if current_high is not None:
                current_bar["high"] = current_high
            if current_low is not None:
                current_bar["low"] = current_low
            current_volume = _optional_number(stock.get("volume"))
            current_amount = _optional_number(stock.get("amount"))
            if current_volume is not None:
                current_bar["volume"] = current_volume
            if current_amount is not None:
                current_bar["amount"] = current_amount
            indicator_history.append(current_bar)
        indicators = calculate_indicators(indicator_history)
        macd_hist = indicators["macd"].get("hist")
        kdj_j = indicators["kdj"].get("j")
        boll_upper = indicators["boll"].get("upper")

        score = 50.0
        evidence: list[str] = []
        risks: list[str] = []
        if ma20 is None:
            evidence.append("近20日日线缓存不足，技术结论置信度降低")
            score = 48.0
        else:
            if price >= ma20:
                score += 12
                evidence.append(f"现价高于MA20 {((price / ma20 - 1) * 100):.1f}%")
            else:
                score -= 12
                risks.append(f"现价低于MA20 {((1 - price / ma20) * 100):.1f}%")
            if ma5 and ma10 and ma5 > ma10 > ma20:
                score += 14
                evidence.append("MA5 > MA10 > MA20，短中期趋势向上")
            elif ma5 and ma10 and ma5 < ma10 < ma20:
                score -= 14
                risks.append("MA5 < MA10 < MA20，短中期趋势偏弱")
            elif ma5 and price >= ma5:
                score += 4
            elif ma5:
                score -= 4

        if rsi is not None:
            if 45 <= rsi <= 70:
                score += 8
                evidence.append(f"RSI {rsi:.0f}，动能处于可持续区间")
            elif rsi > 80:
                score -= 9
                risks.append(f"RSI {rsi:.0f}，短线偏热")
            elif rsi < 30:
                score += 3
                evidence.append(f"RSI {rsi:.0f}，存在超卖修复可能")

        if volume_ratio is not None and 1.2 < volume_ratio <= 4:
            score += 8
            evidence.append(f"量比 {volume_ratio:.2f}（>1.2），成交活跃度匹配")
        elif volume_ratio is not None and volume_ratio > 6:
            score -= 5
            risks.append(f"量比 {volume_ratio:.2f}，短线交易过热")
        if 0 < change_pct <= 7:
            score += 5
        elif change_pct > 8:
            score -= 7
            risks.append(f"当日涨幅 {change_pct:.2f}%，避免追高")
        elif change_pct < -5:
            score -= 6
            risks.append(f"当日跌幅 {change_pct:.2f}%，趋势需重新确认")

        if macd_hist is not None:
            if macd_hist > 0:
                score += 4
                evidence.append(f"MACD柱体 {macd_hist:.3f} 为正，短期动能偏强")
            else:
                score -= 4
                risks.append(f"MACD柱体 {macd_hist:.3f} 为负，动能尚未修复")
        if kdj_j is not None and kdj_j > 100:
            score -= 3
            risks.append(f"KDJ-J {kdj_j:.1f}，短线偏热")
        if boll_upper is not None and price > boll_upper:
            score -= 4
            risks.append("收盘价高于布林上轨，追高风险增加")

        score = round(_clamp(score), 1)
        signal = "看多" if score >= 64 else "看空" if score <= 38 else "中性"
        recent_lows = [as_float(row.get("low")) for row in history[-20:] if as_float(row.get("low")) > 0]
        recent_highs = [as_float(row.get("high")) for row in history[-20:] if as_float(row.get("high")) > 0]
        support = min(recent_lows) if recent_lows else (ma20 or price)
        resistance = max(recent_highs) if recent_highs else (ma60 or price)
        return {
            "agent": "技术面 Agent",
            "skill": "a-stock-data 口径：MA、MACD、RSI、KDJ、BOLL、量价与支撑阻力",
            "score": score,
            "signal": signal,
            "summary": evidence[0] if evidence else "技术指标尚未形成明确方向",
            "evidence": evidence,
            "risks": risks,
            "metrics": {
                "ma5": round(ma5, 2) if ma5 else None,
                "ma10": round(ma10, 2) if ma10 else None,
                "ma20": round(ma20, 2) if ma20 else None,
                "ma60": round(ma60, 2) if ma60 else None,
                "rsi14": rsi,
                "rsi6": indicators["rsi"].get("rsi6"),
                "rsi24": indicators["rsi"].get("rsi24"),
                "macd_dif": indicators["macd"].get("dif"),
                "macd_dea": indicators["macd"].get("dea"),
                "macd_hist": macd_hist,
                "kdj_k": indicators["kdj"].get("k"),
                "kdj_d": indicators["kdj"].get("d"),
                "kdj_j": kdj_j,
                "boll_upper": boll_upper,
                "boll_middle": indicators["boll"].get("middle"),
                "boll_lower": indicators["boll"].get("lower"),
                "volume_ma5": indicators["volume"].get("ma5"),
                "indicator_volume_ratio": indicators["volume"].get("ratio"),
                "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
                "support": round(support, 2) if support else None,
                "resistance": round(resistance, 2) if resistance else None,
                "history_points": history_points,
            },
        }

    def _fundamental_agent(self, stock: dict) -> dict:
        pe = _optional_number(stock.get("pe"))
        pb = _optional_number(stock.get("pb"))
        roe = _optional_number(stock.get("roe"))
        gross_margin = _optional_number(stock.get("gross_margin"))
        revenue_growth = _optional_number(stock.get("revenue_growth"))
        deducted_growth = _optional_number(stock.get("deducted_profit_growth"))
        ocf_to_profit = _optional_number(stock.get("ocf_to_profit"))
        debt_ratio = _optional_number(stock.get("debt_ratio"))
        receivable_ratio = _optional_number(stock.get("receivable_to_revenue"))
        sector = _normalise_sector(stock.get("sector"))
        cycle = cycle_guard_from_stock({**stock, "sector": sector})
        is_financial_sector = any(term in sector for term in ("银行", "证券", "保险", "金融"))
        score = 50.0
        evidence: list[str] = []
        risks: list[str] = []
        data_gaps: list[str] = []

        if cycle.get("is_cyclical"):
            normalized_pe = _optional_number(cycle.get("normalized_pe"))
            evidence.append(f"{cycle.get('cyclical_sector_label')}按周期口径估值，TTM PE不单独作为低估加分")
            if cycle.get("pe_inversion_risk"):
                score -= 22
                risks.append("周期盈利高位触发PE反向风险")
            elif cycle.get("cycle_phase") in {"peak", "contraction"}:
                score -= 12
                risks.append(f"周期处于{cycle.get('cycle_phase_label')}阶段，低PE不构成安全边际")
            elif normalized_pe is not None:
                if normalized_pe <= 20:
                    score += 10
                    evidence.append(f"标准化PE {normalized_pe:.1f}")
                elif normalized_pe > 40:
                    score -= 10
                    risks.append(f"标准化PE {normalized_pe:.1f}，中枢估值偏高")
            elif not cycle.get("cycle_data_available"):
                data_gaps.append("周期阶段/标准化PE")
            if pe is not None and pe <= 0 and cycle.get("cycle_phase") in {"trough", "recovery"}:
                evidence.append("周期底部PE失真，转看PB/ROE与现金流")
        elif pe is None:
            data_gaps.append("PE(TTM)")
        elif pe <= 0:
            score -= 20
            risks.append("PE为负，当前盈利质量需重点核查")
        elif pe <= 20:
            score += 18
            evidence.append(f"PE {pe:.1f}，估值处于相对合理区间")
        elif pe <= 40:
            score += 8
            evidence.append(f"PE {pe:.1f}，估值可接受")
        elif pe <= 80:
            score -= 5
            risks.append(f"PE {pe:.1f}，估值偏高")
        else:
            score -= 14
            risks.append(f"PE {pe:.1f}，估值压力较大")

        if roe is None:
            data_gaps.append("ROE")
        elif roe >= 20:
            score += 12
            evidence.append(f"ROE {roe:.1f}%，盈利能力较强")
        elif roe >= 12:
            score += 7
            evidence.append(f"ROE {roe:.1f}%，盈利能力稳健")
        elif roe > 0:
            score -= 4
        else:
            score -= 12
            risks.append(f"ROE {roe:.1f}%，净资产收益偏弱")

        if pb is not None and pb > 8:
            score -= 5
            risks.append(f"PB {pb:.1f}，需关注资产定价压力")
        elif pb is not None and 0 < pb <= 2:
            score += 4

        if gross_margin is None:
            data_gaps.append("毛利率")
        elif gross_margin >= 30:
            score += 7
            evidence.append(f"毛利率 {gross_margin:.1f}%")
        elif gross_margin >= 15:
            score += 3
        else:
            score -= 5
            risks.append(f"毛利率 {gross_margin:.1f}%，盈利空间偏薄")

        if revenue_growth is None:
            data_gaps.append("营收增速")
        elif revenue_growth > 10:
            score += 6
            evidence.append(f"营收同比增长 {revenue_growth:.1f}%")
        elif revenue_growth < 0:
            score -= 7
            risks.append(f"营收同比下降 {abs(revenue_growth):.1f}%")

        if deducted_growth is None:
            data_gaps.append("扣非净利润增速")
        elif deducted_growth > 10:
            score += 7
            evidence.append(f"扣非净利润同比增长 {deducted_growth:.1f}%")
        elif deducted_growth < 0:
            score -= 9
            risks.append(f"扣非净利润同比下降 {abs(deducted_growth):.1f}%")

        if ocf_to_profit is None:
            data_gaps.append("经营现金流/净利润")
        elif ocf_to_profit >= 1:
            score += 8
            evidence.append(f"经营现金流/净利润 {ocf_to_profit:.2f}")
        elif ocf_to_profit >= 0.8:
            score += 4
        elif ocf_to_profit < 0:
            score -= 12
            risks.append(f"经营现金流/净利润 {ocf_to_profit:.2f}，现金流为负")
        else:
            score -= 5
            risks.append(f"经营现金流/净利润 {ocf_to_profit:.2f}，低于0.8")

        if debt_ratio is None:
            data_gaps.append("资产负债率")
        elif is_financial_sector:
            evidence.append(f"资产负债率 {debt_ratio:.1f}%，金融行业不套用制造业阈值")
        elif debt_ratio <= 50:
            score += 4
        elif debt_ratio > 70:
            score -= 7
            risks.append(f"资产负债率 {debt_ratio:.1f}%")

        if receivable_ratio is None:
            data_gaps.append("应收/营收比")
        elif receivable_ratio <= 20:
            score += 3
        elif receivable_ratio > 50:
            score -= 8
            risks.append(f"应收/营收比 {receivable_ratio:.1f}%")

        financial_meta = (stock.get("_feature_meta") or {}).get("financial") or {}
        if financial_meta.get("status") != "available":
            risks.append("财务主表未覆盖，本轮不对缺失指标打中性分")

        score = round(_clamp(score), 1)
        signal = "看多" if score >= 64 else "看空" if score <= 38 else "中性"
        return {
            "agent": "基本面与财务排雷 Agent",
            "skill": "周期/非周期估值、盈利质量、现金流真实性、负债与应收排雷",
            "score": score,
            "signal": signal,
            "summary": evidence[0] if evidence else "基本面指标未形成明显优势",
            "evidence": evidence,
            "risks": risks,
            "metrics": {
                "pe": pe, "pb": pb, "roe": roe, "gross_margin": gross_margin,
                "revenue_growth": revenue_growth, "deducted_profit_growth": deducted_growth,
                "ocf_to_profit": ocf_to_profit, "debt_ratio": debt_ratio,
                "receivable_to_revenue": receivable_ratio,
                "is_cyclical": cycle.get("is_cyclical"),
                "cycle_phase": cycle.get("cycle_phase"),
                "cycle_phase_label": cycle.get("cycle_phase_label"),
                "normalized_pe": cycle.get("normalized_pe"),
                "pe_inversion_risk": cycle.get("pe_inversion_risk"),
            },
            "data_gaps": data_gaps,
            "source": financial_meta,
        }

    def _capital_flow_agent(self, stock: dict) -> dict:
        main_inflow_raw = stock.get("main_net_inflow")
        inflow_pct_raw = stock.get("main_net_inflow_pct")
        volume_ratio_raw = stock.get("volume_ratio")
        main_inflow = as_int(main_inflow_raw) if main_inflow_raw not in (None, "", "-") else None
        inflow_yi = main_inflow / 1e8 if main_inflow is not None else None
        inflow_pct = as_float(inflow_pct_raw) if inflow_pct_raw not in (None, "", "-") else None
        volume_ratio = as_float(volume_ratio_raw) if volume_ratio_raw not in (None, "", "-") else None
        turnover = as_float(stock.get("turnover"))
        score = 50.0
        evidence: list[str] = []
        risks: list[str] = []

        if main_inflow is None:
            evidence.append("来源未提供主力资金流，资金面不计入正负因子")
        elif main_inflow > 0:
            flow_boost = min(28.0, 5 + math.log10(1 + main_inflow / 1e6) * 7)
            score += flow_boost
            evidence.append(f"主力净流入 {inflow_yi:+.2f} 亿")
        elif main_inflow < 0:
            flow_penalty = min(28.0, 5 + math.log10(1 + abs(main_inflow) / 1e6) * 7)
            score -= flow_penalty
            risks.append(f"主力净流出 {inflow_yi:+.2f} 亿")
        else:
            evidence.append("主力资金暂未形成净流入优势")

        if inflow_pct is not None and inflow_pct >= 5:
            score += 7
            evidence.append(f"主力净流入占比 {inflow_pct:.1f}%")
        elif inflow_pct is not None and inflow_pct <= -5:
            score -= 7
            risks.append(f"主力净流入占比 {inflow_pct:.1f}%")
        if volume_ratio is not None and 1.2 < volume_ratio <= 4:
            score += 6
        if 3 <= turnover <= 15:
            score += 5
            evidence.append(f"换手率 {turnover:.1f}%，流动性适中")
        elif turnover > 25:
            score -= 6
            risks.append(f"换手率 {turnover:.1f}%，交易波动较大")

        score = round(_clamp(score), 1)
        signal = "看多" if score >= 64 else "看空" if score <= 38 else "中性"
        return {
            "agent": "资金面 Agent",
            "skill": "主力净流入、资金强度、量比与换手",
            "score": score,
            "signal": signal,
            "summary": evidence[0] if evidence else "资金面未形成显著优势",
            "evidence": evidence,
            "risks": risks,
            "metrics": {
                "main_net_inflow": main_inflow,
                "main_net_inflow_yi": round(inflow_yi, 2) if inflow_yi is not None else None,
                "main_net_inflow_pct": round(inflow_pct, 2) if inflow_pct is not None else None,
                "turnover": round(turnover, 2),
                "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
            },
        }

    def _risk_agent(
        self,
        stock: dict,
        history: list[dict],
        profile: str,
        announcements: list[dict] | None = None,
    ) -> dict:
        price = as_float(stock.get("price"))
        closes = [as_float(row.get("close")) for row in history if as_float(row.get("close")) > 0]
        if price > 0 and (not closes or abs(closes[-1] - price) > 0.0001):
            closes.append(price)
        returns = [((closes[index] / closes[index - 1]) - 1) * 100 for index in range(1, len(closes)) if closes[index - 1] > 0]
        volatility = _std(returns[-20:])
        drawdown = self._max_drawdown(closes[-20:])
        change_pct = abs(as_float(stock.get("change_pct")))
        score = 78.0
        risks: list[str] = []
        evidence: list[str] = []
        if len(history) < 60:
            score -= 12
            risks.append("近60日日线不足，风险估计保守处理")
        if volatility > 4:
            score -= 25
            risks.append(f"20日波动率 {volatility:.1f}%，波动较高")
        elif volatility > 2.5:
            score -= 12
            risks.append(f"20日波动率 {volatility:.1f}%，波动中等")
        else:
            evidence.append(f"20日波动率 {volatility:.1f}%，价格波动可控")
        if drawdown > 18:
            score -= 18
            risks.append(f"近20日最大回撤 {drawdown:.1f}%")
        elif drawdown > 10:
            score -= 8
        if change_pct > 8:
            score -= 10
            risks.append("单日振幅过大，避免以情绪价追入")

        structural = assess_stock_risk(stock, announcements)
        score = score * 0.65 + structural["score"] * 0.35
        if structural["hard_blocked"]:
            score = min(score, 20.0)
        evidence.extend(structural["evidence"][:3])
        risks.extend(structural["hard_blocks"])
        risks.extend(structural["warnings"][:3])
        if structural["missing"]:
            risks.append("排雷数据缺口：" + "、".join(structural["missing"][:4]))
        score = round(_clamp(score), 1)
        risk_level = "高" if structural["hard_blocked"] else "低" if score >= 72 else "中" if score >= 48 else "高"
        config = PROFILE_CONFIG[profile]
        stop_loss_pct = _clamp(config["stop_loss"] + max(volatility - 2, 0) / 100, 0.04, 0.12)
        position_cap = max(5, config["position_cap"] - (10 if risk_level == "高" else 5 if risk_level == "中" else 0))
        return {
            "agent": "风险控制 Agent",
            "skill": "波动率、回撤、财务排雷、解禁、股东户数与公告否决",
            "score": score,
            "signal": "通过" if score >= 60 else "需调整" if score >= 40 else "高风险",
            "summary": evidence[0] if evidence else (risks[0] if risks else "风险水平中性"),
            "evidence": evidence,
            "risks": risks,
            "structural_risk": structural,
            "plan": {
                "risk_level": risk_level,
                "daily_volatility_pct": round(volatility, 2),
                "max_drawdown_20d_pct": drawdown,
                "stop_loss_price": round(price * (1 - stop_loss_pct), 2) if price else None,
                "reference_target_price": round(price * (1 + stop_loss_pct * 2), 2) if price else None,
                "max_research_position_pct": position_cap,
            },
        }

    def _news_policy_agent(self, stock: dict, context: dict, announcements: list[dict]) -> dict:
        """Translate cited macro/policy and announcement inputs into a capped signal."""
        sector = _normalise_sector(stock.get("sector"))
        sector_terms = macro_policy_news_collector.sector_terms(sector)
        context_available = bool(context.get("available"))
        international_items = list(context.get("international_items") or [])
        policy_items = list(context.get("policy_items") or [])
        score = 50.0
        evidence: list[str] = []
        risks: list[str] = []
        sources: list[dict] = []

        macro_adjustment = as_float(context.get("macro_adjustment")) if context_available else 0.0
        if international_items:
            score += max(-4.0, min(4.0, macro_adjustment))
            sources.extend({**item, "impact": "neutral"} for item in international_items[:2])
            evidence.append("国际宏观数据仅作为低权重增长基线，不单独构成买卖依据")

        policy_matches: list[tuple[float, dict, list[str]]] = []
        for item in policy_items:
            if not isinstance(item, dict):
                continue
            delta, impact, matched_terms = macro_policy_news_collector.policy_impact(
                str(item.get("title") or ""), sector_terms,
            )
            if not matched_terms:
                continue
            policy_matches.append((delta, {**item, "impact": impact}, matched_terms))
        policy_matches.sort(key=lambda item: abs(item[0]), reverse=True)
        policy_delta = max(-15.0, min(15.0, sum(item[0] for item in policy_matches[:3])))
        score += policy_delta
        for delta, item, matched_terms in policy_matches[:3]:
            sources.append(item)
            detail = f"{item.get('source')}：{item.get('title')}（匹配{'、'.join(matched_terms)}）"
            if delta > 0:
                evidence.append(detail)
            elif delta < 0:
                risks.append(detail)

        announcement_delta = 0.0
        for item in announcements[:4]:
            if not isinstance(item, dict):
                continue
            delta, impact = macro_policy_news_collector.announcement_impact(str(item.get("title") or ""))
            sourced_item = {**item, "impact": impact}
            sources.append(sourced_item)
            if not macro_policy_news_collector.is_recent(item.get("published_at")):
                continue
            announcement_delta += delta
            if delta > 0:
                evidence.append(f"公司公告：{item.get('title')}")
            elif delta < 0:
                risks.append(f"公司公告：{item.get('title')}")
        announcement_delta = max(-18.0, min(18.0, announcement_delta))
        score += announcement_delta

        available = context_available or bool(announcements)
        if not available:
            summary = "宏观、政策与公司公告源本轮不可用，新闻政策因素未计分"
        elif risks:
            summary = risks[0]
        elif evidence:
            summary = evidence[0]
        elif sector:
            summary = f"已核验宏观政策源，暂未找到直接匹配“{sector}”的有效政策标题"
        else:
            summary = "已核验宏观政策源，当前个股行业字段不足以做定向匹配"

        score = round(_clamp(score), 1)
        signal = "看多" if score >= 64 else "看空" if score <= 38 else "中性"
        return {
            "agent": "宏观政策与公告 Agent",
            "skill": "国际经济、国内发展政策、公司公告与行业匹配",
            "score": score,
            "signal": signal,
            "summary": summary,
            "evidence": evidence[:4],
            "risks": risks[:4],
            "sources": sources[:8],
            "available": available,
            "metrics": {
                "macro_adjustment": round(macro_adjustment, 1),
                "policy_match_count": len(policy_matches),
                "announcement_count": len(announcements),
                "announcement_adjustment": round(announcement_delta, 1),
            },
        }

    def _supervisor_agent(
        self,
        stock: dict,
        technical: dict,
        fundamental: dict,
        capital: dict,
        risk: dict,
        news: dict,
        profile: str,
        regime: dict,
        quality: dict,
        strategy_audit: dict,
        horizon: str,
    ) -> dict:
        weights = combined_agent_weights(PROFILE_CONFIG[profile]["weights"], horizon)
        weighted_agents = [
            (technical["score"], weights["technical"]),
            (fundamental["score"], weights["fundamental"]),
            (capital["score"], weights["capital"]),
            (risk["score"], weights["safety"]),
        ]
        # A source outage must not become a synthetic neutral-news score.
        if news.get("available"):
            weighted_agents.append((news["score"], weights["news"]))
        weight_total = sum(weight for _, weight in weighted_agents)
        composite = sum(score * weight for score, weight in weighted_agents) / weight_total
        bias = regime.get("bias", "neutral")
        composite += 3 if bias == "bullish" else -4 if bias == "bearish" else 0
        # Evidence quality and an independent audit constrain the ranking. They
        # cannot be offset by a stronger momentum or fund-flow score.
        credibility = as_float(strategy_audit.get("credibility_score"))
        composite -= max(0.0, 62.0 - credibility) * 0.12
        if quality.get("grade") == "不足":
            composite = min(composite, 57.0)
        elif strategy_audit.get("overall_risk") == "高":
            composite = min(composite, 64.0)
        hard_blocked = bool((risk.get("structural_risk") or {}).get("hard_blocked"))
        if hard_blocked:
            composite = min(composite, 35.0)
        composite = round(_clamp(composite), 1)

        bull_points = [
            *technical["evidence"][:2],
            *fundamental["evidence"][:1],
            *capital["evidence"][:2],
            *(news["evidence"][:1] if news.get("available") else []),
        ][:4]
        bear_points = [
            *technical["risks"][:1],
            *fundamental["risks"][:1],
            *capital["risks"][:1],
            *risk["risks"][:2],
            *(news["risks"][:1] if news.get("available") else []),
        ][:4]
        bullish_scores = [technical["score"], fundamental["score"], capital["score"]]
        if news.get("available"):
            bullish_scores.append(news["score"])
        bull_score = round(_mean(bullish_scores), 1)
        downside_safety = risk["score"] * 0.55 + technical["score"] * 0.2 + capital["score"] * 0.25
        if news.get("available"):
            downside_safety = downside_safety * 0.92 + news["score"] * 0.08
        bear_score = round(100 - downside_safety, 1)
        history_points = technical["metrics"]["history_points"]
        confidence = round(_clamp(48 + abs(bull_score - bear_score) * 0.32 + min(history_points, 80) * 0.12, 35, 92), 1)
        if quality.get("grade") == "一般":
            confidence = min(confidence, 68.0)
        elif quality.get("grade") == "不足":
            confidence = min(confidence, 45.0)
        if hard_blocked:
            confidence = min(confidence, 40.0)
        confidence = min(confidence, max(35.0, credibility))

        if hard_blocked:
            verdict = "风险否决"
        elif quality.get("grade") == "不足" or strategy_audit.get("blockers"):
            verdict = "证据不足"
        elif strategy_audit.get("overall_risk") == "高":
            verdict = "待审计修复"
        elif composite >= 72 and technical["score"] >= 50 and capital["score"] >= 50 and risk["score"] >= 50:
            verdict = "优先研究"
        elif composite >= 58:
            verdict = "持续跟踪"
        else:
            verdict = "暂不优先"
        decisive_candidates = [
            ("技术趋势", technical["score"]),
            ("基本面质量", fundamental["score"]),
            ("资金强度", capital["score"]),
            ("风险约束", risk["score"]),
        ]
        if news.get("available"):
            decisive_candidates.append(("宏观政策与公告", news["score"]))
        decisive_factor = max(decisive_candidates, key=lambda item: item[1])[0]
        return {
            "agent": "研究主管 Agent",
            "skill": "多空交叉验证、Alpha/Beta 归因与审计约束",
            "score": composite,
            "verdict": verdict,
            "confidence": confidence,
            "summary": (
                f"{HORIZON_CONFIG[horizon]['label']}窗口、{regime.get('regime', '震荡市')}环境下，决定性因素为{decisive_factor}；"
                f"数据质量{quality.get('grade', '不足')}，审计可信度{credibility:.0f}分。"
            ),
            "horizon": horizon,
            "weights": {key: round(value, 4) for key, value in weights.items()},
            "debate": {
                "bull_score": bull_score,
                "bear_score": bear_score,
                "bull_points": bull_points or ["暂无足够的看多证据"],
                "bear_points": bear_points or ["暂无显著的量化风险信号"],
                "decisive_factor": decisive_factor,
                "alpha_beta_assessment": (
                    "当前为横截面研究快照，尚未完成可交易收益的历史 Alpha/Beta 回归；"
                    "市场环境和行业暴露不计作公司自身 Alpha。"
                ),
            },
        }

    def _analyze_candidate(
        self,
        stock: dict,
        history: list[dict],
        profile: str,
        regime: dict,
        news_context: dict,
        announcements: list[dict],
        *,
        source: str,
        is_realtime: bool,
        data_date: str | None,
        updated_at: str,
        horizon: str,
    ) -> dict:
        news = self._news_policy_agent(stock, news_context, announcements)
        quality = research_protocol.data_quality(
            stock,
            history,
            source=source,
            news_available=bool(news.get("available")),
        )
        hypothesis = research_protocol.hypothesis_card(
            stock,
            data_date=data_date,
            is_realtime=is_realtime,
            source=source,
            data_quality=quality["grade"],
            holding_days=HORIZON_CONFIG[horizon]["trading_days"],
        )
        timeline = research_protocol.time_audit(
            stock,
            history,
            data_date=data_date,
            updated_at=updated_at,
            source=source,
            is_realtime=is_realtime,
        )
        technical = self._technical_agent(stock, history)
        fundamental = self._fundamental_agent(stock)
        capital = self._capital_flow_agent(stock)
        risk = self._risk_agent(stock, history, profile, announcements)
        execution = research_protocol.execution_plan(stock, risk["plan"], quality)
        strategy_audit = research_protocol.strategy_audit(
            stock,
            history,
            timeline=timeline,
            quality=quality,
            execution=execution,
            source=source,
            is_realtime=is_realtime,
            holding_days=HORIZON_CONFIG[horizon]["trading_days"],
        )
        risk["plan"].update({
            "data_quality": quality["grade"],
            "data_blindspot_discount_pct": round((1 - quality["position_multiplier"]) * 100, 0),
            "final_research_position_pct": execution["position_cap_pct"],
            "friction_cost": execution["friction_cost"],
        })
        audit_agent = {
            "agent": "策略审计官",
            "skill": "独立证伪：时间泄漏、成本、容量与样本偏差",
            "score": strategy_audit["credibility_score"],
            "signal": strategy_audit["verdict"],
            "summary": f"审计风险{strategy_audit['overall_risk']}，{strategy_audit['verdict']}。",
            "evidence": [
                item["evidence"]
                for item in strategy_audit["findings"]
                if item["risk_level"] == "低"
            ][:3],
            "risks": strategy_audit["blockers"] or strategy_audit["warnings"][:3],
            "metrics": {"credibility_score": strategy_audit["credibility_score"]},
        }
        supervisor = self._supervisor_agent(
            stock, technical, fundamental, capital, risk, news, profile, regime, quality, strategy_audit,
            horizon,
        )
        agents = {
            "technical": technical,
            "fundamental": fundamental,
            "capital": capital,
            "risk": risk,
            "news": news,
            "audit": audit_agent,
            "supervisor": supervisor,
        }
        horizon_outlook = horizon_potential_analyzer.assess(
            stock,
            history,
            agents,
            regime,
            quality,
            risk.get("structural_risk") or {},
            horizon,
            supervisor.get("weights") or {},
        )
        return {
            "code": stock["code"],
            "name": stock["name"],
            "sector": _normalise_sector(stock.get("sector")),
            "price": round(as_float(stock.get("price")), 2),
            "change_pct": round(as_float(stock.get("change_pct")), 2),
            "turnover": round(as_float(stock.get("turnover")), 2),
            "amount": as_int(stock.get("amount")),
            "market_cap": as_int(stock.get("market_cap")),
            "selection_sources": stock.get("selection_sources") or [],
            "score": horizon_outlook["potential_score"],
            "base_score": supervisor["score"],
            "verdict": horizon_outlook["judgement"],
            "confidence": horizon_outlook["confidence"],
            "horizon_outlook": horizon_outlook,
            "feature_sources": stock.get("_feature_meta") or {},
            "research": {
                "hypothesis_card": hypothesis,
                "data_contract": A_STOCK_DATA_SKILL,
                "data_quality": quality,
                "time_audit": timeline,
                "execution_plan": execution,
                "strategy_audit": strategy_audit,
                "experiment_log": {
                    "protocol": "v3.0 固定研究协议",
                    "factor_changes": "本轮未针对结果调整因子、成本或交易规则",
                    "backtest_base_locked": True,
                    "failure_recording": "审计风险和证据不足项会随本次运行记录保存，不允许仅保留成功结论。",
                },
            },
            "agents": agents,
        }

    @staticmethod
    def _pipeline_status(
        candidate_count: int,
        regime: dict,
        is_realtime: bool,
        news_context: dict,
        announcement_coverage: int,
        source_name: str,
        horizon: str,
        *,
        research_ready: bool = False,
    ) -> list[dict]:
        using_ftshare = source_name == "ftshare_mcp"
        freshness = (
            "FTShare 行情快照（未提供时间戳）"
            if using_ftshare
            else "盘中实时行情" if is_realtime else "最近交易快照（非交易时段）"
        )
        policy_count = len(news_context.get("policy_items") or [])
        international_count = len(news_context.get("international_items") or [])
        news_available = bool(news_context.get("available")) or announcement_coverage > 0
        news_summary = (
            f"已核验国际宏观 {international_count} 项、国内政策 {policy_count} 条，"
            f"公司公告覆盖 {announcement_coverage} 个候选。"
            if news_available
            else "宏观、政策与公司公告源当前不可用，新闻政策因素未计分。"
        )
        return [
            {
                "id": "data",
                "name": "数据采集 Agent",
                "skill": "FTShare 行情快照候选池" if using_ftshare else "实时资金、量比、动量候选池",
                "status": "completed" if candidate_count else "unavailable",
                "summary": (
                    f"{freshness}，有效候选池 {candidate_count} 只；未提供行业、主力资金和量比字段，相关因子不计分。"
                    if using_ftshare
                    else f"{freshness}，有效候选池 {candidate_count} 只。"
                ),
            },
            {
                "id": "hypothesis",
                "name": "研究假设 Agent",
                "skill": "成功标准、失败标准与基准预先写死",
                "status": "completed" if research_ready else "waiting",
                "summary": "先定义可证伪信号和对照基线，不根据结果倒推理由。",
            },
            {
                "id": "time_audit",
                "name": "数据时间审计 Agent",
                "skill": "事件、可用、计算、交易四时间检查",
                "status": "completed" if research_ready else "waiting",
                "summary": "财务、股东户数和解禁均核验披露日期；缺失项不会按零处理。",
            },
            {
                "id": "market",
                "name": "市场环境 Agent",
                "skill": "板块资金与市场状态识别",
                "status": "completed",
                "summary": f"当前识别为 {regime.get('regime', '震荡市')}，置信度 {regime.get('confidence', 0) * 100:.0f}%。",
            },
            {
                "id": "research",
                "name": "研究 Agent 团队",
                "skill": "技术、基本面、资金面与多空裁决",
                "status": "completed" if candidate_count else "waiting",
                "summary": "每只入选股保留可展开的分项评分与证据。",
            },
            {
                "id": "risk",
                "name": "风险控制 Agent",
                "skill": "波动率、回撤、成本与数据盲区仓位上限",
                "status": "completed" if candidate_count else "waiting",
                "summary": "风险结论会覆盖单一看多信号。",
            },
            {
                "id": "execution",
                "name": "交易执行 Agent",
                "skill": "T+1、佣金、印花税、滑点与冲击成本",
                "status": "completed" if research_ready else "waiting",
                "summary": "先扣除真实摩擦成本，再判断参考收益是否还有研究价值。",
            },
            {
                "id": "audit",
                "name": "策略审计 Agent",
                "skill": "独立证伪与失败实验记录",
                "status": "completed" if research_ready else "waiting",
                "summary": "不接受漂亮曲线作为证据，逐项检查未来函数、偏差、成本和容量。",
            },
            {
                "id": "horizon",
                "name": "周期潜力 Agent",
                "skill": "5/10/20日动态权重与近一年相似形态验证",
                "status": "completed" if research_ready else "waiting",
                "summary": (
                    f"本轮观察窗口为{HORIZON_CONFIG[horizon]['label']}"
                    f"（{HORIZON_CONFIG[horizon]['trading_days']}个交易日），历史样本不足会降低置信度。"
                ),
            },
            {
                "id": "news",
                "name": "宏观政策与公告 Agent",
                "skill": "国际经济、国内发展政策、公司公告与行业匹配",
                "status": "completed" if news_available else "unavailable",
                "summary": news_summary,
            },
        ]

    async def run(
        self,
        mode: str = "quick",
        risk_profile: str = "balanced",
        top_n: int = 5,
        sector: str | None = None,
        sector_code: str | None = None,
        horizon: str = "week",
        factor_filters: dict | None = None,
    ) -> dict:
        if mode not in VALID_SELECTION_MODES:
            raise ValueError("mode 必须是 quick 或 full")
        if risk_profile not in VALID_RISK_PROFILES:
            raise ValueError("risk_profile 必须是 conservative、balanced 或 aggressive")
        if horizon not in VALID_HORIZONS:
            raise ValueError("horizon 必须是 week、half_month 或 month")
        top_n = min(max(int(top_n), 3), 10)
        sector_filter = _normalise_sector(sector)
        sector_board_code = normalize_board_code(sector_code) if sector_code else ""
        if sector_board_code and not sector_filter:
            raise ValueError("sector_code 必须与行业名称一起提交")
        factor_config = normalize_selection_factors(factor_filters)
        market_session = self._is_market_session()

        candidate_fetcher = (
            (lambda: collector.fetch_all_board_stocks(sector_board_code, sector_name=sector_filter))
            if sector_board_code
            else (lambda: collector.fetch_intelligent_selection_candidates())
        )
        source_awaitable = self._candidate_snapshot(
            f"stock_selection_candidates_v1:{sector_board_code or 'market'}",
            candidate_fetcher,
            prefer_cache=not market_session,
        )

        source_result, regime_result, macro_result = await asyncio.gather(
            source_awaitable,
            MarketRegime.detect(),
            macro_policy_news_collector.get_context(),
            return_exceptions=True,
        )
        candidates = [] if isinstance(source_result, Exception) else list(source_result.get("stocks") or [])
        source_name = (
            str(source_result.get("source") or "eastmoney")
            if not isinstance(source_result, Exception)
            else "eastmoney"
        )
        if isinstance(regime_result, Exception):
            regime = {"regime": "震荡市", "confidence": 0.5, "bias": "neutral"}
        else:
            regime = regime_result
        news_context = macro_policy_news_collector.empty_context() if isinstance(macro_result, Exception) else macro_result
        if not isinstance(news_context, dict):
            news_context = macro_policy_news_collector.empty_context()

        candidates = [
            stock for stock in candidates
            if as_float(stock.get("price")) > 0
            and "ST" not in str(stock.get("name") or "").upper()
            and "退" not in str(stock.get("name") or "")
        ]
        market_candidate_count = (
            as_int(source_result.get("total"), len(candidates))
            if sector_board_code and not isinstance(source_result, Exception)
            else len(candidates)
        )
        if sector_filter and not sector_board_code:
            candidates = [
                stock for stock in candidates
                if _normalise_sector(stock.get("sector")) == sector_filter
            ]
        sector_candidate_count = len(candidates)
        candidates, factor_metadata = self._apply_factor_filters(candidates, factor_config)
        filtered_candidate_count = len(candidates)
        candidates.sort(key=self._preliminary_priority, reverse=True)
        analysis_limit = self._FULL_ANALYSIS_LIMIT if mode == "full" else self._QUICK_ANALYSIS_LIMIT
        candidates = candidates[:analysis_limit]
        source_data_date = (
            str(source_result.get("data_date") or "") or None
            if not isinstance(source_result, Exception)
            else None
        )
        try:
            configured_announcement_limit = int(settings.macro_news_announcement_limit)
        except (TypeError, ValueError):
            configured_announcement_limit = 48
        announcement_limit = min(len(candidates), max(0, min(configured_announcement_limit, 64)))
        feature_fields = set(FINANCIAL_FIELDS | SHAREHOLDER_FIELDS | LOCKUP_FIELDS) | {
            "sector_rank", "sector_strength_score",
        }
        history_refresh_result, announcements_result, features_result = await asyncio.gather(
            self._refresh_candidate_histories(
                candidates,
                source=source_name,
                data_date=source_data_date,
            ),
            macro_policy_news_collector.get_stock_announcements(
                [stock["code"] for stock in candidates],
                max_stocks=announcement_limit,
            ),
            stock_feature_service.enrich(candidates, feature_fields, full_market=False),
            return_exceptions=True,
        )
        try:
            histories = await self._load_histories([stock["code"] for stock in candidates])
        except Exception as exc:
            print(f"Stock selection history refresh load failed: {type(exc).__name__}")
            histories = {}
        announcements_by_stock = {} if isinstance(announcements_result, Exception) else announcements_result
        if not isinstance(histories, dict):
            histories = {}
        if not isinstance(announcements_by_stock, dict):
            announcements_by_stock = {}
        feature_coverage = {"total": len(candidates)}
        feature_warnings: list[str] = []
        if isinstance(history_refresh_result, Exception):
            feature_warnings.append(f"近期日线缓存同步失败（{type(history_refresh_result).__name__}）")
        elif isinstance(history_refresh_result, dict):
            failed_histories = list(history_refresh_result.get("failed") or [])
            if failed_histories:
                expected_date = history_refresh_result.get("expected_date") or source_data_date or "最新交易日"
                feature_warnings.append(
                    f"{len(failed_histories)} 只候选近期日线未同步至 {expected_date}，相关技术结论已保守处理。"
                )
        if isinstance(features_result, Exception):
            feature_warnings.append(f"财务与事件特征暂不可用（{type(features_result).__name__}）")
        elif isinstance(features_result, dict):
            candidates = list(features_result.get("stocks") or candidates)
            feature_coverage = features_result.get("coverage") or feature_coverage
            feature_warnings.extend(features_result.get("warnings") or [])
        announcement_coverage = sum(bool(items) for items in announcements_by_stock.values())
        now = shanghai_now()
        # Source timestamps, rather than the server clock alone, decide
        # whether a quote snapshot can be called real-time.
        source_realtime = (
            source_result.get("is_realtime")
            if not isinstance(source_result, Exception)
            else None
        )
        is_realtime = (
            bool(source_realtime)
            if source_realtime is not None
            else market_session and source_name == "eastmoney"
        )
        if source_name != "eastmoney":
            is_realtime = False
        cached_dates = [
            row["date"]
            for history in histories.values()
            for row in history
            if row.get("date")
        ]
        data_date = source_data_date or (now.date().isoformat() if is_realtime else max(cached_dates, default=None))
        analyzed = [
            self._analyze_candidate(
                stock,
                histories.get(stock["code"], []),
                risk_profile,
                regime,
                news_context,
                announcements_by_stock.get(stock["code"], []),
                source=source_name,
                is_realtime=is_realtime,
                data_date=data_date,
                updated_at=now.isoformat(),
                horizon=horizon,
            )
            for stock in candidates
        ]
        analyzed.sort(key=lambda item: (item["score"], item["confidence"]), reverse=True)
        sector_metadata = {
            "value": sector_filter,
            "label": sector_filter or "全部行业",
            "code": sector_board_code or None,
            "matched_candidates": sector_candidate_count,
            "market_candidates": market_candidate_count,
            "directory_complete": (
                bool(source_result.get("complete", True))
                if sector_board_code and not isinstance(source_result, Exception)
                else True
            ),
        }
        factor_metadata = {
            **factor_metadata,
            "schema": SELECTION_FACTOR_SCHEMA,
            "presets": SELECTION_FACTOR_PRESETS,
        }
        macro_policy = {
            **news_context,
            "announcement_coverage": announcement_coverage,
            "announcement_requested": announcement_limit,
        }
        pipeline = self._pipeline_status(
            len(analyzed), regime, is_realtime, news_context, announcement_coverage, source_name,
            horizon,
            research_ready=bool(analyzed),
        )

        if not analyzed:
            if factor_config["enabled"] and sector_candidate_count and not filtered_candidate_count:
                empty_message = "当前候选全部未通过已启用的因子条件，请放宽阈值或关闭部分因子。"
            elif sector_filter:
                empty_message = f"行业板块“{sector_filter}”当前未返回可交易候选股，请切换行业或稍后重试。"
            else:
                empty_message = "行情源及最近有效缓存当前均未返回可交易候选股，系统不会以零价或退市记录生成结果。"
            return {
                "available": False,
                "source": source_name,
                "is_realtime": is_realtime,
                "data_date": data_date,
                "updated_at": now.isoformat(),
                "mode": mode,
                "risk_profile": risk_profile,
                "risk_profile_label": PROFILE_CONFIG[risk_profile]["label"],
                "research_horizon": {"id": horizon, **HORIZON_CONFIG[horizon]},
                "market_regime": regime,
                "candidate_summary": {
                    "live_candidates": filtered_candidate_count,
                    "market_candidates": market_candidate_count,
                    "analyzed": 0,
                    "selected": 0,
                    "risk_excluded": 0,
                },
                "sector_filter": sector_metadata,
                "factor_filter": factor_metadata,
                "cache_used": bool(source_result.get("cache_used")) if not isinstance(source_result, Exception) else False,
                "cache_reason": source_result.get("cache_reason") if not isinstance(source_result, Exception) else None,
                "data_contract": A_STOCK_DATA_SKILL,
                "macro_policy": macro_policy,
                "feature_coverage": feature_coverage,
                "feature_warnings": feature_warnings,
                "agent_pipeline": pipeline,
                "recommendations": [],
                "message": empty_message,
                "disclaimer": "结果仅供研究与学习参考，不构成任何投资建议。",
            }

        eligible = [
            item for item in analyzed
            if not bool(
                (((item.get("agents") or {}).get("risk") or {}).get("structural_risk") or {}).get("hard_blocked")
            )
        ]
        risk_excluded_count = len(analyzed) - len(eligible)
        recommendations = eligible[:top_n]
        for index, recommendation in enumerate(recommendations, start=1):
            recommendation["rank"] = index
        selection_available = bool(recommendations)
        if not selection_available:
            selection_message = "本轮候选全部触发不可抵消的风险否决条件，系统未生成潜力股推荐。"
        else:
            selection_message = (
                f"按{HORIZON_CONFIG[horizon]['label']}窗口调整因子权重，并以近一年相似形态做低权重校验；"
                "随后经过数据时间、真实成本和独立证伪约束。"
                if pipeline[-1]["status"] == "completed"
                else f"按{HORIZON_CONFIG[horizon]['label']}窗口完成量化交叉验证；宏观政策与公告源当前不可用，本轮未计入评分。"
            )
            if risk_excluded_count:
                selection_message += f"另有 {risk_excluded_count} 只候选因硬性风险被排除。"
        return {
            "available": selection_available,
            "source": source_name,
            "is_realtime": is_realtime,
            "data_date": data_date,
            "updated_at": now.isoformat(),
            "mode": mode,
            "risk_profile": risk_profile,
            "risk_profile_label": PROFILE_CONFIG[risk_profile]["label"],
            "research_horizon": {"id": horizon, **HORIZON_CONFIG[horizon]},
            "market_regime": regime,
            "candidate_summary": {
                "live_candidates": filtered_candidate_count,
                "market_candidates": market_candidate_count,
                "analyzed": len(analyzed),
                "selected": len(recommendations),
                "risk_excluded": risk_excluded_count,
            },
            "sector_filter": sector_metadata,
            "factor_filter": factor_metadata,
            "cache_used": bool(source_result.get("cache_used")) if not isinstance(source_result, Exception) else False,
            "cache_reason": source_result.get("cache_reason") if not isinstance(source_result, Exception) else None,
            "data_contract": A_STOCK_DATA_SKILL,
            "macro_policy": macro_policy,
            "feature_coverage": feature_coverage,
            "feature_warnings": feature_warnings,
            "agent_pipeline": pipeline,
            "recommendations": recommendations,
            "message": selection_message,
            "disclaimer": "结果仅供研究与学习参考，不构成任何投资建议。市场行情和指标会随盘中数据变化。",
        }


stock_selection_agents = StockSelectionAgentService()
