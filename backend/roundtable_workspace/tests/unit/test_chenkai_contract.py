"""陈凯席位契约回归（SEAT-003）。

正例 fixture：按旧 4 日线口径「会出三周期共振候选」的合成日线（缓涨后小幅回踩到 20/30 日均线附近）。
旧脚本（chenkai-v1-legacy，未阻断）对它给出 1 只候选；新版（chenkai-v2-20261019）必须：
不出候选、小周期 unknown、4 日线只作明确命名的研究周期、日线级解释仍然给出。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tests.support.harness import WORK_ROOT

SCRIPT = WORK_ROOT / "skills/trading-chenkai-v1/scripts/screen.py"


def _fixture(rise: float = 0.002, n_rise: int = 88, pull: float = -0.002, n_pull: int = 2) -> list[dict]:
    rows, d, px, prev = [], date(2026, 6, 1), 10.0, 10.0
    days = []
    while len(days) < n_rise + n_pull:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    for i, day in enumerate(days):
        px *= (1 + rise) if i < n_rise else (1 + pull)
        rows.append({"tradedate": day.strftime("%Y%m%d"), "symbol": "600001", "name": "合成样例",
                     "open": round(px * 0.998, 2), "high": round(px * 1.006, 2), "low": round(px * 0.994, 2),
                     "close": round(px, 2), "pre_close": round(prev, 2), "pct_chg": round((px / prev - 1) * 100, 3),
                     "amount": 5e8, "vol": 1e6})
        prev = px
    return rows


def _run(script: Path, rows: list[dict], extra=()) -> dict:
    tmp = Path(tempfile.mkdtemp())
    f = tmp / "features.json"
    f.write_text(json.dumps(rows))
    p = subprocess.run([sys.executable, "-I", str(script), "--features", str(f), "--out", str(tmp), *extra],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError(p.stderr)
    return json.loads((tmp / "chenkai.json").read_text())


class TestChenkaiCalibration(unittest.TestCase):
    def test_fixture_is_positive_under_legacy_logic(self):
        """证明 fixture 有效：同一份数据在旧口径（阻断关闭）下会出共振候选。"""
        src = SCRIPT.read_text(encoding="utf-8").replace("TRIPLE_CYCLE_BLOCKED = True", "TRIPLE_CYCLE_BLOCKED = False")
        tmp = Path(tempfile.mkdtemp()) / "legacy_unblocked.py"
        tmp.write_text(src, encoding="utf-8")
        out = _run(tmp, _fixture())
        self.assertEqual(len(out["candidates"]), 1, out["gate"])

    def test_no_triple_cycle_candidates(self):
        out = _run(SCRIPT, _fixture(), ("--symbols", "600001"))
        self.assertEqual(out["candidates"], [])
        self.assertFalse(out["gate"]["open"])
        self.assertEqual(out["gate"]["state"], "小周期待核")
        self.assertTrue(out["triple_cycle_blocked"])
        self.assertEqual(out["rule_version"], "chenkai-v2-20261019")

    def test_small_cycle_unknown_and_4day_named(self):
        out = _run(SCRIPT, _fixture(), ("--symbols", "600001"))
        self.assertEqual(out["small_cycle"]["status"], "unknown")
        self.assertEqual(out["small_cycle"]["required"], "hourly_bars")
        self.assertEqual(out["custom_4day_research"]["name"], "custom_4day_research_v1")
        text = json.dumps(out, ensure_ascii=False)
        for banned in ("小周期=4日线", "小周期用 4 日线", "小周期（4 日线）"):
            self.assertNotIn(banned, text)

    def test_daily_context_branch(self):
        out = _run(SCRIPT, _fixture(), ("--symbols", "600001"))
        ctx = {c["symbol"]: c for c in out["daily_context"]}
        self.assertIn("600001", ctx)
        self.assertEqual(ctx["600001"]["small_cycle_hourly"], "unknown")
        self.assertTrue(ctx["600001"]["weekly_trend_up"])
        self.assertTrue(ctx["600001"]["near_ma20_30"])

    def test_negative_fixture_no_context_claims(self):
        """反例：持续下跌的股票——周线方向不向上，日线解释如实给 False，不出候选。"""
        out = _run(SCRIPT, _fixture(rise=-0.003, n_rise=90, n_pull=0), ("--symbols", "600001"))
        self.assertEqual(out["candidates"], [])
        ctx = {c["symbol"]: c for c in out["daily_context"]}
        self.assertFalse(ctx["600001"]["weekly_trend_up"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
