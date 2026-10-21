"""首次启动与每日采集：新运行目录（没有交易日历、没有快照）里点「采集最新快照」必须能走通。

这条路径以前没有测到：验收用例都预置了 fixture 日历，所以「空库 → 没有日历 → 按钮拿不到交易日 → 永远采不了」的死锁
在 110 项验收全过的情况下仍然存在。这里用**真实 NumcatAdapter + 假 meoz CLI**（不联网、不读任何凭据）复现：

- 假 CLI 的交易日历与本机只读归档一致（归档里缺的工作日＝休市），之后的工作日为开市；
  默认按供应商文档的 end_latest=true 把区间截到「当前可用最新交易日」，只有显式 end_latest=false 才返回未来；
- 日线按会话日从只读归档 panel/data/daily 取（每天只取前 60 行，够快）；
- 浏览器部分（可选，需要 Playwright）真的去点顶部按钮与空页面的归档按钮。

运行：cd WORK_ROOT && python3 -m unittest tests.integration.test_first_run -v
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import textwrap
import unittest
from datetime import date, timedelta
from pathlib import Path

from tests.support.harness import WORKBENCH, Harness

from panel.domain import clock
from panel.services import calendar_service, snapshot_service

ARCHIVE = WORKBENCH / "panel/data/daily"
EXTRA_LIB = Path("/tmp/debs/root/usr/lib/aarch64-linux-gnu")     # 本沙箱里 Chromium 缺的系统库（别处不需要）

FAKE_CLI = textwrap.dedent('''
    import json, os, sys, datetime as dt
    args = sys.argv[1:]
    op = args[args.index("--operation-id") + 1]
    req = json.load(open(args[args.index("--request-file") + 1], encoding="utf-8"))
    here = os.path.dirname(os.path.abspath(__file__))
    scen = json.load(open(os.path.join(here, "scenario.json"), encoding="utf-8"))
    with open(os.path.join(here, "calls.ndjson"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"op": op, "params": req.get("params")}, ensure_ascii=False) + "\\n")
    if scen.get("auth_missing"):
        print(json.dumps({"ok": False, "error_type": "AuthError",
                          "error": "尚未配置 API Key（canary " + scen["canary"] + "）"}, ensure_ascii=False))
        sys.exit(3)
    p = req.get("params") or {}
    def out(rows):
        fields = list(rows[0].keys()) if rows else []
        print(json.dumps({"results": [{"code": 200, "data": {"fields": fields,
                                                           "items": [[r[k] for k in fields] for r in rows]}}]},
                         ensure_ascii=False))
    def d8(s):
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))
    if op == "reference_calendar_range":
        s, e = d8(p["startdate"]), d8(p["enddate"])
        if p.get("end_latest", True) is not False:          # 供应商默认：截到当前可用最新交易日
            e = min(e, dt.date.fromisoformat(scen["latest_published"]))
        closed = set(scen["closed"])
        rows, d = [], s
        while d <= e:
            iso = d.isoformat()
            rows.append({"cal_date": d.strftime("%Y%m%d"),
                         "is_open": "1" if d.weekday() < 5 and iso not in closed else "0"})
            d += dt.timedelta(days=1)
        out(rows)
    elif op == "reference_stock_daily":
        day = p["tradedate"]
        iso = day[:4] + "-" + day[4:6] + "-" + day[6:]
        f = os.path.join(scen["archive"], day + ".json")
        if iso <= scen["latest_published"] and os.path.exists(f):
            out(json.load(open(f, encoding="utf-8"))[: scen.get("rows_per_day", 60)])
        else:
            out([])
    else:
        out([])
''')


def archive_days() -> list[str]:
    return sorted(f"{p.stem[:4]}-{p.stem[4:6]}-{p.stem[6:]}" for p in ARCHIVE.glob("*.json") if p.stem.isdigit())


def closed_weekdays(days: list[str]) -> list[str]:
    """归档覆盖区间里没有文件的工作日＝休市（与真实交易所日历一致）。"""
    have = set(days)
    d, end, out = date.fromisoformat(days[0]), date.fromisoformat(days[-1]), []
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in have:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeSource:
    def __init__(self, root: Path, **scenario):
        self.dir = root / "fake_meoz"
        self.dir.mkdir(parents=True, exist_ok=True)
        days = archive_days()
        scen = {"archive": str(ARCHIVE), "closed": closed_weekdays(days), "latest_published": days[-1], **scenario}
        (self.dir / "meoz.py").write_text(FAKE_CLI, encoding="utf-8")
        (self.dir / "scenario.json").write_text(json.dumps(scen, ensure_ascii=False), encoding="utf-8")
        self.cli = self.dir / "meoz.py"

    def calls(self, op: str | None = None) -> list[dict]:
        f = self.dir / "calls.ndjson"
        rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()] if f.exists() else []
        return [r for r in rows if op is None or r["op"] == op]


class FirstRunBase(unittest.TestCase):
    workers = False
    port = 8787

    def setUp(self):
        if not ARCHIVE.is_dir() or not any(ARCHIVE.glob("*.json")):
            raise unittest.SkipTest("新仓库未携带历史日线归档，首次启动归档验收需外部 fixture")
        snapshot_service.set_provider_factory(None)       # 走默认 NumcatAdapter（与用户机器同一条代码路径）
        calendar_service.set_override(None)
        self.tmp = Path(tempfile.mkdtemp(prefix="rt-first-run-"))
        self.harnesses: list[Harness] = []

    def tearDown(self):
        for h in self.harnesses:
            h.close()
        clock.set_fixed(None)

    def start(self, now: str, **scenario) -> tuple[Harness, FakeSource]:
        clock.set_fixed(now)
        src = FakeSource(self.tmp, **scenario)
        h = Harness(runtime=self.tmp / "runtime", workers=self.workers, port=self.port, file_import_roots=(ARCHIVE,),
                    meoz_cli=src.cli, use_legacy_data_cache=False, provider_max_retry=0, provider_timeout_seconds=60)
        self.harnesses.append(h)
        return h, src


class TestFirstRunCollect(FirstRunBase):
    def collect(self, c, day: str):
        st, acc, _ = c.post("/api/v1/snapshots", {"session_date": day, "source_mode": "provider_api", "symbols": [],
                                                  "allow_stale": True})
        if st != 202:
            return st, acc
        return st, c.wait_job(acc["job"]["job_id"], timeout=300)

    def health(self, c) -> dict:
        st, h, _ = c.get("/api/v1/health/data")
        self.assertEqual(st, 200, h)
        return h

    def test_fresh_install_after_close_collects_today_and_calendar_reaches_future(self):
        h, src = self.start("2026-10-19T08:00:00Z")                      # 周一 16:00（上海）收盘后
        c = h.client()
        c.bootstrap()
        h0 = self.health(c)
        self.assertIsNone(h0["latest_completed_session_date"])            # 空库：没有日历，不猜
        self.assertIn("trading_calendar", h0["missing_inputs"])
        st, job = self.collect(c, "2026-10-19")
        self.assertEqual(st, 202)
        self.assertIn(job["state"], ("succeeded", "partial"), job)
        st, snap, _ = c.get(f"/api/v1/snapshots/{job['resource_id']}")
        self.assertEqual(snap["metadata"]["session_date"], "2026-10-19")
        self.assertEqual(snap["metadata"]["source_mode"], "provider_api")
        h1 = self.health(c)
        self.assertEqual(h1["latest_completed_session_date"], "2026-10-19")
        self.assertNotIn("trading_calendar", h1["missing_inputs"])
        cal_call = src.calls("reference_calendar_range")[0]["params"]
        self.assertIs(cal_call["end_latest"], False)                     # 显式要未来日历
        self.assertGreaterEqual(cal_call["enddate"], "20270101")
        cov = h.db.scalar("SELECT MAX(coverage_end) FROM calendar_versions WHERE source LIKE 'numcat:%'")
        self.assertGreaterEqual(cov, "2027-01-01")
        # 次日：不重新取数也知道「最近完整交易日」；盘前不允许采今天（会话 12 小时过期，像浏览器一样重新领一次）
        clock.set_fixed("2026-10-20T00:30:00Z")                         # 周二 08:30
        c.bootstrap()
        self.assertEqual(self.health(c)["latest_completed_session_date"], "2026-10-19")
        st, err = self.collect(c, "2026-10-20")
        self.assertEqual((st, err["error"]["code"]), (422, "SESSION_NOT_STARTED"), err)
        # 盘中：今天的日线还没发布（假源只发布到 10-19）→ 任务明确失败 SOURCE_EMPTY，最近完整交易日仍是 10-19
        clock.set_fixed("2026-10-20T02:00:00Z")                         # 周二 10:00
        st, job = self.collect(c, "2026-10-20")
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["error"]["error"]["code"], "SOURCE_EMPTY")
        self.assertEqual(self.health(c)["latest_completed_session_date"], "2026-10-19")
        # 收盘半小时后：日历本身就知道今天已完整
        clock.set_fixed("2026-10-20T08:00:00Z")
        self.assertEqual(self.health(c)["latest_completed_session_date"], "2026-10-20")
        # 未来日期直接拒绝
        st, err = self.collect(c, "2026-10-21")
        self.assertEqual((st, err["error"]["code"]), (422, "SESSION_IN_FUTURE"), err)

    def test_fresh_install_on_weekend_falls_back_to_last_completed_session(self):
        h, _src = self.start("2026-10-17T04:00:00Z", latest_published="2026-10-16")   # 周六中午
        c = h.client()
        c.bootstrap()
        st, job = self.collect(c, "2026-10-17")                          # 空库时只能先问「今天」
        self.assertEqual(st, 202)                                        # 日历未知：不能同步拒绝
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["error"]["error"]["code"], "NON_TRADING_SESSION")
        day = self.health(c)["latest_completed_session_date"]            # 失败的任务已经把日历取回来了
        self.assertEqual(day, "2026-10-16")
        st, job = self.collect(c, day)
        self.assertIn(job["state"], ("succeeded", "partial"), job)
        st, err = self.collect(c, "2026-10-17")                          # 日历已知：休市日同步 422
        self.assertEqual((st, err["error"]["code"]), (422, "NON_TRADING_SESSION"), err)

    def test_missing_credentials_are_explained_without_echoing_cli_output(self):
        h, _src = self.start("2026-10-19T08:00:00Z", auth_missing=True, canary="CANARY-7f3a91")
        c = h.client()
        c.bootstrap()
        st, job = self.collect(c, "2026-10-19")
        self.assertEqual(job["state"], "failed")
        err = job["error"]["error"]
        self.assertEqual(err["code"], "SOURCE_CREDENTIALS_MISSING")
        self.assertIn("configure-key", err["message"])
        self.assertNotIn("CANARY-7f3a91", json.dumps(job, ensure_ascii=False))

    def test_unreturned_days_are_unknown_not_closed(self):
        """回归：供应商截断了区间（没返回今天）时，今天是「未知」，不能被当成休市。"""
        rows = [{"cal_date": "20261015", "is_open": "1"}, {"cal_date": "20261016", "is_open": "1"}]
        cal = calendar_service.from_provider_rows(rows, "20261001", "20261231", "a" * 64)
        self.assertEqual(cal.status("2026-10-16"), "open")
        self.assertEqual(cal.status("2026-10-19"), "unknown")
        self.assertIsNone(calendar_service.from_provider_rows([], "20261001", "20261231", "a" * 64))

    def test_archive_import_on_empty_install(self):
        h, _src = self.start("2026-10-20T08:00:00Z")
        c = h.client()
        c.bootstrap()
        st, idx, _ = c.get("/api/v1/workspace/index")
        day = idx["archive"]["coverage_end"]
        self.assertEqual(day, archive_days()[-1])
        st, acc, _ = c.post("/api/v1/snapshots", {"session_date": day, "source_mode": "file_import", "symbols": [],
                                                  "allow_stale": True})
        self.assertEqual(st, 202, acc)
        job = c.wait_job(acc["job"]["job_id"], timeout=300)
        self.assertIn(job["state"], ("succeeded", "partial"), job)
        st, snap, _ = c.get("/api/v1/snapshots/latest?allow_stale=true")
        self.assertEqual(st, 200, snap)
        self.assertEqual(snap["metadata"]["source_mode"], "file_import")
        self.assertEqual(snap["metadata"]["quote_date"], day)
        self.assertNotEqual(snap["metadata"]["freshness"], "fresh")     # 归档不冒充今天的行情
        self.assertGreater(len(snap["instruments"]), 1000)


class TestFirstRunInBrowser(FirstRunBase):
    """真实浏览器里点按钮（需要 Playwright + Chromium；没有就跳过）。"""
    workers = True

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("没有安装 Playwright")

    def setUp(self):
        super().setUp()
        self.port = free_port()
        os.environ["ROUNDTABLE_QUIET"] = "1"                            # 不打印逐请求访问日志
        self.addCleanup(os.environ.pop, "ROUNDTABLE_QUIET", None)

    def open(self, h: Harness):
        from playwright.sync_api import sync_playwright
        h.serve_on_port(self.port)
        env = dict(os.environ)
        if EXTRA_LIB.is_dir():
            env["LD_LIBRARY_PATH"] = f"{EXTRA_LIB}:{env.get('LD_LIBRARY_PATH', '')}".rstrip(":")
        pw = sync_playwright().start()
        try:
            browser = pw.chromium.launch(env=env)
        except Exception as ex:
            pw.stop()
            raise unittest.SkipTest(f"Chromium 无法启动：{str(ex).splitlines()[0][:120]}")
        self.addCleanup(pw.stop)
        self.addCleanup(browser.close)
        pg = browser.new_page(viewport={"width": 1280, "height": 860}, locale="zh-CN")
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"http://127.0.0.1:{self.port}/#/environment")   # 环境概览（上一版外壳里的新页面）
        pg.get_by_test_id("collect").wait_for(timeout=15000)
        return pg, errors

    def test_weekend_click_collects_last_trading_day(self):
        h, _src = self.start("2026-10-17T04:00:00Z", latest_published="2026-10-16")
        pg, errors = self.open(h)
        self.assertIn("还没有快照", pg.get_by_test_id("quote-date").inner_text())
        pg.get_by_test_id("collect").click()
        pg.get_by_test_id("quote-date").filter(has_text="2026-10-16").wait_for(timeout=120000)
        note = pg.get_by_test_id("collect-note").inner_text()
        self.assertIn("不是交易日", note)
        self.assertIn("2026-10-16", note)
        self.assertEqual(pg.get_by_test_id("mode-word").inner_text().strip(), "数据源快照")
        self.assertEqual(pg.get_by_test_id("collect-error").count(), 0)
        self.assertEqual(errors, [])

    def test_empty_page_archive_button(self):
        h, _src = self.start("2026-10-20T08:00:00Z")
        pg, errors = self.open(h)
        day = archive_days()[-1]
        pg.get_by_test_id("archive-import").click()
        pg.get_by_test_id("quote-date").filter(has_text=day).wait_for(timeout=120000)
        self.assertEqual(pg.get_by_test_id("mode-word").inner_text().strip(), "本机历史归档")
        # 市场矩阵：涨红跌绿（A 股习惯）与成交额用亿
        up = pg.locator('[data-metric="breadth_up_count"] dd').first
        down = pg.locator('[data-metric="breadth_down_count"] dd').first
        self.assertIn("rise", up.get_attribute("class"))
        self.assertIn("fall", down.get_attribute("class"))
        self.assertNotEqual(up.evaluate("e => getComputedStyle(e).color"), down.evaluate("e => getComputedStyle(e).color"))
        amount = pg.locator('[data-metric="total_amount_minor"] dd').first
        self.assertTrue(amount.inner_text().endswith("亿"), amount.inner_text())
        self.assertIn("元", amount.get_attribute("title") or "")          # 悬停里保留精确元值
        row_amount = pg.locator('[data-field="amount"]').first
        self.assertTrue(row_amount.inner_text().endswith("亿"), row_amount.inner_text())
        # 涨跌家数对比条（同花顺那种）：红涨、灰平、绿跌，宽度按家数占比
        bar = pg.get_by_test_id("breadth-bar")
        self.assertEqual(bar.count(), 1)
        self.assertIn("上涨", bar.inner_text())
        self.assertIn("下跌", bar.inner_text())
        self.assertGreater(int(bar.get_attribute("data-up")), 0)
        self.assertGreater(int(bar.get_attribute("data-down")), 0)
        total_pct = bar.evaluate("e => Array.from(e.querySelectorAll('.bb-track i')).reduce((s, i) => s + parseFloat(i.style.width), 0)")
        self.assertAlmostEqual(total_pct, 100.0, delta=0.5)
        self.assertNotEqual(bar.locator(".bb-up").evaluate("e => getComputedStyle(e).backgroundColor"),
                            bar.locator(".bb-down").evaluate("e => getComputedStyle(e).backgroundColor"))
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
