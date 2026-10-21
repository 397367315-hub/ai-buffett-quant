"""可注入时钟。

服务端所有「现在」都从这里取：交易时段判断、新鲜度、冻结时刻、事件 recorded_at。
测试与验收 runner 可以 set_fixed() 冻结时钟；生产 HTTP 不暴露任何改钟接口。
浏览器的 Date.now 一律不作为业务时钟。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")   # 中国无夏令时，固定 +08:00

_lock = threading.Lock()
_fixed: datetime | None = None
_offset: timedelta = timedelta(0)


def now() -> datetime:
    with _lock:
        if _fixed is not None:
            return _fixed
        return datetime.now(UTC) + _offset


def set_fixed(dt: datetime | str | None) -> None:
    """冻结时钟（None 解除）。字符串按 RFC3339 解析。"""
    global _fixed
    with _lock:
        if dt is None:
            _fixed = None
            return
        if isinstance(dt, str):
            dt = parse_utc(dt)
        if dt.tzinfo is None:
            raise ValueError("冻结时钟必须带时区")
        _fixed = dt.astimezone(UTC)


def advance(seconds: float) -> None:
    global _fixed, _offset
    with _lock:
        if _fixed is not None:
            _fixed = _fixed + timedelta(seconds=seconds)
        else:
            _offset = _offset + timedelta(seconds=seconds)


def utc_iso(dt: datetime | None = None) -> str:
    """UTC RFC3339，以 Z 结尾，秒精度。"""
    d = (dt or now()).astimezone(UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_iso_ms(dt: datetime | None = None) -> str:
    d = (dt or now()).astimezone(UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def parse_utc(text: str) -> datetime:
    if not isinstance(text, str) or not text:
        raise ValueError("时间必须是 RFC3339 字符串")
    d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError(f"时间缺少时区：{text}")
    return d.astimezone(UTC)


def shanghai(dt: datetime | None = None) -> datetime:
    return (dt or now()).astimezone(SHANGHAI)


def shanghai_date(dt: datetime | None = None) -> str:
    """上海会话日（YYYY-MM-DD）。不能由 UTC 日期截断得到。"""
    return shanghai(dt).strftime("%Y-%m-%d")
