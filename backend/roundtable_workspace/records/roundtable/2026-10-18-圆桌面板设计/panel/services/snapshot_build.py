"""快照构建的纯函数部分：行规范化与校验、终结状态、新鲜度/完整度、证据与指标（DATA-001~005）。

本模块不访问网络、不写数据库；输入是已经取回并存档的数据集（每个都带原始字节 SHA-256），
输出是契约形状的 MarketSnapshot（还没有 snapshot_id / fingerprint）。

几条硬规矩：
  - 成交额覆盖率只产生「质量异常线索」，不决定 bar 是否收盘、也不删除当天数据；
  - bar_status 只由来源终结标志或版本化校验流程决定（daily_finality_policy_v1）；
  - 缺值是 null + 原因，不补 0；非法 OHLC / NaN / Infinity / 负金额拒收；
  - 供应商 vol=手、amount=元、pct_chg=百分数：只换算一次，原始值保留在原始字节里；
  - pre_close 只是供应商可比昨收，不能据此声称整段历史已前复权。
"""
from __future__ import annotations

import statistics
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from pathlib import Path

from ..domain import clock
from ..domain import trading_rules as tr
from ..domain import universe as uv
from ..domain.calendar import DAILY_PUBLICATION_DELAY, FRESHNESS_POLICY_VERSION, TradingCalendar
from ..domain.canonical import content_hash, derived_id
from ..domain.metrics_dictionary import INDICES, METRIC_DICTIONARY_VERSION, METRICS
from ..domain.money import normalize_str, ratio_str
from .workload import bounded_memory


def _disk_value(value):
    if isinstance(value, Decimal):
        return {"__decimal__": str(value)}
    if isinstance(value, dict):
        return {k: _disk_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_disk_value(v) for v in value]
    return value


def _memory_value(value):
    if isinstance(value, dict):
        if set(value) == {"__decimal__"}:
            return Decimal(value["__decimal__"])
        return {k: _memory_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_memory_value(v) for v in value]
    return value


class _DiskHistoryDay:
    """Small symbol index over normalized history rows stored as JSONL."""
    def __init__(self, path: Path):
        self.path = path
        self._offsets: dict[str, int] = {}
        with path.open("rb") as f:
            while True:
                offset = f.tell()
                line = f.readline()
                if not line:
                    break
                row = json.loads(line)
                self._offsets[str(row["symbol"])] = offset

    def get(self, symbol: str, default=None):
        offset = self._offsets.get(symbol)
        if offset is None:
            return default
        with self.path.open("rb") as f:
            f.seek(offset)
            return _memory_value(json.loads(f.readline()))

    def items(self):
        with self.path.open("rb") as f:
            for line in f:
                if line.strip():
                    row = _memory_value(json.loads(line))
                    yield row["symbol"], row

    def __iter__(self):
        return (key for key in self._offsets)


class _DiskHistory:
    """Day keyed history store; keeps only requested rows in process memory."""
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="roundtable-history-"))
        self.days: dict[str, _DiskHistoryDay] = {}

    def add(self, day: str, rows: list[dict]) -> None:
        path = self.root / f"{day}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(_disk_value(row), ensure_ascii=False, separators=(",", ":")) + "\n")
        self.days[day] = _DiskHistoryDay(path)

    def __getitem__(self, day):
        return self.days[day]

    def __iter__(self):
        return iter(self.days)

    def __len__(self):
        return len(self.days)

    def __del__(self):
        shutil.rmtree(getattr(self, "root", ""), ignore_errors=True)

SCHEMA_VERSION = "1.0.0"
TRANSFORM_VERSION = "snapshot_transform_v1"
FINALITY_POLICY_VERSION = "daily_finality_policy_v1"
AVAILABILITY_POLICY_VERSION = "daily_bar_availability_policy_v1"
PRICE_LIMIT_DERIVATION = "price_limit_derivation_v1"
ADJUSTMENT_POLICY_RAW = "raw_no_adjustment_v1"
HISTORY_SESSIONS = 70
LOW_AMOUNT_ANOMALY_RATIO = Decimal("0.6")      # 只作质量线索，不决定完整日

POLICY_TEXT = {
    FINALITY_POLICY_VERSION: (
        "日线终结判定：供应商文档说明「历史日期返回沉淀日线、最新交易日返回盘中可用行情」。"
        "会话日早于抓取时的上海日期 → final（沉淀日线）；会话日=当天且未收盘 → partial；"
        "会话日=当天且已收盘 → unknown（供应商未提供终结标志，待次日按沉淀日线重取）。"
        "成交额覆盖率、文件数量、收盘时钟都不能单独把 partial 升级为 final。"),
    AVAILABILITY_POLICY_VERSION: (
        "日期精度的收盘数据可知时刻：会话日 15:00 收盘后加 30 分钟（上海时间）视为公开可知；"
        "time_precision=date_only，availability_basis=authoritative_publication（交易所收盘行情）。"
        "本系统实际首次取得时间另记 first_observed_at；已知修订以新版本快照表示，不回写旧快照。"),
    PRICE_LIMIT_DERIVATION: (
        "缺供应商涨跌停价时的推导：round_half_up(pre_close × (1 ± 比例), 2)，主板 10%（ST 5%）、"
        "创业板/科创板 20%、北交所 30%；新股无涨跌幅限制期间不适用。推导值标为 rule_derivation。"),
    ADJUSTMENT_POLICY_RAW: "价格为供应商原始价（未复权）；pre_close 只是供应商可比昨收，不代表整段序列已复权。",
}


class RowsInvalid(ValueError):
    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__(f"{len(errors)} 行数据非法")


@dataclass
class Dataset:
    role: str
    rows: list[dict]
    raw_sha256: str
    captured_at: str
    source_id: str
    authority_type: str = "vendor"
    operation_id: str | None = None
    params: dict | None = None
    session_date: str | None = None
    reused: bool = False
    first_observed_at: str | None = None
    raw_format: str = "numcat_matrix_v1"
    note: str = ""


