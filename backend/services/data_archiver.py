"""历史归档兼容层。

旧版本会随机生成历史行情。该行为已移除，历史只能由东方财富回补。
"""

from services.data_sync import data_sync
from services.history_cache import history_cache


async def generate_historical_data(days: int = 365):
    """Compatibility wrapper that queues a real EastMoney backfill."""
    return await history_cache.queue_backfill(days=days, include_stock_bars=True)


async def archive_today_data():
    return await data_sync.sync_concept_flow(force=True)
