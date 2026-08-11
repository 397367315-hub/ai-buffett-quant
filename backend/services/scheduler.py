from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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


async def resume_incomplete_fqe_syncs() -> list[int]:
    from services.fqe_reference_data import fqe_reference_data

    try:
        resumed = await fqe_reference_data.resume_incomplete_runs()
        if resumed:
            print(f"[Scheduler] 已恢复FQE审计数据任务: {resumed}")
        return resumed
    except Exception as exc:
        print(f"[Scheduler] FQE审计数据恢复失败: {type(exc).__name__}")
        return []


async def refresh_fqe_audit_data():
    """Append the latest PE snapshot and refresh strategic market evidence."""
    from services.fqe_reference_data import fqe_reference_data

    try:
        coverage = await fqe_reference_data.coverage()
        full = not coverage.get("security_total") or not coverage.get("valuation_series")
        return await fqe_reference_data.queue_sync(full=full, years=3, force=False)
    except Exception as exc:
        print(f"[Scheduler] FQE审计数据更新失败: {type(exc).__name__}")
        return None


async def refresh_ai_robot_short():
    from services.ai_robot import ai_robot_service

    try:
        return await ai_robot_service.refresh("short", trigger="schedule", background=False)
    except Exception as exc:
        print(f"[Scheduler] AI机器人短期池刷新失败: {type(exc).__name__}")
        return None


async def refresh_ai_robot_long():
    from services.ai_robot import ai_robot_service

    try:
        return await ai_robot_service.refresh("long", trigger="schedule", background=False)
    except Exception as exc:
        print(f"[Scheduler] AI机器人长期池刷新失败: {type(exc).__name__}")
        return None


async def check_ai_robot_anomalies():
    from services.ai_robot import ai_robot_service

    try:
        alerts = await ai_robot_service.check_anomalies()
        if alerts:
            print(f"[Scheduler] AI机器人池异常提醒: {len(alerts)} 条")
        return alerts
    except Exception as exc:
        print(f"[Scheduler] AI机器人池异常检查失败: {type(exc).__name__}")
        return []


async def snapshot_ai_robot_performance():
    from services.ai_robot import ai_robot_service

    try:
        return await ai_robot_service.record_performance_snapshot()
    except Exception as exc:
        print(f"[Scheduler] AI机器人组合统计失败: {type(exc).__name__}")
        return None


async def refresh_personal_report_calendar():
    from services.report_calendar import report_calendar_service

    try:
        return await report_calendar_service.refresh_snapshot()
    except Exception as exc:
        print(f"[Scheduler] 财报日历刷新失败: {type(exc).__name__}")
        return None


async def capture_financial_pit_snapshot():
    from services.stock_features import stock_feature_service

    try:
        return await stock_feature_service.capture_financial_pit()
    except Exception as exc:
        print(f"[Scheduler] 公告日财务PIT快照失败: {type(exc).__name__}")
        return None


async def capture_market_auction_snapshot():
    from services.pit_market_data import pit_market_data_service

    try:
        result = await pit_market_data_service.capture_auction()
        print(f"[Scheduler] 全市场竞价PIT快照: {result}")
        return result
    except Exception as exc:
        print(f"[Scheduler] 全市场竞价PIT快照失败: {type(exc).__name__}")
        return None


async def refresh_topic_intraday_evidence():
    from services.topic_strength import topic_strength_service

    try:
        return await topic_strength_service.get(force=True)
    except Exception as exc:
        print(f"[Scheduler] 题材分时资金证据刷新失败: {type(exc).__name__}")
        return None


async def refresh_dragon_board_cache():
    from services.dragon_board import dragon_board_service

    try:
        return await dragon_board_service.refresh()
    except Exception as exc:
        print(f"[Scheduler] 龙虎榜盘后缓存失败: {type(exc).__name__}")
        return None


async def run_overnight_preliminary_scan():
    from services.overnight_strategy import overnight_strategy_service

    try:
        return await overnight_strategy_service.start(
            "preliminary", trigger="schedule", background=False,
        )
    except Exception as exc:
        print(f"[Scheduler] 一夜持股14:30预扫描失败: {type(exc).__name__}")
        return None