@dataclass
class SnapshotInputs:
    session_date: str
    source_mode: str
    symbols: list[str]
    calendar: TradingCalendar | None
    session_daily: Dataset | None
    history: list[Dataset] = field(default_factory=list)
    stockbasic: Dataset | None = None
    st: Dataset | None = None
    suspend: Dataset | None = None
    pricelimit: Dataset | None = None
    valuation: Dataset | None = None
    limit_events: Dataset | None = None
    index_daily: Dataset | None = None
    derived: list[Dataset] = field(default_factory=list)     # industry_map / limit_pool / dividend_years
    finality: str | None = None
    finality_basis: str = ""
    is_synthetic: bool = False
    replay_id: str | None = None
    corporate_actions: list[dict] = field(default_factory=list)
    explicit_evidence: list[dict] = field(default_factory=list)
    anomaly: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    price_basis: str = "raw"
    adjustment_policy_version: str | None = ADJUSTMENT_POLICY_RAW
    freshness_override: tuple[str, str] | None = None        # 仅回放快照：按截点判定
    reference_now: datetime | None = None                     # 回放：日历相对字段按截点而非真实「现在」计算


# ── 数值解析与行校验 ──
def _raw_num(row: dict, key: str, errors: list, idx: int):
    """返回 Decimal / None；非法（NaN、Infinity、不可解析、bool）记错误。"""
    v = row.get(key)
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        errors.append({"row": idx, "field": key, "reason": "bool 不是数值"})
        return None
    try:
        if isinstance(v, float):
            d = Decimal(repr(v))
        else:
            d = Decimal(str(v)) if not isinstance(v, Decimal) else v
    except (InvalidOperation, ValueError):
        errors.append({"row": idx, "field": key, "reason": f"不可解析的数值 {v!r}"})
        return None
    if not d.is_finite():
        errors.append({"row": idx, "field": key, "reason": f"非有限数值 {v!r}"})
        return None
    return d


