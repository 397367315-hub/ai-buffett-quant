import unittest

from quant.signals import _eligible_for_scheduled_scan


class QuantGovernanceEligibilityTests(unittest.TestCase):
    def _strategy(self, *, active=True, approved=None, schedule="daily"):
        payload = {"active": active, "scan_schedule": schedule}
        if approved is not None:
            payload["governance"] = {"approved": approved}
        return payload

    def test_scheduler_requires_active_approved_daily_strategy(self):
        self.assertTrue(_eligible_for_scheduled_scan(self._strategy(approved=True)))
        self.assertFalse(_eligible_for_scheduled_scan(self._strategy(approved=False)))
        self.assertFalse(_eligible_for_scheduled_scan(self._strategy(active=False, approved=True)))
        self.assertFalse(_eligible_for_scheduled_scan(self._strategy(approved=True, schedule="manual")))

    def test_missing_governance_is_grandfathered_for_legacy_compatibility(self):
        self.assertTrue(_eligible_for_scheduled_scan(self._strategy()))


if __name__ == "__main__":
    unittest.main()
