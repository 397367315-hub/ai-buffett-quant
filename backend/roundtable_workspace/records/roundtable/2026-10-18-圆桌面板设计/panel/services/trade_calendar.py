"""交易日历（DATA-002）：判断「今天是交易日吗 / 今天收盘了吗」。

为什么不靠星期几：法定假日（春节、国庆、清明等）经常落在工作日，纯按 weekday 判会把
「假日的 15:00 之后」当成「今天已收盘」，从而把昨天的数据当成今天的。

日历来源：refresh.py 调 `reference_calendar_range`（tradecal）时顺手落盘，
文件 `panel/data/trade_calendar.json`。日历缺失时 **不猜**：返回 None，
调用方退回「按数据里真实出现的日期判断」的保守逻辑。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
CACHE = DATA / "trade_calendar.json"

SESSION_CLOSE_HOUR, SESSION_CLOSE_MIN = 15, 0


def load(path: Path | None = None) -> list[str] | None:
    """返回升序交易日列表（YYYYMMDD）；文件缺失或损坏返回 None。"""
    f = path or CACHE
    try:
        d = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    days = d.get("days") if isinstance(d, dict) else d
    if not isinstance(days, list) or not days:
        return None
    try:
        return sorted({str(x) for x in days})
    except Exception:
        return None


def is_trading_day(day: str, days: list[str] | None) -> bool | None:
    """None = 日历不认识这一天（超出缓存区间），调用方不得据此判断。"""
    if not days:
        return None
    return str(day) in set(days)


def previous_trading_day(day: str, days: list[str] | None) -> str | None:
    if not days:
        return None
    prev = [d for d in days if d < str(day)]
    return prev[-1] if prev else None


def session_state(now: datetime, days: list[str] | None) -> dict:
    """今天的状态：trading_day / after_close / expected_session。

    expected_session = 按日历「此刻应当已经收完盘」的最近交易日；日历缺失时为 None。
    """
    today = now.strftime("%Y%m%d")
    after_close = (now.hour, now.minute) >= (SESSION_CLOSE_HOUR, SESSION_CLOSE_MIN)
    trading = is_trading_day(today, days)
    if trading is None:
        return {"calendar": "unavailable", "today": today, "trading_day": None,
                "after_close": after_close, "expected_session": None}
    if trading:
        expected = today if after_close else previous_trading_day(today, days)
    else:
        expected = previous_trading_day(today, days)   # 假日：最近一个交易日
    return {"calendar": "ok", "today": today, "trading_day": trading,
            "after_close": after_close, "expected_session": expected}


def write_cache(days: list[str], path: Path | None = None, source: str = "tradecal") -> Path:
    f = path or CACHE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({
        "source": source,
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "days": sorted({str(d) for d in days}),
    }, ensure_ascii=False))
    return f
