"""Forward point-in-time market snapshots used by auditable research.

Historical fields that were never captured cannot be reconstructed faithfully.
This service therefore records the full observed universe at each verified
quote date and the 09:25 auction from deployment onward, with explicit coverage
dates for the research workspace.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database import async_session
from models import StockAuctionSnapshot, StockUniverseSnapshot, StockValuationHistory
from quant.market_cache import load_quant_market_snapshot
from services.data_collector import collector, shanghai_now


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _exchange(code: str) -> str:
    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("4", "8", "92")):
        return "BJ"
    return "SZ"


class PITMarketDataService:
    _AUCTION_BATCH_SIZE = 200
    _AUCTION_CONCURRENCY = 4

    def __init__(self) -> None:
        self._universe_lock = asyncio.Lock()
        self._auction_lock = asyncio.Lock()

    @staticmethod
    async def _upsert(model, rows: list[dict[str, Any]], keys: list[str], batch_size: int = 500) -> int:
        if not rows:
            return 0
        written = 0
        async with async_session() as session:
            dialect = session.get_bind().dialect.name
            insert = postgresql_insert if dialect == "postgresql" else sqlite_insert
            for start in range(0, len(rows), batch_size):
                batch = rows[start:start + batch_size]
                statement = insert(model).values(batch)
                updates = {
                    column.name: getattr(statement.excluded, column.name)
                    for column in model.__table__.columns
                    if column.name not in {"id", *keys}
                }
                await session.execute(statement.on_conflict_do_update(index_elements=keys, set_=updates))
                written += len(batch)
            await session.commit()
        return written

    async def capture_universe(self, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._universe_lock:
            payload = snapshot or await load_quant_market_snapshot()
            trade_date = _parse_date(payload.get("data_date"))
            stocks = list(payload.get("stocks") or [])
            if trade_date is None or not stocks or not payload.get("complete"):
                return {
                    "status": "unavailable",
                    "written": 0,
                    "reason": "完整全市场快照或数据日不可用",
                }
            rows = []
            for stock in stocks:
                code = str(stock.get("code") or "")
                if not code:
                    continue
                quote_at = collector._quote_timestamp_datetime(stock.get("quote_timestamp"))
                price = _number(stock.get("price"))
                rows.append({
                    "stock_code": code,
                    "stock_name": str(stock.get("name") or ""),
                    "exchange": _exchange(code),
                    "trade_date": trade_date,
                    "industry": str(stock.get("sector") or "") or None,
                    "market_cap": int(value) if (value := _number(stock.get("market_cap"))) is not None else None,
                    "close_price": price,
                    "is_suspended": bool(price is None or price <= 0),
                    "status_quality": "verified_full_market_quote",
                    "source": str(payload.get("source") or "eastmoney"),
                    "observed_at": quote_at.replace(tzinfo=None) if quote_at else None,
                    "updated_at": datetime.utcnow(),
                })
            written = await self._upsert(
                StockUniverseSnapshot, rows, ["stock_code", "trade_date"],
            )
            return {
                "status": "success" if written == len(rows) else "partial",
                "written": written,
                "stock_count": len(rows),
                "data_date": trade_date.isoformat(),
                "industry_covered": sum(bool(item.get("industry")) for item in rows),
                "market_cap_covered": sum(item.get("market_cap") is not None for item in rows),
                "source": str(payload.get("source") or "eastmoney"),
            }

    async def latest_universe_snapshot(self) -> dict[str, Any]:
        """Rebuild a usable research snapshot from the latest persisted universe."""
        async with async_session() as session:
            latest = (await session.execute(
                select(func.max(StockUniverseSnapshot.trade_date))
            )).scalar_one()
            if latest is None:
                return {}
            rows = (await session.execute(
                select(StockUniverseSnapshot, StockValuationHistory.latest_pe_ttm)
                .outerjoin(
                    StockValuationHistory,
                    StockValuationHistory.stock_code == StockUniverseSnapshot.stock_code,
                )
                .where(StockUniverseSnapshot.trade_date == latest)
                .order_by(StockUniverseSnapshot.stock_code)
            )).all()

        stocks = []
        latest_observed_at: datetime | None = None
        for row, latest_pe_ttm in rows:
            if row.observed_at and (
                latest_observed_at is None or row.observed_at > latest_observed_at
            ):
                latest_observed_at = row.observed_at
            stocks.append({
                "code": row.stock_code,
                "name": row.stock_name or "",
                "price": row.close_price,
                "market_cap": row.market_cap,
                "sector": row.industry or "",
                "pe": latest_pe_ttm if latest_pe_ttm is not None else "",
                "is_suspended": bool(row.is_suspended),
            })
        if not stocks:
            return {}
        return {
            "stocks": stocks,
            "total": len(stocks),
            "source": "pit_universe_cache",
            "cache_source": rows[0][0].source if rows else "eastmoney",
            "data_date": latest.isoformat(),
            "source_updated_at": latest_observed_at.isoformat() if latest_observed_at else None,
            "fetched_at": latest_observed_at.isoformat() if latest_observed_at else None,
            "is_realtime": False,
            "complete": True,
            "reconstructed": True,
        }

    async def _universe_for_auction(self) -> tuple[list[dict[str, Any]], date | None]:
        async with async_session() as session:
            latest = (await session.execute(select(func.max(StockUniverseSnapshot.trade_date)))).scalar_one()
            rows = list((await session.execute(
                select(StockUniverseSnapshot)
                .where(StockUniverseSnapshot.trade_date == latest)
                .order_by(StockUniverseSnapshot.stock_code)
            )).scalars().all()) if latest else []
        if rows:
            return [{
                "code": row.stock_code,
                "name": row.stock_name,
                "industry": row.industry,
                "market_cap": row.market_cap,
            } for row in rows], latest
        snapshot = await load_quant_market_snapshot()
        return list(snapshot.get("stocks") or []), _parse_date(snapshot.get("data_date"))

    async def capture_auction(self) -> dict[str, Any]:
        """Capture one full-market 09:25 Tencent quote snapshot."""
        now = shanghai_now()
        minute = now.hour * 60 + now.minute
        if now.weekday() >= 5 or not (9 * 60 + 24 <= minute <= 9 * 60 + 27):
            return {"status": "outside_window", "written": 0, "window": "09:24-09:27"}

        async with self._auction_lock:
            universe, _ = await self._universe_for_auction()
            metadata = {
                str(item.get("code") or ""): item
                for item in universe if item.get("code")
            }
            codes = sorted(metadata)
            if not codes:
                return {"status": "unavailable", "written": 0, "reason": "股票池为空"}

            semaphore = asyncio.Semaphore(self._AUCTION_CONCURRENCY)

            async def fetch_batch(batch: list[str]) -> list[dict[str, Any]]:
                async with semaphore:
                    try:
                        result = await asyncio.wait_for(collector.fetch_tencent_quotes(batch), timeout=20)
                    except Exception:
                        return []
                    return list(result.get("stocks") or [])

            batches = [codes[start:start + self._AUCTION_BATCH_SIZE] for start in range(0, len(codes), self._AUCTION_BATCH_SIZE)]
            responses = await asyncio.gather(*(fetch_batch(batch) for batch in batches))
            quote_rows = [item for response in responses for item in response]
            rows: list[dict[str, Any]] = []
            for quote in quote_rows:
                code = str(quote.get("code") or "")
                quote_at = collector._quote_timestamp_datetime(quote.get("quote_timestamp"))
                if quote_at is None or quote_at.date() != now.date():
                    continue
                quote_minute = quote_at.hour * 60 + quote_at.minute
                if not (9 * 60 + 24 <= quote_minute <= 9 * 60 + 27):
                    continue
                price = _number(quote.get("price"))
                previous_close = _number(quote.get("previous_close"))
                volume = _number(quote.get("volume"))
                high_open_pct = (
                    (price / previous_close - 1) * 100
                    if price is not None and previous_close not in (None, 0) else None
                )
                meta = metadata.get(code) or {}
                rows.append({
                    "stock_code": code,
                    "stock_name": str(quote.get("name") or meta.get("name") or ""),
                    "trade_date": now.date(),
                    "quote_at": quote_at.replace(tzinfo=None),
                    "auction_price": price,
                    "previous_close": previous_close,
                    "high_open_pct": round(high_open_pct, 6) if high_open_pct is not None else None,
                    "auction_volume": int(volume) if volume is not None else None,
                    "auction_amount": int(round(price * volume)) if price is not None and volume is not None else None,
                    "auction_volume_ratio": _number(quote.get("volume_ratio")),
                    "industry": str(meta.get("industry") or meta.get("sector") or "") or None,
                    "market_cap": int(value) if (value := _number(meta.get("market_cap"))) is not None else None,
                    "source": "tencent",
                    "is_realtime": True,
                    "updated_at": datetime.utcnow(),
                })
            written = await self._upsert(
                StockAuctionSnapshot, rows, ["stock_code", "trade_date"],
            )
            return {
                "status": "success" if written and written == len(codes) else "partial" if written else "unavailable",
                "written": written,
                "requested": len(codes),
                "coverage_pct": round(written / len(codes) * 100, 2) if codes else 0.0,
                "data_date": now.date().isoformat(),
                "source": "tencent",
                "window": "09:24-09:27",
            }

    async def coverage(self) -> dict[str, Any]:
        async with async_session() as session:
            universe = (await session.execute(select(
                func.count(StockUniverseSnapshot.id),
                func.count(func.distinct(StockUniverseSnapshot.stock_code)),
                func.count(func.distinct(StockUniverseSnapshot.trade_date)),
                func.min(StockUniverseSnapshot.trade_date),
                func.max(StockUniverseSnapshot.trade_date),
                func.sum(func.cast(StockUniverseSnapshot.industry.is_not(None), type_=StockUniverseSnapshot.id.type)),
                func.sum(func.cast(StockUniverseSnapshot.market_cap.is_not(None), type_=StockUniverseSnapshot.id.type)),
            ))).one()
            auction = (await session.execute(select(
                func.count(StockAuctionSnapshot.id),
                func.count(func.distinct(StockAuctionSnapshot.stock_code)),
                func.count(func.distinct(StockAuctionSnapshot.trade_date)),
                func.min(StockAuctionSnapshot.trade_date),
                func.max(StockAuctionSnapshot.trade_date),
            ))).one()
        return {
            "universe": {
                "records": int(universe[0] or 0),
                "stocks": int(universe[1] or 0),
                "sessions": int(universe[2] or 0),
                "from": universe[3].isoformat() if universe[3] else None,
                "to": universe[4].isoformat() if universe[4] else None,
                "industry_records": int(universe[5] or 0),
                "market_cap_records": int(universe[6] or 0),
            },
            "auction": {
                "records": int(auction[0] or 0),
                "stocks": int(auction[1] or 0),
                "sessions": int(auction[2] or 0),
                "from": auction[3].isoformat() if auction[3] else None,
                "to": auction[4].isoformat() if auction[4] else None,
            },
        }


pit_market_data_service = PITMarketDataService()