async def run_overnight_entry_scan():
    from services.overnight_strategy import overnight_strategy_service

    try:
        return await overnight_strategy_service.start(
            "entry", trigger="schedule", background=False,
        )
    except Exception as exc:
        print(f"[Scheduler] 一夜持股尾盘复核失败: {type(exc).__name__}")
        return None


async def run_overnight_auction_watch():
    from services.overnight_strategy import overnight_strategy_service

    try:
        return await overnight_strategy_service.start(
            "auction", trigger="schedule", background=False,
        )
    except Exception as exc:
        print(f"[Scheduler] 一夜持股09:25竞价盯盘失败: {type(exc).__name__}")
        return None


async def monitor_overnight_exits():
    from services.overnight_strategy import overnight_strategy_service

    try:
        return await overnight_strategy_service.start(
            "exit", trigger="schedule", background=False,
        )
    except Exception as exc:
        print(f"[Scheduler] 一夜持股早盘退出检查失败: {type(exc).__name__}")
        return None


async def force_overnight_exits():
    from services.overnight_strategy import overnight_strategy_service

    try:
        return await overnight_strategy_service.start(
            "force_exit", trigger="schedule", background=False,
        )
    except Exception as exc:
        print(f"[Scheduler] 一夜持股10:00强制退出失败: {type(exc).__name__}")
        return None


