"""P1–P3 端到端：同一快照 → 席位 → Case → 计划（研究/模拟）→ 组合/日记 → 复盘 → 卡组 → 回放 → 验证。

真实 HTTP 管线（进程内 App，带 cookie / Origin / CSRF / 幂等键），任务用 drain 同步执行；
数据只来自服务端配置的只读历史日线归档（file_import），不访问供应商、不读 .env。
运行：cd WORK_ROOT && python3 -m unittest tests.integration.test_p1_flow -v
"""
from __future__ import annotations

import unittest

from tests.support.harness import Harness, WORKBENCH

from panel.domain import clock

SYMS = ["600000.SH", "000001.SZ"]


def rule(rid, kind, desc, origin="user_rule", price=None, deadline=None, symbol=None):
    return {"rule_id": rid, "description": desc, "rule_origin": origin, "origin_ref": "user:me" if origin == "user_rule" else "ADR-fixture",
            "source_version": "v1", "rule_version": "v1", "kind": kind, "symbol": symbol, "price_decimal": price,
            "threshold_ratio": None, "deadline_session_date": deadline, "evidence_ids": []}


class TestP1Flow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        archive = WORKBENCH / "panel/data/daily"
        if not archive.is_dir() or not any(archive.glob("*.json")):
            raise unittest.SkipTest("新仓库未携带历史日线归档，P1 端到端验收需外部 fixture")
        clock.set_fixed("2026-10-20T08:00:00Z")      # 周二 16:00（上海）收盘后
        cls.h = Harness()
        cls.c = cls.h.client()
        cls.c.bootstrap()

    @classmethod
    def tearDownClass(cls):
        cls.h.close()
        clock.set_fixed(None)

    def ok(self, st, obj, want=(200, 201, 202)):
        self.assertIn(st, want if isinstance(want, tuple) else (want,), obj)
        return obj

    def job(self, accepted):
        j = self.c.wait_job(accepted["job"]["job_id"])
        return j

    def test_flow(self):
        c = self.c
        # ── 快照（file_import）──
        st, acc, hd = c.post("/api/v1/snapshots", {"session_date": "2026-10-16", "source_mode": "file_import",
                                                   "symbols": SYMS, "allow_stale": True})
        self.ok(st, acc, 202)
        self.assertIn("Location", hd)
        j = self.job(acc)
        self.assertEqual(j["state"], "succeeded", j)
        sid = j["resource_id"]
        st, snap, _ = c.get(f"/api/v1/snapshots/{sid}")
        self.ok(st, snap, 200)
        self.assertEqual(snap["metadata"]["session_date"], "2026-10-16")
        self.assertEqual(snap["metadata"]["source_mode"], "file_import")

        # ── 旧健康探针带上构建指纹（启动器用它判断后台跑的是不是旧版本）──
        st, legacy_health, _ = c.get("/api/health")
        self.ok(st, legacy_health, 200)
        self.assertRegex(legacy_health.get("build") or "", r"^[0-9a-f]{16}$")

        # ── 盘后复盘（只读扩展）：数字带来源，缺的写未提供 ──
        st, pm, _ = c.get(f"/api/v1/snapshots/{sid}/postmarket")
        self.ok(st, pm, 200)
        self.assertEqual(pm["schema_version"], "postmarket_report_v1")
        self.assertFalse(pm["generator"]["model_called"])
        sec = {x["id"]: x for x in pm["sections"]}
        self.assertIn("breadth", sec)
        self.assertEqual(sec["breadth"]["items"][0]["metric_id"], "breadth_up_count")
        self.assertTrue(pm["summary"]["headline"])
        self.assertTrue(pm["markdown"].startswith("# A 股盘后复盘"))
        self.assertTrue(any(g["item"].startswith("两融") for g in pm["gaps"]))
        st, err, _ = c.get("/api/v1/snapshots/snap_not_exist/postmarket")
        self.assertEqual(st, 404)

        # ── 单席运行 ──
        body = {"seat_id": "trading-wyckoff-v1", "snapshot_id": sid, "symbols": SYMS, "cycle_id": "cycle_smoke_1",
                "llm_mode": "disabled", "provider_id": None, "model_id": None, "user_context_id": None, "replay_id": None}
        st, acc, _ = c.post("/api/v1/seat-runs", body)
        self.ok(st, acc, 202)
        j = self.job(acc)
        self.assertIn(j["state"], ("succeeded", "partial"), j)
        st, run, _ = c.get(f"/api/v1/seat-runs/{j['resource_id']}")
        self.ok(st, run, 200)
        self.assertEqual(run["snapshot_id"], sid)

        # ── Case（三席，主方法 + 反证 + 纪律）──
        seats = ["trading-wyckoff-v1", "trading-livermore-v1", "trading-tuixue-v1"]
        roles = [{"seat_id": "trading-wyckoff-v1", "role": "primary"}, {"seat_id": "trading-livermore-v1", "role": "validator"},
                 {"seat_id": "trading-tuixue-v1", "role": "discipline"}]
        cb = {"supersedes_case_id": None, "snapshot_id": sid, "title": "冒烟 Case", "question": "两只票按威科夫是否处于吸筹末段？",
              "symbols": SYMS, "horizon": "swing", "seat_ids": seats, "primary_strategy_id": "trading-wyckoff-v1",
              "role_assignments": roles, "llm_mode": "disabled", "provider_id": None, "model_id": None,
              "user_context_id": None, "replay_id": None}
        st, acc, _ = c.post("/api/v1/cases", cb)
        self.ok(st, acc, 202)
        j = self.job(acc)
        self.assertIn(j["state"], ("succeeded", "partial"), j)
        case_id = j["resource_id"]
        st, case, _ = c.get(f"/api/v1/cases/{case_id}")
        self.ok(st, case, 200)
        st, fin, _ = c.get(f"/api/v1/cases/{case_id}/final")
        self.ok(st, fin, 200)
        self.assertIsNone(fin["executed_return_ratio"]) if "executed_return_ratio" in fin else None

        # ── 组合 ──
        st, pf, _ = c.post("/api/v1/portfolios", {"mode": "paper", "initial_cash_minor": "10000000", "name": "冒烟模拟组合"})
        self.ok(st, pf, 201)
        pid = pf["portfolio_id"]

        # ── 研究计划：可冻结但不可执行 ──
        pb = {"change_reason": "initial", "effective_from": "2026-10-20T08:00:00Z", "portfolio_id": None,
              "basis_portfolio_revision": None, "case_id": case_id, "snapshot_id": sid,
              "primary_strategy_id": "trading-wyckoff-v1", "purpose": "research", "planned_shares": None,
              "cancel_rules": [], "time_exit_rule": None, "exit_deadline": None, "title": "研究：吸筹末段观察",
              "hypothesis": "若放量突破交易区间上沿则进入上涨段", "symbols": ["600000.SH"], "horizon": "swing",
              "entry_rules": [rule("r_entry", "entry", "放量突破区间上沿")], "exit_rules": [], "invalidation_rules": [],
              "risk_budget_minor": None, "max_position_ratio": None, "benchmark_id": None,
              "evaluation_window": {"start_session_date": "2026-10-19", "end_session_date": "2026-10-30"},
              "fee_model_version": None, "replay_id": None}
        st, plan, _ = c.post("/api/v1/plans", pb)
        self.ok(st, plan, 201)
        st, fr, _ = c.post(f"/api/v1/plans/{plan['plan_id']}/freeze", {"expected_version": 1})
        self.ok(st, fr, 200)
        self.assertEqual(fr["status"], "frozen")
        self.assertIn(fr["execution_eligibility"], ("ineligible", "unknown"))

        # ── 模拟计划：四要件 + 预算 + 组合绑定 ──
        sb = dict(pb, purpose="simulation", portfolio_id=pid, basis_portfolio_revision=pf["revision"], planned_shares=100,
                  title="模拟：突破买入 100 股",
                  entry_rules=[rule("r_entry", "entry", "突破 7.80 触发", price="7.8000", symbol="600000.SH")],
                  cancel_rules=[rule("r_cancel", "cancel", "三个交易日未触发则取消")],
                  invalidation_rules=[rule("r_inv", "invalidation", "跌破 7.20 失效", price="7.2000", symbol="600000.SH")],
                  exit_rules=[rule("r_exit", "exit", "跌破 7.30 防守卖出", price="7.3000", symbol="600000.SH")],
                  time_exit_rule=rule("r_time", "time_exit", "持有满 10 个交易日退出", deadline="2026-11-03"),
                  exit_deadline="2026-11-03T07:00:00Z", risk_budget_minor="50000", max_position_ratio="0.1",
                  fee_model_version="legacy-paper-fee-v1", benchmark_id=None)
        st, sp, _ = c.post("/api/v1/plans", sb)
        self.ok(st, sp, 201)
        st, sfr, _ = c.post(f"/api/v1/plans/{sp['plan_id']}/freeze", {"expected_version": 1})
        self.ok(st, sfr, 200)

        # ── 日记：观察记录 ──
        jb = {"portfolio_id": pid, "event_type": "note", "mode": "paper", "effective_at": "2026-10-20T08:00:00Z",
              "symbol": None, "side": None, "shares": None, "price_decimal": None, "net_cash_delta_minor": None,
              "snapshot_id": None, "plan_id": None, "plan_version": None, "corrects_event_id": None,
              "correction_action": None, "corrected_event_type": None, "note": "今天只观察，不交易",
              "replay_id": None, "expected_portfolio_revision": pf["revision"], "evidence_ids": [], "reported_fees": None}
        st, ev, _ = c.post("/api/v1/journal/events", jb)
        self.ok(st, ev, 201)
        st, evs, _ = c.get(f"/api/v1/journal/events?portfolio_id={pid}")
        self.ok(st, evs, 200)
        self.assertGreaterEqual(len(evs["items"]), 2)

        # ── 复盘（事前）──
        st, acc, _ = c.post("/api/v1/reviews", {"plan_id": sp["plan_id"], "plan_version": 1, "frozen_hash": sfr["frozen_hash"],
                                                 "review_kind": "preflight", "as_of_snapshot_id": sid})
        self.ok(st, acc, 202)
        j = self.job(acc)
        self.assertIn(j["state"], ("succeeded", "partial"), j)
        st, rv, _ = c.get(f"/api/v1/reviews/{j['resource_id']}")
        self.ok(st, rv, 200)
        self.assertIsNone(rv["directional_price_return_ratio"])

        # ── 卡组（P3）──
        for src in ({"source_type": "case", "source_id": case_id, "source_version": None, "title": "Case 复习"},
                    {"source_type": "plan", "source_id": sp["plan_id"], "source_version": "1", "title": "计划要件"},
                    {"source_type": "review", "source_id": rv["review_id"], "source_version": None, "title": "复盘要点"}):
            st, deck, _ = c.post("/api/v1/decks", src)
            self.ok(st, deck, 201)
            self.assertGreaterEqual(len(deck["cards"]), 1)
            st, d2, _ = c.get(f"/api/v1/decks/{deck['deck_id']}")
            self.ok(st, d2, 200)

        # ── 工作区索引 ──
        st, idx, _ = c.get("/api/v1/workspace/index")
        self.ok(st, idx, 200)
        self.assertTrue(idx["cases"] and idx["plans"] and idx["portfolios"] and idx["decks"])

        # ── P4 关闭 ──
        st, err, _ = c.get("/api/v1/alerts")
        self.assertEqual(st, 501, err)
        self.assertEqual(err["error"]["code"], "FEATURE_DISABLED")

        # ── 验证（候选研究，冻结后排队）──
        st, reg, _ = c.get("/api/v1/seats/registry")
        seat = next(s for s in reg["seats"] if s["seat_id"] == "trading-wyckoff-v1")
        from panel.services import archive_service
        m = archive_service.manifest()
        mi = {"target_type": "seat", "target_id": "trading-wyckoff-v1", "target_version": seat["rule_version"],
              "dataset_fingerprint": m["fingerprint"], "rule_version": seat["rule_version"],
              "calendar_version": m["calendar_version"], "fee_model_version": "legacy-paper-fee-v1",
              "execution_model_version": "candidate_research_t1_open_th_close_v1", "seed": 20261019,
              "window": {"start_session_date": "2026-09-01", "end_session_date": "2026-10-09"},
              "split_policy": "full_sample_descriptive", "hypothesis": "威科夫候选 5 日后验变化扣成本后优于等权基准",
              "skill_sha256": seat["skill_sha256"], "code_revision": "smoke", "universe_policy_id": "universe_cn_mainboard_v1",
              "universe_fingerprint": "0" * 64, "sample_selection_policy": "every_10_sessions horizon=5",
              "inclusion_criteria": ["沪深主板非 ST"], "exclusion_criteria": ["当日停牌"], "training_window": None,
              "benchmark_id": "equal_weight_universe", "baseline_policy": "等权全池", "allocation_budget_minor": None,
              "trial_budget": 10,
              "statistical_plan": {"primary_metric": "excess_return_ratio", "secondary_metrics": [], "null_hypothesis": "均值=0",
                                   "minimum_sample_size": 2, "confidence_interval_method": "hac lag=1 block=2 replicates=200",
                                   "significance_level_ratio": "0.05", "multiple_testing_policy": "none_single_test",
                                   "outlier_policy": "keep_all"},
              "stopping_rules": [{"condition_id": "stop_budget", "description": "预算到界即停"},
                                 {"condition_id": "stop_integrity", "description": "输入含截点之后数据即停"}]}
        st, acc, _ = c.post("/api/v1/validations", {"evaluation_manifest": mi})
        self.ok(st, acc, 202)
        j = self.c.wait_job(acc["job"]["job_id"], timeout=600)
        self.assertIn(j["state"], ("succeeded", "partial"), j)
        st, vr, _ = c.get(f"/api/v1/validations/{j['resource_id']}")
        self.ok(st, vr, 200)
        self.assertIsNone(vr["metrics"]["execution_return_ratio"])
        self.assertEqual(vr["evaluation_manifest"]["content"], mi)

        # ── 回放：建 → 记决定 → 推进 → 揭晓 ──
        st, err, _ = c.post("/api/v1/replays", {"symbols": SYMS, "start_session_date": "2026-09-15",
                                                "end_session_date": "2026-09-30", "strict_as_of": True,
                                                "data_archive_fingerprint": "f" * 64, "initial_cash_minor": "10000000"})
        self.assertEqual(st, 422, err)
        st, acc, _ = c.post("/api/v1/replays", {"symbols": SYMS, "start_session_date": "2026-09-15",
                                                "end_session_date": "2026-09-30", "strict_as_of": True,
                                                "data_archive_fingerprint": m["fingerprint"], "initial_cash_minor": "10000000"})
        self.ok(st, acc, 202)
        rid = acc["job"]["resource_id"]
        ctx = c.refresh_context()
        self.assertEqual(ctx["mode"], "replay")
        self.assertEqual(ctx["replay_id"], rid)
        j = c.wait_job(acc["job"]["job_id"])
        self.assertEqual(j["state"], "succeeded", j)
        st, rep, _ = c.get(f"/api/v1/replays/{rid}")
        self.ok(st, rep, 200)
        self.assertEqual(rep["state"], "active")
        # 漏了 X-Replay-Id 也按会话截点；legacy 路由拒绝
        st, err, _ = c.get("/api/dashboard")
        self.assertEqual(st, 409, err)
        st, vis, _ = c.get(f"/api/v1/snapshots/{rep['visible_snapshot_id']}")
        self.ok(st, vis, 200)
        self.assertEqual(vis["metadata"]["session_date"], "2026-09-15")
        st, live_snap, _ = c.get(f"/api/v1/snapshots/{sid}")
        self.assertEqual(st, 404, live_snap)
        # 未保存决定不能推进
        st, err, _ = c.post(f"/api/v1/replays/{rid}/step", {"expected_cursor_session_date": "2026-09-15", "steps": 1})
        self.assertEqual(st, 409, err)
        self.assertEqual(err["error"]["code"], "DECISION_REQUIRED")
        st, pfs, _ = c.get("/api/v1/workspace/index")
        rpid = pfs["portfolios"][0]["portfolio_id"]
        st, rpf, _ = c.get(f"/api/v1/portfolios/{rpid}")
        self.ok(st, rpf, 200)
        note = dict(jb, portfolio_id=rpid, effective_at=rep["knowledge_cutoff_at"], note="继续等待", replay_id=rid,
                    expected_portfolio_revision=rpf["revision"])
        st, ev, _ = c.post("/api/v1/journal/events", note)
        self.ok(st, ev, 201)
        st, rep2, _ = c.post(f"/api/v1/replays/{rid}/step", {"expected_cursor_session_date": "2026-09-15", "steps": 1})
        self.ok(st, rep2, 200)
        self.assertEqual(rep2["cursor_session_date"], "2026-09-16")
        st, rep3, _ = c.post(f"/api/v1/replays/{rid}/reveal", {"expected_cursor_session_date": "2026-09-16",
                                                               "acknowledge_training_end": True})
        self.ok(st, rep3, 200)
        self.assertTrue(rep3["revealed"])
        st, err, _ = c.post(f"/api/v1/replays/{rid}/step", {"expected_cursor_session_date": "2026-09-16", "steps": 1})
        self.assertEqual(st, 409, err)
        # 切回真实盘
        ctx = c.refresh_context()
        st, back, _ = c.post("/api/v1/session/context", {"expected_context_revision": ctx["context_revision"], "mode": "live",
                                                         "replay_id": None, "acknowledge_training_end": True})
        self.ok(st, back, 200)
        self.assertEqual(back["mode"], "live")


if __name__ == "__main__":
    unittest.main()
