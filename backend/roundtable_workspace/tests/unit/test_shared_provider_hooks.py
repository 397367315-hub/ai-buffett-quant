import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.support.harness import Harness  # noqa: F401
from panel.adapters.deepseek import DeepSeekClient, configure_shared_llm, clear_shared_llm
from panel.adapters.llm_base import LlmUnavailable
from panel.adapters.numcat import NumcatAdapter
from panel.adapters.shared_hooks import configure_shared_numcat, clear_shared_numcat
from panel.data.evidence_store import EvidenceStore
from panel.services import data_bridge


class TestSharedHooks(unittest.TestCase):
    def tearDown(self):
        clear_shared_numcat()
        clear_shared_llm()
        os.environ.pop("DEEPSEEK_API_KEY", None)

    def test_numcat_adapter_and_bridge_use_host_envelope_and_keep_units(self):
        calls = []

        def query(apiname, params, fields):
            calls.append((apiname, params, fields))
            return {"code": 0, "data": {"fields": ["tradedate", "close"],
                                         "items": [["20261021", "12.30"]]},
                    "source": "numcat"}

        configure_shared_numcat(query, lambda: True)
        tmp = Path(tempfile.mkdtemp())
        store = EvidenceStore(tmp / "evidence")
        adapter = NumcatAdapter(Path("/missing/meoz.py"), store, tmp / "cache")
        raw = adapter.daily("20261021", past=False)
        self.assertEqual(raw.rows[0]["close"], "12.30")
        self.assertTrue(raw.raw_sha256)
        settings = SimpleNamespace(meoz_cli=Path("/missing/meoz.py"))
        result = data_bridge.query(settings, "daily", {"tradedate": "2026-10-21"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rows"][0]["tradedate"], "20261021")
        self.assertEqual(calls[0][0], "daily")
        self.assertEqual(calls[-1][1]["tradedate"], "20261021")

    def test_numcat_hook_unavailable_is_reported_without_cli_fallback(self):
        configure_shared_numcat(lambda *_: {}, lambda: False)
        tmp = Path(tempfile.mkdtemp())
        settings = SimpleNamespace(meoz_cli=Path("/missing/meoz.py"))
        result = data_bridge.query(settings, "daily")
        self.assertFalse(result["ok"])
        self.assertIn("不可用", result["reason"])

    def test_deepseek_shared_config_resolves_host_key_without_persisting_it(self):
        configure_shared_llm(base_url="https://host.example", model="deepseek-chat",
                             api_key_resolver=lambda: "host-secret")
        client = DeepSeekClient(runtime_dir=Path(tempfile.mkdtemp()))
        self.assertEqual(client.base_url, "https://host.example")
        self.assertEqual(client.model, "deepseek-chat")
        self.assertEqual(client.api_key(), "host-secret")
        self.assertEqual(client.key_source(), "host:deepseek_api_key")
        self.assertFalse((client.runtime_dir / "config").exists())
        with self.assertRaises(LlmUnavailable) as ctx:
            client.search("query")
        self.assertEqual(ctx.exception.code, "LLM_SEARCH_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
