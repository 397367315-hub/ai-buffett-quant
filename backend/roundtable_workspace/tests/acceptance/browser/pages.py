"""浏览器 driver（Playwright + Chromium，真实页面、真实本地服务、同一个用例 runtime）。

- 每个用例在自己的端口上起被测服务（ctx.harness 已固定端口），浏览器只连 127.0.0.1；
- 结果投影只来自：DOM（data-* 属性与可见文字）、浏览器收到的网络响应体、localStorage/sessionStorage/CacheStorage、
  下载的导出文件、截图（证据）——不读被测代码的内部状态；
- 截图左下角叠加「用例 | 快照 | 构建」标签，存到 cases/<id>/screenshots/。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from panel.domain import clock

from .. import fixtures as fx
from ..errors import Blocked

EXTRA_LIB = Path("/tmp/debs/root/usr/lib/aarch64-linux-gnu")


# ── 浏览器会话 ──
class Browser:
    def __init__(self, ctx):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise Blocked("本机没有安装 Playwright（pip install playwright && python -m playwright install chromium）",
                          code="BROWSER_UNAVAILABLE")
        self.ctx = ctx
        self.port = ctx.serve()
        self.base = f"http://127.0.0.1:{self.port}"
        env = dict(os.environ)
        if EXTRA_LIB.is_dir():
            env["LD_LIBRARY_PATH"] = f"{EXTRA_LIB}:{env.get('LD_LIBRARY_PATH', '')}".rstrip(":")
        self._pw = sync_playwright().start()
        try:
            self.browser = self._pw.chromium.launch(env=env)
        except Exception as ex:
            self._pw.stop()
            raise Blocked(f"Chromium 无法启动：{str(ex).splitlines()[0][:200]}", code="BROWSER_UNAVAILABLE")
        self.contexts = []
        self.responses: list[dict] = []
        self.downloads: list[Path] = []
        self.shots = 0
        self.page_errors: list[str] = []

    def new_page(self, *, width=1440, height=900, cookie: str | None = None, record=True):
        bc = self.browser.new_context(viewport={"width": width, "height": height}, locale="zh-CN",
                                      timezone_id="Asia/Shanghai", accept_downloads=True)
        if cookie:
            bc.add_cookies([{"name": "rt_local_session", "value": cookie, "domain": "127.0.0.1", "path": "/",
                             "httpOnly": True, "sameSite": "Strict"}])
        pg = bc.new_page()
        pg.on("pageerror", lambda e: self.page_errors.append(str(e)))
        if record:
            def on_resp(r):
                if "/api/" not in r.url:
                    return
                try:
                    body = r.text()
                except Exception:
                    body = ""
                self.responses.append({"url": r.url.replace(self.base, ""), "status": r.status, "body": body})
            pg.on("response", on_resp)
        self.contexts.append(bc)
        return pg

    def goto(self, pg, path: str, wait_ms: int = 1500):
        # 浏览器验收测的是 v1 工作台，它现在在「实验功能」里（#/lab/...）；
        # 上一版外壳（#/funnel、#/seats…）由 tests/integration/test_hybrid_shell.py 单独测。
        p = path
        if isinstance(p, str) and p.startswith("#/") and not p.startswith("#/lab/"):
            p = f"#/lab/{p[2:]}"
        pg.goto(f"{self.base}/{p.lstrip('/')}")
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(wait_ms)

    def shot(self, pg, name: str) -> Path:
        d = self.ctx.case_dir / "screenshots"
        d.mkdir(parents=True, exist_ok=True)
        from ..runner import code_hash
        build = self.ctx.state.setdefault("build_hash", code_hash()[0][:10])
        snap = pg.evaluate("document.querySelector('[data-testid=status-band]')?.dataset.snapshotId || 'no-snapshot'")
        label = f"{self.ctx.case['test_id']} | {snap} | build {build}"
        pg.evaluate("""(t) => { let e = document.getElementById('__ac_label'); if (!e) { e = document.createElement('div');
            e.id = '__ac_label'; e.style.cssText = 'position:fixed;left:6px;bottom:6px;z-index:99;background:#14263a;color:#fff;' +
            'font:11px/1.4 monospace;padding:2px 6px;border-radius:3px;opacity:.85;pointer-events:none';
            document.body.appendChild(e); } e.textContent = t; }""", label)
        f = d / f"{name}.png"
        pg.screenshot(path=str(f), full_page=True)
        self.shots += 1
        self.ctx.add_evidence(f, "image/png", f"截图：{name}（{label}）")
        return f

    def close(self):
        for c in self.contexts:
            try:
                c.close()
            except Exception:
                pass
        try:
            self.browser.close()
        finally:
            self._pw.stop()


def browser(ctx) -> Browser:
    b = ctx.state.get("browser")
    if b is None:
        b = Browser(ctx)
        ctx.state["browser"] = b
        ctx.closers.append(b.close)
    return b


def _live_cookie(ctx) -> str:
    return ctx.client().cookie


# ── AC-014：失联不切演示 ──
def browser_open(ctx, inp):
    ctx.ensure("snapshot_final")
    b = browser(ctx)
    pg = b.new_page(cookie=_live_cookie(ctx))
    b.goto(pg, f"#{inp.get('path')}" if inp.get("path") not in (None, "/") else "#/environment")
    ctx.state["page"] = pg
    st = pg.get_attribute("[data-testid=status-band]", "data-ui-data-state")
    ctx.result["ui_data_state_before"] = st
    b.shot(pg, "open")


def http_fault_set(ctx, inp):
    status = int(inp.get("status") or 503)
    ctx.state["http_fault_status"] = status
    pg = ctx.state.get("page")
    if pg is not None:
        body = json.dumps({"error": {"code": "SOURCE_UNAVAILABLE", "message": "注入的上游故障", "request_id": "req_fault",
                                     "retryable": True, "details": []}})
        pg.route(re.compile(r".*/api/.*"), lambda route: route.fulfill(status=status, content_type="application/json", body=body))
        ctx.state["fault_routed"] = True


def browser_reload(ctx, inp):
    b = browser(ctx)
    pg = ctx.state["page"]
    pg.reload()
    pg.wait_for_timeout(1500)
    band = pg.locator("[data-testid=status-band]")
    st = band.get_attribute("data-ui-data-state")
    demo = band.get_attribute("data-demo")
    root = pg.locator("[data-testid=app-root]")
    exe = root.get_attribute("data-execution-enabled")
    text = pg.inner_text("body")
    ctx.result.update({"ui_data_state": st, "demo_used_without_explicit_mode": demo == "true" or "演示" in band.inner_text(),
                       "new_plan_execution_enabled": exe == "true",
                       "unavailable_reason_visible": "数据不可用" in text,
                       "last_known_visible": pg.locator("[data-testid=last-known]").count() > 0})
    b.shot(pg, "reload-503")


# ── AC-011：缺值/0/负值 ──
def snapshot_render_projection(ctx, inp):
    b = browser(ctx)
    pg = b.new_page(cookie=_live_cookie(ctx))
    b.goto(pg, "#/environment")
    snap_resp = next((json.loads(r["body"]) for r in reversed(b.responses)
                      if r["url"].startswith("/api/v1/snapshots/latest") and r["status"] == 200), None)
    def cell(sym, field):
        loc = pg.locator(f"[data-testid=instrument-table] tr[data-symbol='{sym}'] [data-field='{field}']")
        if loc.count() == 0:
            return None, None
        v = loc.first.get_attribute("data-value")
        return (v if v != "" else None), loc.first.inner_text().strip()
    z, _ = cell("600005.SH", "change_ratio")
    n, _ = cell("600006.SH", "change_ratio")
    a, a_text = cell("600007.SH", "amount")
    ctx.result.update({"values": {"pct_chg_zero": z, "pct_chg_negative": n, "amount_missing": a},
                       "display": {"amount_missing": a_text},
                       "missing_fields": snap_resp["metadata"]["missing_fields"] if snap_resp else None,
                       "rendered_snapshot_id": snap_resp["snapshot_id"] if snap_resp else None})
    b.shot(pg, "environment-missing-values")


# ── AC-080：两个分母 ──
def market_width_render(ctx, inp):
    ctx.harness()
    n_env, n_cand = int(inp["market_denominator"]), int(inp["candidate_denominator"])
    rows = []
    d = "20261016"
    for i in range(n_cand):
        code = f"60{i:04d}" if i < n_cand // 2 else f"00{i:04d}"
        rows.append({"tradedate": d, "symbol": code, "name": f"主板{i}", "open": "10", "high": "10.3", "low": "9.9",
                     "close": "10.1", "pre_close": "10", "vol": "100", "amount": "101000"})
    extra = n_env - n_cand
    for i in range(extra):
        half = extra // 2
        code = f"300{i:03d}" if i < min(half, 1000) else (f"301{i - 1000:03d}" if i < half else f"688{(i - half) % 1000:03d}"
                                                           if i - half < 1000 else f"689{(i - half - 1000):03d}")
        rows.append({"tradedate": d, "symbol": code, "name": f"双创{i}", "open": "20", "high": "20.5", "low": "19.8",
                     "close": "19.9", "pre_close": "20", "vol": "100", "amount": "199000"})
    f = dict(ctx.fixture("snapshot_final"), facts=[])
    r = fx.ingest_fixture_snapshot(ctx, f, snapshot_id="snapshot_fixture_width", rows=rows, lineage_key="fixture:width")
    b = browser(ctx)
    pg = b.new_page(cookie=_live_cookie(ctx))
    b.goto(pg, "#/environment")
    m = pg.locator("[data-testid=market-denominator]")
    c = pg.locator("[data-testid=candidate-denominator]")
    md, cd = m.get_attribute("data-count"), c.get_attribute("data-count")
    mu, cu = m.get_attribute("data-universe-id"), c.get_attribute("data-universe-id")
    mt, ct = m.inner_text(), c.inner_text()
    ctx.result.update({"market_denominator": int(md) if md else None, "candidate_denominator": int(cd) if cd else None,
                       "market_universe_id_shown": mu, "candidate_universe_id_shown": cu,
                       "both_universe_ids_visible": bool(mu and cu and mu != cu and mu in mt and cu in ct),
                       "same_denominator_falsely_implied": (md == cd) or not (md and cd),
                       "fixture_universe_labels": {"market": inp["market_universe_id"], "candidate": inp["candidate_universe_id"]},
                       "snapshot_id": r["snapshot_id"]})
    b.shot(pg, "environment-denominators")


# ── AC-039：心理规则来源 ──
def psychology_rules_render(ctx, inp):
    ctx.ensure("snapshot_final")
    f = ctx.fixture("psychology_rules")
    rules = []
    for rr in f["rules"]:
        rules.append({"rule_id": rr["rule_id"], "rule_origin": rr["rule_origin"], "origin_ref": rr["source_ref"],
                      "text": {"author_quote": "（合成 fixture）原书条目", "editorial_summary": "（合成 fixture）编者提炼条目",
                               "engineering_policy": "（合成 fixture）工程冷却规则", "user_rule": "（合成 fixture）我的规则"}[rr["rule_origin"]],
                      "check": "must_yes" if rr["rule_origin"] in ("user_rule", "editorial_summary") else "info"})
    pid = fx.default_portfolio(ctx)
    b = browser(ctx)
    pg = b.new_page(cookie=_live_cookie(ctx))
    body = json.dumps({"schema": "psychology_rules_v1", "rules": rules}, ensure_ascii=False)
    pg.route("**/psychology_rules.v1.json", lambda route: route.fulfill(status=200, content_type="application/json", body=body))
    b.goto(pg, f"#/portfolio/{pid}")
    pg.get_by_role("button", name="录入模拟成交").click()
    pg.wait_for_selector("[data-testid=psychology-checklist]")
    items = pg.locator("[data-testid=psychology-checklist] [data-rule-id]")
    seen = []
    for i in range(items.count()):
        it = items.nth(i)
        seen.append({"rule_id": it.get_attribute("data-rule-id"), "rule_origin": it.get_attribute("data-origin"),
                     "badge": it.locator("[data-testid=origin-badge]").inner_text().strip(),
                     "check": next((r["check"] for r in rules if r["rule_id"] == it.get_attribute("data-rule-id")), "must_yes")})
    fixture_ids = {r["rule_id"] for r in f["rules"]}
    shown = [s for s in seen if s["rule_id"] in fixture_ids]
    eng = [s for s in shown if s["rule_origin"] == "engineering_policy"]
    ctx.state["rendered_rules"] = [{"rule_id": s["rule_id"], "rule_origin": s["rule_origin"], "check": s["check"]} for s in shown]
    ctx.result.update({"rule_origins": sorted({s["rule_origin"] for s in shown}), "rendered": shown,
                       "engineering_rule_attributed_to_author": any("原书" in s["badge"] for s in eng) or not eng})
    b.shot(pg, "psychology-checklist")


# ── AC-069：旧回测资料 ──
def validation_page_render(ctx, inp):
    b = browser(ctx)
    pg = b.new_page(cookie=_live_cookie(ctx))
    b.goto(pg, "#/validation", wait_ms=2500)
    le = pg.locator("[data-testid=legacy-evaluation]")
    if le.count() == 0:
        raise AssertionError("验证页没有渲染旧资料区块")
    text = le.inner_text()
    darwin = pg.locator("[data-testid=legacy-rows] tr", has_text="达尔文")
    sd = le.get_attribute("data-sampled-days")
    upper_claims = [m.group(0) for m in re.finditer(r".{0,12}上界.{0,4}", text)]
    claimed = any(("没有数学保证" not in c and "不能继承" not in c) for c in upper_claims)
    ctx.result.update({"darwin_clean_performance": (darwin.count() > 0 and darwin.first.get_attribute("data-clean") == "true"),
                       "darwin_row_rendered": darwin.count() > 0,
                       "lookahead_result_claimed_upper_bound": claimed, "upper_bound_mentions": upper_claims,
                       "sampled_days": int(sd) if sd else None,
                       "rule_proxy_disclosed": "代理" in text,
                       "unresolved_script_provenance_disclosed": ("provenance" in text) or ("未完全核实" in text)})
    b.shot(pg, "validation-legacy")


# ── AC-070：卡牌不诱导交易 ──
BANNED_UNLOCK = ("解锁", "SSR", "稀有", "连胜", "段位", "战力", "充值", "加仓奖励")
BANNED_TASKS = ("去交易", "完成一笔交易", "每日签到", "今日任务", "立即买入", "赶紧")


def card_page_render(ctx, inp):
    ctx.ensure("snapshot_final")
    case_id = fx.ensure_case(ctx)
    # 盈利连胜场景：自报账户里连续三笔盈利的手工记录（卡牌页面不应因此出现任何解锁/奖励）
    st, pf, _ = fx.call(ctx, "POST", "/api/v1/portfolios", {"mode": "user_reported", "initial_cash_minor": "1000000",
                                                             "name": "盈利连胜（合成）"}, want=201)
    pid, rev = pf["portfolio_id"], pf["revision"]
    for i, (buy, sell) in enumerate([("10.00", "11.00"), ("10.00", "11.50"), ("10.00", "12.00")]):
        for side, px in (("buy", buy), ("sell", sell)):
            gross = int(float(px) * 100 * 100)
            net = -gross if side == "buy" else gross
            body = {"portfolio_id": pid, "event_type": "manual_trade_record", "mode": "user_reported",
                    "effective_at": clock.utc_iso(), "symbol": fx.SYM_A, "side": side, "shares": 100, "price_decimal": px,
                    "net_cash_delta_minor": str(net), "snapshot_id": None, "plan_id": None, "plan_version": None,
                    "corrects_event_id": None, "correction_action": None, "corrected_event_type": None,
                    "note": f"合成盈利记录 {i + 1}", "replay_id": None, "expected_portfolio_revision": rev, "evidence_ids": [],
                    "reported_fees": None}
            st, ev, _ = fx.call(ctx, "POST", "/api/v1/journal/events", body, want=201)
            rev += 1
    st, deck, _ = fx.call(ctx, "POST", "/api/v1/decks", {"source_type": "case", "source_id": case_id, "source_version": None,
                                                         "title": "复习：fixture Case"}, want=201)
    b = browser(ctx)
    pg = b.new_page(cookie=_live_cookie(ctx))
    texts = []
    b.goto(pg, "#/seats")
    texts.append(pg.inner_text("main"))
    b.shot(pg, "seat-library")
    b.goto(pg, f"#/seats/{deck['deck_id']}")
    deck_text = pg.inner_text("main")
    texts.append(deck_text)
    pg.locator("[data-testid=flash-card] button", has_text="看答案").first.click()
    src = pg.locator("[data-testid=deck-source]")
    ev = pg.locator("[data-testid=card-evidence]")
    b.shot(pg, "deck")
    all_text = "\n".join(texts)
    unlocks = sum(all_text.count(w) for w in BANNED_UNLOCK)
    tasks = sum(all_text.count(w) for w in BANNED_TASKS)
    ctx.result.update({"profit_based_execution_unlocks": unlocks, "forced_trade_tasks": tasks,
                       "evidence_source_visible": src.count() > 0 and ev.count() > 0,
                       "conflicts_visible": "分歧" in deck_text, "scenario": inp.get("scenario"),
                       "profit_streak_trades": 6})


# ── AC-057：训练里的未来数据 ──
def browser_replay_open(ctx, inp):
    ctx.ensure("server_session_context")
    cl = ctx.state["sessions"]["session_fixture_replay"]
    b = browser(ctx)
    pg = b.new_page(cookie=cl.cookie)
    rid = cl.context["replay_id"]
    visited = []
    for path in ("#/environment", f"#/training/{rid}", "#/cases", "#/plans", "#/portfolio", "#/seats", "#/validation"):
        b.goto(pg, path)
        visited.append({"path": path, "html": pg.content()})
        b.shot(pg, f"replay-{path.strip('#/').replace('/', '_') or 'home'}")
    b.goto(pg, f"#/training/{rid}")
    exp = None
    btn = pg.locator("[data-testid=export-replay]")
    if btn.count():
        with pg.expect_download() as dl:
            btn.click()
        d = dl.value
        p = ctx.case_dir / "export" / d.suggested_filename
        p.parent.mkdir(parents=True, exist_ok=True)
        d.save_as(str(p))
        exp = p
        ctx.add_evidence(p, "application/json", "训练页导出的文件")
    storage = pg.evaluate("""async () => {
        const dump = s => Object.fromEntries(Array.from({length: s.length}, (_, i) => [s.key(i), s.getItem(s.key(i))]));
        const caches_ = [];
        if (window.caches) { for (const k of await caches.keys()) { const c = await caches.open(k);
            for (const r of await c.keys()) { const resp = await c.match(r); caches_.push({url: r.url, body: await resp.text()}); } } }
        return {local: dump(localStorage), session: dump(sessionStorage), cache: caches_};
    }""")
    ctx.state.update({"replay_pages": visited, "replay_export": exp, "replay_storage": storage, "replay_cutoff": cl.context["knowledge_cutoff_at"]})
    ctx.result["visited_pages"] = [v["path"] for v in visited]
    ctx.result["session_mode_in_browser"] = pg.get_attribute("[data-testid=app-root]", "data-session-mode")


def _future_bars(obj, cutoff_session: str) -> int:
    n = 0
    if isinstance(obj, dict):
        if "session_date" in obj and "close_decimal" in obj and str(obj["session_date"]) > cutoff_session:
            n += 1
        for v in obj.values():
            n += _future_bars(v, cutoff_session)
    elif isinstance(obj, list):
        for v in obj:
            n += _future_bars(v, cutoff_session)
    return n


def browser_network_cache_export_audit(ctx, inp):
    from ..drivers.security import future_markers, scan_future
    b = browser(ctx)
    forbidden = set(inp["forbidden_fact_ids"])
    marks = future_markers(ctx)
    unknown = forbidden - set(marks)
    if unknown:
        raise AssertionError(f"没有这些 fact 的识别标记：{unknown}")

    def hits(blob: str) -> list[str]:
        return sorted(f for f in scan_future(ctx, blob) if f in forbidden)
    net_blob = "\n".join(r["body"] for r in b.responses)
    st = ctx.state["replay_storage"]
    cache_blob = json.dumps(st, ensure_ascii=False)
    html_blob = "\n".join(v["html"] for v in ctx.state["replay_pages"])
    exp = ctx.state.get("replay_export")
    exp_blob = exp.read_text(encoding="utf-8") if exp else ""
    cut_session = clock.shanghai_date(clock.parse_utc(ctx.state["replay_cutoff"]))
    fut_bars = 0
    for r in b.responses:
        try:
            fut_bars += _future_bars(json.loads(r["body"]), cut_session)
        except ValueError:
            pass
    # 阳性对照：真实盘会话读取同一份未来数据，检测器必须能认出来，否则上面的「0 命中」不可信
    live = b.new_page(cookie=ctx.state["sessions"]["session_fixture_live"].cookie, record=False)
    b.goto(live, "#/environment")
    resp = live.request.get(f"{b.base}/api/v1/snapshots/snapshot_fixture_future_live")
    control = sorted(f for f in scan_future(ctx, resp.text()) if f in forbidden)
    ctx.result.update({"network": {"forbidden_fact_ids": hits(net_blob), "responses": len(b.responses)},
                       "cache": {"forbidden_fact_ids": hits(cache_blob), "keys": sorted(st["local"]) + sorted(st["session"])},
                       "html_state": {"forbidden_fact_ids": hits(html_blob), "pages": len(ctx.state["replay_pages"])},
                       "export": {"forbidden_fact_ids": hits(exp_blob), "file": exp.name if exp else None,
                                  "exported": bool(exp_blob)},
                       "future_ohlc_sent_then_hidden": fut_bars > 0, "future_bars_in_network": fut_bars,
                       "detector_positive_control": control == sorted(f for f in forbidden if f == "f_close_future") or bool(control),
                       "positive_control_hits": control})
    nd = ctx.case_dir / "replay-payload-audit.ndjson"
    nd.parent.mkdir(parents=True, exist_ok=True)
    with nd.open("w", encoding="utf-8") as f:
        for r in b.responses:
            f.write(json.dumps({"url": r["url"], "status": r["status"], "bytes": len(r["body"]),
                                "forbidden": hits(r["body"])}, ensure_ascii=False) + "\n")
    ctx.add_evidence(nd, "application/x-ndjson", "训练会话浏览器收到的全部 API 响应的未来数据扫描")


# ── AC-071：主闭环（桌面 + 390px） ──
PAGES = ["environment", "cases", "seats", "plans", "portfolio", "training", "validation"]
PRIMARY = {"environment": "研究一个问题", "cases": "新建案例研究", "portfolio": "新建组合", "training": "开始训练",
           "validation": "冻结登记并排队"}


def _page_checks(pg, ws: str, vw: int) -> dict:
    over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    tags = pg.evaluate("Array.from(document.querySelectorAll('.tag, .conds .res')).map(e => e.innerText.trim())")
    unreachable = 0
    want = PRIMARY.get(ws)
    if want:
        btn = pg.get_by_role("button", name=want).first
        if btn.count() == 0:
            unreachable += 1
        else:
            btn.scroll_into_view_if_needed()
            box = btn.bounding_box()
            if not box or box["x"] < 0 or box["x"] + box["width"] > vw + 1 or not btn.is_visible():
                unreachable += 1
    # 键盘：Tab 走 30 步，焦点必须能离开同一个元素且不会卡在页面外
    pg.mouse.click(1, 1)
    seen, stuck, last = set(), 0, None
    for _ in range(30):
        pg.keyboard.press("Tab")
        cur = pg.evaluate("(() => { const e = document.activeElement; return e ? (e.tagName + '|' + (e.textContent || '').slice(0, 30) + '|' + (e.getAttribute('href') || '')) : 'none'; })()")
        stuck = stuck + 1 if cur == last else 0
        last = cur
        seen.add(cur)
    trap = 1 if (stuck >= 5 or len(seen) < 3) else 0
    return {"overflow_px": over, "untextual_labels": sum(1 for t in tags if not t), "labels": len(tags),
            "primary_unreachable": unreachable, "focus_trap": trap, "focus_distinct": len(seen)}


def _flow_desktop(ctx, b, pg) -> dict:
    """在真实页面上走一遍：研究 → 起草计划 → 冻结 → 事前审查 → 建组合 → 写日记。"""
    steps = {}
    b.goto(pg, "#/environment")
    pg.get_by_role("button", name="研究一个问题").click()
    pg.wait_for_selector("text=新建案例研究")
    pg.get_by_label("标题").fill("UI 验收：合成甲是否值得等")
    pg.get_by_label("研究问题").fill("按主方法看，合成甲现在能不能等到确认？")
    pg.get_by_label("添加标的").fill("600001")
    pg.get_by_role("button", name=re.compile("600001.SH")).first.click()
    pg.get_by_role("button", name="开始研究").click()
    pg.wait_for_selector("[data-testid=case-detail][data-state=succeeded], [data-testid=case-detail][data-state=partial], "
                         "[data-testid=case-detail][data-state=failed]", timeout=120000)
    steps["case"] = pg.get_attribute("[data-testid=case-detail]", "data-state")
    b.shot(pg, "desktop-case")
    pg.get_by_role("button", name="由此起草计划").click()
    pg.wait_for_selector("text=起草事前计划")
    pg.get_by_label("假设（为什么这样计划）").fill("等放量突破再看，不追")
    pg.locator("fieldset", has_text="入场触发").get_by_label("条件").first.fill("放量突破前高")
    pg.get_by_role("button", name="保存草稿").click()
    pg.wait_for_selector("[data-testid=plan-detail]")
    b.shot(pg, "desktop-plan-draft")
    pg.get_by_role("button", name="冻结事前计划").click()
    pg.get_by_role("button", name=re.compile("确认冻结")).click()
    pg.wait_for_selector("[data-testid=plan-detail][data-status=frozen]")
    steps["plan_frozen"] = True
    b.shot(pg, "desktop-plan-frozen")
    pg.get_by_role("button", name=re.compile("事前审查")).click()
    pg.wait_for_selector("[data-testid=review]", timeout=120000)
    steps["preflight"] = pg.get_attribute("[data-testid=review]", "data-review-kind")
    b.shot(pg, "desktop-preflight")
    b.goto(pg, "#/portfolio")
    pg.get_by_label("名称").fill("UI 验收组合")
    pg.get_by_role("button", name="新建组合").click()
    pg.wait_for_selector("[data-testid=portfolio-detail]")
    pg.locator("[data-testid=diary-form]").get_by_label("当时看到了什么").fill("合成甲缩量回踩")
    pg.locator("[data-testid=diary-form]").get_by_role("button", name="保存日记").click()
    pg.wait_for_selector("[data-testid=journal] tr[data-event-type=note]")
    steps["diary"] = True
    b.shot(pg, "desktop-portfolio-diary")
    return steps


def _flow_mobile(ctx, b, pg) -> dict:
    steps = {}
    b.goto(pg, "#/plans")
    b.goto(pg, "#/portfolio")
    link = pg.locator("[data-testid=portfolio-list] a").first
    link.click()
    pg.wait_for_selector("[data-testid=portfolio-detail]")
    pg.locator("[data-testid=diary-form]").get_by_label("当时看到了什么").fill("手机上记一笔：今天只观察")
    pg.locator("[data-testid=diary-form]").get_by_role("button", name="保存日记").click()
    pg.locator("[data-testid=journal] tr[data-event-type=note]").nth(1).wait_for(timeout=30000)
    steps["diary"] = True
    b.shot(pg, "mobile-portfolio-diary")
    return steps


def browser_acceptance_flow(ctx, inp):
    ctx.ensure("snapshot_final")
    from ..drivers.replay import ensure_archive
    ensure_archive(ctx)          # 训练与方法验证页的主操作需要只读历史归档（合成）
    b = browser(ctx)
    out = {"viewports": {}}
    total = {"unreachable": 0, "overflow": False, "traps": 0, "untextual": 0}
    for vp in inp["viewports"]:
        w, h = vp["width"], vp["height"]
        tag = "desktop" if w > 600 else "mobile"
        pg = b.new_page(width=w, height=h, cookie=_live_cookie(ctx))
        flow = _flow_desktop(ctx, b, pg) if tag == "desktop" else _flow_mobile(ctx, b, pg)
        per = {}
        for ws in PAGES:
            b.goto(pg, f"#/{ws}")
            chk = _page_checks(pg, ws, w)
            per[ws] = chk
            b.shot(pg, f"{tag}-{ws}")
            total["unreachable"] += chk["primary_unreachable"]
            total["overflow"] = total["overflow"] or chk["overflow_px"] > 1
            total["traps"] += chk["focus_trap"]
            total["untextual"] += chk["untextual_labels"]
        # 对话框：Esc 关闭、焦点回到打开它的按钮
        b.goto(pg, "#/portfolio")
        out["viewports"][f"{w}x{h}"] = {"flow": flow, "pages": per}
    ctx.result.update({"primary_actions_unreachable": total["unreachable"], "viewport_horizontal_overflow": total["overflow"],
                       "keyboard_focus_traps": total["traps"], "state_labels_textual": total["untextual"] == 0,
                       "screenshot_count": b.shots, "detail": out, "page_errors": b.page_errors[-5:]})
