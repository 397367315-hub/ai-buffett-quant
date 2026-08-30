"""Level-2 radar orchestration.

The service keeps the slow vendor workflow outside the ordinary stock-detail
request.  A detail request can therefore render immediately from cached
features, start a background historical sync, or clearly report that the
provider has not been configured.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Iterable

from sqlalchemy import func, select

from config import settings
from database import async_session
from models import Level2Feature1m, StockDailyBar
from engines.microstructure import build_feature_series, build_summary, detect_events
from market_data.level2.normalizer import normalize_symbol
from market_data.level2.providers.base import Level2DataType
from market_data.level2.providers.numcat import NumCatProvider
from market_data.level2.repository import Level2Repository
from market_data.level2.fetcher import FetchResult, Level2Fetcher


logger = logging.getLogger(__name__)


class Level2Service:
    """Coordinate provider, resumable fetcher, repository, and feature engine."""

    def __init__(
        self,
        *,
        provider: NumCatProvider | None = None,
        repository: Level2Repository | None = None,
    ) -> None:
        self.provider = provider or NumCatProvider()
        self.repository = repository or Level2Repository()
        self.fetcher = Level2Fetcher(self.provider, self.repository)
        self._tasks: dict[tuple[str, date], asyncio.Task] = {}
        self._locks: dict[tuple[str, date], asyncio.Lock] = {}

    @staticmethod
    def _lock_for(key: tuple[str, date], locks: dict[tuple[str, date], asyncio.Lock]) -> asyncio.Lock:
        lock = locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            locks[key] = lock
        return lock

    async def resolve_trade_date(self, requested: date | None = None) -> date:
        if requested is not None:
            return requested
        async with async_session() as session:
            latest_l2 = (await session.execute(select(func.max(Level2Feature1m.trade_date)))).scalar_one_or_none()
            latest_daily = (await session.execute(select(func.max(StockDailyBar.trade_date)))).scalar_one_or_none()
        return latest_daily or latest_l2 or datetime.utcnow().date()

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.provider.name,
            "configured": self.provider.configured,
            "capabilities": self.provider.capabilities.as_dict(),
            "realtime_mode": "not_advertised_until_provider_confirms",
            "scope": "single_symbol_single_trade_date",
            "api_key_exposed_to_frontend": False,
        }

    def _task_running(self, key: tuple[str, date]) -> bool:
        task = self._tasks.get(key)
        return bool(task and not task.done())

    def start_sync(
        self,
        symbol: str,
        trade_date: date,
        *,
        force: bool = False,
        data_types: Iterable[Level2DataType] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_symbol(symbol)
        key = (normalized, trade_date)
        if not self.provider.configured:
            return {
                "status": "provider_not_configured",
                "pending": False,
                "started": False,
                "provider": self.provider.name,
                "message": "Level-2数据源未配置API密钥，普通行情和个股决策不受影响。",
            }
        if self._task_running(key):
            return {
                "status": "pending",
                "pending": True,
                "started": False,
                "provider": self.provider.name,
                "message": "Level-2历史数据正在后台同步。",
            }
        try:
            task = asyncio.create_task(self.sync(
                normalized,
                trade_date,
                force=force,
                data_types=data_types,
                start_time=start_time,
                end_time=end_time,
            ))
        except RuntimeError:
            # There is no running loop only in synchronous tooling; the API
            # always has one. Return a truthful status instead of doing a
            # blocking provider request from the request thread.
            return {
                "status": "not_started",
                "pending": False,
                "started": False,
                "provider": self.provider.name,
                "message": "当前运行环境没有可用的后台事件循环。",
            }
        self._tasks[key] = task
        task.add_done_callback(lambda done, task_key=key: self._finish_task(task_key, done))
        return {
            "status": "pending",
            "pending": True,
            "started": True,
            "provider": self.provider.name,
            "message": "Level-2历史数据已进入后台同步队列。",
        }

    def _finish_task(self, key: tuple[str, date], task: asyncio.Task) -> None:
        self._tasks.pop(key, None)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Level-2 background sync failed for %s/%s", key[0], key[1])

    async def sync(
        self,
        symbol: str,
        trade_date: date,
        *,
        force: bool = False,
        data_types: Iterable[Level2DataType] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_symbol(symbol)
        key = (normalized, trade_date)
        lock = self._lock_for(key, self._locks)
        async with lock:
            fetch_result = await self.fetcher.run(
                normalized,
                trade_date,
                data_types=data_types,
                force=force,
                start_time=start_time,
                end_time=end_time,
            )
            return await self._rebuild_features(normalized, trade_date, fetch_result)

    async def _rebuild_features(
        self,
        symbol: str,
        trade_date: date,
        fetch_result: FetchResult | None = None,
    ) -> dict[str, Any]:
        # Keep the in-process working set bounded. The raw tables remain the
        # source of truth and can be processed in a future SQL streaming pass.
        try:
            max_rows = min(max(int(settings.level2_max_rows), 1_000), 2_000_000)
        except (TypeError, ValueError):
            max_rows = 500_000
        trades = await self.repository.load_trades(symbol, trade_date, limit=max_rows)
        orders = await self.repository.load_orders(symbol, trade_date, limit=max_rows)
        quotes = await self.repository.load_quotes(symbol, trade_date, limit=max_rows)
        features = build_feature_series(trades, orders, quotes)
        for row in features:
            row["symbol"] = symbol
            row["trade_date"] = trade_date
            row.setdefault("source", "numcat")
        if features:
            await self.repository.save_features(features)
        quality = self._quality(trade_date, trades, orders, quotes, fetch_result)
        await self.repository.save_quality({
            "symbol": symbol,
            "trade_date": trade_date,
            "status": quality["status"],
            "first_timestamp": quality.get("first_timestamp"),
            "last_timestamp": quality.get("last_timestamp"),
            "trade_count": quality["trade_count"],
            "order_count": quality["order_count"],
            "quote_count": quality["quote_count"],
            "pagination_complete": quality["pagination_complete"],
            "quote_depth_coverage": quality.get("quote_depth_coverage_pct"),
            "confidence": quality.get("confidence"),
            "warnings": quality.get("warnings") or [],
            "checks": quality.get("checks") or {},
            "source": "numcat",
        })
        return {
            "symbol": symbol,
            "trade_date": trade_date.isoformat(),
            "status": quality["status"],
            "quality": quality,
            "feature_count": len(features),
            "fetch": self._fetch_payload(fetch_result),
        }

    @staticmethod
    def _fetch_payload(result: FetchResult | None) -> dict[str, Any] | None:
        if result is None:
            return None
        return {
            "statuses": result.statuses,
            "rows": result.rows,
            "errors": result.errors,
            "pagination_complete": result.pagination_complete,
            "complete": result.complete,
        }

    @staticmethod
    def _quality(
        trade_date: date,
        trades: list[Any],
        orders: list[Any],
        quotes: list[Any],
        fetch_result: FetchResult | None,
    ) -> dict[str, Any]:
        timestamps = [
            item.timestamp
            for collection in (trades, orders, quotes)
            for item in collection
            if getattr(item, "timestamp", None) is not None
        ]
        first = min(timestamps) if timestamps else None
        last = max(timestamps) if timestamps else None
        complete_by_jobs = bool(fetch_result and fetch_result.complete)
        if fetch_result is None:
            pagination_complete = True
            job_errors: dict[str, str] = {}
        else:
            pagination_complete = bool(fetch_result.statuses) and all(fetch_result.pagination_complete.values())
            job_errors = fetch_result.errors
        depth_observations = 0
        full_depth = 0
        for quote in quotes:
            levels = list(getattr(quote, "bids", []) or []) + list(getattr(quote, "asks", []) or [])
            observed = sum(level.price is not None and level.volume is not None for level in levels)
            depth_observations += observed
            if observed >= 16:
                full_depth += 1
        depth_coverage = full_depth / len(quotes) * 100 if quotes else 0.0
        warnings: list[str] = []
        if not trades:
            warnings.append("未获得逐笔成交样本")
        if not orders:
            warnings.append("未获得逐笔委托样本，撤单/订单生命周期特征受限")
        if not quotes:
            warnings.append("未获得十档盘口样本，OBI和盘口质量特征受限")
        if job_errors:
            warnings.append("至少一种Level-2数据分页未完整结束")
        if timestamps and any(item.date() != trade_date for item in timestamps):
            warnings.append("检测到跨交易日时间戳，已排除出高置信解释")
        if quotes and depth_coverage < 60:
            warnings.append("十档深度字段覆盖不足")
        if first and first.time().hour < 9:
            warnings.append("首条记录早于常规连续竞价时段，请核验供应商时间口径")
        if not (trades or orders or quotes):
            status = "no_data"
        elif complete_by_jobs and trades and quotes and not any("跨交易日" in item for item in warnings):
            status = "complete" if not warnings or (len(warnings) == 1 and "逐笔委托" in warnings[0]) else "degraded"
        elif fetch_result and any(value == "partial" for value in fetch_result.statuses.values()):
            status = "partial"
        else:
            status = "degraded"
        # Confidence is a quality indicator, never a model certainty claim.
        coverage_parts = [min(1.0, len(trades) / 100), min(1.0, len(quotes) / 100)]
        if orders:
            coverage_parts.append(min(1.0, len(orders) / 100))
        if not pagination_complete:
            coverage_parts.append(0.25)
        confidence = sum(coverage_parts) / len(coverage_parts) * 100 if coverage_parts else 0.0
        if status in {"partial", "degraded", "no_data"}:
            confidence = min(confidence, 55.0)
        return {
            "status": status,
            # Keep these as datetime objects for the DateTime quality-snapshot
            # columns. FastAPI serializes them when the result is returned.
            "first_timestamp": first,
            "last_timestamp": last,
            "trade_count": len(trades),
            "order_count": len(orders),
            "quote_count": len(quotes),
            "pagination_complete": pagination_complete,
            "quote_depth_coverage_pct": round(depth_coverage, 1),
            "confidence": round(confidence, 1),
            "warnings": warnings,
            "checks": {
                "trade_date_consistent": not any("跨交易日" in item for item in warnings),
                "cursor_complete": pagination_complete,
                "quote_depth_observations": depth_observations,
                "full_depth_quote_count": full_depth,
                "source_jobs": fetch_result.statuses if fetch_result else {},
            },
        }

    async def summary(
        self,
        symbol: str,
        *,
        trade_date: date | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        normalized = normalize_symbol(symbol)
        target = await self.resolve_trade_date(trade_date)
        quality = await self.repository.get_quality(normalized, target)
        features = await self.repository.load_features(normalized, target)
        configured = self.provider.configured
        pending = self._task_running((normalized, target))
        sync_status: dict[str, Any] | None = None
        if configured and (refresh or (not features and quality is None)) and not pending:
            sync_status = self.start_sync(normalized, target, force=refresh)
            pending = bool(sync_status.get("pending"))
        elif not configured and not features:
            sync_status = {
                "status": "provider_not_configured",
                "pending": False,
                "started": False,
                "message": "Level-2数据源未配置API密钥，普通个股页面继续正常使用。",
            }
        elif pending:
            sync_status = {"status": "pending", "pending": True, "started": False}
        metrics = build_summary(features, quality or {"status": "not_available"})
        jobs = await self.repository.job_status(normalized, target)
        return {
            "symbol": normalized,
            "trade_date": target.isoformat(),
            "provider": self.provider.name,
            "configured": configured,
            "pending": pending,
            "available": bool(features),
            "capabilities": self.provider.capabilities.as_dict(),
            "data_quality": quality or {"status": "not_available", "warnings": []},
            "jobs": jobs,
            "sync": sync_status,
            "summary": metrics,
            # Keep the metric fields at the top-level too, which makes the
            # endpoint convenient for OpenClaw and older frontend consumers.
            **{key: value for key, value in metrics.items() if key not in {"timeline"}},
            "disclaimer": "Level-2指标描述可观测微观结构，不识别真实账户身份，也不单独产生买卖结论。",
        }

    async def timeline(
        self,
        symbol: str,
        *,
        trade_date: date | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        payload = await self.summary(symbol, trade_date=trade_date, refresh=refresh)
        metrics = payload.get("summary") or {}
        return {
            "symbol": payload["symbol"],
            "trade_date": payload["trade_date"],
            "available": payload["available"],
            "pending": payload["pending"],
            "data_quality": payload["data_quality"],
            "timeline": metrics.get("timeline") or [],
            "events": detect_events(metrics.get("timeline") or []),
            "confidence": metrics.get("confidence", 0),
            "disclaimer": payload["disclaimer"],
        }

    async def events(
        self,
        symbol: str,
        *,
        trade_date: date | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        payload = await self.timeline(symbol, trade_date=trade_date, refresh=refresh)
        return {
            "symbol": payload["symbol"],
            "trade_date": payload["trade_date"],
            "available": payload["available"],
            "pending": payload["pending"],
            "data_quality": payload["data_quality"],
            "events": payload["events"],
            "disclaimer": payload["disclaimer"],
        }

    async def sync_status(self, symbol: str, trade_date: date | None = None) -> dict[str, Any]:
        normalized = normalize_symbol(symbol)
        target = await self.resolve_trade_date(trade_date)
        quality = await self.repository.get_quality(normalized, target)
        return {
            "symbol": normalized,
            "trade_date": target.isoformat(),
            "provider": self.provider.name,
            "configured": self.provider.configured,
            "pending": self._task_running((normalized, target)),
            "quality": quality,
            "jobs": await self.repository.job_status(normalized, target),
        }


level2_service = Level2Service()
