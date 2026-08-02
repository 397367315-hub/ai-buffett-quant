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

from sqlalchemy import select

from config import settings
from database import async_session
from models import StockDailyBar
from services.data_collector import as_float, as_int, is_a_share_market_session, normalize_board_code, shanghai_now
from services.macro_policy_news import macro_policy_news_collector
from services.quant_scorer import MarketRegime
from services.research_protocol import research_protocol
from services.data_collector import collector
from services.a_stock_data import A_STOCK_DATA_SKILL, calculate_indicators


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


class StockSelectionAgentService:
    """Runs a visible research pipeline over verified real-time market data."""

    _HISTORY_LOOKBACK_DAYS = 120
    _QUICK_ANALYSIS_LIMIT = 45
    _FULL_ANALYSIS_LIMIT = 80

    @staticmethod
    def _is_market_session() -> bool:
        return is_a_share_market_session()

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

        if volume_ratio is not None and 1.2 <= volume_ratio <= 4:
            score += 8
            evidence.append(f"量比 {volume_ratio:.2f}，成交活跃度匹配")
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
        score = 50.0
        evidence: list[str] = []
        risks: list[str] = []

        if pe is None:
            evidence.append("PE未披露，估值判断采用中性权重")
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
            evidence.append("ROE未披露，盈利能力结论有限")
        elif roe >= 20:
            score += 18
            evidence.append(f"ROE {roe:.1f}%，盈利能力较强")
        elif roe >= 12:
            score += 10
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

        score = round(_clamp(score), 1)
        signal = "看多" if score >= 64 else "看空" if score <= 38 else "中性"
        return {
            "agent": "基本面 Agent",
            "skill": "PE、PB、ROE 质量与估值筛查",
            "score": score,
            "signal": signal,
            "summary": evidence[0] if evidence else "基本面指标未形成明显优势",
            "evidence": evidence,
            "risks": risks,
            "metrics": {"pe": pe, "pb": pb, "roe": roe},
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
        if volume_ratio is not None and 1.2 <= volume_ratio <= 4:
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

    def _risk_agent(self, stock: dict, history: list[dict], profile: str) -> dict:
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

        score = round(_clamp(score), 1)
        risk_level = "低" if score >= 72 else "中" if score >= 48 else "高"
        config = PROFILE_CONFIG[profile]
        stop_loss_pct = _clamp(config["stop_loss"] + max(volatility - 2, 0) / 100, 0.04, 0.12)
        position_cap = max(5, config["position_cap"] - (10 if risk_level == "高" else 5 if risk_level == "中" else 0))
        return {
            "agent": "风险控制 Agent",
            "skill": "波动率、回撤、止损与研究仓位上限",
            "score": score,
            "signal": "通过" if score >= 60 else "需调整" if score >= 40 else "高风险",
            "summary": evidence[0] if evidence else (risks[0] if risks else "风险水平中性"),
            "evidence": evidence,
            "risks": risks,
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
    ) -> dict:
        weights = PROFILE_CONFIG[profile]["weights"]
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
        confidence = min(confidence, max(35.0, credibility))

        if quality.get("grade") == "不足" or strategy_audit.get("blockers"):
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
                f"{regime.get('regime', '震荡市')}环境下，决定性因素为{decisive_factor}；"
                f"数据质量{quality.get('grade', '不足')}，审计可信度{credibility:.0f}分。"
            ),
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
        risk = self._risk_agent(stock, history, profile)
        execution = research_protocol.execution_plan(stock, risk["plan"], quality)
        strategy_audit = research_protocol.strategy_audit(
            stock,
            history,
            timeline=timeline,
            quality=quality,
            execution=execution,
            source=source,
            is_realtime=is_realtime,
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
            "score": supervisor["score"],
            "verdict": supervisor["verdict"],
            "confidence": supervisor["confidence"],
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
            "agents": {
                "technical": technical,
                "fundamental": fundamental,
                "capital": capital,
                "risk": risk,
                "news": news,
                "audit": audit_agent,
                "supervisor": supervisor,
            },
        }

    @staticmethod
    def _pipeline_status(
        candidate_count: int,
        regime: dict,
        is_realtime: bool,
        news_context: dict,
        announcement_coverage: int,
        source_name: str,
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
                "summary": "基本面披露日期缺失或同日成交假设会被标记为风险。",
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
    ) -> dict:
        if mode not in VALID_SELECTION_MODES:
            raise ValueError("mode 必须是 quick 或 full")
        if risk_profile not in VALID_RISK_PROFILES:
            raise ValueError("risk_profile 必须是 conservative、balanced 或 aggressive")
        top_n = min(max(int(top_n), 3), 10)
        sector_filter = _normalise_sector(sector)
        sector_board_code = normalize_board_code(sector_code) if sector_code else ""
        if sector_board_code and not sector_filter:
            raise ValueError("sector_code 必须与行业名称一起提交")

        source_awaitable = (
            collector.fetch_all_board_stocks(sector_board_code, sector_name=sector_filter)
            if sector_board_code
            else collector.fetch_intelligent_selection_candidates()
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
        filtered_candidate_count = len(candidates)
        candidates.sort(key=self._preliminary_priority, reverse=True)
        analysis_limit = self._FULL_ANALYSIS_LIMIT if mode == "full" else self._QUICK_ANALYSIS_LIMIT
        candidates = candidates[:analysis_limit]
        try:
            configured_announcement_limit = int(settings.macro_news_announcement_limit)
        except (TypeError, ValueError):
            configured_announcement_limit = 48
        announcement_limit = min(len(candidates), max(0, min(configured_announcement_limit, 64)))
        histories_result, announcements_result = await asyncio.gather(
            self._load_histories([stock["code"] for stock in candidates]),
            macro_policy_news_collector.get_stock_announcements(
                [stock["code"] for stock in candidates],
                max_stocks=announcement_limit,
            ),
            return_exceptions=True,
        )
        histories = {} if isinstance(histories_result, Exception) else histories_result
        announcements_by_stock = {} if isinstance(announcements_result, Exception) else announcements_result
        if not isinstance(histories, dict):
            histories = {}
        if not isinstance(announcements_by_stock, dict):
            announcements_by_stock = {}
        announcement_coverage = sum(bool(items) for items in announcements_by_stock.values())
        now = shanghai_now()
        # The FTShare fallback exposes a quote snapshot without a source
        # timestamp, so it must not be presented as a verified live tick.
        is_realtime = self._is_market_session() and source_name == "eastmoney"
        cached_dates = [
            row["date"]
            for history in histories.values()
            for row in history
            if row.get("date")
        ]
        data_date = now.date().isoformat() if is_realtime else max(cached_dates, default=None)
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
            )
            for stock in candidates
        ]
        analyzed.sort(key=lambda item: (item["score"], item["confidence"]), reverse=True)
        sector_metadata = {
            "value": sector_filter,
            "label": sector_filter or "全部行业",
            "code": sector_board_code or None,
            "matched_candidates": filtered_candidate_count,
            "market_candidates": market_candidate_count,
            "directory_complete": (
                bool(source_result.get("complete", True))
                if sector_board_code and not isinstance(source_result, Exception)
                else True
            ),
        }
        macro_policy = {
            **news_context,
            "announcement_coverage": announcement_coverage,
            "announcement_requested": announcement_limit,
        }
        pipeline = self._pipeline_status(
            len(analyzed), regime, is_realtime, news_context, announcement_coverage, source_name,
            research_ready=bool(analyzed),
        )

        if not analyzed:
            empty_message = (
                f"行业板块“{sector_filter}”当前未返回可交易候选股，请切换行业或稍后重试。"
                if sector_filter
                else "实时行情源当前未返回可交易候选股，系统不会以零价或退市记录生成选股结果。"
            )
            return {
                "available": False,
                "source": source_name,
                "is_realtime": is_realtime,
                "data_date": data_date,
                "updated_at": now.isoformat(),
                "mode": mode,
                "risk_profile": risk_profile,
                "risk_profile_label": PROFILE_CONFIG[risk_profile]["label"],
                "market_regime": regime,
                "candidate_summary": {
                    "live_candidates": filtered_candidate_count,
                    "market_candidates": market_candidate_count,
                    "analyzed": 0,
                    "selected": 0,
                },
                "sector_filter": sector_metadata,
                "data_contract": A_STOCK_DATA_SKILL,
                "macro_policy": macro_policy,
                "agent_pipeline": pipeline,
                "recommendations": [],
                "message": empty_message,
                "disclaimer": "结果仅供研究与学习参考，不构成任何投资建议。",
            }

        recommendations = analyzed[:top_n]
        for index, recommendation in enumerate(recommendations, start=1):
            recommendation["rank"] = index
        return {
            "available": True,
            "source": source_name,
            "is_realtime": is_realtime,
            "data_date": data_date,
            "updated_at": now.isoformat(),
            "mode": mode,
            "risk_profile": risk_profile,
            "risk_profile_label": PROFILE_CONFIG[risk_profile]["label"],
            "market_regime": regime,
            "candidate_summary": {
                "live_candidates": filtered_candidate_count,
                "market_candidates": market_candidate_count,
                "analyzed": len(analyzed),
                "selected": len(recommendations),
            },
            "sector_filter": sector_metadata,
            "data_contract": A_STOCK_DATA_SKILL,
            "macro_policy": macro_policy,
            "agent_pipeline": pipeline,
            "recommendations": recommendations,
            "message": (
                "排序先由量化 Agent 交叉验证，再经过数据时间审计、真实成本和独立证伪约束；宏观政策与公告仅在可核验时以低权重纳入评分。"
                if pipeline[-1]["status"] == "completed"
                else "排序先由量化 Agent 交叉验证，再经过数据时间审计、真实成本和独立证伪约束；宏观政策与公告源当前不可用，本轮未计入评分。"
            ),
            "disclaimer": "结果仅供研究与学习参考，不构成任何投资建议。市场行情和指标会随盘中数据变化。",
        }


stock_selection_agents = StockSelectionAgentService()
