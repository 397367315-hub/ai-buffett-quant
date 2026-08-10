"""Persistent AI stock pools with auditable 100-share paper positions."""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select

from database import async_session
from models import (
    AIRobotJournal,
    AIRobotPick,
    AIRobotRun,
    ConceptBoard,
    MarketBoard,
    MarketDataCache,
    PersonalPoolItem,
)
from services.data_collector import collector, shanghai_now
from services.ai_service import ai_service
from services.stock_selection_agents import stock_selection_agents
from services.quote_cache import quote_snapshot_service


POOL_CONFIG: dict[str, dict[str, Any]] = {
    "short": {
        "label": "短期池",
        "horizon": "week",
        "mode": "quick",
        "risk_profile": "balanced",
        "holding_period": "1-4周",
        "refresh_rule": "每个交易日盘后 15:45",
        "criteria": ["20日动量", "MACD与放量", "RSI区间", "MA20趋势", "换手活跃度", "公告风险否决"],
    },
    "long": {
        "label": "长期池",
        "horizon": "month",
        "mode": "full",
        "risk_profile": "conservative",
        "holding_period": "3-12个月",
        "refresh_rule": "每个交易日盘后 16:20",
        "criteria": ["行业景气", "营收与利润质量", "ROE", "经营现金流", "负债约束", "估值与公告风险"],
    },
}


SECTOR_BLUEPRINTS: list[dict[str, Any]] = [
    {
        "key": "energy",
        "label": "电力/能源",
        "industry_terms": ["电力行业", "公用事业", "电网设备"],
        "concept_terms": ["绿色电力", "核电", "智能电网"],
    },
    {
        "key": "medicine",
        "label": "医药/CXO",
        "industry_terms": ["医疗服务", "化学制药", "生物制品", "医药商业"],
        "concept_terms": ["CXO", "创新药", "肝炎概念"],
    },
    {
        "key": "ai_compute",
        "label": "AI/算力",
        "industry_terms": ["软件开发", "通信设备", "互联网服务"],
        "concept_terms": ["人工智能", "算力", "ChatGPT", "数据中心"],
    },
    {
        "key": "advanced_manufacturing",
        "label": "高端制造",
        "industry_terms": ["通用设备", "专用设备", "自动化设备", "工程机械"],
        "concept_terms": ["机器人概念", "工业母机", "高端装备", "机器视觉"],
    },
    {
        "key": "consumer",
        "label": "消费复苏",
        "industry_terms": ["酿酒行业", "食品饮料", "商业百货", "旅游酒店"],
        "concept_terms": ["酿酒概念", "新零售", "预制菜", "免税概念"],
    },
    {
        "key": "resources",
        "label": "有色金属/资源",
        "industry_terms": ["有色金属", "小金属", "贵金属", "能源金属"],
        "concept_terms": ["稀土永磁", "黄金概念", "锂矿概念"],
    },
    {
        "key": "finance_dividend",
        "label": "金融/红利",
        "industry_terms": ["银行", "证券", "保险"],
        "concept_terms": ["高股息", "中特估", "破净股"],
    },
    {
        "key": "semiconductor",
        "label": "半导体/电子",
        "industry_terms": ["半导体", "电子元件", "消费电子", "光学光电子"],
        "concept_terms": ["半导体概念", "国产芯片", "存储芯片", "第三代半导体"],
    },
]


