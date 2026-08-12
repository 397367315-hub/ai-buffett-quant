"""Daily market evidence used by the strategic-analysis factors."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, desc, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database import async_session
from models import MarketSentimentDaily, StockDailyBar
from services.data_collector import collector, shanghai_now


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _percentile(value: float | None, values: list[float]) -> float | None:
    if value is None or not values:
        return None
    return round(sum(item <= value for item in values) / len(values) * 100, 2)


class StrategicMarketDataService:
    _CONCURRENCY = 4

    def __init__(self) -> None:
        self._ensure_lock = asyncio.Lock()

    @staticmethod
    def _analysis_ready(payload: dict[str, Any]) -> bool:
        summary = payload.get("summary") or {}
        return bool(
            payload.get("available")
            and summary.get("is_current")
            and summary.get("breadth_complete")
            and int(summary.get("amount_history_count") or 0) >= 5
            and int(summary.get("turnover_history_count") or 0) >= 5
            and summary.get("failed_limit_rate") is not None
            and summary.get("max_streak_height") is not None
        )

    @staticmethod
    async def _aggregate(trade_date: date) -> dict[str, Any]:
        async with async_session() as session:
            row = (await session.execute(
                select(
                    func.count(StockDailyBar.id),
                    func.sum(case((StockDailyBar.change_pct > 0, 1), else_=0)),
                    func.sum(case((StockDailyBar.change_pct < 0, 1), else_=0)),
                    func.sum(case((StockDailyBar.change_pct == 0, 1), else_=0)),
                    func.sum(StockDailyBar.amount),
                    func.count(StockDailyBar.amount),
                    func.avg(StockDailyBar.turnover),
                    func.count(StockDailyBar.turnover),
                ).where(StockDailyBar.trade_date == trade_date)
            )).one()
        return {
            "stock_count": int(row[0] or 0),
            "up_count": int(row[1] or 0),
            "down_count": int(row[2] or 0),
            "flat_count": int(row[3] or 0),
            "market_amount": int(row[4]) if row[4] is not None else None,
            "amount_count": int(row[5] or 0),
            "average_turnover": round(float(row[6]), 6) if row[6] is not None else None,
            "turnover_count": int(row[7] or 0),
        }

    @staticmethod
    async def _aggregate_many(trade_dates: list[date]) -> dict[date, dict[str, Any]]:
        """Build daily breadth and explicitly labelled limit-board approximations.

        The limit fields use adjusted daily OHLC bars and board-code thresholds.
        They are suitable for factor research, but not a replacement for a
        historical 09:25/level-2 event feed.
        """
        if not trade_dates:
            return {}
        code = StockDailyBar.stock_code
        limit_pct = case(
            (code.like("300%"), 20.0),
            (code.like("301%"), 20.0),
            (code.like("302%"), 20.0),
            (code.like("688%"), 20.0),
            (code.like("689%"), 20.0),
            (code.like("4%"), 30.0),
            (code.like("8%"), 30.0),
            (code.like("92%"), 30.0),
            else_=10.0,
        )
        denominator = func.nullif(1 + StockDailyBar.change_pct / 100.0, 0)
        previous_close = StockDailyBar.close_price / denominator
        high_change = (StockDailyBar.high_price / func.nullif(previous_close, 0) - 1) * 100.0
        valid = and_(
            StockDailyBar.change_pct.is_not(None),
            StockDailyBar.close_price.is_not(None),
            StockDailyBar.close_price > 0,
        )
        sealed_up = and_(valid, StockDailyBar.change_pct >= limit_pct - 0.30)
        sealed_down = and_(valid, StockDailyBar.change_pct <= -limit_pct + 0.30)
        touched_up = and_(valid, StockDailyBar.high_price.is_not(None), high_change >= limit_pct - 0.30)
        failed_up = and_(touched_up, StockDailyBar.change_pct < limit_pct - 0.30)
        async with async_session() as session:
            rows = (await session.execute(
                select(
                    StockDailyBar.trade_date,
                    func.count(StockDailyBar.id),
                    func.sum(case((StockDailyBar.change_pct > 0, 1), else_=0)),
                    func.sum(case((StockDailyBar.change_pct < 0, 1), else_=0)),
                    func.sum(case((StockDailyBar.change_pct == 0, 1), else_=0)),
                    func.sum(StockDailyBar.amount),
                    func.count(StockDailyBar.amount),
                    func.avg(StockDailyBar.turnover),
                    func.count(StockDailyBar.turnover),
                    func.sum(case((sealed_up, 1), else_=0)),
                    func.sum(case((sealed_down, 1), else_=0)),
                    func.sum(case((failed_up, 1), else_=0)),
                )
                .where(StockDailyBar.trade_date.in_(trade_dates))
                .group_by(StockDailyBar.trade_date)
            )).all()
            limit_rows = (await session.execute(
                select(StockDailyBar.trade_date, StockDailyBar.stock_code)
                .where(StockDailyBar.trade_date.in_(trade_dates), sealed_up)
                .order_by(StockDailyBar.trade_date, StockDailyBar.stock_code)
            )).all()

        session_index = {day: index for index, day in enumerate(sorted(trade_dates))}
        streaks: dict[str, tuple[int, int]] = {}
        max_streak_by_date: dict[date, int] = {}
        for trade_day, stock_code in limit_rows:
            index = session_index.get(trade_day)
            if index is None:
                continue
            previous_index, previous_streak = streaks.get(str(stock_code), (-2, 0))
            streak = previous_streak + 1 if previous_index == index - 1 else 1
            streaks[str(stock_code)] = (index, streak)
            max_streak_by_date[trade_day] = max(max_streak_by_date.get(trade_day, 0), streak)

        return {
            row[0]: {
                "stock_count": int(row[1] or 0),
                "up_count": int(row[2] or 0),
                "down_count": int(row[3] or 0),
                "flat_count": int(row[4] or 0),
                "market_amount": int(row[5]) if row[5] is not None else None,
                "amount_count": int(row[6] or 0),
                "average_turnover": round(float(row[7]), 6) if row[7] is not None else None,
                "turnover_count": int(row[8] or 0),
                "limit_up_count": int(row[9] or 0),
                "limit_down_count": int(row[10] or 0),
                "failed_limit_count": int(row[11] or 0),
                "max_streak_height": max_streak_by_date.get(row[0], 0),
            }
            for row in rows
        }

    @staticmethod
    async def _upsert(rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        async with async_session() as session:
            insert = postgresql_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
            statement = insert(MarketSentimentDaily).values(rows)
            updates = {
                column.name: getattr(statement.excluded, column.name)
                for column in MarketSentimentDaily.__table__.columns
                if column.name != "trade_date"
            }
            await session.execute(statement.on_conflict_do_update(
                index_elements=["trade_date"], set_=updates,
            ))
            await session.commit()
        return len(rows)

    async def sync_recent(self, days: int = 30) -> dict[str, Any]:
        bounded = min(max(int(days), 1), 260)
        async with async_session() as session:
            trade_dates = list((await session.execute(
                select(StockDailyBar.trade_date)
                .distinct()
                .order_by(desc(StockDailyBar.trade_date))
                .limit(bounded)
            )).scalars().all())
            existing = {
                row.trade_date: row
                for row in (await session.execute(
                    select(MarketSentimentDaily).where(MarketSentimentDaily.trade_date.in_(trade_dates))
                )).scalars().all()
            } if trade_dates else {}
        if not trade_dates:
            return {"status": "unavailable", "written": 0, "reason": "stock_daily_bars_empty"}

        aggregates = await self._aggregate_many(trade_dates)

        semaphore = asyncio.Semaphore(self._CONCURRENCY)
        recent_pool_cutoff = shanghai_now().date() - timedelta(days=35)

        async def fetch_one(trade_date: date) -> dict[str, Any]:
            aggregate = aggregates.get(trade_date) or await self._aggregate(trade_date)
            prior = existing.get(trade_date)
            limit_up_count = prior.limit_up_count if prior else None
            limit_down_count = prior.limit_down_count if prior else None
            failed_count = prior.failed_limit_count if prior else None
            max_streak = prior.max_streak_height if prior else None
            exact_limit_data = bool(
                prior
                and prior.source != "daily_bar_derived"
                and None not in (limit_up_count, limit_down_count, failed_count, max_streak)
            )
            should_fetch_pools = (
                trade_date >= recent_pool_cutoff
                and not exact_limit_data
            )
            if should_fetch_pools:
                async with semaphore:
                    up, down, failed = await asyncio.gather(
                        collector.fetch_limit_up_pool(page_size=500, target_date=trade_date),
                        collector.fetch_limit_down_pool(page_size=500, target_date=trade_date),
                        collector.fetch_failed_limit_pool(page_size=500, target_date=trade_date),
                    )
                if up.get("trade_date"):
                    limit_up_count = int(up.get("total") or 0)
                    max_streak = max(
                        (int(item.get("continuous_days") or 0) for item in up.get("stocks") or []),
                        default=0,
                    )
                if down.get("trade_date"):
                    limit_down_count = int(down.get("total") or 0)
                if failed.get("trade_date"):
                    failed_count = int(failed.get("total") or 0)
                exact_limit_data = bool(
                    up.get("trade_date") and down.get("trade_date") and failed.get("trade_date")
                )
            if not exact_limit_data:
                limit_up_count = aggregate.get("limit_up_count")
                limit_down_count = aggregate.get("limit_down_count")
                failed_count = aggregate.get("failed_limit_count")
                max_streak = aggregate.get("max_streak_height")
            denominator = (limit_up_count or 0) + (failed_count or 0)
            failed_rate = round((failed_count or 0) / denominator * 100, 4) if denominator else None
            return {
                "trade_date": trade_date,
                **{key: aggregate.get(key) for key in (
                    "stock_count", "up_count", "down_count", "flat_count",
                    "market_amount", "amount_count", "average_turnover", "turnover_count",
                )},
                "limit_up_count": limit_up_count,
                "limit_down_count": limit_down_count,
                "failed_limit_count": failed_count,
                "failed_limit_rate": failed_rate,
                "max_streak_height": max_streak,
                "source": "eastmoney+daily_bars" if exact_limit_data else "daily_bar_derived",
                "updated_at": datetime.utcnow(),
            }

        rows: list[dict[str, Any]] = []
        for start in range(0, len(trade_dates), self._CONCURRENCY):
            batch = trade_dates[start:start + self._CONCURRENCY]
            results = await asyncio.gather(*(fetch_one(item) for item in batch), return_exceptions=True)
            rows.extend(item for item in results if isinstance(item, dict))
        written = await self._upsert(rows)
        try:
            from services.quant_research_workspace import quant_research_workspace

            quant_research_workspace.invalidate_manifest_cache()
        except Exception:
            pass
        exact_sessions = sum(item.get("source") != "daily_bar_derived" for item in rows)
        return {
            "status": "success" if written == len(trade_dates) else "partial",
            "written": written,
            "requested": len(trade_dates),
            "exact_sessions": exact_sessions,
            "derived_sessions": len(rows) - exact_sessions,
            "data_date": max(trade_dates).isoformat(),
        }

    async def history(self, limit: int = 120) -> dict[str, Any]:
        bounded = min(max(int(limit), 20), 260)
        async with async_session() as session:
            rows = list((await session.execute(
                select(MarketSentimentDaily)
                .order_by(desc(MarketSentimentDaily.trade_date))
                .limit(bounded)
            )).scalars().all())
        rows.reverse()
        history = [{
            "date": row.trade_date.isoformat(),
            "up_count": row.up_count,
            "down_count": row.down_count,
            "flat_count": row.flat_count,
            "stock_count": row.stock_count,
            "market_amount": row.market_amount,
            "amount_count": row.amount_count,
            "average_turnover": row.average_turnover,
            "turnover_count": row.turnover_count,
            "limit_up_count": row.limit_up_count,
            "limit_down_count": row.limit_down_count,
            "failed_limit_count": row.failed_limit_count,
            "failed_limit_rate": row.failed_limit_rate,
            "max_streak_height": row.max_streak_height,
        } for row in rows]
        latest = history[-1] if history else {}
        amount_values = [
            value for item in history
            if (value := _number(item.get("market_amount"))) is not None and value > 0
            and int(item.get("stock_count") or 0) >= 1_000
            and int(item.get("amount_count") or 0) >= int(item.get("stock_count") or 0) * 0.9
        ]
        turnover_values = [
            value for item in history
            if (value := _number(item.get("average_turnover"))) is not None and value >= 0
            and int(item.get("stock_count") or 0) >= 1_000
            and int(item.get("turnover_count") or 0) >= int(item.get("stock_count") or 0) * 0.9
        ]
        latest_amount = _number(latest.get("market_amount"))
        latest_turnover = _number(latest.get("average_turnover"))
        amount_ma5 = sum(amount_values[-5:]) / 5 if len(amount_values) >= 5 else None
        amount_ma20 = sum(amount_values[-20:]) / 20 if len(amount_values) >= 20 else None
        counted = sum(int(latest.get(key) or 0) for key in ("up_count", "down_count", "flat_count"))
        stock_count = int(latest.get("stock_count") or 0)
        directional = int(latest.get("up_count") or 0) + int(latest.get("down_count") or 0)
        latest_date = date.fromisoformat(str(latest.get("date"))) if latest.get("date") else None
        is_current = bool(latest_date and (shanghai_now().date() - latest_date).days <= 10)
        breadth_complete = bool(stock_count >= 1_000 and counted >= stock_count * 0.9)
        summary = {
            "latest": latest or None,
            "is_current": is_current,
            "breadth_complete": breadth_complete,
            "amount_complete": bool(stock_count >= 1_000 and int(latest.get("amount_count") or 0) >= stock_count * 0.9),
            "turnover_complete": bool(stock_count >= 1_000 and int(latest.get("turnover_count") or 0) >= stock_count * 0.9),
            "breadth_ratio": (
                round(int(latest.get("up_count") or 0) / directional * 100, 2)
                if directional else None
            ),
            "breadth_net": (
                int(latest.get("up_count") or 0) - int(latest.get("down_count") or 0)
                if breadth_complete else None
            ),
            "market_amount_ma5": round(amount_ma5, 2) if amount_ma5 is not None else None,
            "market_amount_ma20": round(amount_ma20, 2) if amount_ma20 is not None else None,
            "market_amount_vs_ma5_pct": (
                round((latest_amount / amount_ma5 - 1) * 100, 2)
                if latest_amount is not None and amount_ma5 not in (None, 0) else None
            ),
            "market_amount_percentile": _percentile(latest_amount, amount_values[-120:]),
            "average_turnover_percentile": _percentile(latest_turnover, turnover_values[-120:]),
            "amount_history_count": len(amount_values),
            "turnover_history_count": len(turnover_values),
            "failed_limit_rate": _number(latest.get("failed_limit_rate")),
            "max_streak_height": (
                int(latest["max_streak_height"])
                if latest.get("max_streak_height") is not None else None
            ),
        }
        return {
            "history": history,
            "summary": summary,
            "count": len(history),
            "data_date": history[-1]["date"] if history else None,
            "source": "database_cache",
            "is_realtime": False,
            "cache_used": bool(history),
            "available": bool(history),
        }

    async def ensure_history(self, limit: int = 120) -> dict[str, Any]:
        """Return complete strategy evidence, repairing a stale cache once."""
        snapshot = await self.history(limit=limit)
        if self._analysis_ready(snapshot):
            return snapshot

        async with self._ensure_lock:
            snapshot = await self.history(limit=limit)
            if self._analysis_ready(snapshot):
                return snapshot
            try:
                refresh = await asyncio.wait_for(self.sync_recent(days=5), timeout=30.0)
                refresh_error = None
            except Exception as exc:
                refresh = None
                refresh_error = type(exc).__name__
            snapshot = await self.history(limit=limit)
            snapshot["refresh_attempted"] = True
            if refresh is not None:
                snapshot["refresh_result"] = refresh
            if refresh_error:
                snapshot["refresh_error"] = refresh_error
            return snapshot


strategic_market_data_service = StrategicMarketDataService()
