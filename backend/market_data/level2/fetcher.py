"""Bounded, resumable cursor fetcher for Level-2 historical records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

from config import settings

from .normalizer import normalize_order_row, normalize_quote_row, normalize_trade_row
from .providers.base import Level2DataType, Level2Provider
from .repository import Level2Repository


@dataclass(slots=True)
class FetchResult:
    symbol: str
    trade_date: date
    statuses: dict[str, str] = field(default_factory=dict)
    rows: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    pagination_complete: dict[str, bool] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return bool(self.statuses) and all(value == "completed" for value in self.statuses.values())


class Level2Fetcher:
    DATA_TYPES: tuple[Level2DataType, ...] = ("trade", "order", "quote")

    def __init__(self, provider: Level2Provider, repository: Level2Repository | None = None) -> None:
        self.provider = provider
        self.repository = repository or Level2Repository()

    @staticmethod
    def _max_pages() -> int:
        try:
            return min(max(int(settings.level2_max_pages), 1), 10_000)
        except (TypeError, ValueError):
            return 200

    @staticmethod
    def _max_rows() -> int:
        try:
            return min(max(int(settings.level2_max_rows), 1_000), 2_000_000)
        except (TypeError, ValueError):
            return 500_000

    async def run(
        self,
        symbol: str,
        trade_date: date,
        *,
        data_types: Iterable[Level2DataType] | None = None,
        force: bool = False,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> FetchResult:
        result = FetchResult(symbol=symbol, trade_date=trade_date)
        selected = tuple(dict.fromkeys(data_types or self.DATA_TYPES))
        for data_type in selected:
            await self._run_type(
                result, data_type, force=force, start_time=start_time, end_time=end_time,
            )
        return result

    async def _run_type(
        self,
        result: FetchResult,
        data_type: Level2DataType,
        *,
        force: bool,
        start_time: str | None,
        end_time: str | None,
    ) -> None:
        job = await self.repository.get_job(result.symbol, result.trade_date, data_type)
        if job and job.status == "completed" and not force:
            result.statuses[data_type] = "completed"
            result.rows[data_type] = int(job.rows or 0)
            result.pagination_complete[data_type] = True
            return

        now = datetime.utcnow()
        cursor = None if force or job is None else job.cursor
        pages = 0 if force or job is None else int(job.pages or 0)
        rows_seen = 0 if force or job is None else int(job.rows or 0)
        await self.repository.save_job({
            "symbol": result.symbol,
            "trade_date": result.trade_date,
            "data_type": data_type,
            "provider": self.provider.name,
            "cursor": cursor,
            "status": "running",
            "pages": pages,
            "rows": rows_seen,
            "error": None,
            "started_at": now if job is None or force else (job.started_at or now),
            "completed_at": None,
        })
        seen_cursors: set[str] = set()
        try:
            while True:
                if pages >= self._max_pages():
                    raise RuntimeError("Level-2页数达到安全上限，已保留断点")
                page = await self.provider.fetch_page(
                    data_type,
                    result.symbol,
                    result.trade_date,
                    cursor=cursor,
                    start_time=start_time,
                    end_time=end_time,
                )
                normalized = self._normalize_page(data_type, page.rows, result.symbol, result.trade_date, page.fields)
                if data_type == "trade":
                    written = await self.repository.save_trades(normalized)
                elif data_type == "order":
                    written = await self.repository.save_orders(normalized)
                else:
                    written = await self.repository.save_quotes(normalized)
                rows_seen += written
                pages += 1
                next_cursor = page.next_cursor if page.has_more else None
                if rows_seen >= self._max_rows():
                    raise RuntimeError("Level-2行数达到安全上限，已保留断点")
                if next_cursor and (next_cursor in seen_cursors or next_cursor == cursor):
                    raise RuntimeError("Level-2返回重复游标，已停止以避免死循环")
                if next_cursor:
                    seen_cursors.add(next_cursor)
                await self.repository.save_job({
                    "symbol": result.symbol,
                    "trade_date": result.trade_date,
                    "data_type": data_type,
                    "provider": self.provider.name,
                    "cursor": next_cursor,
                    "status": "running" if next_cursor else "completed",
                    "pages": pages,
                    "rows": rows_seen,
                    "error": None,
                    "started_at": now if job is None or force else (job.started_at or now),
                    "completed_at": None if next_cursor else datetime.utcnow(),
                })
                if not next_cursor:
                    result.statuses[data_type] = "completed"
                    result.rows[data_type] = rows_seen
                    result.pagination_complete[data_type] = True
                    break
                cursor = next_cursor
        except Exception as exc:
            error = str(exc)
            await self.repository.save_job({
                "symbol": result.symbol,
                "trade_date": result.trade_date,
                "data_type": data_type,
                "provider": self.provider.name,
                "cursor": cursor,
                "status": "partial" if rows_seen else "failed",
                "pages": pages,
                "rows": rows_seen,
                "error": error[:500],
                "started_at": now if job is None or force else (job.started_at or now),
                "completed_at": None,
            })
            result.statuses[data_type] = "partial" if rows_seen else "failed"
            result.rows[data_type] = rows_seen
            result.errors[data_type] = error[:500]
            result.pagination_complete[data_type] = False

    @staticmethod
    def _normalize_page(data_type: Level2DataType, rows: list[dict[str, Any]], symbol: str, trade_date: date, fields: list[str]):
        output = []
        for row in rows:
            try:
                if data_type == "trade":
                    output.append(normalize_trade_row(row, fields, symbol=symbol, trade_date=trade_date))
                elif data_type == "order":
                    output.append(normalize_order_row(row, fields, symbol=symbol, trade_date=trade_date))
                else:
                    output.append(normalize_quote_row(row, fields, symbol=symbol, trade_date=trade_date))
            except (TypeError, ValueError, KeyError):
                # A malformed vendor row is excluded from analytics, while the
                # fetch job still records the page and exposes quality loss.
                continue
        return output
