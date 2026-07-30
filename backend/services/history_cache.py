"""真实行情的一年期历史缓存和回补任务。"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import date, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database import async_session
from models import (
    CacheBackfillRun,
    ConceptFundFlowDaily,
    IndustryFundFlowDaily,
    MarketBoard,
    NorthboundDealDaily,
    StockDailyBar,
)
from services.data_collector import collector, normalize_stock_code, shanghai_now


def _parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _chunks(items: list, size: int) -> Iterable[list]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


class HistoryCacheService:
    """Caches source records only; failures remain explicit rather than synthetic."""

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}

    @staticmethod
    def _insert_for(session, model):
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            return postgresql_insert(model)
        return sqlite_insert(model)

    async def _upsert(self, model, rows: list[dict], keys: list[str]) -> int:
        if not rows:
            return 0
        async with async_session() as session:
            statement = self._insert_for(session, model).values(rows)
            updates = {
                column.name: getattr(statement.excluded, column.name)
                for column in model.__table__.columns
                if column.name not in {"id", *keys, "created_at"}
            }
            statement = statement.on_conflict_do_update(index_elements=keys, set_=updates)
            await session.execute(statement)
            await session.commit()
        return len(rows)

    async def _set_run(self, run_id: int, **values) -> None:
        async with async_session() as session:
            await session.execute(update(CacheBackfillRun).where(CacheBackfillRun.id == run_id).values(**values))
            await session.commit()

    async def queue_backfill(
        self,
        days: int = 365,
        include_stock_bars: bool = True,
        max_stocks: int | None = None,
    ) -> dict:
        async with async_session() as session:
            active = (await session.execute(
                select(CacheBackfillRun)
                .where(CacheBackfillRun.status.in_(("queued", "running")))
                .order_by(CacheBackfillRun.id.desc())
                .limit(1)
            )).scalar_one_or_none()
            if active is not None:
                return {
                    "run_id": active.id,
                    "status": active.status,
                    "days": active.requested_days,
                    "include_stock_bars": "stock-bars" in active.dataset,
                    "already_running": True,
                }

            run = CacheBackfillRun(
                dataset="concept,industry,northbound,stock-bars" if include_stock_bars else "concept,industry,northbound",
                requested_days=days,
                status="queued",
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)

        task = asyncio.create_task(self._run_backfill(run.id, days, include_stock_bars, max_stocks))
        self._tasks[run.id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run.id, None))
        return {"run_id": run.id, "status": "queued", "days": days, "include_stock_bars": include_stock_bars}

    async def resume_incomplete_runs(self) -> list[int]:
        """Resume persisted work after a web instance restart.

        Each write is an upsert, so re-fetching an already written batch is safe.
        """
        async with async_session() as session:
            runs = (await session.execute(
                select(CacheBackfillRun)
                .where(CacheBackfillRun.status.in_(("queued", "running")))
                .order_by(CacheBackfillRun.id.asc())
            )).scalars().all()

        resumed: list[int] = []
        for run in runs:
            if run.id in self._tasks:
                continue
            task = asyncio.create_task(
                self._run_backfill(
                    run.id,
                    run.requested_days,
                    "stock-bars" in run.dataset,
                    None,
                )
            )
            self._tasks[run.id] = task
            task.add_done_callback(lambda _, run_id=run.id: self._tasks.pop(run_id, None))
            resumed.append(run.id)
        return resumed

    async def _run_backfill(self, run_id: int, days: int, include_stock_bars: bool, max_stocks: int | None) -> None:
        try:
            await self._set_run(
                run_id,
                status="running",
                started_at=datetime.utcnow(),
                completed_tasks=0,
                records_written=0,
                error=None,
            )
            concept_boards, industry_boards = await asyncio.gather(
                collector.fetch_all_concept_flow(),
                collector.fetch_all_industry_flow(),
            )
            board_jobs = [("concept", row) for row in concept_boards] + [("industry", row) for row in industry_boards]
            stock_universe = await collector.fetch_stock_universe() if include_stock_bars else []
            if max_stocks is not None:
                stock_universe = stock_universe[:max_stocks]
            if include_stock_bars and not stock_universe:
                raise RuntimeError("全市场股票清单为空，未开始个股日线回补")

            await self._upsert(
                MarketBoard,
                [
                    {
                        "board_type": board_type,
                        "code": row["code"],
                        "name": row["name"],
                        "source": "eastmoney",
                        "updated_at": datetime.utcnow(),
                    }
                    for board_type, row in board_jobs
                    if row.get("code") and row.get("name")
                ],
                ["board_type", "code"],
            )
            await self._set_run(run_id, total_tasks=len(board_jobs) + len(stock_universe) + 1)

            records_written = 0
            stock_failures: list[str] = []
            if include_stock_bars:
                stock_written, stock_failures = await self._backfill_stock_bars(
                    run_id,
                    stock_universe,
                    days,
                    completed_offset=0,
                    initial_records_written=records_written,
                )
                records_written += stock_written

            north_written = await self._backfill_northbound(days)
            records_written += north_written
            north_offset = len(stock_universe) if include_stock_bars else 0
            await self._set_run(
                run_id,
                completed_tasks=north_offset + 1,
                records_written=records_written,
            )

            board_written, board_failures = await self._backfill_boards(
                run_id,
                board_jobs,
                days,
                completed_offset=north_offset + 1,
                initial_records_written=records_written,
            )
            records_written += board_written

            warnings = []
            if stock_failures:
                warnings.append(f"个股日线未完成 {len(stock_failures)} 只: {','.join(stock_failures[:10])}")
            if board_failures:
                warnings.append(f"板块资金流历史未完成 {board_failures} 个")
            if not north_written:
                warnings.append("北向历史未返回记录")

            await self._set_run(
                run_id,
                status="completed" if not warnings else "partial",
                completed_at=datetime.utcnow(),
                records_written=records_written,
                completed_tasks=len(board_jobs) + len(stock_universe) + 1,
                error="; ".join(warnings) or None,
            )
        except Exception as exc:
            await self._set_run(
                run_id,
                status="failed",
                completed_at=datetime.utcnow(),
                error=f"{type(exc).__name__}: {exc}",
            )

    async def _backfill_boards(
        self,
        run_id: int,
        board_jobs: list[tuple[str, dict]],
        days: int,
        completed_offset: int,
        initial_records_written: int,
    ) -> tuple[int, int]:
        semaphore = asyncio.Semaphore(8)

        async def fetch_one(board_type: str, board: dict):
            async with semaphore:
                try:
                    history = await collector.fetch_board_flow_history(board["code"], days)
                    return board_type, history, None
                except Exception as exc:
                    print(f"Backfill board {board.get('code')} failed: {type(exc).__name__}")
                    return board_type, None, type(exc).__name__

        responses = await asyncio.gather(*(fetch_one(board_type, board) for board_type, board in board_jobs))
        written = 0
        completed = completed_offset
        failures = 0
        for response_batch in _chunks(list(responses), 30):
            concept_rows: list[dict] = []
            industry_rows: list[dict] = []
            for board_type, payload, error in response_batch:
                completed += 1
                if error or not payload or not payload.get("history"):
                    failures += 1
                    continue
                target = concept_rows if board_type == "concept" else industry_rows
                for row in payload["history"]:
                    target.append({
                        "board_code": payload["code"],
                        "trade_date": _parse_date(row["trade_date"]),
                        "close_price": row["close_price"],
                        "change_pct": row["change_pct"],
                        "main_net_inflow": row["main_net_inflow"],
                        "main_net_inflow_pct": row["main_net_inflow_pct"],
                        "super_large_net_inflow": row["super_large_net_inflow"],
                        "large_net_inflow": row["large_net_inflow"],
                        "medium_net_inflow": row["medium_net_inflow"],
                        "small_net_inflow": row["small_net_inflow"],
                        "up_count": None,
                        "down_count": None,
                        "leading_stock": None,
                    })
            written += await self._upsert(ConceptFundFlowDaily, concept_rows, ["board_code", "trade_date"])
            written += await self._upsert(IndustryFundFlowDaily, industry_rows, ["board_code", "trade_date"])
            await self._set_run(
                run_id,
                completed_tasks=completed,
                records_written=initial_records_written + written,
            )
        return written, failures

    async def _backfill_northbound(self, days: int, source_rows: list[dict] | None = None) -> int:
        rows = []
        for item in source_rows if source_rows is not None else await collector.fetch_north_bound_daily(days):
            rows.append({
                "trade_date": _parse_date(item["date"]),
                "deal_amount": item["deal_amount"],
                "net_inflow": item["net_inflow"],
                "buy_amount": item["buy_amount"],
                "sell_amount": item["sell_amount"],
                "balance": item["balance"],
                "source": "eastmoney",
                "updated_at": datetime.utcnow(),
            })
        return await self._upsert(NorthboundDealDaily, rows, ["trade_date"])

    async def _backfill_stock_bars(
        self,
        run_id: int,
        stock_universe: list[dict],
        days: int,
        completed_offset: int,
        initial_records_written: int,
    ) -> tuple[int, list[str]]:
        cached_codes = await self._cached_stock_codes(days)
        pending_stocks = [stock for stock in stock_universe if stock["code"] not in cached_codes]
        semaphore = asyncio.Semaphore(10)

        async def fetch_one(stock: dict):
            async with semaphore:
                last_error = ""
                for attempt in range(3):
                    try:
                        data = await collector.fetch_stock_price_history(stock["code"], days)
                        if data.get("history"):
                            return stock, data, None
                        raise RuntimeError("empty history")
                    except Exception as exc:
                        last_error = type(exc).__name__
                        if attempt < 2:
                            await asyncio.sleep(0.5 * (attempt + 1))
                print(f"Backfill stock {stock.get('code')} failed: {last_error}")
                return stock, None, last_error

        written = 0
        failures: list[str] = []
        completed = completed_offset + len(stock_universe) - len(pending_stocks)
        await self._set_run(
            run_id,
            completed_tasks=completed,
            records_written=initial_records_written,
        )
        for source_batch in _chunks(pending_stocks, 40):
            results = await asyncio.gather(*(fetch_one(stock) for stock in source_batch))
            rows = []
            for stock, payload, error in results:
                completed += 1
                if error or not payload:
                    failures.append(stock["code"])
                    continue
                for bar in payload["history"]:
                    rows.append({
                        "stock_code": payload["code"],
                        "stock_name": payload.get("name") or stock.get("name"),
                        "market": str(stock.get("market") or ""),
                        "trade_date": _parse_date(bar["trade_date"]),
                        "open_price": bar["open"], "close_price": bar["close"], "high_price": bar["high"], "low_price": bar["low"],
                        "volume": bar["volume"], "amount": bar["amount"], "amplitude": bar["amplitude"],
                        "change_pct": bar["change_pct"], "change_amount": bar["change_amount"], "turnover": bar["turnover"],
                        "source": payload.get("source", "eastmoney"), "updated_at": datetime.utcnow(),
                    })
            written += await self._upsert(StockDailyBar, rows, ["stock_code", "trade_date"])
            await self._set_run(
                run_id,
                completed_tasks=completed,
                records_written=initial_records_written + written,
            )
        return written, failures

    async def _cached_stock_codes(self, days: int) -> set[str]:
        cutoff = shanghai_now().date() - timedelta(days=max(days, 1))
        async with async_session() as session:
            statement = select(StockDailyBar.stock_code).where(StockDailyBar.trade_date >= cutoff).distinct()
            return set((await session.execute(statement)).scalars().all())

    async def cache_current_concept_flow(self) -> dict:
        rows = await collector.fetch_all_concept_flow()
        if not rows:
            return {"status": "unavailable", "count": 0, "source": "eastmoney"}
        today = shanghai_now().date()
        await self._upsert(
            MarketBoard,
            [
                {"board_type": "concept", "code": row["code"], "name": row["name"], "source": "eastmoney", "updated_at": datetime.utcnow()}
                for row in rows
            ],
            ["board_type", "code"],
        )
        payload = [
            {
                "board_code": row["code"], "trade_date": today,
                "close_price": row["close_price"], "change_pct": row["change_pct"],
                "main_net_inflow": row["main_net_inflow"], "main_net_inflow_pct": row["main_net_inflow_pct"],
                "super_large_net_inflow": row["super_large_net_inflow"], "large_net_inflow": row["large_net_inflow"],
                "medium_net_inflow": row["medium_net_inflow"], "small_net_inflow": 0,
                "up_count": row["up_count"], "down_count": row["down_count"], "leading_stock": row["leading_stock"],
            }
            for row in rows
        ]
        count = await self._upsert(ConceptFundFlowDaily, payload, ["board_code", "trade_date"])
        return {"status": "success", "count": count, "source": "eastmoney", "trade_date": today.isoformat()}

    async def cache_current_industry_flow(self) -> dict:
        rows = await collector.fetch_all_industry_flow()
        if not rows:
            return {"status": "unavailable", "count": 0, "source": "eastmoney"}
        today = shanghai_now().date()
        await self._upsert(
            MarketBoard,
            [
                {"board_type": "industry", "code": row["code"], "name": row["name"], "source": "eastmoney", "updated_at": datetime.utcnow()}
                for row in rows
            ],
            ["board_type", "code"],
        )
        payload = [
            {
                "board_code": row["code"], "trade_date": today,
                "close_price": row["close_price"], "change_pct": row["change_pct"],
                "main_net_inflow": row["main_net_inflow"], "main_net_inflow_pct": row["main_net_inflow_pct"],
                "super_large_net_inflow": row["super_large_net_inflow"], "large_net_inflow": row["large_net_inflow"],
                "medium_net_inflow": row["medium_net_inflow"], "small_net_inflow": 0,
                "up_count": row["up_count"], "down_count": row["down_count"],
            }
            for row in rows
        ]
        count = await self._upsert(IndustryFundFlowDaily, payload, ["board_code", "trade_date"])
        return {"status": "success", "count": count, "source": "eastmoney", "trade_date": today.isoformat()}

    async def cache_current_northbound(self) -> dict:
        history = await collector.fetch_north_bound_daily(days=1)
        if not history:
            return {"status": "unavailable", "count": 0, "source": "eastmoney"}
        count = await self._backfill_northbound(1, history)
        return {"status": "success", "count": count, "source": "eastmoney", "trade_date": history[-1]["date"]}

    async def get_stock_history(self, stock_code: str, days: int = 365) -> list[dict]:
        code = normalize_stock_code(stock_code)
        async with async_session() as session:
            statement = (
                select(StockDailyBar)
                .where(StockDailyBar.stock_code == code)
                .order_by(StockDailyBar.trade_date.desc())
                .limit(days)
            )
            rows = list(reversed((await session.execute(statement)).scalars().all()))
        return [
            {
                "date": row.trade_date.isoformat(), "code": row.stock_code, "name": row.stock_name,
                "open": row.open_price, "close": row.close_price, "high": row.high_price, "low": row.low_price,
                "volume": row.volume, "amount": row.amount, "change_pct": row.change_pct,
                "change_amount": row.change_amount, "turnover": row.turnover,
            }
            for row in rows
        ]

    async def get_cache_stats(self) -> dict:
        async with async_session() as session:
            concept_count, concept_min, concept_max = (await session.execute(
                select(func.count(), func.min(ConceptFundFlowDaily.trade_date), func.max(ConceptFundFlowDaily.trade_date))
            )).one()
            stock_count, stock_min, stock_max = (await session.execute(
                select(func.count(), func.min(StockDailyBar.trade_date), func.max(StockDailyBar.trade_date))
            )).one()
            stock_symbols = (await session.execute(
                select(func.count(func.distinct(StockDailyBar.stock_code)))
            )).scalar_one()
            north_count, north_min, north_max = (await session.execute(
                select(func.count(), func.min(NorthboundDealDaily.trade_date), func.max(NorthboundDealDaily.trade_date))
            )).one()
            latest_run = (await session.execute(
                select(CacheBackfillRun).order_by(CacheBackfillRun.id.desc()).limit(1)
            )).scalar_one_or_none()
        return {
            "concept_flow": {"records": concept_count, "from": concept_min.isoformat() if concept_min else None, "to": concept_max.isoformat() if concept_max else None},
            "stock_bars": {
                "records": stock_count,
                "stocks": stock_symbols,
                "from": stock_min.isoformat() if stock_min else None,
                "to": stock_max.isoformat() if stock_max else None,
            },
            "northbound": {"records": north_count, "from": north_min.isoformat() if north_min else None, "to": north_max.isoformat() if north_max else None},
            "latest_run": None if latest_run is None else {
                "id": latest_run.id, "status": latest_run.status, "dataset": latest_run.dataset,
                "requested_days": latest_run.requested_days, "total_tasks": latest_run.total_tasks,
                "completed_tasks": latest_run.completed_tasks, "records_written": latest_run.records_written,
                "error": latest_run.error,
            },
        }

    async def get_run(self, run_id: int) -> dict | None:
        async with async_session() as session:
            run = await session.get(CacheBackfillRun, run_id)
        if run is None:
            return None
        return {
            "id": run.id, "status": run.status, "dataset": run.dataset, "requested_days": run.requested_days,
            "total_tasks": run.total_tasks, "completed_tasks": run.completed_tasks,
            "records_written": run.records_written, "error": run.error,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        }


history_cache = HistoryCacheService()
