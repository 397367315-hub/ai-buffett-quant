"""只读历史日线归档（file_import / 回放 / 验证共用）。

- 归档根目录只能由服务端配置（Settings.file_import_roots），客户端不能传路径；
- 指纹 = sha256(canonical-json-v1([[日期, 字节数, sha256], ...]))：回放与验证用它冻结「用的是哪份数据」；
- 日历 = 归档里真实出现的交易日（显式列表，范围外 unknown），版本号带指纹前缀；
- 归档是事后下载的历史数据：只把日线 OHLC 按 daily_bar_availability_policy_v1（收盘后 30 分钟、date_only）
  当作可知；证券身份、行业、估值、分红等「当前表」不进入严格回放。
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from ..config import get_settings
from ..domain import calendar as cal_mod
from ..domain.canonical import content_hash

_lock = threading.Lock()
_cache: dict = {}


def _scan(roots: tuple[Path, ...]) -> dict:
    files: dict[str, Path] = {}
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for p in sorted(root.glob("*.json")):
            if p.stem.isdigit() and len(p.stem) == 8:
                d = cal_mod._iso(p.stem)
                files.setdefault(d, p)        # 先配置的根目录优先
    return files


def manifest() -> dict:
    roots = tuple(get_settings().file_import_roots)
    files = _scan(roots)
    sig = tuple((d, str(p), p.stat().st_size, p.stat().st_mtime_ns) for d, p in sorted(files.items()))
    with _lock:
        hit = _cache.get(roots)
        if hit and hit["sig"] == sig:
            return hit["m"]
    items = []
    for d, p in sorted(files.items()):
        items.append([d, p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest()])
    fp = content_hash(items)
    days = sorted(files)
    m = {"fingerprint": fp, "files": {d: str(p) for d, p in files.items()}, "items": items,
         "coverage_start": days[0] if days else None, "coverage_end": days[-1] if days else None,
         "session_count": len(days), "calendar_version": f"archive_calendar_{fp[:12]}"}
    with _lock:
        _cache[roots] = {"sig": sig, "m": m}
    return m


def calendar() -> cal_mod.TradingCalendar | None:
    m = manifest()
    if not m["files"]:
        return None
    days = frozenset(m["files"])
    return cal_mod.TradingCalendar(calendar_version=m["calendar_version"], open_sessions=days,
                                   coverage_start=m["coverage_start"], coverage_end=m["coverage_end"],
                                   source="file_import archive dates", explicit_closed_only=True)


def day_path(day: str) -> Path | None:
    p = manifest()["files"].get(day)
    return Path(p) if p else None


def read_day(day: str) -> tuple[bytes, list[dict]] | None:
    p = day_path(day)
    if p is None:
        return None
    raw = p.read_bytes()
    return raw, json.loads(raw)
