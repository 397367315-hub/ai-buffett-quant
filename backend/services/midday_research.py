"""Evidence-bound intraday tactical research for the A-share lunch break.

The service turns the 11:35 complete-market snapshot into a persistent report,
tracks the same candidate set through the afternoon, and validates the report
against the 14:55 strategy run and closing market structure.  It never places
orders and never treats a missing field as a passed condition.
"""

from __future__ import annotations

import asyncio
import json
import math
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from typing import Any

from sqlalchemy import desc, func, select

from database import async_session
from models import ResearchHypothesis, ResearchSession, StockDailyBar
from quant.indicators import normalize_snapshot_stock
from quant.market_cache import load_quant_market_snapshot, save_quant_market_snapshot
from services.ai_service import ai_service
from services.data_collector import collector, is_a_share_market_session, shanghai_now
from services.market_decision_workbench import market_decision_workbench_service
from services.overnight_strategy import STRATEGY_CONFIG, overnight_strategy_service
from services.topic_strength import topic_strength_service


MIDDAY_MODE = "midday"
WEEKEND_MODES = ("quick", "deep", "topic")
RESEARCH_VERSION = "midday-tactical-research-v1.0.0"
CONTRACT_VERSION = "midday-research-v1.0"
MARKET_DATA_VERSION = "complete-market-snapshot+workbench-v2"
STRATEGY_VERSION = "overnight-preview-v2"
PROMPT_VERSION = "midday-evidence-v1"
MODEL_VERSION = "structured-tactical-agents-v1"


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    parsed = _number(value)
    return int(parsed) if parsed is not None else None


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _shanghai_timestamp(value: datetime | None, now: datetime) -> datetime | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(now.tzinfo) if now.tzinfo is not None else aware


def _round(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _percent_text(value: float | None, digits: int = 2) -> str:
    return f"{value:.{digits}f}%" if value is not None and math.isfinite(value) else "未观测"


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return min(upper, max(lower, value))


def _scale(value: float | None, low: float, high: float) -> float | None:
    if value is None or high <= low:
        return None
    return _clamp((value - low) / (high - low) * 100)


def _average(values: list[float | None]) -> float | None:
    observed = [float(value) for value in values if value is not None]
    return sum(observed) / len(observed) if observed else None


def _weighted(values: list[tuple[float | None, float]]) -> float | None:
    observed = [(value, weight) for value, weight in values if value is not None and weight > 0]
    weight_sum = sum(weight for _, weight in observed)
    return sum(float(value) * weight for value, weight in observed) / weight_sum if weight_sum else None


def _unique(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))


def _phase(now: datetime) -> tuple[str, str]:
    if now.weekday() >= 5:
        return "HISTORICAL", "非交易日历史研究"
    minute = now.hour * 60 + now.minute
    if minute < 9 * 60 + 15:
        return "PREMARKET", "盘前历史研究"
    if minute < 11 * 60 + 30:
        return "MORNING", "上午盘中研究"
    if minute < 13 * 60:
        return "MIDDAY", "午间战术研究"
    if minute < 14 * 60 + 55:
        return "AFTERNOON", "午后跟踪"
    if minute < 15 * 60 + 5:
        return "TAIL", "14:55执行核验"
    return "CLOSE", "盘后验证"


def _normalise_stocks(snapshot: dict) -> list[dict]:
    output: list[dict] = []
    for raw in snapshot.get("stocks") or []:
        item = normalize_snapshot_stock(raw)
        code = str(item.get("code") or "")
        change = _number(item.get("change_pct"))
        price = _number(item.get("price"))
        if len(code) != 6 or not code.isdigit() or change is None or price is None or price <= 0:
            continue
        output.append({
            "code": code,
            "name": str(item.get("name") or code),
            "sector": str(item.get("sector") or "未分类"),
            "price": price,
            "change_pct": change,
            "volume_ratio": _number(item.get("vol_ratio")),
            "turnover": _number(item.get("turnover")),
            "amount": _integer(raw.get("amount")),
            "volume": _integer(raw.get("volume")),
            "market_cap_yi": _number(item.get("market_cap")),
            "main_net_inflow": _integer(raw.get("main_net_inflow")),
            "main_net_inflow_pct": _number(raw.get("main_net_inflow_pct")),
            "high": _number(raw.get("high")),
            "low": _number(raw.get("low")),
            "previous_close": _number(raw.get("previous_close")),
        })
    return output


def _fund_behaviour(stock: dict) -> dict:
    change = _number(stock.get("change_pct"))
    flow = _number(stock.get("main_net_inflow"))
    ratio = _number(stock.get("volume_ratio"))
    turnover = _number(stock.get("turnover"))
    if flow is not None and flow > 0 and change is not None and change > 0.5 and ratio is not None and ratio >= 1.2:
        state, label, text = "ACCUMULATION", "增量推动", "资金流入、价格上涨且量能放大，属于正向共振。"
    elif flow is not None and flow > 0 and change is not None and change <= 0.5:
        state, label, text = "ABSORPTION", "流入未涨", "资金流入但价格承压，需要观察上方抛压能否被消化。"
    elif flow is not None and flow < 0 and change is not None and change > 1:
        state, label, text = "EMOTION_DRIVEN", "价涨资流出", "上涨缺少主动资金配合，可能更多由情绪或板块Beta推动。"
    elif flow is not None and flow < 0 and change is not None and change < 0:
        state, label, text = "DISTRIBUTION", "流出共振", "价格与资金同步走弱，暂不把下跌解释成普通震荡。"
    elif change is not None and change > 0.8 and ratio is not None and ratio < 1:
        state, label, text = "LOW_VOLUME_RISE", "缩量上涨", "价格上涨但量比不足，持续性需要午后成交确认。"
    else:
        state, label, text = "NEUTRAL", "中性观察", "价格、资金和量能尚未形成一致方向。"
    if turnover is not None and turnover >= 25:
        text += " 换手偏高，需额外防范筹码松动。"
    return {"state": state, "label": label, "interpretation": text}


def _limit_threshold(code: str, name: str) -> float:
    if "ST" in name.upper():
        return 4.8
    if code.startswith(("4", "8", "92")):
        return 29.5
    if code.startswith(("300", "301", "302", "688", "689")):
        return 19.5
    return 9.5


