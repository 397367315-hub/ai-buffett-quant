import asyncio
import json
import tempfile
from decimal import Decimal
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from starlette.requests import Request

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "roundtable_workspace" / "records/roundtable/2026-10-18-圆桌面板设计"))

from panel.services.feature_rows import CachedDailyRows, FeatureRows
from panel.services.seat_runner import _rows_from_blob
from services.roundtable_legacy import RoundtableLegacyRefresh
from api.roundtable_legacy_routes import _bound_runtime
class RoundtableStreamingTests(unittest.TestCase):
    def test_feature_rows_preserve_null_decimal_and_large_integer(self):
        rows = FeatureRows([{"symbol": "600000", "amount": 9007199254740993,
                             "ratio": Decimal("0.123456789012"), "missing": None}])
        row = rows[0]
        self.assertEqual(row["amount"], 9007199254740993)
        self.assertEqual(row["ratio"], Decimal("0.123456789012"))
        self.assertIsNone(row["missing"])

    def test_feature_rows_mmap_columns_preserve_dynamic_and_exact_values(self):
        rows = FeatureRows([
            {"symbol": "600000", "large": 9007199254740993, "ratio": Decimal("0.123456789012"), "dynamic_ret20": None},
            {"symbol": "000001", "large": 7, "ratio": 1.25, "dynamic_ret20": "pending"},
        ])
        self.assertEqual(rows[0]["large"], 9007199254740993)
        self.assertEqual(rows[0]["ratio"], Decimal("0.123456789012"))
        self.assertIsNone(rows[0]["dynamic_ret20"])
        self.assertEqual(rows[1]["dynamic_ret20"], "pending")
        rows[0]["dynamic_ret20"] = Decimal("2.5")
        self.assertEqual(rows[0]["dynamic_ret20"], Decimal("2.5"))
        rows.close()

    def test_cached_daily_rows_are_repeatable_and_filterable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "20261001.json").write_text(json.dumps([{"symbol": "600000", "x": None}]))
            (root / "20261002.json").write_text(json.dumps([{"symbol": "000001", "x": 2}]))
            cached = CachedDailyRows([(root / "20261001.json", 1), (root / "20261002.json", 1)])
            self.assertEqual(len(cached), 2)
            self.assertEqual(list(cached), list(cached))
            self.assertEqual(len(cached.without_day("20261001")), 1)

    def test_seat_runner_accepts_shared_code_data_envelope(self):
        raw = json.dumps({"code": 200, "data": {
            "fields": ["symbol", "amount"],
            "items": [["600000.SH", "9007199254740993"]],
        }}).encode()
        rows = _rows_from_blob(raw, "numcat_matrix_v1")
        self.assertEqual(rows[0]["symbol"], "600000.SH")
        self.assertEqual(rows[0]["amount"], "9007199254740993")

    def test_legacy_refresh_state_marks_queued_job_interrupted_on_rebind(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = SimpleNamespace(runtime_dir=Path(tmp), unavailable=False, mark_dirty=lambda: None)
            (Path(tmp) / "legacy_refresh.json").write_text(json.dumps({
                "status": "queued", "job_id": "legacy-test",
            }))
            refresh = RoundtableLegacyRefresh()
            state = refresh.status(runtime)
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["stage"], "interrupted")
            self.assertIn("重启", state["error"])

    def test_legacy_refresh_requires_csrf_for_bound_session(self):
        async def run():
            runtime = SimpleNamespace(
                session_tokens={"session-1": "opaque"},
                db=object(), unavailable=False,
            )
            scope = {"type": "http", "method": "POST", "path": "/api/v1/roundtable/host/refresh",
                     "headers": [(b"origin", b"http://localhost:3000"),
                                 (b"x-roundtable-session", b"session-1")]}
            request = Request(scope)
            session = {"mode": "live"}
            with patch("api.roundtable_legacy_routes.get_roundtable_runtime", return_value=runtime), \
                 patch("panel.web.security.load_session", return_value=session):
                with self.assertRaises(Exception) as ctx:
                    await _bound_runtime(request, write=True)
                self.assertEqual(getattr(ctx.exception, "status_code", None), 403)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
