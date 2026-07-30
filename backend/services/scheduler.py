from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def start_scheduler(data_collector=None, db_session=None):
    from apscheduler.triggers.cron import CronTrigger
    from services.data_sync import data_sync

    async def daily_data_collection():
        print(f"[Scheduler] 开始盘后数据采集: {datetime.now()}")
        try:
            result = await data_sync.sync_market_snapshot()
            print(f"[Scheduler] 数据采集完成: {result}")
        except Exception as e:
            print(f"[Scheduler] 数据采集失败: {e}")

    scheduler.add_job(
        daily_data_collection,
        CronTrigger(hour=15, minute=20, day_of_week="mon-fri"),
        id="daily_collection",
        name="每日盘后数据采集",
        replace_existing=True,
    )

    if not scheduler.running:
        scheduler.start()
    print("[Scheduler] 定时任务已启动")
