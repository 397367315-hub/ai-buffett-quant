"""席位智能体（M2/M4/M5）：走真实 DeepSeek 客户端代码路径，但用注入的 transport 回放固定响应，不联网。

覆盖：正常回答与审计、引用/事实/政策三类拒收、空引用、密钥缺失、预算超限、回放禁用、
复盘解读的数字越界与网页引用过滤、圆桌逐席作答。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support.harness import WORK_ROOT, Harness

from panel.adapters import deepseek as ds
from panel.domain import clock
from panel.services import kb_service, seat_agent

BUILDER = WORK_ROOT / "tools/build_seat_kb.py"
SEAT = "trading-wyckoff-v1"


def _resp(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode()


class FakeClient(ds.DeepSeekClient):
    """把 HTTP 换成固定响应；其余逻辑（预算、审计、JSON 解析）都是真的。"""

    def __init__(self, chat: dict | str, *, search: dict | None = None, limit: int = 200000):
        self._chat = chat
        self._search = search or {"results": []}
        self._buf = [b""]
        super().__init__(transport=self._transport, runtime_dir=Path(tempfile.mkdtemp(prefix="rt-llm-")),
                         max_retry=0)
        self.budget.limit = limit

    def _transport(self, method, url, headers, body):
        if url.endswith("/chat/completions"):
            content = self._chat if isinstance(self._chat, str) else json.dumps(self._chat, ensure_ascii=False)
            return 200, _resp({"model": "deepseek-flash", "usage": {"total_tokens": 321},
                               "choices": [{"message": {"content": content}}]})
        if url.endswith("/v1/tools/search"):
            return 200, _resp(self._search)
        return 404, b'{"error":{"message":"not found"}}'


class ScriptedClient(FakeClient):
    """按顺序吐出预设的响应，用来测「工具循环」的每一步。"""

    def __init__(self, payloads: list[dict]):
        self._queue = list(payloads)
        self.calls = []
        super().__init__({"answer": "unused", "kb_refs": []})

    def chat_json(self, system, user, *, max_tokens=1200, temperature=0.0):
        self.calls.append((system, user, max_tokens))
        data = self._queue.pop(0) if self._queue else {"step": "final", "answer": {}}
        return {"data": data, "raw": json.dumps(data, ensure_ascii=False), "usage": {},
                "model": "deepseek-flash", "tokens": 10}


class TestSeatAgent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb_root = Path(tempfile.mkdtemp(prefix="rt-kb-agent-"))
        r = subprocess.run([sys.executable, str(BUILDER), "--out", str(cls.kb_root)], capture_output=True, text=True)
        if r.returncode != 0:
            raise unittest.SkipTest(f"知识库构建失败：{(r.stderr or r.stdout)[:200]}")
        os.environ["ROUNDTABLE_KB_ROOT"] = str(cls.kb_root)
        os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")     # 仅本地假值，不写盘
        kb_service._index.cache_clear()
        clock.set_fixed("2026-10-21T09:00:00Z")
        cls.h = Harness(runtime=Path(tempfile.mkdtemp(prefix="rt-agent-")), workers=False)
        cls.settings = cls.h.settings
        cls.snap = {"snapshot_id": "snap_agent_unit", "metadata": {"session_date": "2026-10-19"},
                    "metrics": [{"metric_id": "breadth_up_count", "value": "2202", "unit": "count", "symbol": None},
                                {"metric_id": "limit_up_count", "value": "47", "unit": "count", "symbol": None}],
                    "instruments": []}

    @classmethod
    def tearDownClass(cls):
        cls.h.close()
        os.environ.pop("ROUNDTABLE_KB_ROOT", None)
        clock.set_fixed(None)
        kb_service._index.cache_clear()

    def _kb_id(self, q="SOS 缩量回踩") -> str:
        return kb_service.search(SEAT, q, k=1)["results"][0]["kb_id"]

    def test_ask_happy_path_and_audit(self):
        client = FakeClient({"answer": "按威科夫，SOS 之后要等缩量回踩，不是突破当天买。",
                            "facts_used": ["breadth_up_count"],
                            "kb_refs": [{"kb_id": self._kb_id(), "quote": ""}],
                            "uncertainties": ["没有分时数据"], "missing_inputs": ["minute_bars"],
                            "action": "explain_only"})
        out = seat_agent.ask(self.h.db, self.settings, seat_id=SEAT, question="SOS 之后怎么买？",
                             snapshot=self.snap, client=client)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["answer"]["kb_refs"][0]["locator"])
        self.assertEqual(out["answer"]["action"], "explain_only")
        row = self.h.db.one("SELECT status, provider_id, prompt_sha256 FROM model_runs WHERE model_run_id=?",
                            (out["audit"]["model_run_id"],))
        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["provider_id"], "deepseek")
        self.assertEqual(len(row["prompt_sha256"]), 64)

    def test_reject_unknown_kb_id(self):
        client = FakeClient({"answer": "这是一段解释。", "facts_used": [], "kb_refs": [{"kb_id": "nope", "quote": ""}]})
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            seat_agent.ask(self.h.db, self.settings, seat_id=SEAT, question="q", snapshot=self.snap, client=client)
        self.assertEqual(cm.exception.code, "LLM_BAD_CITATION")

    def test_reject_quote_not_in_snippet(self):
        kid = self._kb_id()
        client = FakeClient({"answer": "解释。", "facts_used": [], "kb_refs": [{"kb_id": kid, "quote": "这句话书里没有"}]})
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            seat_agent.ask(self.h.db, self.settings, seat_id=SEAT, question="q", snapshot=self.snap, client=client)
        self.assertEqual(cm.exception.code, "LLM_BAD_CITATION")

    def test_reject_unknown_fact(self):
        client = FakeClient({"answer": "解释。", "facts_used": ["not_a_fact"],
                            "kb_refs": [{"kb_id": self._kb_id(), "quote": ""}]})
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            seat_agent.ask(self.h.db, self.settings, seat_id=SEAT, question="q", snapshot=self.snap, client=client)
        self.assertEqual(cm.exception.code, "LLM_UNKNOWN_FACT")

    def test_reject_policy_words(self):
        client = FakeClient({"answer": "目标价 12 元，可以买。", "facts_used": [],
                            "kb_refs": [{"kb_id": self._kb_id(), "quote": ""}]})
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            seat_agent.ask(self.h.db, self.settings, seat_id=SEAT, question="q", snapshot=self.snap, client=client)
        self.assertEqual(cm.exception.code, "LLM_POLICY")

    def test_reject_without_citation(self):
        client = FakeClient({"answer": "解释。", "facts_used": [], "kb_refs": []})
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            seat_agent.ask(self.h.db, self.settings, seat_id=SEAT, question="q", snapshot=self.snap, client=client)
        self.assertEqual(cm.exception.code, "LLM_NO_CITATION")

    def test_reject_non_json(self):
        client = FakeClient("这不是 JSON")
        with self.assertRaises(ds.LlmUnavailable) as cm:
            seat_agent.ask(self.h.db, self.settings, seat_id=SEAT, question="q", snapshot=self.snap, client=client)
        self.assertEqual(cm.exception.code, "LLM_BAD_JSON")

    # ── 工具循环（ADR-012） ──
    def _final(self, kid: str) -> dict:
        return {"step": "final", "answer": {"answer": "按威科夫，SOS 后要等缩量回踩。", "facts_used": [],
                                            "kb_refs": [{"kb_id": kid, "quote": ""}], "uncertainties": [],
                                            "missing_inputs": [], "action": "explain_only"}}

    def test_agent_chat_tool_then_final(self):
        kid = self._kb_id()
        client = ScriptedClient([{"step": "tool", "tool": "search_kb",
                                  "args": {"query": "SOS 缩量回踩", "k": 3}, "why": "先查定义"},
                                 self._final(kid)])
        out = seat_agent.agent_chat(self.h.db, self.settings, seat_id=SEAT, question="SOS 之后怎么买？",
                                    snapshot=self.snap, client=client)
        self.assertEqual(out["mode"], "agent")
        self.assertEqual(len(out["steps"]), 1)
        self.assertEqual(out["steps"][0]["tool"], "search_kb")
        self.assertTrue(out["steps"][0]["ok"])
        self.assertGreaterEqual(out["kb_hits"], 1)
        self.assertEqual(out["answer"]["kb_refs"][0]["kb_id"], kid)
        self.assertEqual(len(client.calls), 2)
        row = self.h.db.one("SELECT status FROM model_runs WHERE model_run_id=?", (out["audit"]["model_run_id"],))
        self.assertEqual(row["status"], "accepted")

    def test_agent_chat_unknown_tool_is_fed_back(self):
        kid = self._kb_id()
        client = ScriptedClient([{"step": "tool", "tool": "rm_rf", "args": {}}, self._final(kid)])
        out = seat_agent.agent_chat(self.h.db, self.settings, seat_id=SEAT, question="q", snapshot=self.snap,
                                    client=client)
        self.assertFalse(out["steps"][0]["ok"])
        self.assertIn("TOOL_UNKNOWN", out["steps"][0]["summary"])
        self.assertEqual(out["status"], "ok")

    def test_agent_chat_step_limit(self):
        client = ScriptedClient([{"step": "tool", "tool": "search_kb", "args": {"query": "回踩"}}] * 3)
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            seat_agent.agent_chat(self.h.db, self.settings, seat_id=SEAT, question="q", snapshot=self.snap,
                                  client=client, max_steps=2)
        self.assertEqual(cm.exception.code, "LLM_STEP_LIMIT")

    def test_agent_chat_bad_protocol(self):
        client = ScriptedClient([{"foo": 1}])
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            seat_agent.agent_chat(self.h.db, self.settings, seat_id=SEAT, question="q", snapshot=self.snap,
                                  client=client)
        self.assertEqual(cm.exception.code, "LLM_BAD_PROTOCOL")

    def test_agent_chat_final_still_needs_citation(self):
        invalid = {"step": "final", "answer": {"answer": "没出处的结论", "kb_refs": []}}
        client = ScriptedClient([invalid, invalid])
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            seat_agent.agent_chat(self.h.db, self.settings, seat_id=SEAT, question="q", snapshot=self.snap,
                                  client=client)
        self.assertEqual(cm.exception.code, "LLM_NO_CITATION")

    def test_agent_chat_repairs_policy_once_and_aggregates_tokens(self):
        kid = self._kb_id()
        invalid = {"step": "final", "answer": {"answer": "可以买入，目标价 12 元。", "kb_refs": []}}
        client = ScriptedClient([invalid, self._final(kid)])
        out = seat_agent.agent_chat(self.h.db, self.settings, seat_id=SEAT, question="q", snapshot=self.snap,
                                    client=client)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["answer"]["kb_refs"][0]["kb_id"], kid)
        self.assertEqual(out["tokens"], 20)
        self.assertEqual(len(client.calls), 2)
        self.assertNotIn("可用工具", client.calls[1][0])

    def test_agent_chat_repair_does_not_accept_fake_citation(self):
        kid = self._kb_id()
        invalid = {"step": "final", "answer": {"answer": "可以买入。", "kb_refs": []}}
        fake = {"step": "final", "answer": {"answer": "中性研究表述。", "kb_refs": [{"kb_id": kid, "quote": "书里没有"}]}}
        client = ScriptedClient([invalid, fake])
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            seat_agent.agent_chat(self.h.db, self.settings, seat_id=SEAT, question="q", snapshot=self.snap,
                                  client=client)
        self.assertEqual(cm.exception.code, "LLM_BAD_CITATION")

    def test_agent_chat_includes_ui_context(self):
        kid = self._kb_id()
        client = ScriptedClient([self._final(kid)])
        seen = {}
        orig = client.chat_json

        def capture(system, user, **kw):
            seen["user"] = user
            return orig(system, user, **kw)

        client.chat_json = capture
        seat_agent.agent_chat(self.h.db, self.settings, seat_id=SEAT, question="这只票怎么看？", snapshot=self.snap,
                              client=client, context="股票：平安银行（000001.SZ）；脚本给的止损：10.20")
        self.assertIn("界面里选中的候选信息", seen["user"])
        self.assertIn("000001.SZ", seen["user"])
        self.assertIn("不是核验过的事实", seen["user"])

    def test_tools_read_skill_and_facts(self):
        from panel.services import seat_tools
        skill = seat_tools.read_skill_text(SEAT)
        self.assertTrue(skill["ok"], skill)
        self.assertTrue(skill["text"])
        self.assertTrue(skill["headings"])
        one = seat_tools.read_skill_text(SEAT, skill["headings"][0])
        self.assertTrue(one["ok"])
        bad = seat_tools.read_skill_text(SEAT, "这一节肯定不存在")
        self.assertFalse(bad["ok"])
        facts = seat_agent.facts_from_snapshot(self.snap, ["000001.SZ"])
        state = {}
        out = seat_tools.run_tool("get_facts", {}, db=self.h.db, settings=self.settings, seat_id=SEAT,
                                  snapshot=self.snap, facts=facts, state=state)
        self.assertTrue(out["ok"])
        self.assertIn("limit_up_count", facts)
        out2 = seat_tools.run_tool("search_kb", {"query": "SOS"}, db=self.h.db, settings=self.settings,
                                   seat_id=SEAT, snapshot=self.snap, facts=facts, state=state)
        self.assertTrue(out2["ok"])
        self.assertTrue(state["kb_hits"])

    # ── M3 个股分析 ──
    def _run(self, **over):
        run = {"seat_run_id": "seatrun_test", "seat_id": SEAT, "run_state": "succeeded", "verdict": "watch",
               "applicability": "applicable", "applicability_reason": "日线可判", "explanation": "缩量回踩未出现",
               "counter_evidence": ["量能不足"], "missing_inputs": ["minute_bars"],
               "condition_results": [
                   {"condition_id": "wyckoff_000001_sos", "description": "000001.SZ SOS 之后缩量回踩",
                    "result": "failed", "evidence_ids": [], "missing_inputs": []},
                   {"condition_id": "wyckoff_b_1", "description": "B 级（需分钟线）：日内跟随",
                    "result": "unknown", "evidence_ids": [], "missing_inputs": ["minute_bars"]},
                   {"condition_id": "wyckoff_600000_role", "description": "600000.SH 不在当日主线角色表中",
                    "result": "failed", "evidence_ids": [], "missing_inputs": []}],
               "candidates": [{"symbol": "000001.SZ", "seat_id": SEAT, "verdict": "watch"}]}
        run.update(over)
        return run

    def _analyze(self, payload: dict, run=None):
        client = FakeClient(payload)
        snap = {"snapshot_id": "snap_agent_unit", "metadata": {"session_date": "2026-10-19"},
                "metrics": [{"metric_id": "limit_up_count", "value": "47", "unit": "count", "symbol": None}],
                "instruments": [{"symbol": "000001.SZ", "last_price_decimal": "11.20",
                                 "quote_observed_at": "2026-10-19T07:00:00Z"}]}
        return seat_agent.analyze(self.h.db, self.settings, seat_id=SEAT, seat_name="威科夫",
                                  run=run or self._run(), snapshot=snap, symbol="000001.SZ", client=client)

    def test_rule_view_filters_conditions_by_symbol(self):
        view = seat_agent.rule_view(self._run(), "000001.SZ")
        descs = [c["description"] for c in view["conditions"]]
        self.assertEqual(len(descs), 2)                       # 本票 + 全局 B 级；别的票不混进来
        self.assertTrue(any("000001.SZ" in d for d in descs))
        self.assertFalse(any("600000" in d for d in descs))
        self.assertEqual(view["verdict_label"], "观察")
        self.assertEqual(view["candidate"]["symbol"], "000001.SZ")

    def test_analyze_requires_stance(self):
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            self._analyze({"answer": "规则结论是观察，等缩量回踩。", "facts_used": [],
                           "kb_refs": [{"kb_id": self._kb_id(), "quote": ""}]})
        self.assertEqual(cm.exception.code, "LLM_BAD_STANCE")

    def test_analyze_divergent_needs_disagreements(self):
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            self._analyze({"answer": "我认为规则判错了。", "stance": "divergent", "disagreements": [],
                           "facts_used": [], "kb_refs": [{"kb_id": self._kb_id(), "quote": ""}]})
        self.assertEqual(cm.exception.code, "LLM_NO_DISAGREEMENT")

    def test_analyze_insufficient_needs_missing(self):
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            self._analyze({"answer": "现在判断不了。", "stance": "insufficient", "disagreements": [],
                           "facts_used": [], "missing_inputs": [], "uncertainties": [],
                           "kb_refs": [{"kb_id": self._kb_id(), "quote": ""}]})
        self.assertEqual(cm.exception.code, "LLM_NO_MISSING")

    def test_analyze_rejects_stray_numbers(self):
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            self._analyze({"answer": "回踩到 9.87 附近才算数。", "stance": "consistent",
                           "facts_used": [], "kb_refs": [{"kb_id": self._kb_id(), "quote": ""}]})
        self.assertEqual(cm.exception.code, "LLM_NUMBER_NOT_IN_FACTS")

    def test_analyze_happy_path_and_audit(self):
        out = self._analyze({"answer": "规则说这是观察，但量能这条其实没有满足，分歧在证据强度。",
                             "stance": "divergent", "disagreements": ["规则说量能不足，但事实里没有成交量字段"],
                             "facts_used": ["rule.verdict"], "kb_refs": [{"kb_id": self._kb_id(), "quote": ""}],
                             "uncertainties": ["没有分时"], "missing_inputs": ["minute_bars"]})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["answer"]["stance_label"], "分歧")
        self.assertEqual(len(out["answer"]["disagreements"]), 1)
        self.assertEqual(out["rule"]["seat_run_id"], "seatrun_test")
        row = self.h.db.one("SELECT status FROM model_runs WHERE model_run_id=?", (out["audit"]["model_run_id"],))
        self.assertEqual(row["status"], "accepted")

    def test_analyze_rejected_in_replay(self):
        client = FakeClient({"answer": "x", "stance": "consistent", "kb_refs": []})
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            seat_agent.analyze(self.h.db, self.settings, seat_id=SEAT, seat_name="威科夫",
                               run=self._run(), snapshot=None, symbol="000001.SZ", replay_id="replay_x",
                               client=client)
        self.assertEqual(cm.exception.code, "LLM_DISABLED_IN_REPLAY")

    def test_disabled_without_key(self):
        old = os.environ.pop("DEEPSEEK_API_KEY", None)
        try:
            client = ds.DeepSeekClient(runtime_dir=Path(tempfile.mkdtemp()))
            ok, why = client.available()
            self.assertFalse(ok)
            self.assertIn("DEEPSEEK_API_KEY", why)
            with self.assertRaises(ds.LlmUnavailable) as cm:
                seat_agent.ask(self.h.db, self.settings, seat_id=SEAT, question="q", snapshot=self.snap, client=client)
            self.assertEqual(cm.exception.code, "LLM_DISABLED")
        finally:
            if old is not None:
                os.environ["DEEPSEEK_API_KEY"] = old

    def test_budget_exceeded(self):
        client = FakeClient({"answer": "x", "facts_used": [], "kb_refs": []}, limit=10)
        client.budget.add(20)
        with self.assertRaises(ds.LlmUnavailable) as cm:
            seat_agent.ask(self.h.db, self.settings, seat_id=SEAT, question="q", snapshot=self.snap, client=client)
        self.assertEqual(cm.exception.code, "LLM_BUDGET_EXCEEDED")

    def test_replay_is_rejected(self):
        client = FakeClient({"answer": "x", "facts_used": [], "kb_refs": []})
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            seat_agent.ask(self.h.db, self.settings, seat_id=SEAT, question="q", snapshot=self.snap,
                           client=client, replay_id="replay_x")
        self.assertEqual(cm.exception.code, "LLM_DISABLED_IN_REPLAY")

    def test_roundtable_runs_each_seat(self):
        kid_ok = self._kb_id()
        client = FakeClient({"answer": "解释。", "facts_used": ["limit_up_count"],
                             "kb_refs": [{"kb_id": kid_ok, "quote": ""}]})
        # 每一席用自己的 KB；这里两席都返回同一个 kb_id 会因不匹配被拒，所以逐席构造
        seats = [{"seat_id": SEAT, "role": "primary"}, {"seat_id": "trading-livermore-v1", "role": "validator"}]
        outs = []
        for s in seats:
            k = kb_service.search(s["seat_id"], "关键点", k=1)["results"][0]["kb_id"]
            c = FakeClient({"answer": "解释。", "facts_used": ["limit_up_count"], "kb_refs": [{"kb_id": k, "quote": ""}]})
            outs.append(seat_agent.ask(self.h.db, self.settings, seat_id=s["seat_id"], question="怎么看？",
                                       snapshot=self.snap, client=c))
        self.assertEqual(len(outs), 2)
        self.assertEqual(outs[1]["seat_id"], "trading-livermore-v1")

    def test_interpret_rejects_number_outside_report(self):
        report = {"markdown": "# 复盘\n上涨 2202 家，涨停 47 家。", "gaps": [{"item": "两融余额"}],
                  "session_date": "2026-10-19"}
        client = FakeClient({"commentary": "上涨 5000 家，情绪很好。", "web_refs": []})
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            seat_agent.interpret_report(self.h.db, self.settings, report=report, client=client)
        self.assertEqual(cm.exception.code, "LLM_UNKNOWN_NUMBER")

    def test_interpret_filters_web_refs_to_search_hits(self):
        report = {"markdown": "# 复盘\n上涨 2202 家，涨停 47 家。", "gaps": [{"item": "两融余额"}],
                  "session_date": "2026-10-19"}
        search = {"results": [{"title": "两融数据", "url": "https://example.com/margin", "snippet": "余额 1.8 万亿"}]}
        client = FakeClient({"commentary": "上涨 2202 家，涨停 47 家，宽度偏弱。",
                             "web_refs": [{"title": "两融数据", "url": "https://example.com/margin"},
                                          {"title": "编的", "url": "https://evil.example/x"}]}, search=search)
        out = seat_agent.interpret_report(self.h.db, self.settings, report=report, enable_search=True, client=client)
        self.assertEqual(out["status"], "ok")
        self.assertEqual([r["url"] for r in out["interpretation"]["web_refs"]], ["https://example.com/margin"])
        self.assertTrue(out["used_search"])


if __name__ == "__main__":
    unittest.main()
