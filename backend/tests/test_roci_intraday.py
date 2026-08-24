import unittest
from datetime import datetime

from roci.intraday import _fact, classify_data_status
from roci.intraday_skills import build_shadow_skill_outputs


class RociIntradayContractTests(unittest.TestCase):
    def test_cached_breadth_cannot_be_reported_as_complete_realtime(self):
        status = classify_data_status(
            {"shanghai": {"value": 3000}},
            index_realtime=True,
            turnover={"sh_amount": 100, "is_realtime": True},
            breadth={"up_count": 1000, "down_count": 2000},
            breadth_status="CACHED",
        )
        self.assertEqual(status, "PARTIAL_REALTIME")

    def test_missing_critical_input_is_insufficient(self):
        self.assertEqual(
            classify_data_status(
                {"shanghai": {"value": 3000}},
                index_realtime=True,
                turnover={},
                breadth={},
                breadth_status="UNAVAILABLE",
            ),
            "INSUFFICIENT_DATA",
        )

    def test_shadow_skill_contract_is_complete_and_excluded(self):
        items = build_shadow_skill_outputs({"data_status": "CACHED", "states": {}, "breadth": {}, "turnover": {}, "indexes": {}, "migration": {}, "scenario_validation": {}}, {})
        self.assertEqual([item["skill_id"] for item in items], [f"ROCI-S{number:03d}" for number in range(90, 98)])
        self.assertTrue(all(item["status"] == "SHADOW" and item["shadow_excluded_from_action"] for item in items))

    def test_intraday_evidence_timestamp_is_json_safe(self):
        evidence = _fact("状态", "MIXED", source="roci_intraday_snapshots", timestamp=datetime(2026, 8, 25, 9, 30))
        self.assertEqual(evidence["source_timestamp"], "2026-08-25T09:30:00")


if __name__ == "__main__":
    unittest.main()
