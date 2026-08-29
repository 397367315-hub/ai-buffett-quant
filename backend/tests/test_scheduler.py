import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from services import scheduler as scheduler_module


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_auction_watch_always_uses_independent_auction_strategy(self):
        with patch(
            "services.overnight_strategy.overnight_strategy_service.start",
            new_callable=AsyncMock,
            return_value={"run": {"id": 1}},
        ) as start:
            result = await scheduler_module.run_overnight_auction_watch()

        self.assertEqual(result, {"run": {"id": 1}})
        start.assert_awaited_once_with(
            "auction",
            trigger="schedule",
            background=False,
            strategy_id="overnight_auction_confirm_v1",
        )

    async def test_resume_incomplete_backfills_restarts_persisted_work(self):
        with patch(
            "services.history_cache.history_cache.resume_incomplete_runs",
            new_callable=AsyncMock,
            return_value=[3],
        ) as resume:
            resumed = await scheduler_module.resume_incomplete_backfills()

        self.assertEqual(resumed, [3])
        resume.assert_awaited_once()

    async def test_resume_incomplete_fqe_syncs_restarts_persisted_work(self):
        with patch(
            "services.fqe_reference_data.fqe_reference_data.resume_incomplete_runs",
            new_callable=AsyncMock,
            return_value=[7],
        ) as resume:
            resumed = await scheduler_module.resume_incomplete_fqe_syncs()

        self.assertEqual(resumed, [7])
        resume.assert_awaited_once()

    async def test_scheduler_registers_single_instance_backfill_recovery_job(self):
        fake_scheduler = MagicMock()
        fake_scheduler.running = True

        with patch.object(scheduler_module, "scheduler", fake_scheduler):
            await scheduler_module.start_scheduler()

        recovery_call = next(
            call
            for call in fake_scheduler.add_job.call_args_list
            if call.kwargs["id"] == "resume_history_backfill"
        )
        self.assertIs(recovery_call.args[0], scheduler_module.resume_incomplete_backfills)
        self.assertEqual(recovery_call.args[1], "interval")
        self.assertEqual(recovery_call.kwargs["minutes"], 1)
        self.assertTrue(recovery_call.kwargs["coalesce"])
        self.assertEqual(recovery_call.kwargs["max_instances"], 1)

    async def test_scheduler_registers_market_snapshots_and_personal_automation(self):
        fake_scheduler = MagicMock()
        fake_scheduler.running = True

        with patch.object(scheduler_module, "scheduler", fake_scheduler):
            await scheduler_module.start_scheduler()

        calls = {call.kwargs["id"]: call for call in fake_scheduler.add_job.call_args_list}
        expected = {
            "startup_cache_recovery",
            "midday_collection",
            "midday_ai_research",
            "midday_track_1330",
            "midday_track_1400",
            "midday_track_1430",
            "midday_track_1455",
            "midday_close_validation",
            "decision_2026_morning_freeze",
            "decision_2026_midday_freeze",
            "decision_2026_hypothesis_1330",
            "decision_2026_hypothesis_1400",
            "decision_2026_tail_1440",
            "decision_2026_tail_1455",
            "decision_2026_close_validation",
            "daily_collection",
            "ai_robot_short_daily",
            "ai_robot_long_daily",
            "ai_robot_anomaly_check",
            "ai_robot_performance_close",
            "dragon_board_close_cache",
            "margin_leverage_first_disclosure",
            "margin_leverage_final_disclosure",
            "personal_report_calendar",
            "overnight_preliminary_scan",
            "overnight_entry_scan",
            "overnight_auction_watch",
            "overnight_exit_monitor",
            "overnight_force_exit",
            "resume_fqe_data_sync",
            "fqe_audit_data_close",
        }
        self.assertTrue(expected.issubset(calls))
        self.assertIs(calls["ai_robot_short_daily"].args[0], scheduler_module.refresh_ai_robot_short)
        self.assertIs(calls["ai_robot_long_daily"].args[0], scheduler_module.refresh_ai_robot_long)
        self.assertIs(calls["dragon_board_close_cache"].args[0], scheduler_module.refresh_dragon_board_cache)
        self.assertIs(calls["margin_leverage_first_disclosure"].args[0], scheduler_module.refresh_margin_leverage_cache)
        self.assertIs(calls["margin_leverage_final_disclosure"].args[0], scheduler_module.refresh_margin_leverage_cache)
        self.assertIs(calls["personal_report_calendar"].args[0], scheduler_module.refresh_personal_report_calendar)
        self.assertIs(calls["overnight_preliminary_scan"].args[0], scheduler_module.run_overnight_preliminary_scan)
        self.assertIs(calls["overnight_entry_scan"].args[0], scheduler_module.run_overnight_entry_scan)
        self.assertIs(calls["overnight_auction_watch"].args[0], scheduler_module.run_overnight_auction_watch)
        self.assertIs(calls["overnight_exit_monitor"].args[0], scheduler_module.monitor_overnight_exits)
        self.assertIs(calls["overnight_force_exit"].args[0], scheduler_module.force_overnight_exits)
        self.assertIs(calls["resume_fqe_data_sync"].args[0], scheduler_module.resume_incomplete_fqe_syncs)
        self.assertIs(calls["fqe_audit_data_close"].args[0], scheduler_module.refresh_fqe_audit_data)
        self.assertIs(calls["midday_ai_research"].args[0], scheduler_module.run_midday_research)
        self.assertIs(calls["midday_track_1330"].args[0], scheduler_module.track_midday_research)
        self.assertEqual(calls["midday_track_1330"].kwargs["args"], ["13:30"])
        self.assertIs(calls["midday_close_validation"].args[0], scheduler_module.validate_midday_research)
        self.assertIs(calls["decision_2026_morning_freeze"].args[0], scheduler_module.capture_decision_workbench_window)
        self.assertEqual(calls["decision_2026_morning_freeze"].kwargs["args"], ["morning_1040"])
        self.assertIs(calls["decision_2026_close_validation"].args[0], scheduler_module.close_and_validate_decision_workbench)

    async def test_startup_recovery_uses_recent_cache_during_market_session(self):
        fake_scheduler = MagicMock()
        fake_scheduler.running = True
        fake_data_sync = MagicMock()
        fake_data_sync.get_cache_stats = AsyncMock(
            return_value={"stock_bars": {"to": "2026-08-11"}}
        )
        fake_data_sync.sync_market_snapshot = AsyncMock()
        refreshed = AsyncMock(return_value={"code": 0, "data": {"data_date": "2026-08-11"}})

        with (
            patch.object(scheduler_module, "scheduler", fake_scheduler),
            patch("services.data_sync.data_sync", fake_data_sync),
            patch(
                "services.data_collector.shanghai_now",
                return_value=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            ),
            patch("api.routes.refresh_market_overview_after_sync", refreshed),
        ):
            await scheduler_module.start_scheduler()
            recovery_call = next(
                call
                for call in fake_scheduler.add_job.call_args_list
                if call.kwargs["id"] == "startup_cache_recovery"
            )
            result = await recovery_call.args[0]()

        self.assertEqual(result["status"], "cache_refreshed")
        refreshed.assert_awaited_once_with({"data_date": "2026-08-11"})
        fake_data_sync.sync_market_snapshot.assert_not_awaited()

    async def test_startup_recovery_defers_full_sync_when_cache_is_missing(self):
        fake_scheduler = MagicMock()
        fake_scheduler.running = True
        fake_data_sync = MagicMock()
        fake_data_sync.get_cache_stats = AsyncMock(return_value={"stock_bars": {"to": None}})
        fake_data_sync.sync_market_snapshot = AsyncMock()

        with (
            patch.object(scheduler_module, "scheduler", fake_scheduler),
            patch("services.data_sync.data_sync", fake_data_sync),
        ):
            await scheduler_module.start_scheduler()
            recovery_call = next(
                call
                for call in fake_scheduler.add_job.call_args_list
                if call.kwargs["id"] == "startup_cache_recovery"
            )
            result = await recovery_call.args[0]()

        self.assertEqual(result["status"], "deferred")
        self.assertEqual(result["reason"], "recent_stock_cache_unavailable")
        fake_data_sync.sync_market_snapshot.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
