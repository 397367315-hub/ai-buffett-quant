"""Persistence for normalized Level-2 rows and derived one-minute features."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Iterable

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database import async_session
from models import (
    Level2FetchJob,
    Level2Feature1m,
    Level2OrderHistory,
    Level2QualitySnapshot,
    Level2QuoteHistory,
    Level2TradeHistory,
)

from .models import BookLevel, OrderBookSnapshot, OrderTick, TradeTick
from .normalizer import timestamp_key


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    try:
        return json.loads(json.dumps(value, ensure_ascii=True))
    except (TypeError, ValueError):
        return str(value)


def _source_id(explicit: str | None, timestamp: datetime, raw: dict[str, Any], prefix: str) -> str:
    if explicit not in (None, ""):
        return f"{prefix}:{str(explicit)}"[:160]
    encoded = json.dumps(_json_safe(raw), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return f"{prefix}:{timestamp_key(timestamp, encoded)}"[:160]


class Level2Repository:
    """Small repository layer; business and provider code never issue SQL."""

    @staticmethod
    def _insert(session, model):
        return postgresql_insert(model) if session.get_bind().dialect.name == "postgresql" else sqlite_insert(model)

    async def _upsert(self, model, rows: list[dict[str, Any]], keys: list[str], batch_size: int = 500) -> int:
        if not rows:
            return 0
        async with async_session() as session:
            for start in range(0, len(rows), batch_size):
                batch = rows[start:start + batch_size]
                statement = self._insert(session, model).values(batch)
                updates = {
                    column.name: getattr(statement.excluded, column.name)
                    for column in model.__table__.columns
                    if column.name not in {"id", *keys}
                }
                await session.execute(statement.on_conflict_do_update(index_elements=keys, set_=updates))
            await session.commit()
        return len(rows)

    async def get_job(self, symbol: str, trade_date: date, data_type: str) -> Level2FetchJob | None:
        async with async_session() as session:
            return await session.scalar(select(Level2FetchJob).where(
                Level2FetchJob.symbol == symbol,
                Level2FetchJob.trade_date == trade_date,
                Level2FetchJob.data_type == data_type,
            ))

    async def save_job(self, values: dict[str, Any]) -> None:
        payload = {**values, "updated_at": datetime.utcnow()}
        await self._upsert(Level2FetchJob, [payload], ["symbol", "trade_date", "data_type"], batch_size=1)

    async def save_trades(self, rows: Iterable[TradeTick]) -> int:
        payload: list[dict[str, Any]] = []
        for row in rows:
            raw = _json_safe(row.raw)
            payload.append({
                "symbol": row.symbol,
                "trade_date": row.trade_date,
                "timestamp": row.timestamp,
                "source_trade_id": _source_id(row.trade_id, row.timestamp, raw, "trade"),
                "trade_id": row.trade_id,
                "price": row.price,
                "volume": row.volume,
                "amount": row.amount,
                "side": row.side,
                "direction_method": row.direction_method,
                "direction_confidence": row.direction_confidence,
                "trade_code": row.trade_code,
                "buy_order_id": row.buy_order_id,
                "sell_order_id": row.sell_order_id,
                "source": row.source,
                "raw_payload": raw if isinstance(raw, dict) else {"value": raw},
                "fetched_at": datetime.utcnow(),
            })
        return await self._upsert(Level2TradeHistory, payload, ["symbol", "trade_date", "source_trade_id"])

    async def save_orders(self, rows: Iterable[OrderTick]) -> int:
        payload: list[dict[str, Any]] = []
        for row in rows:
            raw = _json_safe(row.raw)
            explicit = row.order_id or row.order_no
            payload.append({
                "symbol": row.symbol,
                "trade_date": row.trade_date,
                "timestamp": row.timestamp,
                "source_order_id": _source_id(explicit, row.timestamp, raw, "order"),
                "order_id": row.order_id,
                "price": row.price,
                "volume": row.volume,
                "amount": row.amount,
                "side": row.side,
                "order_type": row.order_type,
                "order_no": row.order_no,
                "source": row.source,
                "raw_payload": raw if isinstance(raw, dict) else {"value": raw},
                "fetched_at": datetime.utcnow(),
            })
        return await self._upsert(Level2OrderHistory, payload, ["symbol", "trade_date", "source_order_id"])

    async def save_quotes(self, rows: Iterable[OrderBookSnapshot]) -> int:
        payload: list[dict[str, Any]] = []
        for row in rows:
            raw = _json_safe(row.raw)
            payload.append({
                "symbol": row.symbol,
                "trade_date": row.trade_date,
                "timestamp": row.timestamp,
                "source_snapshot_id": _source_id(None, row.timestamp, raw, "quote"),
                "last_price": row.last_price,
                "open_price": row.open_price,
                "high_price": row.high_price,
                "low_price": row.low_price,
                "pre_close": row.pre_close,
                "volume": row.volume,
                "amount": row.amount,
                "bid_json": [level.as_dict() for level in row.bids],
                "ask_json": [level.as_dict() for level in row.asks],
                "source": row.source,
                "raw_payload": raw if isinstance(raw, dict) else {"value": raw},
                "fetched_at": datetime.utcnow(),
            })
        return await self._upsert(Level2QuoteHistory, payload, ["symbol", "trade_date", "source_snapshot_id"])

    async def save_features(self, rows: Iterable[dict[str, Any]]) -> int:
        payload = []
        json_columns = {"hfi_components", "components", "explanation"}
        for row in rows:
            # Date/DateTime columns must remain native Python values for both
            # SQLite and PostgreSQL. Only JSON columns need recursive cleanup.
            item = {
                key: _json_safe(value) if key in json_columns else value
                for key, value in row.items()
            }
            item.setdefault("created_at", datetime.utcnow())
            payload.append(item)
        return await self._upsert(Level2Feature1m, payload, ["symbol", "trade_date", "minute"])

    async def save_quality(self, values: dict[str, Any]) -> int:
        return await self._upsert(Level2QualitySnapshot, [{**values, "generated_at": datetime.utcnow()}], ["symbol", "trade_date"], batch_size=1)

    async def load_trades(self, symbol: str, trade_date: date, limit: int | None = None) -> list[TradeTick]:
        async with async_session() as session:
            statement = select(Level2TradeHistory).where(
                Level2TradeHistory.symbol == symbol,
                Level2TradeHistory.trade_date == trade_date,
            ).order_by(Level2TradeHistory.timestamp.asc(), Level2TradeHistory.id.asc())
            if limit:
                statement = statement.limit(limit)
            rows = list((await session.execute(statement)).scalars().all())
        return [TradeTick(
            symbol=row.symbol, trade_date=row.trade_date, timestamp=row.timestamp,
            trade_id=row.trade_id, price=row.price, volume=row.volume, amount=row.amount,
            side=row.side, direction_method=row.direction_method or "unclassified",
            direction_confidence=float(row.direction_confidence or 0), trade_code=row.trade_code,
            buy_order_id=row.buy_order_id, sell_order_id=row.sell_order_id,
            source=row.source, raw=row.raw_payload or {},
        ) for row in rows]

    async def load_orders(self, symbol: str, trade_date: date, limit: int | None = None) -> list[OrderTick]:
        async with async_session() as session:
            statement = select(Level2OrderHistory).where(
                Level2OrderHistory.symbol == symbol,
                Level2OrderHistory.trade_date == trade_date,
            ).order_by(Level2OrderHistory.timestamp.asc(), Level2OrderHistory.id.asc())
            if limit:
                statement = statement.limit(limit)
            rows = list((await session.execute(statement)).scalars().all())
        return [OrderTick(
            symbol=row.symbol, trade_date=row.trade_date, timestamp=row.timestamp,
            order_id=row.order_id, price=row.price, volume=row.volume, amount=row.amount,
            side=row.side, order_type=row.order_type, order_no=row.order_no,
            source=row.source, raw=row.raw_payload or {},
        ) for row in rows]

    @staticmethod
    def _levels(value: Any) -> list[BookLevel]:
        if not isinstance(value, list):
            return []
        result = []
        for index, item in enumerate(value, start=1):
            if not isinstance(item, dict):
                continue
            try:
                level = int(item.get("level") or index)
            except (TypeError, ValueError):
                level = index
            try:
                price = float(item["price"]) if item.get("price") is not None else None
            except (TypeError, ValueError):
                price = None
            try:
                volume = float(item["volume"]) if item.get("volume") is not None else None
            except (TypeError, ValueError):
                volume = None
            result.append(BookLevel(price, volume, level))
        return result

    async def load_quotes(self, symbol: str, trade_date: date, limit: int | None = None) -> list[OrderBookSnapshot]:
        async with async_session() as session:
            statement = select(Level2QuoteHistory).where(
                Level2QuoteHistory.symbol == symbol,
                Level2QuoteHistory.trade_date == trade_date,
            ).order_by(Level2QuoteHistory.timestamp.asc(), Level2QuoteHistory.id.asc())
            if limit:
                statement = statement.limit(limit)
            rows = list((await session.execute(statement)).scalars().all())
        return [OrderBookSnapshot(
            symbol=row.symbol, trade_date=row.trade_date, timestamp=row.timestamp,
            last_price=row.last_price, open_price=row.open_price, high_price=row.high_price,
            low_price=row.low_price, pre_close=row.pre_close, volume=row.volume,
            amount=row.amount, bids=self._levels(row.bid_json), asks=self._levels(row.ask_json),
            source=row.source, raw=row.raw_payload or {},
        ) for row in rows]

    async def load_features(self, symbol: str, trade_date: date, limit: int | None = None) -> list[dict[str, Any]]:
        async with async_session() as session:
            statement = select(Level2Feature1m).where(
                Level2Feature1m.symbol == symbol,
                Level2Feature1m.trade_date == trade_date,
            ).order_by(Level2Feature1m.minute.asc(), Level2Feature1m.id.asc())
            if limit:
                statement = statement.limit(limit)
            rows = list((await session.execute(statement)).scalars().all())
        excluded = {"id", "symbol", "trade_date", "created_at"}
        output = []
        for row in rows:
            payload = {column.name: getattr(row, column.name) for column in Level2Feature1m.__table__.columns if column.name not in excluded}
            payload["symbol"] = row.symbol
            payload["trade_date"] = row.trade_date
            payload["minute"] = row.minute
            output.append(payload)
        return output

    async def get_quality(self, symbol: str, trade_date: date) -> dict[str, Any] | None:
        async with async_session() as session:
            row = await session.scalar(select(Level2QualitySnapshot).where(
                Level2QualitySnapshot.symbol == symbol,
                Level2QualitySnapshot.trade_date == trade_date,
            ))
        if row is None:
            return None
        return {
            "status": row.status,
            "first_timestamp": row.first_timestamp.isoformat() if row.first_timestamp else None,
            "last_timestamp": row.last_timestamp.isoformat() if row.last_timestamp else None,
            "trade_count": row.trade_count,
            "order_count": row.order_count,
            "quote_count": row.quote_count,
            "pagination_complete": bool(row.pagination_complete),
            "quote_depth_coverage_pct": row.quote_depth_coverage,
            "confidence": row.confidence,
            "warnings": row.warnings or [],
            "checks": row.checks or {},
            "source": row.source,
            "generated_at": row.generated_at.isoformat() if row.generated_at else None,
        }

    async def job_status(self, symbol: str, trade_date: date) -> list[dict[str, Any]]:
        async with async_session() as session:
            rows = list((await session.execute(select(Level2FetchJob).where(
                Level2FetchJob.symbol == symbol,
                Level2FetchJob.trade_date == trade_date,
            ).order_by(Level2FetchJob.data_type.asc()))).scalars().all())
        return [{
            "data_type": row.data_type,
            "provider": row.provider,
            "status": row.status,
            "pages": row.pages,
            "rows": row.rows,
            "error": row.error,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        } for row in rows]
