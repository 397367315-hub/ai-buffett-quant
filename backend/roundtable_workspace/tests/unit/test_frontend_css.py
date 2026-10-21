"""两套样式都是「源文件 + 作用域化生成物」，这里防止改了源样式忘了重新生成（前端会静默串味）。

    python3 tools/scope_css.py <源.css> <生成.css> <作用域>   # 重新生成
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

WORK = Path(__file__).resolve().parents[2]
REPO_ROOT = WORK.parents[1]
FB = REPO_ROOT / "integrations/trading-roundtable/frontend"
PAIRS = [
    (FB / "src/styles.css", FB / "src/v1.css", ".v1-shell"),
]


class TestScopedCss(unittest.TestCase):
    def test_generated_css_is_current(self):
        for src, dst, scope in PAIRS:
            with self.subTest(dst=dst.name):
                self.assertTrue(src.is_file(), src)
                r = subprocess.run([sys.executable, str(WORK / "tools/scope_css.py"), str(src), str(dst), scope, "--check"],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr.strip())

    def test_scope_prefix_and_root_mapping(self):
        r = subprocess.run([sys.executable, str(WORK / "tools/scope_css.py"), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        # The integrated repository keeps the scoped legacy result, while the
        # historical backup source is intentionally excluded from the checkout.
        css = (FB / "src/legacy/legacy.css").read_text(encoding="utf-8")
        self.assertIn(".legacy-shell ", css)                 # 普通选择器加了作用域
        self.assertNotIn(":root{", css)                       # :root 映射到外壳上
        self.assertIn("@keyframes", css)                      # 关键帧原样保留
        self.assertIn("@media", css)


if __name__ == "__main__":
    unittest.main()
