from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def start_scheduler(data_collector=None, db_session=None):
    from apscheduler.triggers.cron import CronTrigger

    async def daily_data_collection():
        print(f"[Scheduler] 开始盘后数据采集: {datetime.now()}")
        try:
            if data_collector:
                concept_flow = await data_collector.fetch_concept_flow(page_size=200)
                industry_flow = await data_collector.fetch_industry_flow(page_size=200)
                market_summary = await data_collector.fetch_market_summary()
                print(f"[Scheduler] 数据采集完成: 概念板块{len(concept_flow)}条, 行业板块{len(industry_flow)}条")
        except Exception as e:
            print(f"[Scheduler] 数据采集失败: {e}")

    scheduler.add_job(
        daily_data_collection,
        CronTrigger(hour=15, minute=30, day_of_week="mon-fri"),
        id="daily_collection",
        name="每日盘后数据采集",
        replace_existing=True,
    )

    scheduler.start()
    print("[Scheduler] 定时任务已启动")
