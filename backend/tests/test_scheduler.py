import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from services import scheduler as scheduler_module


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_incomplete_backfills_restarts_persisted_work(self):
        with patch(
            "services.history_cache.history_cache.resume_incomplete_runs",
            new_callable=AsyncMock,
            return_value=[3],
        ) as resume:
            resumed = await scheduler_module.resume_incomplete_backfills()

        self.assertEqual(resumed, [3])
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
            "daily_collection",
            "ai_robot_short_daily",
            "ai_robot_long_daily",
            "ai_robot_anomaly_check",
            "ai_robot_performance_close",
            "dragon_board_close_cache",
            "personal_report_calendar",
            "overnight_preliminary_scan",
            "overnight_entry_scan",
            "overnight_exit_monitor",
            "overnight_force_exit",
        }
        self.assertTrue(expected.issubset(calls))
        self.assertIs(calls["ai_robot_short_daily"].args[0], scheduler_module.refresh_ai_robot_short)
        self.assertIs(calls["ai_robot_long_daily"].args[0], scheduler_module.refresh_ai_robot_long)
        self.assertIs(calls["dragon_board_close_cache"].args[0], scheduler_module.refresh_dragon_board_cache)
        self.assertIs(calls["personal_report_calendar"].args[0], scheduler_module.refresh_personal_report_calendar)
        self.assertIs(calls["overnight_preliminary_scan"].args[0], scheduler_module.run_overnight_preliminary_scan)
        self.assertIs(calls["overnight_entry_scan"].args[0], scheduler_module.run_overnight_entry_scan)
        self.assertIs(calls["overnight_exit_monitor"].args[0], scheduler_module.monitor_overnight_exits)
        self.assertIs(calls["overnight_force_exit"].args[0], scheduler_module.force_overnight_exits)


if __name__ == "__main__":
    unittest.main()
