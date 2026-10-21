"""版本化交易日历与会话判定（DATA-002）。

- 交易日按 Asia/Shanghai 会话日判断，不能用周几或 UTC 日期截断代替；
- 日历只对覆盖区间内的日子给出 open/closed，区间外一律 unknown，不猜；
- 「最近完整交易日」按收盘时刻 + 版本化发布延迟计算，而不是本地日期减 1；
- 连续竞价窗口之外（盘前、午休、收盘后、非交易日）不能生成新的模拟成交。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from . import clock

DEFAULT_WINDOWS = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))
CLOSE_TIME = time(15, 0)
FRESHNESS_POLICY_VERSION = "freshness_policy_v1"
# 日线在收盘后多久视为「应已发布」：版本化工程政策，不是供应商承诺
DAILY_PUBLICATION_DELAY = timedelta(minutes=30)


def _d(s: str) -> date:
    return date.fromisoformat(s)


@dataclass
class TradingCalendar:
    calendar_version: str
    open_sessions: frozenset[str]
    coverage_start: str
    coverage_end: str
    closed_sessions: frozenset[str] = frozenset()
    windows: tuple = DEFAULT_WINDOWS
    is_synthetic: bool = False
    source: str = "unknown"
    explicit_closed_only: bool = False   # True：只有显式列出的 closed 才算休市，其余未列出=unknown
    _sorted: list[str] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._sorted = sorted(self.open_sessions)

    # ── 单日状态 ──
    def status(self, day: str) -> str:
        """open / closed / unknown。"""
        if day in self.open_sessions:
            return "open"
        if day in self.closed_sessions:
            return "closed"
        if self.explicit_closed_only:
            return "unknown"
        if self.coverage_start <= day <= self.coverage_end:
            return "closed"
        return "unknown"

    def is_trading_day(self, day: str) -> bool | None:
        st = self.status(day)
        return None if st == "unknown" else st == "open"

    def prev_session(self, day: str) -> str | None:
        lo = [d for d in self._sorted if d < day]
        return lo[-1] if lo else None

    def next_session(self, day: str) -> str | None:
        hi = [d for d in self._sorted if d > day]
        return hi[0] if hi else None

    def sessions_between(self, start: str, end: str) -> list[str]:
        return [d for d in self._sorted if start <= d <= end]

    def sessions_behind(self, older: str, newer: str) -> int | None:
        """newer 比 older 晚几个交易日（都必须是已知交易日）。"""
        if older not in self.open_sessions or newer not in self.open_sessions:
            return None
        return len([d for d in self._sorted if older < d <= newer])

    # ── 时点判定 ──
    def phase(self, now: datetime | None = None) -> str:
        """pre_open / continuous / midday / post_close / non_trading / unknown。"""
        sh = clock.shanghai(now)
        day = sh.strftime("%Y-%m-%d")
        st = self.status(day)
        if st == "unknown":
            return "unknown"
        if st == "closed":
            return "non_trading"
        t = sh.time()
        (m0, m1), (a0, a1) = self.windows[0], self.windows[1]
        if t < m0:
            return "pre_open"
        if m0 <= t < m1:
            return "continuous"
        if m1 <= t < a0:
            return "midday"
        if a0 <= t < a1:
            return "continuous"
        return "post_close"

    def can_simulate_new_trade(self, now: datetime | None = None) -> bool:
        return self.phase(now) == "continuous"

    def latest_completed_session(self, now: datetime | None = None,
                                 publication_delay: timedelta = DAILY_PUBLICATION_DELAY) -> str | None:
        """此刻「按用途可用」的最近完整交易日；日历不认识今天时返回 None（不猜）。"""
        sh = clock.shanghai(now)
        day = sh.strftime("%Y-%m-%d")
        st = self.status(day)
        if st == "unknown":
            return None
        if st == "open":
            close_at = datetime.combine(sh.date(), CLOSE_TIME, tzinfo=clock.SHANGHAI) + publication_delay
            if sh >= close_at:
                return day
        return self.prev_session(day)

    def resolve(self, now: datetime | None = None) -> dict:
        """会话解析（AC-005）：上海会话日 ≠ UTC 日期截断。"""
        n = now or clock.now()
        sh = clock.shanghai(n)
        day = sh.strftime("%Y-%m-%d")
        st = self.status(day)
        if st == "open":
            session_date = day
        elif st == "closed":
            session_date = self.next_session(day)
        else:
            session_date = None
        return {
            "now_utc": clock.utc_iso(n),
            "utc_date": n.astimezone(clock.UTC).strftime("%Y-%m-%d"),
            "shanghai_date": day,
            "shanghai_time": sh.strftime("%H:%M:%S"),
            "calendar_version": self.calendar_version,
            "calendar_status_today": st,
            "session_date": session_date,
            "phase": self.phase(n),
            "latest_completed_session_date": self.latest_completed_session(n),
            "can_simulate_new_trade": self.can_simulate_new_trade(n),
        }

    def describe(self) -> dict:
        return {"calendar_version": self.calendar_version, "source": self.source,
                "coverage_start": self.coverage_start, "coverage_end": self.coverage_end,
                "open_session_count": len(self.open_sessions), "is_synthetic": self.is_synthetic,
                "windows": [[a.strftime("%H:%M:%S"), b.strftime("%H:%M:%S")] for a, b in self.windows]}


def from_fixture(fx: dict) -> TradingCalendar:
    """验收 fixture：只认显式列出的 open/closed，其余 unknown。"""
    opens = frozenset(fx.get("open_sessions") or [])
    closed = frozenset(fx.get("closed_sessions") or [])
    allday = sorted(opens | closed)
    wins = tuple((time.fromisoformat(a), time.fromisoformat(b))
                 for a, b in (fx.get("continuous_auction_local_windows") or [["09:30:00", "11:30:00"], ["13:00:00", "15:00:00"]]))
    return TradingCalendar(calendar_version=fx.get("calendar_version", "calendar_fixture"),
                           open_sessions=opens, closed_sessions=closed,
                           coverage_start=allday[0] if allday else "9999-12-31",
                           coverage_end=allday[-1] if allday else "0000-01-01",
                           windows=wins, is_synthetic=bool(fx.get("is_synthetic", True)),
                           source="fixture", explicit_closed_only=True)


def from_open_days(version: str, open_days: list[str], coverage_start: str, coverage_end: str,
                   source: str) -> TradingCalendar:
    """供应商日历：覆盖区间内未列出的日子视为休市。open_days 可为 YYYYMMDD 或 YYYY-MM-DD。"""
    norm = frozenset(_iso(d) for d in open_days)
    return TradingCalendar(calendar_version=version, open_sessions=norm,
                           coverage_start=_iso(coverage_start), coverage_end=_iso(coverage_end),
                           source=source)


def _iso(d: str) -> str:
    d = str(d)
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return d


def compact(d: str) -> str:
    return d.replace("-", "")
