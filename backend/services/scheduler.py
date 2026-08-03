from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def resume_incomplete_backfills() -> list[int]:
    """Restart persisted cache work after a transient worker or database failure."""
    from services.history_cache import history_cache

    try:
        resumed = await history_cache.resume_incomplete_runs()
        if resumed:
            print(f"[Scheduler] 已恢复历史数据回补任务: {resumed}")
        return resumed
    except Exception as exc:
        # The next interval retries a temporary database/DNS outage.
        print(f"[Scheduler] 历史数据回补恢复失败: {type(exc).__name__}")
        return []


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
    scheduler.add_job(
        resume_incomplete_backfills,
        "interval",
        minutes=1,
        id="resume_history_backfill",
        name="恢复未完成历史数据回补",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60,
    )

    async def quant_signal_scan():
        """Run after each session opens without blocking ordinary API traffic."""
        from services.data_collector import shanghai_now

        if shanghai_now().weekday() >= 5:
            return
        try:
            from quant.signals import quant_signal_service

            job = await quant_signal_service.start_scan(force=False, scheduled_only=True)
            print(f"[Scheduler] 量化信号扫描任务: {job.get('job_id')}")
        except Exception as exc:
            print(f"[Scheduler] 量化信号扫描失败: {type(exc).__name__}")

    scheduler.add_job(
        quant_signal_scan,
        CronTrigger(hour=9, minute=32, day_of_week="mon-fri"),
        id="quant_signal_morning", name="量化信号早盘扫描", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=300,
    )
    scheduler.add_job(
        quant_signal_scan,
        CronTrigger(hour=13, minute=2, day_of_week="mon-fri"),
        id="quant_signal_afternoon", name="量化信号午后扫描", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=300,
    )

    if not scheduler.running:
        scheduler.start()
    print("[Scheduler] 定时任务已启动")
