"""真实行情的一年期历史缓存和回补任务。"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import DBAPIError

try:
    from asyncpg.exceptions import PostgresConnectionError
except ImportError:  # Local SQLite-only test environments need no asyncpg.
    class PostgresConnectionError(Exception):
        pass

from database import async_session, engine
from models import (
    CacheBackfillRun,
    ConceptFundFlowDaily,
    IndustryFundFlowDaily,
    MarketBoard,
    NorthboundDealDaily,
    StockDailyBar,
)
from services.data_collector import as_int, collector, normalize_stock_code, shanghai_now


def _parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _chunks(items: list, size: int) -> Iterable[list]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


class HistoryCacheService:
    """Caches source records only; failures remain explicit rather than synthetic."""

    # asyncpg rejects statements with 32,767 or more bound parameters.  Keep
    # a margin for dialect-specific generated binds, while retaining practical
    # batch sizes for the year-long market-wide backfill.
    _POSTGRES_BIND_BUDGET = 30_000
    _SQLITE_BIND_BUDGET = 900
    # Backfills make thousands of independent upstream requests. Bound every
    # request so a half-open proxy connection cannot freeze an entire run.
    _BACKFILL_REQUEST_TIMEOUT_SECONDS = 25
    _BACKFILL_FETCH_CONCURRENCY = 6
    _BACKFILL_BATCH_SIZE = 12
    _RECENT_STOCK_REFRESH_CONCURRENCY = 8
    _RECENT_STOCK_REFRESH_DAYS = 45
    # Render's free managed Postgres can take longer than the old ten-second
    # retry window to recover a transient DNS resolution failure.
    _DATABASE_OPERATION_ATTEMPTS = 8
    _BOARD_HISTORY_PROBE_CODE = "BK1034"

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}

    def _track_task(self, run_id: int, task: asyncio.Task) -> None:
        """Keep the in-memory task registry aligned with persisted work."""
        self._tasks[run_id] = task

        def clear_task(finished_task: asyncio.Task) -> None:
            self._tasks.pop(run_id, None)
            if finished_task.cancelled():
                return
            try:
                error = finished_task.exception()
            except asyncio.CancelledError:
                return
            if error is not None:
                print(f"Backfill run {run_id} stopped unexpectedly: {type(error).__name__}: {error}")

        task.add_done_callback(clear_task)

    @staticmethod
    def _insert_for(session, model):
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            return postgresql_insert(model)
        return sqlite_insert(model)

    @classmethod
    def _upsert_batch_size(cls, session, rows: list[dict]) -> int:
        """Return a bind-safe number of rows for one multi-value INSERT."""
        if not rows:
            return 1
        dialect = session.get_bind().dialect.name
        bind_budget = cls._POSTGRES_BIND_BUDGET if dialect == "postgresql" else cls._SQLITE_BIND_BUDGET
        columns_per_row = max(len(row) for row in rows)
        return max(1, bind_budget // max(columns_per_row, 1))

    async def _upsert(self, model, rows: list[dict], keys: list[str]) -> int:
        if not rows:
            return 0

        async def write_rows():
            async with async_session() as session:
                for batch in _chunks(rows, self._upsert_batch_size(session, rows)):
                    statement = self._insert_for(session, model).values(batch)
                    updates = {
                        column.name: getattr(statement.excluded, column.name)
                        for column in model.__table__.columns
                        if column.name not in {"id", *keys, "created_at"}
                    }
                    statement = statement.on_conflict_do_update(index_elements=keys, set_=updates)
                    await session.execute(statement)
                await session.commit()
            return len(rows)

        return await self._with_database_retry(write_rows)

    async def _set_run(self, run_id: int, **values) -> None:
        async def save_run():
            async with async_session() as session:
                await session.execute(update(CacheBackfillRun).where(CacheBackfillRun.id == run_id).values(**values))
                await session.commit()

        await self._with_database_retry(save_run)

    async def _with_database_retry(self, operation):
        """Retry idempotent cache writes after a transient Postgres disconnect."""
        for attempt in range(self._DATABASE_OPERATION_ATTEMPTS):
            try:
                return await operation()
            except (DBAPIError, OSError, PostgresConnectionError):
                if attempt + 1 >= self._DATABASE_OPERATION_ATTEMPTS:
                    raise
                await engine.dispose()
                await asyncio.sleep(attempt + 1)

    async def _request_with_deadline(self, awaitable):
        """Apply the backfill-specific deadline around an upstream request."""
        return await asyncio.wait_for(awaitable, timeout=self._BACKFILL_REQUEST_TIMEOUT_SECONDS)

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
        self._track_task(run.id, task)
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
            self._track_task(run.id, task)
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
            stock_universe = await self._request_with_deadline(collector.fetch_stock_universe()) if include_stock_bars else []
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

            # The directory response is the reliable source for a complete
            # current board snapshot. Store it before attempting the
            # separately hosted historical endpoint, which is often blocked
            # from overseas regions.
            records_written = await self._cache_current_board_snapshots(board_jobs)
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

            board_written, board_failures, board_history_issue = await self._backfill_boards(
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
            if board_history_issue == "snapshot_only":
                warnings.append("板块资金流上游仅返回当日快照，未将其标记为一年历史")
            elif board_history_issue == "request_failed":
                warnings.append("板块资金流历史上游请求失败，已保留当日完整快照")
            elif board_failures:
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
        except (DBAPIError, OSError, PostgresConnectionError, httpx.RequestError, asyncio.TimeoutError) as exc:
            # Preserve the persisted queued/running state after a transient
            # database or upstream-network failure. The scheduler will resume
            # this work once connectivity returns.
            print(f"Backfill run {run_id} paused after transient error: {type(exc).__name__}")
        except Exception as exc:
            try:
                await self._set_run(
                    run_id,
                    status="failed",
                    completed_at=datetime.utcnow(),
                    error=f"{type(exc).__name__}: {exc}",
                )
            except Exception as status_exc:
                # A later scheduler pass will see the still-running row and
                # resume it after the database becomes reachable again.
                print(
                    f"Backfill run {run_id} failed but status update failed: "
                    f"{type(status_exc).__name__}"
                )

    async def _backfill_boards(
        self,
        run_id: int,
        board_jobs: list[tuple[str, dict]],
        days: int,
        completed_offset: int,
        initial_records_written: int,
    ) -> tuple[int, int, str | None]:
        semaphore = asyncio.Semaphore(self._BACKFILL_FETCH_CONCURRENCY)

        async def fetch_one(board_type: str, board: dict):
            last_error = ""
            for attempt in range(3):
                try:
                    async with semaphore:
                        history = await self._request_with_deadline(
                            collector.fetch_board_flow_history(board["code"], days)
                        )
                    return board_type, history, None
                except Exception as exc:
                    last_error = type(exc).__name__
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (attempt + 1))
            print(f"Backfill board {board.get('code')} failed: {last_error}")
            return board_type, None, last_error

        if not board_jobs:
            return 0, 0, None

        # A stable industry board detects the overseas EastMoney behaviour:
        # push2his resets the connection and push2delay returns only today's
        # snapshot. One probe avoids issuing the same unusable request for
        # every board after a service restart.
        probe_index = next(
            (
                index
                for index, (_, board) in enumerate(board_jobs)
                if board.get("code") == self._BOARD_HISTORY_PROBE_CODE
            ),
            0,
        )
        probe_type, probe_board = board_jobs[probe_index]
        _, probe_payload, probe_error = await fetch_one(probe_type, probe_board)
        if probe_error:
            await self._set_run(
                run_id,
                completed_tasks=completed_offset + len(board_jobs),
                records_written=initial_records_written,
            )
            return 0, len(board_jobs), "request_failed"
        if not self._board_history_covers_window(probe_payload, days):
            await self._set_run(
                run_id,
                completed_tasks=completed_offset + len(board_jobs),
                records_written=initial_records_written,
            )
            return 0, 0, "snapshot_only"

        prefetched = {(probe_type, str(probe_board.get("code"))): probe_payload}

        async def fetch_pending(board_type: str, board: dict):
            key = (board_type, str(board.get("code")))
            payload = prefetched.pop(key, None)
            if payload is not None:
                return board_type, payload, None
            return await fetch_one(board_type, board)

        written = 0
        completed = completed_offset
        failures = 0
        for source_batch in _chunks(board_jobs, self._BACKFILL_BATCH_SIZE):
            response_batch = await asyncio.gather(
                *(fetch_pending(board_type, board) for board_type, board in source_batch)
            )
            concept_rows: list[dict] = []
            industry_rows: list[dict] = []
            for board_type, payload, error in response_batch:
                completed += 1
                if error or not payload or not payload.get("history"):
                    failures += 1
                    continue
                target = concept_rows if board_type == "concept" else industry_rows
                for row in payload["history"]:
                    record = {
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
                    }
                    if board_type == "concept":
                        record["leading_stock"] = None
                    target.append(record)
            written += await self._upsert(ConceptFundFlowDaily, concept_rows, ["board_code", "trade_date"])
            written += await self._upsert(IndustryFundFlowDaily, industry_rows, ["board_code", "trade_date"])
            await self._set_run(
                run_id,
                completed_tasks=completed,
                records_written=initial_records_written + written,
            )
        return written, failures, None

    @staticmethod
    def _board_history_covers_window(payload: dict | None, days: int) -> bool:
        """Require a genuine historical range instead of a fallback snapshot."""
        history = (payload or {}).get("history") or []
        if not history:
            return False
        if days <= 1:
            return True

        dates = []
        for row in history:
            try:
                dates.append(_parse_date(row["trade_date"]))
            except (KeyError, TypeError, ValueError):
                continue
        if len(dates) < 2:
            return False
        return min(dates) <= shanghai_now().date() - timedelta(days=days)

    async def _cache_current_board_snapshots(self, board_jobs: list[tuple[str, dict]]) -> int:
        """Persist the complete current directory without pretending it is history."""
        today = shanghai_now().date()
        concept_rows: list[dict] = []
        industry_rows: list[dict] = []
        for board_type, row in board_jobs:
            code = str(row.get("code") or "")
            if not code:
                continue
            record = {
                "board_code": code,
                "trade_date": today,
                "close_price": row.get("close_price"),
                "change_pct": row.get("change_pct"),
                "main_net_inflow": row.get("main_net_inflow"),
                "main_net_inflow_pct": row.get("main_net_inflow_pct"),
                "super_large_net_inflow": row.get("super_large_net_inflow"),
                "large_net_inflow": row.get("large_net_inflow"),
                "medium_net_inflow": row.get("medium_net_inflow"),
                "small_net_inflow": as_int(row.get("small_net_inflow")),
                "up_count": row.get("up_count"),
                "down_count": row.get("down_count"),
            }
            if board_type == "concept":
                record["leading_stock"] = row.get("leading_stock") or None
                concept_rows.append(record)
            else:
                industry_rows.append(record)

        written = await self._upsert(ConceptFundFlowDaily, concept_rows, ["board_code", "trade_date"])
        written += await self._upsert(IndustryFundFlowDaily, industry_rows, ["board_code", "trade_date"])
        return written

    async def _backfill_northbound(self, days: int, source_rows: list[dict] | None = None) -> int:
        if source_rows is None:
            try:
                source_rows = await self._request_with_deadline(collector.fetch_north_bound_daily(days))
            except Exception as exc:
                print(f"Backfill northbound failed: {type(exc).__name__}")
                return 0
        rows = []
        for item in source_rows:
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
        semaphore = asyncio.Semaphore(self._BACKFILL_FETCH_CONCURRENCY)

        async def fetch_one(stock: dict):
            last_error = ""
            for attempt in range(3):
                try:
                    async with semaphore:
                        data = await self._request_with_deadline(
                            collector.fetch_stock_price_history(stock["code"], days)
                        )
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
        for source_batch in _chunks(pending_stocks, self._BACKFILL_BATCH_SIZE):
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

    async def cache_stock_price_histories(self, payloads: list[tuple[dict, dict]]) -> int:
        """Persist on-demand strategy history using the same canonical bar schema.

        Quant backtests can fill a small missing subset without starting a
        second market-wide backfill. The existing unique key keeps this
        idempotent and preserves the source used for every bar.
        """
        rows = []
        for stock, payload in payloads:
            for bar in payload.get("history") or []:
                try:
                    trade_date = _parse_date(str(bar["trade_date"]))
                except (KeyError, TypeError, ValueError):
                    continue
                rows.append({
                    "stock_code": payload.get("code") or stock.get("code"),
                    "stock_name": payload.get("name") or stock.get("name"),
                    "market": str(stock.get("market") or ""),
                    "trade_date": trade_date,
                    "open_price": bar.get("open"), "close_price": bar.get("close"),
                    "high_price": bar.get("high"), "low_price": bar.get("low"),
                    "volume": bar.get("volume"), "amount": bar.get("amount"),
                    "amplitude": bar.get("amplitude"), "change_pct": bar.get("change_pct"),
                    "change_amount": bar.get("change_amount"), "turnover": bar.get("turnover"),
                    "source": payload.get("source", "tencent"), "updated_at": datetime.utcnow(),
                })
        return await self._upsert(StockDailyBar, rows, ["stock_code", "trade_date"])

    async def cache_current_stock_bars(self) -> dict:
        """Persist one complete, timestamp-verified A-share daily snapshot.

        The year-long backfill seeds the cache, but a completed backfill is
        not a substitute for adding each new trading day.  Reusing the
        code-sorted full-market quote snapshot keeps every stock on the same
        source date without issuing thousands of per-symbol requests.
        """
        try:
            snapshot = await collector.fetch_quant_market_snapshot(include_special=True)
        except Exception as exc:
            return {"status": "unavailable", "count": 0, "source": "eastmoney", "error": type(exc).__name__}

        raw_date = snapshot.get("data_date")
        try:
            trade_date = _parse_date(str(raw_date))
        except (TypeError, ValueError):
            return {
                "status": "unavailable",
                "count": 0,
                "source": str(snapshot.get("source") or "eastmoney"),
                "error": "quote_timestamp_missing",
            }

        # The same verified full-market close is the durable fallback used by
        # quantitative scans after a Render restart or an upstream outage.
        from quant.market_cache import save_quant_market_snapshot
        await save_quant_market_snapshot(snapshot)

        rows: list[dict] = []
        for stock in snapshot.get("stocks") or []:
            quote_at = collector._quote_timestamp_datetime(stock.get("quote_timestamp"))
            if quote_at is None or quote_at.date() != trade_date:
                continue
            close_price = stock.get("price")
            try:
                close_price = float(close_price)
            except (TypeError, ValueError):
                continue
            if close_price <= 0:
                continue

            previous_close = stock.get("previous_close")
            try:
                previous_close = float(previous_close)
            except (TypeError, ValueError):
                previous_close = None
            change_amount = stock.get("change_amount")
            if change_amount in (None, "", "-") and previous_close not in (None, 0):
                change_amount = close_price - previous_close

            code = str(stock.get("code") or "")
            market = "SH" if code.startswith(("6", "9")) else "BJ" if code.startswith(("4", "8")) else "SZ"
            rows.append({
                "stock_code": code,
                "stock_name": str(stock.get("name") or ""),
                "market": market,
                "trade_date": trade_date,
                "open_price": stock.get("open"),
                "close_price": close_price,
                "high_price": stock.get("high"),
                "low_price": stock.get("low"),
                "volume": stock.get("volume"),
                "amount": stock.get("amount"),
                "amplitude": stock.get("amplitude"),
                "change_pct": stock.get("change_pct"),
                "change_amount": change_amount,
                "turnover": stock.get("turnover"),
                "source": str(snapshot.get("source") or "eastmoney"),
                "updated_at": datetime.utcnow(),
            })

        if not rows:
            return {
                "status": "unavailable",
                "count": 0,
                "source": str(snapshot.get("source") or "eastmoney"),
                "data_date": trade_date.isoformat(),
                "error": "no_verified_quote_rows",
            }

        written = await self._upsert(StockDailyBar, rows, ["stock_code", "trade_date"])
        total = len(snapshot.get("stocks") or [])
        return {
            "status": "success" if not total or len(rows) == total else "partial",
            "count": written,
            "verified_stocks": len(rows),
            "total_stocks": total,
            "source": str(snapshot.get("source") or "eastmoney"),
            "data_date": trade_date.isoformat(),
            "source_updated_at": snapshot.get("source_updated_at"),
            "is_realtime": bool(snapshot.get("is_realtime")),
        }

    async def _latest_stock_bar_dates(self, stock_codes: list[str]) -> dict[str, date]:
        if not stock_codes:
            return {}

        async def load_dates():
            async with async_session() as session:
                result = await session.execute(
                    select(StockDailyBar.stock_code, func.max(StockDailyBar.trade_date))
                    .where(StockDailyBar.stock_code.in_(stock_codes))
                    .group_by(StockDailyBar.stock_code)
                )
                return {
                    str(code): trade_date
                    for code, trade_date in result.all()
                    if trade_date is not None
                }

        return await self._with_database_retry(load_dates)

    async def _latest_cached_stock_bar_date(self) -> date | None:
        """Return the latest market date already verified in stock daily bars."""

        async def load_date():
            async with async_session() as session:
                return (
                    await session.execute(select(func.max(StockDailyBar.trade_date)))
                ).scalar_one_or_none()

        return await self._with_database_retry(load_date)

    async def _clear_stale_current_board_snapshot(self, model, source_trade_date: date) -> int:
        """Remove a pre-market row that was previously stamped with today."""
        current_date = shanghai_now().date()
        if source_trade_date >= current_date:
            return 0

        async def clear_rows():
            async with async_session() as session:
                result = await session.execute(
                    delete(model).where(model.trade_date == current_date)
                )
                await session.commit()
                return int(result.rowcount or 0)

        return await self._with_database_retry(clear_rows)

    async def refresh_recent_stock_histories(
        self,
        stocks: list[dict],
        expected_date: str | None,
        days: int | None = None,
    ) -> dict:
        """Top up only stale selection candidates through the daily-bar source."""
        try:
            target_date = _parse_date(str(expected_date)) if expected_date else None
        except (TypeError, ValueError):
            target_date = None
        if target_date is None:
            return {"status": "skipped", "expected_date": None, "requested": 0, "refreshed": 0, "current": 0, "failed": []}

        by_code: dict[str, dict] = {}
        for stock in stocks:
            try:
                code = normalize_stock_code(stock.get("code"))
            except ValueError:
                continue
            by_code.setdefault(code, dict(stock))
        latest_dates = await self._latest_stock_bar_dates(list(by_code))
        pending = [
            stock for code, stock in by_code.items()
            if latest_dates.get(code) is None or latest_dates[code] < target_date
        ]
        if not pending:
            return {
                "status": "current",
                "expected_date": target_date.isoformat(),
                "requested": len(by_code),
                "refreshed": 0,
                "current": len(by_code),
                "failed": [],
            }

        refresh_days = min(max(int(days or self._RECENT_STOCK_REFRESH_DAYS), 5), 90)
        semaphore = asyncio.Semaphore(self._RECENT_STOCK_REFRESH_CONCURRENCY)

        async def fetch_one(stock: dict) -> tuple[dict, dict | None, str | None]:
            code = stock["code"]
            try:
                async with semaphore:
                    payload = await self._request_with_deadline(
                        collector.fetch_stock_price_history(code, refresh_days)
                    )
                if not payload.get("history"):
                    return stock, None, "empty_history"
                return stock, payload, None
            except Exception as exc:
                return stock, None, type(exc).__name__

        results = await asyncio.gather(*(fetch_one(stock) for stock in pending))
        payloads = [(stock, payload) for stock, payload, error in results if payload is not None and error is None]
        written = await self.cache_stock_price_histories(payloads)
        refreshed_codes = {
            str(payload.get("code") or stock.get("code"))
            for stock, payload in payloads
            if payload is not None
        }
        failed = [
            str(stock.get("code") or "")
            for stock, payload, error in results
            if payload is None or error is not None
        ]
        return {
            "status": "success" if not failed else "partial",
            "expected_date": target_date.isoformat(),
            "requested": len(by_code),
            "refreshed": len(refreshed_codes),
            "current": len(by_code) - len(pending),
            "records_written": written,
            "failed": failed,
        }

    async def _cached_stock_codes(self, days: int) -> set[str]:
        cutoff = shanghai_now().date() - timedelta(days=max(days, 1))
        recent_cutoff = shanghai_now().date() - timedelta(days=7)

        async def load_codes():
            async with async_session() as session:
                statement = (
                    select(StockDailyBar.stock_code)
                    .group_by(StockDailyBar.stock_code)
                    .having(func.min(StockDailyBar.trade_date) <= cutoff)
                    .having(func.max(StockDailyBar.trade_date) >= recent_cutoff)
                )
                return set((await session.execute(statement)).scalars().all())

        codes = await self._with_database_retry(load_codes)
        # The corrected Tencent parser has rewritten the previously affected
        # STAR Market rows. Treat those cached symbols like every other code
        # so a process restart resumes only genuinely missing histories.
        return codes

    async def cache_current_concept_flow(
        self,
        trade_date: date | None = None,
        *,
        verified_trade_date: bool = False,
    ) -> dict:
        trade_date = trade_date or await self._latest_cached_stock_bar_date()
        if trade_date is None:
            return {
                "status": "unavailable",
                "count": 0,
                "source": "eastmoney",
                "error": "market_date_unverified",
            }
        rows = await collector.fetch_all_concept_flow()
        if not rows:
            return {"status": "unavailable", "count": 0, "source": "eastmoney"}
        cleared_stale_rows = (
            await self._clear_stale_current_board_snapshot(ConceptFundFlowDaily, trade_date)
            if verified_trade_date
            else 0
        )
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
                "board_code": row["code"], "trade_date": trade_date,
                "close_price": row["close_price"], "change_pct": row["change_pct"],
                "main_net_inflow": row["main_net_inflow"], "main_net_inflow_pct": row["main_net_inflow_pct"],
                "super_large_net_inflow": row["super_large_net_inflow"], "large_net_inflow": row["large_net_inflow"],
                "medium_net_inflow": row["medium_net_inflow"], "small_net_inflow": as_int(row.get("small_net_inflow")),
                "up_count": row["up_count"], "down_count": row["down_count"], "leading_stock": row["leading_stock"],
            }
            for row in rows
        ]
        count = await self._upsert(ConceptFundFlowDaily, payload, ["board_code", "trade_date"])
        return {
            "status": "success",
            "count": count,
            "source": "eastmoney",
            "trade_date": trade_date.isoformat(),
            "cleared_stale_rows": cleared_stale_rows,
        }

    async def cache_current_industry_flow(
        self,
        trade_date: date | None = None,
        *,
        verified_trade_date: bool = False,
    ) -> dict:
        trade_date = trade_date or await self._latest_cached_stock_bar_date()
        if trade_date is None:
            return {
                "status": "unavailable",
                "count": 0,
                "source": "eastmoney",
                "error": "market_date_unverified",
            }
        rows = await collector.fetch_all_industry_flow()
        if not rows:
            return {"status": "unavailable", "count": 0, "source": "eastmoney"}
        cleared_stale_rows = (
            await self._clear_stale_current_board_snapshot(IndustryFundFlowDaily, trade_date)
            if verified_trade_date
            else 0
        )
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
                "board_code": row["code"], "trade_date": trade_date,
                "close_price": row["close_price"], "change_pct": row["change_pct"],
                "main_net_inflow": row["main_net_inflow"], "main_net_inflow_pct": row["main_net_inflow_pct"],
                "super_large_net_inflow": row["super_large_net_inflow"], "large_net_inflow": row["large_net_inflow"],
                "medium_net_inflow": row["medium_net_inflow"], "small_net_inflow": as_int(row.get("small_net_inflow")),
                "up_count": row["up_count"], "down_count": row["down_count"],
            }
            for row in rows
        ]
        count = await self._upsert(IndustryFundFlowDaily, payload, ["board_code", "trade_date"])
        return {
            "status": "success",
            "count": count,
            "source": "eastmoney",
            "trade_date": trade_date.isoformat(),
            "cleared_stale_rows": cleared_stale_rows,
        }

    async def cache_current_northbound(self) -> dict:
        history = await collector.fetch_north_bound_daily(days=1)
        if not history:
            return {"status": "unavailable", "count": 0, "source": "eastmoney"}
        count = await self._backfill_northbound(1, history)
        return {"status": "success", "count": count, "source": "eastmoney", "trade_date": history[-1]["date"]}

    async def get_stock_history(self, stock_code: str, days: int = 365) -> list[dict]:
        code = normalize_stock_code(stock_code)

        async def load_history():
            async with async_session() as session:
                statement = (
                    select(StockDailyBar)
                    .where(StockDailyBar.stock_code == code)
                    .order_by(StockDailyBar.trade_date.desc())
                    .limit(days)
                )
                return list(reversed((await session.execute(statement)).scalars().all()))

        rows = await self._with_database_retry(load_history)
        return [
            {
                "date": row.trade_date.isoformat(), "code": row.stock_code, "name": row.stock_name,
                "open": row.open_price, "close": row.close_price, "high": row.high_price, "low": row.low_price,
                "volume": row.volume, "amount": row.amount, "change_pct": row.change_pct,
                "change_amount": row.change_amount, "turnover": row.turnover,
            }
            for row in rows
        ]

    async def _board_cache_stats(self, session, model, board_type: str) -> dict:
        """Report coverage separately from the aggregate row count."""
        record_count, first_date, last_date = (await session.execute(
            select(func.count(), func.min(model.trade_date), func.max(model.trade_date))
        )).one()
        board_count = (await session.execute(
            select(func.count(func.distinct(model.board_code)))
        )).scalar_one()
        date_count = (await session.execute(
            select(func.count(func.distinct(model.trade_date)))
        )).scalar_one()

        current_date = shanghai_now().date()
        latest_snapshot_boards = 0
        if last_date is not None:
            latest_snapshot_boards = (await session.execute(
                select(func.count(func.distinct(model.board_code))).where(model.trade_date == last_date)
            )).scalar_one()
        directory_boards = (await session.execute(
            select(func.count()).select_from(MarketBoard).where(MarketBoard.board_type == board_type)
        )).scalar_one()

        # At least 200 market sessions plus an earliest record one year back
        # is a deliberately conservative definition of a complete yearly
        # board history. Sparse rows must not be presented as full coverage.
        year_cutoff = current_date - timedelta(days=365)
        covered_boards = (
            select(model.board_code)
            .group_by(model.board_code)
            .having(func.min(model.trade_date) <= year_cutoff)
            .having(func.count(func.distinct(model.trade_date)) >= 200)
            .subquery()
        )
        year_history_boards = (await session.execute(
            select(func.count()).select_from(covered_boards)
        )).scalar_one()

        return {
            "records": record_count,
            "boards": board_count,
            "dates": date_count,
            "from": first_date.isoformat() if first_date else None,
            "to": last_date.isoformat() if last_date else None,
            "coverage": {
                "latest_snapshot_boards": latest_snapshot_boards,
                "latest_snapshot_date": last_date.isoformat() if last_date else None,
                # Backward-compatible alias used by the existing dashboard.
                # It now means the latest verified market snapshot, rather
                # than the calendar date on a pre-market or holiday refresh.
                "today_snapshot_boards": latest_snapshot_boards,
                "directory_boards": directory_boards,
                "year_history_boards": year_history_boards,
                "year_history_complete": bool(directory_boards) and year_history_boards >= directory_boards,
            },
        }

    async def get_cache_stats(self) -> dict:
        async def load_stats():
            async with async_session() as session:
                concept_stats = await self._board_cache_stats(session, ConceptFundFlowDaily, "concept")
                industry_stats = await self._board_cache_stats(session, IndustryFundFlowDaily, "industry")
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
            return (
                concept_stats, industry_stats,
                stock_count, stock_min, stock_max, stock_symbols,
                north_count, north_min, north_max, latest_run,
            )

        (
            concept_stats, industry_stats,
            stock_count, stock_min, stock_max, stock_symbols,
            north_count, north_min, north_max, latest_run,
        ) = await self._with_database_retry(load_stats)
        return {
            "concept_flow": concept_stats,
            "industry_flow": industry_stats,
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
        async def load_run():
            async with async_session() as session:
                return await session.get(CacheBackfillRun, run_id)

        run = await self._with_database_retry(load_run)
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
