"""Evidence-backed 5/10/20 trading-day potential assessment."""

from __future__ import annotations

import math
from typing import Any


HORIZON_CONFIG: dict[str, dict] = {
    "week": {
        "label": "未来一周",
        "trading_days": 5,
        "multipliers": {"technical": 1.35, "fundamental": 0.65, "capital": 1.35, "safety": 1.0, "news": 0.8},
    },
    "half_month": {
        "label": "未来半个月",
        "trading_days": 10,
        "multipliers": {"technical": 1.1, "fundamental": 1.0, "capital": 1.1, "safety": 1.05, "news": 0.9},
    },
    "month": {
        "label": "未来一个月",
        "trading_days": 20,
        "multipliers": {"technical": 0.8, "fundamental": 1.4, "capital": 0.75, "safety": 1.2, "news": 1.0},
    },
}
VALID_HORIZONS = frozenset(HORIZON_CONFIG)


def combined_agent_weights(base: dict[str, float], horizon: str) -> dict[str, float]:
    config = HORIZON_CONFIG[horizon]
    weighted = {
        key: max(0.0, float(value)) * float(config["multipliers"].get(key, 1.0))
        for key, value in base.items()
    }
    total = sum(weighted.values()) or 1.0
    return {key: value / total for key, value in weighted.items()}


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = _mean(values)
    return math.sqrt(sum((value - average) ** 2 for value in values) / (len(values) - 1))


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _features(closes: list[float], index: int) -> tuple[float, float, float, float] | None:
    if index < 20 or closes[index - 20] <= 0:
        return None
    momentum_5 = closes[index] / closes[index - 5] - 1
    momentum_20 = closes[index] / closes[index - 20] - 1
    ma20 = _mean(closes[index - 19:index + 1])
    ma_gap = closes[index] / ma20 - 1 if ma20 else 0.0
    returns = [closes[pos] / closes[pos - 1] - 1 for pos in range(index - 19, index + 1) if closes[pos - 1] > 0]
    volatility = _std(returns)
    return momentum_5, momentum_20, ma_gap, volatility


def _analogue_study(closes: list[float], horizon_days: int) -> dict:
    current = _features(closes, len(closes) - 1)
    if current is None:
        return {
            "available": False,
            "sample_count": 0,
            "reason": "至少需要21个有效收盘价",
        }
    candidates: list[tuple[float, int, float]] = []
    scales = (0.04, 0.10, 0.08, 0.025)
    for index in range(20, len(closes) - horizon_days):
        features = _features(closes, index)
        if features is None or closes[index] <= 0:
            continue
        distance = sum(abs(left - right) / scale for left, right, scale in zip(features, current, scales))
        forward_return = (closes[index + horizon_days] / closes[index] - 1) * 100
        candidates.append((distance, index, forward_return))
    candidates.sort(key=lambda item: item[0])

    # Greedily keep the closest shapes whose forward return windows do not
    # overlap. This avoids inflating confidence with near-duplicate labels.
    selected: list[tuple[float, int, float]] = []
    for candidate in candidates:
        if all(abs(candidate[1] - existing[1]) >= horizon_days for existing in selected):
            selected.append(candidate)
        if len(selected) >= 12:
            break
    returns = [item[2] for item in selected]
    if len(returns) < 5:
        return {
            "available": False,
            "sample_count": len(returns),
            "reason": "近一年相似形态的非重叠前瞻样本不足5个",
        }
    median = _percentile(returns, 0.5)
    return {
        "available": True,
        "sample_count": len(returns),
        "positive_rate_pct": round(sum(value > 0 for value in returns) / len(returns) * 100, 1),
        "median_return_pct": round(median or 0.0, 2),
        "lower_quartile_pct": round(_percentile(returns, 0.25) or 0.0, 2),
        "upper_quartile_pct": round(_percentile(returns, 0.75) or 0.0, 2),
        "method": (
            "过去一年内按5日动量、20日动量、MA20距离和20日波动率寻找最近的最多12个历史形态，"
            f"并确保{horizon_days}日后续收益窗口不重叠。"
        ),
        "limitation": "相似形态统计不是价格预测，有限样本、市场制度和行业环境变化都可能使结果失效。",
    }


