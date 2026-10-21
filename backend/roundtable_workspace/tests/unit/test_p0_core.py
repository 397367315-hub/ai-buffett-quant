"""P0 底座单元测试：精度、哈希、契约、日历、新鲜度、终结状态、股票池、迁移、HTTP 边界。

运行：cd WORK_ROOT && python3 -m unittest discover -s tests -t . -v
每个用例用独立临时 runtime，不碰源包与真实账本，不访问供应商。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from tests.support.harness import WORK_ROOT, Harness  # noqa: F401  （同时设置 sys.path）

from panel.domain import calendar as cal_mod, clock, fees, money, trading_rules as tr, universe as uv
from panel.domain.canonical import content_hash, strict_json_loads
from panel.domain.schema import contract
from panel.services import snapshot_build as sb

SPEC_EXAMPLES = WORK_ROOT / "contracts" / ".." / "acceptance"
FIX = json.loads((WORK_ROOT / "acceptance" / "acceptance_cases.json").read_text(encoding="utf-8"))["fixtures"]


class TestMoneyAndFees(unittest.TestCase):
    def test_fee_fixture_exact(self):
        buy = fees.compute_fees("buy", Decimal("10.0000") * 100, "fee_fixture_v1")
        self.assertEqual((buy["commission_minor"], buy["stamp_minor"], buy["transfer_minor"], buy["total_minor"]),
                         ("500", "0", "1", "501"))
        sell = fees.compute_fees("sell", Decimal("11.0000") * 100, "fee_fixture_v1")
        self.assertEqual((sell["commission_minor"], sell["stamp_minor"], sell["transfer_minor"], sell["total_minor"]),
                         ("500", "55", "1", "556"))
        self.assertEqual(100000 + 501, 100501)
        self.assertEqual(110000 - 556, 109444)

    def test_dividend_precision(self):
        cash = Decimal("0.0264499") * 10000
        ref = Decimal("0.0262938") * 10000
        self.assertEqual(money.yuan_to_minor(cash), 26450)
        self.assertEqual(money.yuan_to_minor(ref), 26294)
        self.assertNotEqual(money.yuan_to_minor(cash), money.yuan_to_minor(Decimal("0.03") * 10000))

    def test_float_and_nan_rejected(self):
        for bad in (0.1, float("nan"), "NaN", "Infinity", True):
            with self.assertRaises(money.PrecisionError):
                money.to_decimal(bad)

    def test_big_minor_round_trip(self):
        self.assertEqual(money.minor_str(money.parse_minor("9007199254740993")), "9007199254740993")
        with self.assertRaises(money.MoneyOutOfRange):
            money.parse_minor("9223372036854775808")


class TestCanonicalAndContract(unittest.TestCase):
    def test_spec_example_hashes(self):
        ex = WORK_ROOT.parent / "交易圆桌_十三席开发方案_20261019_v1.0" / "examples"
        if not ex.exists():
            self.skipTest("规格包 examples 不在旁边")
        snap = strict_json_loads((ex / "MarketSnapshot.json").read_text(encoding="utf-8"))
        body = {k: v for k, v in snap.items() if k not in ("snapshot_id", "fingerprint")}
        self.assertEqual(content_hash(body), snap["fingerprint"])
        plan = strict_json_loads((ex / "DecisionPlan.json").read_text(encoding="utf-8"))
        self.assertEqual(content_hash({k: v for k, v in plan.items() if k != "frozen_hash"}), plan["frozen_hash"])
        for name in ("MarketSnapshot", "DecisionPlan", "ReviewResult", "Portfolio", "CaseCreateRequest"):
            obj = strict_json_loads((ex / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(contract().issues(name, obj), [], name)

    def test_decimal_precision_changes_hash(self):
        self.assertNotEqual(content_hash({"p": "1.00"}), content_hash({"p": "1"}))
        with self.assertRaises(TypeError):
            content_hash({"x": 1.5})


class TestCalendarAndFreshness(unittest.TestCase):
    cal = cal_mod.from_fixture(FIX["calendar"])

    def test_session_resolve_not_utc_truncation(self):         # AC-005
        r = self.cal.resolve(clock.parse_utc("2026-10-18T16:30:00Z"))
        self.assertEqual((r["session_date"], r["utc_date"], r["latest_completed_session_date"],
                          r["can_simulate_new_trade"]), ("2026-10-19", "2026-10-18", "2026-10-16", False))

    def test_weekend_and_preopen_fresh(self):                  # AC-004
        for now in ("2026-10-18T10:00:00Z", "2026-10-19T00:10:00Z"):
            self.assertEqual(sb.classify_freshness(self.cal, "2026-10-16", clock.parse_utc(now))[0], "fresh")

    def test_stale_reason(self):                               # AC-003
        f, why = sb.classify_freshness(self.cal, "2026-10-12", clock.parse_utc("2026-10-19T07:10:00Z"))
        self.assertEqual(f, "stale")
        self.assertIn("expected_session_missing", why)

    def test_calendar_unknown(self):                           # AC-015
        f, why = sb.classify_freshness(None, "2026-10-16", clock.parse_utc("2026-10-19T07:10:00Z"))
        self.assertEqual(f, "unknown")
        self.assertIn("calendar_unavailable", why)

    def test_no_simulation_outside_continuous(self):           # AC-043
        for t in ("2026-10-17T02:00:00Z", "2026-10-01T02:00:00Z", "2026-10-19T04:00:00Z"):
            self.assertFalse(self.cal.can_simulate_new_trade(clock.parse_utc(t)))
        self.assertTrue(self.cal.can_simulate_new_trade(clock.parse_utc("2026-10-19T02:00:00Z")))


class TestRowsAndFinality(unittest.TestCase):
    def test_unit_normalization(self):                         # AC-012
        r = sb.normalize_units_demo({"vol": "100", "amount": "1234567.89", "pct_chg": "1.25",
                                     "open": "1", "high": "1", "low": "1", "close": "1"})
        self.assertEqual((r["volume_shares"], r["amount_minor"], r["change_ratio"]), (10000, "123456789", "0.0125"))

    def test_invalid_rows_rejected(self):                      # AC-013
        ok, errors = sb.validate_rows(FIX["invalid_rows"]["rows"])
        self.assertEqual(ok, [])
        self.assertEqual(len({e["row"] for e in errors}), 4)
        self.assertIn("high", {e["field"] for e in errors})

    def test_finality_policy(self):
        cal = self.__class__.cal = cal_mod.from_fixture(FIX["calendar"])
        self.assertEqual(sb.provider_finality("2026-10-16", "2026-10-19T01:00:00Z", cal)[0], "final_confirmed")
        self.assertEqual(sb.provider_finality("2026-10-19", "2026-10-19T02:00:00Z", cal)[0], "not_final")
        self.assertIsNone(sb.provider_finality("2026-10-19", "2026-10-19T08:00:00Z", cal)[0])
        self.assertEqual(sb.bar_status_of(None), "unknown")


class TestUniverse(unittest.TestCase):
    def test_candidate_universe(self):                         # AC-079
        members = []
        for m in FIX["universe_eligibility"]["members"]:
            br = tr.classify_board(m["symbol"], m["stockbasic_market"])
            members.append(uv.Member(m["symbol"], br.board, br.category, br.raw_market, m["special_treatment"], True))
        res = uv.build(uv.CANDIDATE_POLICY, "2026-10-16", members)
        self.assertEqual(set(res.eligible), {"600001.SH", "000001.SZ"})
        self.assertEqual(res.excluded["600003.SH"], "unknown_market")
        self.assertEqual(res.excluded["600002.SH"], "special_treatment")
        self.assertEqual(res.excluded_markets, {"chinext", "star", "star_cdr", "beijing", "b_share_sh", "b_share_sz"})

    def test_vendor_b_share_marked_main_is_excluded(self):
        br = tr.classify_board("900901.SH", "主板", "云赛B股")
        self.assertEqual(br.category, "b_share")
        self.assertEqual(tr.to_symbol("920001"), "920001.BJ")


class TestDatabase(unittest.TestCase):
    def test_migration_checksum_guard_and_backup_restore(self):
        from panel.data import db as dbm
        tmp = Path(tempfile.mkdtemp())
        d = dbm.Database(tmp / "a.sqlite3")
        self.assertEqual(d.migrate(tmp / "bk"), [1, 2, 3])
        self.assertEqual(d.migrate(tmp / "bk"), [])
        bk = d.backup_now(tmp / "bk", "t")
        rep = dbm.restore_to(bk, tmp / "restored.sqlite3")
        self.assertEqual(rep["integrity_check"], "ok")
        self.assertEqual(rep["foreign_key_errors"], [])
        with self.assertRaises(FileExistsError):
            dbm.restore_to(bk, tmp / "restored.sqlite3")
        d.conn().execute("UPDATE schema_migrations SET checksum=? WHERE version=1", ("0" * 64,))
        with self.assertRaises(dbm.MigrationChecksumMismatch):
            d.migrate(tmp / "bk")

    def test_immutable_triggers(self):
        from panel.data import db as dbm
        d = dbm.Database(Path(tempfile.mkdtemp()) / "b.sqlite3")
        d.migrate()
        c = d.conn()
        c.execute("INSERT INTO calendar_versions VALUES('c1','x','2026-01-01','2026-12-31',0,0,?,?,'{}')",
                  ("a" * 64, "2026-10-19T00:00:00Z"))
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("UPDATE calendar_versions SET source='y'")
        # 卡组版本（003）：只追加，不改不删
        c.execute("INSERT INTO card_decks(deck_id, version, source_type, source_id, source_version, replay_id, created_at, "
                  "canonical_payload) VALUES('deck_x', 1, 'case', 'case_x', NULL, NULL, '2026-10-19T00:00:00Z', '{}')")
        for sql in ("UPDATE card_decks SET canonical_payload='{\"cards\":[]}'", "DELETE FROM card_decks"):
            with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                c.execute(sql)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM card_decks").fetchone()[0], 1)


class TestHttpBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = Harness()

    @classmethod
    def tearDownClass(cls):
        cls.h.close()

    def test_unauthenticated_401(self):
        c = self.h.client()
        for p in ("/api/v1/health/data", "/api/v1/snapshots/latest", "/api/dashboard"):
            self.assertEqual(c.get(p)[0], 401, p)

    def test_untrusted_origin_before_session(self):            # AC-018
        c = self.h.client()
        st, obj, _ = c.post("/api/v1/snapshots", {}, headers={"Origin": "https://evil.invalid"}, csrf=False)
        self.assertEqual(st, 403)

    def test_csrf_required_even_with_cookie(self):             # AC-090
        c = self.h.client()
        c.bootstrap()
        body = {"session_date": "2026-10-16", "source_mode": "file_import", "symbols": [], "allow_stale": True}
        self.assertEqual(c.post("/api/v1/snapshots", body, csrf=False)[0], 403)
        self.assertEqual(c.post("/api/v1/snapshots", body, headers={"X-CSRF-Token": "wrong"})[0], 403)

    def test_replay_header_mismatch_403(self):
        c = self.h.client()
        c.bootstrap()
        self.assertEqual(c.get("/api/v1/health/data", headers={"X-Replay-Id": "replay_fake"})[0], 403)

    def test_no_cors(self):
        c = self.h.client()
        st, obj, hdrs = c.request("OPTIONS", "/api/v1/health/data", headers={"Origin": "https://evil.invalid"})
        self.assertEqual(st, 403)
        self.assertNotIn("Access-Control-Allow-Origin", hdrs)

    def test_path_traversal_static(self):
        c = self.h.client()
        for p in ("/../../contracts/openapi.yaml", "/%2e%2e/%2e%2e/panel/data/dashboard.json", "/.env"):
            self.assertIn(c.get(p)[0], (403, 404), p)

    def test_oversized_body_413(self):
        c = self.h.client()
        c.bootstrap()
        st, obj, _ = c.post("/api/v1/snapshots", None, headers={"Content-Length": str(5 << 20)})
        self.assertEqual(st, 413)


if __name__ == "__main__":
    unittest.main(verbosity=2)
