"""交易日历的存取与选择（DATA-002）。

日历版本不可变地存进 calendar_versions；快照、回放、模拟成交都引用 calendar_version。
「当前日历」= 覆盖今天的最新非合成供应商日历；都没有时返回 None，调用方必须按 unknown 处理。
验收与测试可用 set_override() 注入受控 fixture 日历或模拟「日历不可用」。
"""
from __future__ import annotations

import json
import threading

from ..domain import calendar as cal_mod
from ..domain import clock
from ..domain.calendar import TradingCalendar
from ..domain.canonical import canonical_json, content_hash, sha256_hex

_lock = threading.Lock()
_override: dict | None = None      # {"calendar": TradingCalendar|None, "unavailable": bool}


def set_override(calendar: TradingCalendar | None = None, unavailable: bool = False) -> None:
    global _override
    with _lock:
        _override = None if (calendar is None and not unavailable) else {"calendar": calendar, "unavailable": unavailable}


def _payload(cal: TradingCalendar) -> dict:
    return {"calendar_version": cal.calendar_version, "source": cal.source,
            "coverage_start": cal.coverage_start, "coverage_end": cal.coverage_end,
            "open_sessions": sorted(cal.open_sessions), "closed_sessions": sorted(cal.closed_sessions),
            "windows": [[a.strftime("%H:%M:%S"), b.strftime("%H:%M:%S")] for a, b in cal.windows],
            "is_synthetic": cal.is_synthetic, "explicit_closed_only": cal.explicit_closed_only}


def store(db, cal: TradingCalendar) -> None:
    p = _payload(cal)
    with db.txn() as c:
        if c.execute("SELECT 1 FROM calendar_versions WHERE calendar_version=?", (cal.calendar_version,)).fetchone():
            return
        c.execute("""INSERT INTO calendar_versions(calendar_version, source, coverage_start, coverage_end, is_synthetic,
                     explicit_closed_only, content_sha256, created_at, canonical_payload) VALUES(?,?,?,?,?,?,?,?,?)""",
                  (cal.calendar_version, cal.source, cal.coverage_start, cal.coverage_end, int(cal.is_synthetic),
                   int(cal.explicit_closed_only), content_hash(p), clock.utc_iso(), canonical_json(p)))


def _from_row(r) -> TradingCalendar:
    from datetime import time
    p = json.loads(r["canonical_payload"])
    return TradingCalendar(calendar_version=p["calendar_version"], open_sessions=frozenset(p["open_sessions"]),
                           closed_sessions=frozenset(p.get("closed_sessions") or []),
                           coverage_start=p["coverage_start"], coverage_end=p["coverage_end"],
                           windows=tuple((time.fromisoformat(a), time.fromisoformat(b)) for a, b in p["windows"]),
                           is_synthetic=bool(p["is_synthetic"]), source=p["source"],
                           explicit_closed_only=bool(p.get("explicit_closed_only")))


def load(db, version: str) -> TradingCalendar | None:
    with _lock:
        ov = _override
    if ov and ov.get("unavailable"):
        return None          # 日历服务整体不可用：任何版本都取不到，调用方按 unknown 处理
    if ov and ov.get("calendar") is not None and ov["calendar"].calendar_version == version:
        return ov["calendar"]
    r = db.one("SELECT * FROM calendar_versions WHERE calendar_version=?", (version,))
    return _from_row(r) if r else None


def current(db) -> TradingCalendar | None:
    with _lock:
        ov = _override
    if ov:
        return None if ov["unavailable"] else ov["calendar"]
    today = clock.shanghai_date()
    # 供应商日历（覆盖区间内未列出＝休市）优先于「归档里出现过的日期」推出的保守日历（未列出＝未知）
    r = db.one("""SELECT * FROM calendar_versions WHERE is_synthetic=0 AND coverage_start<=? AND coverage_end>=?
                  ORDER BY explicit_closed_only ASC, created_at DESC LIMIT 1""", (today, today))
    if r:
        return _from_row(r)
    return None


def latest_any(db) -> TradingCalendar | None:
    with _lock:
        ov = _override
    if ov:
        return None if ov["unavailable"] else ov["calendar"]
    r = db.one("SELECT * FROM calendar_versions WHERE is_synthetic=0 ORDER BY coverage_end DESC, created_at DESC LIMIT 1")
    return _from_row(r) if r else None


def from_provider(open_days: list[str], start: str, end: str, raw_sha256: str) -> TradingCalendar:
    version = f"numcat_tradecal_{cal_mod.compact(cal_mod._iso(end))}_{raw_sha256[:12]}"
    return cal_mod.from_open_days(version, open_days, start, end, source="numcat:reference_calendar_range")


def from_provider_rows(rows: list[dict], requested_start: str, requested_end: str, raw_sha256: str
                       ) -> TradingCalendar | None:
    """供应商日历行 → TradingCalendar。

    覆盖范围取供应商**实际返回**的日期区间（与请求区间取交集），而不是请求的区间：
    供应商没返回的日子是「不知道」，不能当成「休市」——否则今天还没发布时会被误判成休市日。
    行里带 is_open（end_latest=false 时含休市日与未来日），只有 is_open 为真的日子算开市。
    """
    dated: list[str] = []
    opens: list[str] = []
    for r in rows or []:
        d = str(r.get("cal_date") or r.get("tradedate") or "").strip()
        if not d:
            continue
        iso = cal_mod._iso(d)
        dated.append(iso)
        if str(r.get("is_open", "1")).strip().lower() in ("1", "true"):
            opens.append(iso)
    if not dated:
        return None
    lo = max(min(dated), cal_mod._iso(requested_start))
    hi = min(max(dated), cal_mod._iso(requested_end))
    if lo > hi:
        return None
    version = f"numcat_tradecal_{cal_mod.compact(hi)}_{raw_sha256[:12]}"
    return cal_mod.from_open_days(version, [d for d in opens if lo <= d <= hi], lo, hi,
                                  source="numcat:reference_calendar_range")


def from_legacy_cache(days: list[str], raw: bytes) -> TradingCalendar:
    iso = sorted(cal_mod._iso(d) for d in days)
    version = f"legacy_tradecal_cache_{sha256_hex(raw)[:12]}"
    return cal_mod.from_open_days(version, iso, iso[0], iso[-1], source="legacy panel/data/trade_calendar.json")