def _sector_structures(stocks: list[dict], topic: dict) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for stock in stocks:
        if stock["sector"] != "未分类":
            grouped[stock["sector"]].append(stock)

    flow_rows = ((topic.get("market") or {}).get("top_sectors") or [])
    flow_map = {str(item.get("name") or ""): item for item in flow_rows}
    flow_rank = {str(item.get("name") or ""): index for index, item in enumerate(flow_rows, start=1)}
    member_flow: dict[str, int | None] = {}
    for name, members in grouped.items():
        if len(members) < 5:
            continue
        values = []
        for item in members:
            value = _number(item.get("main_net_inflow"))
            if value is not None:
                values.append(value)
        member_flow[name] = _integer(sum(values)) if values else None
    member_flow_rank = {
        name: index
        for index, (name, _) in enumerate(
            sorted(
                ((name, value) for name, value in member_flow.items() if value is not None),
                key=lambda item: item[1],
                reverse=True,
            ),
            start=1,
        )
    }
    output = []
    for name, members in grouped.items():
        if len(members) < 5:
            continue
        changes = [float(item["change_pct"]) for item in members]
        ordered = sorted(members, key=lambda item: (item["change_pct"], item.get("amount") or 0), reverse=True)
        positive = [item for item in members if item["change_pct"] > 0]
        breadth = len(positive) / len(members) * 100
        average_change = sum(changes) / len(changes)
        median_change = median(changes)
        leader = ordered[0]
        middle_pool = positive or members
        middle = max(middle_pool, key=lambda item: (item.get("market_cap_yi") or 0, item.get("amount") or 0))
        follow = next((item for item in ordered[1:] if item["code"] != middle["code"]), ordered[min(1, len(ordered) - 1)])
        rear_rows = sorted(members, key=lambda item: item["change_pct"])[:max(1, len(members) // 4)]
        rear = rear_rows[0]
        rear_average = sum(item["change_pct"] for item in rear_rows) / len(rear_rows)
        concentration_gap = leader["change_pct"] - median_change
        flow = flow_map.get(name)
        if flow is not None and _number(flow.get("main_net_inflow")) is not None:
            net_inflow = _integer(flow.get("main_net_inflow"))
            rank = flow_rank.get(name)
            flow_source = "行业资金榜"
        else:
            net_inflow = member_flow.get(name)
            rank = member_flow_rank.get(name) if net_inflow is not None else None
            flow_source = "板块成员主力资金汇总" if net_inflow is not None else "无可用资金样本"
        score = _weighted([
            (breadth, 0.35),
            (_scale(average_change, -2.5, 4.5), 0.25),
            (_scale(median_change, -2, 3.5), 0.2),
            (_scale(float(12 - rank), 0, 11) if rank is not None else None, 0.2),
        ])
        if score is not None and concentration_gap >= 6:
            score = max(0.0, score - 12)
        if average_change >= 1 and breadth >= 65 and median_change >= 0.5 and concentration_gap < 6:
            status, direction = "强化", "strengthening"
        elif average_change >= 1 and (breadth < 40 or concentration_gap >= 6):
            status, direction = "龙头抱团", "crowded"
        elif average_change > 0 and breadth >= 55:
            status, direction = "扩散", "broadening"
        elif average_change <= -1 or breadth <= 30:
            status, direction = "弱化", "weakening"
        else:
            status, direction = "分歧", "divergent"
        output.append({
            "name": name,
            "status": status,
            "direction": direction,
            "structure_score": _round(score),
            "member_count": len(members),
            "up_count": len(positive),
            "breadth_pct": _round(breadth),
            "average_change_pct": _round(average_change, 2),
            "median_change_pct": _round(median_change, 2),
            "leader_gap_pct": _round(concentration_gap, 2),
            "rear_average_pct": _round(rear_average, 2),
            "main_net_inflow": net_inflow,
            "flow_rank": rank,
            "flow_source": flow_source,
            "roles": [
                {"role": "先锋", "code": leader["code"], "name": leader["name"], "change_pct": _round(leader["change_pct"], 2)},
                {"role": "中军", "code": middle["code"], "name": middle["name"], "change_pct": _round(middle["change_pct"], 2)},
                {"role": "跟随", "code": follow["code"], "name": follow["name"], "change_pct": _round(follow["change_pct"], 2)},
                {"role": "后排", "code": rear["code"], "name": rear["name"], "change_pct": _round(rear["change_pct"], 2)},
            ],
            "evidence": [
                f"上涨宽度{breadth:.1f}%（{len(positive)}/{len(members)}）",
                f"板块均值{average_change:+.2f}%，中位数{median_change:+.2f}%",
                f"先锋相对中位数{concentration_gap:+.2f}%，后排均值{rear_average:+.2f}%",
                f"{flow_source}排名第{rank}" if rank is not None else "暂无可计算的板块资金样本",
            ],
            "risk_flags": _unique([
                "先锋与中位数差距过大，存在龙头抱团" if concentration_gap >= 6 else "",
                "上涨宽度不足40%" if breadth < 40 else "",
                "后排平均下跌，扩散尚未形成" if rear_average < 0 else "",
                "板块资金未进入前十" if rank is None or rank > 10 else "",
            ]),
        })
    return sorted(
        output,
        key=lambda item: (item.get("structure_score") is not None, item.get("structure_score") or -1),
        reverse=True,
    )[:10]


def _morning_autopsy(
    stocks: list[dict],
    workbench: dict,
    market_quote: dict,
    positions: dict[str, float],
    previous_report: dict | None,
    sectors: list[dict],
) -> dict:
    changes = [float(item["change_pct"]) for item in stocks]
    up = sum(value > 0 for value in changes)
    down = sum(value < 0 for value in changes)
    directional = up + down
    breadth = up / directional * 100 if directional else None
    market_median = median(changes) if changes else None
    limit_up = sum(
        item["change_pct"] >= _limit_threshold(item["code"], item["name"])
        for item in stocks
    )
    limit_down = sum(
        item["change_pct"] <= -_limit_threshold(item["code"], item["name"])
        for item in stocks
    )
    market_amount = sum(item.get("amount") or 0 for item in stocks) or _integer(
        (workbench.get("headline_metrics") or {}).get("market_amount")
    )
    previous_metrics = ((previous_report or {}).get("morning_autopsy") or {}).get("metrics") or {}
    previous_amount = _number(previous_metrics.get("market_amount"))
    amount_change = (
        (market_amount / previous_amount - 1) * 100
        if market_amount is not None and previous_amount not in (None, 0) else None
    )
    high_changes = [item["change_pct"] for item in stocks if positions.get(item["code"], 0.5) >= 0.8]
    low_changes = [item["change_pct"] for item in stocks if positions.get(item["code"], 0.5) <= 0.2]
    capped = [item for item in stocks if item.get("market_cap_yi") is not None and item["market_cap_yi"] > 0]
    capped.sort(key=lambda item: item["market_cap_yi"])
    quartile = max(1, len(capped) // 4)
    small_changes = [item["change_pct"] for item in capped[:quartile]]
    large_changes = [item["change_pct"] for item in capped[-quartile:]]
    index_change = _number(market_quote.get("sh_change_pct"))
    state = workbench.get("market_state") or {}
    health = _number((workbench.get("structure_health") or {}).get("score"))
    liquidity_dimension = next(
        (item for item in state.get("dimensions") or [] if item.get("id") == "liquidity"), {}
    )
    liquidity_score = _number(liquidity_dimension.get("score"))
    reinforced = sum(item.get("status") in {"强化", "扩散"} for item in sectors)
    attack = _average([_number(state.get("score")), breadth, _scale(float(reinforced), 0, 5)])
    risk_safety = next((item for item in state.get("dimensions") or [] if item.get("id") == "risk"), {})
    crowding = _number((workbench.get("crowding_risk") or {}).get("score"))
    risk = _average([
        100 - _number(risk_safety.get("score")) if _number(risk_safety.get("score")) is not None else None,
        crowding,
        _scale(_number((workbench.get("headline_metrics") or {}).get("failed_limit_rate")), 10, 40),
    ])
    if amount_change is not None:
        volume_support = "支持" if amount_change >= 5 else "不足" if amount_change <= -5 else "中性"
        volume_evidence = f"较前一午间同口径成交额{amount_change:+.1f}%"
        baseline = "前一午间全市场快照"
    elif liquidity_score is not None:
        volume_support = "未确认"
        volume_evidence = (
            f"尚无前一午间同口径基线；日级成交活跃分{liquidity_score:.1f}仅作背景，"
            "不用于午间同比判断"
        )
        baseline = "工作台历史成交基准（首日建立午间基线）"
    else:
        volume_support = "未确认"
        volume_evidence = "首日建立午间成交基线，量能方向不作猜测"
        baseline = "首日基线"
    if index_change is not None and index_change > 0.5 and (market_median or 0) <= 0:
        true_strength = "指数上涨但多数个股体感偏弱"
    elif index_change is not None and index_change < -0.3 and (market_median or 0) > 0:
        true_strength = "指数偏弱但市场内部仍有韧性"
    elif index_change is not None and index_change > 0 and (market_median or 0) > 0 and (breadth or 0) >= 55:
        true_strength = "指数与个股结构同步偏强"
    elif index_change is not None and index_change < 0 and (market_median or 0) < 0:
        true_strength = "指数与个股结构同步偏弱"
    else:
        true_strength = "指数与个股结构分化"
    strong = bool(
        (health or 0) >= 65
        and (breadth or 0) >= 55
        and (market_median or 0) > 0
        and volume_support in {"支持", "中性"}
    )
    return {
        "market_state": state.get("state_label") or "不可判定",
        "market_health": _round(health),
        "attack_intensity": _round(attack),
        "risk_level": _round(risk),
        "risk_label": "高" if (risk or 0) >= 70 else "中" if (risk or 0) >= 40 else "低",
        "truth_label": "真实偏强" if strong else "表面偏强，内部未确认" if index_change is not None and index_change > 0 else "内部偏弱或分化",
        "one_line": f"{true_strength}；量能{volume_support}；上涨宽度{breadth:.1f}%" if breadth is not None else f"{true_strength}；市场宽度未确认",
        "metrics": {
            "index_change_pct": _round(index_change, 2),
            "market_amount": market_amount,
            "previous_midday_amount": _integer(previous_amount),
            "amount_change_pct": _round(amount_change),
            "up_count": up,
            "down_count": down,
            "limit_up_count": limit_up,
            "limit_down_count": limit_down,
            "breadth_pct": _round(breadth),
            "market_median_pct": _round(market_median, 2),
            "high_position_count": len(high_changes),
            "high_position_avg_pct": _round(_average(high_changes), 2),
            "low_position_count": len(low_changes),
            "low_position_avg_pct": _round(_average(low_changes), 2),
            "large_cap_avg_pct": _round(_average(large_changes), 2),
            "small_cap_avg_pct": _round(_average(small_changes), 2),
            "volume_support": volume_support,
            "comparison_baseline": baseline,
        },
        "answers": [
            {"question": "上午成交是否支持当前涨跌？", "answer": volume_evidence, "nature": "FACT" if amount_change is not None else "INFERENCE"},
            {"question": "指数看起来强，还是市场真的强？", "answer": true_strength, "nature": "INFERENCE"},
            {"question": "涨停与跌停反馈如何？", "answer": f"涨停{limit_up}家，跌停{limit_down}家", "nature": "FACT"},
            {"question": "高位股与低位股谁更占优？", "answer": f"高位组均值{_percent_text(_average(high_changes))}，低位组均值{_percent_text(_average(low_changes))}", "nature": "FACT"},
            {"question": "权重与小盘风格如何？", "answer": f"大市值组均值{_percent_text(_average(large_changes))}，小市值组均值{_percent_text(_average(small_changes))}", "nature": "FACT"},
        ],
    }


def _principal_conflict(workbench: dict, autopsy: dict, previous_report: dict | None, strategic: dict | None) -> dict:
    cognition = workbench.get("market_cognition") or {}
    current = (cognition.get("principal_contradiction") or {}).get("statement") or "当前主要矛盾尚未形成稳定结论"
    previous_midday = ((previous_report or {}).get("principal_conflict") or {}).get("current_statement")
    strategic_statement = (((strategic or {}).get("report") or {}).get("conclusion") or {}).get("principal_conflict")
    baseline_statement = previous_midday or strategic_statement
    metrics = autopsy.get("metrics") or {}
    amount_change = _number(metrics.get("amount_change_pct"))
    breadth = _number(metrics.get("breadth_pct"))
    health = _number(autopsy.get("market_health"))
    conflict_text = str(current)
    liquidity_conflict = any(term in conflict_text for term in ("成交", "量能", "增量", "流动性"))
    if liquidity_conflict and amount_change is not None and amount_change >= 8 and (breadth or 0) >= 58 and (health or 0) >= 65:
        status, label = "RESOLVED", "主要矛盾阶段性解决"
    elif liquidity_conflict and ((amount_change is not None and amount_change <= -5) or (breadth is not None and breadth < 45)):
        status, label = "UNRESOLVED", "主要矛盾尚未解决"
    elif baseline_statement and current != baseline_statement and (health or 0) >= 65 and (breadth or 0) >= 55:
        status, label = "SHIFTING", "主要矛盾正在转换"
    elif baseline_statement and current == baseline_statement:
        status, label = "UNRESOLVED", "主要矛盾延续"
    elif (health or 0) < 45:
        status, label = "INTENSIFIED", "主要矛盾加剧"
    else:
        status, label = "OBSERVING", "主要矛盾等待午后验证"
    cognition_evidence = [
        str(item)
        for item in ((cognition.get("principal_contradiction") or {}).get("evidence") or [])
        if not any(
            marker in str(item)
            for marker in ("成交额较前5日基准", "待采集", "待补", "不可计算")
        )
    ]
    return {
        "current_statement": current,
        "previous_statement": baseline_statement,
        "baseline_type": "前一午间研究" if previous_midday else "最近周末战略研究" if strategic_statement else "当前市场工作台",
        "status": status,
        "status_label": label,
        "dominant_aspect": (cognition.get("dominant_aspect") or {}).get("statement"),
        "evidence": _unique([
            *cognition_evidence,
            f"午间成交同口径变化{amount_change:+.1f}%" if amount_change is not None else "首日建立午间成交基线",
            f"上涨宽度{breadth:.1f}%" if breadth is not None else "市场宽度未确认",
            f"结构健康度{health:.1f}" if health is not None else "结构健康度未确认",
        ])[:8],
        "validation": "午后观察成交扩张、板块扩散与高位负反馈是否同步变化。",
    }


def _stock_anomalies(stocks: list[dict], sectors: list[dict], positions: dict[str, float], market_median: float) -> dict:
    sector_rows = {item["name"]: item for item in sectors}
    sector_average = {item["name"]: _number(item.get("average_change_pct")) for item in sectors}
    buckets: dict[str, list[dict]] = {
        "contrarian_strength": [],
        "alpha_strengthening": [],
        "beta_weak": [],
        "high_position_negative_feedback": [],
    }
    for stock in stocks:
        change = stock["change_pct"]
        sector_avg = sector_average.get(stock["sector"])
        market_alpha = change - market_median
        sector_alpha = change - sector_avg if sector_avg is not None else None
        ratio = stock.get("volume_ratio")
        flow = stock.get("main_net_inflow")
        position = positions.get(stock["code"])
        behaviour = _fund_behaviour(stock)
        base = {
            **stock,
            "market_alpha_pct": _round(market_alpha, 2),
            "sector_alpha_pct": _round(sector_alpha, 2),
            "position_20d_pct": _round(position * 100, 1) if position is not None else None,
            "fund_behaviour": behaviour,
        }
        if (
            ((market_median < 0 and change >= 1) or (sector_avg is not None and sector_avg < 0 and change >= 1))
            and market_alpha >= 1.5
            and (sector_alpha is None or sector_alpha >= 1)
            and (ratio is None or ratio >= 1)
        ):
            buckets["contrarian_strength"].append({
                **base,
                "score": _round(_clamp(55 + market_alpha * 7 + max(sector_alpha or 0, 0) * 4 + max((ratio or 1) - 1, 0) * 10)),
                "reason": f"相对市场中位数{market_alpha:+.2f}%，相对板块{sector_alpha:+.2f}%" if sector_alpha is not None else f"相对市场中位数{market_alpha:+.2f}%",
            })
        if market_alpha >= 2 and (sector_alpha is None or sector_alpha >= 1) and ratio is not None and ratio >= 1.2:
            buckets["alpha_strengthening"].append({
                **base,
                "score": _round(_clamp(50 + market_alpha * 8 + max(sector_alpha or 0, 0) * 5 + (ratio - 1.2) * 8)),
                "reason": f"量比{ratio:.2f}，个股同时跑赢市场与板块",
            })
        sector_state = (sector_rows.get(stock["sector"]) or {}).get("status")
        if sector_avg is not None and sector_avg >= 1 and change >= 0 and sector_alpha is not None and sector_alpha <= -1.2:
            buckets["beta_weak"].append({
                **base,
                "score": _round(_clamp(55 + abs(sector_alpha) * 8 + (10 if flow is not None and flow < 0 else 0))),
                "reason": f"板块均值{sector_avg:+.2f}%，个股落后{abs(sector_alpha):.2f}%，属于Beta跟随而非自身强化",
            })
        if position is not None and position >= 0.8 and change <= -1.5 and (flow is None or flow < 0 or (ratio or 0) >= 1.2):
            buckets["high_position_negative_feedback"].append({
                **base,
                "score": _round(_clamp(55 + abs(change) * 7 + (position - 0.8) * 50)),
                "reason": f"近20日位置{position * 100:.1f}%，上午涨跌{change:+.2f}%{f'，板块{sector_state}' if sector_state else ''}",
            })
    counts = {key: len(value) for key, value in buckets.items()}
    for key in buckets:
        buckets[key] = sorted(buckets[key], key=lambda item: item.get("score") or 0, reverse=True)[:12]
    return {**buckets, "counts": counts}


def _fund_summary(stocks: list[dict]) -> dict:
    patterns = Counter()
    notable = []
    for stock in stocks:
        behaviour = _fund_behaviour(stock)
        patterns[behaviour["state"]] += 1
        if behaviour["state"] != "NEUTRAL" and (
            abs(stock["change_pct"]) >= 1 or abs(stock.get("main_net_inflow") or 0) >= 50_000_000
        ):
            notable.append({**stock, "fund_behaviour": behaviour})
    notable.sort(
        key=lambda item: (abs(item.get("main_net_inflow") or 0), abs(item.get("change_pct") or 0)),
        reverse=True,
    )
    labels = {
        "ACCUMULATION": "增量推动", "ABSORPTION": "流入未涨", "EMOTION_DRIVEN": "价涨资流出",
        "DISTRIBUTION": "流出共振", "LOW_VOLUME_RISE": "缩量上涨", "NEUTRAL": "中性观察",
    }
    return {
        "patterns": [
            {"state": key, "label": labels.get(key, key), "count": value}
            for key, value in patterns.most_common()
        ],
        "notable": notable[:10],
        "method": "资金方向必须与价格、量比、换手共同解释，单独净流入不作为买卖信号。",
    }


def _afternoon_scenarios(autopsy: dict, workbench: dict, sectors: list[dict]) -> list[dict]:
    metrics = autopsy.get("metrics") or {}
    breadth = _number(metrics.get("breadth_pct"))
    health = _number(autopsy.get("market_health"))
    attack = _number(autopsy.get("attack_intensity"))
    risk = _number(autopsy.get("risk_level"))
    amount_change = _number(metrics.get("amount_change_pct"))
    crowding = _number((workbench.get("crowding_risk") or {}).get("score"))
    reinforced = sum(item.get("status") in {"强化", "扩散"} for item in sectors[:6])
    crowded = sum(item.get("status") == "龙头抱团" for item in sectors[:6])
    attack_raw = _average([
        health, attack, breadth, _scale(amount_change, -10, 15), _scale(float(reinforced), 0, 4),
    ]) or 1
    range_raw = _average([
        100 - abs((health if health is not None else 50) - 50) * 1.3,
        100 - abs((breadth if breadth is not None else 50) - 50) * 1.5,
        _scale(float(crowded), 0, 3),
    ]) or 1
    pullback_raw = _average([
        risk,
        crowding,
        100 - breadth if breadth is not None else None,
        _scale(-amount_change if amount_change is not None else None, -10, 15),
    ]) or 1
    raw = [max(5.0, attack_raw), max(5.0, range_raw), max(5.0, pullback_raw)]
    total = sum(raw)
    supports = [round(value / total * 100, 1) for value in raw]
    supports[1] = round(100 - supports[0] - supports[2], 1)
    return [
        {
            "key": "ATTACK", "name": "A 继续进攻", "support_pct": supports[0], "nature": "FORECAST",
            "conditions": ["13:30后同口径成交继续放大", "核心板块从先锋扩散到中军和后排", "市场上涨宽度稳定在55%以上"],
            "watch": ["13:30成交额", "强化板块数量", "市场中位数"],
            "action": "只跟踪结构健康且Alpha为正的候选，等待14:55最终规则确认。",
            "falsification": ["成交增速转负", "板块宽度快速收缩", "高位负反馈扩大"],
        },
        {
            "key": "RANGE", "name": "B 震荡分歧", "support_pct": supports[1], "nature": "FORECAST",
            "conditions": ["成交没有持续扩张", "核心板块维持但后排不扩散", "市场中位数围绕零轴反复"],
            "watch": ["板块中位数", "龙头与后排差距", "缩量上涨数量"],
            "action": "降低候选优先级，只保留逆势与资金价格共振标的。",
            "falsification": ["成交和宽度同步突破", "核心板块全面转弱"],
        },
        {
            "key": "PULLBACK", "name": "C 冲高回落", "support_pct": supports[2], "nature": "FORECAST",
            "conditions": ["高位股出现连续负反馈", "核心板块资金与价格同步转弱", "市场宽度跌破40%"],
            "watch": ["高位负反馈", "炸板率", "板块资金方向"],
            "action": "停止提高风险暴露，14:55候选即使满足静态条件也需从严人工确认。",
            "falsification": ["高位股重新企稳", "市场中位数和成交同步修复"],
        },
    ]


def _research_chain(workbench: dict, midday_session_id: str | None = None) -> list[dict]:
    queue = {item.get("id"): item for item in (workbench.get("execution_queue") or {}).get("phases") or []}
    return [
        {"key": "weekend", "time": "周末", "label": "战略研究", "question": "下周应该研究什么？", "status": "available", "href": "/research"},
        {"key": "midday", "time": "11:42", "label": "战术研究", "question": "上午发生了什么，下午观察什么？", "status": "completed" if midday_session_id else "pending", "href": "/research/midday"},
        {"key": "tail", "time": "14:55", "label": "执行筛选", "question": "是否满足可执行条件？", "status": (queue.get("tail") or {}).get("display_status") or "等待", "href": "/quant"},
        {"key": "auction", "time": "次日09:25", "label": "最终确认", "question": "竞价量比与高开是否同时确认？", "status": (queue.get("auction") or {}).get("display_status") or "等待", "href": "/quant"},
        {"key": "review", "time": "盘后", "label": "结果复盘", "question": "判断对不对，为什么？", "status": "等待验证", "href": "/pro/research"},
    ]


def _hypotheses(report: dict, target: date) -> list[dict]:
    scenarios = report.get("afternoon_scenarios") or []
    selected = max(scenarios, key=lambda item: item.get("support_pct") or 0, default=None)
    output = []
    if selected:
        output.append({
            "key": f"afternoon_{selected['key'].lower()}",
            "scope": "market",
            "target": selected["key"],
            "title": f"下午情景：{selected['name']}",
            "statement": f"基于午间结构，{selected['name']}当前支持度最高；只有触发条件出现才视为成立。",
            "horizon": "T+0",
            "evidence": (report.get("principal_conflict") or {}).get("evidence") or [],
            "falsification": selected.get("falsification") or [],
            "due_date": target.isoformat(),
        })
    for item in ((report.get("tail_preview") or {}).get("candidates") or [])[:5]:
        output.append({
            "key": f"tail_{item['code']}",
            "scope": "stock",
            "target": item["code"],
            "title": f"{item['name']}能否保持至14:55",
            "statement": f"若午后结构不恶化，{item['name']}可能进入14:55正式候选；当前只是午间预演。",
            "horizon": "T+0",
            "evidence": item.get("passed_evidence") or [],
            "falsification": ["跌破午间关键结构", "量比回落至策略阈值以下", "14:55分钟条件不满足"],
            "due_date": target.isoformat(),
        })
    return output


def build_midday_report(
    *,
    workbench: dict,
    topic: dict,
    snapshot: dict,
    market_quote: dict,
    positions: dict[str, float],
    previous_report: dict | None,
    strategic: dict | None,
    tail_preview: dict,
    session_id: str | None = None,
) -> dict:
    stocks = _normalise_stocks(snapshot)
    sectors = _sector_structures(stocks, topic)
    autopsy = _morning_autopsy(stocks, workbench, market_quote, positions, previous_report, sectors)
    market_median = _number((autopsy.get("metrics") or {}).get("market_median_pct")) or 0.0
    anomalies = _stock_anomalies(stocks, sectors, positions, market_median)
    conflict = _principal_conflict(workbench, autopsy, previous_report, strategic)
    scenarios = _afternoon_scenarios(autopsy, workbench, sectors)
    now = shanghai_now()
    target = _as_date(snapshot.get("data_date")) or _as_date((workbench.get("meta") or {}).get("decision_date")) or now.date()
    phase_code, phase_label = _phase(now)
    if target != now.date():
        phase_code, phase_label = "HISTORICAL", "历史快照研究"
    position_coverage = len(positions) / len(stocks) * 100 if stocks else 0.0
    position_coverage_ok = position_coverage >= 80
    observed = [
        bool(stocks), bool(workbench.get("available")), bool(market_quote), position_coverage_ok,
        bool(sectors), bool(topic), bool(tail_preview.get("strategy_id")), bool(previous_report),
    ]
    completeness = sum(observed) / len(observed) * 100
    report = {
        "meta": {
            "contract_version": CONTRACT_VERSION,
            "generated_at": now.isoformat(),
            "data_date": target.isoformat(),
            "phase": phase_code,
            "phase_label": phase_label,
            "is_realtime": bool(snapshot.get("is_realtime") and target == now.date() and is_a_share_market_session(now)),
            "source": "+".join(_unique([snapshot.get("source"), topic.get("source"), (workbench.get("meta") or {}).get("source")])),
            "stock_count": len(stocks),
        },
        "conclusion": {
            "market_state": autopsy.get("market_state"),
            "market_health": autopsy.get("market_health"),
            "attack_intensity": autopsy.get("attack_intensity"),
            "risk_level": autopsy.get("risk_level"),
            "risk_label": autopsy.get("risk_label"),
            "principal_conflict": conflict.get("current_statement"),
            "conflict_status": conflict.get("status_label"),
            "action": "条件观察" if phase_code in {"MIDDAY", "AFTERNOON"} else "历史复盘",
            "one_line": autopsy.get("one_line"),
        },
        "morning_autopsy": autopsy,
        "principal_conflict": conflict,
        "sector_structures": sectors,
        "stock_anomalies": anomalies,
        "fund_behaviour": _fund_summary(stocks),
        "afternoon_scenarios": scenarios,
        "tail_preview": tail_preview,
        "tracking": {"checkpoints": [], "policy": "固定跟踪午间候选，不以午后涨幅榜替换样本。"},
        "validation": {"completed": False, "status": "PENDING", "message": "等待14:55与收盘数据验证"},
        "research_chain": _research_chain(workbench, session_id),
        "data_quality": {
            "completeness_pct": _round(completeness),
            "complete_market_snapshot": bool(snapshot.get("complete") and stocks),
            "position_coverage_pct": _round(position_coverage),
            "same_time_midday_baseline": bool(previous_report),
            "missing_fields": _unique([
                "前一午间同口径快照（本次已建立基线）" if not previous_report else "",
                f"近20日位置覆盖不足（{len(positions)}/{len(stocks)}）" if not position_coverage_ok else "",
                "上证盘中指数" if not market_quote else "",
                "完整全市场快照" if not stocks else "",
            ]),
            "missing_policy": "缺失字段不按通过处理；首日基线明确标注，不用全日成交额伪装午间同比。",
        },
        "agent_runs": [
            {"agent": "MorningAutopsyAgent", "status": "completed", "output": "上午成交、宽度、中位数、位置与市值风格"},
            {"agent": "ConflictChangeAgent", "status": "completed", "output": "周末战略到午间主要矛盾变化"},
            {"agent": "SectorStructureAgent", "status": "completed", "output": f"{len(sectors)}个板块内部结构"},
            {"agent": "AlphaBetaAgent", "status": "completed", "output": "逆势、Alpha、Beta与高位负反馈"},
            {"agent": "CapitalBehaviourAgent", "status": "completed", "output": "资金+价格+量比+换手联合解释"},
            {"agent": "TailPreviewAgent", "status": "completed", "output": f"{tail_preview.get('candidate_count', 0)}只14:55预演候选"},
            {"agent": "ScenarioAgent", "status": "completed", "output": "下午三情景条件系统"},
            {"agent": "ValidationAgent", "status": "pending", "output": "等待14:55与盘后真实结果"},
        ],
        "ai_synthesis": {"available": False, "status": "pending", "narrative": None},
    }
    report["hypotheses"] = _hypotheses(report, target)
    return report


async def _ai_tactical_synthesis(report: dict) -> dict:
    payload = {
        "conclusion": report.get("conclusion"),
        "morning_autopsy": report.get("morning_autopsy"),
        "principal_conflict": report.get("principal_conflict"),
        "sector_structures": (report.get("sector_structures") or [])[:5],
        "stock_anomaly_counts": (report.get("stock_anomalies") or {}).get("counts"),
        "afternoon_scenarios": report.get("afternoon_scenarios"),
        "tail_preview": {
            "strategy": (report.get("tail_preview") or {}).get("strategy_name"),
            "candidate_count": (report.get("tail_preview") or {}).get("candidate_count"),
            "high_quality_count": (report.get("tail_preview") or {}).get("high_quality_count"),
        },
    }
    prompt = (
        "你是A股午间战术研究ReportAgent。只能解释输入结构化事实，不得新增行情、概率或股票，"
        "不得把支持度写成统计胜率，不得给出自动买卖指令。用中文输出四段：上午本质、主要矛盾、"
        "下午触发条件、14:55验证边界。总字数不超过420字。"
    )
    try:
        text = await asyncio.wait_for(
            ai_service.generate(json.dumps(payload, ensure_ascii=False, default=str)[:16000], system_prompt=prompt),
            timeout=20,
        )
    except Exception as exc:
        return {"available": False, "status": type(exc).__name__, "narrative": None}
    if not text or text.startswith("[AI服务"):
        return {"available": False, "status": "provider_unavailable", "narrative": None}
    return {
        "available": True,
        "status": "completed",
        "narrative": text.strip(),
        "guard": "AI只解释结构化报告，不修改评分、支持度、候选和执行规则",
    }


def _session_view(row: ResearchSession, *, include_report: bool = True) -> dict:
    report = dict(row.report) if isinstance(row.report, dict) else {}
    payload = {
        "id": row.id,
        "mode": row.mode,
        "status": row.status,
        "stage": row.stage,
        "progress": row.progress,
        "as_of_date": row.as_of_date.isoformat() if row.as_of_date else None,
        "source_data_date": row.source_data_date.isoformat() if row.source_data_date else None,
        "error": row.error,
        "created_at": _utc_iso(row.created_at),
        "updated_at": _utc_iso(row.updated_at),
        "completed_at": _utc_iso(row.completed_at),
        "versions": {
            "market_data": row.market_data_version,
            "strategy": row.strategy_version,
            "model": row.model_version,
            "prompt": row.prompt_version,
            "research": row.research_version,
        },
    }
    if include_report:
        payload["report"] = report
    else:
        conclusion = report.get("conclusion") or {}
        validation = report.get("validation") or {}
        payload["summary"] = {
            "market_state": conclusion.get("market_state"),
            "principal_conflict": conclusion.get("principal_conflict"),
            "conflict_status": conclusion.get("conflict_status"),
            "candidate_count": (report.get("tail_preview") or {}).get("candidate_count", 0),
            "scenario": max(
                report.get("afternoon_scenarios") or [],
                key=lambda item: item.get("support_pct") or 0,
                default={},
            ).get("name"),
            "validation_status": validation.get("status"),
        }
    return payload


def _hypothesis_view(row: ResearchHypothesis) -> dict:
    return {
        "id": row.id,
        "key": row.hypothesis_key,
        "scope": row.scope,
        "target": row.target,
        "title": row.title,
        "statement": row.statement,
        "horizon": row.horizon,
        "evidence": list(row.evidence) if isinstance(row.evidence, list) else [],
        "falsification": list(row.falsification) if isinstance(row.falsification, list) else [],
        "due_date": row.due_date.isoformat() if row.due_date else None,
        "status": row.status,
        "actual_result": row.actual_result,
        "validation_result": row.validation_result,
    }


class MiddayResearchService:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    async def _update(self, session_id: str, **values: Any) -> None:
        async with async_session() as session:
            row = await session.get(ResearchSession, session_id)
            if row is None or row.mode != MIDDAY_MODE:
                return
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = datetime.utcnow()
            await session.commit()

    def _schedule(self, session_id: str, *, force: bool = False) -> None:
        current = self._tasks.get(session_id)
        if current and not current.done():
            return
        task = asyncio.create_task(self._run(session_id, force=force), name=f"midday-research-{session_id}")
        self._tasks[session_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(session_id, None))

    async def start(self, *, force: bool = False, background: bool = True) -> dict:
        now = shanghai_now()
        async with async_session() as session:
            active = (await session.execute(
                select(ResearchSession).where(
                    ResearchSession.mode == MIDDAY_MODE,
                    ResearchSession.status.in_(["DRAFT", "RUNNING"]),
                ).order_by(desc(ResearchSession.created_at)).limit(1)
            )).scalar_one_or_none()
            latest_today = (await session.execute(
                select(ResearchSession).where(
                    ResearchSession.mode == MIDDAY_MODE,
                    ResearchSession.as_of_date == now.date(),
                    ResearchSession.status.in_(["COMPLETED", "REVIEWING", "VALIDATING"]),
                ).order_by(desc(ResearchSession.created_at)).limit(1)
            )).scalar_one_or_none()
        if active:
            if background:
                self._schedule(active.id, force=force)
            else:
                await self._run(active.id, force=force)
            return (
                await self.get(active.id)
                if not background
                else _session_view(active, include_report=False)
            )
        if latest_today and not force:
            phase_code, _ = _phase(now)
            latest_local = _shanghai_timestamp(
                latest_today.completed_at or latest_today.created_at,
                now,
            )
            needs_intraday_refresh = (
                phase_code in {"MIDDAY", "AFTERNOON", "TAIL", "CLOSE"}
                and (
                    latest_local is None
                    or latest_local.date() != now.date()
                    or latest_local.time() < time(11, 30)
                )
            )
            if not needs_intraday_refresh:
                return _session_view(latest_today, include_report=False)
        session_id = f"mr_{uuid.uuid4().hex[:20]}"
        row = ResearchSession(
            id=session_id,
            mode=MIDDAY_MODE,
            topic=None,
            status="DRAFT",
            stage="等待午间研究任务",
            progress=0,
            as_of_date=now.date(),
            market_data_version=MARKET_DATA_VERSION,
            fundamental_data_version="cached-market-position-v1",
            strategy_version=STRATEGY_VERSION,
            model_version=MODEL_VERSION,
            prompt_version=PROMPT_VERSION,
            research_version=RESEARCH_VERSION,
            report={},
        )
        async with async_session() as session:
            session.add(row)
            await session.commit()
        if background:
            self._schedule(session_id, force=force)
            return _session_view(row, include_report=False)
        await self._run(session_id, force=force)
        return await self.get(session_id) or _session_view(row, include_report=False)

    async def resume_incomplete_runs(self) -> None:
        try:
            async with async_session() as session:
                rows = list((await session.execute(select(ResearchSession).where(
                    ResearchSession.mode == MIDDAY_MODE,
                    ResearchSession.status.in_(["DRAFT", "RUNNING"]),
                ))).scalars().all())
                for row in rows:
                    row.status = "DRAFT"
                    row.stage = "服务恢复后继续午间研究"
                    row.error = None
                await session.commit()
            for row in rows:
                self._schedule(row.id)
        except Exception as exc:
            print(f"Midday research resume failed: {type(exc).__name__}")

    @staticmethod
    async def _previous_report(session_id: str, target: date | None) -> dict | None:
        statement = select(ResearchSession).where(
            ResearchSession.mode == MIDDAY_MODE,
            ResearchSession.id != session_id,
            ResearchSession.status.in_(["COMPLETED", "REVIEWING", "VALIDATING"]),
        )
        if target:
            statement = statement.where(ResearchSession.source_data_date < target)
        statement = statement.order_by(desc(ResearchSession.source_data_date), desc(ResearchSession.created_at)).limit(1)
        async with async_session() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
        return dict(row.report) if row and isinstance(row.report, dict) else None

    @staticmethod
    async def _latest_strategic() -> dict | None:
        async with async_session() as session:
            row = (await session.execute(select(ResearchSession).where(
                ResearchSession.mode.in_(WEEKEND_MODES),
                ResearchSession.status.in_(["COMPLETED", "REVIEWING", "ARCHIVED"]),
            ).order_by(desc(ResearchSession.created_at)).limit(1))).scalar_one_or_none()
        return _session_view(row) if row else None

    @staticmethod
    async def _position_map_with_prices(target: date, stocks: list[dict]) -> dict[str, float]:
        codes = [item["code"] for item in stocks]
        if not codes:
            return {}
        cutoff = target - timedelta(days=35)
        try:
            async with async_session() as session:
                rows = list((await session.execute(select(
                    StockDailyBar.stock_code,
                    func.min(StockDailyBar.low_price),
                    func.max(StockDailyBar.high_price),
                ).where(
                    StockDailyBar.trade_date >= cutoff,
                    StockDailyBar.trade_date <= target,
                    StockDailyBar.stock_code.in_(codes),
                ).group_by(StockDailyBar.stock_code))).all())
        except Exception as exc:
            print(f"Midday position cache failed: {type(exc).__name__}")
            return {}
        prices = {item["code"]: item["price"] for item in stocks}
        output = {}
        for code, low, high in rows:
            price = _number(prices.get(str(code)))
            if price is not None and low is not None and high is not None and high > low:
                output[str(code)] = _clamp((price - float(low)) / (float(high) - float(low)), 0, 1)
        return output

    @staticmethod
    async def _tail_preview(snapshot: dict, target: date, dashboard: dict) -> dict:
        config = dict(dashboard.get("strategy") or STRATEGY_CONFIG)
        raw_stocks = list(snapshot.get("stocks") or [])
        prefiltered = overnight_strategy_service._prefilter(raw_stocks, config)
        codes = [str(item.get("code") or "") for item in prefiltered]
        bars_by_code = await overnight_strategy_service._daily_bars(codes, target)
        rows = []
        for stock in prefiltered:
            code = str(stock.get("code") or "")
            bars = bars_by_code.get(code, [])
            closes = [value for item in bars for value in [_number(item.get("close"))] if value is not None and value > 0]
            price = _number(stock.get("price"))
            ma = {
                period: overnight_strategy_service._moving_average(closes, price, period) if price is not None else None
                for period in (5, 10, 20, 30)
            }
            ma_available = all(value is not None for value in ma.values())
            ma_order = bool(ma_available and ma[10] > ma[20] > ma[30])
            price_above = bool(
                ma_available and price is not None and price > ma[5]
                and (not config.get("require_price_above_ma10") or price > ma[10])
            )
            recent_volumes = [value for item in bars[-2:] for value in [_number(item.get("volume"))] if value is not None]
            current_volume = _number(stock.get("volume"))
            staircase = bool(len(recent_volumes) == 2 and current_volume is not None and recent_volumes[0] < recent_volumes[1] < current_volume)
            listing_ok = len(bars) >= int(config.get("minimum_listing_sessions") or 60)
            checks = [
                ("均线多头", ma_order if ma_available else None),
                ("价格站上MA5/MA10", price_above if ma_available else None),
                ("近3日台阶放量", staircase if config.get("require_volume_staircase") else True),
                ("上市交易日", listing_ok),
            ]
            failed = [label for label, value in checks if value is False]
            unavailable = [label for label, value in checks if value is None]
            passed = [label for label, value in checks if value is True]
            score = 55.0
            midpoint = sum(config.get("change_pct") or [3, 5]) / 2
            score += max(0, 15 - abs(float(stock.get("change_pct") or 0) - midpoint) * 6)
            score += min(12, max(0, (float(stock.get("vol_ratio") or 0) - float(config.get("volume_ratio_min") or 1.2)) * 10))
            score += 8 if ma_order else 0
            score += 6 if staircase else 0
            quality = "高质量" if not failed and not unavailable else "等待确认" if len(failed) + len(unavailable) <= 1 else "观察"
            if quality == "观察":
                continue
            rows.append({
                "code": code,
                "name": str(stock.get("name") or code),
                "sector": str(stock.get("sector") or "未分类"),
                "price": price,
                "change_pct": _round(_number(stock.get("change_pct")), 2),
                "volume_ratio": _round(_number(stock.get("vol_ratio")), 2),
                "turnover": _round(_number(stock.get("turnover")), 2),
                "market_cap_yi": _round(_number(stock.get("market_cap")), 1),
                "score": _round(_clamp(score)),
                "quality": quality,
                "passed_evidence": passed,
                "failed": failed,
                "unavailable": unavailable,
                "pending_confirmation": _unique([
                    "14:45-14:55分时位于均价线上方" if config.get("require_vwap_hold") else "",
                    "14:55新高回踩不破" if config.get("require_late_high_retest") else "",
                    "当日重大公告与财报窗口复核",
                    "14:55市场执行闸门",
                ]),
                "baseline": {
                    "change_pct": _round(_number(stock.get("change_pct")), 2),
                    "volume_ratio": _round(_number(stock.get("vol_ratio")), 2),
                    "main_net_inflow": _integer(stock.get("main_net_inflow")),
                },
            })
        rows.sort(key=lambda item: item.get("score") or 0, reverse=True)
        rows = rows[:12]
        return {
            "strategy_id": str(config.get("id") or STRATEGY_CONFIG["id"]),
            "strategy_name": str(config.get("name") or STRATEGY_CONFIG["name"]),
            "strategy_version": str(config.get("version") or ""),
            "preview_only": True,
            "data_date": target.isoformat(),
            "is_realtime": bool(snapshot.get("is_realtime") and target == shanghai_now().date()),
            "scanned_count": len(raw_stocks),
            "prefiltered_count": len(prefiltered),
            "candidate_count": len(rows),
            "high_quality_count": sum(item["quality"] == "高质量" for item in rows),
            "waiting_confirmation_count": sum(item["quality"] != "高质量" for item in rows),
            "candidates": rows,
            "rules": {
                "change_pct": config.get("change_pct"),
                "volume_ratio_min": config.get("volume_ratio_min"),
                "turnover_pct": config.get("turnover_pct"),
                "market_cap_yi": config.get("market_cap_yi"),
                "exclude_star_market": bool(config.get("exclude_star_market")),
                "exclude_chinext": bool(config.get("exclude_chinext")),
            },
            "boundary": "午间预演不建立模拟持仓；必须经过14:55分钟条件和市场执行闸门。",
        }

    async def _run(self, session_id: str, *, force: bool = False) -> None:
        try:
            await self._update(session_id, status="RUNNING", stage="读取上午全市场快照", progress=6, error=None)
            workbench = await asyncio.wait_for(market_decision_workbench_service.get(force=force), timeout=48)
            await self._update(session_id, stage="市场尸检与主要矛盾变化", progress=24)
            topic, snapshot, market_quote, dashboard = await asyncio.gather(
                topic_strength_service.get(force=False),
                load_quant_market_snapshot(),
                collector.fetch_market_turnover(),
                overnight_strategy_service.dashboard(),
                return_exceptions=True,
            )
            topic = {} if isinstance(topic, Exception) else topic
            snapshot = {} if isinstance(snapshot, Exception) else snapshot
            market_quote = {} if isinstance(market_quote, Exception) else market_quote
            dashboard = {} if isinstance(dashboard, Exception) else dashboard
            target = _as_date(snapshot.get("data_date")) or _as_date((workbench.get("meta") or {}).get("decision_date"))
            now = shanghai_now()
            minute = now.hour * 60 + now.minute
            intraday_refresh_window = now.weekday() < 5 and 9 * 60 + 15 <= minute <= 15 * 60 + 10
            should_refresh_snapshot = (
                not snapshot.get("stocks")
                or target is None
                or (target == now.date() and force)
                or (target is not None and target != now.date())
            )
            if should_refresh_snapshot and (
                is_a_share_market_session(now) or intraday_refresh_window
            ):
                fetched = await asyncio.wait_for(collector.fetch_quant_market_snapshot(include_special=True), timeout=35)
                if fetched.get("stocks"):
                    snapshot = fetched
                    target = _as_date(fetched.get("data_date")) or target
                    await save_quant_market_snapshot(fetched)
            if target is None:
                target = now.date()
            await self._update(session_id, stage="扫描板块内部强化与个股异常", progress=45, source_data_date=target)
            normalised = _normalise_stocks(snapshot)
            positions, previous_report, strategic = await asyncio.gather(
                self._position_map_with_prices(target, normalised),
                self._previous_report(session_id, target),
                self._latest_strategic(),
            )
            await self._update(session_id, stage="运行14:55策略预演", progress=68)
            tail_preview = await self._tail_preview(snapshot, target, dashboard)
            report = build_midday_report(
                workbench=workbench,
                topic=topic,
                snapshot=snapshot,
                market_quote=market_quote,
                positions=positions,
                previous_report=previous_report,
                strategic=strategic,
                tail_preview=tail_preview,
                session_id=session_id,
            )
            await self._update(session_id, stage="生成下午三情景与AI解读", progress=88)
            report["ai_synthesis"] = await _ai_tactical_synthesis(report)
            await self._persist(session_id, report, target)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Midday research run failed: {type(exc).__name__}: {exc}")
            await self._update(
                session_id,
                status="FAILED",
                stage="午间研究失败，可重新发起",
                error=f"{type(exc).__name__}: {str(exc)[:240]}",
            )

    async def _persist(self, session_id: str, report: dict, target: date) -> None:
        async with async_session() as session:
            row = await session.get(ResearchSession, session_id)
            if row is None or row.mode != MIDDAY_MODE:
                return
            for item in report.get("hypotheses") or []:
                existing = (await session.execute(select(ResearchHypothesis).where(
                    ResearchHypothesis.session_id == session_id,
                    ResearchHypothesis.hypothesis_key == item["key"],
                ))).scalar_one_or_none()
                values = {
                    "scope": item["scope"], "target": item.get("target"), "title": item["title"],
                    "statement": item["statement"], "nature": "FORECAST", "horizon": item.get("horizon") or "T+0",
                    "evidence": item.get("evidence") or [], "falsification": item.get("falsification") or [],
                    "due_date": _as_date(item.get("due_date")),
                }
                if existing:
                    for key, value in values.items():
                        setattr(existing, key, value)
                else:
                    session.add(ResearchHypothesis(session_id=session_id, hypothesis_key=item["key"], **values))
            row.report = report
            row.source_data_date = target
            row.status = "COMPLETED"
            row.stage = "午间研究完成，等待午后跟踪"
            row.progress = 100
            row.error = None
            row.completed_at = datetime.utcnow()
            row.updated_at = datetime.utcnow()
            await session.commit()

    async def get(self, session_id: str) -> dict | None:
        async with async_session() as session:
            row = await session.get(ResearchSession, session_id)
            if row is None or row.mode != MIDDAY_MODE:
                return None
            hypotheses = list((await session.execute(select(ResearchHypothesis).where(
                ResearchHypothesis.session_id == session_id,
            ).order_by(ResearchHypothesis.id))).scalars().all())
        payload = _session_view(row)
        payload["hypotheses"] = [_hypothesis_view(item) for item in hypotheses]
        return payload

    async def latest(self) -> dict | None:
        async with async_session() as session:
            row = (await session.execute(select(ResearchSession).where(
                ResearchSession.mode == MIDDAY_MODE,
            ).order_by(desc(ResearchSession.created_at)).limit(1))).scalar_one_or_none()
        return await self.get(row.id) if row else None

    async def list(self, *, limit: int = 30) -> list[dict]:
        async with async_session() as session:
            rows = list((await session.execute(select(ResearchSession).where(
                ResearchSession.mode == MIDDAY_MODE,
            ).order_by(desc(ResearchSession.created_at)).limit(max(1, min(limit, 100))))).scalars().all())
        return [_session_view(row, include_report=False) for row in rows]

    @staticmethod
    def _checkpoint_rows(report: dict, snapshot: dict) -> list[dict]:
        current = {item["code"]: item for item in _normalise_stocks(snapshot)}
        output = []
        for candidate in (report.get("tail_preview") or {}).get("candidates") or []:
            quote = current.get(str(candidate.get("code") or ""))
            if not quote:
                output.append({"code": candidate.get("code"), "name": candidate.get("name"), "status": "MISSING", "status_label": "行情缺失"})
                continue
            baseline = candidate.get("baseline") or {}
            change_delta = quote["change_pct"] - (_number(baseline.get("change_pct")) or 0)
            baseline_ratio = _number(baseline.get("volume_ratio"))
            ratio = _number(quote.get("volume_ratio"))
            flow = _number(quote.get("main_net_inflow"))
            if change_delta >= 0.8 and (baseline_ratio is None or ratio is None or ratio >= baseline_ratio):
                status, label = "STRENGTHENED", "强度增强"
            elif change_delta <= -1 or (flow is not None and flow < 0 and change_delta < -0.3):
                status, label = "WEAKENED", "午后失效"
            else:
                status, label = "HOLDING", "结构保持"
            output.append({
                "code": candidate.get("code"), "name": candidate.get("name"), "sector": candidate.get("sector"),
                "status": status, "status_label": label,
                "change_pct": _round(quote.get("change_pct"), 2), "change_delta_pct": _round(change_delta, 2),
                "volume_ratio": _round(ratio, 2), "main_net_inflow": _integer(flow),
            })
        return output

    async def track(self, checkpoint: str, *, session_id: str | None = None, force_quote: bool = True) -> dict:
        research = await self.get(session_id) if session_id else await self.latest()
        if not research:
            raise LookupError("尚无午间研究可跟踪")
        if research.get("status") != "COMPLETED":
            raise ValueError("午间研究尚未完成")
        target = _as_date(research.get("source_data_date"))
        if target != shanghai_now().date():
            raise ValueError("只有当日午间研究可以追加盘中跟踪")
        snapshot = {}
        if force_quote:
            try:
                snapshot = await asyncio.wait_for(collector.fetch_quant_market_snapshot(include_special=True), timeout=35)
                if snapshot.get("stocks"):
                    await save_quant_market_snapshot(snapshot)
            except Exception as exc:
                print(f"Midday checkpoint live quote failed: {type(exc).__name__}")
        if not snapshot.get("stocks"):
            snapshot = await load_quant_market_snapshot()
        if _as_date(snapshot.get("data_date")) != target:
            raise ValueError("当前行情日期与午间研究日期不一致")
        report = dict(research.get("report") or {})
        rows = self._checkpoint_rows(report, snapshot)
        now = shanghai_now()
        checkpoint_data = {
            "checkpoint": str(checkpoint or now.strftime("%H:%M")),
            "captured_at": now.isoformat(),
            "data_date": target.isoformat(),
            "is_realtime": bool(snapshot.get("is_realtime")),
            "strengthened_count": sum(item.get("status") == "STRENGTHENED" for item in rows),
            "holding_count": sum(item.get("status") == "HOLDING" for item in rows),
            "weakened_count": sum(item.get("status") == "WEAKENED" for item in rows),
            "stocks": rows,
        }
        tracking = dict(report.get("tracking") or {})
        checkpoints = [
            item for item in tracking.get("checkpoints") or []
            if item.get("checkpoint") != checkpoint_data["checkpoint"]
        ]
        checkpoints.append(checkpoint_data)
        tracking["checkpoints"] = checkpoints
        tracking["latest"] = checkpoint_data
        report["tracking"] = tracking
        await self._update(research["id"], report=report, stage=f"已跟踪至{checkpoint_data['checkpoint']}")
        return checkpoint_data

    async def validate_pending(self) -> list[str]:
        now = shanghai_now()
        async with async_session() as session:
            rows = list((await session.execute(select(ResearchSession).where(
                ResearchSession.mode == MIDDAY_MODE,
                ResearchSession.source_data_date == now.date(),
                ResearchSession.status == "COMPLETED",
            ))).scalars().all())
        pending = [row for row in rows if not ((row.report or {}).get("validation") or {}).get("completed")]
        if not pending:
            return []
        snapshot = {}
        try:
            snapshot = await asyncio.wait_for(collector.fetch_quant_market_snapshot(include_special=True), timeout=35)
        except Exception as exc:
            print(f"Midday close validation live quote failed: {type(exc).__name__}")
        if not snapshot.get("stocks"):
            snapshot = await load_quant_market_snapshot()
        stocks = _normalise_stocks(snapshot)
        if (
            _as_date(snapshot.get("data_date")) != now.date()
            or not snapshot.get("complete")
            or len(stocks) < 1000
        ):
            print("Midday close validation skipped: current complete-market snapshot unavailable")
            return []
        changes = [item["change_pct"] for item in stocks]
        current_median = median(changes) if changes else None
        breadth = sum(value > 0 for value in changes) / max(sum(value != 0 for value in changes), 1) * 100 if changes else None
        market_quote, dashboard = await asyncio.gather(
            collector.fetch_market_turnover(), overnight_strategy_service.dashboard(), return_exceptions=True,
        )
        market_quote = {} if isinstance(market_quote, Exception) else market_quote
        dashboard = {} if isinstance(dashboard, Exception) else dashboard
        entry = dashboard.get("latest_entry_run") or {}
        if _as_date(entry.get("data_date")) != now.date():
            print("Midday close validation skipped: current 14:55 strategy run unavailable")
            return []
        index_change = _number(market_quote.get("sh_change_pct"))
        if (breadth or 0) >= 55 and (current_median or 0) > 0 and (index_change is None or index_change >= 0):
            actual_scenario = "ATTACK"
        elif (breadth is not None and breadth <= 40) or (current_median is not None and current_median <= -1):
            actual_scenario = "PULLBACK"
        else:
            actual_scenario = "RANGE"
        actual_codes = {
            str(item.get("code") or "") for item in entry.get("candidates") or []
            if item.get("tail_qualified") or item.get("qualified")
        }
        updated = []
        for row in pending:
            report = dict(row.report or {})
            scenarios = report.get("afternoon_scenarios") or []
            predicted = max(scenarios, key=lambda item: item.get("support_pct") or 0, default={}).get("key")
            preview_codes = {str(item.get("code") or "") for item in (report.get("tail_preview") or {}).get("candidates") or []}
            hits = preview_codes & actual_codes
            scenario_result = "CORRECT" if predicted == actual_scenario else "PARTIAL" if predicted == "RANGE" or actual_scenario == "RANGE" else "WRONG"
            validation = {
                "completed": True,
                "status": scenario_result,
                "validated_at": now.isoformat(),
                "predicted_scenario": predicted,
                "actual_scenario": actual_scenario,
                "close_metrics": {"index_change_pct": _round(index_change, 2), "breadth_pct": _round(breadth), "market_median_pct": _round(current_median, 2)},
                "preview_candidate_count": len(preview_codes),
                "tail_candidate_count": len(actual_codes),
                "candidate_hits": sorted(hits),
                "preview_hit_rate_pct": _round(len(hits) / len(preview_codes) * 100, 1) if preview_codes else None,
                "message": f"下午实际情景{actual_scenario}；午间预演{len(preview_codes)}只，14:55命中{len(hits)}只。",
            }
            report["validation"] = validation
            async with async_session() as session:
                saved = await session.get(ResearchSession, row.id)
                if saved is None:
                    continue
                saved.report = report
                saved.stage = "盘后验证完成"
                saved.updated_at = datetime.utcnow()
                hypotheses = list((await session.execute(select(ResearchHypothesis).where(
                    ResearchHypothesis.session_id == row.id,
                    ResearchHypothesis.status == "PENDING",
                ))).scalars().all())
                for hypothesis in hypotheses:
                    if hypothesis.hypothesis_key.startswith("afternoon_"):
                        hypothesis.status = scenario_result
                        hypothesis.actual_result = f"收盘实际情景：{actual_scenario}"
                    elif hypothesis.hypothesis_key.startswith("tail_"):
                        hypothesis.status = "CORRECT" if str(hypothesis.target or "") in actual_codes else "WRONG"
                        hypothesis.actual_result = "进入14:55候选" if str(hypothesis.target or "") in actual_codes else "未进入14:55候选"
                    hypothesis.validation_result = hypothesis.status
                    hypothesis.validated_at = datetime.utcnow()
                await session.commit()
            updated.append(row.id)
        return updated

    async def framework(self) -> dict:
        latest = await self.latest()
        report = (latest or {}).get("report") or {}
        return {
            "layers": report.get("research_chain") or _research_chain({}),
            "latest_midday_session": latest.get("id") if latest else None,
            "data_date": latest.get("source_data_date") if latest else None,
            "validation": report.get("validation") or None,
        }


midday_research_service = MiddayResearchService()
