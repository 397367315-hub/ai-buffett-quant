#!/usr/bin/env python3
"""界面预览与截图（开发自检用）：进程内启动本地服务 → 预置数据 → Chromium 打开各工作区截图。

    LD_LIBRARY_PATH=/tmp/debs/root/usr/lib/aarch64-linux-gnu python3 tools/ui_preview.py --out /tmp/shots [--seed]

只监听 127.0.0.1；数据来自只读归档（panel/data/daily）；runtime 在临时目录，不碰真实账本。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.support.harness import WORKBENCH, Harness   # noqa: E402

PAGES = ["environment", "cases", "seats", "plans", "portfolio", "training", "validation"]


def seed(h: Harness, port: int) -> dict:
    sys.path.insert(0, str(ROOT / "tools"))
    from preview_client import LocalClient
    c = LocalClient(port)
    c.bootstrap()
    st, acc = c.req("POST", "/api/v1/snapshots", {"session_date": "2026-10-19", "source_mode": "file_import",
                                                  "symbols": ["600000.SH", "000001.SZ", "600519.SH"], "allow_stale": True})
    j = c.wait(acc["job"]["job_id"])
    sid = j["resource_id"]
    st, pf = c.req("POST", "/api/v1/portfolios", {"mode": "paper", "initial_cash_minor": "100000000", "name": "我的模拟组合"})
    body = {"supersedes_case_id": None, "snapshot_id": sid, "title": "浦发银行回踩是否值得等", "question": "按利弗莫尔的关键点，现在是不是顺势？需要什么证据才算确认？",
            "symbols": ["600000.SH"], "horizon": "swing", "seat_ids": ["trading-livermore-v1", "trading-candle-v1", "trading-wyckoff-v1"],
            "primary_strategy_id": "trading-livermore-v1",
            "role_assignments": [{"seat_id": "trading-livermore-v1", "role": "primary"}, {"seat_id": "trading-candle-v1", "role": "validator"},
                                 {"seat_id": "trading-wyckoff-v1", "role": "validator"}],
            "llm_mode": "disabled", "provider_id": None, "model_id": None, "user_context_id": None, "replay_id": None}
    st, acc = c.req("POST", "/api/v1/cases", body)
    j = c.wait(acc["job"]["job_id"])
    return {"snapshot_id": sid, "portfolio_id": pf["portfolio_id"], "case_id": j["resource_id"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "rt_shots"))
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--pages", default=",".join(PAGES))
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rt = Path(tempfile.mkdtemp(prefix="rt-preview-"))
    h = Harness(workers=True, port=a.port, runtime=rt, file_import_roots=(WORKBENCH / "panel/data/daily",))
    h.serve_on_port(a.port)
    info = seed(h, a.port) if a.seed else {}
    print(json.dumps(info, ensure_ascii=False))
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        for vw, vh, tag in ((1440, 900, "desktop"), (390, 844, "mobile")):
            ctx = b.new_context(viewport={"width": vw, "height": vh}, locale="zh-CN", timezone_id="Asia/Shanghai")
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            for name in a.pages.split(","):
                path = name if "/" in name else name
                if name == "cases" and info.get("case_id"):
                    path = f"cases/{info['case_id']}"
                pg.goto(f"http://127.0.0.1:{a.port}/#/{path}")
                pg.wait_for_timeout(1800)
                f = out / f"{tag}-{name.replace('/', '_')}.png"
                pg.screenshot(path=str(f), full_page=True)
                over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                print(tag, name, "overflow", over, "errors", errs[-3:])
            ctx.close()
        b.close()
    h.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
