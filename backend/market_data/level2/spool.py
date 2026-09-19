"""Bounded, disposable storage for one Level-2 fetch.

The spool deliberately has no resume semantics.  It keeps only normalized
records for the lifetime of a fetch and leaves the durable repository to store
the small job metadata and derived feature rows.
"""

from __future__ import annotations

import dataclasses
import json
import math
import sqlite3
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator
from zoneinfo import ZoneInfo

from .models import BookLevel, OrderBookSnapshot, OrderTick, TradeTick
from .repository import _json_safe, _source_id


class Level2SpoolCapacityError(RuntimeError):
    """Raised when the temporary spool reaches its configured disk bound."""


class Level2Spool:
    """A disk-backed, single-symbol/single-date Level-2 working set."""

    ROWS_PER_KIND_PER_MINUTE = 10_000
    MAX_BYTES = 128 * 1024 * 1024
    SQLITE_PAGE_SIZE = 4096
    SHANGHAI = ZoneInfo("Asia/Shanghai")

    def __init__(self, delegate_repository: Any, symbol: str, trade_date: date) -> None:
        self.delegate_repository = delegate_repository
        self.symbol = symbol
        self.trade_date = trade_date
        self.rejected_rows = 0
        self._closed = False
        self._truncation_cache: list[str] | None = None
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="level2-spool-")
        self.directory = Path(self._temporary_directory.name)
        self.db_path = self.directory / "spool.sqlite3"
        try:
            self._connection = sqlite3.connect(str(self.db_path))
            self._connection.execute(f"PRAGMA page_size = {self.SQLITE_PAGE_SIZE}")
            self._connection.execute(f"PRAGMA max_page_count = {self.MAX_BYTES // self.SQLITE_PAGE_SIZE}")
            self._connection.execute("PRAGMA cache_size = -2048")
            self._connection.execute("PRAGMA temp_store = FILE")
            self._connection.execute("PRAGMA journal_mode = DELETE")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            self._connection.executescript(
                """
                CREATE TABLE records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    minute TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    amount REAL,
                    price REAL,
                    volume REAL,
                    depth_observations INTEGER NOT NULL DEFAULT 0,
                    full_depth INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(kind, source_id)
                );
                CREATE INDEX idx_level2_spool_kind_timestamp
                    ON records(kind, timestamp, id);
                CREATE INDEX idx_level2_spool_minute_kind_timestamp
                    ON records(minute, kind, timestamp, id);
                """
            )
            self._connection.commit()
        except Exception:
            self._cleanup()
            raise

    def __enter__(self) -> "Level2Spool":
        self._ensure_open()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._connection.close()
        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        temporary_directory = getattr(self, "_temporary_directory", None)
        if temporary_directory is not None:
            temporary_directory.cleanup()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Level2 spool is closed")

    async def get_job(self, symbol: str, trade_date: date, data_type: str) -> None:
        """A spool never resumes an earlier process."""
        return None

    async def save_job(self, values: dict[str, Any]) -> None:
        """Forward only the small fetch cursor/status metadata."""
        self._ensure_open()
        await self.delegate_repository.save_job(values)

    async def save_trades(self, rows: Iterable[TradeTick]) -> int:
        return self._save_rows("trade", rows)

    async def save_orders(self, rows: Iterable[OrderTick]) -> int:
        return self._save_rows("order", rows)

    async def save_quotes(self, rows: Iterable[OrderBookSnapshot]) -> int:
        return self._save_rows("quote", rows)

    def _save_rows(self, kind: str, rows: Iterable[Any]) -> int:
        self._ensure_open()
        written = 0
        try:
            for row in rows:
                normalized = self._prepare_row(kind, row)
                if normalized is None:
                    self.rejected_rows += 1
                    continue
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO records (
                        kind, symbol, trade_date, timestamp, minute, source_id,
                        payload_json, amount, price, volume,
                        depth_observations, full_depth
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    normalized,
                )
                written += int(cursor.rowcount == 1)
            self._connection.commit()
        except sqlite3.DatabaseError as exc:
            self._connection.rollback()
            message = str(exc).lower()
            if "full" in message or "max_page_count" in message:
                raise Level2SpoolCapacityError(
                    f"Level2 spool reached its {self.MAX_BYTES // (1024 * 1024)} MiB disk cap"
                ) from exc
            raise RuntimeError(f"Level2 spool database write failed: {exc}") from exc
        self._truncation_cache = None
        return written

    def _prepare_row(self, kind: str, row: Any) -> tuple[Any, ...] | None:
        expected = {"trade": TradeTick, "order": OrderTick, "quote": OrderBookSnapshot}[kind]
        if not isinstance(row, expected):
            return None
        timestamp = getattr(row, "timestamp", None)
        if not isinstance(timestamp, datetime):
            return None
        local_timestamp = _local_timestamp(timestamp)
        if (
            getattr(row, "symbol", None) != self.symbol
            or getattr(row, "trade_date", None) != self.trade_date
            or local_timestamp.date() != self.trade_date
        ):
            return None

        raw = _json_safe(getattr(row, "raw", {}))
        explicit_id = None
        if kind == "trade":
            explicit_id = row.trade_id
        elif kind == "order":
            explicit_id = row.order_id or row.order_no
        source_id = _source_id(explicit_id, timestamp, raw, kind)
        payload = _serialize_without_raw(row)
        payload["timestamp"] = _timestamp_key(timestamp)
        payload_json = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        timestamp_key = _timestamp_key(timestamp)
        minute = timestamp_key[:16] + ":00"

        amount = price = volume = None
        depth_observations = full_depth = 0
        if kind == "trade":
            price = _finite_float(row.price)
            volume = _finite_float(row.volume)
            amount = _effective_amount(row.amount, price, volume)
        elif kind == "order":
            price = _finite_float(row.price)
            volume = _finite_float(row.volume)
            amount = _effective_amount(row.amount, price, volume)
        else:
            depth_observations = sum(
                level.price is not None and level.volume is not None
                for level in [*(row.bids or []), *(row.asks or [])]
            )
            full_depth = int(depth_observations >= 16)
        return (
            kind,
            self.symbol,
            self.trade_date.isoformat(),
            timestamp_key,
            minute,
            source_id,
            payload_json,
            amount,
            price,
            volume,
            depth_observations,
            full_depth,
        )

    def amount_threshold(self) -> float | None:
        """Return the exact SQL-backed 85th percentile of valid trade amounts."""
        self._ensure_open()
        count = int(self._connection.execute(
            "SELECT COUNT(*) FROM records WHERE kind = 'trade' AND amount > 0"
        ).fetchone()[0])
        if not count:
            return None
        position = (count - 1) * 85 / 100
        lower = math.floor(position)
        upper = math.ceil(position)
        values = self._connection.execute(
            """
            SELECT amount FROM records
            WHERE kind = 'trade' AND amount > 0
            ORDER BY amount ASC
            LIMIT ? OFFSET ?
            """,
            (upper - lower + 1, lower),
        ).fetchall()
        if not values:
            return None
        low = float(values[0][0])
        if lower == upper:
            return low
        high = float(values[upper - lower][0])
        return low + (high - low) * (position - lower)

    def iter_minutes(self) -> Iterator[tuple[datetime, list[TradeTick], list[OrderTick], list[OrderBookSnapshot]]]:
        """Yield chronological minute batches, bounded to 10,000 rows per kind."""
        self._ensure_open()
        self._refresh_truncation()
        minute_cursor = self._connection.execute("SELECT minute FROM records GROUP BY minute ORDER BY minute ASC")
        for (minute_code,) in minute_cursor:
            minute = datetime.fromisoformat(minute_code)
            start = minute_code
            end = _timestamp_key(minute + timedelta(minutes=1))
            yield (
                minute,
                self._load_kind("trade", start, end),
                self._load_kind("order", start, end),
                self._load_kind("quote", start, end),
            )

    def _load_kind(self, kind: str, start: str, end: str) -> list[Any]:
        rows = self._connection.execute(
            """
            SELECT payload_json FROM records
            WHERE kind = ? AND timestamp >= ? AND timestamp < ?
            ORDER BY timestamp ASC, id ASC
            LIMIT ?
            """,
            (kind, start, end, self.ROWS_PER_KIND_PER_MINUTE),
        ).fetchall()
        return [_deserialize(kind, json.loads(payload_json)) for (payload_json,) in rows]

    @property
    def truncated_minutes(self) -> int:
        self._ensure_open()
        self._refresh_truncation()
        return len(self._truncation_cache or [])

    @property
    def truncated_minute_codes(self) -> list[str]:
        self._ensure_open()
        self._refresh_truncation()
        return list(self._truncation_cache or [])

    @property
    def truncated_codes(self) -> list[str]:
        return self.truncated_minute_codes

    def _refresh_truncation(self) -> None:
        if self._truncation_cache is not None:
            return
        rows = self._connection.execute(
            """
            SELECT minute
            FROM (
                SELECT minute, kind
                FROM records
                GROUP BY minute, kind
                HAVING COUNT(*) > ?
            )
            GROUP BY minute
            ORDER BY minute ASC
            """,
            (self.ROWS_PER_KIND_PER_MINUTE,),
        ).fetchall()
        self._truncation_cache = [row[0] for row in rows]

    def stats(self) -> dict[str, Any]:
        self._ensure_open()
        counts = dict(self._connection.execute(
            "SELECT kind, COUNT(*) FROM records GROUP BY kind"
        ).fetchall())
        first, last = self._connection.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM records"
        ).fetchone()
        depth_observations, full_depth = self._connection.execute(
            "SELECT COALESCE(SUM(depth_observations), 0), COALESCE(SUM(full_depth), 0) FROM records WHERE kind = 'quote'"
        ).fetchone()
        inconsistent = self._connection.execute(
            "SELECT EXISTS(SELECT 1 FROM records WHERE substr(timestamp, 1, 10) != trade_date)"
        ).fetchone()[0]
        return {
            "trade_count": int(counts.get("trade", 0)),
            "order_count": int(counts.get("order", 0)),
            "quote_count": int(counts.get("quote", 0)),
            "first_timestamp": datetime.fromisoformat(first) if first else None,
            "last_timestamp": datetime.fromisoformat(last) if last else None,
            "quote_depth_observations": int(depth_observations or 0),
            "full_depth_quote_count": int(full_depth or 0),
            "trade_date_consistent": not bool(inconsistent),
        }


