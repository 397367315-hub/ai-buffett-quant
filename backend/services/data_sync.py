"""兼容旧 API 的真实数据同步入口。"""

import asyncio
from collections.abc import Callable
from datetime import date
from typing import Any

from services.history_cache import history_cache
from services.strategic_market_data import strategic_market_data_service


class DataSyncService:
    @staticmethod
    def _progress(
        callback: Callable[[int, str, str], None] | None,
        value: int,
        phase: str,
        message: str,
    ) -> None:
        if callback:
            callback(value, phase, message)

    @staticmethod
    async def _safe_component(name: str, awaitable) -> dict:
        try:
            result = await awaitable
            return result if isinstance(result, dict) else {
                "status": "unavailable", "count": 0, "error": f"{name}_invalid_response",
            }
        except Exception as exc:
            return {
                "status": "unavailable",
                "count": 0,
                "error": f"{type(exc).__name__}: {exc}"[:300],
            }

    @staticmethod
    async def sync_concept_flow(force: bool = False) -> dict:
        del force
        return await history_cache.cache_current_concept_flow()

    @staticmethod
    async def sync_market_snapshot(
        progress: Callable[[int, str, str], None] | None = None,
    ) -> dict[str, Any]:
        DataSyncService._progress(progress, 5, "market_quotes", "正在同步全市场股票行情与北向成交额")
        northbound, stock_bars = await asyncio.gather(
            DataSyncService._safe_component("northbound", history_cache.cache_current_northbound()),
            DataSyncService._safe_component("stock_bars", history_cache.cache_current_stock_bars()),
        )
        DataSyncService._progress(progress, 52, "board_flows", "股票行情已核验，正在同步概念与行业资金")
        raw_trade_date = stock_bars.get("data_date") if isinstance(stock_bars, dict) else None
        try:
            trade_date = date.fromisoformat(str(raw_trade_date)) if raw_trade_date else None
        except ValueError:
            trade_date = None
        verified_trade_date = bool(
            trade_date
            and isinstance(stock_bars, dict)
            and stock_bars.get("status") in {"success", "partial"}
        )
        concept, industry = await asyncio.gather(
            DataSyncService._safe_component(
                "concept",
                history_cache.cache_current_concept_flow(
                    trade_date=trade_date,
                    verified_trade_date=verified_trade_date,
                ),
            ),
            DataSyncService._safe_component(
                "industry",
                history_cache.cache_current_industry_flow(
                    trade_date=trade_date,
                    verified_trade_date=verified_trade_date,
                ),
            ),
        )
        DataSyncService._progress(progress, 76, "strategic_evidence", "板块快照已完成，正在更新市场情绪证据")
        try:
            # Build sentiment evidence only after the verified daily bars have
            # been written, so a new trading day cannot race with aggregation.
            strategic_evidence = await strategic_market_data_service.sync_recent(days=5)
        except Exception as exc:
            strategic_evidence = {
                "status": "unavailable",
                "written": 0,
                "error": type(exc).__name__,
            }
        components = {
            "concept": concept,
            "industry": industry,
            "northbound": northbound,
            "stock_bars": stock_bars,
            "strategic_evidence": strategic_evidence,
        }
        successful = sum(
            isinstance(value, dict) and value.get("status") in {"success", "partial", "current"}
            for value in components.values()
        )
        status = "success" if successful == len(components) else "partial" if successful else "unavailable"
        DataSyncService._progress(progress, 88, "overview", "基础数据同步完成，正在重建市场速览快照")
        return {
            "status": status,
            "data_date": trade_date.isoformat() if trade_date else None,
            **components,
        }

    @staticmethod
    async def sync_daily_market_data() -> dict:
        return await DataSyncService.sync_market_snapshot()

    @staticmethod
    async def get_cache_stats() -> dict:
        return await history_cache.get_cache_stats()


data_sync = DataSyncService()
