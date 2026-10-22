#!/usr/bin/env python3
"""圆桌决策面板 · 实时刷新管道

把面板从「手拼的静态页」变成「数据驱动的重绘页」。

用法：
    python3 refresh.py --once                 # 刷新一次
    python3 refresh.py --serve 8765           # 起本地服务，每 5 分钟自动重建
    python3 refresh.py --serve 8765 --interval 120

依赖：
    猫爪 meoz CLI（默认 /Users/zhengchenggong/Downloads/meoz-2/scripts/meoz.py）
    可用环境变量 MEOZ_CLI 覆盖

设计原则：
    1. 只读猫爪接口，不写任何交易系统。
    2. 数据新鲜度写在页面顶部；取不到的字段保留上一次的值并标 stale，不静默沿用。
    3. 面板是分钟级刷新，不是逐笔。猫爪的历史接口是日级，实时接口在 sz/sh 节点。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from .services.feature_rows import CachedDailyRows, write_feature_rows
from .services.workload import bounded_memory
import hashlib

HERE = Path(__file__).resolve().parent
PANEL = HERE.parent / "圆桌决策面板_预览.html"
# Generated output belongs in runtime storage; panel/data is a read-only
# fixture tree.  The host installs the shared NumCat hook at process startup.
DATA = Path(os.environ.get("ROUNDTABLE_RUNTIME") or (HERE.parents[3] / "runtime")).expanduser() / "legacy_data"
DATA.mkdir(parents=True, exist_ok=True)
CACHE = DATA / "cache.json"

MEOZ = os.environ.get("MEOZ_CLI",
                      "/Users/zhengchenggong/Downloads/meoz-2/scripts/meoz.py")
ASSET_ROOT = Path(os.environ.get("ROUNDTABLE_ASSET_ROOT") or HERE.parents[3]).expanduser()
SKILLS = ASSET_ROOT / "skills"

INDICES = [("000001.SH", "上证指数"), ("399001.SZ", "深证成指"),
           ("399006.SZ", "创业板指"), ("000300.SH", "沪深300"), ("000852.SH", "中证1000")]


# ────────────────────────── 取数 ──────────────────────────
def meoz(op, apiname, fields, params):
    try:
        from panel.adapters.shared_hooks import shared_numcat
        query_sync, available = shared_numcat()
    except ImportError:
        query_sync, available = None, None
    if query_sync is not None:
        if available is not None and not available():
            raise RuntimeError("宿主 NumCat 数据源不可用")
        envelope = query_sync(apiname, dict(params), fields)
        data = (envelope or {}).get("data") or {}
        fs = data.get("fields") or []
        items = data.get("items") or data.get("rows") or []
        return [dict(zip(fs, row)) if isinstance(row, (list, tuple)) else dict(row) for row in items]
    req = DATA / "_req.json"
    req.write_text(json.dumps({"apiname": apiname, "fields": fields, "params": params},
                              ensure_ascii=False))
    p = subprocess.run([sys.executable, MEOZ, "query", "--operation-id", op,
                        "--request-file", str(req)], capture_output=True, text=True)
    try:
        d = json.loads(p.stdout)
    except Exception:
        raise RuntimeError(f"{op} 返回非 JSON：{p.stdout[:200]} {p.stderr[:200]}")
    r = d.get("results")
    if not r:
        raise RuntimeError(f"{op} 失败：{json.dumps(d, ensure_ascii=False)[:300]}")
    dd = r[0]["data"]
    return [dict(zip(dd["fields"], row)) for row in dd["items"]]


def fetch_indices(asof=None):
    params = {"symbols": [c for c, _ in INDICES]}
    if asof:
        params["tradedate"] = asof
    rows = meoz("reference_index_indexdaily", "indexdaily",
                "tradedate,symbol,name,close,pre_close,pct_chg", params)
    return rows



def trim_partial(feats):
    """盘中刷新时接口会把当天未收盘的快照一起返回，成交额中位数接近 0。

    拿这种数据去跑席位，全市场宽度、成交额过滤、涨停家数全是错的。
    这里用「成交额≥1 亿的家数」做覆盖率判断，最后一天明显不完整就丢掉。
    """
    dates = sorted({str(r["tradedate"]) for r in feats})
    if len(dates) < 2:
        return feats, None

    def cover(d):
        return sum(1 for r in feats
                   if str(r["tradedate"]) == d and (r.get("amount") or 0) >= 1e8)

    last, prev = dates[-1], dates[-2]
    if cover(last) < 0.6 * max(1, cover(prev)):
        filtered = feats.without_day(last) if isinstance(feats, CachedDailyRows) else [r for r in feats if str(r["tradedate"]) != last]
        return filtered, last
    return feats, None


def _write_calendar(days):
    """把交易日历落盘，供 panel/services/trade_calendar.py 读取（DATA-002）。"""
    try:
        if str(HERE.parent) not in sys.path:
            sys.path.insert(0, str(HERE.parent))
        from panel.services import trade_calendar
        trade_calendar.write_cache(days, path=DATA / "trade_calendar.json")
    except Exception as ex:            # 日历写不进不该阻断主流程
        print("交易日历缓存写入失败:", ex, flush=True)


def _cache_usable(f, day):
    """历史日的缓存永久可用；**当天**的缓存只有在收盘后写入才可用。

    盘中抓到的当天日线是半天的数据，成交额只有全天的一小半；如果直接当成缓存复用，
    之后每次刷新都会拿到这份残缺数据，panel 会一直显示「落后一个交易日」。
    """
    if day != datetime.now().strftime("%Y%m%d"):
        return True
    mt = datetime.fromtimestamp(f.stat().st_mtime)
    return (mt.hour, mt.minute) >= (15, 5)


def fetch_daily(days=70, progress=None):
    """逐日取全市场日线，带本地缓存。只补缺的日期。"""
    import concurrent.futures as cf
    from datetime import date, timedelta

    try:
        cal = meoz("reference_calendar_range", "tradecal", "",
                   {"startdate": (date.today() - timedelta(days=days * 2 + 40)).strftime("%Y%m%d"),
                    "enddate": date.today().strftime("%Y%m%d")})
        _write_calendar([r["cal_date"] for r in cal])
        dates = [r["cal_date"] for r in cal][-days:]
    except RuntimeError as ex:
        # 数据源连不上时用本地日线缓存的日期重建（只补缺日期；缺的日子会跳过并在下面报出来）
        cached = sorted(p.stem for p in (DATA / "daily").glob("*.json") if p.stem.isdigit())
        if not cached:
            raise
        dates = cached[-days:]
        print(f"交易日历取数失败，改用本地日线缓存的 {len(dates)} 个交易日（{dates[0]}–{dates[-1]}）：{str(ex)[:100]}")
    cache = DATA / "daily"
    cache.mkdir(exist_ok=True)

    def one(d):
        f = cache / f"{d}.json"
        if f.exists() and _cache_usable(f, d):
            with f.open(encoding="utf-8") as source:
                cached = json.load(source)
            rows = [r for r in cached if str(r.get("tradedate", "")).replace("-", "")[:8] == str(d).replace("-", "")[:8]]
            if len(rows) != len(cached):
                write_feature_rows(f, rows)
            return f, len(rows)
        try:
            rows = meoz("reference_stock_daily", "daily",
                        "tradedate,symbol,name,open,high,low,close,pre_close,pct_chg,amount,vol",
                        {"tradedate": d})
        except Exception:
            return f, 0
        rows = [r for r in rows if str(r.get("tradedate", "")).replace("-", "")[:8] == str(d).replace("-", "")[:8]]
        if rows:
            write_feature_rows(f, rows)
        return f, len(rows)

    files = []
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for done, (path, count) in enumerate(ex.map(one, dates), 1):
            if count:
                files.append((path, count))
            if progress and (done % 10 == 0 or done == len(dates)):
                progress(f"日线已同步 {done}/{len(dates)} 个交易日，可用 {len(files)} 日")
    return CachedDailyRows(files), dates[-1]


def fetch_valuation(asof):
    f = DATA / "valuation.json"
    try:
        rows = meoz("reference_stock_valuation", "valuation",
                    "tradedate,symbol,name,close,pe_ttm,pb,dv_ttm,total_mv",
                    {"tradedate": asof})
        f.write_text(json.dumps(rows, ensure_ascii=False))
        return f
    except Exception:
        return f if f.exists() else None


def fetch_limit_events(asof):
    """涨停事件流 → 每只票的首封时间、开板次数、封单额、连板数、换手率。

    事件编号：1=冲击涨停、2=封板、3/4=开板与回封类事件。这里不猜编号语义，
    只用「最早出现的 2 类事件」当首封时间，用最后一行的累计字段当终值。
    """
    try:
        rows = meoz("reference_stock_limit_event_v2_history", "limit_event_v2_history",
                    "tradedate,symbol,name,servertime,event,fd_amount,limit_times,open_times,turnover_rate",
                    {"tradedate": asof, "event_types": [1, 2, 3, 4]})
    except Exception as ex:
        f = DATA / "limit_events.json"
        if f.exists():
            print("涨停事件流取数失败，沿用上一次缓存:", str(ex)[:100])
            return f
        print("涨停事件流取数失败:", ex)
        return None
    agg = {}
    for r in rows:
        code = str(r.get("symbol") or "").split(".")[0].zfill(6)
        if not code:
            continue
        cur = agg.setdefault(code, {"first_seal": None, "open_times": None,
                                    "fd_amount": None, "limit_times": None, "turnover": None})
        t = r.get("servertime")
        if int(r.get("event") or 0) == 2 and t and (cur["first_seal"] is None or t < cur["first_seal"]):
            cur["first_seal"] = t
        # 事件按时间升序返回，最后一个非空值即当日终值
        if r.get("open_times") is not None:
            cur["open_times"] = int(r["open_times"])
        if r.get("fd_amount"):
            try: cur["fd_amount"] = float(r["fd_amount"])
            except (TypeError, ValueError): pass
        if r.get("limit_times") is not None:
            cur["limit_times"] = int(r["limit_times"])
        if r.get("turnover_rate"):
            try: cur["turnover"] = float(r["turnover_rate"])
            except (TypeError, ValueError): pass
    f = DATA / "limit_events.json"
    f.write_text(json.dumps(agg, ensure_ascii=False))
    return f


def write_req(apiname, fields, params):
    p = DATA / f"_req_{apiname}.json"
    p.write_text(json.dumps({"apiname": apiname, "fields": fields, "params": params},
                            ensure_ascii=False))
    return p


def build_limit_pool(feats, asof):
    """从日线还原涨停池（缺 first_time / open_times / fd_amount，置 None）。"""
    from decimal import Decimal, ROUND_HALF_UP
    def hup(x):
        return float(Decimal(repr(float(x))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    rows = [r for r in feats if r["tradedate"] == asof]
    out = []
    for r in rows:
        pc, hi, cl = r.get("pre_close"), r.get("high"), r.get("close")
        if not pc or hi is None or cl is None or pc <= 0:
            continue
        sym = str(r["symbol"]).zfill(6)
        if sym.startswith(("300", "301")):
            ratio = 0.20
        elif "ST" in str(r.get("name", "")).upper():
            ratio = 0.05
        else:
            ratio = 0.10
        lp = hup(pc * (1 + ratio))
        typ = None
        if abs(cl - lp) < 0.0045 and abs(hi - lp) < 0.0045:
            typ = "u"
        elif abs(hi - lp) < 0.0045:
            typ = "ub"
        if typ:
            out.append({"tradedate": asof, "symbol": sym, "name": r.get("name", ""),
                        "type": typ, "limit_times": None, "first_time": None,
                        "open_times": None, "fd_amount": None,
                        "amount": r.get("amount"), "close": cl,
                        "pct_chg": r.get("pct_chg")})
    p = DATA / "limit_pool.json"
    p.write_text(json.dumps(out, ensure_ascii=False))
    return p


def _file_digest(path):
    if path is None or not Path(path).is_file():
        return "missing"
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@bounded_memory
def run_seat(name, features_path, industry_path=None, extra=None):
    if name == "trading-tuixue-v1":
        return None
    script_dir = SKILLS / name / "scripts"
    inputs = [features_path, industry_path, *((extra or {}).values()), *sorted(script_dir.glob("*.py"))]
    key = hashlib.sha256(json.dumps([name, *[_file_digest(p) for p in inputs]], sort_keys=True).encode()).hexdigest()
    cached = DATA / "seat_cache" / f"{key}.json"
    if cached.is_file():
        return json.loads(cached.read_text())
    result = _run_seat(name, features_path, industry_path, extra or {})
    if result is not None:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(result, ensure_ascii=False))
    return result


def _run_book_script(cmd):
    # Child interpreters do not inherit the host's sys.path. Make the bounded
    # reader available explicitly, as in the v1 runner, instead of silently
    # falling back to loading the entire market as JSON dictionaries.
    panel_parent = str(HERE.parent)
    launcher = (f"import runpy,sys;sys.path.insert(0,{panel_parent!r});"
                "p=sys.argv.pop(1);sys.argv[0]=p;runpy.run_path(p,run_name='__main__')")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "LANG": "C.UTF-8", "PYTHONHASHSEED": "0"}
    return subprocess.run([cmd[0], "-I", "-c", launcher, *cmd[1:]],
                          capture_output=True, text=True, timeout=300, env=env)


def _run_seat(name, features_path, industry_path=None, extra=None):
    script = SKILLS / name / "scripts" / ("review.py" if name == "trading-tuixue-v1" else "screen.py")
    out = DATA / f"seat_{name}.json"
    output_key = name.split("-")[1]
    output_path = DATA / ("screen.json" if name == "trading-wildman-v2" else f"{output_key}.json")
    output_path.unlink(missing_ok=True)
    cmd = [sys.executable, str(script)]
    if name == "trading-tuixue-v1":
        return None      # 审查席不参与刷新
    if name == "trading-wildman-v2":
        lp = extra.get("limit_pool_path")
        if not lp:
            return None
        # 野人哥要两步：先 build_features 生成 compact，再 screen
        compact = DATA / "features_compact.json"
        compact.unlink(missing_ok=True)
        bf = SKILLS / name / "scripts" / "build_features.py"
        b = _run_book_script([sys.executable, str(bf), "--daily", str(features_path),
                            "--out", str(compact)] +
                           (["--industry", str(industry_path)] if industry_path else []))
        if b.returncode != 0 or not compact.exists():
            print(f"  ! {name} build_features 失败: {(b.stderr or b.stdout)[:160]}")
            return None
        cmd += ["--features", str(compact), "--limit-pool", str(lp)]
    else:
        cmd += ["--features", str(features_path)]
    if industry_path and name in ("trading-jianmen-v1", "trading-yangnan-v1",
                                  "trading-gushiguifei-v1", "trading-livermore-v1",
                                  "trading-lidaxiao-v1"):
        cmd += ["--industry", str(industry_path)]
    if name == "trading-boardtech-v1":
        lp = extra.get("limit_pool_path")
        if not lp:
            return None
        cmd += ["--limit-pool", str(lp)]
        le = extra.get("limit_events_path")
        if le and Path(le).exists():
            cmd += ["--limit-events", str(le)]
        cmd += ["--top", "20"]
        cmd += ["--out", str(DATA)]
        p_ = _run_book_script(cmd)
        f_ = DATA / "boardtech.json"
        if p_.returncode != 0 or not f_.exists():
            print(f"  ! {name} 无输出: {(p_.stderr or p_.stdout)[:160]}")
            return None
        return json.loads(f_.read_text())
    if name == "trading-lidaxiao-v1":
        val = extra.get("valuation_path")
        if not val:
            return None
        cmd += ["--valuation", str(val), "--top", "30"]
    if name == "trading-darwin-v1":
        val = extra.get("valuation_path")
        if not val:
            return None
        cmd = [sys.executable, str(script), "--valuation", str(val)]
        dv = extra.get("dividend_path")
        if dv:
            cmd += ["--dividend-years", str(dv)]
        cmd += ["--top", "50"]
    cmd += ["--out", str(DATA)]
    p = _run_book_script(cmd)
    key = name.split("-")[1]
    f = DATA / ("screen.json" if name == "trading-wildman-v2" else f"{key}.json")
    if name == "trading-wildman-v2" and p.returncode == 0 and f.exists():
        raw = json.loads(f.read_text())
        st = raw.get("stage") or {}
        label = str(st.get("stage") or "")
        allow = any(k in label for k in ("启动", "发酵", "主升", "修复"))
        return {"seat": name,
                "gate": {"open": allow, "state": label or "未判定",
                         "reason": "；".join(st.get("evidence") or [])[:120]},
                "candidates": [],
                "note": f"周期定位与角色标注已输出 {len(raw.get('roles') or [])} 只；买点需按 §6.1–6.5 逐条核，"
                        f"脚本不直接出票。"}
    if p.returncode != 0 or not f.exists():
        err = (p.stderr or p.stdout or "").strip().splitlines()[-1:] or ["未知错误"]
        print(f"  ! {name} 失败: {err[0][:160]}")
        return {"seat": name, "gate": {"open": False, "state": "取数失败",
                                       "reason": err[0][:120]}, "candidates": []}
    return json.loads(f.read_text())


# ────────────────────────── 渲染 ──────────────────────────
UP, DN = "var(--up)", "var(--dn)"


def fmt(n):
    return f"{n:,}"


def render_idx(idx_rows, asof, stale=False):
    by = {r["symbol"]: r for r in idx_rows}
    items = []
    for code, label in INDICES:
        r = by.get(code)
        if not r:
            continue
        pct = r.get("pct_chg") or 0
        chg = (r.get("close") or 0) - (r.get("pre_close") or 0)
        cls = "up" if pct >= 0 else "dn"
        sign = "+" if pct >= 0 else "−"
        items.append(
            f'    <div class="it"><span class="in">{label}</span>'
            f'<span class="iv">{fmt(round(r["close"], 2))}</span>'
            f'<span class="ic {cls}">{sign}{abs(chg):.2f}</span>'
            f'<span class="ip {cls}">{sign}{abs(pct):.2f}%</span></div>')
    snap = "刷新失败，显示上次数据" if stale else f"快照 {asof[:4]}-{asof[4:6]}-{asof[6:]}"
    return ('<div class="idxbar">\n  <div class="iw">\n' + "\n".join(items) +
            f'\n    <span class="snap"><span class="dot"></span>{snap}</span>\n  </div>\n</div>')


SEAT_META = [
    ("trading-wildman-v2", "野人哥 · 保住本金", "周期 → 主线 → 角色 → 次日预期 → 买点"),
    ("trading-jianmen-v1", "剑门", "生态三档 + 地位排序"),
    ("trading-yangnan-v1", "杨楠 · 情绪流龙头", "四指标 + 两次冰点"),
    ("trading-yangjia-v1", "炒股养家", "赚钱效应四指标"),
    ("trading-tuixue-v1", "退学炒股", "模式四要件审查，不选股"),
    ("trading-gushiguifei-v1", "一路奔行 · 股是股非", "A／B／C 区域 + 量能"),
    ("trading-wyckoff-v1", "威科夫 · 供求与量价事件", "区间内不交易，等 SOS 或 SOW"),
    ("trading-candle-v1", "蜡烛图 · 形态确认", "不选股，只确认"),
    ("trading-livermore-v1", "利弗莫尔 · 关键点", "只在关键点动手，只做领头羊"),
    ("trading-darwin-v1", "达尔文 · 长期价值", "避风险 → 高质量 → 合理价格 → 永久持有"),
    ("trading-boardtech-v1", "明发 · 打板技术", "确定性 → 有预期 → 上升趋势 → 封板质量"),
    ("trading-chenkai-v1", "陈凯 · 交易之路", "多周期 → 背离 → 情绪 → 纪律"),
    ("trading-lidaxiao-v1", "李大霄 · 估值为锚", "市场估值定位 → 底部区域 → 蓝筹龙头"),
]
PILL = {"开": "go", "关": "off", "停": "stop"}


def render_seats(results):
    cards = []
    for key, title, blurb in SEAT_META:
        if key == "trading-tuixue-v1":
            cards.append(f'''      <div class="seat">
        <div class="top"><span class="nm">{title}</span><span class="pill wait">审查席</span></div>
        <div class="sys">{blurb}。它不产生候选，只在其他席位出票后做最后一道拦截。</div>
        <div class="foot"><code>{key}</code><span class="cand">待审</span></div>
      </div>''')
            continue
        r = results.get(key)
        if r is None:
            cards.append(f'''      <div class="seat dim">
        <div class="top"><span class="nm">{title}</span><span class="pill off">未接入</span></div>
        <div class="sys">{blurb}</div>
        <div class="foot"><code>{key}</code><span class="cand">—</span></div>
      </div>''')
            continue
        g = r.get("gate", {})
        op = bool(g.get("open"))
        n = len(r.get("candidates") or [])
        state = str(g.get("state") or ("开" if op else "关"))[:14]
        reason = str(g.get("reason") or "")[:110]
        dim = "" if op else " dim"
        cards.append(f'''      <div class="seat{dim}">
        <div class="top"><span class="nm">{title}</span><span class="pill {"go" if op else "off"}">{state}</span></div>
        <div class="sys">{blurb}。{reason}</div>
        <div class="foot"><code>{key}</code><span class="cand">候选 <b>{n}</b></span></div>
      </div>''')
    return '    <div class="seats">\n' + "\n\n".join(cards) + "\n    </div>"


def render_pools(results):
    def cnt(keys):
        # 只统计开闸席位的候选；关闸席位即使有排序榜也不计入池
        return sum(len(results.get(k, {}).get("candidates") or [])
                   for k in keys
                   if k in results and results[k].get("gate", {}).get("open"))
    short = cnt(["trading-jianmen-v1", "trading-yangnan-v1", "trading-yangjia-v1",
                 "trading-livermore-v1"])
    swing = cnt(["trading-gushiguifei-v1", "trading-wyckoff-v1"])
    long_ = cnt(["trading-darwin-v1"])
    return f'''    <div class="pools">
      <div class="pool" data-pool="short">
        <div class="pt">短期池 <span class="ph">1–3 天</span></div>
        <div class="pn">{short}</div>
        <div class="ps">剑门 · 杨楠 · 养家 · 利弗莫尔（野人哥的 C 类买点也归这里）</div>
        <div class="pd">由开闸席位的候选自动汇总。</div>
      </div>
      <div class="pool{"" if swing else " empty"}" data-pool="swing">
        <div class="pt">波段池 <span class="ph">3–15 天</span></div>
        <div class="pn">{swing if swing else "—"}</div>
        <div class="ps">一路奔行 · 威科夫（野人哥的 A／B／D 归这里）</div>
        <div class="pd">由开闸席位的候选自动汇总。</div>
      </div>
      <div class="pool{"" if long_ else " empty"}" data-pool="value">
        <div class="pt">长期价值池 <span class="ph">季度以上</span></div>
        <div class="pn">{long_ if long_ else "—"}</div>
        <div class="ps">达尔文（Prasad 三步法）</div>
        <div class="pd">财务接口无权限时用「连续分红＋高股息」代理质量。</div>
      </div>
    </div>
    <div class="ptabs">
      <button class="on" data-f="all">全部 <span>{short+swing+long_}</span></button>
      <button data-f="short">短期 <span>{short}</span></button>
      <button data-f="swing">波段 <span>{swing}</span></button>
      <button data-f="value">长期价值 <span>{long_}</span></button>
    </div>'''






# ────────────────────────── 详情与规则 ──────────────────────────
FLOW_CACHE = DATA / "flow_cache.json"


def fetch_flow(symbols, start, end):
    """一次取多只票的主力资金。成功时把结果写进本地缓存；失败时用缓存里已有的日期。

    缓存按 symbol → {交易日: 主力净额} 存，和日线缓存一样只是「上次取到的原始值」，
    不参与任何推断；没有缓存的票在详情里显示「无资金数据」。
    """
    if not symbols:
        return {}
    cache = {}
    if FLOW_CACHE.exists():
        try:
            cache = json.loads(FLOW_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    try:
        rows = meoz("reference_theme_fundflow-kp", "fundflow_kp",
                    "tradedate,symbol,main_net_amount",
                    {"symbols": symbols[:80], "startdate": start, "enddate": end})
        out = {}
        for x in rows:
            out.setdefault(str(x["symbol"]).zfill(6), {})[x["tradedate"]] = x["main_net_amount"]
        for sym, m in out.items():
            cache.setdefault(sym, {}).update(m)
        FLOW_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        return out
    except Exception as ex:
        hit = sum(1 for sym in symbols if sym in cache)
        if hit:
            print(f"主力资金取数失败，用本地缓存（{hit}/{len(symbols)} 只有缓存）:", str(ex)[:100])
        return {sym: {d: v for d, v in cache.get(sym, {}).items() if start <= d <= end}
                for sym in symbols if sym in cache}


def build_detail(feats, symbols, flow, asof):
    """为每只候选生成 K 线、量价状态与阶段判定。"""
    import statistics as stt
    by = {}
    wanted = {str(s).zfill(6) for s in symbols}
    for r in feats:
        symbol = str(r["symbol"]).zfill(6)
        if symbol in wanted:
            by.setdefault(symbol, []).append(r)
    out = {}
    for sym in symbols:
        rs = sorted(by.get(sym, []), key=lambda x: x["tradedate"])[-60:]
        if len(rs) < 30:
            continue
        c = [x["close"] for x in rs]; h = [x["high"] for x in rs]
        l = [x["low"] for x in rs]; o = [x["open"] for x in rs]
        v = [x.get("vol") or 0 for x in rs]
        n = len(rs)
        ma5 = [None]*n; ma10 = [None]*n; ma20 = [None]*n
        for w, arr in ((5, ma5), (10, ma10), (20, ma20)):
            for i in range(w-1, n):
                arr[i] = round(sum(c[i-w+1:i+1])/w, 2)
        vma5 = [None]*n; vma20 = [None]*n
        for w, arr in ((5, vma5), (20, vma20)):
            for i in range(w, n):
                arr[i] = sum(v[i-w:i])/w if w == 5 else sum(v[i-w+1:i+1])/w
        dates = [x["tradedate"] for x in rs]
        fl = flow.get(sym, {})
        fv = [fl.get(d) for d in dates]
        K = [{"d": dates[i], "o": round(o[i],2), "h": round(h[i],2), "l": round(l[i],2),
              "c": round(c[i],2), "m5": ma5[i], "m20": ma20[i]} for i in range(n)]
        state = []
        for i in range(n):
            if vma5[i]:
                r_ = v[i]/vma5[i]
                up = rs[i]["pct_chg"] > 0
                if r_ >= 1.3: state.append("放量上涨" if up else "放量下跌")
                elif r_ <= 0.8: state.append("缩量上涨" if up else "缩量下跌")
                else: state.append("平量上涨" if up else "平量下跌")
            else:
                state.append("-")
        i = n-1
        close = c[i]; m20 = ma20[i] or close; m10 = ma10[i] or close
        ret5 = (close/c[i-5]-1)*100 if i >= 5 else None
        ret20 = (close/c[i-20]-1)*100 if i >= 20 else None
        dev20 = (close/m20-1)*100
        vr = (v[i]/vma20[i]) if vma20[i] else None
        hh20 = max(h[i-19:i+1]); ll20 = min(l[i-19:i+1])
        amp20 = (hh20/ll20-1)*100 if ll20 else 0
        flow5 = sum(x for x in fv[-5:] if x)
        flow20 = sum(x for x in fv[-20:] if x)
        up_days = sum(1 for k in range(max(0, i-9), i+1) if rs[k]["pct_chg"] > 0)
        stage = "震荡"; why = []
        if dev20 > 12 and ret5 and ret5 > 15:
            stage = "加速"; why.append(f"5日涨 {ret5:.1f}%，偏离 MA20 {dev20:.1f}%")
        elif close > m20 and (ma5[i] or 0) > m20 and vr and vr > 1.2 and hh20 >= max(h[i-19:i+1]):
            stage = "启动"; why.append("站上 MA20 且 5 日线在上方，放量创 20 日新高")
        elif close < m10 and (ma5[i] or 0) < m10:
            stage = "衰退"; why.append("跌破 MA10 且 5 日线在 10 日线下方")
        elif vr and vr > 1.5 and abs(rs[i]["pct_chg"]) < 3:
            stage = "分歧"; why.append(f"放量 {vr:.1f} 倍但涨幅仅 {rs[i]['pct_chg']:.1f}%")
        elif abs(dev20) < 6 and amp20 < 18 and vr and vr < 0.95:
            stage = "蓄势"; why.append(f"贴近 MA20（偏离 {dev20:.1f}%），20 日振幅 {amp20:.1f}%")
        else:
            why.append("均线纠缠，无明确方向")
        if flow5 > 0: why.append(f"近5日主力净流入 {flow5/1e8:+.2f} 亿")
        elif flow5 < 0: why.append(f"近5日主力净流出 {flow5/1e8:+.2f} 亿")
        out[sym] = {"name": rs[-1].get("name",""), "dates": dates, "k": K, "v": v,
                    "state": state, "flow": fv, "stage": stage, "why": why,
                    "stats": {"ret5": None if ret5 is None else round(ret5,1),
                              "ret20": None if ret20 is None else round(ret20,1),
                              "dev20": round(dev20,1),
                              "vr": None if vr is None else round(vr,2),
                              "amp20": round(amp20,1),
                              "flow5": round(flow5/1e8,2), "flow20": round(flow20/1e8,2),
                              "dist_hh": round((close/hh20-1)*100,1),
                              "ma_align": "多头" if (ma5[i] or 0) > m20 else "空头",
                              "up_days10": up_days}}
    return out


def build_rules(detail):
    """由 K 线生成入场/离场价位。

    止损口径（本技能设定，非书里给出）：
      · ATR(14) 度量该股自己的日常波动，止损距离以 ATR 为单位，不用固定百分比
      · 结构性防守位：近5日最低 / MA10 / 启动阳线最低 / 近20日箱体下沿
      · 取「距现价 ≥ MIN_ATR 倍 ATR」的最高支撑位——太近会被日常噪声打掉
      · 若最远的支撑位仍不到 MIN_ATR，改用「现价 − MIN_ATR×ATR」这个纯波动止损
      · 超过 MAX_ATR 倍 ATR 或超过 MAX_PCT%，标记不建议入场
    """
    MIN_ATR, MAX_ATR, SANITY_PCT, MIN_PCT = 1.5, 4.0, 15.0, 1.0
    out = {}
    for sym, d in detail.items():
        k = d["k"]; n = len(k); i = n-1
        if n < 20:
            continue
        c = [x["c"] for x in k]; h = [x["h"] for x in k]; l = [x["l"] for x in k]
        vol = d["v"]
        close = c[i]
        ma5, ma20 = k[i]["m5"], k[i]["m20"]
        ma10 = round(sum(c[i-9:i+1])/10, 2) if n >= 10 else None

        # ATR(14)：真实波幅的均值
        trs = []
        for t in range(max(1, i-13), i+1):
            tr = max(h[t]-l[t], abs(h[t]-c[t-1]), abs(l[t]-c[t-1]))
            trs.append(tr)
        atr = sum(trs)/len(trs) if trs else (close*0.02)
        min_dist = MIN_ATR*atr

        hh20 = max(h[i-19:i+1])
        j = max(range(max(0, i-9), i+1), key=lambda x: vol[x])
        start_low = l[j]
        box_low = min(l[i-19:i+1])
        low5 = min(l[max(0, i-4):i+1])

        levels = sorted([x for x in (low5, ma10, start_low, box_low) if x and x < close],
                        reverse=True)
        stop, flag = None, "正常"
        for lv in levels:
            if close - lv >= min_dist:
                stop = lv
                break
        if stop is None:
            stop = close - min_dist
            flag = f"结构位过近，按 {MIN_ATR}×ATR 设定"
        dist = close - stop
        if dist > MAX_ATR*atr or dist/close*100 > SANITY_PCT:
            flag = "止损过远，不建议在此位置入场"
        elif dist < MIN_PCT/100*close:
            flag = "止损距离小于 1%，形态过紧"

        out[sym] = {"close": close, "ma5": ma5, "ma10": ma10, "ma20": ma20,
                    "hh20": round(hh20, 2),
                    "entry_pull": ma5, "entry_break": round(hh20 + 0.01, 2),
                    "stop": round(stop, 2), "start_low": round(start_low, 2),
                    "start_date": d["dates"][j],
                    "atr": round(atr, 2),
                    "atr_mult": round(dist/atr, 2) if atr else None,
                    "risk_pct": round(dist/close*100, 2),
                    "risk_flag": flag,
                    "tgt1": round(close+2*dist, 2), "tgt2": round(close+3*dist, 2),
                    "trail": ma10, "time_exit": "5 个交易日内不创新高则退出"}
    return out


def inject_detail(html, detail, rules):
    import re
    jd = json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
    jr = json.dumps(rules, ensure_ascii=False, separators=(",", ":"))
    html = re.sub(r'(<script>window\.DETAIL = ).*?(;</script>)',
                  lambda m: m.group(1)+jd+m.group(2), html, count=1, flags=re.S)
    html = re.sub(r'(<script>window\.RULES = ).*?(;</script>)',
                  lambda m: m.group(1)+jr+m.group(2), html, count=1, flags=re.S)
    return html




UNIVERSES = {"all": "沪深主板＋创业板", "main": "沪深主板"}
CHINEXT_PREFIXES = ("300", "301", "302")


def _not_in_sample(sym: str, universe: str = "all") -> bool:
    """样本口径之外的代码：科创板 688/689（689 是科创板存托凭证，如九号公司）、北交所 8xx/4xx、B 股 9xx；
    universe="main" 时再把创业板 300/301/302 排除（这就是界面上「仅主板 / 含创业板」开关）。"""
    s_ = str(sym).zfill(6)
    if s_.startswith(("688", "689", "8", "4", "9")):
        return True
    return universe == "main" and s_.startswith(CHINEXT_PREFIXES)


def sample_counts(feats, asof):
    """当日全量 → 各口径只数（全量、含创业板、仅主板），以及被排除的科创板/创业板/北交所B股。"""
    rows = [str(r["symbol"]).zfill(6) for r in feats if r["tradedate"] == asof]
    star = sum(1 for s_ in rows if s_.startswith(("688", "689")))
    other = sum(1 for s_ in rows if s_.startswith(("8", "4", "9")))
    chinext = sum(1 for s_ in rows if s_.startswith(CHINEXT_PREFIXES))
    return {"total": len(rows), "all": len(rows) - star - other, "main": len(rows) - star - other - chinext,
            "star": star, "chinext": chinext, "other": other}


def render_funnel(results, feats, asof, universe="all"):
    """量化终端风漏斗：SVG 立体漏斗 + 粒子流 + 点击看条件（数量按 universe 口径）。"""
    import math
    today = [r for r in feats if r["tradedate"] == asof and not _not_in_sample(r["symbol"], universe)]
    seen_days = {}
    for r in feats:
        sym = str(r["symbol"]).zfill(6)
        seen_days[sym] = seen_days.get(sym, 0) + 1
    pool = []
    for r in today:
        sym = str(r["symbol"]).zfill(6)
        nm = str(r.get("name", "")).upper()
        if "ST" in nm or "退" in nm or not r.get("close") or r["close"] < 2:
            continue
        if (r.get("amount") or 0) < 1e8 or seen_days.get(sym, 0) < 60:
            continue
        pool.append(sym)

    all_c, open_c, uniq = [], [], {}
    for k, v in results.items():
        cs = v.get("candidates") or []
        all_c.extend(cs)
        if v.get("gate", {}).get("open"):
            open_c.extend(cs)
            for c in cs:
                uniq.setdefault(str(c["symbol"]).zfill(6), c.get("name", ""))

    n1, n2 = len(today), len(pool)
    _c = sample_counts(feats, asof)
    n3 = len(all_c)   # 候选条目数（含跨席位重复）：与 L4 开闸条目、L5 去重后保持单调
    n4 = len(open_c)
    n5 = len(uniq)

    layers = [
        {"tag": "L1", "name": UNIVERSES[universe], "count": n1,
         "conds": [f"样本口径：{UNIVERSES[universe]}",
                   (f"不在样本：科创板 {_c['star']} 只、创业板 {_c['chinext']} 只、北交所/B 股 {_c['other']} 只"
                    if universe == "main" else
                    f"不在样本：科创板 {_c['star']} 只、北交所/B 股 {_c['other']} 只"),
                   f"当日全市场共 {_c['total']} 只；条形按对数宽度绘制"],
         "hint": "起点。所有后续层都在这个池子里做减法。"},
        {"tag": "L2", "name": "合格池", "count": n2,
         "conds": ["排除 ST / 退市风险", "股价 ≥ 2 元", "当日成交额 ≥ 1 亿元", "上市满 60 个交易日"],
         "hint": "这一层只回答「能不能买」，不判断好坏。"},
        {"tag": "L3", "name": "席位覆盖", "count": n3,
         "conds": ["被十三个席位中至少一个认领", "认领依据各自不同：形态、区域、事件、基本面"],
         "names": [v for v in list(uniq.values())[:40]] or sorted({c.get("name","") for c in all_c})[:40],
         "hint": "这一层是最大的一次收窄——绝大多数票没有任何席位认领。"},
        {"tag": "L4", "name": "过环境闸门", "count": n4,
         "conds": ["只留当日开闸席位的候选", "关闸席位的排序榜不计入"],
         "names": [c.get("name","") for c in open_c[:40]],
         "hint": "闸门由各席位自己判定，多数日子会关着。"},
        {"tag": "L5", "name": "去重研究候选", "count": n5,
         "conds": ["同一只票被多个席位选中只算一次", "进入下方候选池"],
         "names": list(uniq.values())[:40],
         "hint": "最终候选池。注意：回测显示这些闸门本身没有统计显著的超额收益。"},
    ]
    data = {"as_of": asof, "layers": layers}
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    drop23 = n2 - n3
    note = (f"L2→L3 是最大的一次收窄：{n2:,} 只里只有 {n3} 只被任何席位认领。"
            f"L3→L4 掉 {n3-n4} 只，说明今天开闸席位的覆盖面很广。L5 去重 {n4-n5} 只。")
    warn = ("今天开闸席位多，不代表信号变强——回测显示这些闸门本身没有统计显著的超额收益。"
            "漏斗的作用是把「有依据的几只」和「没依据的一堆」分开，不是预测涨跌。")
    return (f'<script>window.FUNNEL = {js};</script>\n'
            '    <div class="qf">\n'
            '      <div class="qf-stage">\n'
            '        <button class="qf-replay" id="qfreplay">重播</button>\n'
            '        <svg id="qfsvg" viewBox="0 0 440 556" role="img" aria-label="选股漏斗">\n'
            '          <defs><filter id="qfshadow" x="-20%" y="-20%" width="140%" height="140%">\n'
            '            <feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#1c1917" flood-opacity="0.16"/>\n'
            '          </filter></defs>\n'
            '        </svg>\n'
            '      </div>\n'
            '      <div class="qf-side" id="qfside"></div>\n'
            '    </div>\n'
            f'    <div class="fnf">{note}</div>\n    <div class="fnf warn">{warn}</div>')


def render_cands(results):
    rows = []
    pool_of = {"trading-jianmen-v1": "short", "trading-yangnan-v1": "short",
               "trading-yangjia-v1": "short", "trading-livermore-v1": "short",
               "trading-gushiguifei-v1": "swing", "trading-wyckoff-v1": "swing",
               "trading-darwin-v1": "value"}
    label = {"short": "短期", "swing": "波段", "value": "长期"}
    seen = set()
    for key, pool in pool_of.items():
        r = results.get(key)
        if not r or not r.get("gate", {}).get("open"):
            continue
        for c in (r.get("candidates") or [])[:12]:
            if c["symbol"] in seen:
                continue
            seen.add(c["symbol"])
            rows.append((pool, key, c))
    if not rows:
        return '      <tbody>\n        <tr><td colspan="4" style="text-align:center;color:var(--muted)">今日无席位开闸</td></tr>\n      </tbody>'
    out = []
    for pool, key, c in rows[:24]:
        seat = next((t for k, t, _ in SEAT_META if k == key), key)
        out.append(f'''        <tr data-pool="{pool}">
          <td><span class="nm2">{c.get("name","")}</span><br><span class="code">{c["symbol"]}</span></td>
          <td><span class="st{'' if pool!='swing' else ' hi'}">{label[pool]}</span><div class="lyr">{c.get("role","")}</div></td>
          <td><div class="seatTags"><span class="st hi">{seat.split(" · ")[0]}</span></div></td>
          <td>{c.get("confirms") or c.get("buy_point") or c.get("reason") or "见席位输出"}
            <div class="note2">{c.get("price_basis") or c.get("industry") or ""}</div></td>
        </tr>''')
    return '      <tbody>\n' + "\n".join(out) + '\n      </tbody>'








# 固定研究结果：668 个交易日（2023-10 ~ 2026-10）的席位择时回测。
# 这是历史结论，不随每日刷新变化；样本窗口与口径写在 backtest 字段里。
BACKTEST = {
    "status": "legacy_unverified",
    "status_note": "本页数字来自 2026-10-19 的一次手工脚本运行，未走 v1 的 ValidationRun 流程；样本、成本与统计口径见回测记录，结论仅供待复核。",
    "window": "2024-10-01 → 2026-10-16；短线席位 42 个采样日，价值席位 22 个月度快照",
    "baseline": -0.147,
    "note": ("短线席位：42 个采样日，T+1 开盘买入、T+5 收盘卖出；价值席位：月度快照，T+1 开盘买入、T+60 收盘卖出。"
             "都等权、扣双边 0.06%，基准是同一天全市场等权。这不是「13 席都做了同一个试验」：炒股养家在选股层面没有采样，"
             "杨楠 42 个采样日闸门一天没开，退学炒股与蜡烛图按定义不产候选。达尔文的「连续分红年数」只有当前一份，"
             "回测时套了历史快照，属前视污染——受污染的数字没有数学保证是上界，任何方向都不能拿来说事。"),
    "rows": [
        {
            "seat": "一路奔行",
            "days": 42,
            "avgCand": 25.2,
            "excess": 0.04,
            "win": 57.1,
            "t": 0.11
        },
        {
            "seat": "威科夫",
            "days": 39,
            "avgCand": 18.8,
            "excess": -0.17,
            "win": 35.9,
            "t": -0.55
        },
        {
            "seat": "陈凯",
            "days": 42,
            "avgCand": 29.7,
            "excess": -0.18,
            "win": 42.9,
            "t": -0.58
        },
        {
            "seat": "利弗莫尔",
            "days": 42,
            "avgCand": 28.9,
            "excess": -0.32,
            "win": 50.0,
            "t": -0.59
        },
        {
            "seat": "明发·阿荣（打板）",
            "days": 42,
            "avgCand": 29.1,
            "excess": -1.46,
            "win": 35.7,
            "t": -2.4
        },
        {
            "seat": "剑门",
            "days": 42,
            "avgCand": 30.0,
            "excess": -1.47,
            "win": 42.9,
            "t": -2.14,
            "gateOpenDays": 20,
            "excessGateFiltered": -1.88,
            "tGateFiltered": -1.99,
            "note": "42 个采样日里闸门只有 20 天开着；只算开闸日超额 -1.88%（t -1.99），比不筛更差——闸门对选股层没有帮助"
        },
        {
            "seat": "达尔文",
            "days": 22,
            "avgCand": 30.0,
            "excess": -1.74,
            "win": 36.0,
            "t": -1.16,
            "horizon": 60,
            "note": "用当前分红表套历史快照，有前视偏差（未来信息倒填历史）；受污染结果没有数学保证是上界，不可当真实业绩", "caveat_id": "lookahead_contaminated"
        },
        {
            "seat": "李大霄",
            "days": 11,
            "avgCand": 30.0,
            "excess": -4.36,
            "win": 9.0,
            "t": -3.97,
            "horizon": 60,
            "note": "只算开闸的 11 个快照；其余 11 个快照闸门关着，没有候选"
        }
    ],
    "gate": [
        {
            "seat": "剑门 · 生态三档",
            "days": 147,
            "excess": 0.52,
            "t": 1.93
        },
        {
            "seat": "炒股养家 · 赚钱效应",
            "days": 195,
            "excess": 0.18,
            "t": 0.7
        },
        {
            "seat": "杨楠 · 情绪冰点",
            "days": 7,
            "excess": -1.82,
            "t": -0.86
        }
    ],
    "years": [],
    "yearCols": [],
    "verdict": "八个可测席位里没有一个跑赢市场。李大霄 −4.36%（t=−3.97）和剑门 −1.47%、打板 −1.46% 是显著跑输，一路奔行 +0.04% 是唯一贴着 0 的。闸门层面是另一回事：剑门的「最高板≥6 且连板≥7」开闸后 5 天比关闸后好 0.52%，原始 t=1.93，按重叠折算后约 0.9，仍不显著。结论：这些席位的价值可能在「判断现在能不能做」，不在「挑哪只票」。"
}

CAND_META = {
    "trading-jianmen-v1": ("short", "短期"), "trading-yangnan-v1": ("short", "短期"),
    "trading-yangjia-v1": ("short", "短期"), "trading-livermore-v1": ("short", "短期"),
    "trading-gushiguifei-v1": ("swing", "波段"), "trading-wyckoff-v1": ("swing", "波段"),
    "trading-darwin-v1": ("value", "长期"),
    "trading-boardtech-v1": ("short", "短期"),
    "trading-chenkai-v1": ("swing", "波段"),
    "trading-lidaxiao-v1": ("value", "长期"),
}

# ────────────────────────── 前端接口（AGI QUANT 契约） ──────────────────────────
SEAT_API = [
    ("trading-wildman-v2", "wildman", "野人哥", "保住本金", ["周期与主线", "本金防守", "风控优先"], "wildman.webp"),
    ("trading-jianmen-v1", "jianmen", "剑门", "纪律择时", ["生态三档", "地位排序", "龙空龙"], "jianmen.webp"),
    ("trading-yangnan-v1", "yangnan", "杨楠", "情绪流龙头", ["情绪周期", "龙头挖掘", "冰点判断"], "yangnan.webp"),
    ("trading-yangjia-v1", "yangjia", "炒股养家", "市场理解", ["赚钱效应", "仓位递进", "弱转强"], "chaogu.webp"),
    ("trading-tuixue-v1", "tuixue", "退学炒股", "只做模式内", ["模式四要件", "执行纪律", "审查席"], "seat_05_tuixue.webp"),
    ("trading-gushiguifei-v1", "gushiguifei", "一路奔行", "区域与量能", ["A/B/C 区域", "SOS 后回踩", "量能位置"], "seat_06_yilubenxing.webp"),
    ("trading-wyckoff-v1", "wyckoff", "威科夫", "供求与事件", ["SOS / SOW", "努力与结局", "区间不交易"], "seat_07_wyckoff.webp"),
    ("trading-candle-v1", "candle", "蜡烛图", "形态确认", ["K 线形态", "确认器", "不独立出票"], "seat_08_candle.webp"),
    ("trading-livermore-v1", "livermore", "利弗莫尔", "关键点", ["关键点", "领头羊", "金字塔加仓"], "seat_09_livermore.webp"),
    ("trading-darwin-v1", "darwin", "达尔文", "长期价值", ["避大风险", "高质量", "永久持有"], "seat_10_darwin.webp"),
    ("trading-boardtech-v1", "boardtech", "明发·阿荣", "打板技术", ["确定性", "封板质量", "龙头梯队"], "seat_13_boardtech.webp"),
    ("trading-chenkai-v1", "chenkai", "陈凯", "交易之路", ["多周期", "背离", "交易纪律"], "seat_11_chenkai.webp"),
    ("trading-lidaxiao-v1", "lidaxiao", "李大霄", "估值为锚", ["钻石底", "婴儿底", "蓝筹龙头"], "seat_12_lidaxiao.webp"),
]
GATE_MAP = {"弱": "WEAK", "非冰点": "NO_SIGNAL", "混合": "MIXED", "无标的": "NO_SIGNAL"}


RULE_KEYS = ("entry_mode", "entry_pull", "entry_break", "pivot", "chase_pct",
             "add_rule", "stop", "risk_pct", "risk_flag", "tgt1", "tgt2",
             "target_note", "time_exit", "atr", "atr_mult")


def seat_rule(base, c):
    """席位自带价位优先，通用 ATR 规则只兜底。

    利弗莫尔给的是关键点本身、一路奔行给的是 A 区低点——这些都不是按固定倍数算出来的，
    面板不能拿 ATR 覆盖掉。
    """
    rule = dict(base or {})
    if not rule:
        return rule
    close_px = float(c.get("close") or rule.get("close") or 0)
    seat_stop_raw = c.get("stop") or c.get("defense")
    if seat_stop_raw:
        seat_stop = float(seat_stop_raw)
        if close_px and 0 < seat_stop < close_px:
            dist = close_px - seat_stop
            atr = rule.get("atr") or 0
            rule.update({
                "stop": round(seat_stop, 2),
                "risk_pct": round(dist / close_px * 100, 2),
                "atr_mult": round(dist / atr, 2) if atr else None,
                "risk_flag": c.get("risk_flag") or "正常",
                "tgt1": round(close_px + 2 * dist, 2),
                "tgt2": round(close_px + 3 * dist, 2),
            })
    mode = c.get("rule_mode") or ("valuation" if c.get("pe") is not None else None)
    if mode == "board":
        rule.update({
            "entry_mode": "board",
            "entry_pull": None,
            "entry_break": None,
            "pivot": None,
            "add_rule": c.get("buy_point") or "涨停价挂单打板；一字板只能次日集合竞价排队",
            "stop": None, "risk_pct": None, "atr_mult": None,
            "tgt1": None, "tgt2": None,
            "target_note": c.get("exit_rule") or "断板即卖；高位出现反转征兆立刻出局",
            "time_exit": "次日不再封板就离场",
            "risk_flag": "正常",
        })
    if mode == "structure":
        rule.update({
            "entry_mode": "structure",
            "entry_pull": None,
            "entry_break": None,
            "tgt1": None, "tgt2": None,
            "add_rule": c.get("buy_point") or "回踩 MA20 不破且缩量再看，不追高",
            "target_note": c.get("exit_rule") or "跌破 MA20 且 MA20 走平，结构失效",
            "time_exit": "顶背离出现即退出",
            "risk_flag": "正常",
        })
    elif mode == "valuation":
        rule.update({
            "entry_mode": "valuation",
            "entry_pull": None,
            "entry_break": None,
            "tgt1": None, "tgt2": None,
            "add_rule": c.get("buy_point") or "底部区域分批买，不用等回调",
            "target_note": c.get("exit_rule"),
            "time_exit": "不设时间止损，持仓季度以上",
            "risk_flag": "正常",   # −15% 是容错线不是技术止损，不该当风险标记弹出来
        })
    if c.get("pivot"):
        pivot = float(c["pivot"])
        rule.update({
            "entry_mode": "pivot",
            "pivot": round(pivot, 2),
            "entry_break": round(pivot + 0.01, 2),
            "entry_pull": None,
            "chase_pct": c.get("chase_pct"),
            "add_rule": c.get("add_rule") or "突破下一个关键点才加，第二笔不超过首笔 100%",
            "tgt1": None, "tgt2": None,
            "target_note": "不设目标价（书里按关键点判断退出）",
            "time_exit": c.get("time_exit") or "5 个交易日不创新高则退",
        })
    return rule


def pick_row(key, c, detail, rules):
    """一个席位自己筛出的票，给抽屉列表用；止损取该席位自己的口径。"""
    code = str(c["symbol"]).zfill(6)
    d = detail.get(code) or {}
    rule = seat_rule(rules.get(code), c)
    pool, label = CAND_META.get(key, ("short", "短期"))
    return {"code": code,
            "name": c.get("name") or d.get("name") or "",
            "pool": pool, "poolLabel": label,
            "role": c.get("role") or c.get("industry") or "",
            "close": c.get("close") or rule.get("close"),
            "stop": rule.get("stop"), "risk_pct": rule.get("risk_pct"),
            "risk_flag": rule.get("risk_flag") or c.get("risk_flag") or "正常",
            "stage": d.get("stage"),
            # 把这席位自己的规则一起带上：同一只票被两个席位选中时，
            # 从谁的抽屉点进去就按谁的口径显示，不能串味
            "rules": {k: rule.get(k) for k in RULE_KEYS} if rule else None}


def build_dashboard(results, feats, asof, detail=None, rules=None, universe="all"):
    import datetime as _dt
    detail = detail or {}
    rules = rules or {}
    today = [r for r in feats if r["tradedate"] == asof and not _not_in_sample(r["symbol"], universe)]
    seen = {}
    for r in feats:
        k = str(r["symbol"]).zfill(6)
        seen[k] = seen.get(k, 0) + 1
    pool = [r for r in today
            if "ST" not in str(r.get("name", "")).upper() and "退" not in str(r.get("name", ""))
            and (r.get("close") or 0) >= 2 and (r.get("amount") or 0) >= 1e8
            and seen.get(str(r["symbol"]).zfill(6), 0) >= 60]

    # 全市场报价快照：个人池里的票可能早就不在候选表里了，估值不能靠候选表
    quotes = {}
    for r in pool:
        c_ = str(r["symbol"]).zfill(6)
        quotes[c_] = [r.get("name") or "", r.get("close"), r.get("pre_close")]

    all_c, open_c, uniq = [], [], {}
    for k, v in results.items():
        cs = v.get("candidates") or []
        all_c.extend(cs)
        if v.get("gate", {}).get("open"):
            open_c.extend(cs)
            for c in cs:
                uniq.setdefault(str(c["symbol"]).zfill(6), c.get("name", ""))
    n1, n2 = len(today), len(pool)
    _c = sample_counts(feats, asof)
    n3 = len(all_c)   # 候选条目数：与 L4（开闸席位条目）、L5（去重后）保持单调递减
    n4, n5 = len(open_c), len(uniq)   # L4 开闸席位条目数，L5 去重后代码数
    stages = [
        {"id": "L1", "title": UNIVERSES[universe], "shortTitle": "初始市场样本", "count": n1,
         "note": "沪深主板 + 创业板（不是全市场）" if universe == "all" else "只看沪深主板（不含创业板）",
         "logic": ("以「沪深主板 + 创业板」构建第一层样本；科创板、北交所、B 股不在其中。" if universe == "all"
                   else "以「沪深主板」构建第一层样本；创业板、科创板、北交所、B 股都不在其中。"),
         "conditions": (["沪深主板", "创业板",
                         f"不在样本：科创板 {_c['star']} 只、北交所/B 股 {_c['other']} 只",
                         f"当日全市场共 {_c['total']} 只"] if universe == "all" else
                        ["沪深主板",
                         f"不在样本：创业板 {_c['chinext']} 只、科创板 {_c['star']} 只、北交所/B 股 {_c['other']} 只",
                         f"当日全市场共 {_c['total']} 只"])},
        {"id": "L2", "title": "合格池", "shortTitle": "基础资格过滤", "count": n2,
         "note": "价格 · 流动性 · 上市时间", "logic": "先排除不具备基础可交易性的股票。",
         "conditions": ["剔除 ST / *ST", "股价 ≥ 2 元", "日成交额 ≥ 1 亿元", "上市满 60 个交易日"]},
        {"id": "L3", "title": "席位覆盖", "shortTitle": "策略席位生成候选", "count": n3,
         "note": "任一席位给出候选", "logic": "十三个独立席位分别运行（审查席与确认席不产候选），任一命中即进入候选集合。",
         "conditions": [f"{len(SEAT_API)} 个席位独立运行", "候选集合取并集", "此阶段不投票、不折中"]},
        {"id": "L4", "title": "环境闸门", "shortTitle": "当前环境匹配", "count": n4,
         "note": "只留开启席位的候选", "logic": "按各席位独立的环境判断，过滤不允许开仓的候选。",
         "conditions": ["读取席位开 / 不开状态", "只保留允许参与的席位候选", "保留原始席位归属"]},
        {"id": "L5", "title": "去重研究候选", "shortTitle": "最终重点跟踪池", "count": n5,
         "note": "同一只股票只计算一次", "logic": "按代码去重，保留命中股票的全部席位来源。",
         "conditions": ["股票代码唯一去重", "保留命中席位列表", "进入候选池"]},
    ]
    now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    seats = []
    for key, sid, name, sub, kw, portrait in SEAT_API:
        r = results.get(key)
        if key == "trading-tuixue-v1" or r is None:
            seats.append({"id": sid, "name": name, "subtitle": sub, "version": key,
                          "gate": "CLOSED" if key == "trading-tuixue-v1" else "UNCONFIGURED",
                          # SEAT-001：审查席不选股 ≠ 运行成功；未配置 ≠ 运行成功
                          "runState": "SKIPPED" if key == "trading-tuixue-v1" else "IDLE",
                          "count": None,
                          "keywords": kw, "portrait": f"/avatars/{portrait}" if portrait else None,
                          "configured": True, "updatedAt": now, "picks": [],
                          "reasoning": "审查席：不产生候选，只对其他席位的出票做模式审查（四要件 + 四条纪律）。"})
            continue
        g = r.get("gate", {})
        op = bool(g.get("open"))
        state = str(g.get("state") or "")
        if r.get("note"):
            state = state + "｜" + r["note"]
        gate = "OPEN" if op else GATE_MAP.get(state, "CLOSED")
        seats.append({"id": sid, "name": name, "subtitle": sub, "version": key,
                      "gate": gate, "runState": "SUCCESS",
                      "count": (None if r.get("note") else len(r.get("candidates") or [])),
                      "keywords": kw,
                      "portrait": f"/avatars/{portrait}" if portrait else None,
                      "configured": True, "updatedAt": now,
                      # 闸门没开也照样列出脚本筛出的票，界面上标明「不开闸」——
                      # 藏起来等于假装它没筛出东西，那是另一种撒谎
                      "picks": [pick_row(key, c, detail, rules)
                                for c in (r.get("candidates") or [])[:30]],
                      "reasoning": (state + "｜" if state else "") + str(g.get("reason") or "")[:150]})
    # 候选池：带上 K 线、量价状态与入场/离场规则
    # 同一只票可能被几个席位同时选中，先把归属记全，界面上不能只挂第一个席位的名字
    owners = {}
    for key in CAND_META:
        r = results.get(key)
        if not r or not r.get("gate", {}).get("open"):
            continue
        nm = next((x[2] for x in SEAT_API if x[0] == key), key)
        for c in (r.get("candidates") or [])[:30]:
            owners.setdefault(str(c["symbol"]).zfill(6), []).append(nm)

    cands, seen_c = [], set()
    for key, (pool, label) in CAND_META.items():
        r = results.get(key)
        if not r or not r.get("gate", {}).get("open"):
            continue
        seat_name = next((x[2] for x in SEAT_API if x[0] == key), key)
        for c in (r.get("candidates") or [])[:30]:
            code = str(c["symbol"]).zfill(6)
            if code in seen_c:
                continue
            seen_c.add(code)
            d = detail.get(code) or {}
            rule = seat_rule(rules.get(code), c)
            k = d.get("k") or []
            cands.append({
                "code": code, "name": c.get("name") or d.get("name") or "",
                "pool": pool, "poolLabel": label, "seat": seat_name,
                "seats": owners.get(code) or [seat_name],
                "role": c.get("role") or c.get("industry") or "",
                "tags": c.get("tags") or [],
                "risks": c.get("risks") or [],
                "stage": d.get("stage"),
                "state10": (d.get("state") or [])[-10:],
                "kline": [{"d": x["d"], "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"]} for x in k[-60:]],
                "flow": (d.get("flow") or [])[-20:],
                "stats": d.get("stats"),
                "rules": {k2: rule.get(k2) for k2 in RULE_KEYS} if rule else None,
            })
    pools = {"short": 0, "swing": 0, "value": 0}
    for c in cands:
        pools[c["pool"]] += 1
    # L5 必须等于候选池里真能点开的只数：这一层就是「进入候选池」，口径不一致就是骗人
    for st in stages:
        if st["id"] == "L5":
            st["count"] = len(cands)
            st["note"] = "同一只股票只计算一次（每席最多取前 30 只）"
    # ── 市场闸门：主用剑门的生态三档（回测最好），野人哥的周期与宽度作参照 ──
    jm = results.get("trading-jianmen-v1") or {}
    wm = results.get("trading-wildman-v2") or {}
    jg = jm.get("gate") or {}
    market_gate = {
        "date": asof,
        "primary": {
            "source": "剑门 · 生态三档",
            "open": bool(jg.get("open")),
            "state": str(jg.get("state") or "未判"),
            "reason": str(jg.get("reason") or ""),
            "metrics": {
                "最高板": (jg.get("factors") or {}).get("max_board"),
                "连板家数": (jg.get("factors") or {}).get("consec2_5d"),
                "封板家数": (jg.get("factors") or {}).get("n_seal"),
            },
        },
        "secondary": {
            "source": "野人哥 · 周期与宽度",
            # 野人哥脚本没有 gate，run_seat 把周期阶段放进了 gate.state
            "state": str((wm.get("gate") or {}).get("state") or "未判"),
            "note": str((wm.get("gate") or {}).get("reason") or "")[:80],
        },
        "backtest": {"excess": 0.52, "t": 1.93, "days": 147, "window": "456 个交易日"},
        "note": "主闸门用剑门，是因为它是十三个席位里唯一在样本外把 t 做到 1.645 以上的（原始值，重叠折算后约 0.9）。",
    }
    # DATA-001/002：把「报价日 / 生成时间 / 完整日 / 新鲜度 / 完整度」分开暴露
    _qd = f"{asof[:4]}-{asof[4:6]}-{asof[6:]}" if asof else None
    # 交易日历：有就用它判「今天是否交易日 / 是否已收盘」，没有就明说不可判定，不许猜
    _cal = {"calendar": "unavailable", "today": None, "trading_day": None,
            "after_close": None, "expected_session": None}
    _lc = asof
    _days = None
    try:
        if str(HERE.parent) not in sys.path:
            sys.path.insert(0, str(HERE.parent))
        from datetime import timezone as _tz
        from panel.services import trade_calendar as _tc
        _days = _tc.load(DATA / "trade_calendar.json")
        _now_dt = datetime.now(_tz(timedelta(hours=8)))
        _cal = _tc.session_state(_now_dt, _days)
        _have = sorted({str(r["tradedate"]) for r in feats})
        _cut = _cal.get("expected_session") or asof
        _lc = ([d for d in _have if d <= _cut] or [asof])[-1]
    except Exception as ex:
        _cal["calendar"] = f"error: {type(ex).__name__}"
    _lcd = f"{_lc[:4]}-{_lc[4:6]}-{_lc[6:]}" if _lc else None
    # 新鲜度按「行情是否到最近一个完整交易日」判，不按文件刚写入判
    _exp = _cal.get("expected_session")
    _fresh, _fresh_why = "unknown", "交易日历不可用，无法判断行情是否落后"
    if _exp and _lc:
        if _lc == _exp:
            _fresh, _fresh_why = "fresh", "行情已到最近一个完整交易日；仍是收盘数据，不是实时行情"
        else:
            _behind = 0
            try:
                _idx = {d: i for i, d in enumerate(_days or [])}
                _behind = max(0, _idx.get(_exp, 0) - _idx.get(_lc, 0)) if _days else 0
            except Exception:
                _behind = 0
            _cal["sessions_behind"] = _behind
            _fresh = "stale"
            _fresh_why = f"行情只到 {_lc}，最近完整交易日是 {_exp}" + (f"，落后 {_behind} 个交易日" if _behind else "")
    data_status = {
        "quote_date": _qd,
        "session_date": _qd,
        "generated_at": now,
        "latest_completed_session_date": _lcd,
        "calendar": {
            "state": _cal.get("calendar"),
            "today": _cal.get("today"),
            "today_is_trading_day": _cal.get("trading_day"),
            "after_close": _cal.get("after_close"),
            "expected_session": _cal.get("expected_session"),
            "sessions_behind": _cal.get("sessions_behind"),
            "note": ("交易日历可用" if _cal.get("calendar") == "ok"
                     else "交易日历不可用：节假日无法识别，只能按真实出现的日期保守判断"),
        },
        "timezone": "Asia/Shanghai",
        "feed_precision": "daily_bar",
        "source_mode": "provider_api",
        "freshness": _fresh,
        "freshness_reason": _fresh_why,
        "quality": "partial",
        "quality_reason": "旧数据没有逐只来源完成标志，bar_status 只能为 unknown",
    }
    from .adapters.legacy_dashboard import annotate_quote_completion
    annotate_quote_completion({"dataStatus": data_status})
    return {"source": "live", "asOf": now, "dataStatus": data_status,
            "stages": stages, "seats": seats,
            "marketGate": market_gate,
            "candidates": cands, "poolCounts": pools, "backtest": BACKTEST,
            "quotes": quotes, "quoteDate": asof}


# ────────────────────────── 重建 ──────────────────────────
def replace_block(html, name, new):
    b, e = f"<!--B:{name}-->", f"<!--E:{name}-->"
    i, j = html.find(b), html.find(e)
    if i < 0 or j < 0:
        return html
    return html[:i + len(b)] + "\n" + new + "\n" + html[j:]


def build(asof=None, verbose=True, universe="all", history_days: int = 130, progress=None):
    t0 = time.time()
    stale = False
    if progress:
        progress("获取指数快照")
    try:
        idx = fetch_indices(asof)
        asof = idx[0]["tradedate"]
    except Exception as ex:
        if verbose: print("指数取数失败，沿用缓存:", ex)
        cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
        if not cache:
            raise
        idx = cache["idx"]; asof = cache["asof"]; stale = True

    history_days = max(30, min(int(history_days), 130))
    if progress:
        progress(f"同步 {history_days} 个交易日的市场日线")
    feats, last = fetch_daily(history_days, progress=progress)
    feats, dropped = trim_partial(feats)
    if dropped:
        asof = max(str(r["tradedate"]) for r in feats)
        if verbose:
            print(f"丢弃未收盘的当日快照 {dropped}（成交额覆盖不足），改用 {asof}")
    fpath = DATA / "features.json"
    write_feature_rows(fpath, feats)
    ipath = DATA / "industry.json"
    ind = None
    if ipath.exists():
        ind = ipath

    val = fetch_valuation(asof)
    lpe = fetch_limit_events(asof)
    dv = DATA / "dividend_years.json"
    results = {}
    for key, _, _ in SEAT_META:
        if progress:
            progress(f"运行席位：{key}")
        lp = build_limit_pool(feats, asof) if key in ("trading-wildman-v2",
                                                      "trading-boardtech-v1") else None
        r = run_seat(key, fpath, ind,
                     extra={"valuation_path": val, "dividend_path": dv if dv.exists() else None,
                            "limit_pool_path": lp, "limit_events_path": lpe})
        if r:
            # 席位脚本各自扫全市场，会把科创板 / 北交所 / B 股也扫进来。
            # 面板的样本口径是「沪深主板 + 创业板」，这里统一挡掉，否则漏斗说不在样本、
            # 候选里却冒出来，还会带着 30% 的涨跌幅限制让人以为数据错了。
            if r.get("candidates"):
                r["candidates"] = [c for c in r["candidates"] if not _not_in_sample(c["symbol"], universe)]
            results[key] = r
    if verbose:
        print("席位跑完", len(results), "个")

    CACHE.write_text(json.dumps({"asof": asof, "idx": idx}, ensure_ascii=False))

    UNIVERSE_INFO = {"id": universe, "label": UNIVERSES[universe]}
    html = PANEL.read_text() if PANEL.is_file() else None
    if html is None and verbose:
        print("HTML 模板缺失，仅生成 JSON；不写入只读源目录")
    if html is not None:
        html = replace_block(html, "IDX", render_idx(idx, asof, stale))
        html = replace_block(html, "SEATS", render_seats(results))
        html = replace_block(html, "POOLS", render_pools(results))
        fv = render_funnel(results, feats, asof, universe)
        html = replace_block(html, "FUNNEL", fv)
        import re as _r2
        m = _r2.search(r'data-n="(\d+)"', fv)
        mlast = _r2.findall(r'data-n="(\d+)"', fv)
        if m and mlast:
            html = _r2.sub(r'(<span class="cnt" id="headcnt">)[^<]*(</span>)',
                       lambda x: x.group(1)+f"{int(m.group(1)):,} → {int(mlast[-1]):,}"+x.group(2),
                           html, count=1)
        html = replace_block(html, "CANDS", render_cands(results))

    # 详情与规则：为当前候选重新生成，否则新票点开是空的
    # 关闸席位的票也要有 K 线：席位抽屉里照样要看得到他筛出来的东西
    syms, seen = [], set()
    for key in results:
        for c in (results[key].get("candidates") or []):
            s_ = str(c["symbol"]).zfill(6)
            if s_ not in seen:
                seen.add(s_); syms.append(s_)
    syms = syms[:120]   # 席位抽屉要能点开本人每一只票，覆盖范围随之上调
    from datetime import date, timedelta
    flow = fetch_flow(syms, (date.today()-timedelta(days=150)).strftime("%Y%m%d"), asof)
    if progress:
        progress("生成候选详情和独立规则")
    detail = build_detail(feats, syms, flow, asof)
    rules = build_rules(detail)
    if html is not None:
        html = inject_detail(html, detail, rules)
    if verbose:
        print(f"详情覆盖 {len(detail)}/{len(syms)} 只")
    # 时间戳
    import re as _re
    # 与漏斗口径一致：剔除科创板(688)、北交所(8/4)、B股(9)
    n_all = len([r for r in feats
                 if r["tradedate"] == asof
                 and not str(r["symbol"]).zfill(6).startswith(("688", "8", "4", "9"))])
    if html is not None:
        html = _re.sub(r'全市场 <b>[\d,]+</b> 只', f'全市场 <b>{n_all:,}</b> 只', html)
        html = _re.sub(r'<h1>.*?</h1>',
                   f'<h1>十个人的判断，<br>一个漏斗的答案</h1>', html, count=1, flags=_re.S)
        html = _re.sub(r'截止 <b>[0-9-]+ 收盘</b>',
                   f'截止 <b>{asof[:4]}-{asof[4:6]}-{asof[6:]} 收盘</b>', html)
        stamp = f'<span class="chip">刷新于 <b>{datetime.now():%H:%M:%S}</b></span>'
        if _re.search(r'<span class="chip">刷新于 <b>[0-9:]+</b></span>', html):
            html = _re.sub(r'<span class="chip">刷新于 <b>[0-9:]+</b></span>', stamp, html)
        else:
            html = html.replace('<span class="chip warn">预览版 · 部分模块待接入</span>', stamp)
        if universe == "all":
            PANEL.write_text(html)          # 单文件快照只出「含创业板」这一版
    payload = build_dashboard(results, feats, asof, detail, rules, universe)
    payload["dataStatus"]["history_requested_sessions"] = history_days
    payload["dataStatus"]["history_available_sessions"] = len(feats.files) if isinstance(feats, CachedDailyRows) else len({r["tradedate"] for r in feats})
    payload["universe"] = {"id": universe, "label": UNIVERSES[universe], "counts": sample_counts(feats, asof)}
    out_name = "dashboard.json" if universe == "all" else f"dashboard_{universe}.json"
    (DATA / out_name).write_text(json.dumps(payload, ensure_ascii=False))
    if verbose:
        print(f"重建完成 {time.time()-t0:.1f}s → {out_name}" + (f" + {PANEL.name}" if universe == "all" else ""))
    return asof


def serve(port, interval):
    import http.server, socketserver, threading
    os.chdir(PANEL.parent)

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    def loop():
        while True:
            try:
                build()
            except Exception as ex:
                print("刷新失败:", ex)
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True).start()
    print(f"http://localhost:{port}/{PANEL.name}  每 {interval}s 重建一次")
    with socketserver.TCPServer(("", port), H) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--serve", type=int)
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--asof")
    ap.add_argument("--universe", choices=("all", "main"), help="只重建一个口径（默认两个都建）")
    a = ap.parse_args()
    if a.serve:
        serve(a.serve, a.interval)
    elif a.universe:
        build(a.asof, universe=a.universe)
    else:
        build(a.asof, universe="all")      # 含创业板 → dashboard.json
        build(a.asof, universe="main")     # 仅主板  → dashboard_main.json
