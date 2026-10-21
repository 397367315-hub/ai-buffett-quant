"""模型接口的 HTTP 行为（ADR-007）：默认关闭、状态不泄露密钥、校验失败拒收、回放禁用。

用注入的假客户端（不联网）；密钥只在本进程里放一个假值，用完清掉。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.support.harness import WORK_ROOT, Harness

from panel.adapters import deepseek as ds
from panel.services import seat_agent

KB_ROOT = Path(tempfile.mkdtemp(prefix="rt-kb-api-"))
WORK_ROOT.joinpath("tools/build_seat_kb.py")


def setUpModule():
    import subprocess, sys
    subprocess.run([sys.executable, str(WORK_ROOT / "tools/build_seat_kb.py"), "--out", str(KB_ROOT)],
                   capture_output=True, text=True)
    os.environ["ROUNDTABLE_KB_ROOT"] = str(KB_ROOT)


def tearDownModule():
    os.environ.pop("ROUNDTABLE_KB_ROOT", None)


def fake_factory(chat: dict):
    def make(settings):
        c = ds.DeepSeekClient(transport=lambda m, u, h, b: (
            200, json.dumps({"model": "deepseek-flash", "usage": {"total_tokens": 100},
                             "choices": [{"message": {"content": json.dumps(chat, ensure_ascii=False)}}]},
                            ensure_ascii=False).encode()
            if u.endswith("/chat/completions") else (200, b'{"results": []}')),
            runtime_dir=settings.runtime_dir, max_retry=0)
        return c
    return make


class TestLlmApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = Harness(runtime=Path(tempfile.mkdtemp(prefix="rt-llm-http-")), workers=False)
        cls.c = cls.h.client()
        cls.c.bootstrap()

    @classmethod
    def tearDownClass(cls):
        seat_agent.set_client_factory(None)
        cls.h.close()

    def setUp(self):
        seat_agent.set_client_factory(None)
        os.environ.pop("DEEPSEEK_API_KEY", None)

    def test_status_disabled_without_key_and_never_leaks(self):
        st, s, _ = self.c.get("/api/v1/llm/status")
        self.assertEqual(st, 200)
        self.assertFalse(s["enabled"])
        self.assertIn("DEEPSEEK_API_KEY", s["reason"])
        self.assertNotIn("sk-", json.dumps(s))

    def test_ask_returns_503_when_disabled(self):
        st, err, _ = self.c.post("/api/v1/seats/trading-wyckoff-v1/ask", {"question": "SOS 之后怎么买？"})
        self.assertEqual(st, 503, err)
        self.assertEqual(err["error"]["code"], "LLM_DISABLED")

    def test_api_key_can_come_from_env_file(self):
        old = os.environ.pop("DEEPSEEK_API_KEY", None)
        f = Path(tempfile.mkdtemp()) / "deepseek.env"
        f.write_text('DEEPSEEK_API_KEY="sk-from-file"\n', encoding="utf-8")
        os.environ["DEEPSEEK_ENV_FILE"] = str(f)
        try:
            self.assertEqual(ds.DeepSeekClient().api_key(), "sk-from-file")
            st, s, _ = self.c.get("/api/v1/llm/status")
            self.assertTrue(s["enabled"])
            self.assertNotIn("sk-from-file", json.dumps(s))
        finally:
            os.environ.pop("DEEPSEEK_ENV_FILE", None)
            if old is not None:
                os.environ["DEEPSEEK_API_KEY"] = old

    def test_ask_ok_with_injected_client(self):
        from panel.services import kb_service
        kb_service._index.cache_clear()
        kid = kb_service.search("trading-wyckoff-v1", "SOS 回踩", k=1)["results"][0]["kb_id"]
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        seat_agent.set_client_factory(fake_factory({
            "answer": "SOS 之后等缩量回踩，不追突破。",
            "facts_used": [], "kb_refs": [{"kb_id": kid, "quote": ""}],
            "uncertainties": [], "missing_inputs": ["minute_bars"], "action": "explain_only"}))
        st, out, _ = self.c.post("/api/v1/seats/trading-wyckoff-v1/ask", {"question": "SOS 之后怎么买？"})
        self.assertEqual(st, 200, out)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["answer"]["kb_refs"][0]["kb_id"], kid)
        self.assertTrue(out["audit"]["model_run_id"].startswith("mr_"))
        st, s, _ = self.c.get("/api/v1/llm/status")
        self.assertTrue(s["enabled"])

    def test_ask_rejects_bad_citation(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        seat_agent.set_client_factory(fake_factory({"answer": "解释。", "facts_used": [],
                                                    "kb_refs": [{"kb_id": "nope", "quote": ""}]}))
        st, err, _ = self.c.post("/api/v1/seats/trading-wyckoff-v1/ask", {"question": "q"})
        self.assertEqual(st, 422, err)
        self.assertEqual(err["error"]["code"], "LLM_BAD_CITATION")

    def test_commander_disabled_empty_and_ok(self):
        st, err, _ = self.c.post("/api/v1/commander/sessions", {"question": "今天怎么看？"})
        self.assertEqual(st, 503, err)
        self.assertEqual(err["error"]["code"], "LLM_DISABLED")
        st, err, _ = self.c.post("/api/v1/commander/sessions", {"question": "   "})
        self.assertEqual(st, 422, err)
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        seat_agent.set_client_factory(fake_factory({"step": "final", "report": {
            "summary": "今天先看宽度，不急着动。", "seat_findings": [], "disagreements": [],
            "watch_next": ["看量能能否跟上"], "missing_inputs": [], "data_notes": ["未调用数据源"],
            "action": "explain_only"}}))
        st, out, _ = self.c.post("/api/v1/commander/sessions", {"question": "今天怎么看？"})
        self.assertEqual(st, 200, out)
        self.assertIn("宽度", out["report"]["summary"])
        self.assertEqual(out["report"]["action"], "explain_only")
        self.assertTrue(out["audit"]["model_run_id"].startswith("mr_"))

    def test_chat_ok_with_injected_client(self):
        from panel.services import kb_service
        kb_service._index.cache_clear()
        kid = kb_service.search("trading-wyckoff-v1", "SOS 回踩", k=1)["results"][0]["kb_id"]
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        seat_agent.set_client_factory(fake_factory({
            "step": "final",
            "answer": {"answer": "先查自己的书：SOS 之后等缩量回踩。", "facts_used": [],
                       "kb_refs": [{"kb_id": kid, "quote": ""}], "uncertainties": [], "missing_inputs": [],
                       "action": "explain_only"}}))
        st, out, _ = self.c.post("/api/v1/seats/trading-wyckoff-v1/chat", {"question": "SOS 之后怎么买？"})
        self.assertEqual(st, 200, out)
        self.assertEqual(out["mode"], "agent")
        self.assertEqual(out["steps"], [])
        self.assertEqual(out["answer"]["kb_refs"][0]["kb_id"], kid)

    def test_chat_disabled_and_unknown_seat(self):
        st, err, _ = self.c.post("/api/v1/seats/trading-wyckoff-v1/chat", {"question": "q"})
        self.assertEqual(st, 503, err)
        self.assertEqual(err["error"]["code"], "LLM_DISABLED")
        st, err, _ = self.c.post("/api/v1/seats/trading-nope/chat", {"question": "q"})
        self.assertEqual(st, 404, err)

    def test_seat_model_bind_and_unbind(self):
        st, s, _ = self.c.post("/api/v1/llm/profiles", {"action": "save", "activate": False,
                                                        "profile": {"name": "便宜的", "provider": "deepseek",
                                                                    "model": "deepseek-flash"}})
        self.assertEqual(st, 200, s)
        pid = s["profile"]["id"]
        st, b, _ = self.c.post("/api/v1/llm/seat-model", {"seat_id": "trading-livermore-v1", "profile_id": pid})
        self.assertEqual(st, 200, b)
        self.assertEqual(b["seat_bindings"]["trading-livermore-v1"]["profile_id"], pid)
        st, s2, _ = self.c.get("/api/v1/llm/status")
        self.assertEqual(s2["seat_bindings"]["trading-livermore-v1"]["profile_name"], "便宜的")
        cfg = json.loads((self.h.tmp / "config" / "llm.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["seats"]["trading-livermore-v1"], pid)
        st, b2, _ = self.c.post("/api/v1/llm/seat-model", {"seat_id": "trading-livermore-v1", "profile_id": None})
        self.assertEqual(st, 200, b2)
        self.assertEqual(b2["seat_bindings"], {})

    def test_seat_model_unknown_seat_and_profile(self):
        st, err, _ = self.c.post("/api/v1/llm/seat-model", {"seat_id": "trading-nope", "profile_id": None})
        self.assertEqual(st, 404, err)
        st, err, _ = self.c.post("/api/v1/llm/seat-model", {"seat_id": "trading-wyckoff-v1", "profile_id": "p-nope"})
        self.assertEqual(st, 422, err)
        self.assertEqual(err["error"]["code"], "PROFILE_NOT_FOUND")

    def test_ask_rejects_unknown_seat(self):
        st, err, _ = self.c.post("/api/v1/seats/trading-nope/ask", {"question": "q"})
        self.assertEqual(st, 404, err)

    def test_ask_rejected_in_replay(self):
        # 直接构造回放上下文较麻烦：用服务层断言（HTTP 层的 409 由 handler 前置检查，见实现）
        from panel.services import seat_agent as sa
        with self.assertRaises(sa.AgentRejected) as cm:
            sa.ask(self.h.db, self.h.settings, seat_id="trading-wyckoff-v1", question="q",
                   snapshot=None, replay_id="replay_x")
        self.assertEqual(cm.exception.code, "LLM_DISABLED_IN_REPLAY")

    def test_roundtable_endpoint(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        from panel.services import kb_service
        kb_service._index.cache_clear()
        kid = kb_service.search("trading-livermore-v1", "关键点", k=1)["results"][0]["kb_id"]
        seat_agent.set_client_factory(fake_factory({
            "answer": "关键点之上才做，跌回区间就不做。", "facts_used": [],
            "kb_refs": [{"kb_id": kid, "quote": ""}], "action": "explain_only"}))
        st, out, _ = self.c.post("/api/v1/roundtable/sessions",
                                 {"question": "这只票现在做不做？", "seats": [{"seat_id": "trading-livermore-v1", "role": "primary"}]})
        self.assertEqual(st, 200, out)
        self.assertEqual(len(out["seats"]), 1)
        self.assertEqual(out["seats"][0]["role_label"], "主方法")


class TestLlmConfigApi(unittest.TestCase):
    """在页面里改模型：写配置、存密钥（不回显）、清密钥、拒收未知供应商。"""

    def setUp(self):
        for k in ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY_ENV", "LLM_JSON_MODE",
                  "LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
            os.environ.pop(k, None)
        self.h = Harness(runtime=Path(tempfile.mkdtemp(prefix="rt-llm-cfg-")), workers=False)
        self.c = self.h.client()
        self.c.bootstrap()

    def tearDown(self):
        self.h.close()

    def test_requires_csrf(self):
        st, err, _ = self.c.post("/api/v1/llm/config", {"provider": "deepseek"}, csrf=False)
        self.assertEqual(st, 403, err)

    def test_switch_provider_and_persist(self):
        st, s, _ = self.c.post("/api/v1/llm/config", {"provider": "openai_compatible", "model": "gpt-4.1",
                                                      "base_url": "https://api.openai.com/v1",
                                                      "api_key_env": "OPENAI_API_KEY", "daily_token_limit": 12345})
        self.assertEqual(st, 200, s)
        self.assertEqual(s["provider"], "openai_compatible")
        self.assertEqual(s["budget"]["limit"], 12345)
        self.assertEqual(s["config_source"]["provider"], "file")
        cfg = json.loads((self.h.tmp / "config" / "llm.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["version"], 1)
        prof = cfg["profiles"][0]
        self.assertEqual(prof["provider"], "openai_compatible")
        self.assertEqual(prof["model"], "gpt-4.1")
        self.assertEqual(cfg["active"], prof["id"])

    def test_save_secret_never_echoed(self):
        st, s, _ = self.c.post("/api/v1/llm/config", {"provider": "openai_compatible", "model": "m",
                                                      "base_url": "http://127.0.0.1:9/v1",
                                                      "api_key_env": "OPENAI_API_KEY",
                                                      "api_key": "sk-very-secret-123"})
        self.assertEqual(st, 200, s)
        self.assertTrue(s["key_configured"])
        self.assertEqual(s["key_source"], "runtime:llm_secrets.json")
        self.assertNotIn("sk-very-secret-123", json.dumps(s))
        f = self.h.tmp / "config" / "llm_secrets.json"
        self.assertTrue(f.is_file())
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        st, s2, _ = self.c.get("/api/v1/llm/status")
        self.assertNotIn("sk-very-secret-123", json.dumps(s2))
        self.assertTrue(s2["enabled"])

    def test_clear_secret(self):
        self.c.post("/api/v1/llm/config", {"provider": "openai_compatible", "model": "m", "base_url": "http://x/v1",
                                           "api_key_env": "OPENAI_API_KEY", "api_key": "sk-abcdefgh"})
        st, s, _ = self.c.post("/api/v1/llm/config", {"clear_api_key": True})
        self.assertEqual(st, 200, s)
        self.assertFalse(s["key_configured"])
        self.assertFalse((self.h.tmp / "config" / "llm_secrets.json").exists())

    def test_unknown_provider_and_bad_limit_rejected(self):
        st, err, _ = self.c.post("/api/v1/llm/config", {"provider": "not_a_provider"})
        self.assertEqual(st, 422, err)
        self.assertEqual(err["error"]["code"], "UNKNOWN_PROVIDER")
        st, err, _ = self.c.post("/api/v1/llm/config", {"daily_token_limit": 0})
        self.assertEqual(st, 422, err)


class TestLlmProfilesApi(unittest.TestCase):
    """模型存档（ADR-009）：存多份、随时切换、密钥按存档分开、试连如实返回。"""

    def setUp(self):
        for k in ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY_ENV", "LLM_JSON_MODE",
                  "LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
            os.environ.pop(k, None)
        self.h = Harness(runtime=Path(tempfile.mkdtemp(prefix="rt-llm-prof-")), workers=False)
        self.c = self.h.client()
        self.c.bootstrap()

    def tearDown(self):
        self.h.close()

    def _save(self, **prof):
        body = {"action": "save", "profile": prof}
        for k in ("api_key", "clear_api_key", "activate"):
            if k in prof:
                body[k] = prof.pop(k)
        return self.c.post("/api/v1/llm/profiles", body)

    def test_save_creates_and_activates_without_ghost_default(self):
        st, s, _ = self._save(name="OpenAI 主力", provider="openai_compatible", model="gpt-4.1",
                              base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY")
        self.assertEqual(st, 200, s)
        self.assertEqual(s["profile_name"], "OpenAI 主力")
        self.assertEqual(len(s["profiles"]), 1)
        self.assertTrue(s["profiles"][0]["active"])
        self.assertEqual(s["config_source"]["provider"], "file")

    def test_second_profile_does_not_steal_active_until_activated(self):
        _, a, _ = self._save(name="DeepSeek 官方", provider="deepseek", model="deepseek-flash")
        first = a["profile_id"]
        st, b, _ = self._save(name="OpenAI 主力", provider="openai_compatible", model="gpt-4.1",
                              base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY",
                              activate=False)
        self.assertEqual(st, 200, b)
        self.assertEqual(b["profile_id"], first)
        self.assertEqual(len(b["profiles"]), 2)
        second = next(p["id"] for p in b["profiles"] if p["id"] != first)
        st, c, _ = self.c.post("/api/v1/llm/profiles", {"action": "activate", "id": second})
        self.assertEqual(st, 200, c)
        self.assertEqual(c["profile_id"], second)
        self.assertEqual(c["provider"], "openai_compatible")
        self.assertEqual(c["model"], "gpt-4.1")

    def test_secret_is_per_profile(self):
        _, a, _ = self._save(name="A", provider="openai_compatible", model="m", base_url="http://x/v1",
                             api_key_env="OPENAI_API_KEY", api_key="sk-aaaaaaaaaaaa", activate=False)
        _, b, _ = self._save(name="B", provider="openai_compatible", model="m", base_url="http://y/v1",
                             api_key_env="OPENAI_API_KEY")
        pa = next(p for p in b["profiles"] if p["id"] == a["profile"]["id"])
        pb = next(p for p in b["profiles"] if p["id"] == b["profile"]["id"])
        self.assertTrue(pa["key_configured"])
        self.assertTrue(pa["enabled"])
        self.assertFalse(pb["key_configured"])          # 同名环境变量、不同存档：各自的密钥互不影响
        self.assertFalse(pb["enabled"])

    def test_duplicate_then_delete_falls_back(self):
        _, a, _ = self._save(name="DeepSeek 官方", provider="deepseek", model="deepseek-flash")
        pid = a["profile_id"]
        st, b, _ = self.c.post("/api/v1/llm/profiles", {"action": "duplicate", "id": pid})
        self.assertEqual(st, 200, b)
        self.assertEqual(len(b["profiles"]), 2)
        self.assertEqual(b["profile_id"], pid)           # 复制不抢当前
        dup = next(p for p in b["profiles"] if p["id"] != pid)
        self.assertIn("副本", dup["name"])
        st, c, _ = self.c.post("/api/v1/llm/profiles", {"action": "delete", "id": pid})
        self.assertEqual(st, 200, c)
        self.assertEqual(c["profile_id"], dup["id"])      # 删掉当前那一份 → 自动切到剩下的
        self.assertEqual(len(c["profiles"]), 1)

    def test_unknown_provider_and_missing_profile_rejected(self):
        st, err, _ = self._save(name="X", provider="not_a_provider")
        self.assertEqual(st, 422, err)
        self.assertEqual(err["error"]["code"], "UNKNOWN_PROVIDER")
        st, err, _ = self.c.post("/api/v1/llm/profiles", {"action": "activate", "id": "p-nope"})
        self.assertEqual(st, 422, err)
        self.assertEqual(err["error"]["code"], "PROFILE_NOT_FOUND")
        st, err, _ = self.c.post("/api/v1/llm/profiles", {"action": "explode"})
        self.assertEqual(st, 422, err)

    def test_test_endpoint_disabled_reason_and_stub_success(self):
        _, s, _ = self._save(name="OpenAI 主力", provider="openai_compatible", model="gpt-4.1",
                             base_url="http://127.0.0.1:9/v1", api_key_env="OPENAI_API_KEY")
        st, r, _ = self.c.post("/api/v1/llm/test", {"id": s["profile_id"]})
        self.assertEqual(st, 200, r)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "LLM_DISABLED")
        self.assertIn("OPENAI_API_KEY", r["reason"])

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubChat)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        st, s2, _ = self._save(name="本地桩", provider="openai_compatible", model="gpt-4.1",
                               base_url=f"http://127.0.0.1:{port}/v1", api_key_env="OPENAI_API_KEY",
                               api_key="sk-stub-key-123")
        self.assertEqual(st, 200, s2)
        st, r2, _ = self.c.post("/api/v1/llm/test", {})
        self.assertEqual(st, 200, r2)
        self.assertTrue(r2["ok"], r2)
        self.assertEqual(r2["model"], "gpt-4.1")

    def test_profiles_never_leak_secret(self):
        self._save(name="A", provider="openai_compatible", model="m", base_url="http://x/v1",
                   api_key_env="OPENAI_API_KEY", api_key="sk-super-secret-xyz")
        st, s, _ = self.c.get("/api/v1/llm/status")
        self.assertNotIn("sk-super-secret-xyz", json.dumps(s))
        self.assertIn("key_configured", s)


    def test_models_endpoint_disabled_and_stub(self):
        _, s, _ = self._save(name="本地桩", provider="openai_compatible", model="gpt-4.1",
                             base_url="http://127.0.0.1:9/v1", api_key_env="OPENAI_API_KEY")
        st, r, _ = self.c.post("/api/v1/llm/models", {"id": s["profile_id"]})
        self.assertEqual(st, 200, r)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "LLM_DISABLED")

        srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubChat)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        _, s2, _ = self._save(name="本地桩", provider="openai_compatible", model="gpt-4.1",
                              base_url=f"http://127.0.0.1:{port}/v1", api_key_env="OPENAI_API_KEY",
                              api_key="sk-stub-key-123")
        st, r2, _ = self.c.post("/api/v1/llm/models", {"id": s2["profile_id"]})
        self.assertEqual(st, 200, r2)
        self.assertTrue(r2["ok"], r2)   # 还没选模型也能先拉清单
        names = [m["name"] for m in r2["models"]]
        self.assertEqual(names, ["gpt-4.1", "gpt-4o-mini"])
        self.assertTrue(next(m for m in r2["models"] if m["name"] == "gpt-4.1")["selected"])

    def test_models_unknown_profile_rejected(self):
        st, err, _ = self.c.post("/api/v1/llm/models", {"id": "p-nope"})
        self.assertEqual(st, 422, err)
        self.assertEqual(err["error"]["code"], "PROFILE_NOT_FOUND")


class _StubChat(BaseHTTPRequestHandler):
    """本地假的 chat/completions（只在测试里用，不联网）。"""

    def do_GET(self):
        body = json.dumps({"data": [{"id": "gpt-4.1"}, {"id": "gpt-4o-mini", "owned_by": "openai"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        self.rfile.read(n)
        body = json.dumps({"model": "gpt-4.1", "usage": {"total_tokens": 3},
                           "choices": [{"message": {"content": '{"ok": true}'}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    unittest.main()
