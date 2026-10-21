"""P1–P4 领域纯函数单测：时点可见性、成交假设、公司行动、心理规则来源、切分隔离、提醒闸门与引擎、卡组文案审计。

运行：cd WORK_ROOT && python3 -m unittest tests.unit.test_domain_p1_p4 -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.support.harness import Harness  # noqa: F401  （设置 sys.path）

from panel.domain import clock, corporate_actions as ca, fill_model, pit, psychology
from panel.services import alert_service as al, deck_service, validation_service as vs
from panel.web.errors import ApiError


class TestPointInTime(unittest.TestCase):
    FACTS = [
        {"fact_id": "fin", "report_period": "2025-12-31", "available_at": "2026-03-30T12:00:00Z", "time_precision": "timestamp", "value": "10"},
        {"fact_id": "fin_rev", "revises_fact_id": "fin", "available_at": "2026-04-02T12:00:00Z", "time_precision": "timestamp", "value": "8"},
        {"fact_id": "date_only", "publication_date": "2026-03-30", "available_at": None, "time_precision": "date"},
        {"fact_id": "unknown", "available_at": None, "first_observed_at": "2026-04-03T01:00:00Z", "time_precision": "unknown"},
    ]

    def test_report_period_is_not_availability(self):
        v = pit.visible(self.FACTS, "2026-03-27T07:10:00Z")
        self.assertEqual([f["fact_id"] for f in v["visible"]], [])
        v = pit.visible(self.FACTS, "2026-03-31T07:10:00Z")
        self.assertIn("fin", [f["fact_id"] for f in v["visible"]])

    def test_date_only_shifted_and_unknown_excluded(self):
        v = pit.visible(self.FACTS, "2026-03-30T07:00:00Z")
        self.assertEqual(v["excluded"]["date_only"], "publication_time_unproven")
        self.assertEqual(v["excluded"]["unknown"], "publication_time_unproven")
        self.assertFalse(v["synthetic_midnight_assigned"])

    def test_revision_supersedes_without_rewriting(self):
        v1 = pit.visible(self.FACTS, "2026-03-31T07:10:00Z")
        v2 = pit.visible(self.FACTS, "2026-04-03T07:10:00Z")
        self.assertEqual(pit.value_of(v1, "fin"), "10")
        self.assertEqual(pit.value_of(v2, "fin"), "8")


class TestFillModel(unittest.TestCase):
    def test_intrabar_path_is_unknown(self):
        r = fill_model.resolve_daily_bar(entry_trigger_price="10.5", stop_price="9.5",
                                         bar={"open": "10", "high": "11", "low": "9", "close": "10"})
        self.assertFalse(r["exact_path_known"])
        self.assertEqual(r["verified_real_fill_count"], 0)
        self.assertIn("intrabar_path_ambiguous", r["warnings"])

    def test_limit_down_sell_unknown_not_filled(self):
        r = fill_model.resolve_exit_on_daily(side="sell", bar={"open": "9", "high": "9", "low": "9", "close": "9"},
                                             lower_limit="9.00")
        self.assertEqual(r["fill_status"], "unknown")
        self.assertTrue(r["risk_exposure_remains"])
        self.assertIn("queue_execution_unverified", r["warning_codes"])

    def test_suspended_unfilled(self):
        r = fill_model.resolve_exit_on_daily(side="sell", bar={"open": "9", "high": "9", "low": "9", "close": "9"}, suspended=True)
        self.assertEqual(r["fill_status"], "unfilled")
        self.assertIsNone(r["fill_price"])


class TestCorporateActions(unittest.TestCase):
    def test_dividend_rounding_at_aggregate(self):
        r = ca.cash_dividend(10000, "0.0264499", ex_reference_deduction="0.0262938")
        self.assertEqual(r["cash_receivable_minor"], "26450")
        self.assertFalse(r["per_share_early_rounding"])

    def test_double_count_rejected(self):
        with self.assertRaises(ApiError) as cm:
            ca.return_basis("total_return_adjusted", True)
        self.assertEqual(cm.exception.code, "DOUBLE_COUNTED_CORPORATE_ACTION")

    def test_as_of_adjust_ignores_future_actions(self):
        bars = [{"session_date": "2026-10-15", "raw_close": "10.0000"}, {"session_date": "2026-10-16", "raw_close": "9.9000"}]
        acts = [{"action_id": "known", "available_at": "2026-10-14T08:00:00Z", "ex_session_date": "2026-10-16", "cash_per_share": "0.1"},
                {"action_id": "future", "available_at": "2026-10-20T08:00:00Z", "ex_session_date": "2026-10-21", "split_ratio": "2"}]
        r = ca.adjust_as_of(bars, acts, "2026-10-16T07:10:00Z")
        self.assertEqual(r["used_action_ids"], ["known"])
        self.assertEqual(r["bars"][0]["adjusted_close"], "9.9000")


class TestPsychologyOrigins(unittest.TestCase):
    def test_author_quote_needs_locatable_source(self):
        with self.assertRaises(ApiError):
            psychology.validate_origin({"rule_origin": "author_quote", "origin_ref": None})
        self.assertTrue(psychology.validate_origin({"rule_origin": "author_quote", "origin_ref": "《交易心理分析》第11章"})["accepted"])

    def test_engineering_policy_keeps_origin(self):
        r = psychology.validate_origin({"rule_origin": "engineering_policy", "origin_ref": "ADR-psy-1"})
        self.assertIsNone(r["rewritten_origin"])

    def test_bundled_rules_are_well_formed(self):
        rules = psychology.load_rules()["rules"]
        for r in rules:
            psychology.validate_origin(r)
        self.assertEqual({r["rule_origin"] for r in rules}, {"author_quote", "editorial_summary", "engineering_policy"})
        res = psychology.submit(rules, {r["rule_id"]: True for r in rules})
        self.assertTrue(res["checklist_completion"])
        self.assertIsNone(res["emotional_health_diagnosis"])


class TestSplitAndClaims(unittest.TestCase):
    def test_label_window_overlap(self):
        r = vs.split_check("2026-06-30", "2026-07-01", 5)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "LABEL_WINDOW_OVERLAP")
        self.assertTrue(vs.split_check("2026-06-30", "2026-07-10", 5)["ok"])
        self.assertTrue(vs.split_check("2026-06-30", "2026-07-01", 5, purge=5)["ok"])

    def test_small_sample_claim_blocked(self):
        r = vs.claim_check(42, 4, 5, "original_book_invalid_and_no_skill")
        self.assertFalse(r["claim_allowed"])
        self.assertEqual(r["conclusion"], "inconclusive")


class TestAlertGateAndEngine(unittest.TestCase):
    def test_gate_closed_by_default(self):
        ev = al.evaluate(dict(al.DEFAULT_CAPABILITY))
        self.assertFalse(ev["alert_enabled"])
        self.assertIn("provider_permission_unverified", ev["reasons"])
        self.assertEqual(ev["background_subscriptions"], 0)

    def test_engine_dedupe_and_disconnect(self):
        """合成「已核验分钟级权限 + 显式启用」能力跑确定性引擎：追补去重、旧事件标历史、断线后不再出方向提醒。

        提醒事件外键引用真实快照，所以借用验收上下文物化 fixture 快照（临时运行时，不碰真实数据）。"""
        from tests.acceptance.context import CaseContext
        from tests.acceptance.drivers import alerts as alert_driver
        from tests.acceptance.spec import load_spec
        spec = load_spec()
        case = next(c for c in spec["cases"] if c["test_id"] == "AC-073")
        ctx = CaseContext(case, spec, Path(tempfile.mkdtemp(prefix="rt-unit-alert-")), "unit")
        try:
            r = alert_driver._selftest(ctx)
        finally:
            ctx.close()
        self.assertEqual(r["unique_stored_event_count_before_disconnect"], 2)
        self.assertEqual(r["realtime_notification_count"], 1)
        self.assertTrue(r["old_event_marked_historical"])
        self.assertEqual(r["connection_state_after_disconnect"], "unknown")
        self.assertFalse(r["new_direction_alert_after_disconnect"])
        self.assertTrue(r["last_observation_labeled"])
        self.assertFalse(r["no_risk_assertion"])   # 断线时绝不声称「无风险」

    def test_capability_needs_every_condition(self):
        base = {**al.DEFAULT_CAPABILITY, "provider_permission_verified": True, "permission_evidence": "unit",
                "feed_precision": "minute", "explicit_user_action": True}
        self.assertTrue(al.evaluate(base)["alert_enabled"])
        for k, v in (("provider_permission_verified", False), ("feed_precision", "daily_snapshot"),
                     ("explicit_user_action", False), ("permission_evidence", None)):
            self.assertFalse(al.evaluate({**base, k: v})["alert_enabled"], k)


class TestDeckIncentiveAudit(unittest.TestCase):
    def test_banned_words(self):
        hits = deck_service.incentive_audit(["连胜三天解锁 SSR"])
        self.assertIn("SSR", hits)
        self.assertIn("连胜", hits)
        self.assertEqual(deck_service.incentive_audit(["主方法的失效条件有哪些？"]), [])


if __name__ == "__main__":
    unittest.main()
