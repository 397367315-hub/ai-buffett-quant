"""两套外壳并存：上一版五页（默认）+ 新版两页（嵌在同一外壳里）+「实验功能」入口。

测的是给用户看的那一层：
  默认 "/" 是上一版的智能漏斗（AGI QUANT 侧栏、市场闸门条、五层漏斗、13 席）；
  侧栏七个页面（漏斗/席位/候选池/个人池/回测/环境概览/案例研究）都能打开，地址可复制、刷新不丢；
  环境概览、案例研究嵌在上一版外壳里（同一个深色侧栏），数据来自 v1；
  侧栏底部「实验」进入新版工作台 #/lab/...，返回上一版正常；
  390px 宽度不横向溢出，底部导航条能点到全部页面。

上一版页面的数据来自 refresh.py 生成的 panel/data/dashboard.json（老刷新管道）；
新版两页来自 v1 快照——两套数据并存是用户明确选择的（见 ADR-004）。

运行：cd WORK_ROOT && python3 -m unittest tests.integration.test_hybrid_shell -v
"""
from __future__ import annotations

import os
import re
import socket
import tempfile
import unittest
from pathlib import Path

from tests.support.harness import WORKBENCH, Harness

from panel.domain import clock

EXTRA_LIB = Path("/tmp/debs/root/usr/lib/aarch64-linux-gnu")     # 本沙箱里 Chromium 缺的系统库（别处不需要）
DASHBOARD = WORKBENCH / "panel/data/dashboard.json"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@unittest.skipUnless(DASHBOARD.is_file(), "没有 panel/data/dashboard.json（先跑 refresh.py --once）")
class TestHybridShell(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("没有安装 Playwright")
        clock.set_fixed("2026-10-20T08:00:00Z")
        os.environ["ROUNDTABLE_QUIET"] = "1"
        # 端口要与 settings.port 一致：Host/Origin 白名单按它生成，否则会话与旧接口都会被 403
        cls.port = free_port()
        cls.h = Harness(runtime=Path(tempfile.mkdtemp(prefix="rt-hybrid-")), port=cls.port, workers=False)
        cls.h.serve_on_port(cls.port)
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.h.close()
        clock.set_fixed(None)
        os.environ.pop("ROUNDTABLE_QUIET", None)

    def setUp(self):
        from playwright.sync_api import sync_playwright
        env = dict(os.environ)
        if EXTRA_LIB.is_dir():
            env["LD_LIBRARY_PATH"] = f"{EXTRA_LIB}:{env.get('LD_LIBRARY_PATH', '')}".rstrip(":")
        self.pw = sync_playwright().start()
        try:
            self.browser = self.pw.chromium.launch(env=env)
        except Exception as ex:                                  # pragma: no cover
            self.pw.stop()
            raise unittest.SkipTest(f"Chromium 无法启动：{str(ex).splitlines()[0][:120]}")
        self.errors: list[str] = []

    def tearDown(self):
        self.browser.close()
        self.pw.stop()

    def open(self, path: str, width: int = 1440, height: int = 900):
        pg = self.browser.new_page(viewport={"width": width, "height": height}, locale="zh-CN",
                                   timezone_id="Asia/Shanghai")
        pg.on("pageerror", lambda e: self.errors.append(str(e)[:200]))
        pg.goto(f"{self.base}/{path.lstrip('/')}", wait_until="networkidle")
        return pg

    def shell(self, pg) -> str | None:
        return pg.evaluate("document.querySelector('[data-shell]')?.dataset.shell")

    def test_default_is_classic_funnel_with_real_dashboard(self):
        pg = self.open("")
        pg.locator(".funnel-stage-wrap, .visual-panel").first.wait_for(timeout=20000)
        self.assertEqual(self.shell(pg), "classic")
        self.assertTrue(pg.locator(".wordmark").inner_text().startswith("AGI"))
        self.assertIn("智能筛选漏斗", pg.locator(".heading-row h1").inner_text())
        # 老刷新管道的数据：初始股票池与最终候选池有真实数字，不是 4,600 那组演示值
        kpi = pg.locator(".metric-bottom strong").all_inner_texts()
        self.assertNotEqual(kpi[0].replace(",", ""), "4600")
        self.assertTrue(pg.locator(".mgate").count() >= 1)
        self.assertIn("市场闸门", pg.locator(".mgate").first.inner_text())
        self.assertIn("已接入真实数据", pg.locator(".market-tag").inner_text())

    def test_classic_pages_and_islands(self):
        checks = [("#/seats", ".seat-card, .seatcard", "独立策略席位"), ("#/candidates", "main", "候选"),
                  ("#/portfolio", "main", "个人池"), ("#/backtest", "main", "回测")]
        for path, sel, word in checks:
            with self.subTest(path=path):
                pg = self.open(path)
                pg.locator(sel).first.wait_for(timeout=20000)
                self.assertEqual(self.shell(pg), "classic")
                self.assertIn(word, pg.inner_text("body"))
                pg.close()
        # 两页新版嵌在上一版外壳里
        for path, page in (("#/environment", "environment"), ("#/cases", "cases")):
            with self.subTest(path=path):
                pg = self.open(path)
                pg.get_by_test_id("v1-island").wait_for(timeout=20000)
                self.assertEqual(self.shell(pg), "classic")
                self.assertEqual(pg.get_by_test_id("v1-island").get_attribute("data-v1-page"), page)
                self.assertTrue(pg.get_by_test_id("status-band").count() == 1)
                self.assertIn("AGI", pg.locator(".wordmark").inner_text())
                pg.close()

    def test_old_server_without_universe_param_falls_back(self):
        """浏览器刷新了、后台还是改动前启动的旧服务：它不认识 ?universe= 会返回 400。
        这时必须退回不带参数的旧地址（13 席正常显示），并明确提示「重启服务后开关可用」，不能默默显示 4 个演示席位。"""
        pg = self.browser.new_page(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        pg.on("pageerror", lambda e: self.errors.append(str(e)[:200]))
        pg.route(re.compile(r".*/api/dashboard\?.*"), lambda route: route.fulfill(
            status=400, content_type="application/json",
            body='{"error":{"code":"UNKNOWN_QUERY_PARAM","message":"unknown query parameter","request_id":"req_x","retryable":false,"details":[]}}'))
        pg.goto(f"{self.base}/#/seats", wait_until="networkidle")
        pg.locator(".seat-card, .seatcard").first.wait_for(timeout=20000)
        self.assertEqual(pg.locator(".seat-card, .seatcard").count(), 13)      # 13 席都在，不是 4 个演示席位
        self.assertEqual(pg.get_by_test_id("scope-stale").count(), 1)          # 明确提示要重启
        pg.goto(f"{self.base}/", wait_until="networkidle")
        pg.get_by_test_id("scope-switch").wait_for(timeout=10000)
        self.assertEqual(pg.get_by_test_id("scope-switch").get_attribute("data-supported"), "false")
        self.assertTrue(pg.get_by_test_id("scope-main").is_disabled())

    def test_universe_switch_main_vs_chinext(self):
        pg = self.open("")
        pg.locator(".metric-bottom strong").first.wait_for(timeout=20000)
        pg.get_by_test_id("scope-switch").wait_for(timeout=10000)
        all_l1 = pg.locator(".metric-bottom strong").first.inner_text()
        self.assertEqual(pg.locator(".metric-foot").first.inner_text(), "沪深主板＋创业板")
        pg.get_by_test_id("scope-main").click()
        self.assertEqual(pg.get_by_test_id("scope-main").get_attribute("aria-pressed"), "true")
        pg.wait_for_timeout(2500)
        main_l1 = pg.locator(".metric-bottom strong").first.inner_text()
        self.assertNotEqual(main_l1, all_l1)                       # 两个口径数字不同
        self.assertLess(int(main_l1.replace(",", "")), int(all_l1.replace(",", "")))
        self.assertEqual(pg.locator(".metric-foot").first.inner_text(), "沪深主板")
        # 候选池也跟着换一份（数量与漏斗末层一致）
        last_layer = pg.locator(".step-right b").last.inner_text()
        pg.goto(f"{self.base}/#/candidates", wait_until="networkidle")
        pg.wait_for_timeout(1500)
        self.assertEqual(pg.locator(".cand-card, .cand-row, [data-code]").count(), int(last_layer))
        # 切回：数字回到含创业板
        pg.goto(f"{self.base}/", wait_until="networkidle")
        pg.get_by_test_id("scope-all").click()
        pg.wait_for_timeout(2500)
        self.assertEqual(pg.locator(".metric-bottom strong").first.inner_text(), all_l1)

    def test_candidate_detail_has_moving_averages(self):
        pg = self.open("#/candidates")
        pg.locator(".cand-card, .cand-row, [data-code]").first.click()
        box = pg.get_by_test_id("cand-chart")
        box.wait_for(timeout=20000)
        self.assertGreaterEqual(box.get_attribute("data-ma-count") and int(box.get_attribute("data-ma-count")), 3)
        chips = pg.locator(".ma-chip").all_inner_texts()
        self.assertTrue(any(c.startswith("MA5 ") for c in chips), chips)
        self.assertTrue(any(c.startswith("MA20 ") for c in chips), chips)
        self.assertGreaterEqual(pg.locator(".cand-chart path").count(), 3)      # 每条均线一根 path
        before = pg.locator(".cand-chart path").count()
        pg.locator(".ma-chip", has_text="MA10").click()
        self.assertEqual(pg.locator(".cand-chart path").count(), before - 1)     # 点一下隐藏
        self.assertEqual(self.errors, [])

    def test_lab_entry_and_back(self):
        pg = self.open("")
        pg.get_by_test_id("rail-lab").click()
        pg.get_by_test_id("app-root").wait_for(timeout=20000)
        self.assertEqual(self.shell(pg), "lab")
        self.assertIn("#/lab/", pg.url)
        # 工作区链接都带 lab 前缀（「返回主界面」是故意指回上一版壳的，单独测）
        nav = pg.locator("nav.wsnav a:not(.wsnav-home)").all_inner_texts()
        self.assertIn("事前计划", nav)
        href = pg.locator("nav.wsnav a:not(.wsnav-home)").first.get_attribute("href")
        self.assertTrue(href.startswith("#/lab/"), href)
        # 实验功能里有明确的返回入口
        self.assertEqual(pg.get_by_test_id("lab-home").get_attribute("href"), "#/funnel")
        pg.get_by_test_id("lab-home").click()
        pg.locator(".rail").first.wait_for(timeout=20000)
        self.assertEqual(self.shell(pg), "classic")
        self.assertIn("#/funnel", pg.url)
        pg.get_by_test_id("rail-lab").click()
        pg.get_by_test_id("app-root").wait_for(timeout=20000)
        # 回上一版：地址栏直接给 #/seats
        pg.goto(f"{self.base}/#/seats", wait_until="networkidle")
        pg.locator(".seat-card, .seatcard").first.wait_for(timeout=20000)
        self.assertEqual(self.shell(pg), "classic")
        # 裸的 v1 地址被规范到实验功能
        pg.goto(f"{self.base}/#/plans", wait_until="networkidle")
        pg.get_by_test_id("app-root").wait_for(timeout=20000)
        self.assertIn("#/lab/plans", pg.url)
        self.assertEqual(self.shell(pg), "lab")

    def test_roundtable_lives_in_classic_seats_page(self):
        pg = self.open("#/seats")
        btn = pg.get_by_test_id("open-roundtable")
        btn.wait_for(timeout=20000)
        self.assertIn("圆桌", btn.inner_text())
        btn.click()
        pg.get_by_test_id("roundtable").wait_for(timeout=20000)
        self.assertIn("#/roundtable", pg.url)
        self.assertEqual(self.shell(pg), "classic")          # 留在主界面，不是实验功能
        pg.locator('[data-testid^="roundtable-seat-"]').first.wait_for(timeout=20000)
        self.assertGreaterEqual(pg.locator('[data-testid^="roundtable-seat-"]').count(), 13)

    def test_mobile_bottom_nav_reaches_all_pages(self):
        pg = self.open("", width=390, height=844)
        pg.locator(".visual-panel").first.wait_for(timeout=20000)
        self.assertFalse(pg.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"))
        buttons = pg.locator(".rail-button").count()
        self.assertGreaterEqual(buttons, 8)                       # 7 个页面 + 实验入口
        pg.get_by_test_id("rail-environment").click()
        pg.get_by_test_id("v1-island").wait_for(timeout=20000)
        self.assertIn("#/environment", pg.url)
        self.assertFalse(pg.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"))

    def test_no_page_errors(self):
        for path in ("", "#/seats", "#/candidates", "#/portfolio", "#/backtest", "#/environment", "#/cases", "#/lab/environment"):
            pg = self.open(path)
            pg.wait_for_timeout(1200)
            pg.close()
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