def _fen(yuan: Decimal) -> int:
    with localcontext() as ctx:
        ctx.prec = 60
        return int((yuan * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def normalize_daily_row(row: dict, idx: int, errors: list) -> dict | None:
    """供应商日线行 → 规范值（Decimal 价格、整数股、整数分、ratio）。返回 None 表示该行被拒收。"""
    local: list = []
    o = _raw_num(row, "open", local, idx)
    h = _raw_num(row, "high", local, idx)
    lo = _raw_num(row, "low", local, idx)
    c = _raw_num(row, "close", local, idx)
    pc = _raw_num(row, "pre_close", local, idx)
    pct = _raw_num(row, "pct_chg", local, idx)
    vol = _raw_num(row, "vol", local, idx)
    amt = _raw_num(row, "amount", local, idx)
    amt_minor_raw = _raw_num(row, "amount_minor", local, idx)
    for k, v in (("open", o), ("high", h), ("low", lo), ("close", c), ("pre_close", pc)):
        if v is not None and v <= 0:
            local.append({"row": idx, "field": k, "reason": "价格必须为正"})
    if vol is not None and vol < 0:
        local.append({"row": idx, "field": "vol", "reason": "成交量不能为负"})
    if amt is not None and amt < 0:
        local.append({"row": idx, "field": "amount", "reason": "成交额不能为负"})
    if amt_minor_raw is not None and amt_minor_raw < 0:
        local.append({"row": idx, "field": "amount_minor", "reason": "成交额不能为负"})
    prices = [x for x in (o, c, lo) if x is not None]
    if h is not None and prices and h < max(prices):
        local.append({"row": idx, "field": "high", "reason": "high 低于 open/close/low"})
    hp = [x for x in (o, c, h) if x is not None]
    if lo is not None and hp and lo > min(hp):
        local.append({"row": idx, "field": "low", "reason": "low 高于 open/close/high"})
    if local:
        errors.extend(local)
        return None
    vol_shares = None
    if vol is not None:
        s = vol * 100
        vol_shares = int(s.to_integral_value(rounding=ROUND_HALF_UP))
    amount_minor = int(amt_minor_raw) if amt_minor_raw is not None else (_fen(amt) if amt is not None else None)
    return {"open": o, "high": h, "low": lo, "close": c, "pre_close": pc,
            "change_ratio": (pct / 100) if pct is not None else None,
            "volume_shares": vol_shares, "amount_minor": amount_minor,
            "name": row.get("name") or "", "tradedate": str(row.get("tradedate") or "")}


def normalize_units_demo(raw: dict) -> dict:
    """AC-012：单位换算可追溯（手→股、元→分、百分数→ratio），原始值原样保留。"""
    errs: list = []
    n = normalize_daily_row(raw, 0, errs)
    if errs or n is None:
        raise RowsInvalid(errs)
    return {"volume_shares": n["volume_shares"], "amount_minor": str(n["amount_minor"]) if n["amount_minor"] is not None else None,
            "change_ratio": normalize_str(n["change_ratio"]) if n["change_ratio"] is not None else None,
            "raw": dict(raw)}


def validate_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    errors: list[dict] = []
    ok = []
    for i, r in enumerate(rows):
        n = normalize_daily_row(r, i, errors)
        if n is not None:
            ok.append(n)
    return ok, errors


# ── 时点、终结与新鲜度 ──
def session_close_utc(day: str, delay: timedelta = DAILY_PUBLICATION_DELAY) -> str:
    sh = datetime.combine(datetime.fromisoformat(day).date(), time(15, 0), tzinfo=clock.SHANGHAI) + delay
    return clock.utc_iso(sh)


def provider_finality(session_date: str, captured_at: str, calendar: TradingCalendar | None) -> tuple[str | None, str]:
    """daily_finality_policy_v1 的实现。"""
    cap = clock.parse_utc(captured_at)
    cap_day = clock.shanghai_date(cap)
    if session_date < cap_day:
        return "final_confirmed", f"{FINALITY_POLICY_VERSION}: 会话日早于抓取日（沉淀日线）"
    if session_date == cap_day:
        phase = calendar.phase(cap) if calendar else "unknown"
        if phase in ("pre_open", "continuous", "midday"):
            return "not_final", f"{FINALITY_POLICY_VERSION}: 当日仍在交易（{phase}），日线未终结"
        return None, f"{FINALITY_POLICY_VERSION}: 当日已收盘但供应商未提供终结标志，次日重取后才算 final"
    return None, f"{FINALITY_POLICY_VERSION}: 抓取时间早于会话日，无法判定"


def bar_status_of(finality: str | None) -> str:
    return {"final_confirmed": "final", "not_final": "partial"}.get(finality or "", "unknown")


def classify_freshness(calendar: TradingCalendar | None, quote_date: str | None, now: datetime | None = None
                       ) -> tuple[str, str]:
    """按交易日历与发布预期判断「此刻最新」（freshness_policy_v1）；不比较自然日、不看文件写入时间。"""
    if calendar is None:
        return "unknown", "calendar_unavailable：交易日历不可用，无法判断是否落后"
    if quote_date is None:
        return "unknown", "quote_date_unknown：没有可靠的报价日"
    expected = calendar.latest_completed_session(now)
    if expected is None:
        return "unknown", f"calendar_does_not_cover_today：日历 {calendar.calendar_version} 不覆盖今天"
    if quote_date > expected:
        return "fresh", f"intraday_partial：{quote_date} 仍在交易或尚未终结；最近完整交易日为 {expected}"
    if quote_date == expected:
        return "fresh", f"latest_expected_completed_session：{quote_date}（{FRESHNESS_POLICY_VERSION}）"
    behind = calendar.sessions_behind(quote_date, expected)
    tail = f"，落后 {behind} 个交易日" if behind else ""
    return "stale", f"expected_session_missing：应有 {expected}，实际 {quote_date}{tail}（{FRESHNESS_POLICY_VERSION}）"


def feed_precision_of(bar_status: str, session_date: str, captured_at: str) -> str:
    if bar_status == "final":
        return "daily_bar"
    cap_day = clock.shanghai_date(clock.parse_utc(captured_at))
    if bar_status == "partial" and session_date == cap_day:
        return "intraday_snapshot"
    return "date_snapshot"


# ── 构建 ──
def _dstr(d: Decimal | None) -> str | None:
    if d is None:
        return None
    s = format(d, "f")
    if s.startswith("-"):
        return None         # 价格字段不接受负数（已在校验中拒收）
    return s


def _limit_price(pre_close: Decimal, ratio: Decimal, up: bool) -> Decimal:
    f = (1 + ratio) if up else (1 - ratio)
    return (pre_close * f).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


_LIMIT_RATIO = {"SH_MAIN": Decimal("0.10"), "SZ_MAIN": Decimal("0.10"), "CHINEXT": Decimal("0.20"),
                "STAR": Decimal("0.20"), "BSE": Decimal("0.30")}


def derive_limits(board: str, is_st: bool | None, pre_close: Decimal | None) -> tuple[Decimal, Decimal] | None:
    if pre_close is None or board not in _LIMIT_RATIO:
        return None
    r = _LIMIT_RATIO[board]
    if board in ("SH_MAIN", "SZ_MAIN") and is_st:
        r = Decimal("0.05")
    return _limit_price(pre_close, r, True), _limit_price(pre_close, r, False)


def _iso(d: str) -> str:
    d = str(d)
    return f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 and d.isdigit() else d


@dataclass
class BuildResult:
    payload: dict                      # 无 snapshot_id / fingerprint；evidence.snapshot_id 待填
    identity: str
    warnings: list[str]
    invalid_rows: list[dict]
    universes: dict
    evidence_roles: dict               # role(+date) → evidence_id
    seat_inputs: dict                  # 供席位运行器使用的数据集清单（role → sha256 / 格式）
    anomalies: dict


def _identity(inp: SnapshotInputs) -> str:
    ds = []
    for d in [inp.session_daily, inp.stockbasic, inp.st, inp.suspend, inp.pricelimit, inp.valuation,
              inp.limit_events, inp.index_daily, *inp.history, *inp.derived]:
        if d is not None:
            ds.append([d.role, d.session_date, d.raw_sha256])
    return content_hash({"transform": TRANSFORM_VERSION, "metric_dict": METRIC_DICTIONARY_VERSION,
                         "source_mode": inp.source_mode, "session_date": inp.session_date,
                         "symbols": sorted(inp.symbols), "datasets": ds, "replay_id": inp.replay_id,
                         "calendar": inp.calendar.calendar_version if inp.calendar else None,
                         "finality": inp.finality, "explicit": inp.explicit_evidence,
                         "corporate_actions": inp.corporate_actions,
                         "price_basis": inp.price_basis, "adjustment": inp.adjustment_policy_version,
                         "freshness_override": list(inp.freshness_override) if inp.freshness_override else None})


@bounded_memory
def build(inp: SnapshotInputs, now: datetime | None = None) -> BuildResult:
    now = now or clock.now()
    identity = _identity(inp)
    warnings = list(inp.warnings)
    missing: list[str] = []
    D = inp.session_date
    ev: dict[str, dict] = {}          # key → evidence object（snapshot_id 先置空，入库前补）
    ev_ids: dict[str, str] = {}

    def add_dataset_ev(key: str, ds: Dataset, field_path: str, *, available_at=None, precision="exact",
                       basis="recorded_observation", unit="json", quality="unverified", value=None):
        eid = derived_id("ev", identity, key)
        ev_ids[key] = eid
        ev[key] = {
            "evidence_id": eid, "kind": "api_fact" if ds.authority_type in ("vendor", "exchange", "issuer") else
            ("user_statement" if ds.authority_type == "user" else "api_fact"),
            "snapshot_id": None, "symbol": None, "observed_at": None,
            "available_at": available_at, "first_observed_at": ds.first_observed_at or ds.captured_at,
            "time_precision": precision, "availability_basis": basis, "captured_at": ds.captured_at,
            "source_ref": {"source_id": ds.source_id, "authority_type": ds.authority_type,
                           "locator": f"evidence:{ds.raw_sha256}", "content_sha256": ds.raw_sha256,
                           "retrieved_at": ds.captured_at, "published_at": None,
                           "citation": ds.operation_id or ds.note or None},
            "field_path": field_path,
            "value": value if value is not None else {"row_count": len(ds.rows), "operation_id": ds.operation_id,
                                                      "params": ds.params or {}, "raw_format": ds.raw_format,
                                                      "reused_cache": ds.reused, "note": ds.note},
            "unit": unit, "quality": quality, "related_evidence_ids": [],
        }
        return eid

    def add_rule_ev(key: str, value, field_path: str, related: list[str] | None = None, citation: str | None = None):
        eid = derived_id("ev", identity, key)
        ev_ids[key] = eid
        ev[key] = {
            "evidence_id": eid, "kind": "rule_derivation", "snapshot_id": None, "symbol": None,
            "observed_at": None, "available_at": None, "first_observed_at": clock.utc_iso(now),
            "time_precision": "unknown", "availability_basis": "unknown", "captured_at": clock.utc_iso(now),
            "source_ref": {"source_id": "roundtable_rules", "authority_type": "synthetic" if inp.is_synthetic else "user",
                           "locator": None, "content_sha256": content_hash(value), "retrieved_at": clock.utc_iso(now),
                           "published_at": None, "citation": citation},
            "field_path": field_path, "value": value, "unit": "json", "quality": "verified",
            "related_evidence_ids": related or [],
        }
        return eid

    # ── 证券身份 ──
    identity_map: dict[str, dict] = {}
    if inp.stockbasic:
        for r in inp.stockbasic.rows:
            sym = tr.to_symbol(str(r.get("code") or r.get("symbol") or ""))
            if not sym:
                continue
            identity_map[sym] = r
        add_dataset_ev("stockbasic", inp.stockbasic, "instruments[*].board",
                       precision="exact", basis="recorded_observation",
                       value={"row_count": len(inp.stockbasic.rows), "operation_id": inp.stockbasic.operation_id,
                              "note": "stockbasic.market 为当前分类，非历史时点分类"})
    else:
        missing.append("stockbasic")
        warnings.append("identity_fallback_code_prefix：无 stockbasic，板块按代码段受控 fallback 判定")
    st_set: set[str] | None = None
    if inp.st:
        st_set = set()
        for r in inp.st.rows:
            if _iso(str(r.get("tradedate") or "")) == D:
                s = tr.to_symbol(str(r.get("symbol") or ""))
                if s:
                    st_set.add(s)
        add_dataset_ev("st", inp.st, "instruments[*].is_st", precision="date_only",
                       available_at=session_close_utc(D), basis="authoritative_publication")
    else:
        missing.append("st_status")
    suspended: set[str] | None = None
    if inp.suspend:
        suspended = set()
        for r in inp.suspend.rows:
            if _iso(str(r.get("tradedate") or "")) == D and str(r.get("suspend_type") or "") in ("S", "停牌"):
                s = tr.to_symbol(str(r.get("symbol") or ""))
                if s:
                    suspended.add(s)
        add_dataset_ev("suspend", inp.suspend, "instruments[*].suspended", precision="date_only",
                       available_at=session_close_utc(D), basis="authoritative_publication")
    else:
        missing.append("suspend_status")
    limits: dict[str, tuple[Decimal, Decimal]] = {}
    if inp.pricelimit:
        for r in inp.pricelimit.rows:
            s = tr.to_symbol(str(r.get("symbol") or ""))
            up, dn = _raw_num(r, "up_limit", [], 0), _raw_num(r, "down_limit", [], 0)
            if s and up is not None and dn is not None and _iso(str(r.get("tradedate") or D)) == D:
                limits[s] = (up, dn)
        add_dataset_ev("pricelimit", inp.pricelimit, "instruments[*].upper_limit_decimal", precision="date_only",
                       available_at=session_close_utc(D), basis="authoritative_publication")
    else:
        missing.append("price_limit_vendor")

    # ── 当日日线 ──
    bars: dict[str, dict] = {}
    invalid: list[dict] = []
    bar_status = bar_status_of(inp.finality)
    if inp.session_daily:
        sd = inp.session_daily
        avail = session_close_utc(D) if bar_status == "final" else sd.captured_at
        prec = "date_only" if bar_status == "final" else "exact"
        basis = "authoritative_publication" if bar_status == "final" else "recorded_observation"
        add_dataset_ev("daily_session", sd, "daily_bars[*]", available_at=avail, precision=prec, basis=basis,
                       quality="verified" if bar_status == "final" else "unverified")
        for i, r in enumerate(sd.rows):
            sym = tr.to_symbol(str(r.get("symbol") or r.get("code") or ""))
            if not sym:
                continue
            if str(r.get("tradedate") or D).replace("-", "") != D.replace("-", ""):
                continue
            n = normalize_daily_row(r, i, invalid)
            if n is not None:
                bars[sym] = n
        if invalid:
            missing.append(f"invalid_rows:{len(invalid)}")
            warnings.append(f"invalid_rows_excluded：{len(invalid)} 行非法数据被拒收（未修正、未补值）")
        # 字段级缺失：某些行没有成交额/成交量 → 记缺失字段名（值保持 null，不补 0）
        for fname, key in (("amount", "amount_minor"), ("volume", "volume_shares")):
            n_missing = sum(1 for b in bars.values() if b[key] is None)
            if n_missing:
                missing.append(fname)
                warnings.append(f"field_missing：{n_missing} 只股票缺 {fname}（保持 null，不补 0）")
    else:
        missing.append("daily_bars")
    if bar_status != "final":
        missing.append("final_daily_bars")
        if inp.finality is None:
            warnings.append("finality_unverified：来源未确认日线终结状态，bar_status=unknown")
    # 质量异常线索（不改变 bar_status）
    anomalies = dict(inp.anomaly)
    if inp.history and bars:
        prev = inp.history[-1]
        def _active(rows):
            n_ = 0
            for rr in rows:
                a = _raw_num(rr, "amount", [], 0)
                if a is not None and a >= Decimal("100000000"):
                    n_ += 1
            return n_
        today_n = sum(1 for b in bars.values() if b["amount_minor"] is not None and b["amount_minor"] >= 10_000_000_000)
        prev_n = _active(prev.rows)
        if prev_n:
            ratio = Decimal(today_n) / Decimal(prev_n)
            anomalies["coverage_above_1e8_ratio_to_prev"] = normalize_str(ratio.quantize(Decimal("0.0001")))
            if ratio < LOW_AMOUNT_ANOMALY_RATIO:
                anomalies["low_amount_anomaly"] = True
                warnings.append(f"low_amount_anomaly：成交额 ≥1 亿家数只有前日的 {ratio:.2f}，仅作质量线索，不据此判断收盘与否")

    # ── 历史窗口（席位输入与指标） ──
    hist_bars = _DiskHistory()
    for hd in inp.history:
        day = hd.session_date
        key = f"daily_history:{day}"
        add_dataset_ev(key, hd, f"seat_inputs.daily_history[{day}]", available_at=session_close_utc(day),
                       precision="date_only", basis="authoritative_publication", quality="verified")
        m: dict[str, dict] = {}
        errs: list = []
        for i, r in enumerate(hd.rows):
            sym = tr.to_symbol(str(r.get("symbol") or ""))
            if not sym:
                continue
            n = normalize_daily_row(r, i, errs)
            if n is not None:
                m[sym] = n
        hist_bars.add(day, [{"symbol": sym, **bar} for sym, bar in m.items()])
    for d in inp.derived:
        add_dataset_ev(f"{d.role}", d, f"seat_inputs.{d.role}", quality="unverified" if "current" in d.role else "verified",
                       precision="unknown" if "current" in d.role else "date_only",
                       available_at=None if "current" in d.role else session_close_utc(D),
                       basis="unknown" if "current" in d.role else "authoritative_publication")
    if inp.valuation:
        add_dataset_ev("valuation", inp.valuation, "seat_inputs.valuation", available_at=session_close_utc(D),
                       precision="date_only", basis="authoritative_publication")
    else:
        missing.append("valuation")
    if inp.limit_events:
        add_dataset_ev("limit_events", inp.limit_events, "seat_inputs.limit_events", precision="exact",
                       available_at=session_close_utc(D), basis="recorded_observation")
    else:
        missing.append("limit_events")
    if inp.index_daily:
        add_dataset_ev("index_daily", inp.index_daily, "metrics.index_trend", available_at=session_close_utc(D),
                       precision="date_only", basis="authoritative_publication")
    else:
        missing.append("index_daily")

    # ── 成员与股票池 ──
    members: list[uv.Member] = []
    names: dict[str, str] = {}
    all_syms = set(bars) | set(identity_map)
    for sym in sorted(all_syms):
        idr = identity_map.get(sym)
        name = (bars.get(sym) or {}).get("name") or (idr or {}).get("name") or ""
        names[sym] = name
        if idr is not None:
            br = tr.classify_board(sym, idr.get("market"), name)
            ld, dd = str(idr.get("list_date") or ""), str(idr.get("delist_date") or "")
            listed = (not ld or _iso(ld) <= D) and (not dd or dd in ("None", "null") or _iso(dd) > D)
            if str(idr.get("list_status") or "L") == "D" and (not dd or _iso(dd) <= D):
                listed = False
        else:
            br = tr.classify_by_code_prefix(sym)
            listed = True if sym in bars else None
        is_st = (sym in st_set) if st_set is not None else (tr.is_st_name(name) if name else None)
        members.append(uv.Member(sym, br.board, br.category, br.raw_market, is_st, listed, name, br.source))
    env = uv.build(uv.ENVIRONMENT_POLICY, D, members)
    cand = uv.build(uv.CANDIDATE_POLICY, D, members)
    member_by_sym = {m.symbol: m for m in members}
    env_set, cand_set = set(env.eligible), set(cand.eligible)
    for u in (env, cand):
        add_rule_ev(f"universe:{u.policy_id}", {**u.summary(), "policy": uv.POLICIES[u.policy_id],
                                                "exclusion_reason_counts": _count(u.excluded.values())},
                    f"metadata.universe[{u.policy_id}]", citation="universe policy")
    for pid, text in POLICY_TEXT.items():
        add_rule_ev(f"policy:{pid}", {"policy_id": pid, "text": text}, f"policies.{pid}", citation=pid)
    if inp.finality is not None or inp.finality_basis:
        add_rule_ev("finality_decision", {"finality": inp.finality, "basis": inp.finality_basis,
                                          "policy": FINALITY_POLICY_VERSION}, "metadata.bar_status")

    # ── InstrumentQuote ──
    instruments = []
    include = sorted(env_set | cand_set | {s for s in inp.symbols if s in member_by_sym})
    derived_limit_used = 0
    for sym in include:
        m = member_by_sym[sym]
        b = bars.get(sym)
        up_dn = limits.get(sym)
        lim_ev = ev_ids.get("pricelimit")
        if up_dn is None and b is not None:
            dl = derive_limits(m.board, m.is_st, b["pre_close"])
            if dl:
                up_dn = dl
                derived_limit_used += 1
                lim_ev = ev_ids.get(f"policy:{PRICE_LIMIT_DERIVATION}")
        susp = (sym in suspended) if suspended is not None else (None if b is None else False)
        if b is None and suspended is not None and sym not in suspended:
            susp = None
        eids = [e for e in (ev_ids.get("stockbasic"), ev_ids.get("st"), ev_ids.get("suspend"), lim_ev,
                            ev_ids.get("daily_session")) if e]
        instruments.append({
            "symbol": sym, "name": m.name or sym, "board": m.board,
            "lot_size": tr.lot_size(m.board), "is_st": m.is_st,
            "listed_date": _iso(str(identity_map[sym].get("list_date"))) if sym in identity_map and identity_map[sym].get("list_date") else None,
            "suspended": susp, "price_limit_rule_id": tr.price_limit_rule_id(m.board, m.is_st),
            "last_price_decimal": _dstr(b["close"]) if b else None,
            "prev_close_decimal": _dstr(b["pre_close"]) if b else None,
            "upper_limit_decimal": _dstr(up_dn[0]) if up_dn else None,
            "lower_limit_decimal": _dstr(up_dn[1]) if up_dn else None,
            "quote_observed_at": (session_close_utc(D, timedelta(0)) if bar_status == "final" else
                                  (inp.session_daily.captured_at if inp.session_daily else None)) if b else None,
            "evidence_ids": eids,
        })
    if derived_limit_used:
        warnings.append(f"price_limit_derived：{derived_limit_used} 只股票的涨跌停价按 {PRICE_LIMIT_DERIVATION} 推导")

    # ── DailyBar（当日全体 + 请求股票的历史） ──
    daily_bars = []
    sev = ev_ids.get("daily_session")
    for sym in include:
        b = bars.get(sym)
        if b is None:
            continue
        daily_bars.append(_bar(sym, D, b, bar_status, inp, sev, session_close_utc(D) if bar_status == "final" else
                               (inp.session_daily.captured_at if inp.session_daily else None)))
    for sym in sorted(set(inp.symbols)):
        for day in sorted(hist_bars):
            hb = hist_bars[day].get(sym)
            if hb is not None:
                daily_bars.append(_bar(sym, day, hb, "final", inp, ev_ids.get(f"daily_history:{day}"),
                                       session_close_utc(day)))
    daily_bars.sort(key=lambda x: (x["symbol"], x["session_date"]))

    # ── 指标 ──
    metrics = compute_metrics(D, bars, hist_bars, env_set, cand_set, member_by_sym, limits, inp, ev_ids,
                              add_rule_ev, bar_status)

    # ── 冲突（同字段多来源不一致：保留证据，不取均值） ──
    conflicts = []
    if inp.valuation and bars:
        vclose = {}
        for r in inp.valuation.rows:
            s = tr.to_symbol(str(r.get("symbol") or ""))
            c = _raw_num(r, "close", [], 0)
            if s and c is not None:
                vclose[s] = c
        diff = sorted(s for s, c in vclose.items() if s in bars and bars[s]["close"] is not None and
                      abs(bars[s]["close"] - c) > Decimal("0.0001") and s in env_set)
        for s in diff[:50]:
            conflicts.append({"conflict_id": derived_id("conflict", identity, s), "field_path": f"daily_bars[{s}].close_decimal",
                              "evidence_ids": [ev_ids["daily_session"], ev_ids["valuation"]],
                              "resolution": "primary_source_selected", "selected_evidence_id": ev_ids["daily_session"],
                              "reason": f"日线收盘 {bars[s]['close']} 与估值接口收盘 {vclose[s]} 不一致；按事前优先级采用日线接口"})
        if len(diff) > 50:
            warnings.append(f"source_conflicts_truncated：{len(diff)} 处收盘价冲突，只列前 50 处")

    # ── 元数据 ──
    quote_date = D if bars else None
    ref_now = inp.reference_now or now
    if inp.freshness_override:
        freshness, freshness_reason = inp.freshness_override
    else:
        freshness, freshness_reason = classify_freshness(inp.calendar, quote_date, ref_now)
    if not bars:
        quality = "unavailable"
    elif bar_status == "final" and not missing:
        quality = "complete"
    else:
        quality = "partial"
    captured = inp.session_daily.captured_at if inp.session_daily else clock.utc_iso(now)
    source_ids = sorted({d.source_id for d in [inp.session_daily, inp.stockbasic, inp.st, inp.suspend,
                                               inp.pricelimit, inp.valuation, inp.limit_events, inp.index_daily,
                                               *inp.history, *inp.derived] if d is not None})
    metadata = {
        "quote_date": quote_date, "session_date": D, "captured_at": captured, "generated_at": clock.utc_iso(now),
        "latest_completed_session_date": inp.calendar.latest_completed_session(ref_now) if inp.calendar else None,
        "timezone": "Asia/Shanghai",
        "feed_precision": feed_precision_of(bar_status, D, captured) if bars else "unknown",
        "source_mode": inp.source_mode, "freshness": freshness, "freshness_reason": freshness_reason,
        "quality": quality, "missing_fields": sorted(set(missing)), "source_ids": source_ids,
        "universe_policy_id": uv.CANDIDATE_POLICY, "universe_fingerprint": cand.fingerprint,
        "price_basis": inp.price_basis, "adjustment_policy_version": inp.adjustment_policy_version,
        "calendar_version": inp.calendar.calendar_version if inp.calendar else "calendar_unavailable",
        "is_synthetic": inp.is_synthetic,
    }
    # 显式证据（fixture 事实、训练可见事实）按快照身份重新编号：证据 ID 全局唯一，同一事实进入不同快照是不同的证据行
    explicit = []
    for e in inp.explicit_evidence:
        e2 = dict(e)
        e2["evidence_id"] = derived_id("ev", identity, "explicit", e["evidence_id"])
        explicit.append(e2)
    evidence = list(ev.values()) + explicit
    payload = {"schema_version": SCHEMA_VERSION, "metadata": metadata, "instruments": instruments,
               "daily_bars": daily_bars, "metrics": metrics, "evidence": evidence, "conflicts": conflicts,
               "corporate_actions": list(inp.corporate_actions)}
    seat_inputs = {"history": [[d.session_date, d.raw_sha256, d.raw_format] for d in inp.history],
                   "session": [D, inp.session_daily.raw_sha256, inp.session_daily.raw_format] if inp.session_daily else None}
    for d in [inp.stockbasic, inp.valuation, inp.limit_events, inp.pricelimit, inp.st, inp.suspend, *inp.derived]:
        if d is not None:
            seat_inputs[d.role] = [d.raw_sha256, d.raw_format]
    return BuildResult(payload, identity, warnings, invalid, {"environment": env.summary(), "candidate": cand.summary()},
                       ev_ids, seat_inputs, anomalies)


def _count(values) -> dict:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _bar(sym, day, b, status, inp: SnapshotInputs, ev_id, available_at):
    return {"symbol": sym, "session_date": day,
            "open_decimal": _dstr(b["open"]), "high_decimal": _dstr(b["high"]), "low_decimal": _dstr(b["low"]),
            "close_decimal": _dstr(b["close"]), "volume_shares": b["volume_shares"],
            "amount_minor": str(b["amount_minor"]) if b["amount_minor"] is not None else None,
            "bar_status": status, "adjustment": "raw", "adjustment_as_of": None,
            "available_at": available_at, "evidence_ids": [ev_id] if ev_id else []}


# ── 市场矩阵指标（DATA-006） ──
def _ret(b: dict) -> Decimal | None:
    if b is None or b["close"] is None or b["pre_close"] is None or b["pre_close"] == 0:
        return None
    return b["close"] / b["pre_close"] - 1


def _r(d: Decimal | None) -> str | None:
    if d is None:
        return None
    return normalize_str(d.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP))


def compute_metrics(D, bars, hist_bars, env_set, cand_set, members, limits, inp: SnapshotInputs, ev_ids,
                    add_rule_ev, bar_status) -> list[dict]:
    out = []
    avail = session_close_utc(D) if bar_status == "final" else (inp.session_daily.captured_at if inp.session_daily else None)
    sev = ev_ids.get("daily_session")

    def put(mid, value, symbol=None, extra_ev=()):
        if mid not in METRICS:
            raise KeyError(mid)
        key = f"metricdef:{mid}"
        if key not in ev_ids:
            add_rule_ev(key, METRICS[mid], f"metric_dictionary.{mid}", citation=METRIC_DICTIONARY_VERSION)
        unit = METRICS[mid]["unit"]
        if isinstance(value, Decimal):
            v = _r(value) if unit in ("ratio", "multiple") else normalize_str(value)
        elif isinstance(value, int):
            v = str(value)
        else:
            v = value
        out.append({"metric_id": mid, "symbol": symbol, "value": v, "unit": unit,
                    "rule_version": METRIC_DICTIONARY_VERSION, "available_at": avail,
                    "evidence_ids": [e for e in (ev_ids[key], sev, *extra_ev) if e]})

    env_syms = sorted(env_set)
    quoted = [s for s in env_syms if _ret(bars.get(s)) is not None]
    rets = {s: _ret(bars[s]) for s in quoted}
    put("breadth_member_count", len(env_syms))
    put("breadth_quoted_count", len(quoted))
    put("breadth_quote_coverage", Decimal(len(quoted)) / Decimal(len(env_syms)) if env_syms else None)
    up = sum(1 for s in quoted if rets[s] > 0)
    dn = sum(1 for s in quoted if rets[s] < 0)
    put("breadth_up_count", up if quoted else None)
    put("breadth_down_count", dn if quoted else None)
    put("breadth_up_ratio", Decimal(up) / Decimal(len(quoted)) if quoted else None)
    put("breadth_median_pct_chg", Decimal(str(statistics.median(rets.values()))) if quoted else None)

    # 涨跌停（非 ST）
    def lim(sym):
        if sym in limits:
            return limits[sym]
        b = bars.get(sym)
        m = members.get(sym)
        return derive_limits(m.board, m.is_st, b["pre_close"]) if (b and m) else None

    have_limits = bool(limits) or bool(bars)
    lu, ld, broken, st_lu = [], [], [], []
    for s in env_syms:
        b = bars.get(s)
        if b is None or b["close"] is None:
            continue
        L = lim(s)
        if not L:
            continue
        m = members[s]
        if b["close"] == L[0]:
            (st_lu if m.is_st else lu).append(s)
        elif m.is_st is not True and b["high"] is not None and b["high"] == L[0] and b["close"] < L[0]:
            broken.append(s)
        if m.is_st is not True and b["close"] == L[1]:
            ld.append(s)
    lim_ev = (ev_ids.get("pricelimit"),)
    put("limit_up_count", len(lu) if have_limits else None, extra_ev=lim_ev)
    put("limit_down_count", len(ld) if have_limits else None, extra_ev=lim_ev)
    put("broken_board_count", len(broken) if have_limits else None, extra_ev=lim_ev)
    put("broken_board_ratio", Decimal(len(broken)) / Decimal(len(lu) + len(broken)) if (lu or broken) else None,
        extra_ev=lim_ev)
    put("st_limit_up_count", len(st_lu) if (have_limits and inp.st is not None) else None, extra_ev=(ev_ids.get("st"),))

    # 连板（连续交易日；缺 bar 即中断）与昨日涨停组
    days = sorted(hist_bars)
    hist_ev = tuple(ev_ids.get(f"daily_history:{d}") for d in days[-20:])

    def is_lu(day_bars, sym):
        b = day_bars.get(sym)
        if b is None or b["close"] is None or b["pre_close"] is None:
            return False
        m = members.get(sym)
        if m is None or m.is_st:
            return False
        L = derive_limits(m.board, tr.is_st_name(b["name"]) if b["name"] else m.is_st, b["pre_close"])
        return bool(L) and b["close"] == L[0]

    if days:
        streaks = {}
        for s in lu:
            n = 1
            for day in reversed(days[-19:]):
                if is_lu(hist_bars[day], s):
                    n += 1
                else:
                    break
            streaks[s] = n
        put("max_consecutive_boards", max(streaks.values()) if streaks else 0, extra_ev=hist_ev)
        put("consecutive_board_count", sum(1 for v in streaks.values() if v >= 2), extra_ev=hist_ev)
        prev_day = days[-1]
        prev_members = [s for s in env_syms if is_lu(hist_bars[prev_day], s)]
        prev_quoted = [s for s in prev_members if _ret(bars.get(s)) is not None]
        put("prev_limit_up_member_count", len(prev_members), extra_ev=(ev_ids.get(f"daily_history:{prev_day}"),))
        put("prev_limit_up_quoted_count", len(prev_quoted), extra_ev=(ev_ids.get(f"daily_history:{prev_day}"),))
        put("prev_limit_up_followthrough",
            (sum(rets[s] for s in prev_quoted) / Decimal(len(prev_quoted))) if prev_quoted else None,
            extra_ev=(ev_ids.get(f"daily_history:{prev_day}"),))
        # 成交额相对 20 日均值
        today_amt = sum(bars[s]["amount_minor"] for s in env_syms if bars.get(s) and bars[s]["amount_minor"] is not None)
        past = []
        for day in days[-20:]:
            tot = sum(hb["amount_minor"] for sym_, hb in hist_bars[day].items()
                      if sym_ in env_set and hb["amount_minor"] is not None)
            if tot:
                past.append(tot)
        put("amount_vs_20d_avg", (Decimal(today_amt) / (Decimal(sum(past)) / len(past))) if past and today_amt else None,
            extra_ev=hist_ev)
    else:
        for mid in ("max_consecutive_boards", "consecutive_board_count", "prev_limit_up_member_count",
                    "prev_limit_up_quoted_count", "prev_limit_up_followthrough", "amount_vs_20d_avg"):
            put(mid, None)

    # 题材扩散（行业代理）
    industry = {}
    if inp.stockbasic:
        for r in inp.stockbasic.rows:
            s = tr.to_symbol(str(r.get("code") or r.get("symbol") or ""))
            if s and r.get("industry"):
                industry[s] = str(r["industry"])
    if industry and have_limits:
        by_ind: dict[str, int] = {}
        for s in lu:
            if s in industry:
                by_ind[industry[s]] = by_ind.get(industry[s], 0) + 1
        put("limit_up_industry_count", len(by_ind), extra_ev=(ev_ids.get("stockbasic"),))
        if by_ind and lu:
            top = max(sorted(by_ind), key=lambda k: by_ind[k])
            put("top_industry_limit_up_share", Decimal(by_ind[top]) / Decimal(len(lu)), extra_ev=(ev_ids.get("stockbasic"),))
            mem = [s for s in quoted if industry.get(s) == top]
            put("top_industry_member_up_ratio",
                Decimal(sum(1 for s in mem if rets[s] > 0)) / Decimal(len(mem)) if mem else None,
                extra_ev=(ev_ids.get("stockbasic"),))
        else:
            put("top_industry_limit_up_share", None)
            put("top_industry_member_up_ratio", None)
    else:
        for mid in ("limit_up_industry_count", "top_industry_limit_up_share", "top_industry_member_up_ratio"):
            put(mid, None)

    # 流动性 / 波动
    amts = [bars[s]["amount_minor"] for s in env_syms if bars.get(s) and bars[s]["amount_minor"] is not None]
    put("total_amount_minor", sum(amts) if amts else None)
    put("active_amount_count", sum(1 for a in amts if a >= 10_000_000_000) if amts else None)
    if quoted:
        absr = [abs(rets[s]) for s in quoted]
        put("abs_pct_chg_median", Decimal(str(statistics.median(absr))))
        put("big_move_share", Decimal(sum(1 for a in absr if a >= Decimal("0.05"))) / Decimal(len(quoted)))
    else:
        put("abs_pct_chg_median", None)
        put("big_move_share", None)
    if inp.valuation:
        tov = {}
        for r in inp.valuation.rows:
            s = tr.to_symbol(str(r.get("symbol") or ""))
            t = _raw_num(r, "turnover_rate", [], 0)
            if s in env_set and t is not None:
                tov[s] = t
        put("high_turnover_share", Decimal(sum(1 for t in tov.values() if t >= 10)) / Decimal(len(tov)) if tov else None,
            extra_ev=(ev_ids.get("valuation"),))
    else:
        put("high_turnover_share", None)
    cq = [s for s in cand_set if _ret(bars.get(s)) is not None]
    put("candidate_member_count", len(cand_set))
    put("candidate_quoted_count", len(cq))

    # 指数趋势
    if inp.index_daily:
        series: dict[str, list[dict]] = {}
        for r in inp.index_daily.rows:
            s = str(r.get("symbol") or r.get("code") or "").split(".")[0]   # 供应商历史行返回 6 位指数代码
            if s:
                series.setdefault(s, []).append(r)
        for code, _name in INDICES:
            rows = sorted(series.get(code.split(".")[0], []), key=lambda x: str(x.get("tradedate")))
            rows = [x for x in rows if _iso(str(x.get("tradedate"))) <= D]
            if not rows or _iso(str(rows[-1].get("tradedate"))) != D:
                for mid in ("index_pct_chg", "index_ret_20", "index_close_to_ma20"):
                    put(mid, None, symbol=code if _symbol_ok(code) else None, extra_ev=(ev_ids.get("index_daily"),))
                continue
            closes = [_raw_num(x, "close", [], 0) for x in rows]
            last, pre = closes[-1], _raw_num(rows[-1], "pre_close", [], 0)
            sym = code if _symbol_ok(code) else None
            put("index_pct_chg", (last / pre - 1) if (last is not None and pre) else None, symbol=sym,
                extra_ev=(ev_ids.get("index_daily"),))
            put("index_ret_20", (last / closes[-21] - 1) if len(closes) >= 21 and closes[-21] else None, symbol=sym,
                extra_ev=(ev_ids.get("index_daily"),))
            w = [c for c in closes[-20:] if c is not None]
            put("index_close_to_ma20", (last / (sum(w) / len(w))) if len(w) == 20 and last is not None else None,
                symbol=sym, extra_ev=(ev_ids.get("index_daily"),))
    else:
        for code, _ in INDICES:
            for mid in ("index_pct_chg", "index_ret_20", "index_close_to_ma20"):
                put(mid, None, symbol=code if _symbol_ok(code) else None)
    return out


def _symbol_ok(code: str) -> bool:
    import re
    return bool(re.match(r"^[0-9]{6}\.(SH|SZ|BJ)$", code))
