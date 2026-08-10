"""Multi-session sector-flow analysis with an optional data-grounded AI narrative."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

from sqlalchemy import desc, select

from database import async_session
from models import ConceptFundFlowDaily, IndustryFundFlowDaily, MarketBoard
from services.ai_service import ai_service
from services.data_collector import shanghai_now


FLOW_WINDOWS = {
    "week": {"label": "近一周", "sessions": 5},
    "two_weeks": {"label": "近两周", "sessions": 10},
    "month": {"label": "近一个月", "sessions": 20},
    "quarter": {"label": "近一个季度", "sessions": 60},
    "year": {"label": "近一年", "sessions": 250},
}


def _value(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


class FlowAnalysisService:
    async def _dataset(self, board_type: str, sessions: int) -> tuple[list, dict[str, str], list]:
        model = IndustryFundFlowDaily if board_type == "industry" else ConceptFundFlowDaily
        async with async_session() as session:
            dates = list((await session.execute(
                select(model.trade_date).distinct().order_by(desc(model.trade_date)).limit(sessions)
            )).scalars().all())
            rows = list((await session.execute(
                select(model).where(model.trade_date.in_(dates)).order_by(model.trade_date.asc())
            )).scalars().all()) if dates else []
            codes = list({row.board_code for row in rows})
            names = {
                row.code: row.name
                for row in (await session.execute(
                    select(MarketBoard).where(
                        MarketBoard.board_type == board_type,
                        MarketBoard.code.in_(codes),
                    )
                )).scalars().all()
            } if codes else {}
        return rows, names, sorted(dates)

    @staticmethod
    def _board_rows(rows: list, names: dict[str, str], dates: list) -> list[dict]:
        grouped: dict[str, list] = defaultdict(list)
        for row in rows:
            grouped[row.board_code].append(row)
        output = []
        latest_date = dates[-1] if dates else None
        for code, items in grouped.items():
            items.sort(key=lambda row: row.trade_date)
            flows = [_value(row.main_net_inflow) for row in items]
            changes = [_value(row.change_pct) for row in items]
            latest_row = next((row for row in reversed(items) if row.trade_date == latest_date), items[-1])
            previous = flows[:-1]
            split = max(1, len(flows) // 2)
            early_average = sum(flows[:split]) / max(split, 1)
            late_values = flows[split:] or flows[-1:]
            late_average = sum(late_values) / max(len(late_values), 1)
            total = sum(flows)
            positive_days = sum(flow > 0 for flow in flows)
            negative_days = sum(flow < 0 for flow in flows)
            output.append({
                "code": code,
                "name": names.get(code, code),
                "days": len(items),
                "total_inflow": int(round(total)),
                "average_inflow": int(round(total / max(len(items), 1))),
                "latest_inflow": int(round(_value(latest_row.main_net_inflow))),
                "positive_days": positive_days,
                "negative_days": negative_days,
                "positive_ratio_pct": round(positive_days / max(len(items), 1) * 100, 1),
                "average_change_pct": round(sum(changes) / max(len(changes), 1), 2),
                "trend_delta": int(round(late_average - early_average)),
                "previous_average": int(round(sum(previous) / max(len(previous), 1))) if previous else 0,
            })
        return output

    @staticmethod
    def _daily(rows: list, dates: list) -> list[dict]:
        grouped: dict[Any, list] = defaultdict(list)
        for row in rows:
            grouped[row.trade_date].append(row)
        return [{
            "date": target.isoformat(),
            "net_inflow": int(round(sum(_value(row.main_net_inflow) for row in grouped[target]))),
            "inflow_boards": sum(_value(row.main_net_inflow) > 0 for row in grouped[target]),
            "outflow_boards": sum(_value(row.main_net_inflow) < 0 for row in grouped[target]),
            "board_count": len(grouped[target]),
        } for target in dates]

    @staticmethod
    def _deterministic_summary(window: dict, board_type: str, boards: list[dict], daily: list[dict]) -> dict:
        label = "行业" if board_type == "industry" else "概念"
        top_inflows = sorted(boards, key=lambda item: item["total_inflow"], reverse=True)[:5]
        # A positive value is never an outflow.  Older snapshots occasionally
        # contained only the inflow ranking; keep that fact visible instead of
        # presenting the weakest positive board as money leaving the sector.
        negative_boards = [item for item in boards if item["total_inflow"] < 0]
        top_outflows = sorted(negative_boards, key=lambda item: item["total_inflow"])[:5]
        sustained_inflows = sorted(
            (item for item in boards if item["total_inflow"] > 0 and item["positive_ratio_pct"] >= 60),
            key=lambda item: (item["positive_ratio_pct"], item["total_inflow"]), reverse=True,
        )[:5]
        sustained_outflows = sorted(
            (item for item in boards if item["total_inflow"] < 0 and item["negative_days"] >= max(2, item["days"] * 0.6)),
            key=lambda item: item["total_inflow"],
        )[:5]
        turning_positive = sorted(
            (item for item in boards if item["latest_inflow"] > 0 and item["previous_average"] <= 0),
            key=lambda item: item["latest_inflow"], reverse=True,
        )[:5]
        turning_negative = sorted(
            (item for item in boards if item["latest_inflow"] < 0 and item["previous_average"] >= 0),
            key=lambda item: item["latest_inflow"],
        )[:5]

        aggregate = sum(item["net_inflow"] for item in daily)
        latest = daily[-1] if daily else {}
        latest_breadth = (
            latest.get("inflow_boards", 0) / max(latest.get("board_count", 0), 1) * 100
            if latest else 0
        )
        gross_positive = sum(max(item["total_inflow"], 0) for item in boards)
        top3_positive = sum(max(item["total_inflow"], 0) for item in top_inflows[:3])
        concentration = round(top3_positive / gross_positive * 100, 1) if gross_positive else 0.0
        score = 50.0
        score += max(-20.0, min(20.0, aggregate / 1e10 * 4))
        score += max(-15.0, min(15.0, (latest_breadth - 50) * 0.6))
        if sustained_inflows:
            score += min(10.0, len(sustained_inflows) * 2)
        if sustained_outflows:
            score -= min(10.0, len(sustained_outflows) * 2)
        score = round(max(0.0, min(100.0, score)), 1)
        tone = "偏强" if score >= 62 else "偏弱" if score <= 38 else "分化"
        leaders = "、".join(item["name"] for item in sustained_inflows[:3] or top_inflows[:3]) or "暂无稳定主线"
        headline = f"{window['label']}{label}资金{tone}，当前主线为{leaders}。"
        summary = (
            f"覆盖 {len(daily)}/{window['sessions']} 个缓存交易日；最新一日流入板块占比 {latest_breadth:.1f}%，"
            f"前3强流入集中度 {concentration:.1f}%。"
        )
        suggestions = []
        if sustained_inflows:
            suggestions.append(f"优先跟踪连续流入的{leaders}，等待价格趋势和成交额同步确认。")
        if turning_positive:
            suggestions.append(f"观察刚转强的{'、'.join(item['name'] for item in turning_positive[:3])}，至少再确认一个交易日。")
        if concentration >= 55:
            suggestions.append("资金集中度较高，主线以外板块的持续性可能较弱，避免只凭单日脉冲追涨。")
        if tone == "偏弱":
            suggestions.append("整体资金偏弱，控制总仓位并优先保留抗跌、持续流入方向。")
        risks = []
        if len(daily) < window["sessions"]:
            risks.append(f"历史缓存仅覆盖 {len(daily)} 个交易日，少于目标 {window['sessions']} 日。")
        if turning_negative:
            risks.append(f"{'、'.join(item['name'] for item in turning_negative[:3])}最新资金由正转负，需防范轮动退潮。")
        return {
            "score": score,
            "tone": tone,
            "headline": headline,
            "summary": summary,
            "latest_breadth_pct": round(latest_breadth, 1),
            "concentration_top3_pct": concentration,
            "aggregate_inflow": int(round(aggregate)),
            "top_inflows": top_inflows,
            "top_outflows": top_outflows,
            "outflow_data_available": bool(negative_boards),
            "sustained_inflows": sustained_inflows,
            "sustained_outflows": sustained_outflows,
            "turning_positive": turning_positive,
            "turning_negative": turning_negative,
            "suggestions": suggestions[:4],
            "risks": risks[:4],
        }

    async def _ai_narrative(self, payload: dict) -> str | None:
        if not ai_service.client:
            return None
        compact = {
            "window": payload["window"],
            "coverage": payload["coverage"],
            "headline": payload["analysis"]["headline"],
            "score": payload["analysis"]["score"],
            "top_inflows": [{"name": item["name"], "total": item["total_inflow"], "positive_days": item["positive_days"]} for item in payload["analysis"]["top_inflows"]],
            "top_outflows": [{"name": item["name"], "total": item["total_inflow"]} for item in payload["analysis"]["top_outflows"]],
            "turning_positive": [item["name"] for item in payload["analysis"]["turning_positive"]],
            "turning_negative": [item["name"] for item in payload["analysis"]["turning_negative"]],
        }
        prompt = (
            "请只依据下面JSON，用中文输出三段：资金主线、轮动变化、风险与观察建议。"
            "不预测必然涨跌，不新增JSON以外的数据，每段不超过80字。\n"
            + json.dumps(compact, ensure_ascii=False)
        )
        try:
            result = await asyncio.wait_for(
                ai_service.generate(prompt, "你是A股资金流审计分析师，结论必须可追溯到输入数据。"),
                timeout=15,
            )
        except Exception:
            return None
        return result if result and not result.startswith("[AI服务") else None

    async def analyze(self, board_type: str, window_key: str) -> dict:
        if board_type not in {"industry", "concept"}:
            raise ValueError("board_type 仅支持 industry 或 concept")
        if window_key not in FLOW_WINDOWS:
            raise ValueError("window 仅支持 week、two_weeks、month、quarter 或 year")
        window = FLOW_WINDOWS[window_key]
        rows, names, dates = await self._dataset(board_type, window["sessions"])
        boards = self._board_rows(rows, names, dates)
        daily = self._daily(rows, dates)
        analysis = self._deterministic_summary(window, board_type, boards, daily)
        payload = {
            "available": bool(rows),
            "board_type": board_type,
            "board_label": "行业板块" if board_type == "industry" else "概念板块",
            "window": {"id": window_key, **window},
            "period": {
                "start": dates[0].isoformat() if dates else None,
                "end": dates[-1].isoformat() if dates else None,
            },
            "coverage": {
                "actual_sessions": len(dates),
                "requested_sessions": window["sessions"],
                "board_count": len(boards),
                "complete": len(dates) >= window["sessions"],
            },
            "analysis": analysis,
            "daily": daily,
            "source": "database_cache",
            "is_realtime": False,
            "updated_at": shanghai_now().isoformat(),
            "ai_narrative": None,
            "ai_generated": False,
            "method": "按最近缓存交易日聚合板块主力净流入、连续性、广度、集中度和由正转负变化。",
        }
        if rows:
            narrative = await self._ai_narrative(payload)
            payload["ai_narrative"] = narrative
            payload["ai_generated"] = bool(narrative)
        return payload


flow_analysis_service = FlowAnalysisService()
