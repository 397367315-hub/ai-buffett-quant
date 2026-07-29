import math
import json
from typing import Optional
from datetime import date, timedelta
from collections import defaultdict
from sqlalchemy import select
from database import async_session
from models import ConceptFundFlowDaily


class MarketRegime:
    """市场状态识别"""

    @staticmethod
    async def detect() -> dict:
        """基于近20日概念板块数据判断市场状态"""
        today = date.today()
        start = today - timedelta(days=30)

        async with async_session() as session:
            stmt = select(ConceptFundFlowDaily).where(
                ConceptFundFlowDaily.trade_date >= start,
                ConceptFundFlowDaily.trade_date <= today,
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

        if len(rows) < 50:
            return {"regime": "震荡市", "confidence": 0.5, "bias": "neutral"}

        # 聚合每日数据
        daily_agg = defaultdict(lambda: {"total_inflow": 0, "avg_change": 0, "count": 0})
        for r in rows:
            d = r.trade_date.isoformat()
            daily_agg[d]["total_inflow"] += r.main_net_inflow or 0
            daily_agg[d]["avg_change"] += r.change_pct or 0
            daily_agg[d]["count"] += 1

        dates = sorted(daily_agg.keys())
        if len(dates) < 5:
            return {"regime": "震荡市", "confidence": 0.5, "bias": "neutral"}

        recent = dates[-10:]
        inflows = [daily_agg[d]["total_inflow"] / 1e8 for d in recent]

        # 判断标准
        positive_days = sum(1 for v in inflows if v > 0)
        total_inflow = sum(inflows)
        avg_daily_change = sum(daily_agg[d]["avg_change"] / daily_agg[d]["count"] for d in recent) / len(recent)

        if positive_days >= 7 and total_inflow > 50:
            regime = "牛市"
            confidence = min(0.9, 0.5 + positive_days * 0.05 + total_inflow * 0.001)
            bias = "bullish"
        elif positive_days <= 3 and total_inflow < -50:
            regime = "熊市"
            confidence = min(0.9, 0.5 + (10 - positive_days) * 0.05 + abs(total_inflow) * 0.001)
            bias = "bearish"
        else:
            regime = "震荡市"
            confidence = 0.6 + abs(positive_days - 5) * 0.02
            bias = "bullish" if total_inflow > 0 else "bearish" if total_inflow < -10 else "neutral"

        return {
            "regime": regime,
            "confidence": round(confidence, 2),
            "bias": bias,
            "positive_days_10": positive_days,
            "total_inflow_10d": round(total_inflow, 1),
            "avg_daily_change_pct": round(avg_daily_change, 2),
        }


class DynamicWeights:
    """动态权重系统"""
    BASE_WEIGHTS = {
        "fund_flow": 0.30,
        "momentum": 0.20,
        "valuation": 0.18,
        "liquidity": 0.15,
        "sector_strength": 0.12,
        "risk": 0.05,
    }

    REGIME_ADJUSTMENTS = {
        "牛市": {"momentum": 0.08, "fund_flow": 0.05, "valuation": -0.08, "risk": -0.05},
        "熊市": {"valuation": 0.10, "risk": 0.05, "fund_flow": -0.05, "momentum": -0.10},
        "震荡市": {"fund_flow": 0.05, "sector_strength": 0.03, "momentum": -0.03, "liquidity": -0.05},
    }

    @classmethod
    def get_weights(cls, regime: str) -> dict:
        """根据市场状态返回动态权重"""
        weights = cls.BASE_WEIGHTS.copy()
        adj = cls.REGIME_ADJUSTMENTS.get(regime, {})
        for k, v in adj.items():
            weights[k] = max(0.02, min(0.50, weights.get(k, 0) + v))

        # 归一化
        total = sum(weights.values())
        return {k: round(v / total, 4) for k, v in weights.items()}

    @classmethod
    def explain(cls, regime: str) -> str:
        explanations = {
            "牛市": "牛市中动量最重要（趋势会延续），降低估值权重（牛市不看估值），加大动量+资金权重",
            "熊市": "熊市中估值最重要（寻找安全边际），加大风险控制，降低动量权重（避免追跌）",
            "震荡市": "震荡市中资金流向最可靠（主力行为决定方向），加大资金+板块轮动权重",
        }
        return explanations.get(regime, "默认均衡配置")


class RiskParity:
    """风险平价仓位分配"""

    @staticmethod
    def allocate(candidates: list[dict], total_capital: float) -> list[dict]:
        """
        基于波动率进行风险平价分配
        波动率越低的股票分配越多仓位（风险平价原则）
        """
        if not candidates:
            return candidates

        # 用换手率作为波动率代理（换手率越高≈波动越大）
        volatilities = []
        for s in candidates:
            turnover = float(s.get("turnover", 3) or 3)
            change_abs = abs(float(s.get("change_pct", 2) or 2))
            vol_proxy = turnover * 0.6 + change_abs * 5  # 波动率代理
            volatilities.append(max(0.5, vol_proxy))

        # 风险平价：仓位比例 ∝ 1/波动率
        inv_vol = [1.0 / v for v in volatilities]
        total_inv = sum(inv_vol)
        weights = [iv / total_inv for iv in inv_vol]

        results = []
        for i, s in enumerate(candidates):
            allocated = total_capital * weights[i]
            price = float(s.get("price", 10) or 10)
            shares = int(allocated / price / 100) * 100
            if shares < 100:
                shares = 100

            results.append({
                **s,
                "risk_parity_weight": round(weights[i] * 100, 1),
                "volatility_proxy": round(volatilities[i], 1),
                "allocated_capital": round(allocated, 0),
                "suggested_shares": shares,
            })

        return results


class EnhancedQuantScorer:
    """增强版量化评分引擎"""

    def __init__(self):
        self.regime = "震荡市"

    async def update_regime(self):
        regime_info = await MarketRegime.detect()
        self.regime = regime_info["regime"]
        return regime_info

    @staticmethod
    def normalize(value: float, min_val: float, max_val: float) -> float:
        if max_val == min_val:
            return 50
        return max(0, min(100, (value - min_val) / (max_val - min_val) * 100))

    def score_fund_flow(self, main_inflow_yi: float) -> dict:
        if main_inflow_yi >= 10:
            s = 90 + min(10, (main_inflow_yi - 10) * 0.5)
        elif main_inflow_yi >= 5:
            s = 70 + (main_inflow_yi - 5) * 4
        elif main_inflow_yi >= 1:
            s = 50 + (main_inflow_yi - 1) * 5
        elif main_inflow_yi >= -1:
            s = 50 + main_inflow_yi * 25
        elif main_inflow_yi >= -5:
            s = 25 + (main_inflow_yi + 5) * 5
        else:
            s = max(5, 25 + main_inflow_yi * 2)
        return {"score": round(s, 1), "raw": main_inflow_yi}

    def score_momentum(self, change_pct: float, volume_ratio: float) -> dict:
        if 2 <= change_pct <= 5:
            cs = 85 + (change_pct - 2) * 3
        elif 5 < change_pct <= 7:
            cs = 90 - (change_pct - 5) * 2
        elif 0 < change_pct < 2:
            cs = 60 + change_pct * 12.5
        elif change_pct > 7:
            cs = max(40, 80 - (change_pct - 7) * 5)
        else:
            cs = max(10, 50 + change_pct * 5)

        if 1.5 <= volume_ratio <= 4:
            vs = 85 + (volume_ratio - 1.5) * 3
        elif 4 < volume_ratio <= 8:
            vs = 90 - (volume_ratio - 4) * 4
        elif 0.8 <= volume_ratio < 1.5:
            vs = 55 + (volume_ratio - 0.8) * 25
        elif volume_ratio > 8:
            vs = max(30, 70 - (volume_ratio - 8) * 3)
        else:
            vs = max(20, volume_ratio * 40)

        return {"score": round(cs * 0.5 + vs * 0.5, 1), "change_score": round(cs, 1), "volume_score": round(vs, 1)}

    def score_valuation(self, pe: Optional[float], roe: Optional[float]) -> dict:
        pe_s = 50
        if pe is not None and pe > 0:
            if pe <= 15: pe_s = 90
            elif pe <= 25: pe_s = 80 - (pe - 15)
            elif pe <= 40: pe_s = 70 - (pe - 25) * 0.67
            elif pe <= 60: pe_s = 60 - (pe - 40) * 0.5
            else: pe_s = max(25, 50 - (pe - 60) * 0.2)
        elif pe is not None and pe < 0:
            pe_s = 20

        roe_s = 50
        if roe is not None:
            if roe >= 20: roe_s = 95
            elif roe >= 15: roe_s = 80 + (roe - 15)
            elif roe >= 10: roe_s = 65 + (roe - 10) * 3
            elif roe >= 5: roe_s = 50 + (roe - 5) * 3
            elif roe > 0: roe_s = 30 + roe * 4
            else: roe_s = 15

        return {"score": round(pe_s * 0.6 + roe_s * 0.4, 1), "pe_score": round(pe_s, 1), "roe_score": round(roe_s, 1)}

    def score_liquidity(self, turnover: float) -> dict:
        if 3 <= turnover <= 10:
            s = 85 + (turnover - 3) * 0.7
        elif 10 < turnover <= 20:
            s = 88 - (turnover - 10) * 2
        elif 1 <= turnover < 3:
            s = 60 + (turnover - 1) * 12.5
        elif turnover > 20:
            s = max(35, 68 - (turnover - 20))
        else:
            s = max(15, turnover * 40)
        return {"score": round(s, 1)}

    def score_sector_strength(self, sector_change_pct: float, sector_flow_yi: float) -> dict:
        s = 50
        if sector_change_pct > 0: s += min(30, sector_change_pct * 6)
        else: s += max(-25, sector_change_pct * 5)
        if sector_flow_yi > 5: s += 15
        elif sector_flow_yi > 0: s += sector_flow_yi * 2
        else: s += max(-10, sector_flow_yi)
        return {"score": round(max(10, min(100, s)), 1)}

    def compute(self, stock: dict, sector_change: float = 0, sector_flow: float = 0, weights: dict = None) -> dict:
        if weights is None:
            weights = DynamicWeights.BASE_WEIGHTS

        main_inflow = stock.get("main_inflow_yi", 0)
        change_pct = float(stock.get("change_pct", 0) or 0)
        volume_ratio = float(stock.get("volume_ratio", 0) or 0)
        pe = float(stock.get("pe", 0) or 0) if stock.get("pe") and stock.get("pe") != "-" else None
        roe = float(stock.get("roe", 0) or 0) if stock.get("roe") and stock.get("roe") != "-" else None
        turnover = float(stock.get("turnover", 0) or 0)

        factors = {
            "fund_flow": self.score_fund_flow(main_inflow),
            "momentum": self.score_momentum(change_pct, volume_ratio),
            "valuation": self.score_valuation(pe, roe),
            "liquidity": self.score_liquidity(turnover),
            "sector_strength": self.score_sector_strength(sector_change, sector_flow),
            "risk": {"score": 60.0},
        }

        composite = sum(factors[k]["score"] * weights.get(k, 0) for k in weights)

        if composite >= 80: grade, label = "S", "强烈推荐"
        elif composite >= 70: grade, label = "A", "推荐"
        elif composite >= 60: grade, label = "B", "关注"
        elif composite >= 50: grade, label = "C", "中性"
        else: grade, label = "D", "回避"

        return {
            "composite_score": round(composite, 1),
            "grade": grade,
            "grade_label": label,
            "factors": factors,
            "weights": weights,
            "regime": self.regime,
        }


enhanced_scorer = EnhancedQuantScorer()


class BacktestEngine:
    """回测引擎：用历史数据验证评分策略"""

    @staticmethod
    async def run(days: int = 30, top_n: int = 5) -> dict:
        """回测过去N天：每天选TOP N股票，计算次日收益"""
        from services.data_collector import collector

        async with async_session() as session:
            stmt = select(ConceptFundFlowDaily.trade_date).distinct().order_by(
                ConceptFundFlowDaily.trade_date.desc()
            ).limit(days + 1)
            result = await session.execute(stmt)
            trade_dates = [r[0] for r in result.all()]

        if len(trade_dates) < 2:
            return {"error": "历史数据不足"}

        # 简化回测：用历史数据库中的资金流向数据模拟选股
        daily_results = []
        total_return = 0

        for i in range(len(trade_dates) - 1):
            trade_date = trade_dates[i]
            next_date = trade_dates[i + 1]

            async with async_session() as session:
                stmt = select(ConceptFundFlowDaily).where(
                    ConceptFundFlowDaily.trade_date == trade_date,
                ).order_by(ConceptFundFlowDaily.main_net_inflow.desc()).limit(top_n)
                result = await session.execute(stmt)
                selected = result.scalars().all()

            if not selected:
                continue

            # 计算这些板块次日的平均涨跌幅
            next_day_changes = []
            async with async_session() as session:
                for s in selected:
                    n_stmt = select(ConceptFundFlowDaily).where(
                        ConceptFundFlowDaily.board_code == s.board_code,
                        ConceptFundFlowDaily.trade_date == next_date,
                    )
                    n_result = await session.execute(n_stmt)
                    next_day = n_result.scalar_one_or_none()
                    if next_day and next_day.change_pct is not None:
                        next_day_changes.append(next_day.change_pct)

            if next_day_changes:
                avg_return = sum(next_day_changes) / len(next_day_changes)
                total_return += avg_return
                daily_results.append({
                    "date": trade_date.isoformat(),
                    "next_date": next_date.isoformat(),
                    "selected_count": len(selected),
                    "valid_count": len(next_day_changes),
                    "avg_next_return": round(avg_return, 2),
                    "cumulative_return": round(total_return, 2),
                })

        if not daily_results:
            return {"error": "无法完成回测"}

        win_days = sum(1 for d in daily_results if d["avg_next_return"] > 0)
        win_rate = round(win_days / len(daily_results) * 100, 1)
        avg_daily_return = round(total_return / len(daily_results), 2)
        max_drawdown = BacktestEngine._calc_max_drawdown(daily_results)

        return {
            "period": f"{trade_dates[-1]} ~ {trade_dates[0]}",
            "trading_days": len(daily_results),
            "total_return": round(total_return, 2),
            "avg_daily_return": avg_daily_return,
            "win_rate": win_rate,
            "max_drawdown": max_drawdown,
            "daily_details": daily_results[-10:],
            "sharpe_ratio": round(avg_daily_return / max(0.01, BacktestEngine._calc_std(daily_results)), 2) if daily_results else 0,
        }

    @staticmethod
    def _calc_max_drawdown(daily_results: list[dict]) -> float:
        peak = 0
        max_dd = 0
        cumulative = 0
        for d in daily_results:
            cumulative += d["avg_next_return"]
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)
        return round(max_dd, 2)

    @staticmethod
    def _calc_std(daily_results: list[dict]) -> float:
        returns = [d["avg_next_return"] for d in daily_results]
        if len(returns) < 2:
            return 0.01
        avg = sum(returns) / len(returns)
        variance = sum((r - avg) ** 2 for r in returns) / len(returns)
        return math.sqrt(variance)