def _serialize_without_raw(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: _serialize_without_raw(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if field.name != "raw"
        }
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _serialize_without_raw(item) for key, item in value.items() if key != "raw"}
    if isinstance(value, (list, tuple)):
        return [_serialize_without_raw(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    return str(value)


def _timestamp_key(value: datetime) -> str:
    return _local_timestamp(value).isoformat(timespec="microseconds")


def _local_timestamp(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(Level2Spool.SHANGHAI).replace(tzinfo=None)
    return value.replace(tzinfo=None)


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _effective_amount(amount: Any, price: float | None, volume: float | None) -> float | None:
    direct = _finite_float(amount)
    if direct is not None and direct > 0:
        return direct
    if price is not None and volume is not None and price > 0 and volume > 0:
        result = price * volume
        if math.isfinite(result):
            return result
    return None


def _deserialize(kind: str, payload: dict[str, Any]) -> Any:
    payload = dict(payload)
    payload["trade_date"] = date.fromisoformat(payload["trade_date"])
    payload["timestamp"] = datetime.fromisoformat(payload["timestamp"])
    if kind == "trade":
        return TradeTick(**payload)
    if kind == "order":
        return OrderTick(**payload)
    payload["bids"] = [_deserialize_level(level) for level in payload.get("bids", [])]
    payload["asks"] = [_deserialize_level(level) for level in payload.get("asks", [])]
    return OrderBookSnapshot(**payload)


def _deserialize_level(value: dict[str, Any]) -> BookLevel:
    return BookLevel(
        price=_finite_float(value.get("price")),
        volume=_finite_float(value.get("volume")),
        level=int(value.get("level") or 0),
    )
