"""Date-aligned, auditable A-share market decision workbench.

The service aggregates existing structured research outputs. Missing values
remain unavailable and are never converted into synthetic neutral scores.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime
from typing import Any, Awaitable

from sqlalchemy import desc, select

from database import async_session
from models import MarketDataCache, MarketSentimentDaily, StockSelectionRun
from services.data_collector import collector, is_a_share_market_session, shanghai_now
from services.overnight_strategy import overnight_strategy_service
from services.topic_strength import topic_strength_service


WORKBENCH_CACHE_KEY = "market_decision_workbench_latest_v1"
WORKBENCH_CACHE_PREFIX = "market_decision_workbench_v1:"
SCORE_VERSION = "market-state-v1.0.0"
CANDIDATE_SCORE_VERSION = "workbench-candidate-v1.0.0"

SCORE_DIMENSIONS = (
    ("trend", "市场趋势", 20),
    ("breadth", "市场宽度", 15),
    ("liquidity", "成交活跃", 15),
    ("emotion", "市场情绪", 15),
    ("mainline", "主线集中度", 15),
    ("capital", "资金行为", 10),
    ("risk", "风险状态", 10),
)


def _number(value: object) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: object) -> int | None:
    parsed = _number(value)
    return int(parsed) if parsed is not None else None


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return min(upper, max(lower, value))


def _scale(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return _clamp((value - low) / (high - low) * 100)


def _average(values: list[float | None]) -> float | None:
    observed = [float(value) for value in values if value is not None]
    return sum(observed) / len(observed) if observed else None


def _round(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None else None


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _moving_average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _index_dimension(
    index_history: list[dict],
    decision_date: date,
) -> tuple[float | None, dict, list[str], str]:
    rows: list[tuple[date, float]] = []
    for item in index_history:
        row_date = _date(item.get("date") or item.get("trade_date"))
        close = _number(item.get("close"))
        if row_date and close is not None and row_date <= decision_date:
            rows.append((row_date, close))
    rows = sorted({row_date: close for row_date, close in rows}.items())
    closes = [item[1] for item in rows]
    if len(closes) < 20:
        return (
            None,
            {"sample_count": len(closes)},
            ["上证指数至少20个交易日日线"],
            "样本不足，不计算趋势分",
        )

    close = closes[-1]
    ma5 = _moving_average(closes, 5)
    ma10 = _moving_average(closes, 10)
    ma20 = _moving_average(closes, 20)
    ma30 = _moving_average(closes, 30)
    ma60 = _moving_average(closes, 60)
    return_5d = (close / closes[-6] - 1) * 100 if len(closes) >= 6 and closes[-6] else None
    return_20d = (close / closes[-21] - 1) * 100 if len(closes) >= 21 and closes[-21] else None
    distance_ma20 = (close / ma20 - 1) * 100 if ma20 else None
    alignment = None
    if ma5 is not None and ma10 is not None and ma20 is not None:
        alignment = 100.0 if ma5 > ma10 > ma20 else 65.0 if ma5 > ma20 else 25.0
    score = _average([
        _scale(distance_ma20, -4, 4) if distance_ma20 is not None else None,
        alignment,
        _scale(return_5d, -3, 3) if return_5d is not None else None,
        _scale(return_20d, -8, 8) if return_20d is not None else None,
    ])
    metrics = {
        "data_date": rows[-1][0].isoformat(),
        "close": _round(close, 2),
        "ma5": _round(ma5, 2),
        "ma10": _round(ma10, 2),
        "ma20": _round(ma20, 2),
        "ma30": _round(ma30, 2),
        "ma60": _round(ma60, 2),
        "distance_ma20_pct": _round(distance_ma20, 2),
        "return_5d_pct": _round(return_5d, 2),
        "return_20d_pct": _round(return_20d, 2),
        "sample_count": len(closes),
    }
    if rows[-1][0] != decision_date:
        return (
            None,
            metrics,
            [f"上证指数最新值为 {rows[-1][0].isoformat()}，与决策日 {decision_date.isoformat()} 不同日"],
            "跨日指数只展示上一可用值，不计入当日评分",
        )
    evidence = [
        f"上证收盘 {close:.2f}，相对MA20 {distance_ma20:+.2f}%"
        if distance_ma20 is not None else "上证MA20待采集",
        f"近5日涨跌 {return_5d:+.2f}%" if return_5d is not None else "近5日涨跌待采集",
    ]
    return score, metrics, evidence, "指数位置、均线排列、5日斜率和20日收益等权"


def _breadth_dimension(market: dict) -> tuple[float | None, dict, list[str], str]:
    sentiment = market.get("sentiment") or {}
    ratio = _number(sentiment.get("up_ratio"))
    up = _integer(sentiment.get("up"))
    down = _integer(sentiment.get("down"))
    if ratio is None and up is not None and down is not None and up + down:
        ratio = up / (up + down) * 100
    if ratio is None:
        return None, {"up": up, "down": down}, ["全市场上涨/下跌家数"], "上涨占比缺失"
    return (
        _scale(ratio, 25, 75),
        {
            "up": up,
            "down": down,
            "flat": _integer(sentiment.get("flat")),
            "up_ratio": _round(ratio, 1),
            "breadth": sentiment.get("breadth") or "不可判定",
        },
        [f"上涨 {up if up is not None else '--'} 家，下跌 {down if down is not None else '--'} 家，上涨占比 {ratio:.1f}%"],
        "上涨占比25%-75%线性映射",
    )


def _liquidity_dimension(
    market: dict,
    sentiment_history: list[dict],
    decision_date: date,
) -> tuple[float | None, dict, list[str], str]:
    liquidity = market.get("liquidity") or {}
    current = _number(liquidity.get("market_amount"))
    prior: list[tuple[date, float]] = []
    for item in sentiment_history:
        row_date = _date(item.get("trade_date"))
        amount = _number(item.get("market_amount"))
        if row_date and amount is not None and row_date < decision_date:
            prior.append((row_date, amount))
    prior.sort(key=lambda item: item[0])
    values = [item[1] for item in prior]
    average_5 = sum(values[-5:]) / min(len(values), 5) if len(values) >= 3 else None
    average_20 = sum(values[-20:]) / min(len(values), 20) if len(values) >= 10 else None
    comparison = average_5 or average_20
    metrics = {
        "market_amount": _integer(current),
        "previous_5d_average": _integer(average_5),
        "previous_20d_average": _integer(average_20),
        "amount_complete": bool(liquidity.get("amount_complete")),
    }
    if current is None or comparison in (None, 0):
        return (
            None,
            metrics,
            ["两市成交额与至少3个历史交易日"],
            "历史成交额不足，不按绝对金额猜测活跃度",
        )
    ratio = current / comparison
    change_pct = (ratio - 1) * 100
    score = _clamp(55 + (ratio - 1) * 100)
    metrics.update({
        "comparison_ratio": _round(ratio, 3),
        "change_pct": _round(change_pct, 1),
    })
    return (
        score,
        metrics,
        [f"两市成交额 {current / 1e8:.0f} 亿，较历史基准 {change_pct:+.1f}%"],
        "当前成交额相对前5日均额；历史不足时回退20日均额",
    )


def _emotion_dimension(market: dict) -> tuple[float | None, dict, list[str], str]:
    emotion = market.get("emotion") or {}
    limit_up = _integer(emotion.get("zt_count"))
    limit_down = _integer(emotion.get("dt_count"))
    break_rate = _number(emotion.get("break_rate"))
    score = _average([
        _scale(float(limit_up), 10, 100) if limit_up is not None else None,
        _clamp(100 - limit_down * 4) if limit_down is not None else None,
        _clamp(100 - break_rate * 2) if break_rate is not None else None,
    ])
    if score is None:
        return None, {}, ["涨停、跌停或炸板率"], "情绪事件数据缺失"
    return (
        score,
        {
            "limit_up": limit_up,
            "limit_down": limit_down,
            "failed_limit": _integer(emotion.get("zb_count")),
            "break_rate": _round(break_rate, 2),
        },
        [
            f"涨停 {limit_up if limit_up is not None else '--'}、跌停 {limit_down if limit_down is not None else '--'}",
            f"炸板率 {break_rate:.1f}%" if break_rate is not None else "炸板率待采集",
        ],
        "涨停活跃、跌停安全度和炸板安全度按可观测项等权",
    )


def _mainline_dimension(topics: list[dict]) -> tuple[float | None, dict, list[str], str]:
    valid = [item for item in topics if _number(item.get("strength_score")) is not None]
    if not valid:
        return None, {}, ["题材强度与板块宽度"], "主线样本缺失"
    top = valid[:3]
    strengths = [_number(item.get("strength_score")) for item in top]
    average_strength = _average(strengths)
    top_strength = strengths[0] if strengths else None
    breadth = _average([_number(item.get("breadth")) for item in top])
    strong_ratio = sum(item.get("status") == "强" for item in top) / len(top) * 100
    continuity = sum(item.get("novelty") == "延续" for item in top) / len(top) * 100
    score = (
        (top_strength or 0) * 0.35
        + (average_strength or 0) * 0.25
        + strong_ratio * 0.20
        + (breadth if breadth is not None else 0) * 0.10
        + continuity * 0.10
    )
    return (
        _clamp(score),
        {
            "top_topic": top[0].get("name"),
            "top_strength": _round(top_strength, 1),
            "top3_average_strength": _round(average_strength, 1),
            "top3_average_breadth": _round(breadth, 1),
            "strong_ratio": _round(strong_ratio, 1),
            "continuity_ratio": _round(continuity, 1),
        },
        [
            f"前三题材：{'、'.join(str(item.get('name') or '--') for item in top)}",
            f"强题材 {sum(item.get('status') == '强' for item in top)}/{len(top)}，平均强度 {average_strength:.1f}"
            if average_strength is not None else "题材平均强度待采集",
        ],
        "头部强度、前三均值、强题材占比、宽度和延续性加权",
    )


def _capital_dimension(market: dict) -> tuple[float | None, dict, list[str], str]:
    sectors = [
        item for item in market.get("top_sectors") or []
        if _number(item.get("main_net_inflow")) is not None
    ]
    if not sectors:
        return None, {}, ["行业资金净额"], "资金行为数据缺失"
    top = sectors[:5]
    total = sum(_number(item.get("main_net_inflow")) or 0 for item in top)
    market_amount = _number((market.get("liquidity") or {}).get("market_amount"))
    ratio_pct = total / market_amount * 100 if market_amount else None
    positive_ratio = sum(
        (_number(item.get("main_net_inflow")) or 0) > 0 for item in top
    ) / len(top) * 100
    score = _average([
        _scale(ratio_pct, -1, 4) if ratio_pct is not None else None,
        positive_ratio,
    ])
    names = "、".join(str(item.get("name") or "--") for item in top[:3])
    return (
        score,
        {
            "top5_net_inflow": int(total),
            "top5_to_market_amount_pct": _round(ratio_pct, 2),
            "positive_ratio": _round(positive_ratio, 1),
            "top_sectors": [item.get("name") for item in top],
        },
        [f"资金靠前板块：{names}，前五净额合计 {total / 1e8:+.1f} 亿"],
        "前五行业资金净额占成交额比例与正流入占比",
    )


def _risk_dimension(
    market: dict,
    topics: list[dict],
    loss_alert: dict,
) -> tuple[float | None, dict, list[str], str]:
    emotion = market.get("emotion") or {}
    limit_down = _integer(emotion.get("dt_count"))
    break_rate = _number(emotion.get("break_rate"))
    leaders = [item.get("leader") or {} for item in topics[:5]]
    observed_heat = [
        bool(item.get("overheated"))
        for item in leaders
        if item.get("overheated") is not None
    ]
    heat_ratio = sum(observed_heat) / len(observed_heat) * 100 if observed_heat else None
    market_components = [
        _clamp(100 - limit_down * 4) if limit_down is not None else None,
        _clamp(100 - break_rate * 2) if break_rate is not None else None,
        _clamp(100 - heat_ratio * 0.6) if heat_ratio is not None else None,
    ]
    if not any(value is not None for value in market_components):
        return None, {}, ["跌停、炸板率或主线过热状态"], "市场风险字段缺失"
    score = _average([
        *market_components,
        60.0 if loss_alert.get("warning") else 100.0 if loss_alert else None,
    ])
    if score is None:
        return None, {}, ["跌停、炸板率或主线过热状态"], "风险字段缺失"
    warnings = []
    if break_rate is not None and break_rate >= 25:
        warnings.append("炸板率偏高")
    if heat_ratio is not None and heat_ratio >= 50:
        warnings.append("头部题材龙头偏热")
    if loss_alert.get("warning"):
        warnings.append(str(loss_alert.get("reason") or "模拟交易出现连续亏损提醒"))
    return (
        score,
        {
            "limit_down": limit_down,
            "break_rate": _round(break_rate, 2),
            "overheated_leader_ratio": _round(heat_ratio, 1),
            "consecutive_losses": _integer(loss_alert.get("consecutive_losses")),
            "warnings": warnings,
        },
        warnings or ["当前已观测风险项未触发高风险阈值"],
        "跌停、炸板、龙头过热与连续亏损提醒的安全度均值",
    )


def calculate_market_state(
    topic_snapshot: dict,
    index_history: list[dict],
    sentiment_history: list[dict],
    loss_alert: dict | None = None,
) -> dict:
    """Calculate seven dimensions without imputing missing values."""
    decision_date = _date(topic_snapshot.get("data_date"))
    if decision_date is None:
        return {
            "state_code": "S0",
            "state_label": "不可判定",
            "score": None,
            "execution_level": "等待数据",
            "coverage_pct": 0.0,
            "confidence_pct": 0.0,
            "dimensions": [],
            "version": SCORE_VERSION,
        }
    market = topic_snapshot.get("market") or {}
    topics = list(topic_snapshot.get("topics") or [])
    calculations = {
        "trend": _index_dimension(index_history, decision_date),
        "breadth": _breadth_dimension(market),
        "liquidity": _liquidity_dimension(market, sentiment_history, decision_date),
        "emotion": _emotion_dimension(market),
        "mainline": _mainline_dimension(topics),
        "capital": _capital_dimension(market),
        "risk": _risk_dimension(market, topics, loss_alert or {}),
    }
    dimensions = []
    observed_weight = 0
    weighted_score = 0.0
    for key, label, weight in SCORE_DIMENSIONS:
        score, metrics, evidence, method = calculations[key]
        observed = score is not None
        contribution = round(score * weight / 100, 2) if observed else None
        if observed:
            observed_weight += weight
            weighted_score += score * weight
        dimensions.append({
            "id": key,
            "label": label,
            "weight": weight,
            "score": _round(score, 1),
            "observed": observed,
            "contribution": contribution,
            "metrics": metrics,
            "evidence": evidence,
            "method": method,
        })
    coverage = float(observed_weight)
    observed_score = round(weighted_score / observed_weight, 1) if observed_weight else None
    quality = topic_snapshot.get("data_quality") or {}
    quality_factor = 1.0 if quality.get("complete_market_snapshot") else 0.82
    confidence = round(coverage * quality_factor, 1)
    total = observed_score if coverage >= 40 else None
    if total is None:
        state_code, state_label, execution = "S0", "不可判定", "等待数据"
    elif total >= 80:
        state_code, state_label, execution = "S1", "强趋势", "强执行"
    elif total >= 65:
        state_code, state_label, execution = "S2", "趋势启动", "正常执行"
    elif total >= 50:
        state_code, state_label, execution = "S3", "震荡", "谨慎执行"
    elif total >= 35:
        state_code, state_label, execution = "S4", "情绪退潮", "低频执行"
    else:
        state_code, state_label, execution = "S5", "风险释放", "停止高风险策略"
    return {
        "state_code": state_code,
        "state_label": state_label,
        "score": total,
        "observed_score": observed_score,
        "execution_level": execution,
        "coverage_pct": coverage,
        "confidence_pct": confidence,
        "dimensions": dimensions,
        "version": SCORE_VERSION,
        "thresholds": [
            {"min": 80, "label": "强执行"},
            {"min": 65, "label": "正常执行"},
            {"min": 50, "label": "谨慎执行"},
            {"min": 35, "label": "低频执行"},
            {"min": 0, "label": "停止高风险策略"},
        ],
        "missing_policy": "缺失维度不填50分；总分仅按已观测权重归一化，并同步披露覆盖率。",
    }


def _main_lines(topic_snapshot: dict) -> list[dict]:
    output = []
    for index, topic in enumerate((topic_snapshot.get("topics") or [])[:8], start=1):
        status = str(topic.get("status") or "观察")
        novelty = str(topic.get("novelty") or "待核验")
        breadth = _number(topic.get("breadth"))
        leader = topic.get("leader") or {}
        if index <= 2 and status == "强":
            classification = "核心主线"
        elif status == "强":
            classification = "强势支线"
        else:
            classification = "短期热点"
        if leader.get("overheated"):
            lifecycle = "分化预警"
        elif status != "强" and breadth is not None and breadth < 45:
            lifecycle = "退潮"
        elif novelty == "新出现":
            lifecycle = "启动"
        elif novelty == "延续" and breadth is not None and breadth >= 65:
            lifecycle = "强化"
        elif novelty == "延续":
            lifecycle = "扩散"
        else:
            lifecycle = "观察"
        risks = []
        if leader.get("overheated"):
            risks.append("龙头近5日涨幅或换手偏热")
        if breadth is None:
            risks.append("板块宽度待采集")
        elif breadth < 50:
            risks.append("板块宽度不足50%")
        risks.extend(str(item) for item in (topic.get("audit") or {}).get("gaps") or [])
        output.append({
            "rank": index,
            "name": str(topic.get("name") or "未分类"),
            "classification": classification,
            "lifecycle": lifecycle,
            "strength_score": _round(_number(topic.get("strength_score")), 1),
            "breadth": _round(breadth, 1),
            "change_pct": _round(_number(topic.get("sector_change_pct")), 2),
            "main_net_inflow": _integer(topic.get("sector_main_net_inflow")),
            "member_count": _integer(topic.get("member_count")),
            "evidence": str(topic.get("evidence") or "证据待采集"),
            "leader": {
                "code": str(leader.get("code") or ""),
                "name": str(leader.get("name") or "--"),
                "price": _round(_number(leader.get("price")), 2),
                "change_pct": _round(_number(leader.get("pct")), 2),
                "boards": _integer(leader.get("boards")),
                "heat_status": leader.get("heat_status") or "待核验",
            },
            "risk_flags": _unique(risks),
        })
    return output


def _topic_candidate(topic: dict, market_state: dict, decision_date: str) -> dict | None:
    stock = topic.get("leader") or {}
    code = str(stock.get("code") or "")
    if not code:
        return None
    market_fit = _number(market_state.get("score"))
    sector_strength = _number(topic.get("strength_score"))
    return_5d = _number(stock.get("return_5d_pct"))
    trend = _scale(return_5d, -5, 20) if return_5d is not None else None
    turnover = _number(stock.get("turnover"))
    intraday = stock.get("intraday") or {}
    volume_price = _average([
        _clamp(100 - abs(turnover - 8) * 7) if turnover is not None else None,
        80.0 if intraday.get("above_vwap") is True
        else 25.0 if intraday.get("above_vwap") is False else None,
    ])
    stock_change = _number(stock.get("pct"))
    sector_change = _number(topic.get("sector_change_pct"))
    relative = _scale(stock_change - sector_change, -3, 7) if (
        stock_change is not None and sector_change is not None
    ) else None
    inflow = _number(stock.get("main_net_inflow"))
    capital = _clamp(50 + _clamp(inflow / 100_000_000, -1, 1) * 40) if inflow is not None else None
    data_gaps = [str(item) for item in stock.get("data_gaps") or []]
    penalty = 6.0 if stock.get("overheated") else 0.0
    if turnover is not None and turnover > 20:
        penalty += 2
    if intraday.get("active_direction") == "sell":
        penalty += 2
    penalty = min(10.0, penalty + min(3, len(data_gaps)))
    weighted_parts = [
        (market_fit, 0.20),
        (sector_strength, 0.20),
        (trend, 0.15),
        (volume_price, 0.15),
        (relative, 0.10),
        (capital, 0.10),
    ]
    observed_weight = sum(weight for value, weight in weighted_parts if value is not None)
    score = (
        _clamp(sum(float(value) * weight for value, weight in weighted_parts if value is not None) / observed_weight - penalty)
        if observed_weight else None
    )
    components = {
        "market_fit": _round(market_fit, 1),
        "sector_strength": _round(sector_strength, 1),
        "trend": _round(trend, 1),
        "volume_price": _round(volume_price, 1),
        "relative_strength": _round(relative, 1),
        "capital": _round(capital, 1),
        "risk_penalty": _round(penalty, 1),
    }
    why = [str(topic.get("evidence") or "")]
    if intraday.get("above_vwap") is True:
        why.append("分时价格位于均价线上方")
    if inflow is not None:
        why.append(f"个股主力净额 {inflow / 1e8:+.2f} 亿")
    why_not = []
    if stock.get("overheated"):
        why_not.append("位置偏热，风险扣分")
    if intraday.get("active_direction") == "sell":
        why_not.append("已采集主动方向偏卖出")
    why_not.extend(f"{item}待补" for item in data_gaps[:2])
    return {
        "code": code,
        "name": str(stock.get("name") or code),
        "sector": str(topic.get("name") or stock.get("industry") or "未分类"),
        "price": _round(_number(stock.get("price")), 2),
        "change_pct": _round(stock_change, 2),
        "score": _round(score, 1),
        "confidence_pct": round(observed_weight / 0.90 * 100, 1),
        "score_breakdown": components,
        "score_method": CANDIDATE_SCORE_VERSION,
        "strategy": "主线结构观察",
        "pool": "主线观察池",
        "status": "观察",
        "execution_eligible": False,
        "stale": False,
        "data_date": decision_date,
        "why_selected": _unique(why)[:4],
        "why_not_full": _unique(why_not) or ["尚未经过独立策略触发确认"],
        "abandon_conditions": ["板块宽度跌破50%", "龙头断板或板块明显退潮", "个股跌破关键结构"],
        "source": "topic_strength",
    }


def _selection_candidates(
    selection: dict | None,
    decision_date: str,
    market_state: dict,
    main_lines: list[dict],
) -> list[dict]:
    if not selection:
        return []
    data_date = str(selection.get("data_date") or "")[:10]
    if _date(data_date) and _date(decision_date) and _date(data_date) > _date(decision_date):
        return []
    same_date = data_date == decision_date
    result = selection.get("result") or {}
    output = []
    sector_by_name = {item["name"]: item for item in main_lines}
    for item in (result.get("recommendations") or [])[:8]:
        agents = item.get("agents") or {}
        technical = agents.get("technical") or {}
        capital_agent = agents.get("capital") or {}
        risk_agent = agents.get("risk") or {}
        supervisor = agents.get("supervisor") or {}
        sector = str(item.get("sector") or "未分类")
        matching_line = sector_by_name.get(sector) if same_date else None
        debate = supervisor.get("debate") or {}
        outlook = item.get("horizon_outlook") or {}
        abandon = outlook.get("invalidation_conditions") or outlook.get("risk_triggers") or []
        risk_score = _number(risk_agent.get("score"))
        output.append({
            "code": str(item.get("code") or ""),
            "name": str(item.get("name") or item.get("code") or "--"),
            "sector": sector,
            "price": _round(_number(item.get("price")), 2),
            "change_pct": _round(_number(item.get("change_pct")), 2),
            "score": _round(_number(item.get("score")), 1),
            "confidence_pct": _round(_number(item.get("confidence")), 1),
            "score_breakdown": {
                "market_fit": _round(_number(market_state.get("score")), 1) if same_date else None,
                "sector_strength": _round(_number((matching_line or {}).get("strength_score")), 1),
                "trend": _round(_number(technical.get("score")), 1),
                "volume_price": None,
                "relative_strength": None,
                "capital": _round(_number(capital_agent.get("score")), 1),
                "risk_penalty": _round((100 - risk_score) / 10, 1) if risk_score is not None else None,
            },
            "score_method": "stock-selection-agent-v3",
            "strategy": str((result.get("research_horizon") or {}).get("label") or "智能选股Agent"),
            "pool": "今日候选池" if same_date else "历史观察池",
            "status": "待策略触发" if same_date else "跨日观察",
            "execution_eligible": False,
            "stale": not same_date,
            "data_date": data_date or None,
            "why_selected": [str(value) for value in (debate.get("bull_points") or [])[:4]]
            or [str(supervisor.get("summary") or "Agent证据待查看")],
            "why_not_full": [str(value) for value in (debate.get("bear_points") or [])[:4]]
            or ["需结合策略触发与风险审计"],
            "abandon_conditions": [str(value) for value in abandon[:4]]
            or ["市场状态恶化", "板块明显退潮", "个股结构失效"],
            "source": "stock_selection_run",
            "source_run_id": selection.get("id"),
        })
    return [item for item in output if item["code"]]


def _overnight_candidates(overnight: dict, decision_date: str) -> list[dict]:
    run = overnight.get("latest_entry_run") or {}
    same_date = str(run.get("data_date") or "")[:10] == decision_date
    eligible_run = same_date and run.get("status") in {"completed", "partial"}
    output = []
    for item in (run.get("candidates") or [])[:8]:
        code = str(item.get("code") or item.get("stock_code") or "")
        if not code:
            continue
        conditions = item.get("conditions") or []
        failed = [
            str(row.get("label") or row.get("key"))
            for row in conditions if row.get("status") == "failed"
        ]
        passed = [
            str(row.get("label") or row.get("key"))
            for row in conditions if row.get("status") == "passed"
        ]
        strategy = (run.get("data_quality") or {}).get("strategy") or {}
        output.append({
            "code": code,
            "name": str(item.get("name") or item.get("stock_name") or code),
            "sector": str(item.get("sector") or item.get("industry") or "未分类"),
            "price": _round(_number(item.get("price") or item.get("close_price")), 2),
            "change_pct": _round(_number(item.get("change_pct")), 2),
            "score": _round(_number(item.get("score") or item.get("total_score")), 1),
            "confidence_pct": None,
            "score_breakdown": item.get("score_breakdown") or {},
            "score_method": str(strategy.get("version") or "overnight-strategy"),
            "strategy": str(run.get("strategy_name") or "14:55尾盘候选策略"),
            "pool": "14:55执行池" if eligible_run else "尾盘观察池",
            "status": "待竞价确认" if eligible_run else "观察",
            "execution_eligible": eligible_run,
            "stale": not same_date,
            "data_date": run.get("data_date"),
            "why_selected": passed[:4] or [str(item.get("basis") or "通过尾盘策略结构筛选")],
            "why_not_full": failed[:4] or ["仍需次日竞价规则确认"],
            "abandon_conditions": ["竞价量比或高开幅度不达标", "板块竞价转弱", "数据时效失效"],
            "source": "overnight_entry_run",
            "source_run_id": run.get("id"),
        })
    return output


def _merge_candidates(groups: list[tuple[int, list[dict]]]) -> list[dict]:
    by_code: dict[str, tuple[int, dict]] = {}
    for priority, rows in groups:
        for row in rows:
            code = str(row.get("code") or "")
            if code and (code not in by_code or priority > by_code[code][0]):
                by_code[code] = (priority, row)
    output = [row for _, row in by_code.values()]
    output.sort(
        key=lambda item: (
            bool(item.get("execution_eligible")),
            not bool(item.get("stale")),
            _number(item.get("score")) or -1,
        ),
        reverse=True,
    )
    return output[:12]


def _strategy_selector(market_state: dict, overnight: dict) -> dict:
    score = _number(market_state.get("score"))
    loss_alert = overnight.get("loss_alert") or {}
    if score is None:
        max_position, conclusion = 0, "等待数据"
    elif score >= 80:
        max_position, conclusion = 50, "执行"
    elif score >= 65:
        max_position, conclusion = 40, "执行"
    elif score >= 50:
        max_position, conclusion = 30, "谨慎"
    elif score >= 35:
        max_position, conclusion = 15, "观察"
    else:
        max_position, conclusion = 0, "不执行"
    risk_note = str(loss_alert.get("reason") or "") if loss_alert.get("warning") else ""
    overnight_status = (
        "allowed" if score is not None and score >= 65
        else "limited" if score is not None and score >= 35
        else "forbidden"
    )
    if loss_alert.get("warning") and overnight_status == "allowed":
        overnight_status = "limited"
    rows = [
        {
            "id": "trend_breakout",
            "name": "趋势突破",
            "status": "allowed" if score is not None and score >= 65 else "limited" if score is not None and score >= 50 else "forbidden",
            "priority": 1 if score is not None and score >= 65 else 3,
            "max_position_pct": max_position,
            "reason": "市场趋势与宽度允许顺势研究" if score is not None and score >= 65 else "市场状态尚未支持高频趋势执行",
            "href": "/pro/screener",
        },
        {
            "id": "tail_1455",
            "name": str((overnight.get("strategy") or {}).get("name") or "14:55尾盘候选策略"),
            "status": overnight_status,
            "priority": 1 if overnight_status == "allowed" else 2,
            "max_position_pct": min(max_position, 30),
            "reason": risk_note or (
                "需完成14:55结构确认，不能提前按缓存建立信号"
                if overnight_status != "forbidden" else "当前市场评分不支持尾盘高风险策略"
            ),
            "href": "/quant",
        },
        {
            "id": "auction_confirmation",
            "name": "次日竞价确认",
            "status": "allowed" if score is not None and score >= 50 else "limited" if score is not None and score >= 35 else "forbidden",
            "priority": 1,
            "max_position_pct": min(max_position, 30),
            "reason": "只处理前一交易日已进入尾盘队列的候选，并由规则判定确认或放弃",
            "href": "/quant",
        },
        {
            "id": "high_chase",
            "name": "高位追涨",
            "status": "limited" if score is not None and score >= 80 else "forbidden",
            "priority": 4,
            "max_position_pct": min(max_position, 10),
            "reason": "高位拥挤和炸板风险需要额外限制",
            "href": "/pro/topic-strength",
        },
        {
            "id": "countertrend_bottom",
            "name": "逆势抄底",
            "status": "forbidden" if score is None or score < 50 else "limited",
            "priority": 5,
            "max_position_pct": min(max_position, 10),
            "reason": "未形成独立反转证据前不把下跌误判为低估",
            "href": "/quant",
        },
    ]
    return {
        "conclusion": conclusion,
        "max_total_position_pct": max_position,
        "strategies": rows,
        "allowed": [item["name"] for item in rows if item["status"] == "allowed"],
        "limited": [item["name"] for item in rows if item["status"] == "limited"],
        "forbidden": [item["name"] for item in rows if item["status"] == "forbidden"],
        "loss_alert": loss_alert,
        "policy": "市场状态决定策略许可；连续亏损只提醒和降级，不阻断行情观察。",
    }


def _phase(run: dict | None, phase_id: str, label: str, scheduled_at: str) -> dict:
    row = run or {}
    status = str(row.get("status") or "not_run")
    count = _integer(row.get("qualified_count")) or len(row.get("candidates") or [])
    if status in {"queued", "running"}:
        display = "运行中"
    elif status in {"completed", "partial"} and count:
        display = "有候选"
    elif status in {"completed", "partial"}:
        display = "无信号"
    elif status == "failed":
        display = "失败"
    elif status == "unavailable":
        display = "等待窗口"
    else:
        display = "尚未运行"
    return {
        "id": phase_id,
        "label": label,
        "scheduled_at": scheduled_at,
        "status": status,
        "display_status": display,
        "data_date": row.get("data_date"),
        "candidate_count": count,
        "message": str(row.get("message") or "等待对应交易窗口"),
        "run_id": row.get("id"),
    }


def _execution_queue(overnight: dict) -> dict:
    phases = [
        _phase(overnight.get("latest_preliminary_run"), "preliminary", "全市场预扫描", "14:30"),
        _phase(overnight.get("latest_entry_run"), "tail", "尾盘结构确认", "14:55"),
        _phase(overnight.get("latest_auction_run"), "auction", "AI竞价盯盘", "次日09:24-09:27"),
    ]
    auction = phases[-1]
    phases.append({
        "id": "decision",
        "label": "模拟执行决策",
        "scheduled_at": "次日09:30",
        "status": "ready" if auction["display_status"] == "有候选" else "waiting",
        "display_status": "买入/持有" if auction["display_status"] == "有候选" else "等待确认",
        "data_date": auction["data_date"],
        "candidate_count": auction["candidate_count"],
        "message": "只有规则确认后的候选进入100股模拟记录；不连接券商。",
        "run_id": auction["run_id"],
    })
    return {
        "phases": phases,
        "schedule": "14:30预扫描 → 14:55尾盘确认 → 次日09:24-09:27竞价盯盘 → 确认/放弃",
        "execution_mode": "研究用100股模拟成交，不连接券商",
    }


def assemble_workbench(
    topic_snapshot: dict,
    index_history: list[dict],
    sentiment_history: list[dict],
    selection: dict | None,
    overnight: dict,
    *,
    calculated_at: str | None = None,
) -> dict:
    decision_date = str(topic_snapshot.get("data_date") or "")[:10]
    market_state = calculate_market_state(
        topic_snapshot,
        index_history,
        sentiment_history,
        overnight.get("loss_alert") or {},
    )
    main_lines = _main_lines(topic_snapshot)
    topic_candidates = [
        candidate
        for topic in (topic_snapshot.get("topics") or [])[:8]
        if (candidate := _topic_candidate(topic, market_state, decision_date)) is not None
    ]
    selection_candidates = _selection_candidates(
        selection,
        decision_date,
        market_state,
        main_lines,
    )
    overnight_candidates = _overnight_candidates(overnight, decision_date)
    candidates = _merge_candidates([
        (4, [item for item in overnight_candidates if not item["stale"]]),
        (3, [item for item in selection_candidates if not item["stale"]]),
        (2, topic_candidates),
        (1, [item for item in overnight_candidates if item["stale"]]),
        (1, [item for item in selection_candidates if item["stale"]]),
    ])
    strategy_selector = _strategy_selector(market_state, overnight)
    market = topic_snapshot.get("market") or {}
    sentiment = market.get("sentiment") or {}
    emotion = market.get("emotion") or {}
    liquidity = market.get("liquidity") or {}
    dimension_by_id = {
        item["id"]: item for item in market_state.get("dimensions") or []
    }
    sentiment_temperature = _average([
        _number((dimension_by_id.get("breadth") or {}).get("score")),
        _number((dimension_by_id.get("emotion") or {}).get("score")),
    ])
    up = _integer(sentiment.get("up"))
    down = _integer(sentiment.get("down"))
    up_down_ratio = up / down if up is not None and down not in (None, 0) else None

    observed_dimensions = [
        item for item in market_state.get("dimensions") or [] if item.get("observed")
    ]
    strongest = sorted(
        observed_dimensions,
        key=lambda item: _number(item.get("score")) or -1,
        reverse=True,
    )
    weakest = sorted(
        observed_dimensions,
        key=lambda item: _number(item.get("score")) or 101,
    )
    key_evidence = _unique([
        str(evidence)
        for dimension in strongest[:3]
        for evidence in (dimension.get("evidence") or [])[:1]
    ])
    risk_warnings = _unique([
        str(item)
        for dimension in weakest[:2]
        for item in (dimension.get("evidence") or [])[:1]
        if item
    ])
    loss_alert = overnight.get("loss_alert") or {}
    if loss_alert.get("warning"):
        risk_warnings.append(str(loss_alert.get("reason") or "模拟交易连续亏损提醒"))
    risk_dimension = dimension_by_id.get("risk") or {}
    market_risk = risk_warnings[:4]
    if not market_risk:
        market_risk = (
            ["未触发已观测的高风险阈值"]
            if risk_dimension.get("observed")
            else ["市场风险字段待采集，当前不可判定"]
        )
    stock_risk = _unique([
        flag for item in main_lines[:5] for flag in item.get("risk_flags") or []
    ])[:5]
    if not stock_risk:
        stock_risk = (
            ["未触发已观测个股风险"]
            if main_lines
            else ["个股风险需在候选生成后判定"]
        )

    component_dates = {
        "topic_strength": decision_date or None,
        "index_history": ((dimension_by_id.get("trend") or {}).get("metrics") or {}).get("data_date"),
        "stock_selection": str((selection or {}).get("data_date") or "")[:10] or None,
        "overnight_entry": str((overnight.get("latest_entry_run") or {}).get("data_date") or "")[:10] or None,
        "auction": str((overnight.get("latest_auction_run") or {}).get("data_date") or "")[:10] or None,
    }
    stale_components = [
        key for key, value in component_dates.items()
        if value and decision_date and value != decision_date and key != "auction"
    ]
    missing_fields = [
        str(item)
        for item in (topic_snapshot.get("data_quality") or {}).get("missing_fields") or []
    ]
    missing_fields.extend(
        f"{item['label']}评分"
        for item in market_state.get("dimensions") or []
        if not item.get("observed")
    )
    available = bool(decision_date and market_state.get("dimensions"))
    now = calculated_at or shanghai_now().isoformat()
    return {
        "available": available,
        "meta": {
            "decision_date": decision_date or None,
            "calculated_at": now,
            "updated_at": topic_snapshot.get("updated_at") or now,
            "is_realtime": bool(topic_snapshot.get("is_realtime")),
            "cache_used": bool(topic_snapshot.get("cache_hit")),
            "source": topic_snapshot.get("source") or "unavailable",
            "coverage_pct": market_state.get("coverage_pct"),
            "confidence_pct": market_state.get("confidence_pct"),
            "decision_scope": "当日执行" if topic_snapshot.get("is_realtime") else "最近完整交易日复盘/下一交易日准备",
        },
        "market_state": market_state,
        "headline_metrics": {
            "sentiment_temperature": _round(sentiment_temperature, 1),
            "market_amount": _integer(liquidity.get("market_amount")),
            "up_count": up,
            "down_count": down,
            "up_down_ratio": _round(up_down_ratio, 2),
            "limit_up": _integer(emotion.get("zt_count")),
            "limit_down": _integer(emotion.get("dt_count")),
            "failed_limit_rate": _round(_number(emotion.get("break_rate")), 2),
            "main_line": main_lines[0]["name"] if main_lines else None,
        },
        "ai_judgement": {
            "market_summary": (
                f"当前市场状态为{market_state.get('state_label')}，评分 "
                f"{market_state.get('score') if market_state.get('score') is not None else '--'}，"
                f"执行等级为{market_state.get('execution_level')}。"
            ),
            "key_evidence": key_evidence or ["当前结构化证据不足，暂不形成方向判断"],
            "dominant_sectors": [item["name"] for item in main_lines[:3]],
            "preferred_strategies": strategy_selector["allowed"],
            "avoid_conditions": _unique(risk_warnings) or ["市场状态或板块结构恶化时降低执行级别"],
            "conclusion": strategy_selector["conclusion"],
            "confidence_pct": market_state.get("confidence_pct"),
            "generated_by": "evidence_bound_rule_explainer",
            "note": "解释层只复述结构化评分，不生成行情、不改变量化结果。",
        },
        "strategy_selector": strategy_selector,
        "main_lines": main_lines,
        "candidates": candidates,
        "candidate_summary": {
            "total": len(candidates),
            "execution_ready": sum(bool(item.get("execution_eligible")) for item in candidates),
            "same_day_observation": sum(
                not item.get("stale") and not item.get("execution_eligible")
                for item in candidates
            ),
            "historical_observation": sum(bool(item.get("stale")) for item in candidates),
            "rule": "只有数据日等于决策日且完成对应策略规则的候选才能进入执行队列。",
        },
        "execution_queue": _execution_queue(overnight),
        "risk": {
            "market": market_risk,
            "strategy": [str(loss_alert.get("reason"))] if loss_alert.get("warning") else [],
            "stock": stock_risk,
            "reminder_only": True,
            "disclaimer": "工作台用于研究、模拟与复盘，不连接券商，不构成投资建议。",
        },
        "audit": {
            "component_dates": component_dates,
            "stale_components": stale_components,
            "missing_fields": _unique(missing_fields),
            "score_version": SCORE_VERSION,
            "candidate_score_version": CANDIDATE_SCORE_VERSION,
            "data_sources": _unique([
                str(topic_snapshot.get("source") or ""),
                "tencent_index_history" if index_history else "",
                str((selection or {}).get("source") or ""),
                str((overnight.get("quote") or {}).get("source") or ""),
            ]),
            "same_day_rule": "跨日组件只作上一可用值或历史观察，不计入当日候选执行资格。",
            "no_future_data": True,
            "missing_policy": "缺失数据保持为空并列入审计，不填造、不按默认50分通过。",
        },
        "quick_links": [
            {"label": "题材强弱", "href": "/pro/topic-strength"},
            {"label": "智能选股", "href": "/pro/stock-picker"},
            {"label": "量化策略", "href": "/quant"},
            {"label": "个人投资池", "href": "/pro/personal"},
            {"label": "交易复盘", "href": "/pro/research"},
        ],
    }


class MarketDecisionWorkbenchService:
    _LIVE_CACHE_SECONDS = 60

    @staticmethod
    async def _safe(awaitable: Awaitable[Any], fallback: Any, timeout: float) -> Any:
        try:
            return await asyncio.wait_for(awaitable, timeout=timeout)
        except Exception as exc:
            print(f"Workbench component failed: {type(exc).__name__}")
            return fallback

    @staticmethod
    async def _read_cache() -> dict | None:
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, WORKBENCH_CACHE_KEY)
            return dict(row.payload) if row and isinstance(row.payload, dict) else None
        except Exception:
            return None

    @staticmethod
    def _cache_contract_valid(payload: dict | None) -> bool:
        if not payload or not payload.get("available"):
            return False
        meta = payload.get("meta") or {}
        state = payload.get("market_state") or {}
        audit = payload.get("audit") or {}
        coverage = _number(meta.get("coverage_pct")) or 0.0
        if audit.get("score_version") != SCORE_VERSION:
            return False
        if coverage < 40 and state.get("score") is not None:
            return False
        return bool(meta.get("decision_date") and isinstance(state.get("dimensions"), list))

    @staticmethod
    async def _write_cache(payload: dict) -> None:
        decision_date = str((payload.get("meta") or {}).get("decision_date") or "")
        keys = [WORKBENCH_CACHE_KEY]
        if decision_date:
            keys.append(f"{WORKBENCH_CACHE_PREFIX}{decision_date}")
        try:
            async with async_session() as session:
                for key in keys:
                    row = await session.get(MarketDataCache, key)
                    if row is None:
                        session.add(MarketDataCache(key=key, payload=payload))
                    else:
                        row.payload = payload
                await session.commit()
        except Exception as exc:
            print(f"Workbench cache save failed: {type(exc).__name__}")

    @staticmethod
    def _cache_fresh(payload: dict, now: datetime, seconds: int) -> bool:
        raw = (payload.get("meta") or {}).get("calculated_at")
        try:
            saved = datetime.fromisoformat(str(raw))
            if saved.tzinfo and now.tzinfo:
                return (now - saved).total_seconds() <= seconds
            return (
                now.replace(tzinfo=None) - saved.replace(tzinfo=None)
            ).total_seconds() <= seconds
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _prefer_cached(candidate: dict, cached: dict | None) -> bool:
        if not MarketDecisionWorkbenchService._cache_contract_valid(cached):
            return False
        candidate_meta = candidate.get("meta") or {}
        cached_meta = cached.get("meta") or {}
        candidate_date = _date(candidate_meta.get("decision_date"))
        cached_date = _date(cached_meta.get("decision_date"))
        candidate_coverage = _number(candidate_meta.get("coverage_pct")) or 0.0
        cached_coverage = _number(cached_meta.get("coverage_pct")) or 0.0
        if cached_date and (candidate_date is None or candidate_date < cached_date):
            return True
        if cached_date == candidate_date and cached_coverage > candidate_coverage:
            return True
        return bool(
            candidate_coverage < 40
            and cached_coverage >= 40
            and (candidate_date is None or cached_date is None or cached_date <= candidate_date)
        )

    @staticmethod
    def _retained_cache(cached: dict, candidate: dict) -> dict:
        result = dict(cached)
        meta = result.get("meta") or {}
        audit = result.get("audit") or {}
        candidate_meta = candidate.get("meta") or {}
        result["meta"] = {
            **meta,
            "cache_used": True,
            "refresh_status": "retained_verified_cache",
        }
        result["audit"] = {
            **audit,
            "refresh_warning": (
                "本次刷新覆盖率"
                f"{candidate_meta.get('coverage_pct', 0)}%，低于最近核验快照；"
                "系统未用不完整结果覆盖现有工作台。"
            ),
        }
        return result

    @staticmethod
    async def _load_selection() -> dict | None:
        try:
            async with async_session() as session:
                row = (await session.execute(
                    select(StockSelectionRun)
                    .order_by(desc(StockSelectionRun.created_at))
                    .limit(1)
                )).scalar_one_or_none()
            if row is None:
                return None
            return {
                "id": row.id,
                "data_date": row.data_date.isoformat() if row.data_date else None,
                "source": row.source,
                "is_realtime": bool(row.is_realtime),
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "result": dict(row.result) if isinstance(row.result, dict) else {},
            }
        except Exception as exc:
            print(f"Workbench selection load failed: {type(exc).__name__}")
            return None

    @staticmethod
    async def _load_sentiment_history() -> list[dict]:
        try:
            async with async_session() as session:
                rows = list((await session.execute(
                    select(MarketSentimentDaily)
                    .order_by(desc(MarketSentimentDaily.trade_date))
                    .limit(25)
                )).scalars().all())
            return [{
                "trade_date": row.trade_date.isoformat(),
                "market_amount": row.market_amount,
                "up_count": row.up_count,
                "down_count": row.down_count,
                "limit_up_count": row.limit_up_count,
                "limit_down_count": row.limit_down_count,
                "failed_limit_rate": row.failed_limit_rate,
            } for row in rows]
        except Exception as exc:
            print(f"Workbench sentiment history load failed: {type(exc).__name__}")
            return []

    async def get(self, *, force: bool = False) -> dict:
        now = shanghai_now()
        cached = await self._read_cache()
        if not self._cache_contract_valid(cached):
            cached = None
        active_window = (
            is_a_share_market_session(now)
            or (now.hour == 9 and 20 <= now.minute <= 29)
            or (now.hour == 14 and now.minute >= 20)
            or (now.hour == 15 and now.minute <= 5)
        )
        cache_seconds = self._LIVE_CACHE_SECONDS if active_window else 300
        if cached and not force and self._cache_fresh(cached, now, cache_seconds):
            result = dict(cached)
            result["meta"] = {**(result.get("meta") or {}), "cache_used": True}
            return result

        topic, index_history, overnight, selection, sentiment_history = await asyncio.gather(
            self._safe(topic_strength_service.get(force=force and active_window), {}, 18),
            self._safe(collector.fetch_shanghai_index_history(120), [], 12),
            self._safe(overnight_strategy_service.dashboard(), {}, 15),
            self._load_selection(),
            self._load_sentiment_history(),
        )
        topic_date = str(topic.get("data_date") or "")
        if not topic_date:
            return cached or assemble_workbench(
                {}, index_history, sentiment_history, selection, overnight,
            )
        payload = assemble_workbench(
            topic, index_history, sentiment_history, selection, overnight,
        )
        if self._prefer_cached(payload, cached):
            return self._retained_cache(cached, payload)
        if payload.get("available"):
            await self._write_cache(payload)
            return payload
        return cached or payload


market_decision_workbench_service = MarketDecisionWorkbenchService()
