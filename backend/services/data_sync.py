"""兼容旧 API 的真实数据同步入口。"""

import asyncio
from datetime import date

from services.history_cache import history_cache
from services.strategic_market_data import strategic_market_data_service


class DataSyncService:
    @staticmethod
    async def sync_concept_flow(force: bool = False) -> dict:
        del force
        return await history_cache.cache_current_concept_flow()

    @staticmethod
    async def sync_market_snapshot() -> dict:
        northbound, stock_bars = await asyncio.gather(
            history_cache.cache_current_northbound(),
            history_cache.cache_current_stock_bars(),
        )
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
            history_cache.cache_current_concept_flow(
                trade_date=trade_date,
                verified_trade_date=verified_trade_date,
            ),
            history_cache.cache_current_industry_flow(
                trade_date=trade_date,
                verified_trade_date=verified_trade_date,
            ),
        )
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
        return {
            "concept": concept,
            "industry": industry,
            "northbound": northbound,
            "stock_bars": stock_bars,
            "strategic_evidence": strategic_evidence,
        }

    @staticmethod
    async def sync_daily_market_data() -> dict:
        return await DataSyncService.sync_market_snapshot()

    @staticmethod
    async def get_cache_stats() -> dict:
        return await history_cache.get_cache_stats()


data_sync = DataSyncService()
