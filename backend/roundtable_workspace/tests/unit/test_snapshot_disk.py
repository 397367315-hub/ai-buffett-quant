import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from tests.support.harness import WORKBENCH  # noqa: F401

from panel.services.snapshot_build import _DiskHistory
from panel.adapters.numcat import DiskRows, _rows


class SnapshotDiskRowsTests(unittest.TestCase):
    def test_history_day_preserves_decimal_and_symbol_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _DiskHistory()
            # The implementation owns its temp directory; this test only
            # checks the public mapping behavior and Decimal fidelity.
            store.add("2026-10-16", [{"symbol": "600000.SH", "close": Decimal("10.20")}])
            row = store["2026-10-16"].get("600000.SH")
            self.assertEqual(row["close"], Decimal("10.20"))
            self.assertEqual(list(store["2026-10-16"].items())[0][0], "600000.SH")

    def test_raw_response_rows_are_lazy_sequence(self):
        raw = b'{"results":[{"data":{"fields":["symbol","close"],"items":[["600000.SH",1.20],["000001.SZ",2.30]]}}]}'
        with tempfile.TemporaryDirectory() as tmp:
            rows = _rows(raw, Path(tmp) / "rows.jsonl")
            self.assertIsInstance(rows, DiskRows)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["symbol"], "600000.SH")
            self.assertEqual(rows[1]["close"], Decimal("2.30"))


if __name__ == "__main__":
    unittest.main()
