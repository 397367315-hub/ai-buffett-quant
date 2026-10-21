"""席位知识库（M1）：构建、检索、引用可回查。用真实源包构建到临时目录，不需要网络。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support.harness import WORK_ROOT, Harness  # noqa: F401  （设置 sys.path）

from panel.services import kb_service as kb

BUILDER = WORK_ROOT / "tools/build_seat_kb.py"


class TestSeatKb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="rt-kb-"))
        r = subprocess.run([sys.executable, str(BUILDER), "--out", str(cls.tmp)], capture_output=True, text=True)
        if r.returncode != 0:
            raise unittest.SkipTest(f"知识库构建跳过：{(r.stderr or r.stdout)[:200]}")
        os.environ["ROUNDTABLE_KB_ROOT"] = str(cls.tmp)
        kb._index.cache_clear()

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("ROUNDTABLE_KB_ROOT", None)
        kb._index.cache_clear()

    def test_all_seats_have_a_kb(self):
        seats = kb.seats_available()
        self.assertEqual(len(seats), 13)                      # 13 席；common 是公共库，不算席位
        for s in seats:
            st = kb.stats(s)
            self.assertFalse(st["missing"], s)
            self.assertGreaterEqual(st["chunks"], 10, s)
            self.assertTrue(st["built_at"])

    def test_original_text_is_searchable_with_locator(self):
        r = kb.search("trading-wyckoff-v1", "弹簧 缩量回踩 SOS", k=5)
        self.assertFalse(r["missing"])
        self.assertTrue(r["results"])
        hit = r["results"][0]
        self.assertTrue(hit["locator"].startswith(("text:", "md:")))
        self.assertTrue(hit["text"].strip())

    def test_query_returns_relevant_kind_mix(self):
        r = kb.search("trading-wildman-v2", "退潮期 亏钱效应", k=8)
        kinds = {x["kind"] for x in r["results"]}
        self.assertIn("original", kinds)                      # 原书正文能命中
        self.assertIn("skill", kinds)

    def test_missing_kb_is_reported_not_faked(self):
        r = kb.search("trading-not-exist", "任何问题")
        self.assertTrue(r["missing"])
        self.assertEqual(r["results"], [])

    def test_kb_ids_are_deterministic(self):
        before = kb.loaded("trading-livermore-v1")
        ids_before = {x["kb_id"] for x in before}
        out2 = Path(tempfile.mkdtemp(prefix="rt-kb2-"))
        subprocess.run([sys.executable, str(BUILDER), "--out", str(out2)], capture_output=True, text=True)
        rows = [json.loads(l) for l in (out2 / "seats/trading-livermore-v1/kb.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(ids_before, {x["kb_id"] for x in rows})

    def test_every_kb_id_is_retrievable(self):
        row = kb.loaded("trading-candle-v1")[0]
        got = kb.get(row["kb_id"])
        self.assertIsNotNone(got)
        self.assertEqual(got["text"], row["text"])


if __name__ == "__main__":
    unittest.main()
