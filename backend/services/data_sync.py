"""兼容旧 API 的真实数据同步入口。"""

import asyncio

from services.history_cache import history_cache


class DataSyncService:
    @staticmethod
    async def sync_concept_flow(force: bool = False) -> dict:
        del force
        return await history_cache.cache_current_concept_flow()

    @staticmethod
    async def sync_market_snapshot() -> dict:
        concept, industry, northbound = await asyncio.gather(
            history_cache.cache_current_concept_flow(),
            history_cache.cache_current_industry_flow(),
            history_cache.cache_current_northbound(),
        )
        return {"concept": concept, "industry": industry, "northbound": northbound}

    @staticmethod
    async def sync_daily_market_data() -> dict:
        return await DataSyncService.sync_market_snapshot()

    @staticmethod
    async def get_cache_stats() -> dict:
        return await history_cache.get_cache_stats()


data_sync = DataSyncService()
