"""快照服务（DATA-001 / DATA-002 / DATA-004 / DATA-005）：取数组装 → 构建 → 不可变入库 → 按命名空间读取。

- 快照按 ID 不可变（触发器拒绝 UPDATE/DELETE）；同一原始版本重入返回同一 ID；修订生成新 ID 并记血缘；
- latest 只是 snapshot_heads 里的指针：先插快照、同事务再切 head；旧会话补建不会把指针往回拨；
- 上游失败不能把上一快照改写成今天成功：任务失败时 head 不动，旧快照照原样可读（显示为陈旧）；
- file_import 只从服务端预配置的只读归档按 session_date 读，客户端不传路径；
- 回放命名空间（replay_id）里的快照与真实盘互不可见。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from ..adapters.numcat import NumcatAdapter, _rows as disk_rows
from ..config import LEGACY_DATA_DIR, Settings
from ..data.evidence_store import EvidenceStore
from ..domain import calendar as cal_mod
from ..domain import clock
from ..domain.canonical import canonical_bytes, canonical_json, content_hash, derived_id, strict_json_loads
from ..domain.schema import contract
from ..jobs import worker
from ..jobs.worker import JobFailed, JobResult, RetryableError
from . import calendar_service, snapshot_build as sb
from .snapshot_build import Dataset, SnapshotInputs

INDEX_CODES = [c for c, _ in sb.INDICES]


from ..web.errors import ApiError


class SnapshotRejected(ApiError):
    """整批拒收（非法 OHLC / NaN / Infinity / 负金额）：422，不入库、不修正。"""

    def __init__(self, code: str, message: str, details: list | None = None):
        super().__init__(422, code, message, details or [])


# ── 读取 ──
def get(db, snapshot_id: str, replay_id: str | None) -> dict | None:
    r = db.one("SELECT canonical_payload, replay_id FROM snapshots WHERE snapshot_id=?", (snapshot_id,))
    if r is None or r["replay_id"] != replay_id:
        return None
    return json.loads(r["canonical_payload"])


def latest_id(db, replay_id: str | None) -> str | None:
    ns = "live" if replay_id is None else f"replay:{replay_id}"
    r = db.one("SELECT snapshot_id FROM snapshot_heads WHERE namespace=?", (ns,))
    return r["snapshot_id"] if r else None


def latest(db, replay_id: str | None) -> dict | None:
    sid = latest_id(db, replay_id)
    return get(db, sid, replay_id) if sid else None


def current_freshness(db, snap: dict) -> tuple[str, str]:
    """读取时按此刻的日历与时钟重估「是否仍是最新」。快照内容本身不变。"""
    md = snap["metadata"]
    if md.get("is_synthetic") and md["calendar_version"].startswith("calendar_fixture"):
        cal = calendar_service.load(db, md["calendar_version"]) or calendar_service.current(db)
    else:
        cal = calendar_service.current(db)
    if md["freshness"] == "unknown" and cal is None:
        return "unknown", md["freshness_reason"]
    return sb.classify_freshness(cal, md["quote_date"])


CRITICAL_SNAPSHOT_FIELDS = ("stockbasic", "st_status", "suspend_status", "daily_bars", "final_daily_bars")


def execution_blockers(snap: dict, symbol: str) -> list[str]:
    """快照里影响「这一只票能否按规则模拟成交」的缺口（估值/异动事件/指数缺失只降级说明，不阻断）。"""
    out = []
    miss_f = set(snap["metadata"]["missing_fields"])
    for m in CRITICAL_SNAPSHOT_FIELDS:
        if m in miss_f:
            out.append(f"snapshot_missing_{m}")
    if any(m.startswith("invalid_rows") for m in miss_f):
        out.append("snapshot_has_invalid_rows")
    if snap["metadata"]["quality"] == "unavailable":
        out.append("snapshot_quality_unavailable")
    bar = next((b for b in snap["daily_bars"] if b["symbol"] == symbol
                and b["session_date"] == snap["metadata"]["session_date"]), None)
    if bar is None:
        out.append("session_bar_missing")
    elif bar["bar_status"] != "final":
        out.append(f"bar_status_{bar['bar_status']}")
    return out


# ── 入库 ──
def _snapshot_id(identity: str, session_date: str) -> str:
    return f"snap_{session_date.replace('-', '')}_{identity[:12]}"


def store_snapshot(db, res: sb.BuildResult, *, replay_id: str | None, lineage_key: str,
                   snapshot_id: str | None = None, reason: str = "new_source_version") -> tuple[str, bool, str | None]:
    """返回 (snapshot_id, created, supersedes)。同一 identity 已入库则直接返回原 ID（幂等）。"""
    with db.txn() as c:
        row = c.execute("SELECT snapshot_id FROM snapshot_lineage WHERE source_identity_sha256=?",
                        (res.identity,)).fetchone()
        if row:
            sid0 = row["snapshot_id"]
            r2 = c.execute("SELECT replay_id FROM snapshots WHERE snapshot_id=?", (sid0,)).fetchone()
            if r2 and r2["replay_id"] == replay_id:
                return sid0, False, None
        payload = res.payload
        md = payload["metadata"]
        sid = snapshot_id or _snapshot_id(res.identity, md["session_date"])
        if c.execute("SELECT 1 FROM snapshots WHERE snapshot_id=?", (sid,)).fetchone():
            sid = f"{sid[:80]}-{res.identity[:8]}"          # 指定 ID 已被其它内容占用：另起新 ID，旧快照不变
        evidence = []
        for e in payload["evidence"]:
            e = dict(e)
            if e.get("snapshot_id") is None:
                e["snapshot_id"] = sid
            evidence.append(e)
        snap = {"schema_version": payload["schema_version"], "snapshot_id": sid, "metadata": md,
                "instruments": payload["instruments"], "daily_bars": payload["daily_bars"],
                "metrics": payload["metrics"], "evidence": evidence, "conflicts": payload["conflicts"],
                "corporate_actions": payload["corporate_actions"]}
        body = {k: v for k, v in snap.items() if k not in ("snapshot_id", "fingerprint")}
        snap["fingerprint"] = content_hash(body)
        contract().check("MarketSnapshot", snap)
        prev = c.execute("""SELECT l.snapshot_id FROM snapshot_lineage l JOIN snapshots s ON s.snapshot_id=l.snapshot_id
                            WHERE l.lineage_key=? AND s.replay_id IS ? ORDER BY l.created_at DESC LIMIT 1""",
                         (lineage_key, replay_id)).fetchone()
        supersedes = prev["snapshot_id"] if prev else None
        c.execute("""INSERT INTO snapshots(snapshot_id, fingerprint, session_date, quote_date, latest_completed_session_date,
                     captured_at, generated_at, quality, freshness, feed_precision, source_mode, universe_policy_id,
                     universe_fingerprint, price_basis, adjustment_policy_version, calendar_version, is_synthetic,
                     replay_id, canonical_payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (sid, snap["fingerprint"], md["session_date"], md["quote_date"], md["latest_completed_session_date"],
                   md["captured_at"], md["generated_at"], md["quality"], md["freshness"], md["feed_precision"],
                   md["source_mode"], md["universe_policy_id"], md["universe_fingerprint"], md["price_basis"],
                   md["adjustment_policy_version"], md["calendar_version"], int(md["is_synthetic"]), replay_id,
                   canonical_json(snap)))
        for e in evidence:
            sr = e["source_ref"]
            c.execute("""INSERT INTO evidence(evidence_id, snapshot_id, kind, symbol, source_id, authority_type,
                         content_sha256, observed_at, available_at, first_observed_at, captured_at, time_precision,
                         availability_basis, quality, replay_id, canonical_payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (e["evidence_id"], sid, e["kind"], e["symbol"], sr["source_id"], sr["authority_type"],
                       sr["content_sha256"] or content_hash(e["value"]), e["observed_at"], e["available_at"],
                       e["first_observed_at"], e["captured_at"], e["time_precision"], e["availability_basis"],
                       e["quality"], replay_id, canonical_json(e)))
        c.execute("""INSERT INTO snapshot_lineage(snapshot_id, source_identity_sha256, supersedes_snapshot_id, lineage_key,
                     reason, created_at) VALUES(?,?,?,?,?,?)""",
                  (sid, res.identity, supersedes, lineage_key, reason if supersedes else "first_version", clock.utc_iso()))
        ns = "live" if replay_id is None else f"replay:{replay_id}"
        head = c.execute("""SELECT h.snapshot_id, s.session_date, s.captured_at FROM snapshot_heads h
                            JOIN snapshots s ON s.snapshot_id=h.snapshot_id WHERE h.namespace=?""", (ns,)).fetchone()
        if head is None:
            c.execute("INSERT INTO snapshot_heads(namespace, replay_id, snapshot_id, updated_at) VALUES(?,?,?,?)",
                      (ns, replay_id, sid, clock.utc_iso()))
        elif (md["session_date"], md["captured_at"]) >= (head["session_date"], head["captured_at"]):
            c.execute("UPDATE snapshot_heads SET snapshot_id=?, updated_at=? WHERE namespace=?",
                      (sid, clock.utc_iso(), ns))
        return sid, True, supersedes


# ── 组装：供应商 ──
_provider_factory = None


def set_provider_factory(fn) -> None:
    """测试/验收注入受控 provider（必须实现与 NumcatAdapter 相同的方法）。"""
    global _provider_factory
    _provider_factory = fn


def provider(settings: Settings, store: EvidenceStore):
    if _provider_factory is not None:
        return _provider_factory(settings, store)
    return NumcatAdapter(settings.meoz_cli, store, settings.runtime_dir / "provider_cache",
                         max_retry=settings.provider_max_retry, timeout=settings.provider_timeout_seconds)


def _ds(raw, role: str, session_date: str | None) -> Dataset:
    return Dataset(role=role, rows=raw.rows, raw_sha256=raw.raw_sha256, captured_at=raw.captured_at,
                   source_id=raw.source_id or "numcat", operation_id=raw.operation_id, params=raw.params,
                   session_date=session_date, reused=raw.reused, first_observed_at=raw.captured_at)


def _legacy_cache_dataset(store: EvidenceStore, day: str) -> Dataset | None:
    f = LEGACY_DATA_DIR / "daily" / f"{day.replace('-', '')}.json"
    if not f.exists():
        return None
    mt = datetime.fromtimestamp(f.stat().st_mtime, tz=clock.UTC)
    # 盘中写入的当天缓存是半天数据：不能当完整历史日用
    sh = mt.astimezone(clock.SHANGHAI)
    if sh.strftime("%Y-%m-%d") == day and (sh.hour, sh.minute) < (15, 5):
        return None
    raw = f.read_bytes()
    sha = store.put(raw)
    rows = disk_rows(raw, store.root.parent / "provider_cache/rows" / f"{sha}.rows")
    return Dataset(role="daily_history", rows=rows, raw_sha256=sha, captured_at=clock.utc_iso(mt),
                   source_id="legacy_daily_cache", operation_id="reference_stock_daily", params={"tradedate": day},
                   session_date=day, first_observed_at=clock.utc_iso(mt), raw_format="legacy_rows_v1",
                   note="旧 refresh.py 的日线缓存（供应商原始响应的行解析结果）")


def _derived(store: EvidenceStore, role: str, obj, fmt: str, captured: str, source: str, note: str,
             session_date: str | None) -> Dataset:
    raw = canonical_bytes(obj)
    sha = store.put(raw)
    rows = obj if isinstance(obj, list) else [obj]
    return Dataset(role=role, rows=rows, raw_sha256=sha, captured_at=captured, source_id=source,
                   authority_type="vendor", raw_format=fmt, session_date=session_date, note=note,
                   first_observed_at=captured)


CALENDAR_LOOKAHEAD_DAYS = 120      # 交易所年度日历提前公布：多要一段未来，次日起「最近完整交易日」仍可判定


def assemble_provider(db, settings: Settings, store: EvidenceStore, adapter, session_date: str, symbols: list[str],
                      now: datetime, progress=None) -> tuple[SnapshotInputs, list[str]]:
    D = session_date
    Dc = D.replace("-", "")
    today = clock.shanghai_date(now)
    errors: list[str] = []
    start = (datetime.fromisoformat(D) - timedelta(days=240)).strftime("%Y%m%d")
    end = (datetime.fromisoformat(max(today, D)) + timedelta(days=CALENDAR_LOOKAHEAD_DAYS)).strftime("%Y%m%d")
    try:
        cal_raw = adapter.tradecal(start, end)
        cal = calendar_service.from_provider_rows(cal_raw.rows, start, end, cal_raw.raw_sha256)
        if cal is None:
            errors.append("calendar:SOURCE_EMPTY")
            cal = calendar_service.latest_any(db)
        else:
            calendar_service.store(db, cal)
    except (RetryableError, JobFailed) as ex:
        cal = calendar_service.latest_any(db)
        errors.append(f"calendar:{getattr(ex, 'code', 'error')}")
    if cal is not None and cal.status(D) == "closed":
        raise JobFailed("NON_TRADING_SESSION", f"{D} 不是交易日（{cal.calendar_version}）；不会补出 0 值行情",
                        [{"field": "session_date", "reason": "non_trading_session"}])
    if cal is not None and D == today and cal.phase(now) == "pre_open":
        raise JobFailed("SESSION_NOT_STARTED",
                        f"{D} 还没开盘（上海 {clock.shanghai(now).strftime('%H:%M')}）：今天的日线还不存在",
                        [{"field": "session_date", "reason": "session_not_started"}])
    past = D < today
    if progress:
        progress(1, None, "fetch_session")
    try:
        daily = _ds(adapter.daily(Dc, past), "daily_session", D)
    except RetryableError as ex:
        raise JobFailed(ex.code, f"当日日线取数失败：{ex.message}（已按重试上限停止；旧快照保持不变）")
    if not daily.rows:
        raise JobFailed("SOURCE_EMPTY", f"供应商未返回 {D} 的日线（可能尚未发布或不是交易日）",
                        [{"field": "session_date", "reason": "no_rows"}])
    finality, basis = sb.provider_finality(D, daily.captured_at, cal)

    def opt(fn, role):
        try:
            return _ds(fn(), role, D)
        except (RetryableError, JobFailed) as ex:
            errors.append(f"{role}:{getattr(ex, 'code', 'error')}")
            return None
    stockbasic = opt(lambda: adapter.stockbasic(), "stockbasic")
    st = opt(lambda: adapter.st(Dc, past), "st")
    suspend = opt(lambda: adapter.suspend(Dc, past), "suspend")
    plimit = opt(lambda: adapter.pricelimit(Dc, past), "pricelimit")
    valuation = opt(lambda: adapter.valuation(Dc, past), "valuation")
    events = opt(lambda: adapter.limit_events(Dc, past), "limit_events")
    hist_days = []
    if cal is not None:
        hist_days = [d for d in cal.sessions_between(cal_mod._iso(start), D) if d < D][-sb.HISTORY_SESSIONS:]
    history: list[Dataset] = []
    mixed = False
    for i, day in enumerate(hist_days):
        if progress:
            progress(2 + i, 2 + len(hist_days) + 2, "fetch_history")
        ds = _legacy_cache_dataset(store, day) if settings.use_legacy_data_cache else None
        if ds is not None:
            mixed = True
        else:
            try:
                ds = _ds(adapter.daily(day.replace("-", ""), True), "daily_history", day)
            except (RetryableError, JobFailed) as ex:
                errors.append(f"daily_history:{day}:{getattr(ex, 'code', 'error')}")
                continue
        history.append(ds)
    index = None
    if hist_days:
        index = opt(lambda: adapter.index_daily(INDEX_CODES, hist_days[0].replace("-", ""), Dc, past), "index_daily")
    derived = []
    captured = daily.captured_at
    if stockbasic:
        ind = {str(r.get("symbol") or str(r.get("code", "")).split(".")[0]).zfill(6): r.get("industry")
               for r in stockbasic.rows if r.get("industry")}
        derived.append(_derived(store, "industry_map", ind, "industry_map_v1", stockbasic.captured_at,
                                "numcat_stockbasic_derived", "stockbasic.industry（当前分类）", D))
    dv = LEGACY_DATA_DIR / "dividend_years.json"
    if settings.use_legacy_data_cache and dv.exists() and cal is not None and D == cal.latest_completed_session(now):
        raw = dv.read_bytes()
        sha = store.put(raw)
        derived.append(Dataset(role="dividend_years_current", rows=[], raw_sha256=sha,
                               captured_at=clock.utc_iso(datetime.fromtimestamp(dv.stat().st_mtime, tz=clock.UTC)),
                               source_id="legacy_dividend_years", raw_format="dividend_years_v1",
                               note="当前连续分红年数表：只适用于最新会话；用于历史会前视", session_date=None))
    inp = SnapshotInputs(session_date=D, source_mode="mixed" if mixed else "provider_api", symbols=symbols,
                         calendar=cal, session_daily=daily, history=history, stockbasic=stockbasic, st=st,
                         suspend=suspend, pricelimit=plimit, valuation=valuation, limit_events=events,
                         index_daily=index, derived=derived, finality=finality, finality_basis=basis,
                         warnings=[f"optional_source_failed：{e}" for e in errors])
    lp = build_limit_pool(inp)
    if lp is not None:
        inp.derived.append(_derived(store, "limit_pool", lp, "limit_pool_v1", captured,
                                    "roundtable_limit_pool_derived", "由当日日线 + 涨跌停价 + 异动事件推导", D))
    return inp, errors


def build_limit_pool(inp: SnapshotInputs) -> list[dict] | None:
    """席位输入用的涨停池（与 SEAT-SPEC 的 --limit-pool 形状一致）。"""
    from ..domain import trading_rules as tr
    if inp.session_daily is None:
        return None
    limits = {}
    if inp.pricelimit:
        for r in inp.pricelimit.rows:
            s = tr.to_symbol(str(r.get("symbol") or ""))
            up, dn = sb._raw_num(r, "up_limit", [], 0), sb._raw_num(r, "down_limit", [], 0)
            if s and up is not None and dn is not None:
                limits[s] = (up, dn)
    ev = {}
    if inp.limit_events:
        for r in inp.limit_events.rows:
            code = str(r.get("symbol") or "").split(".")[0].zfill(6)
            cur = ev.setdefault(code, {"first_time": None, "open_times": None, "fd_amount": None, "limit_times": None})
            t = r.get("servertime")
            if str(r.get("event")) == "2" and t and (cur["first_time"] is None or str(t) < cur["first_time"]):
                cur["first_time"] = str(t)
            for k_src, k_dst in (("open_times", "open_times"), ("limit_times", "limit_times")):
                if r.get(k_src) is not None:
                    cur[k_dst] = int(r[k_src])
            if r.get("fd_amount") is not None:
                cur["fd_amount"] = str(r["fd_amount"])
    out = []
    D = inp.session_date
    for i, r in enumerate(inp.session_daily.rows):
        sym = tr.to_symbol(str(r.get("symbol") or ""))
        if not sym:
            continue
        n = sb.normalize_daily_row(r, i, [])
        if n is None or n["close"] is None or n["high"] is None:
            continue
        L = limits.get(sym)
        if L is None:
            br = tr.classify_by_code_prefix(sym)
            L = sb.derive_limits(br.board, tr.is_st_name(n["name"]), n["pre_close"])
        if not L:
            continue
        typ = "u" if n["close"] == L[0] else ("ub" if n["high"] == L[0] else ("d" if n["close"] == L[1] else None))
        if not typ:
            continue
        code = sym.split(".")[0]
        e = ev.get(code, {})
        out.append({"tradedate": D.replace("-", ""), "symbol": code, "name": n["name"], "type": typ,
                    "limit_times": e.get("limit_times"), "first_time": e.get("first_time"),
                    "open_times": e.get("open_times"), "fd_amount": e.get("fd_amount"),
                    # 存档用十进制字符串（canonical JSON 拒绝 float）；席位运行器给旧脚本时再转数字
                    "amount": sb.normalize_str(sb.Decimal(n["amount_minor"]) / 100) if n["amount_minor"] is not None else None,
                    "close": str(n["close"]),
                    "pct_chg": sb.normalize_str(n["change_ratio"] * 100) if n["change_ratio"] is not None else None})
    return out


# ── 组装：只读归档（file_import） ──
def _find_archive(settings: Settings, day: str) -> Path | None:
    for root in settings.file_import_roots:
        f = Path(root) / f"{day.replace('-', '')}.json"
        if f.is_file():
            return f
    return None


def assemble_file_import(db, settings: Settings, store: EvidenceStore, session_date: str, symbols: list[str],
                         now: datetime) -> tuple[SnapshotInputs, list[str]]:
    D = session_date
    f = _find_archive(settings, D)
    if f is None:
        raise JobFailed("ARCHIVE_SESSION_MISSING", f"预配置的只读归档中没有 {D} 的日线",
                        [{"field": "session_date", "reason": "not_in_archive"}])
    cal = calendar_service.latest_any(db)
    if cal is None or not (cal.coverage_start <= D <= cal.coverage_end):
        days = set()
        for root in settings.file_import_roots:
            if Path(root).is_dir():
                days |= {cal_mod._iso(p.stem) for p in Path(root).glob("*.json") if p.stem.isdigit()}
        if days:
            cal = cal_mod.TradingCalendar(calendar_version=f"archive_dates_{content_hash(sorted(days))[:12]}",
                                          open_sessions=frozenset(days), coverage_start=min(days),
                                          coverage_end=max(days), source="file_import archive dates",
                                          explicit_closed_only=True)
            calendar_service.store(db, cal)

    def load(day: str, role: str) -> Dataset | None:
        p = _find_archive(settings, day)
        if p is None:
            return None
        raw = p.read_bytes()
        sha = store.put(raw)
        mt = datetime.fromtimestamp(p.stat().st_mtime, tz=clock.UTC)
        rows = disk_rows(raw, store.root.parent / "provider_cache/rows" / f"{sha}.rows")
        return Dataset(role=role, rows=rows, raw_sha256=sha, captured_at=clock.utc_iso(mt),
                       source_id="archive_daily_file", operation_id="reference_stock_daily",
                       params={"tradedate": day.replace("-", "")}, session_date=day,
                       first_observed_at=clock.utc_iso(mt), raw_format="legacy_rows_v1",
                       note=f"只读归档 {p.parent.name}/{p.name}")
    daily = load(D, "daily_session")
    hist_days = [d for d in (cal.sessions_between("0000-01-01", D) if cal else []) if d < D][-sb.HISTORY_SESSIONS:]
    history = [h for h in (load(d, "daily_history") for d in hist_days) if h is not None]
    sh_mt = clock.parse_utc(daily.captured_at).astimezone(clock.SHANGHAI)
    if sh_mt.strftime("%Y-%m-%d") > D or (sh_mt.strftime("%Y-%m-%d") == D and (sh_mt.hour, sh_mt.minute) >= (15, 30)):
        finality, basis = "final_confirmed", "archive_settled_v1：归档文件写于收盘 30 分钟之后，视为沉淀日线"
    elif sh_mt.strftime("%Y-%m-%d") == D:
        finality, basis = "not_final", "archive_settled_v1：归档文件写于当日收盘前，属盘中数据"
    else:
        finality, basis = None, "archive_settled_v1：归档写入时间早于会话日，无法判定"
    inp = SnapshotInputs(session_date=D, source_mode="file_import", symbols=symbols, calendar=cal,
                         session_daily=daily, history=history, finality=finality, finality_basis=basis,
                         warnings=["file_import：归档只有日线；证券身份、ST、停牌、涨跌停价来自受控 fallback 或缺失"])
    lp = build_limit_pool(inp)
    if lp is not None:
        inp.derived.append(_derived(store, "limit_pool", lp, "limit_pool_v1", daily.captured_at,
                                    "roundtable_limit_pool_derived", "由归档日线 + 推导涨跌停价推导（无封板时间）", D))
    return inp, []


# ── 任务 ──
@worker.register("snapshot")
def run_job(ctx: worker.JobContext):
    from ..config import get_settings
    s = ctx.settings or get_settings()
    p = ctx.payload
    store = EvidenceStore(s.evidence_dir)
    now = clock.now()
    D = p["session_date"]
    if p["source_mode"] == "provider_api":
        inp, errors = assemble_provider(ctx.db, s, store, provider(s, store), D, p.get("symbols") or [], now,
                                        progress=ctx.progress)
    elif p["source_mode"] == "file_import":
        inp, errors = assemble_file_import(ctx.db, s, store, D, p.get("symbols") or [], now)
    else:
        raise JobFailed("UNSUPPORTED_SOURCE_MODE", p["source_mode"])
    inp.replay_id = ctx.replay_id
    ctx.progress(90, 100, "build")
    res = sb.build(inp, now)
    md = res.payload["metadata"]
    unknown = [x for x in p.get("symbols") or [] if x not in {i["symbol"] for i in res.payload["instruments"]}]
    if unknown:
        raise JobFailed("SYMBOL_NOT_IN_SNAPSHOT", f"请求的股票不在当日数据中：{', '.join(unknown[:5])}",
                        [{"field": "symbols", "reason": "not_found"}])
    if md["freshness"] == "stale" and not p.get("allow_stale"):
        raise JobFailed("DATA_STALE", f"构建结果为陈旧数据（{md['freshness_reason']}）；如确需历史会话请设 allow_stale=true",
                        [{"field": "allow_stale", "reason": "stale_result_not_allowed"}])
    lineage_key = f"{p['source_mode']}:{D}:{content_hash(sorted(p.get('symbols') or []))[:12]}"
    sid, created, sup = store_snapshot(ctx.db, res, replay_id=ctx.replay_id, lineage_key=lineage_key)
    return JobResult("partial" if errors else "succeeded", resource_id=sid, resource_type="snapshot")


# ── 显式摄入（fixture / 用户手工）：任何非法行整批拒收 ──
def ingest_explicit(db, store: EvidenceStore, *, session_date: str, rows: list[dict], source_mode: str,
                    calendar, finality: str | None, finality_basis: str, captured_at: str, source_id: str,
                    is_synthetic: bool, symbols_map: dict[int, str] | None = None, snapshot_id: str | None = None,
                    replay_id: str | None = None, explicit_evidence: list[dict] | None = None,
                    corporate_actions: list[dict] | None = None, history: list[Dataset] | None = None,
                    anomaly: dict | None = None, stockbasic_rows: list[dict] | None = None,
                    lineage_key: str | None = None, now: datetime | None = None,
                    price_basis: str = "raw", adjustment_policy_version: str | None = sb.ADJUSTMENT_POLICY_RAW,
                    freshness_override: tuple[str, str] | None = None) -> dict:
    ok, errors = sb.validate_rows(rows)
    if errors:
        raise SnapshotRejected("SNAPSHOT_ROWS_INVALID", f"{len({e['row'] for e in errors})} 行数据非法，整批拒收、未入库",
                               [{"field": e["field"], "reason": f"row {e['row']}: {e['reason']}"} for e in errors])
    raw = canonical_bytes({"rows": rows, "source_id": source_id, "session_date": session_date})
    sha = store.put(raw)
    daily = Dataset(role="daily_session", rows=rows, raw_sha256=sha, captured_at=captured_at, source_id=source_id,
                    authority_type="synthetic" if is_synthetic else "user", operation_id=None,
                    session_date=session_date, first_observed_at=captured_at, raw_format="explicit_rows_v1")
    sbds = None
    if stockbasic_rows is not None:
        sraw = canonical_bytes(stockbasic_rows)
        sbds = Dataset(role="stockbasic", rows=stockbasic_rows, raw_sha256=store.put(sraw), captured_at=captured_at,
                       source_id=source_id, authority_type="synthetic" if is_synthetic else "user",
                       raw_format="explicit_rows_v1", session_date=session_date)
    inp = SnapshotInputs(session_date=session_date, source_mode=source_mode, symbols=[], calendar=calendar,
                         session_daily=daily, history=history or [], stockbasic=sbds, finality=finality,
                         finality_basis=finality_basis, is_synthetic=is_synthetic, replay_id=replay_id,
                         explicit_evidence=explicit_evidence or [], corporate_actions=corporate_actions or [],
                         anomaly=anomaly or {}, price_basis=price_basis,
                         adjustment_policy_version=adjustment_policy_version,
                         freshness_override=freshness_override)
    res = sb.build(inp, now)
    sid, created, sup = store_snapshot(db, res, replay_id=replay_id, snapshot_id=snapshot_id,
                                       lineage_key=lineage_key or f"{source_mode}:{session_date}:{source_id}")
    return {"snapshot_id": sid, "created": created, "supersedes": sup, "build": res}
