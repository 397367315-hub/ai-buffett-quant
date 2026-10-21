"""总指挥（ADR-013）：派活 → 各席用自己的书回答 → 汇总报告；报告必须点名真派过的席位、数字要有出处。"""
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
from panel.services import commander, data_bridge, kb_service, seat_agent

BUILDER = WORK_ROOT / "tools/build_seat_kb.py"
SEAT = "trading-wyckoff-v1"


class FakeClient(ds.DeepSeekClient):
    def __init__(self, payloads: list[dict]):
        self._queue = list(payloads)
        super().__init__(transport=lambda m, u, h, b: (500, b"{}"),
                         runtime_dir=Path(tempfile.mkdtemp(prefix="rt-cmd-")), max_retry=0)
        self.budget.limit = 200000

    def chat_json(self, system, user, *, max_tokens=1200, temperature=0.0):
        data = self._queue.pop(0) if self._queue else {"step": "final", "report": {"summary": "空"}}
        return {"data": data, "raw": json.dumps(data, ensure_ascii=False), "usage": {},
                "model": "deepseek-flash", "tokens": 10}


class TestCommander(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb_root = Path(tempfile.mkdtemp(prefix="rt-kb-cmd-"))
        r = subprocess.run([sys.executable, str(BUILDER), "--out", str(cls.kb_root)], capture_output=True, text=True)
        if r.returncode != 0:
            raise unittest.SkipTest(f"知识库构建失败：{(r.stderr or r.stdout)[:200]}")
        os.environ["ROUNDTABLE_KB_ROOT"] = str(cls.kb_root)
        os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-not-real")
        kb_service._index.cache_clear()
        clock.set_fixed("2026-10-21T09:00:00Z")
        cls.h = Harness(runtime=Path(tempfile.mkdtemp(prefix="rt-cmd-")), workers=False)
        cls.settings = cls.h.settings
        cls.snap = {"snapshot_id": "snap_cmd_unit", "metadata": {"session_date": "2026-10-19"},
                    "metrics": [{"metric_id": "limit_up_count", "value": "47", "unit": "count", "symbol": None}],
                    "instruments": []}
        cls.kid = kb_service.search(SEAT, "SOS 缩量回踩", k=1)["results"][0]["kb_id"]

    @classmethod
    def tearDownClass(cls):
        seat_agent.set_client_factory(None)
        cls.h.close()
        os.environ.pop("ROUNDTABLE_KB_ROOT", None)
        clock.set_fixed(None)
        kb_service._index.cache_clear()

    def setUp(self):
        seat_agent.set_client_factory(None)
        seat_agent.set_client_factory(lambda settings: FakeClient([{
            "step": "final", "answer": {"answer": "SOS 之后要等缩量回踩，不追突破当天。", "facts_used": [],
                                        "kb_refs": [{"kb_id": self.kid, "quote": ""}], "uncertainties": [],
                                        "missing_inputs": ["minute_bars"], "action": "explain_only"}}]))

    def _report(self, **over):
        rep = {"summary": "威科夫说现在没有买点：他的书里要求先缩量回踩，再谈进场。",
               "seat_findings": [{"seat_id": SEAT, "point": "SOS 之后等缩量回踩"}],
               "disagreements": [], "watch_next": ["看回踩的量能"], "missing_inputs": ["minute_bars"],
               "data_notes": ["知识库检索"], "action": "explain_only"}
        rep.update(over)
        return {"step": "final", "report": rep}

    def test_dispatch_then_report(self):
        client = FakeClient([
            {"step": "tool", "tool": "list_seats", "args": {}, "why": "先看谁能干"},
            {"step": "tool", "tool": "dispatch", "args": {"seats": [SEAT], "question": "SOS 之后怎么买？"}, "why": "让威科夫说"},
            self._report(),
        ])
        out = commander.run(self.h.db, self.settings, question="现在能买吗？", snapshot=self.snap, client=client)
        self.assertEqual(out["status"], "ok")
        self.assertEqual([s["tool"] for s in out["steps"]], ["list_seats", "dispatch"])
        self.assertEqual(out["dispatched"][0]["seat_id"], SEAT)
        self.assertEqual(out["report"]["seat_findings"][0]["seat_name"], "威科夫")
        self.assertTrue(out["report"]["summary"])

    def test_report_cannot_name_undispatched_seat(self):
        client = FakeClient([self._report(seat_findings=[{"seat_id": "trading-livermore-v1", "point": "他说没事"}])])
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            commander.run(self.h.db, self.settings, question="q", snapshot=self.snap, client=client)
        self.assertEqual(cm.exception.code, "LLM_UNKNOWN_SEAT_FINDING")

    def test_report_rejects_policy_words(self):
        client = FakeClient([self._report(summary="目标价 12 元，可以买。", seat_findings=[])])
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            commander.run(self.h.db, self.settings, question="q", snapshot=self.snap, client=client)
        self.assertEqual(cm.exception.code, "LLM_POLICY")

    def test_report_rejects_stray_numbers(self):
        client = FakeClient([self._report(summary="胜率 60%，可以买。", seat_findings=[])])
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            commander.run(self.h.db, self.settings, question="q", snapshot=self.snap, client=client)
        self.assertEqual(cm.exception.code, "LLM_POLICY")
        client2 = FakeClient([self._report(summary="现在涨停 99 家，情绪不错。", seat_findings=[])])
        with self.assertRaises(seat_agent.AgentRejected) as cm2:
            commander.run(self.h.db, self.settings, question="q", snapshot=self.snap, client=client2)
        self.assertEqual(cm2.exception.code, "LLM_NUMBER_NOT_IN_FACTS")

    def test_dispatch_limit_is_reported_back(self):
        client = FakeClient([
            {"step": "tool", "tool": "dispatch", "args": {"seats": ["a", "b", "c", "d"], "question": "x"}},
            {"step": "tool", "tool": "dispatch", "args": {"seats": [SEAT], "question": "x"}},
            {"step": "tool", "tool": "dispatch", "args": {"seats": [SEAT], "question": "y"}},
            {"step": "tool", "tool": "dispatch", "args": {"seats": [SEAT], "question": "z"}},
            self._report(),
        ])
        out = commander.run(self.h.db, self.settings, question="q", snapshot=self.snap, client=client)
        self.assertFalse(out["steps"][0]["ok"])                       # 4 席超限
        self.assertIn("TOOL_BAD_ARGS", out["steps"][0]["summary"])
        self.assertFalse(out["steps"][3]["ok"])                       # 第三轮派活被拦
        self.assertIn("TOOL_LIMIT", out["steps"][3]["summary"])
        self.assertEqual(out["status"], "ok")

    def test_step_limit(self):
        client = FakeClient([{"step": "tool", "tool": "list_seats", "args": {}}] * 5)
        with self.assertRaises(seat_agent.AgentRejected) as cm:
            commander.run(self.h.db, self.settings, question="q", snapshot=self.snap, client=client, max_steps=2)
        self.assertEqual(cm.exception.code, "LLM_STEP_LIMIT")


class TestDataBridge(unittest.TestCase):
    """数据桥（ADR-014）：目录白名单、参数校验、真实子进程路径（用假 meoz CLI）。"""

    def test_catalog_shape_and_aliases(self):
        cat = data_bridge.catalog()
        self.assertGreaterEqual(len(cat["ops"]), 90)
        self.assertEqual(data_bridge.resolve("margin")["op"], "reference_margin_summary")
        self.assertEqual(data_bridge.resolve("northbound")["op"], "reference_fund-flow_northbound")
        self.assertEqual(data_bridge.resolve("reference_stock_daily")["apiname"], "daily")
        tags = data_bridge.tags()
        for name in ("融资融券", "互联互通", "板块指数", "龙虎榜", "市场统计"):
            self.assertIn(name, tags)
        listed = data_bridge.list_ops(tag="融资融券")
        self.assertTrue(any(o["op"] == "reference_margin_summary" for o in listed))
        self.assertFalse(any(o["op"] == "reference_stock_tick_stream_v1" for o in data_bridge.list_ops()))

    def test_query_rejects_unknown_stream_and_bad_params(self):
        from types import SimpleNamespace
        s = SimpleNamespace(meoz_cli=Path("/nonexistent/meoz.py"))
        self.assertFalse(data_bridge.query(s, "rm_rf")["ok"])
        res = data_bridge.query(s, "reference_stock_stream_limit_event_v2")
        self.assertFalse(res["ok"])
        self.assertIn("订阅", res["reason"])
        res = data_bridge.query(s, "reference_stock_minute-file-download")
        self.assertFalse(res["ok"])
        self.assertIn("下载", res["reason"])
        res = data_bridge.query(s, "reference_stock_daily", {"nonsense": 1})
        self.assertFalse(res["ok"])
        self.assertIn("不认识", res["reason"])
        res = data_bridge.query(s, "reference_stock_daily", {"tradedate": "2026/13/40"})
        self.assertFalse(res["ok"])
        self.assertIn("日期", res["reason"])
        res = data_bridge.query(s, "reference_stock_daily")
        self.assertFalse(res["ok"])
        self.assertIn("meoz", res["reason"])            # 没有 CLI 时如实说

    def test_params_are_normalised(self):
        spec = data_bridge.resolve("reference_stock_daily")
        out = data_bridge.clean_params(spec, {"symbols": ["000001.SZ", "600000", "SZ000002"],
                                              "tradedate": "2026-10-21"})
        self.assertEqual(out["symbols"], ["000001", "600000", "000002"])
        self.assertEqual(out["tradedate"], "20261021")
        margin = data_bridge.resolve("reference_margin_summary")
        out2 = data_bridge.clean_params(margin, {"recentdays": 999})
        self.assertEqual(out2["recentdays"], 60)         # 上限
        with self.assertRaises(ValueError):
            data_bridge.clean_params(spec, {"symbols": ["bad-code"]})

    def test_catalog_is_up_to_date(self):
        """目录是生成物：文档改了忘记重新生成，这里会失败。"""
        import sys
        sys.path.insert(0, str(WORK_ROOT / "tools"))
        import gen_data_catalog
        built = gen_data_catalog.build(WORK_ROOT / "contracts/vendor/numcat-openapi.json")
        current = json.loads((WORK_ROOT / "records/roundtable/2026-10-18-圆桌面板设计/panel/services/"
                              "data_catalog.json").read_text(encoding="utf-8"))
        self.assertEqual([o["op"] for o in built["ops"]], [o["op"] for o in current["ops"]])
        for a, b in zip(built["ops"], current["ops"]):
            self.assertEqual(sorted(a["params"]), sorted(b["params"]), a["op"])
            self.assertEqual(a["apiname"], b["apiname"], a["op"])

    def test_query_through_fake_cli(self):
        """真的走 subprocess：假 meoz 把收到的请求原样回显，验证 fields/params 传对了。"""
        tmp = Path(tempfile.mkdtemp(prefix="rt-meoz-"))
        cli = tmp / "meoz.py"
        seen = tmp / "seen.json"
        cli.write_text(
            "import json, sys\n"
            "a = sys.argv[1:]\n"
            "op = a[a.index('--operation-id') + 1]\n"
            "req = json.load(open(a[a.index('--request-file') + 1], encoding='utf-8'))\n"
            f"open({str(seen)!r}, 'w', encoding='utf-8').write(json.dumps({{'op': op, 'req': req}}, ensure_ascii=False))\n"
            "print(json.dumps({'results': [{'data': {'fields': ['tradedate', 'symbol', 'close'],"
            " 'items': [['20261021', '000001', 11.2]]}}]}))\n",
            encoding="utf-8")
        from types import SimpleNamespace
        s = SimpleNamespace(meoz_cli=cli)
        res = data_bridge.query(s, "daily", {"symbols": ["000001.SZ"], "tradedate": "2026-10-21"})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["rows"][0]["close"], 11.2)
        got = json.loads(seen.read_text(encoding="utf-8"))
        self.assertEqual(got["op"], "reference_stock_daily")
        self.assertEqual(got["req"]["apiname"], "daily")
        self.assertEqual(got["req"]["params"], {"symbols": ["000001"], "tradedate": "20261021"})
        self.assertTrue(got["req"]["fields"].startswith("tradedate,symbol"))
        # 同一组参数第二次走缓存，不再打子进程
        seen.unlink()
        res2 = data_bridge.query(s, "daily", {"symbols": ["000001.SZ"], "tradedate": "2026-10-21"})
        self.assertTrue(res2.get("cached"))
        self.assertFalse(seen.exists())


if __name__ == "__main__":
    unittest.main()
