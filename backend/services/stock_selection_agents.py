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

from database import async_session
from models import StockDailyBar
from services.data_collector import as_float, as_int, shanghai_now
from services.quant_scorer import MarketRegime
from services.data_collector import collector


VALID_SELECTION_MODES = {"quick", "full"}
VALID_RISK_PROFILES = {"conservative", "balanced", "aggressive"}

PROFILE_CONFIG = {
    "conservative": {
        "label": "稳健",
        "weights": {"technical": 0.24, "fundamental": 0.30, "capital": 0.18, "safety": 0.28},
        "position_cap": 12,
        "stop_loss": 0.05,
    },
    "balanced": {
        "label": "均衡",
        "weights": {"technical": 0.30, "fundamental": 0.22, "capital": 0.28, "safety": 0.20},
        "position_cap": 18,
        "stop_loss": 0.07,
    },
    "aggressive": {
        "label": "进取",
        "weights": {"technical": 0.34, "fundamental": 0.16, "capital": 0.34, "safety": 0.16},
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


class StockSelectionAgentService:
    """Runs a visible research pipeline over verified real-time market data."""

    _HISTORY_LOOKBACK_DAYS = 120
    _QUICK_ANALYSIS_LIMIT = 45
    _FULL_ANALYSIS_LIMIT = 80

    @staticmethod
    def _is_market_session() -> bool:
        now = shanghai_now()
        if now.weekday() >= 5:
            return False
        current_time = now.time()
        morning = current_time.replace(hour=9, minute=15, second=0, microsecond=0) <= current_time <= current_time.replace(hour=11, minute=30, second=0, microsecond=0)
        afternoon = current_time.replace(hour=13, minute=0, second=0, microsecond=0) <= current_time <= current_time.replace(hour=15, minute=30, second=0, microsecond=0)
        return morning or afternoon

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
                "high": float(row.high_price or row.close_price),
                "low": float(row.low_price or row.close_price),
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
        volume_ratio = as_float(stock.get("volume_ratio"))
        change_pct = as_float(stock.get("change_pct"))

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

        if 1.2 <= volume_ratio <= 4:
            score += 8
            evidence.append(f"量比 {volume_ratio:.2f}，成交活跃度匹配")
        elif volume_ratio > 6:
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

        score = round(_clamp(score), 1)
        signal = "看多" if score >= 64 else "看空" if score <= 38 else "中性"
        recent_lows = [as_float(row.get("low")) for row in history[-20:] if as_float(row.get("low")) > 0]
        recent_highs = [as_float(row.get("high")) for row in history[-20:] if as_float(row.get("high")) > 0]
        support = min(recent_lows) if recent_lows else (ma20 or price)
        resistance = max(recent_highs) if recent_highs else (ma60 or price)
        return {
            "agent": "技术面 Agent",
            "skill": "均线趋势、RSI、量价与支撑阻力",
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
                "volume_ratio": round(volume_ratio, 2),
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
        main_inflow = as_int(stock.get("main_net_inflow"))
        inflow_yi = main_inflow / 1e8
        inflow_pct = as_float(stock.get("main_net_inflow_pct"))
        volume_ratio = as_float(stock.get("volume_ratio"))
        turnover = as_float(stock.get("turnover"))
        score = 50.0
        evidence: list[str] = []
        risks: list[str] = []

        if main_inflow > 0:
            flow_boost = min(28.0, 5 + math.log10(1 + main_inflow / 1e6) * 7)
            score += flow_boost
            evidence.append(f"主力净流入 {inflow_yi:+.2f} 亿")
        elif main_inflow < 0:
            flow_penalty = min(28.0, 5 + math.log10(1 + abs(main_inflow) / 1e6) * 7)
            score -= flow_penalty
            risks.append(f"主力净流出 {inflow_yi:+.2f} 亿")
        else:
            evidence.append("主力资金暂未形成净流入优势")

        if inflow_pct >= 5:
            score += 7
            evidence.append(f"主力净流入占比 {inflow_pct:.1f}%")
        elif inflow_pct <= -5:
            score -= 7
            risks.append(f"主力净流入占比 {inflow_pct:.1f}%")
        if 1.2 <= volume_ratio <= 4:
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
                "main_net_inflow_yi": round(inflow_yi, 2),
                "main_net_inflow_pct": round(inflow_pct, 2),
                "turnover": round(turnover, 2),
                "volume_ratio": round(volume_ratio, 2),
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

    def _supervisor_agent(
        self,
        stock: dict,
        technical: dict,
        fundamental: dict,
        capital: dict,
        risk: dict,
        profile: str,
        regime: dict,
    ) -> dict:
        weights = PROFILE_CONFIG[profile]["weights"]
        composite = (
            technical["score"] * weights["technical"]
            + fundamental["score"] * weights["fundamental"]
            + capital["score"] * weights["capital"]
            + risk["score"] * weights["safety"]
        )
        bias = regime.get("bias", "neutral")
        composite += 3 if bias == "bullish" else -4 if bias == "bearish" else 0
        composite = round(_clamp(composite), 1)

        bull_points = [
            *technical["evidence"][:2],
            *fundamental["evidence"][:1],
            *capital["evidence"][:2],
        ][:4]
        bear_points = [
            *technical["risks"][:1],
            *fundamental["risks"][:1],
            *capital["risks"][:1],
            *risk["risks"][:2],
        ][:4]
        bull_score = round((technical["score"] + fundamental["score"] + capital["score"]) / 3, 1)
        bear_score = round(100 - (risk["score"] * 0.55 + technical["score"] * 0.2 + capital["score"] * 0.25), 1)
        history_points = technical["metrics"]["history_points"]
        confidence = round(_clamp(48 + abs(bull_score - bear_score) * 0.32 + min(history_points, 80) * 0.12, 35, 92), 1)

        if composite >= 72 and technical["score"] >= 50 and capital["score"] >= 50 and risk["score"] >= 50:
            verdict = "优先研究"
        elif composite >= 58:
            verdict = "持续跟踪"
        else:
            verdict = "暂不优先"
        decisive_factor = max(
            (("技术趋势", technical["score"]), ("基本面质量", fundamental["score"]), ("资金强度", capital["score"]), ("风险约束", risk["score"])),
            key=lambda item: item[1],
        )[0]
        return {
            "agent": "研究主管 Agent",
            "skill": "多空交叉验证、优先级裁决",
            "score": composite,
            "verdict": verdict,
            "confidence": confidence,
            "summary": f"{regime.get('regime', '震荡市')}环境下，决定性因素为{decisive_factor}。",
            "debate": {
                "bull_score": bull_score,
                "bear_score": bear_score,
                "bull_points": bull_points or ["暂无足够的看多证据"],
                "bear_points": bear_points or ["暂无显著的量化风险信号"],
                "decisive_factor": decisive_factor,
            },
        }

    def _analyze_candidate(self, stock: dict, history: list[dict], profile: str, regime: dict) -> dict:
        technical = self._technical_agent(stock, history)
        fundamental = self._fundamental_agent(stock)
        capital = self._capital_flow_agent(stock)
        risk = self._risk_agent(stock, history, profile)
        supervisor = self._supervisor_agent(stock, technical, fundamental, capital, risk, profile, regime)
        return {
            "code": stock["code"],
            "name": stock["name"],
            "price": round(as_float(stock.get("price")), 2),
            "change_pct": round(as_float(stock.get("change_pct")), 2),
            "turnover": round(as_float(stock.get("turnover")), 2),
            "market_cap": as_int(stock.get("market_cap")),
            "selection_sources": stock.get("selection_sources") or [],
            "score": supervisor["score"],
            "verdict": supervisor["verdict"],
            "confidence": supervisor["confidence"],
            "agents": {
                "technical": technical,
                "fundamental": fundamental,
                "capital": capital,
                "risk": risk,
                "supervisor": supervisor,
            },
        }

    @staticmethod
    def _pipeline_status(candidate_count: int, regime: dict, is_realtime: bool) -> list[dict]:
        freshness = "盘中实时行情" if is_realtime else "最近交易快照（非交易时段）"
        return [
            {
                "id": "data",
                "name": "数据采集 Agent",
                "skill": "实时资金、量比、动量候选池",
                "status": "completed" if candidate_count else "unavailable",
                "summary": f"{freshness}，有效候选池 {candidate_count} 只。",
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
                "skill": "波动率、回撤与研究仓位上限",
                "status": "completed" if candidate_count else "waiting",
                "summary": "风险结论会覆盖单一看多信号。",
            },
            {
                "id": "news",
                "name": "新闻公告 Agent",
                "skill": "公告、政策与事件催化",
                "status": "not_configured",
                "summary": "当前未接入可核验的公告新闻源，未将新闻因素计入评分。",
            },
        ]

    async def run(self, mode: str = "quick", risk_profile: str = "balanced", top_n: int = 5) -> dict:
        if mode not in VALID_SELECTION_MODES:
            raise ValueError("mode 必须是 quick 或 full")
        if risk_profile not in VALID_RISK_PROFILES:
            raise ValueError("risk_profile 必须是 conservative、balanced 或 aggressive")
        top_n = min(max(int(top_n), 3), 10)

        source_result, regime_result = await asyncio.gather(
            collector.fetch_intelligent_selection_candidates(),
            MarketRegime.detect(),
            return_exceptions=True,
        )
        candidates = [] if isinstance(source_result, Exception) else list(source_result.get("stocks") or [])
        if isinstance(regime_result, Exception):
            regime = {"regime": "震荡市", "confidence": 0.5, "bias": "neutral"}
        else:
            regime = regime_result

        candidates = [
            stock for stock in candidates
            if as_float(stock.get("price")) > 0
            and "ST" not in str(stock.get("name") or "").upper()
            and "退" not in str(stock.get("name") or "")
        ]
        candidates.sort(key=self._preliminary_priority, reverse=True)
        analysis_limit = self._FULL_ANALYSIS_LIMIT if mode == "full" else self._QUICK_ANALYSIS_LIMIT
        candidates = candidates[:analysis_limit]
        histories = await self._load_histories([stock["code"] for stock in candidates])
        analyzed = [self._analyze_candidate(stock, histories.get(stock["code"], []), risk_profile, regime) for stock in candidates]
        analyzed.sort(key=lambda item: (item["score"], item["confidence"]), reverse=True)
        now = shanghai_now()
        is_realtime = self._is_market_session()
        cached_dates = [
            row["date"]
            for history in histories.values()
            for row in history
            if row.get("date")
        ]
        data_date = now.date().isoformat() if is_realtime else max(cached_dates, default=now.date().isoformat())

        if not analyzed:
            return {
                "available": False,
                "source": "eastmoney",
                "is_realtime": is_realtime,
                "data_date": data_date,
                "updated_at": now.isoformat(),
                "mode": mode,
                "risk_profile": risk_profile,
                "risk_profile_label": PROFILE_CONFIG[risk_profile]["label"],
                "market_regime": regime,
                "candidate_summary": {"live_candidates": 0, "analyzed": 0, "selected": 0},
                "agent_pipeline": self._pipeline_status(0, regime, is_realtime),
                "recommendations": [],
                "message": "实时行情源当前未返回可交易候选股，系统不会以零价或退市记录生成选股结果。",
                "disclaimer": "结果仅供研究与学习参考，不构成任何投资建议。",
            }

        recommendations = analyzed[:top_n]
        for index, recommendation in enumerate(recommendations, start=1):
            recommendation["rank"] = index
        return {
            "available": True,
            "source": "eastmoney",
            "is_realtime": is_realtime,
            "data_date": data_date,
            "updated_at": now.isoformat(),
            "mode": mode,
            "risk_profile": risk_profile,
            "risk_profile_label": PROFILE_CONFIG[risk_profile]["label"],
            "market_regime": regime,
            "candidate_summary": {
                "live_candidates": as_int(source_result.get("total")) if not isinstance(source_result, Exception) else 0,
                "analyzed": len(analyzed),
                "selected": len(recommendations),
            },
            "agent_pipeline": self._pipeline_status(len(analyzed), regime, is_realtime),
            "recommendations": recommendations,
            "message": "排序先由多维 Agent 交叉验证，再由风险规则约束；新闻公告数据未接入时不参与评分。",
            "disclaimer": "结果仅供研究与学习参考，不构成任何投资建议。市场行情和指标会随盘中数据变化。",
        }


stock_selection_agents = StockSelectionAgentService()