class HorizonPotentialAnalyzer:
    @staticmethod
    def assess(
        stock: dict,
        history: list[dict],
        agents: dict,
        regime: dict,
        quality: dict,
        risk_assessment: dict,
        horizon: str,
        weights: dict[str, float],
    ) -> dict:
        config = HORIZON_CONFIG[horizon]
        days = int(config["trading_days"])
        closes = [
            value
            for row in history
            for value in [_number(row.get("close"))]
            if value is not None and value > 0
        ]
        current_price = _number(stock.get("price"))
        if current_price and (not closes or abs(closes[-1] - current_price) > 0.0001):
            closes.append(current_price)

        weighted_scores = []
        for key, weight_key in (
            ("technical", "technical"),
            ("fundamental", "fundamental"),
            ("capital", "capital"),
            ("risk", "safety"),
            ("news", "news"),
        ):
            agent = agents.get(key) or {}
            if key == "news" and not agent.get("available"):
                continue
            score = _number(agent.get("score"))
            weight = float(weights.get(weight_key, 0))
            if score is not None and weight > 0:
                weighted_scores.append((key, score, weight))
        weight_total = sum(item[2] for item in weighted_scores) or 1.0
        factor_score = sum(score * weight for _, score, weight in weighted_scores) / weight_total
        if regime.get("bias") == "bullish":
            factor_score += 3
        elif regime.get("bias") == "bearish":
            factor_score -= 4

        analogue = _analogue_study(closes, days)
        potential_score = factor_score
        if analogue.get("available"):
            positive_rate = float(analogue["positive_rate_pct"])
            median_return = float(analogue["median_return_pct"])
            analogue_score = positive_rate * 0.7 + max(0.0, min(100.0, 50 + median_return * 5)) * 0.3
            potential_score = factor_score * 0.85 + analogue_score * 0.15

        hard_blocked = bool(risk_assessment.get("hard_blocked"))
        if hard_blocked:
            potential_score = min(potential_score, 35.0)
        if quality.get("grade") == "不足":
            potential_score = min(potential_score, 45.0)
        potential_score = round(max(0.0, min(100.0, potential_score)), 1)

        if hard_blocked:
            judgement = "风险否决"
        elif quality.get("grade") == "不足":
            judgement = "证据不足"
        elif potential_score >= 72:
            judgement = "潜力较高"
        elif potential_score >= 58:
            judgement = "中性偏强"
        elif potential_score >= 45:
            judgement = "中性观察"
        else:
            judgement = "潜力偏低"

        daily_returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes)) if closes[index - 1] > 0]
        volatility_range = _std(daily_returns[-20:]) * math.sqrt(days) * 100 if len(daily_returns) >= 10 else None
        base_confidence = _number((agents.get("supervisor") or {}).get("confidence")) or 50.0
        if analogue.get("available"):
            base_confidence = min(base_confidence, 55 + int(analogue["sample_count"]) * 2.5)
        else:
            base_confidence = min(base_confidence, 52.0)
        if quality.get("grade") == "一般":
            base_confidence = min(base_confidence, 65.0)
        elif quality.get("grade") == "不足":
            base_confidence = min(base_confidence, 40.0)
        confidence = round(max(25.0, min(90.0, base_confidence)), 1)

        contribution_labels = {
            "technical": "技术趋势",
            "fundamental": "基本面与财务质量",
            "capital": "资金强度",
            "risk": "风险约束",
            "news": "政策与公告",
        }
        contributions = sorted(
            (
                {
                    "factor": contribution_labels[key],
                    "score": round(score, 1),
                    "weight_pct": round(weight / weight_total * 100, 1),
                    "contribution": round((score - 50) * weight / weight_total, 2),
                }
                for key, score, weight in weighted_scores
            ),
            key=lambda item: abs(item["contribution"]),
            reverse=True,
        )

        validation_conditions: list[str] = []
        invalidation_conditions: list[str] = []
        technical_metrics = (agents.get("technical") or {}).get("metrics") or {}
        ma20 = _number(technical_metrics.get("ma20"))
        if ma20 is not None:
            validation_conditions.append(f"收盘维持在MA20 {ma20:.2f}之上或快速收复")
            invalidation_conditions.append(f"有效跌破MA20 {ma20:.2f}且放量")
        main_flow = _number(stock.get("main_net_inflow"))
        if main_flow is not None:
            validation_conditions.append("主力资金保持净流入或流出持续收窄")
            invalidation_conditions.append("主力资金连续转为显著净流出")
        sector_rank = _number(stock.get("sector_rank"))
        if sector_rank is not None:
            validation_conditions.append(f"所属板块强度维持前{max(5, int(sector_rank))}名附近")
        stop_price = _number(((agents.get("risk") or {}).get("plan") or {}).get("stop_loss_price"))
        if stop_price is not None:
            invalidation_conditions.append(f"价格跌破研究止损参考 {stop_price:.2f}")
        invalidation_conditions.extend(risk_assessment.get("hard_blocks") or [])
        invalidation_conditions.extend((risk_assessment.get("warnings") or [])[:2])
        invalidation_conditions.append("出现新的立案、退市、财务造假或债务逾期公告")

        return {
            "horizon": horizon,
            "label": config["label"],
            "trading_days": days,
            "potential_score": potential_score,
            "judgement": judgement,
            "confidence": confidence,
            "volatility_range_pct": round(volatility_range, 2) if volatility_range is not None else None,
            "factor_contributions": contributions,
            "historical_analogue": analogue,
            "validation_conditions": list(dict.fromkeys(validation_conditions))[:4],
            "invalidation_conditions": list(dict.fromkeys(invalidation_conditions))[:6],
            "basis": (
                f"按{config['label']}调整技术、资金、基本面和风险权重，并用近一年相似形态做低权重校验。"
            ),
            "disclaimer": "潜力分是条件性研究排序，不是目标价、收益承诺或自动交易指令。",
        }


horizon_potential_analyzer = HorizonPotentialAnalyzer()