async def start_scheduler(data_collector=None, db_session=None):
    from apscheduler.triggers.cron import CronTrigger
    from services.data_sync import data_sync

    async def daily_data_collection():
        print(f"[Scheduler] 开始盘后数据采集: {datetime.now()}")
        try:
            result = await data_sync.sync_market_snapshot()
            from services.pit_market_data import pit_market_data_service
            universe = await pit_market_data_service.capture_universe()
            result["pit_universe"] = universe
            from api.routes import refresh_market_overview_after_sync
            from services.ai_robot import ai_robot_service
            result["overview"] = (await refresh_market_overview_after_sync(result)).get("data", {})
            await ai_robot_service.warm_market_cache()
            print(f"[Scheduler] 数据采集完成: {result}")
            return result
        except Exception as e:
            print(f"[Scheduler] 数据采集失败: {e}")
            return None

    async def startup_cache_recovery():
        """Refresh the lightweight overview first and avoid a cold-start data stampede."""
        latest_date = None
        try:
            from services.data_collector import shanghai_now
            stats = await data_sync.get_cache_stats()
            latest_value = (stats.get("stock_bars") or {}).get("to")
            latest_date = date.fromisoformat(str(latest_value)) if latest_value else None
            now = shanghai_now()
            cache_is_recent = bool(latest_date and 0 <= (now.date() - latest_date).days <= 3)
            if cache_is_recent:
                from api.routes import refresh_market_overview_after_sync
                overview = await refresh_market_overview_after_sync({"data_date": latest_date.isoformat()})
                print(f"[Scheduler] 冷启动使用近期行情缓存并重建速览: {latest_date}")
                return {"status": "cache_refreshed", "overview": overview.get("data", {})}
        except Exception as exc:
            print(f"[Scheduler] 冷启动缓存检查失败: {type(exc).__name__}")
        print(f"[Scheduler] 冷启动无近期行情缓存，延后至定时或手动全市场同步: {latest_date}")
        return {
            "status": "deferred",
            "data_date": latest_date.isoformat() if latest_date else None,
            "reason": "recent_stock_cache_unavailable",
        }

    scheduler.add_job(
        daily_data_collection,
        CronTrigger(hour=15, minute=20, day_of_week="mon-fri"),
        id="daily_collection",
        name="每日盘后数据采集",
        replace_existing=True,
    )
    scheduler.add_job(
        startup_cache_recovery,
        "date",
        run_date=datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(seconds=20),
        id="startup_cache_recovery",
        name="服务启动后缓存恢复",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        daily_data_collection,
        CronTrigger(hour=9, minute=40, day_of_week="mon-fri"),
        id="opening_market_snapshot",
        name="开盘后全市场股票数与行情快照",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        daily_data_collection,
        CronTrigger(hour=11, minute=35, day_of_week="mon-fri"),
        id="midday_collection",
        name="午间行情快照采集",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=600,
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
    scheduler.add_job(
        resume_incomplete_fqe_syncs,
        "interval",
        minutes=2,
        id="resume_fqe_data_sync",
        name="恢复未完成FQE审计数据任务",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=120,
    )
    scheduler.add_job(
        refresh_fqe_audit_data,
        CronTrigger(hour=16, minute=10, day_of_week="mon-fri"),
        id="fqe_audit_data_close",
        name="FQE上市历史、PE分位与市场证据盘后更新",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=1800,
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
        refresh_ai_robot_short,
        CronTrigger(hour=15, minute=45, day_of_week="mon-fri"),
        id="ai_robot_short_daily", name="AI机器人短期池每日盘后刷新", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=1800,
    )
    scheduler.add_job(
        refresh_ai_robot_long,
        CronTrigger(hour=16, minute=20, day_of_week="mon-fri"),
        id="ai_robot_long_daily", name="AI机器人长期池每日盘后刷新", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=1800,
    )
    scheduler.add_job(
        check_ai_robot_anomalies,
        CronTrigger(hour=9, minute=15, day_of_week="mon-fri"),
        id="ai_robot_anomaly_check", name="AI机器人池盘前异常检查", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=900,
    )
    scheduler.add_job(
        snapshot_ai_robot_performance,
        CronTrigger(hour=16, minute=50, day_of_week="mon-fri"),
        id="ai_robot_performance_close", name="AI机器人池每日盈亏与复盘", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=900,
    )
    scheduler.add_job(
        refresh_dragon_board_cache,
        CronTrigger(hour=15, minute=35, day_of_week="mon-fri"),
        id="dragon_board_close_cache", name="龙虎榜盘后缓存", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=1800,
    )
    scheduler.add_job(
        refresh_personal_report_calendar,
        CronTrigger(hour=8, minute=20, day_of_week="mon-fri"),
        id="personal_report_calendar", name="个人池财报日历刷新", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=1800,
    )
    scheduler.add_job(
        capture_financial_pit_snapshot,
        CronTrigger(hour=16, minute=35, day_of_week="mon-fri"),
        id="financial_pit_close", name="公告日财务PIT增量快照", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=3600,
    )
    scheduler.add_job(
        capture_market_auction_snapshot,
        CronTrigger(hour=9, minute=25, day_of_week="mon-fri"),
        id="market_auction_pit", name="全市场09:25竞价PIT快照", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=60,
    )
    scheduler.add_job(
        refresh_topic_intraday_evidence,
        CronTrigger(hour="9,10,14", minute="35,55", day_of_week="mon-fri"),
        id="topic_intraday_evidence", name="题材分时均价与主动资金证据", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=180,
    )
    scheduler.add_job(
        quant_signal_scan,
        CronTrigger(hour=13, minute=2, day_of_week="mon-fri"),
        id="quant_signal_afternoon", name="量化信号午后扫描", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=300,
    )
    scheduler.add_job(
        run_overnight_preliminary_scan,
        CronTrigger(hour=14, minute=30, day_of_week="mon-fri"),
        id="overnight_preliminary_scan", name="一夜持股14:30预扫描", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=180,
    )
    scheduler.add_job(
        run_overnight_entry_scan,
        CronTrigger(hour=14, minute=55, day_of_week="mon-fri"),
        id="overnight_entry_scan", name="一夜持股14:55入场复核", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=180,
    )
    scheduler.add_job(
        run_overnight_auction_watch,
        CronTrigger(hour=9, minute="24,25,26,27", day_of_week="mon-fri"),
        id="overnight_auction_watch", name="一夜持股09:25 AI竞价盯盘", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=60,
    )
    scheduler.add_job(
        monitor_overnight_exits,
        CronTrigger(hour=9, minute="31,40,50", day_of_week="mon-fri"),
        id="overnight_exit_monitor", name="一夜持股早盘退出监控", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=180,
    )
    scheduler.add_job(
        force_overnight_exits,
        CronTrigger(hour=10, minute=0, day_of_week="mon-fri"),
        id="overnight_force_exit", name="一夜持股10:00强制退出", replace_existing=True,
        coalesce=True, max_instances=1, misfire_grace_time=300,
    )

    if not scheduler.running:
        scheduler.start()
    print("[Scheduler] 定时任务已启动")