def _number(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _date(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _datetime_text(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _run_dict(row: AIRobotRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "pool_type": row.pool_type,
        "pool_label": POOL_CONFIG.get(row.pool_type, {}).get("label", row.pool_type),
        "status": row.status,
        "trigger": row.trigger,
        "progress": row.progress,
        "message": row.message or "",
        "summary": row.summary or {},
        "error": row.error,
        "source_data_date": row.source_data_date.isoformat() if row.source_data_date else None,
        "is_realtime": bool(row.is_realtime),
        "started_at": _datetime_text(row.started_at),
        "finished_at": _datetime_text(row.finished_at),
        "created_at": _datetime_text(row.created_at),
    }


def _journal_dict(row: AIRobotJournal | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "run_id": row.run_id,
        "pool_type": row.pool_type,
        "journal_date": row.journal_date.isoformat(),
        "source_data_date": row.source_data_date.isoformat() if row.source_data_date else None,
        "is_realtime": bool(row.is_realtime),
        "action_summary": row.action_summary,
        "decision_reason": row.decision_reason,
        "pnl_reflection": row.pnl_reflection or "",
        "lessons": row.lessons or "",
        "metrics": row.metrics or {},
        "picks_snapshot": row.picks_snapshot or [],
        "created_at": _datetime_text(row.created_at),
        "updated_at": _datetime_text(row.updated_at),
    }


def _next_weekday_refresh(now: datetime, hour: int, minute: int) -> datetime:
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


class AIRobotService:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._quote_lock = asyncio.Lock()
        self._quote_cache: tuple[datetime, tuple[str, ...], dict[str, Any]] | None = None

    async def _set_progress(self, run_id: int, progress: int, message: str) -> None:
        async with async_session() as session:
            row = await session.get(AIRobotRun, run_id)
            if row is None:
                return
            row.status = "running"
            row.progress = min(max(int(progress), 0), 99)
            row.message = message[:300]
            if row.started_at is None:
                row.started_at = datetime.utcnow()
            await session.commit()

    async def _create_run(self, pool_type: str, trigger: str) -> tuple[AIRobotRun, bool]:
        if pool_type not in POOL_CONFIG:
            raise ValueError("pool_type 必须是 short 或 long")
        async with async_session() as session:
            active = (await session.execute(
                select(AIRobotRun)
                .where(AIRobotRun.pool_type == pool_type, AIRobotRun.status.in_(["queued", "running"]))
                .order_by(desc(AIRobotRun.id))
                .limit(1)
            )).scalar_one_or_none()
            if active is not None:
                age = datetime.utcnow() - (active.started_at or active.created_at or datetime.utcnow())
                if age <= timedelta(hours=6):
                    return active, False
                active.status = "failed"
                active.error = "WorkerInterrupted"
                active.message = "上次刷新进程已中断，可重新触发"
                active.finished_at = datetime.utcnow()

            config = POOL_CONFIG[pool_type]
            row = AIRobotRun(
                pool_type=pool_type,
                status="queued",
                trigger=str(trigger or "manual")[:20],
                progress=0,
                message="等待机器人开始分析",
                config_snapshot={
                    **config,
                    "simulated_shares": 100,
                    "target_per_sector": 5,
                    "sector_keys": [item["key"] for item in SECTOR_BLUEPRINTS],
                },
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row, True

    def _spawn(self, run_id: int) -> None:
        task = asyncio.create_task(self._execute(run_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def refresh(self, pool_type: str, *, trigger: str = "manual", background: bool = True) -> dict[str, Any]:
        row, created = await self._create_run(pool_type, trigger)
        if created:
            if background:
                self._spawn(row.id)
            else:
                await self._execute(row.id)
                async with async_session() as session:
                    row = await session.get(AIRobotRun, row.id)
        return {"run": _run_dict(row), "created": created}

    @staticmethod
    def _board_match(rows: list[dict], terms: list[str]) -> list[tuple[int, dict]]:
        matches: list[tuple[int, dict]] = []
        for row in rows:
            name = "".join(str(row.get("name") or "").split()).lower()
            if not name or not row.get("code"):
                continue
            best = 0
            for position, term in enumerate(terms):
                needle = "".join(term.split()).lower()
                if name == needle:
                    best = max(best, 1000 - position * 10)
                elif needle in name or name in needle:
                    best = max(best, 700 - position * 10 - abs(len(name) - len(needle)))
            if best:
                flow = _number(row.get("main_net_inflow")) or 0
                matches.append((best + int(max(-99, min(99, flow / 1e8))), row))
        return sorted(matches, key=lambda item: item[0], reverse=True)

    async def _resolve_sectors(self) -> list[dict[str, Any]]:
        industry_result, concept_result = await asyncio.gather(
            collector.fetch_all_industry_flow(),
            collector.fetch_all_concept_flow(),
            return_exceptions=True,
        )
        industry = [] if isinstance(industry_result, Exception) else list(industry_result or [])
        concepts = [] if isinstance(concept_result, Exception) else list(concept_result or [])
        if not industry or not concepts:
            fallback_results = await asyncio.gather(
                collector.fetch_industry_flow(page_size=100) if not industry else asyncio.sleep(0, result=industry),
                collector.fetch_concept_flow(page_size=100) if not concepts else asyncio.sleep(0, result=concepts),
                return_exceptions=True,
            )
            if not industry and not isinstance(fallback_results[0], Exception):
                industry = list(fallback_results[0] or [])
            if not concepts and not isinstance(fallback_results[1], Exception):
                concepts = list(fallback_results[1] or [])
        if not industry or not concepts:
            try:
                async with async_session() as session:
                    cached_rows = (await session.execute(select(MarketBoard))).scalars().all()
                    sector_cache = await session.get(MarketDataCache, "stock_selection_sector_directory_v1")
                    seed_rows = (await session.execute(select(ConceptBoard))).scalars().all()
                directory = (
                    sector_cache.payload.get("sectors") or []
                    if sector_cache and isinstance(sector_cache.payload, dict)
                    else []
                )
                if not industry:
                    industry = [
                        {"code": row.code, "name": row.name, "main_net_inflow": 0}
                        for row in cached_rows if row.board_type == "industry"
                    ]
                    industry.extend(
                        {
                            "code": item.get("code"),
                            "name": item.get("name"),
                            "main_net_inflow": item.get("main_net_inflow", 0),
                        }
                        for item in directory
                        if item.get("code") and item.get("name")
                    )
                if not concepts:
                    concepts = [
                        {"code": row.code, "name": row.name, "main_net_inflow": 0}
                        for row in cached_rows if row.board_type == "concept"
                    ]
                verified_seed = [
                    {"code": row.code, "name": row.name, "main_net_inflow": 0}
                    for row in seed_rows if row.code and row.name
                ]
                industry.extend(verified_seed)
                concepts.extend(verified_seed)
            except Exception:
                pass
        used: set[str] = set()
        resolved: list[dict[str, Any]] = []
        for sector in SECTOR_BLUEPRINTS:
            candidates = [
                *[(score + 30, {**row, "board_type": "industry"}) for score, row in self._board_match(industry, sector["industry_terms"])],
                *[(score, {**row, "board_type": "concept"}) for score, row in self._board_match(concepts, sector["concept_terms"])],
            ]
            candidates.sort(key=lambda item: item[0], reverse=True)
            board = next((row for _, row in candidates if str(row.get("code")) not in used), None)
            if board:
                used.add(str(board["code"]))
            resolved.append({**sector, "board": board})
        return resolved

    @staticmethod
    async def _core_codes() -> set[str]:
        async with async_session() as session:
            rows = (await session.execute(
                select(PersonalPoolItem.code).where(PersonalPoolItem.pool_key == "core")
            )).all()
        return {str(row[0]) for row in rows}

    async def _previous_snapshot(self, run_id: int, pool_type: str) -> tuple[AIRobotRun | None, dict[str, AIRobotPick]]:
        async with async_session() as session:
            previous = (await session.execute(
                select(AIRobotRun)
                .where(
                    AIRobotRun.pool_type == pool_type,
                    AIRobotRun.id != run_id,
                    AIRobotRun.status.in_(["completed", "partial"]),
                )
                .order_by(desc(AIRobotRun.id))
                .limit(1)
            )).scalar_one_or_none()
            if previous is None:
                return None, {}
            picks = (await session.execute(
                select(AIRobotPick).where(AIRobotPick.run_id == previous.id)
            )).scalars().all()
        return previous, {row.code: row for row in picks}

    @staticmethod
    def _evidence(recommendation: dict[str, Any]) -> list[dict[str, Any]]:
        output = []
        for key, agent in (recommendation.get("agents") or {}).items():
            if not isinstance(agent, dict):
                continue
            output.append({
                "agent": agent.get("agent") or key,
                "signal": agent.get("signal"),
                "summary": agent.get("summary") or "",
                "evidence": list(agent.get("evidence") or [])[:3],
                "risks": list(agent.get("risks") or [])[:3],
            })
        return output

    @staticmethod
    def _pick_basis(row: AIRobotPick) -> str:
        recommendation = row.recommendation or {}
        outlook = recommendation.get("horizon_outlook") or {}
        supervisor = (recommendation.get("agents") or {}).get("supervisor") or {}
        return str(outlook.get("basis") or supervisor.get("summary") or row.verdict or "Agent 规则评分通过")

    async def _record_decision_journal(self, run_id: int) -> None:
        async with async_session() as session:
            run = await session.get(AIRobotRun, run_id)
            if run is None or run.status not in {"completed", "partial"}:
                return
            picks = list((await session.execute(
                select(AIRobotPick).where(AIRobotPick.run_id == run_id).order_by(desc(AIRobotPick.score))
            )).scalars().all())
            summary = dict(run.summary or {})
            journal_date = shanghai_now().date()
            source_label = run.source_data_date.isoformat() if run.source_data_date else "无有效行情日期"
            action_summary = (
                f"{POOL_CONFIG[run.pool_type]['label']}本轮保留 {summary.get('retained', 0)} 只、"
                f"新调入 {summary.get('new', 0)} 只、移出 {summary.get('removed', 0)} 只；"
                f"有核验价格的标的均按 100 股模拟，依据数据日 {source_label}。"
            )
            reasons = [
                f"{row.name}({row.code})：{self._pick_basis(row)[:140]}"
                for row in picks[:6]
            ]
            decision_reason = "\n".join(reasons) or "本轮没有形成可验证的入选依据。"
            unavailable = len(SECTOR_BLUEPRINTS) - int(summary.get("available_sectors") or 0)
            lessons = (
                f"本轮有 {unavailable} 个板块数据不完整，结论只覆盖已返回有效数据的板块。"
                if unavailable > 0
                else "八大板块均完成数据核验；仍需用后续行情检验选股依据，不能把评分当作收益承诺。"
            )
            snapshot = [{
                "code": row.code,
                "name": row.name,
                "sector": row.sector_label,
                "state": row.state,
                "score": row.score,
                "confidence": row.confidence,
                "selected_price": row.selected_price,
                "selected_on": row.selected_on.isoformat() if row.selected_on else None,
                "shares": int(row.simulated_shares or 100),
                "basis": self._pick_basis(row),
            } for row in picks]
            journal = (await session.execute(
                select(AIRobotJournal).where(
                    AIRobotJournal.pool_type == run.pool_type,
                    AIRobotJournal.journal_date == journal_date,
                )
            )).scalar_one_or_none()
            if journal is None:
                journal = AIRobotJournal(
                    pool_type=run.pool_type,
                    journal_date=journal_date,
                    action_summary=action_summary,
                    decision_reason=decision_reason,
                )
                session.add(journal)
            journal.run_id = run.id
            journal.source_data_date = run.source_data_date
            journal.is_realtime = bool(run.is_realtime)
            journal.action_summary = action_summary
            journal.decision_reason = decision_reason
            journal.lessons = lessons
            journal.metrics = {
                "selected": summary.get("selected", len(picks)),
                "new": summary.get("new", 0),
                "retained": summary.get("retained", 0),
                "removed": summary.get("removed", 0),
                "waiting_for_price": summary.get("waiting_for_price", 0),
                "available_sectors": summary.get("available_sectors", 0),
            }
            journal.picks_snapshot = snapshot
            await session.commit()

    async def journal_history(self, pool_type: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        if pool_type and pool_type not in POOL_CONFIG:
            raise ValueError("pool_type 必须是 short 或 long")
        query = select(AIRobotJournal).order_by(desc(AIRobotJournal.journal_date), desc(AIRobotJournal.id)).limit(min(max(limit, 1), 180))
        if pool_type:
            query = query.where(AIRobotJournal.pool_type == pool_type)
        async with async_session() as session:
            rows = list((await session.execute(query)).scalars().all())
        return [_journal_dict(row) for row in rows]

    async def performance_calendar(self, pool_type: str | None = None, days: int = 180) -> dict[str, Any]:
        """Aggregate persisted robot journals into a calendar-friendly ledger."""
        if pool_type and pool_type not in POOL_CONFIG:
            raise ValueError("pool_type 必须是 short 或 long")
        bounded_days = min(max(int(days), 7), 730)
        today = shanghai_now().date()
        cutoff = today - timedelta(days=bounded_days - 1)
        query = select(AIRobotJournal).where(
            AIRobotJournal.journal_date >= cutoff,
            AIRobotJournal.journal_date <= today,
        ).order_by(AIRobotJournal.journal_date.asc(), AIRobotJournal.pool_type.asc())
        if pool_type:
            query = query.where(AIRobotJournal.pool_type == pool_type)
        async with async_session() as session:
            rows = list((await session.execute(query)).scalars().all())

        by_date: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = row.journal_date.isoformat()
            day = by_date.setdefault(key, {
                "date": key,
                "pnl": 0.0,
                "cost_value": 0.0,
                "market_value": 0.0,
                "priced_positions": 0,
                "waiting_positions": 0,
                "winners": 0,
                "losers": 0,
                "pools": {},
                "is_realtime": False,
                "source_data_dates": [],
            })
            metrics = row.metrics if isinstance(row.metrics, dict) else {}
            performance = metrics.get("performance") if isinstance(metrics.get("performance"), dict) else {}
            pnl = _number(performance.get("pnl"))
            cost = _number(performance.get("cost_value"))
            market_value = _number(performance.get("market_value"))
            priced = int(performance.get("priced_positions") or 0)
            waiting = int(performance.get("waiting_positions") or 0) + int(performance.get("quote_unavailable_positions") or 0)
            pool_view = {
                "journal_id": row.id,
                "pool_type": row.pool_type,
                "pnl": round(pnl, 2) if pnl is not None else None,
                "pnl_pct": _number(performance.get("pnl_pct")),
                "cost_value": round(cost, 2) if cost is not None else None,
                "market_value": round(market_value, 2) if market_value is not None else None,
                "priced_positions": priced,
                "waiting_positions": waiting,
                "winners": int(performance.get("winners") or 0),
                "losers": int(performance.get("losers") or 0),
                "source_data_date": row.source_data_date.isoformat() if row.source_data_date else None,
                "is_realtime": bool(row.is_realtime),
                "action_summary": row.action_summary,
            }
            day["pools"][row.pool_type] = pool_view
            if pnl is not None:
                day["pnl"] += pnl
            if cost is not None:
                day["cost_value"] += cost
            if market_value is not None:
                day["market_value"] += market_value
            day["priced_positions"] += priced
            day["waiting_positions"] += waiting
            day["winners"] += int(performance.get("winners") or 0)
            day["losers"] += int(performance.get("losers") or 0)
            day["is_realtime"] = day["is_realtime"] or bool(row.is_realtime)
            if row.source_data_date:
                day["source_data_dates"].append(row.source_data_date.isoformat())

        calendar_days = []
        for key, day in sorted(by_date.items()):
            cost = day["cost_value"]
            day["pnl"] = round(day["pnl"], 2)
            day["cost_value"] = round(cost, 2)
            day["market_value"] = round(day["market_value"], 2)
            day["pnl_pct"] = round(day["pnl"] / cost * 100, 2) if cost > 0 else None
            day["status"] = "profit" if day["pnl"] > 0 else "loss" if day["pnl"] < 0 else "flat"
            day["source_data_dates"] = sorted(set(day["source_data_dates"]))
            calendar_days.append(day)

        profits = [item for item in calendar_days if item["status"] == "profit"]
        losses = [item for item in calendar_days if item["status"] == "loss"]
        max_loss_streak = 0
        current_loss_streak = 0
        for item in calendar_days:
            if item["status"] == "loss":
                current_loss_streak += 1
                max_loss_streak = max(max_loss_streak, current_loss_streak)
            else:
                current_loss_streak = 0
        total_pnl = round(sum(item["pnl"] for item in calendar_days), 2)
        total_cost = sum(item["cost_value"] for item in calendar_days)
        return {
            "from": cutoff.isoformat(),
            "to": today.isoformat(),
            "pool_type": pool_type or "all",
            "days": calendar_days,
            "summary": {
                "recorded_days": len(calendar_days),
                "profit_days": len(profits),
                "loss_days": len(losses),
                "flat_days": len(calendar_days) - len(profits) - len(losses),
                "total_pnl": total_pnl,
                "total_pnl_pct": round(total_pnl / total_cost * 100, 2) if total_cost else None,
                "best_day": max(calendar_days, key=lambda item: item["pnl"], default=None),
                "worst_day": min(calendar_days, key=lambda item: item["pnl"], default=None),
                "current_loss_streak": current_loss_streak,
                "max_loss_streak": max_loss_streak,
            },
            "source": "ai_robot_journals",
            "methodology": "按每日机器人复盘中的模拟持仓快照聚合；缺失有效价格的标的不计入盈亏，不以空白补零为盈利。",
        }

    async def performance_calendar_day(self, journal_date: date) -> dict[str, Any]:
        async with async_session() as session:
            rows = list((await session.execute(
                select(AIRobotJournal)
                .where(AIRobotJournal.journal_date == journal_date)
                .order_by(AIRobotJournal.pool_type.asc())
            )).scalars().all())
        return {
            "date": journal_date.isoformat(),
            "journals": [_journal_dict(row) for row in rows],
            "available": bool(rows),
        }

    async def analyze_performance_calendar(
        self,
        *,
        pool_type: str | None = None,
        days: int = 180,
        use_ai: bool = True,
    ) -> dict[str, Any]:
        calendar = await self.performance_calendar(pool_type, days)
        summary = calendar["summary"]
        evidence = {
            "pool_type": calendar["pool_type"],
            "period": [calendar["from"], calendar["to"]],
            "recorded_days": summary["recorded_days"],
            "profit_days": summary["profit_days"],
            "loss_days": summary["loss_days"],
            "total_pnl": summary["total_pnl"],
            "total_pnl_pct": summary["total_pnl_pct"],
            "current_loss_streak": summary["current_loss_streak"],
            "max_loss_streak": summary["max_loss_streak"],
            "recent_days": [
                {"date": item["date"], "pnl": item["pnl"], "pnl_pct": item["pnl_pct"], "pools": item["pools"]}
                for item in calendar["days"][-20:]
            ],
        }
        fallback = (
            f"已记录{summary['recorded_days']}个模拟交易日，盈利{summary['profit_days']}天、"
            f"亏损{summary['loss_days']}天，累计模拟盈亏{summary['total_pnl']:.2f}元。"
        )
        if summary["current_loss_streak"] >= 3:
            fallback += "当前连续亏损达到3天，仅触发提醒；请复核选股依据和仓位，不自动停止行情扫描。"
        elif summary["max_loss_streak"] >= 3:
            fallback += "历史出现过至少3天连续亏损，建议重点检查亏损日的板块集中度和数据完整性。"
        else:
            fallback += "当前没有达到连续3个亏损日的提醒阈值，仍需观察样本外表现。"
        analysis = fallback
        source = "rule_based"
        if use_ai:
            prompt = (
                "你是量化研究复盘助手。只根据下面已持久化的模拟数据分析，不得编造股票价格或胜率，"
                "用中文输出三段：表现事实、可能原因、下一步人工复核。明确这是模拟盘，不给确定买卖指令。\n"
                f"数据：{evidence}"
            )
            generated = await ai_service.generate(prompt)
            if generated and not generated.startswith("[AI服务") and not generated.startswith("[AI服务暂时不可用"):
                analysis = generated.strip()
                source = "deepseek"
        return {
            "analysis": analysis,
            "source": source,
            "generated_at": shanghai_now().isoformat(),
            "evidence": evidence,
            "calendar": calendar,
        }

    async def _latest_journals(self) -> dict[str, dict[str, Any] | None]:
        output: dict[str, dict[str, Any] | None] = {}
        async with async_session() as session:
            for pool_type in POOL_CONFIG:
                row = (await session.execute(
                    select(AIRobotJournal)
                    .where(AIRobotJournal.pool_type == pool_type)
                    .order_by(desc(AIRobotJournal.journal_date), desc(AIRobotJournal.id))
                    .limit(1)
                )).scalar_one_or_none()
                output[pool_type] = _journal_dict(row)
        return output

    async def _execute(self, run_id: int) -> None:
        try:
            async with async_session() as session:
                run = await session.get(AIRobotRun, run_id)
                if run is None:
                    return
                pool_type = run.pool_type
            config = POOL_CONFIG[pool_type]
            await self._set_progress(run_id, 3, "正在核验八大板块目录")
            sectors = await self._resolve_sectors()
            await self._set_progress(run_id, 8, "板块目录已核验，Agent 开始逐板块分析")

            completed = 0
            progress_lock = asyncio.Lock()
            semaphore = asyncio.Semaphore(2)

            async def analyze(sector: dict[str, Any]) -> dict[str, Any]:
                nonlocal completed
                board = sector.get("board")
                if not board:
                    result = {"sector": sector, "result": None, "error": "未找到可核验板块代码"}
                else:
                    try:
                        async with semaphore:
                            value = await stock_selection_agents.run(
                                mode=config["mode"],
                                risk_profile=config["risk_profile"],
                                top_n=10,
                                sector=str(board.get("name") or sector["label"]),
                                sector_code=str(board["code"]),
                                horizon=config["horizon"],
                            )
                        result = {"sector": sector, "result": value, "error": None}
                    except Exception as exc:
                        result = {"sector": sector, "result": None, "error": type(exc).__name__}
                async with progress_lock:
                    completed += 1
                    progress = 8 + round(completed / len(sectors) * 78)
                    await self._set_progress(run_id, progress, f"已完成 {completed}/{len(sectors)} 个板块")
                return result

            analyses = await asyncio.gather(*(analyze(sector) for sector in sectors))
            await self._set_progress(run_id, 89, "正在比对上期名单并建立100股模拟持仓")
            core_codes = await self._core_codes()
            previous_run, previous = await self._previous_snapshot(run_id, pool_type)
            current_codes: set[str] = set()
            excluded_core_codes: set[str] = set()
            pending_picks: list[AIRobotPick] = []
            source_dates: list[date] = []
            realtime_flags: list[bool] = []
            sector_status = []

            for analysis in analyses:
                sector = analysis["sector"]
                board = sector.get("board") or {}
                result = analysis.get("result")
                if not isinstance(result, dict):
                    sector_status.append({
                        "key": sector["key"], "label": sector["label"], "board": board,
                        "status": "unavailable", "selected": 0, "error": analysis.get("error"),
                    })
                    continue
                result_date = _date(result.get("data_date"))
                if result_date:
                    source_dates.append(result_date)
                realtime_flags.append(bool(result.get("is_realtime")))
                selected_count = 0
                for recommendation in result.get("recommendations") or []:
                    code = str(recommendation.get("code") or "")
                    if code in core_codes:
                        excluded_core_codes.add(code)
                        continue
                    if not code or code in current_codes:
                        continue
                    current_codes.add(code)
                    selected_count += 1
                    prior = previous.get(code)
                    price = _number(recommendation.get("price"))
                    selected_price = prior.selected_price if prior and prior.selected_price else (price if price and price > 0 and result_date else None)
                    selected_on = prior.selected_on if prior and prior.selected_price else (result_date if selected_price else None)
                    pending_picks.append(AIRobotPick(
                        run_id=run_id,
                        pool_type=pool_type,
                        sector_key=sector["key"],
                        sector_label=sector["label"],
                        board_code=str(board.get("code") or "") or None,
                        code=code,
                        name=str(recommendation.get("name") or code),
                        selected_price=selected_price,
                        selected_on=selected_on,
                        simulated_shares=100,
                        score=_number(recommendation.get("score")),
                        confidence=_number(recommendation.get("confidence")),
                        verdict=str(recommendation.get("verdict") or ""),
                        state="retained" if prior else "new",
                        criteria=config["criteria"],
                        evidence=self._evidence(recommendation),
                        recommendation=recommendation,
                    ))
                    if selected_count >= 5:
                        break
                sector_status.append({
                    "key": sector["key"],
                    "label": sector["label"],
                    "board": {"code": board.get("code"), "name": board.get("name"), "type": board.get("board_type")},
                    "status": "available" if selected_count else "empty",
                    "selected": selected_count,
                    "source": result.get("source"),
                    "data_date": result.get("data_date"),
                    "message": result.get("message"),
                })

            removed = sorted(set(previous) - current_codes)
            new_codes = sorted(code for code in current_codes if code not in previous)
            retained_codes = sorted(code for code in current_codes if code in previous)
            waiting_count = sum(1 for item in pending_picks if item.selected_price is None)
            available_sectors = sum(1 for item in sector_status if item["status"] == "available")
            summary = {
                "selected": len(pending_picks),
                "new": len(new_codes),
                "retained": len(retained_codes),
                "removed": len(removed),
                "waiting_for_price": waiting_count,
                "new_codes": new_codes,
                "retained_codes": retained_codes,
                "removed_codes": removed,
                "excluded_core_codes": sorted(excluded_core_codes),
                "previous_run_id": previous_run.id if previous_run else None,
                "sector_status": sector_status,
                "available_sectors": available_sectors,
                "simulated_shares_per_pick": 100,
            }
            if pending_picks:
                status = "completed" if available_sectors == len(sectors) else "partial"
                message = f"{config['label']}完成：{len(pending_picks)}只，新增{len(new_codes)}只，保留{len(retained_codes)}只"
            else:
                status = "failed"
                message = "数据源未返回满足风险约束的可验证股票，本轮未建立模拟持仓"

            async with async_session() as session:
                row = await session.get(AIRobotRun, run_id)
                if row is None:
                    return
                session.add_all(pending_picks)
                row.status = status
                row.progress = 100
                row.message = message
                row.summary = summary
                row.source_data_date = max(source_dates) if source_dates else None
                row.is_realtime = bool(realtime_flags) and all(realtime_flags)
                row.finished_at = datetime.utcnow()
                row.error = None if pending_picks else "NoVerifiedSelection"
                await session.commit()
            if pending_picks:
                try:
                    await self._record_decision_journal(run_id)
                except Exception as journal_exc:
                    print(f"AI robot journal write failed: {type(journal_exc).__name__}")
        except Exception as exc:
            async with async_session() as session:
                row = await session.get(AIRobotRun, run_id)
                if row is not None:
                    row.status = "failed"
                    row.progress = 100
                    row.message = "机器人刷新失败，可稍后重试"
                    row.error = type(exc).__name__
                    row.finished_at = datetime.utcnow()
                    await session.commit()

    async def get_run(self, run_id: int) -> dict[str, Any]:
        async with async_session() as session:
            row = await session.get(AIRobotRun, run_id)
            if row is None:
                raise LookupError("机器人运行记录不存在")
        return _run_dict(row)

    async def history(self, pool_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        query = select(AIRobotRun).order_by(desc(AIRobotRun.id)).limit(min(max(limit, 1), 100))
        if pool_type:
            if pool_type not in POOL_CONFIG:
                raise ValueError("pool_type 必须是 short 或 long")
            query = query.where(AIRobotRun.pool_type == pool_type)
        async with async_session() as session:
            rows = (await session.execute(query)).scalars().all()
        return [_run_dict(row) for row in rows]

    async def _latest_runs(self) -> dict[str, AIRobotRun | None]:
        output: dict[str, AIRobotRun | None] = {}
        async with async_session() as session:
            for pool_type in POOL_CONFIG:
                completed = (await session.execute(
                    select(AIRobotRun)
                    .where(AIRobotRun.pool_type == pool_type, AIRobotRun.status.in_(["completed", "partial"]))
                    .order_by(desc(AIRobotRun.id)).limit(1)
                )).scalar_one_or_none()
                latest = (await session.execute(
                    select(AIRobotRun)
                    .where(AIRobotRun.pool_type == pool_type)
                    .order_by(desc(AIRobotRun.id)).limit(1)
                )).scalar_one_or_none()
                output[pool_type] = completed or latest
        return output

    async def _active_runs(self) -> dict[str, dict[str, Any] | None]:
        output: dict[str, dict[str, Any] | None] = {}
        async with async_session() as session:
            for pool_type in POOL_CONFIG:
                row = (await session.execute(
                    select(AIRobotRun)
                    .where(AIRobotRun.pool_type == pool_type, AIRobotRun.status.in_(["queued", "running"]))
                    .order_by(desc(AIRobotRun.id)).limit(1)
                )).scalar_one_or_none()
                output[pool_type] = _run_dict(row) if row else None
        return output

    async def warm_market_cache(self) -> dict[str, Any]:
        """Save sector candidates while the closing snapshot is still reachable."""
        sectors = await self._resolve_sectors()
        semaphore = asyncio.Semaphore(3)

        async def warm(sector: dict[str, Any]) -> dict[str, Any]:
            board = sector.get("board") or {}
            if not board.get("code"):
                return {"key": sector["key"], "status": "unavailable", "count": 0}
            async with semaphore:
                snapshot = await stock_selection_agents._candidate_snapshot(
                    f"stock_selection_candidates_v1:{board['code']}",
                    lambda: collector.fetch_all_board_stocks(
                        str(board["code"]),
                        sector_name=str(board.get("name") or sector["label"]),
                    ),
                )
            return {
                "key": sector["key"],
                "board_code": board["code"],
                "status": "cached" if snapshot.get("stocks") else "unavailable",
                "count": len(snapshot.get("stocks") or []),
                "data_date": snapshot.get("data_date"),
            }

        market, sector_results = await asyncio.gather(
            stock_selection_agents._candidate_snapshot(
                "stock_selection_candidates_v1:market",
                lambda: collector.fetch_intelligent_selection_candidates(),
            ),
            asyncio.gather(*(warm(sector) for sector in sectors)),
        )
        return {
            "market_candidates": len(market.get("stocks") or []),
            "market_data_date": market.get("data_date"),
            "sectors": sector_results,
        }

    async def _quotes(self, codes: list[str]) -> dict[str, Any]:
        key = tuple(sorted(set(codes)))
        if not key:
            return {"stocks": [], "available": False, "source": "eastmoney", "complete": True}
        now = shanghai_now()
        async with self._quote_lock:
            if self._quote_cache and self._quote_cache[1] == key and now - self._quote_cache[0] < timedelta(seconds=30):
                return self._quote_cache[2]
            try:
                payload = await quote_snapshot_service.fetch(list(key), async_session)
            except Exception as exc:
                payload = {
                    "stocks": [], "available": False, "source": "eastmoney", "complete": False,
                    "fetched_at": now.isoformat(), "error": type(exc).__name__,
                }
            self._quote_cache = (now, key, payload)
            return payload

    @staticmethod
    def _pick_view(row: AIRobotPick, quote: dict[str, Any] | None) -> dict[str, Any]:
        quote = quote or {}
        selected_price = _number(row.selected_price)
        latest_price = _number(quote.get("price"))
        shares = int(row.simulated_shares or 100)
        cost_value = selected_price * shares if selected_price and selected_price > 0 else None
        market_value = latest_price * shares if latest_price and latest_price > 0 else None
        pnl = market_value - cost_value if market_value is not None and cost_value is not None else None
        pnl_pct = pnl / cost_value * 100 if pnl is not None and cost_value else None
        recommendation = row.recommendation or {}
        outlook = recommendation.get("horizon_outlook") or {}
        return {
            "id": row.id,
            "run_id": row.run_id,
            "pool_type": row.pool_type,
            "sector_key": row.sector_key,
            "sector_label": row.sector_label,
            "board_code": row.board_code,
            "code": row.code,
            "name": str(quote.get("name") or row.name),
            "selected_price": round(selected_price, 4) if selected_price is not None else None,
            "selected_on": row.selected_on.isoformat() if row.selected_on else None,
            "simulated_shares": shares,
            "cost_value": round(cost_value, 2) if cost_value is not None else None,
            "latest_price": round(latest_price, 4) if latest_price is not None else None,
            "market_value": round(market_value, 2) if market_value is not None else None,
            "pnl": round(pnl, 2) if pnl is not None else None,
            "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
            "price_status": "waiting" if selected_price is None else "quote_unavailable" if latest_price is None else "available",
            "change_pct": _number(quote.get("change_pct")),
            "score": row.score,
            "confidence": row.confidence,
            "verdict": row.verdict or "",
            "state": row.state,
            "criteria": row.criteria or [],
            "evidence": row.evidence or [],
            "basis": outlook.get("basis") or ((recommendation.get("agents") or {}).get("supervisor") or {}).get("summary") or "",
            "holding_label": outlook.get("label") or POOL_CONFIG[row.pool_type]["holding_period"],
            "quote_timestamp": collector._quote_timestamp_datetime(quote.get("quote_timestamp")).isoformat() if collector._quote_timestamp_datetime(quote.get("quote_timestamp")) else None,
        }

    @staticmethod
    def _pool_performance(picks: list[dict[str, Any]]) -> dict[str, Any]:
        priced = [item for item in picks if item["cost_value"] is not None and item["market_value"] is not None]
        total_cost = sum(item["cost_value"] for item in priced)
        market_value = sum(item["market_value"] for item in priced)
        pnl = market_value - total_cost
        return {
            "positions": len(picks),
            "priced_positions": len(priced),
            "waiting_positions": sum(item["price_status"] == "waiting" for item in picks),
            "quote_unavailable_positions": sum(item["price_status"] == "quote_unavailable" for item in picks),
            "simulated_shares": sum(item["simulated_shares"] for item in picks),
            "cost_value": round(total_cost, 2) if priced else None,
            "market_value": round(market_value, 2) if priced else None,
            "pnl": round(pnl, 2) if priced else None,
            "pnl_pct": round(pnl / total_cost * 100, 2) if total_cost > 0 else None,
            "winners": sum((item["pnl"] or 0) > 0 for item in priced),
            "losers": sum((item["pnl"] or 0) < 0 for item in priced),
        }

    async def dashboard(self) -> dict[str, Any]:
        try:
            from services.overnight_strategy import overnight_strategy_service
            overnight = await overnight_strategy_service.robot_summary()
        except Exception as exc:
            overnight = {
                "tag": "一夜持股",
                "positions": [],
                "recent_closed": [],
                "performance": {},
                "data_quality": {"error": type(exc).__name__},
            }
        latest = await self._latest_runs()
        active_runs = await self._active_runs()
        latest_journals = await self._latest_journals()
        run_ids = [row.id for row in latest.values() if row and row.status in {"completed", "partial"}]
        async with async_session() as session:
            rows = (await session.execute(
                select(AIRobotPick).where(AIRobotPick.run_id.in_(run_ids)).order_by(AIRobotPick.sector_key, desc(AIRobotPick.score))
            )).scalars().all() if run_ids else []
        quote_payload = await self._quotes([row.code for row in rows])
        quotes = {str(item.get("code")): item for item in quote_payload.get("stocks") or []}
        now = shanghai_now()
        pools = {}
        all_picks: list[dict[str, Any]] = []
        for pool_type, config in POOL_CONFIG.items():
            run = latest.get(pool_type)
            picks = [self._pick_view(row, quotes.get(row.code)) for row in rows if run and row.run_id == run.id]
            all_picks.extend(picks)
            grouped = []
            for blueprint in SECTOR_BLUEPRINTS:
                items = [item for item in picks if item["sector_key"] == blueprint["key"]]
                if items:
                    grouped.append({"key": blueprint["key"], "label": blueprint["label"], "count": len(items), "picks": items})
            pools[pool_type] = {
                "config": config,
                "run": _run_dict(run) if run else None,
                "sectors": grouped,
                "picks": picks,
                "performance": self._pool_performance(picks),
                "journal": latest_journals.get(pool_type),
                "next_update": _next_weekday_refresh(
                    now,
                    15 if pool_type == "short" else 16,
                    45 if pool_type == "short" else 20,
                ).isoformat(),
            }
        return {
            "updated_at": now.isoformat(),
            "pools": pools,
            "active_runs": active_runs,
            "combined_performance": self._pool_performance(all_picks),
            "quote": {
                "available": bool(quote_payload.get("available")),
                "source": quote_payload.get("source", "eastmoney"),
                "data_date": quote_payload.get("data_date"),
                "source_updated_at": quote_payload.get("source_updated_at"),
                "is_realtime": bool(quote_payload.get("is_realtime")),
                "complete": bool(quote_payload.get("complete")),
                "cache_used": bool(quote_payload.get("cache_used")),
                "stale": bool(quote_payload.get("stale")),
                "fetched_at": quote_payload.get("fetched_at") or now.isoformat(),
                "error": quote_payload.get("error"),
            },
            "overnight": overnight,
            "sector_blueprints": [{"key": item["key"], "label": item["label"]} for item in SECTOR_BLUEPRINTS],
            "simulation_rule": "常规机器人股按入选价模拟100股；一夜持股仅在尾盘分钟条件完整通过后模拟100股，并于次日10:00前退出。",
            "disclaimer": "机器人组合为研究用模拟记录，不会连接券商或自动下单，不构成投资建议。",
        }

    async def _record_performance_journals(self, dashboard: dict[str, Any]) -> None:
        run_ids = [
            int(pool["run"]["id"])
            for pool in dashboard["pools"].values()
            if (pool.get("run") or {}).get("id")
        ]
        for run_id in run_ids:
            await self._record_decision_journal(run_id)

        async with async_session() as session:
            for pool_type, pool in dashboard["pools"].items():
                run_data = pool.get("run") or {}
                if not run_data.get("id"):
                    continue
                journal = (await session.execute(
                    select(AIRobotJournal)
                    .where(AIRobotJournal.run_id == int(run_data["id"]))
                    .order_by(desc(AIRobotJournal.journal_date), desc(AIRobotJournal.id))
                    .limit(1)
                )).scalar_one_or_none()
                if journal is None:
                    continue
                performance = dict(pool.get("performance") or {})
                pnl = _number(performance.get("pnl"))
                pnl_pct = _number(performance.get("pnl_pct"))
                priced = int(performance.get("priced_positions") or 0)
                waiting = int(performance.get("waiting_positions") or 0) + int(performance.get("quote_unavailable_positions") or 0)
                if priced == 0:
                    reflection = f"今天没有足够的有效行情核算盈亏，{waiting} 只标的仍待价格恢复；不据此判断策略表现。"
                elif pnl is not None and pnl > 0:
                    reflection = (
                        f"当前可核算组合浮盈 {pnl:.2f} 元（{pnl_pct or 0:+.2f}%），"
                        f"盈利 {performance.get('winners', 0)} 只、亏损 {performance.get('losers', 0)} 只。"
                        "单日浮盈不证明选股逻辑长期有效，继续观察回撤和板块集中度。"
                    )
                elif pnl is not None and pnl < 0:
                    reflection = (
                        f"当前可核算组合浮亏 {pnl:.2f} 元（{pnl_pct or 0:+.2f}%），"
                        f"盈利 {performance.get('winners', 0)} 只、亏损 {performance.get('losers', 0)} 只。"
                        "优先复核亏损标的原始依据是否失效，不用补仓掩盖错误。"
                    )
                else:
                    reflection = "当前可核算组合接近盈亏平衡，继续用后续交易日验证选股依据和风险约束。"
                metrics = dict(journal.metrics or {})
                metrics.update({
                    "performance_recorded_at": dashboard["updated_at"],
                    "quote_data_date": dashboard.get("quote", {}).get("data_date"),
                    "quote_source": dashboard.get("quote", {}).get("source"),
                    "quote_is_realtime": bool(dashboard.get("quote", {}).get("is_realtime")),
                    "performance": performance,
                })
                latest_by_code = {item["code"]: item for item in pool.get("picks") or []}
                snapshots = []
                for item in journal.picks_snapshot or []:
                    latest = latest_by_code.get(item.get("code"), {})
                    snapshots.append({
                        **item,
                        "latest_price": latest.get("latest_price"),
                        "market_value": latest.get("market_value"),
                        "pnl": latest.get("pnl"),
                        "pnl_pct": latest.get("pnl_pct"),
                        "price_status": latest.get("price_status"),
                    })
                journal.metrics = metrics
                journal.picks_snapshot = snapshots
                journal.pnl_reflection = reflection
            await session.commit()

    async def record_performance_snapshot(self) -> dict[str, Any]:
        dashboard = await self.dashboard()
        async with async_session() as session:
            for pool_type, pool in dashboard["pools"].items():
                run_data = pool.get("run") or {}
                if not run_data.get("id"):
                    continue
                row = await session.get(AIRobotRun, int(run_data["id"]))
                if row is None:
                    continue
                summary = dict(row.summary or {})
                summary["last_performance"] = {
                    "recorded_at": dashboard["updated_at"],
                    **pool["performance"],
                }
                row.summary = summary
            await session.commit()
        await self._record_performance_journals(dashboard)
        return dashboard["combined_performance"]

    async def check_anomalies(self) -> list[dict[str, Any]]:
        dashboard = await self.dashboard()
        alerts = []
        for pool_type, pool in dashboard["pools"].items():
            for item in pool["picks"]:
                change = _number(item.get("change_pct"))
                pnl_pct = _number(item.get("pnl_pct"))
                if change is not None and abs(change) >= 5:
                    alerts.append({"pool_type": pool_type, "code": item["code"], "name": item["name"], "type": "daily_move", "message": f"当日涨跌 {change:+.2f}%"})
                if pnl_pct is not None and pnl_pct <= -8:
                    alerts.append({"pool_type": pool_type, "code": item["code"], "name": item["name"], "type": "drawdown", "message": f"模拟持仓浮亏 {pnl_pct:.2f}%"})
        return alerts

    async def resume_incomplete_runs(self) -> list[int]:
        async with async_session() as session:
            rows = (await session.execute(
                select(AIRobotRun).where(AIRobotRun.status.in_(["queued", "running"])).order_by(AIRobotRun.id)
            )).scalars().all()
            ids = [row.id for row in rows]
            for row in rows:
                row.status = "queued"
                row.message = "服务恢复后继续运行"
            await session.commit()
        for run_id in ids:
            self._spawn(run_id)
        return ids


ai_robot_service = AIRobotService()
