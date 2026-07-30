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


if __name__ == "__main__":
    unittest.main()
