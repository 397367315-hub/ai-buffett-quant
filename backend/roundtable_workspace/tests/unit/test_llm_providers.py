"""模型供应商注册表（ADR-008）：换供应商只改配置；密钥解析、请求形状、能力声明都要对。

全部用注入 transport，不联网；密钥只在进程环境或临时 env 文件里，测完清掉。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support.harness import Harness  # noqa: F401  （设置 sys.path）

from panel.adapters import llm_registry as reg
from panel.adapters.llm_base import LlmUnavailable
from panel.adapters.openai_compatible import OpenAICompatibleProvider
from panel.adapters.anthropic_compatible import AnthropicProvider


def fake_settings(tmp: Path):
    return SimpleNamespace(runtime_dir=tmp)


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rt-prov-"))
        (self.tmp / "config").mkdir()
        self.cfg = self.tmp / "config" / "llm.json"
        for k in ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY_ENV", "LLM_JSON_MODE",
                  "LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_ENV_FILE"):
            os.environ.pop(k, None)

    def tearDown(self):
        for k in ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY_ENV", "LLM_JSON_MODE",
                  "LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_ENV_FILE"):
            os.environ.pop(k, None)

    def test_default_is_deepseek_and_lists_providers(self):
        s = reg.status(fake_settings(self.tmp))
        self.assertEqual(s["provider"], "deepseek")
        self.assertIn("search_available", s)
        ids = [p["id"] for p in s["providers"]]
        self.assertEqual(ids[:3], ["deepseek", "openai_compatible", "anthropic"])
        self.assertTrue(s["providers"][0]["supports_search"])
        self.assertFalse(s["providers"][1]["supports_search"])
        self.assertEqual(s["config_path"], str(self.cfg))

    def test_config_file_switches_provider(self):
        self.cfg.write_text(json.dumps({"provider": "openai_compatible", "model": "gpt-4.1",
                                        "base_url": "https://api.openai.com/v1",
                                        "api_key_env": "OPENAI_API_KEY"}), encoding="utf-8")
        os.environ["OPENAI_API_KEY"] = "sk-openai-test"
        s = reg.status(fake_settings(self.tmp))
        self.assertEqual(s["provider"], "openai_compatible")
        self.assertEqual(s["model"], "gpt-4.1")
        self.assertTrue(s["enabled"])
        self.assertEqual(s["config_source"]["provider"], "file")
        self.assertNotIn("sk-openai-test", json.dumps(s))

    def test_env_overrides_file(self):
        self.cfg.write_text(json.dumps({"provider": "openai_compatible", "model": "gpt-4.1"}), encoding="utf-8")
        os.environ["LLM_PROVIDER"] = "anthropic"
        os.environ["LLM_MODEL"] = "claude-sonnet-4-5"
        s = reg.status(fake_settings(self.tmp))
        self.assertEqual(s["provider"], "anthropic")
        self.assertEqual(s["config_source"]["provider"], "env:LLM_PROVIDER")

    def test_unknown_provider_is_disabled_with_reason(self):
        self.cfg.write_text(json.dumps({"provider": "my_model"}), encoding="utf-8")
        s = reg.status(fake_settings(self.tmp))
        self.assertFalse(s["enabled"])
        self.assertIn("未注册", s["reason"])

    def test_key_from_env_file(self):
        envf = self.tmp / "provider.env"
        envf.write_text("OPENAI_API_KEY=sk-in-file\n", encoding="utf-8")
        os.environ["LLM_ENV_FILE"] = str(envf)
        self.cfg.write_text(json.dumps({"provider": "openai_compatible", "model": "m", "base_url": "http://x/v1",
                                        "api_key_env": "OPENAI_API_KEY"}), encoding="utf-8")
        p = reg.build(fake_settings(self.tmp))
        self.assertEqual(p.api_key(), "sk-in-file")
        self.assertTrue(p.key_source().startswith("file:"))


class TestProviders(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rt-prov2-"))

    def test_openai_compatible_request_shape(self):
        seen = {}
        def transport(method, url, headers, body):
            seen.update({"url": url, "headers": headers, "body": json.loads(body)})
            return 200, json.dumps({"model": "m", "usage": {"total_tokens": 12},
                                    "choices": [{"message": {"content": '{"answer":"ok"}'}}]}).encode()
        os.environ["LLM_API_KEY"] = "sk-x"
        try:
            p = OpenAICompatibleProvider(model="m", base_url="http://127.0.0.1:9/v1", runtime_dir=self.tmp,
                                         transport=transport)
            out = p.chat_json("sys", "usr", max_tokens=64)
            self.assertEqual(out["data"]["answer"], "ok")
            self.assertEqual(seen["url"], "http://127.0.0.1:9/v1/chat/completions")
            self.assertEqual(seen["headers"]["Authorization"], "Bearer sk-x")
            self.assertEqual(seen["body"]["response_format"], {"type": "json_object"})
            self.assertEqual(p.budget.used(), 12)
            with self.assertRaises(LlmUnavailable) as cm:
                p.search("q")
            self.assertEqual(cm.exception.code, "LLM_SEARCH_UNSUPPORTED")
        finally:
            os.environ.pop("LLM_API_KEY", None)

    def test_openai_compatible_without_json_mode(self):
        seen = {}
        def transport(method, url, headers, body):
            seen["body"] = json.loads(body)
            return 200, b'{"choices":[{"message":{"content":"{\\"a\\":1}"}}],"usage":{"total_tokens":1}}'
        os.environ["LLM_API_KEY"] = "sk-x"
        try:
            p = OpenAICompatibleProvider(model="m", base_url="http://x/v1", runtime_dir=self.tmp, transport=transport,
                                         config={"json_mode": False})
            p.chat_json("s", "u")
            self.assertNotIn("response_format", seen["body"])
        finally:
            os.environ.pop("LLM_API_KEY", None)

    def test_anthropic_request_shape_and_fence(self):
        seen = {}
        def transport(method, url, headers, body):
            seen.update({"url": url, "headers": headers, "body": json.loads(body)})
            return 200, json.dumps({"model": "claude-x", "usage": {"input_tokens": 10, "output_tokens": 5},
                                    "content": [{"type": "text", "text": "```json\n{\"answer\":\"hi\"}\n```"}]}).encode()
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        try:
            p = AnthropicProvider(model="claude-x", runtime_dir=self.tmp, transport=transport)
            out = p.chat_json("SYS", "USER", max_tokens=99)
            self.assertEqual(out["data"]["answer"], "hi")
            self.assertEqual(seen["url"], "https://api.anthropic.com/v1/messages")
            self.assertEqual(seen["headers"]["x-api-key"], "sk-ant-test")
            self.assertEqual(seen["headers"]["anthropic-version"], "2023-06-01")
            self.assertEqual(seen["body"]["system"], "SYS")
            self.assertEqual(seen["body"]["max_tokens"], 99)
            self.assertEqual(out["tokens"], 15)
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_list_models_parses_data_and_sorts(self):
        seen = {}
        def transport(method, url, headers, body):
            seen.update({"method": method, "url": url})
            return 200, json.dumps({"data": [{"id": "b-model"}, {"id": "a-model", "owned_by": "org"}]}).encode()
        os.environ["LLM_API_KEY"] = "sk-x"
        try:
            p = OpenAICompatibleProvider(model="m", base_url="http://x/v1", runtime_dir=self.tmp, transport=transport)
            out = p.list_models()
            self.assertEqual([m["name"] for m in out["models"]], ["a-model", "b-model"])
            self.assertEqual(seen["method"], "GET")
            self.assertEqual(seen["url"], "http://x/v1/models")
        finally:
            os.environ.pop("LLM_API_KEY", None)

    def test_list_models_anthropic_uses_v1_models(self):
        seen = {}
        def transport(method, url, headers, body):
            seen["url"] = url
            return 200, b'{"data":[{"id":"claude-sonnet-4-5","display_name":"Sonnet 4.5"}]}'
        os.environ["ANTHROPIC_API_KEY"] = "sk-a"
        try:
            p = AnthropicProvider(model="m", runtime_dir=self.tmp, transport=transport)
            out = p.list_models()
            self.assertEqual(out["models"][0]["name"], "claude-sonnet-4-5")
            self.assertTrue(seen["url"].endswith("/v1/models"))
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_list_models_bad_shape(self):
        os.environ["LLM_API_KEY"] = "sk-x"
        try:
            p = OpenAICompatibleProvider(model="m", base_url="http://x/v1", runtime_dir=self.tmp,
                                         transport=lambda m, u, h, b: (200, b'{"oops": 1}'))
            with self.assertRaises(LlmUnavailable) as cm:
                p.list_models()
            self.assertEqual(cm.exception.code, "LLM_BAD_RESPONSE")
        finally:
            os.environ.pop("LLM_API_KEY", None)

    def test_missing_model_reported(self):
        os.environ["LLM_API_KEY"] = "sk-x"
        try:
            p = OpenAICompatibleProvider(base_url="http://x/v1", runtime_dir=self.tmp)
            ok, why = p.available()
            self.assertFalse(ok)
            self.assertIn("模型名", why)
        finally:
            os.environ.pop("LLM_API_KEY", None)


class TestProfiles(unittest.TestCase):
    """模型存档（ADR-009）：存多份、随时切换；旧配置自动当成一份存档；密钥按存档分开。"""

    ENV = ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY_ENV", "LLM_JSON_MODE",
           "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "LLM_ENV_FILE")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rt-prof-"))
        (self.tmp / "config").mkdir()
        self.cfg = self.tmp / "config" / "llm.json"
        self.s = fake_settings(self.tmp)
        for k in self.ENV:
            os.environ.pop(k, None)

    def tearDown(self):
        for k in self.ENV:
            os.environ.pop(k, None)

    def test_save_activate_delete_cycle(self):
        a, _ = reg.save_profile(self.s, {"name": "DeepSeek", "provider": "deepseek", "model": "deepseek-flash"})
        b, _ = reg.save_profile(self.s, {"name": "OpenAI", "provider": "openai_compatible", "model": "gpt-4.1",
                                         "base_url": "https://api.openai.com/v1",
                                         "api_key_env": "OPENAI_API_KEY"}, activate=False)
        st = reg.status(self.s)
        self.assertEqual(st["profile_id"], a["id"])          # 第二份不抢当前
        self.assertEqual([p["name"] for p in st["profiles"]], ["DeepSeek", "OpenAI"])
        reg.activate_profile(self.s, b["id"])
        self.assertEqual(reg.status(self.s)["profile_id"], b["id"])
        self.assertEqual(reg.build(self.s).provider_id, "openai_compatible")
        doc = json.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(doc["active"], b["id"])
        self.assertEqual([p["id"] for p in doc["profiles"]], [a["id"], b["id"]])
        reg.delete_profile(self.s, b["id"])
        self.assertEqual(reg.status(self.s)["profile_id"], a["id"])

    def test_delete_last_profile_falls_back(self):
        a, _ = reg.save_profile(self.s, {"name": "只有一份", "provider": "deepseek"})
        reg.delete_profile(self.s, a["id"])
        st = reg.status(self.s)
        self.assertEqual(st["provider"], "deepseek")
        self.assertEqual(len(st["profiles"]), 1)
        self.assertEqual(st["profiles"][0]["name"], "DeepSeek（默认）")

    def test_unknown_provider_and_limit(self):
        with self.assertRaises(reg.ProfileError) as cm:
            reg.save_profile(self.s, {"name": "X", "provider": "nope"})
        self.assertEqual(cm.exception.code, "UNKNOWN_PROVIDER")
        for i in range(reg.PROFILE_LIMIT):
            reg.save_profile(self.s, {"name": f"P{i}", "provider": "deepseek"}, activate=False)
        with self.assertRaises(reg.ProfileError) as cm:
            reg.save_profile(self.s, {"name": "overflow", "provider": "deepseek"}, activate=False)
        self.assertEqual(cm.exception.code, "PROFILE_LIMIT")

    def test_legacy_flat_config_becomes_one_profile(self):
        self.cfg.write_text(json.dumps({"provider": "anthropic", "model": "claude-sonnet-4-5",
                                        "base_url": "https://api.anthropic.com",
                                        "api_key_env": "ANTHROPIC_API_KEY"}), encoding="utf-8")
        st = reg.status(self.s)
        self.assertEqual(len(st["profiles"]), 1)
        self.assertEqual(st["provider"], "anthropic")
        self.assertEqual(st["model"], "claude-sonnet-4-5")
        self.assertIn("导入", st["profile_name"])
        # 再存一份时，导入的那份保留
        reg.save_profile(self.s, {"name": "新的", "provider": "deepseek"}, activate=True)
        self.assertEqual(len(reg.status(self.s)["profiles"]), 2)

    def test_secrets_are_per_profile(self):
        a, cfg_a = reg.save_profile(self.s, {"name": "A", "provider": "openai_compatible", "model": "m",
                                             "base_url": "http://x/v1", "api_key_env": "OPENAI_API_KEY"},
                                    activate=False)
        b, cfg_b = reg.save_profile(self.s, {"name": "B", "provider": "openai_compatible", "model": "m",
                                             "base_url": "http://y/v1", "api_key_env": "OPENAI_API_KEY"})
        reg.save_secret(self.s, "OPENAI_API_KEY", "sk-aaaaaaaaaaaa", profile_id=a["id"])
        pa = reg.build(self.s, reg.config_for(self.s, a["id"]))
        pb = reg.build(self.s, reg.config_for(self.s, b["id"]))
        self.assertEqual(pa.api_key(), "sk-aaaaaaaaaaaa")
        self.assertIsNone(pb.api_key())
        self.assertEqual(pa.key_source(), "runtime:llm_secrets.json")
        reg.clear_secret(self.s, "OPENAI_API_KEY", profile_id=a["id"])
        self.assertIsNone(reg.build(self.s, reg.config_for(self.s, a["id"])).api_key())

    def test_deepseek_override_uses_profile_secret(self):
        # DeepSeek 客户端重写了 api_key()/key_source()（兼容 DEEPSEEK_ENV_FILE），也要带存档 id
        prof, _ = reg.save_profile(self.s, {"name": "DS", "provider": "deepseek", "model": "deepseek-flash"})
        reg.save_secret(self.s, "DEEPSEEK_API_KEY", "sk-deepseek-profile", profile_id=prof["id"])
        p = reg.build(self.s)
        self.assertEqual(p.api_key(), "sk-deepseek-profile")
        self.assertEqual(p.key_source(), "runtime:llm_secrets.json")

    def test_env_overrides_active_profile(self):
        reg.save_profile(self.s, {"name": "A", "provider": "deepseek", "model": "deepseek-flash"})
        os.environ["LLM_MODEL"] = "deepseek-v4-pro"
        st = reg.status(self.s)
        self.assertEqual(st["model"], "deepseek-v4-pro")
        self.assertEqual(st["config_source"]["model"], "env:LLM_MODEL")

    def test_switch_provider_resets_inherited_model(self):
        reg.save_profile(self.s, {"name": "A", "provider": "deepseek", "model": "deepseek-flash"})
        cfg = reg.update_active(self.s, {"provider": "openai_compatible"})
        self.assertEqual(cfg["model"], "")
        self.assertEqual(reg.build(self.s).model, "")        # 空＝用供应商默认值，不会沿用旧供应商的模型名
        ok, why = reg.build(self.s).available()
        self.assertFalse(ok)
        self.assertIn("模型名", why)

    def test_models_list_is_saved_and_cleaned(self):
        prof, _ = reg.save_profile(self.s, {
            "name": "A", "provider": "deepseek", "model": "deepseek-flash",
            "models": [{"name": "deepseek-flash", "long_context": True}, {"name": "deepseek-flash"},
                       {"name": " deepseek-v4-pro "}, "deepseek-v4-pro", {"name": ""}, 42]})
        self.assertEqual([m["name"] for m in prof["models"]], ["deepseek-flash", "deepseek-v4-pro"])
        self.assertTrue(prof["models"][0]["long_context"])
        st = reg.status(self.s)
        models = st["profiles"][0]["models"]
        self.assertEqual([m["name"] for m in models], ["deepseek-flash", "deepseek-v4-pro"])
        self.assertTrue(models[0]["selected"])
        self.assertFalse(models[1]["selected"])

    def test_seat_binding_resolution(self):
        a, _ = reg.save_profile(self.s, {"name": "全局", "provider": "deepseek", "model": "deepseek-flash"})
        b, _ = reg.save_profile(self.s, {"name": "这一席专用", "provider": "openai_compatible", "model": "gpt-4.1",
                                         "base_url": "http://x/v1", "api_key_env": "OPENAI_API_KEY"},
                                activate=False)
        reg.bind_seat(self.s, "trading-wyckoff-v1", b["id"])
        cfg = reg.config_for_seat(self.s, "trading-wyckoff-v1")
        self.assertEqual(cfg["provider"], "openai_compatible")
        self.assertEqual(cfg["_profile_name"], "这一席专用")
        self.assertEqual(reg.config_for_seat(self.s, "trading-livermore-v1")["_profile_name"], "全局")
        self.assertEqual(reg.status(self.s)["seat_bindings"]["trading-wyckoff-v1"]["profile_id"], b["id"])
        reg.bind_seat(self.s, "trading-wyckoff-v1", None)
        self.assertEqual(reg.config_for_seat(self.s, "trading-wyckoff-v1")["_profile_name"], "全局")
        with self.assertRaises(reg.ProfileError):
            reg.bind_seat(self.s, "trading-wyckoff-v1", "p-nope")

    def test_duplicate_profile(self):
        a, _ = reg.save_profile(self.s, {"name": "主力", "provider": "deepseek", "model": "deepseek-flash"})
        dup, _ = reg.duplicate_profile(self.s, a["id"])
        self.assertNotEqual(dup["id"], a["id"])
        self.assertIn("副本", dup["name"])
        self.assertEqual(len(reg.status(self.s)["profiles"]), 2)


if __name__ == "__main__":
    unittest.main()
