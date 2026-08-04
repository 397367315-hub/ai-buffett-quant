"""Persistent AI stock pools with auditable 100-share paper positions."""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select

from database import async_session
from models import (
    AIRobotPick,
    AIRobotRun,
    ConceptBoard,
    MarketBoard,
    MarketDataCache,
    PersonalPoolItem,
)
from services.data_collector import collector, shanghai_now
from services.stock_selection_agents import stock_selection_agents
from services.quote_cache import quote_snapshot_service


POOL_CONFIG: dict[str, dict[str, Any]] = {
    "short": {
        "label": "短期池",
        "horizon": "week",
        "mode": "quick",
        "risk_profile": "balanced",
        "holding_period": "1-4周",
        "refresh_rule": "每周日 21:00",
        "criteria": ["20日动量", "MACD与放量", "RSI区间", "MA20趋势", "换手活跃度", "公告风险否决"],
    },
    "long": {
        "label": "长期池",
        "horizon": "month",
        "mode": "full",
        "risk_profile": "conservative",
        "holding_period": "3-12个月",
        "refresh_rule": "每月首个周日 21:00",
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


def _next_sunday(now: datetime) -> datetime:
    days = (6 - now.weekday()) % 7
    candidate = now.replace(hour=21, minute=0, second=0, microsecond=0) + timedelta(days=days)
    return candidate if candidate > now else candidate + timedelta(days=7)


def _first_sunday(year: int, month: int, template: datetime) -> datetime:
    first = template.replace(year=year, month=month, day=1, hour=21, minute=0, second=0, microsecond=0)
    return first + timedelta(days=(6 - first.weekday()) % 7)


def _next_monthly_refresh(now: datetime) -> datetime:
    candidate = _first_sunday(now.year, now.month, now)
    if candidate > now:
        return candidate
    year = now.year + (1 if now.month == 12 else 0)
    month = 1 if now.month == 12 else now.month + 1
    return _first_sunday(year, month, now)


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
                    collector.fetch_all_board_stocks(str(board["code"]), sector_name=str(board.get("name") or sector["label"])),
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
                collector.fetch_intelligent_selection_candidates(),
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
                "next_update": (_next_sunday(now) if pool_type == "short" else _next_monthly_refresh(now)).isoformat(),
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
