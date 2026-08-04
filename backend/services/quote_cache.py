"""Verified stock quotes with a persistent latest-trading-day fallback."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from models import StockDailyBar
from services.data_collector import collector, is_a_share_market_session, normalize_stock_code, shanghai_now


class QuoteSnapshotService:
    @staticmethod
    async def _load(codes: list[str], session_factory) -> dict[str, dict[str, Any]]:
        async with session_factory() as session:
            rows = (await session.execute(
                select(StockDailyBar)
                .where(StockDailyBar.stock_code.in_(codes))
                .order_by(StockDailyBar.stock_code.asc(), StockDailyBar.trade_date.desc())
            )).scalars().all()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row.stock_code in latest or row.close_price is None or row.close_price <= 0:
                continue
            previous_close = None
            if row.change_amount is not None:
                previous_close = float(row.close_price) - float(row.change_amount)
            latest[row.stock_code] = {
                "code": row.stock_code,
                "name": row.stock_name or "",
                "price": float(row.close_price),
                "change_pct": row.change_pct,
                "change_amount": row.change_amount,
                "previous_close": previous_close,
                "high": row.high_price,
                "low": row.low_price,
                "volume": row.volume,
                "amount": row.amount,
                "turnover": row.turnover,
                "quote_timestamp": None,
                "cache_trade_date": row.trade_date.isoformat(),
                "cache_updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "quote_source": row.source or "cache",
            }
        return latest

    @staticmethod
    async def _persist(payload: dict[str, Any], session_factory) -> int:
        rows = []
        source = str(payload.get("source") or "eastmoney")
        for quote in payload.get("stocks") or []:
            quote_at = collector._quote_timestamp_datetime(quote.get("quote_timestamp"))
            try:
                code = normalize_stock_code(quote.get("code"))
                price = float(quote.get("price"))
            except (TypeError, ValueError):
                continue
            if quote_at is None or price <= 0:
                continue
            previous_close = quote.get("previous_close")
            change_amount = quote.get("change_amount")
            if change_amount in (None, "", "-") and previous_close not in (None, "", "-", 0):
                change_amount = price - float(previous_close)
            rows.append({
                "stock_code": code,
                "stock_name": str(quote.get("name") or ""),
                "market": "SH" if code.startswith(("6", "9")) else "BJ" if code.startswith(("4", "8")) else "SZ",
                "trade_date": quote_at.date(),
                "open_price": quote.get("open"),
                "close_price": price,
                "high_price": quote.get("high"),
                "low_price": quote.get("low"),
                "volume": quote.get("volume"),
                "amount": quote.get("amount"),
                "change_pct": quote.get("change_pct"),
                "change_amount": change_amount,
                "turnover": quote.get("turnover"),
                "source": source,
                "updated_at": datetime.utcnow(),
            })
        if not rows:
            return 0
        async with session_factory() as session:
            insert = postgresql_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
            statement = insert(StockDailyBar).values(rows)
            updates = {
                column.name: getattr(statement.excluded, column.name)
                for column in StockDailyBar.__table__.columns
                if column.name not in {"id", "stock_code", "trade_date", "created_at"}
            }
            await session.execute(statement.on_conflict_do_update(
                index_elements=["stock_code", "trade_date"],
                set_=updates,
            ))
            await session.commit()
        return len(rows)

    async def fetch(self, stock_codes: list[str], session_factory) -> dict[str, Any]:
        codes = list(dict.fromkeys(normalize_stock_code(code) for code in stock_codes))
        cached = await self._load(codes, session_factory) if codes else {}
        now = shanghai_now()
        live_payload: dict[str, Any] = {}
        live_error = None

        # A complete close snapshot is the preferred source outside trading
        # hours. Missing symbols still get one bounded upstream recovery try.
        should_fetch = is_a_share_market_session(now) or len(cached) < len(codes)
        if should_fetch and codes:
            try:
                live_payload = await collector.fetch_stock_quotes(codes)
                await self._persist(live_payload, session_factory)
            except Exception as exc:
                live_error = type(exc).__name__

        live = {str(item.get("code")): item for item in live_payload.get("stocks") or []}
        merged = []
        cached_count = 0
        for code in codes:
            if code in live:
                merged.append(live[code])
            elif code in cached:
                merged.append(cached[code])
                cached_count += 1

        cache_dates = [item.get("cache_trade_date") for item in merged if item.get("cache_trade_date")]
        data_date = live_payload.get("data_date") or max(cache_dates, default=None)
        source = live_payload.get("source") if live else None
        if live and cached_count:
            source = f"{source or 'eastmoney'}+cache"
        elif not live and cached_count:
            source = "cache"
        is_realtime = bool(live_payload.get("is_realtime")) and cached_count == 0
        return {
            "stocks": merged,
            "total": len(merged),
            "source": source or "cache",
            "data_date": data_date,
            "source_updated_at": live_payload.get("source_updated_at"),
            "is_realtime": is_realtime,
            "fetched_at": now.isoformat(),
            "complete": len(merged) == len(codes),
            "available": bool(merged),
            "cache_used": cached_count > 0,
            "cached_count": cached_count,
            "stale": not is_realtime,
            "error": live_error,
        }


quote_snapshot_service = QuoteSnapshotService()
