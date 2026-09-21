"""Pure, point-in-time facts for the Wildman five-step mainline screen.

The helper in this module deliberately does not fetch data or infer a trading
calendar.  A missing observation is different from a failed condition, and
all dates used for the result come from the supplied rows.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from math import isfinite
import re
from typing import Any, Iterable
from zoneinfo import ZoneInfo


_SHANGHAI = ZoneInfo("Asia/Shanghai")


_STEP_LABELS = {
    "step1": "消息级别",
    "step2": "首日异动",
    "step3": "中军趋势",
    "step4": "老龙重启",
    "step5": "次日验证",
}

_NEGATED_NEWS = (
    "传闻", "据传", "网传", "市场传言", "或将", "可能将", "可能会",
    "否认", "辟谣", "不实", "未证实", "未经证实", "并非", "不涉及", "无关",
    "没有相关", "尚无相关", "澄清",
)
_MESSAGE_GROUPS = {
    "国家战略": ("国家战略", "国家级战略", "国家重点战略", "纳入国家战略", "国家战略规划"),
    "重磅会议": (
        "中央经济工作会议", "中央政治局会议", "政治局会议", "国务院常务会议",
        "国常会", "全国两会", "中央金融工作会议",
    ),
    "重大事件": (
        "获批", "签署协议", "签署战略合作协议", "发射成功", "重大突破",
        "重大工程", "重大订单", "项目落地", "正式落地",
    ),
}
_EXCLUDED_NAME_PATTERNS = (
    r"低价股", r"低价", r"股权转让", r"并购重组", r"资产重组", r"借壳", r"重组",
    r"ST", r"退市", r"高送转", r"次新", r"新股", r"壳资源", r"壳股", r"地方", r"省属", r"市属",
    r"国企", r"央企", r"中字头", r"高股息", r"超跌", r"红利", r"低估值", r"价值股",
    r"白马", r"蓝筹", r"权重", r"微盘", r"大盘", r"小盘", r"风格", r"含H股", r"A\+H", r"H股",
    r"风格指数", r"行业指数", r"概念指数", r"成分指数", r"指数", r"ETF", r"LOF", r"基金", r"板块指数",
    r"业绩增长", r"中报", r"年报", r"季报", r"预增", r"送转", r"破净", r"专精特新", r"实控人变更",
    r"实际控制人变更", r"国有企业", r"高校", r"含B股", r"B股", r"含可转债", r"可转债", r"科创板",
    r"创业板", r"北交所", r"(?:北京|天津|河北|山西|辽宁|吉林|黑龙江|上海|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾|内蒙古|广西|西藏|宁夏|新疆|香港|澳门)$",
    r"省$", r"市$", r"自治区$",
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "T" in text or (" " in text and len(text) > 10):
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            pass
    text = text[:10] if len(text) >= 10 else text
    try:
        return date.fromisoformat(text)
    except ValueError:
        if len(text) >= 8 and text[:8].isdigit():
            try:
                return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
            except ValueError:
                return None
    return None


def _date_text(value: Any) -> str | None:
    parsed = _parse_date(value)
    return parsed.isoformat() if parsed else None


def _parse_time(value: Any) -> time | None:
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, time):
        return value
    text = str(value or "").strip()
    if "T" in text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).time()
        except ValueError:
            return None
    match = re.fullmatch(r"(\d{1,2}):([0-5]\d)(?::[0-5]\d(?:\.\d+)?)?", text)
    if match:
        return time(int(match.group(1)), int(match.group(2)))
    if text.isdigit() and len(text) in (3, 4):
        padded = text.zfill(4)
        try:
            return time(int(padded[:2]), int(padded[2:]))
        except ValueError:
            return None
    return None


def _code(value: Any) -> str:
    text = str(value or "").strip().upper().split(".", 1)[0]
    return text.zfill(6) if text.isdigit() else text


def _row_date(row: dict[str, Any]) -> date | None:
    return _parse_date(row.get("trade_date") or row.get("tradedate") or row.get("date"))


def _value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _num_field(row: dict[str, Any], *keys: str) -> float | None:
    return _number(_value(row, *keys))


def _theme_values(theme: dict[str, Any]) -> list[str]:
    values = []
    for key in ("theme_id", "theme_name", "name"):
        value = str(theme.get(key) or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _theme_match(row: dict[str, Any], theme: dict[str, Any]) -> bool:
    expected = {item.casefold() for item in _theme_values(theme)}
    if not expected:
        return False
    row_values = {
        str(row.get(key) or "").strip().casefold()
        for key in ("theme_id", "theme_name", "theme", "theme_symbol")
        if row.get(key) is not None
    }
    return not row_values or bool(row_values & expected)


def _member_codes(theme: dict[str, Any]) -> set[str]:
    raw = theme.get("member_codes") or theme.get("symbols") or []
    if isinstance(raw, str):
        raw = re.split(r"[,;\s]+", raw)
    if not isinstance(raw, (list, tuple, set)):
        return set()
    return {_code(item.get("code") if isinstance(item, dict) else item) for item in raw if _code(item.get("code") if isinstance(item, dict) else item)}


def _member_history_map(member_history: list[dict[str, Any]], theme: dict[str, Any]) -> list[tuple[date, set[str]]]:
    result = []
    for row in member_history:
        if not isinstance(row, dict) or not _theme_match(row, theme):
            continue
        day = _row_date(row)
        symbols = row.get("symbols") or row.get("member_codes") or row.get("members") or []
        if isinstance(symbols, str):
            symbols = re.split(r"[,;\s]+", symbols)
        if day and isinstance(symbols, (list, tuple, set)):
            result.append((day, {_code(item.get("code") if isinstance(item, dict) else item) for item in symbols if _code(item.get("code") if isinstance(item, dict) else item)}))
    return sorted(result)


def _members_on(day: date, target: date, theme: dict[str, Any], history: list[tuple[date, set[str]]]) -> tuple[set[str], bool]:
    # A dated membership snapshot is PIT evidence for that exact session.
    rows = [item for item in history if item[0] == day]
    if rows:
        return set(rows[-1][1]), True
    if day == target:
        return _member_codes(theme), bool(_member_codes(theme))
    return set(), False


def _observed_dates(target: date, board_bars: list[dict[str, Any]], limit_history: list[dict[str, Any]]) -> list[date]:
    dates = {_row_date(row) for row in [*board_bars, *limit_history] if isinstance(row, dict)}
    return sorted(day for day in dates if day and day <= target)


def _limit_rows_for_theme(
    limit_history: list[dict[str, Any]],
    day: date,
    target: date,
    theme: dict[str, Any],
    history: list[tuple[date, set[str]]],
) -> list[dict[str, Any]]:
    members, membership_known = _members_on(day, target, theme, history)
    result = []
    if not membership_known:
        return result
    for row in limit_history:
        if not isinstance(row, dict) or _row_date(row) != day:
            continue
        code = _code(row.get("code") or row.get("symbol"))
        if not code:
            continue
        if code in members:
            result.append(row)
    return result


def _source(source: str, day: date | None, *, url: Any = None, published_at: Any = None) -> dict[str, Any]:
    item = {"source": source, "published_at": published_at if published_at is not None else day.isoformat() if day else None}
    if url:
        item["url"] = str(url)
    return item


def _dedupe_sources(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in items:
        key = (item.get("source"), item.get("published_at"), item.get("url"))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _bar_map(rows: Iterable[dict[str, Any]], group_code: Any = None) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, dict[date, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        if isinstance(row, dict) and _row_date(row):
            code = _code(row.get("code") or row.get("symbol") or group_code)
            # Provider retries can return the same session twice.  The later
            # row is the normalized observation for that code/date.
            result[code][_row_date(row)] = row
    return {code: [row for _, row in sorted(values.items())] for code, values in result.items()}


def _bars_for_code(stock_bars: dict[str, list[dict[str, Any]]], code: str) -> list[dict[str, Any]]:
    """Normalize both ``{code: [bars]}`` and bars carrying their own code."""
    values = stock_bars.get(code) or stock_bars.get(str(code)) or []
    if not values:
        for key, candidate in stock_bars.items():
            if _code(key) == code:
                values = candidate
                break
    if isinstance(values, dict):
        values = values.get("bars") or values.get("rows") or values.get("data") or []
    if not isinstance(values, list):
        return []
    return _bar_map(values, code).get(code, [])


def _bar_field(row: dict[str, Any], field: str) -> float | None:
    aliases = {
        "open": ("open", "open_price"), "close": ("close", "close_price"),
        "high": ("high", "high_price"), "low": ("low", "low_price"),
        "volume": ("volume", "vol"), "change_pct": ("change_pct", "pct_chg", "pct", "change"),
    }
    return _num_field(row, *aliases[field])


def _row_at(rows: list[dict[str, Any]], day: date) -> tuple[dict[str, Any] | None, int | None]:
    for index, row in enumerate(rows):
        if _row_date(row) == day:
            return row, index
    return None, None


def _is_index_row(row: dict[str, Any]) -> bool:
    marker = str(_value(row, "kind", "type", "instrument_type") or "").casefold()
    code = str(row.get("code") or row.get("symbol") or row.get("index_code") or row.get("board_code") or "").casefold()
    name = str(row.get("name") or "").casefold()
    return bool(row.get("is_index") or row.get("board_index") or marker in {"index", "board_index", "指数"} or code in {"index", "board_index", "theme_index"} or "板块指数" in name or "主题指数" in name)


def _index_bars(board_bars: list[dict[str, Any]], theme: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [row for row in board_bars if isinstance(row, dict) and _row_date(row) and _theme_match(row, theme)]
    explicit = [row for row in rows if _is_index_row(row)]
    if explicit:
        rows = explicit
    else:
        by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_day[_row_date(row)].append(row)
        rows = [values[0] for day, values in sorted(by_day.items()) if len(values) == 1]
    return sorted(rows, key=lambda row: _row_date(row))


def _firstboard_counts(
    target: date,
    observed: list[date],
    limit_history: list[dict[str, Any]],
    theme: dict[str, Any],
    history: list[tuple[date, set[str]]],
) -> dict[date, dict[str, Any]]:
    counts: dict[date, dict[str, Any]] = {}
    for day in observed:
        rows = _limit_rows_for_theme(limit_history, day, target, theme, history)
        first = []
        for row in rows:
            if _number(row.get("continuous_days")) == 1:
                code = _code(row.get("code") or row.get("symbol"))
                if code and code not in {item["code"] for item in first}:
                    first.append({"code": code, "name": row.get("name") or code, "row": row})
        early = [item for item in first if (parsed := _parse_time(item["row"].get("first_limit_time"))) and time(9, 30) <= parsed <= time(10, 30)]
        counts[day] = {"rows": rows, "first": first, "early": early, "count": len(early), "membership_known": _members_on(day, target, theme, history)[1]}
    return counts


def _board_breakout(board_rows: list[dict[str, Any]], day: date) -> tuple[bool | None, dict[str, Any]]:
    row, index = _row_at(board_rows, day)
    if row is None or index is None:
        return None, {"交易日": day.isoformat(), "板指数据": "缺失"}
    prior = board_rows[:index]
    close, volume = _bar_field(row, "close"), _bar_field(row, "volume")
    highs = [_bar_field(item, "high") for item in prior[-20:]]
    volumes = [_bar_field(item, "volume") for item in prior[-5:]]
    previous_high = max(highs) if len(highs) == 20 and all(value is not None for value in highs) else None
    previous_mean = sum(volumes) / 5 if len(volumes) == 5 and all(value is not None for value in volumes) else None
    passed = None if None in (close, volume, previous_high, previous_mean) else previous_mean > 0 and previous_high > 0 and close > previous_high and volume >= previous_mean * 1.2
    return passed, {
        "交易日": day.isoformat(), "板指收盘": close, "前20日最高": previous_high,
        "板指成交量": volume, "前5日均量": previous_mean,
        "放量倍数": round(volume / previous_mean, 3) if volume is not None and previous_mean else None,
    }


def _stock_bar_metrics(rows: list[dict[str, Any]], day: date) -> dict[str, Any]:
    current, index = _row_at(rows, day)
    if current is None or index is None:
        return {"交易日": day.isoformat(), "日线": "缺失"}
    prior = rows[:index]
    closes = [_bar_field(item, "close") for item in rows[max(0, index - 19):index + 1]]
    prior_closes = [_bar_field(item, "close") for item in prior]
    volumes = [_bar_field(item, "volume") for item in prior[-5:]]
    close, opening, volume = _bar_field(current, "close"), _bar_field(current, "open"), _bar_field(current, "volume")
    ma20 = sum(closes) / 20 if len(closes) == 20 and all(value is not None for value in closes) else None
    previous_close = prior_closes[-1] if prior_closes else _num_field(current, "previous_close", "prev_close")
    previous_mean = sum(volumes) / 5 if len(volumes) == 5 and all(value is not None for value in volumes) else None
    return {
        "交易日": day.isoformat(), "开盘": opening, "收盘": close, "前收": previous_close,
        "MA20": ma20, "成交量": volume, "前5日均量": previous_mean,
        "涨跌幅": _bar_field(current, "change_pct"),
        "最低": _bar_field(current, "low"), "最高": _bar_field(current, "high"),
        "_row": current, "_index": index,
    }


def _opening(rows: list[dict[str, Any]], day: date, auction: dict[str, Any] | None) -> dict[str, Any]:
    metrics = _stock_bar_metrics(rows, day)
    opening, previous = metrics.get("开盘"), metrics.get("前收")
    source = "stockdaily"
    if opening is None or previous is None:
        if isinstance(auction, dict) and _row_date(auction) == day:
            opening = _num_field(auction, "open", "open_price", "auction_price", "price")
            previous = _num_field(auction, "previous_close", "prev_close")
            source = "auction"
    premium = (opening / previous - 1) * 100 if opening is not None and previous else None
    return {"开盘": opening, "前收": previous, "开盘溢价": premium, "来源": source, "日线": metrics.get("交易日")}


def candidate_name_allowed(name: Any) -> bool:
    """Return whether a theme member name is eligible for mainline evidence."""
    text = str(name or "").strip().upper()
    if not text or text == "未归类" or re.fullmatch(r"\d+", text):
        return False
    return not any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _EXCLUDED_NAME_PATTERNS)


def _stock_name_allowed(name: Any) -> bool:
    """Require a real stock name before applying candidate label filters."""
    text = str(name or "").strip()
    return bool(text) and not re.match(r"^(?:\*?ST|退市)", text, re.IGNORECASE)


def _theme_news_names(theme: dict[str, Any]) -> list[str]:
    values = []
    for value in _theme_values(theme):
        text = value.strip()
        for suffix in ("概念", "板块", "行业", "指数"):
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)]
        if text and text not in values:
            values.append(text)
    return values


def _news_datetime(value: Any) -> tuple[date | None, time | None]:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(_SHANGHAI)
        return value.date(), value.time()
    if isinstance(value, date):
        return value, None
    text = str(value or "").strip()
    if not text:
        return None, None
    if "T" not in text and " " not in text:
        return _parse_date(text), None
    iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso_text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(_SHANGHAI)
        return parsed.date(), parsed.time()
    except ValueError:
        parsed_date = _parse_date(text)
        return parsed_date, None


def _news_step(
    news: list[dict[str, Any]],
    theme: dict[str, Any],
    trigger: date | None,
    trigger_time: time | None = None,
) -> dict[str, Any]:
    if trigger is None:
        return {"passed": None, "actual": {"触发日": None, "消息": "无可验证触发日"}, "sources": [], "reason": "缺少首日异动触发日，消息时序未知。"}
    names = _theme_news_names(theme)
    qualified = []
    saw_usable = False
    for item in news:
        if not isinstance(item, dict):
            continue
        published, published_time = _news_datetime(item.get("published_at") or item.get("display_at") or item.get("publish_time") or item.get("date"))
        source, url = str(item.get("source") or "").strip(), str(item.get("url") or item.get("link") or "").strip()
        text = " ".join(str(item.get(key) or "") for key in ("title", "content", "summary", "description"))
        available, available_time = _news_datetime(item.get("available_time") or item.get("available_at"))
        url_ok = url.startswith(("http://", "https://"))
        if published and source and url_ok:
            saw_usable = True
        same_day_after_cutoff = published == trigger and (
            published_time is None and trigger_time is not None
            or published_time is not None and trigger_time is not None and published_time > trigger_time
        )
        available_after_cutoff = available is not None and (
            available > trigger
            or available == trigger and (available_time is None or trigger_time is not None and available_time > trigger_time)
        )
        if not published or published > trigger or same_day_after_cutoff or available_after_cutoff or not source or not url_ok or not any(name and name.casefold() in text.casefold() for name in names):
            continue
        if any(token in text for token in _NEGATED_NEWS):
            continue
        matched_group = next((group for group, phrases in _MESSAGE_GROUPS.items() if any(phrase in text for phrase in phrases)), None)
        policy_grade = str(item.get("policy_grade") or item.get("message_level") or item.get("event_impact") or "").strip()
        structured_grade = policy_grade in _MESSAGE_GROUPS or policy_grade in {"国家级", "重大", "高影响"}
        strong_national = "国家战略" in text or "国家级战略" in text or "国家重点战略" in text
        meeting_action = any(word in text for word in ("审议", "部署", "提出", "通过", "确定", "明确", "发布规划"))
        major_qualifier = any(word in text for word in ("重大", "国家级", "全球首个", "世界首个", "全国首个", "行业首个", "首个"))
        concrete_event = any(word in text for word in ("获批", "签署协议", "发射成功", "重大突破", "重大工程", "重大订单", "项目落地", "正式落地"))
        proven = (
            matched_group == "国家战略" and strong_national
            or matched_group == "重磅会议" and meeting_action
            or matched_group == "重大事件" and concrete_event and (major_qualifier or structured_grade)
            or structured_grade and matched_group is not None and len(text) >= 12
        )
        if matched_group and proven:
            qualified.append((published, matched_group, item, text))
    qualified.sort(key=lambda item: (item[0], str(item[2].get("published_at") or "")), reverse=True)
    sources = [_source(str(item[2].get("source")), item[0], url=item[2].get("url") or item[2].get("link"), published_at=item[2].get("published_at") or item[2].get("display_at") or item[2].get("publish_time") or item[2].get("date")) for item in qualified]
    if qualified:
        item = qualified[0]
        return {
            "passed": True,
            "actual": {"触发日": trigger.isoformat(), "消息级别": item[1], "消息标题": item[2].get("title"), "消息日期": item[0].isoformat(), "发布时间原文": item[2].get("published_at") or item[2].get("display_at") or item[2].get("publish_time") or item[2].get("date"), "来源": item[2].get("source"), "URL": item[2].get("url") or item[2].get("link")},
            "sources": _dedupe_sources(sources),
            "reason": "题材名称、消息级别、发布时间、来源和URL均可核验，且发布时间不晚于触发日。",
        }
    return {
        "passed": False if saw_usable or news else None,
        "actual": {"触发日": trigger.isoformat(), "消息级别": None, "可核验消息数": 0},
        "sources": [],
        "reason": "未找到发布时间不晚于触发日、直接点名题材且有来源URL的国家战略/重磅会议/重大事件消息。" if saw_usable or news else "消息记录缺失，不能把缺消息判为假。",
    }


def _burst_trigger(observed: list[date], burst_dates: list[date], eligible_dates: list[date] | None = None) -> date | None:
    eligible = eligible_dates if eligible_dates is not None else burst_dates
    if not eligible:
        return None
    selected = max(eligible)
    all_bursts = set(burst_dates)
    position = observed.index(selected)
    while position > 0 and observed[position - 1] in all_bursts:
        position -= 1
    return observed[position]


def select_trigger_date(
    target: date,
    theme: dict,
    limit_history: list[dict],
    member_history: list[dict],
) -> date | None:
    """Select the PIT first day of the latest five-session first-board burst.

    This collector-facing preflight intentionally uses only dated limit and
    membership facts.  ``derive_mainline_steps`` additionally requires the
    official theme index breakout/volume facts before Step 2 can pass.
    """
    if not isinstance(target, date) or isinstance(target, datetime):
        raise TypeError("target must be a date")
    rows = [row for row in limit_history if isinstance(row, dict)]
    theme = theme if isinstance(theme, dict) else {}
    history = _member_history_map([row for row in member_history if isinstance(row, dict)], theme)
    observed = sorted({day for day in (_row_date(row) for row in rows) if day and day <= target})
    recent = observed[-5:]
    counts = _firstboard_counts(target, observed, rows, theme, history)
    all_burst_dates = [day for day in observed if counts.get(day, {}).get("count", 0) >= 5]
    return _burst_trigger(observed, all_burst_dates, [day for day in recent if day in all_burst_dates])


def derive_mainline_steps(
    target: date,
    theme: dict,
    *,
    limit_history: list[dict],
    board_bars: list[dict],
    stock_bars: dict[str, list[dict]],
    stock_quotes: dict[str, dict],
    news: list[dict],
    member_history: list[dict],
    auctions: dict[str, dict] | None = None,
) -> dict:
    """Derive the auditable five-step mainline facts as of ``target``."""
    if not isinstance(target, date) or isinstance(target, datetime):
        raise TypeError("target must be a date")
    theme = theme if isinstance(theme, dict) else {}
    limit_history = [row for row in limit_history if isinstance(row, dict)]
    board_bars = [row for row in board_bars if isinstance(row, dict)]
    stock_bars = stock_bars if isinstance(stock_bars, dict) else {}
    stock_quotes = stock_quotes if isinstance(stock_quotes, dict) else {}
    news = news if isinstance(news, list) else []
    member_history = member_history if isinstance(member_history, list) else []
    auctions = auctions if isinstance(auctions, dict) else {}

    history = _member_history_map(member_history, theme)
    observed = _observed_dates(target, board_bars, limit_history)
    counts = _firstboard_counts(target, observed, limit_history, theme, history)
    recent = observed[-5:]
    all_burst_dates = [day for day in observed if counts.get(day, {}).get("count", 0) >= 5]
    burst_dates = [day for day in recent if day in all_burst_dates]
    index_rows = _index_bars(board_bars, theme)
    # The event date is selected from the first-board episode alone.  Board
    # confirmation is evaluated on that date; it must never move the event
    # date to a later day just because a later bar happens to qualify.
    trigger = _burst_trigger(observed, all_burst_dates, burst_dates)
    selected = trigger
    check = _board_breakout(index_rows, trigger) if trigger else (None, {})

    if selected is not None:
        step2_pass = check[0]
        step2_reason = "最近5个实际观测日内找到首板爆发，并在该爆发段首日核验板指收盘突破前20日高点且成交量达到前5日均量1.2倍。" if step2_pass is True else "首板爆发存在，但爆发段首日的真实板块指数突破/成交量证据不足。"
    elif any(not counts.get(day, {}).get("membership_known", False) for day in recent):
        selected, check, step2_pass = None, (None, {}), None
        step2_reason = "最近5个实际观测日的PIT成员记录不完整，首板爆发未知。"
    elif limit_history:
        selected, check, step2_pass = None, (None, {}), False
        step2_reason = "已检查最近5个实际观测日，未找到09:30-10:30至少5只首板的爆发日。"
    else:
        selected, check, step2_pass = None, (None, {}), None
        step2_reason = "首板历史缺失，不能把未采样判为未发生。"
    step2_actual = {
        "最近5个实际观测日": [day.isoformat() for day in recent], "爆发观测日": selected.isoformat() if selected else None,
        "触发日": trigger.isoformat() if trigger else None,
        "连续爆发段首日": trigger.isoformat() if trigger else None,
        "首板数": counts.get(selected, {}).get("count") if selected else None,
        "首板时间窗": "09:30-10:30", "板指条件": check[1],
    }
    step2_sources = []
    if selected:
        board_row = _row_at(index_rows, selected)[0]
        if board_row:
            step2_actual["板指代码"] = board_row.get("code") or board_row.get("symbol")
            step2_actual["板指名称"] = board_row.get("name") or board_row.get("theme_name") or theme.get("theme_name")
            step2_actual["板指来源"] = board_row.get("source") or board_row.get("data_source") or "theme_daily"
        step2_sources = [_source("limit_history", selected), _source(str(step2_actual.get("板指来源") or "theme_daily"), selected)]
    step2 = {"passed": step2_pass, "actual": step2_actual, "sources": step2_sources, "reason": step2_reason, "reason_code": "STEP2_NO_BURST_IN_LAST_5" if step2_pass is False else None}

    # Step 1 is evaluated against the actual trigger date, never against target.
    trigger_time = None
    if trigger is not None and counts.get(trigger, {}).get("early"):
        trigger_time = min(_parse_time(item["row"].get("first_limit_time")) for item in counts[trigger]["early"])
    trigger_time = trigger_time or time(10, 30)
    step1 = _news_step(news, theme, trigger, trigger_time)

    core_stocks: list[dict[str, Any]] = []
    core_candidates: list[dict[str, Any]] = []
    step3_sources = []
    members: set[str] = set()
    missing_codes: list[str] = []
    evaluated_codes: list[str] = []
    sample_codes: list[str] = []
    if trigger is None:
        step3_pass = None
        step3_reason = "缺少触发日，不能使用目标日或未来快照替代触发日。"
    else:
        members, member_known = _members_on(trigger, target, theme, history)
        if not member_known or not members:
            step3_pass = None
            step3_reason = "触发日PIT成员缺失，未借用未来成员。"
        else:
            raw_sample = theme.get("core_sample_codes")
            if isinstance(raw_sample, str):
                raw_sample = re.split(r"[,;\s]+", raw_sample)
            if isinstance(raw_sample, (list, tuple, set)):
                sample_codes = sorted({_code(item.get("code") if isinstance(item, dict) else item) for item in raw_sample if _code(item.get("code") if isinstance(item, dict) else item) in members})
            sampled = isinstance(raw_sample, (list, tuple, set))
            evaluation_codes = sample_codes if sampled else sorted(members)
            for code in evaluation_codes:
                quote = stock_quotes.get(code) or stock_quotes.get(str(code))
                quote_day = _parse_date(quote.get("trade_date")) if isinstance(quote, dict) else None
                if quote_day != trigger:
                    missing_codes.append(code)
                    continue
                bars = _bars_for_code(stock_bars, code)
                metrics = _stock_bar_metrics(bars, trigger)
                cap = _num_field(quote, "market_cap", "marketcap", "market_capitalization", "total_mv")
                opening, previous, close, ma20, volume, mean5 = (metrics.get(key) for key in ("开盘", "前收", "收盘", "MA20", "成交量", "前5日均量"))
                pct = metrics.get("涨跌幅")
                if any(value is None for value in (opening, previous, close, ma20, volume, mean5)):
                    missing_codes.append(code)
                    continue
                evaluated_codes.append(code)
                cap_ok = cap is not None and cap >= 10_000_000_000
                trend = close is not None and ma20 is not None and close > ma20
                direct = opening is not None and previous is not None and close is not None and opening > previous and close > opening
                accelerated = pct is not None and pct >= 5 and volume is not None and mean5 is not None and volume >= mean5 * 1.5 and close is not None and opening is not None and close > opening
                turnover = _num_field(quote, "turnover", "amount", "turnover_amount", "成交额")
                if turnover is None:
                    turnover = _num_field(metrics.get("_row") or {}, "turnover", "amount", "turnover_amount", "成交额")
                passed = cap_ok and trend and volume > 0 and mean5 > 0 and (direct or accelerated) and _stock_name_allowed(quote.get("name"))
                item = {"代码": code, "名称": quote.get("name") or code, "交易日": trigger.isoformat(), "symbol": code, "name": quote.get("name") or code, "trade_date": trigger.isoformat(), "市值": cap, "成交额": turnover, "收盘": close, "MA20": ma20, "开盘": opening, "前收": previous, "成交量": volume, "前5日均量": mean5, "涨跌幅": pct, "通过": passed}
                if passed:
                    core_candidates.append(item)
                step3_sources.extend([_source("stock_quotes", trigger), _source("stock_bars", trigger)])
            core_candidates.sort(key=lambda item: (item.get("成交额") is not None, item.get("成交额") or 0, item.get("市值") or 0), reverse=True)
            core_stocks = core_candidates[:5]
            if core_stocks:
                step3_pass = True
                step3_reason = "触发日PIT成员中找到市值≥100亿元且满足趋势、开盘/强势放量条件的中军；按成交额/市值排序展示最多5只样本，不以涨停池作为筛选前提。"
            else:
                sample_complete = bool(evaluation_codes) and len(evaluated_codes) == len(evaluation_codes)
                full_cap_complete = theme.get("core_capitalization_complete") is True
                if (sample_complete and (not sampled or full_cap_complete or len(evaluation_codes) == len(members))) or (sampled and not sample_codes and full_cap_complete):
                    step3_pass = False
                    step3_reason = "已核验的中军样本均未满足≥100亿元趋势中军条件。"
                else:
                    step3_pass = None
                    step3_reason = "触发日中军样本的市值或日线记录不完整，不能把未采样判为没有百亿中军。"
    step3_actual = {"触发日": trigger.isoformat() if trigger else None, "核心中军": core_stocks, "市值门槛": 10_000_000_000, "成员总数": len(members), "采样成员总数": len(sample_codes) if isinstance(theme.get("core_sample_codes"), (list, tuple, set)) else len(members), "已核验成员数": len(evaluated_codes), "满足条件成员数": len(core_candidates), "样本返回数": len(core_stocks), "缺失样本数": len(missing_codes), "缺失样本代码": missing_codes[:10], "全量市值已完成": theme.get("core_capitalization_complete") if isinstance(theme.get("core_capitalization_complete"), bool) else None}
    step3 = {"passed": step3_pass, "actual": step3_actual, "sources": _dedupe_sources(step3_sources), "reason": step3_reason}

    # Step 4: locate an actual old wave first, then inspect only its restart window.
    step4_sources: list[dict[str, Any]] = []
    old_leaders: list[dict[str, Any]] = []
    restart_rows: list[dict[str, Any]] = []
    step4_actual: dict[str, Any] = {"触发日": trigger.isoformat() if trigger else None, "旧龙": [], "重启窗口": [], "中断观测日数": None, "重启候选": []}
    if trigger is None:
        step4_pass, step4_reason = None, "缺少触发日，旧龙重启时序未知。"
    else:
        limit_observed = sorted({day for day in (_row_date(row) for row in limit_history) if day and day <= target})
        prior_limit_dates = limit_observed[-20:]
        prior = []
        for day in prior_limit_dates:
            if day >= trigger:
                break
            prior.extend(_limit_rows_for_theme(limit_history, day, target, theme, history))
        heights: dict[str, int] = defaultdict(int)
        latest_wave: dict[str, date] = {}
        names: dict[str, str] = {}
        for row in prior:
            code = _code(row.get("code") or row.get("symbol"))
            height = int(_number(row.get("continuous_days")) or 0)
            if code and height >= 4 and height >= heights[code]:
                heights[code], latest_wave[code], names[code] = height, _row_date(row), row.get("name") or code
        max_height = max(heights.values(), default=0)
        old_codes = sorted(code for code, height in heights.items() if height == max_height and max_height >= 4)
        old_leaders = [{"code": code, "name": names[code], "boards": heights[code], "date": latest_wave[code]} for code in old_codes]
        step4_actual["旧龙"] = [{"代码": item["code"], "名称": item["name"], "板数": item["boards"], "日期": item["date"].isoformat()} for item in old_leaders]
        if not old_leaders:
            prior_membership_gap = any(not _members_on(day, target, theme, history)[1] for day in prior_limit_dates if day < trigger)
            step4_pass, step4_reason = (None, "触发日前历史PIT成员记录不完整，无法确认旧龙是否存在。") if prior_membership_gap else (False, "已检查触发日前的完整历史窗口，未找到实际达到4板的旧龙；该条件失败，不能自动通过。")
            step4_reason_code = "STEP4_MEMBERSHIP_HISTORY_UNKNOWN" if prior_membership_gap else "STEP4_OLD_LEADER_NOT_FOUND"
        else:
            window = observed[max(0, observed.index(trigger) - 3):observed.index(trigger) + 1] if trigger in observed else []
            step4_actual["重启窗口"] = [day.isoformat() for day in window]
            prior_end = max(item["date"] for item in old_leaders)
            interrupted = [day for day in observed if prior_end < day < trigger]
            step4_actual["中断观测日数"] = len(interrupted)
            for item in old_leaders:
                code = item["code"]
                for day in window:
                    members, membership_known = _members_on(day, target, theme, history)
                    if not membership_known or code not in members:
                        continue
                    rows = [row for row in _limit_rows_for_theme(limit_history, day, target, theme, history) if _code(row.get("code") or row.get("symbol")) == code]
                    heights_on_day = [int(_number(row.get("continuous_days")) or 0) for row in rows]
                    height = max(heights_on_day, default=0)
                    if height >= 8:
                        step4_actual.setdefault("连续8板排除", []).append({"代码": code, "日期": day.isoformat(), "板数": height})
                        continue
                    bars = _bars_for_code(stock_bars, code)
                    has_bar = _row_at(bars, day)[0] is not None
                    # A restart is a dated old-leader bar pattern, not only a
                    # new limit-up row.  The limit row is needed only for the
                    # explicit current-one-board branch.
                    if rows or has_bar:
                        restart_rows.append({"code": code, "name": (rows[0].get("name") if rows else None) or item["name"], "date": day, "row": rows[0] if rows else {}, "height": height})
            valid_restarts = []
            bars_by_code: dict[str, list[dict[str, Any]]] = {}
            for code, values in stock_bars.items():
                bars_by_code[_code(code)] = _bars_for_code(stock_bars, _code(code))
            for candidate in restart_rows:
                rows = bars_by_code.get(candidate["code"], [])
                metrics = _stock_bar_metrics(rows, candidate["date"])
                index = metrics.get("_index")
                prior_rows = rows[:index] if isinstance(index, int) else []
                lows = [_bar_field(row, "low") for row in prior_rows[-20:]]
                highs = [_bar_field(row, "high") for row in prior_rows[-10:]]
                volume, mean5, close, previous = (metrics.get(key) for key in ("成交量", "前5日均量", "收盘", "前收"))
                low = metrics.get("最低")
                low_position = low is not None and len(lows) == 20 and all(value is not None for value in lows) and low <= min(lows) * 1.3
                volume_ok = volume is not None and mean5 is not None and mean5 > 0 and volume >= mean5 * 1.2
                stabilizes = close is not None and previous is not None and close >= previous
                breaks10 = close is not None and len(highs) == 10 and all(value is not None for value in highs) and close > max(highs)
                current_one_board = candidate["date"] == trigger and candidate.get("height") == 1
                technical = (volume_ok and stabilizes) or (breaks10 and volume_ok) or current_one_board
                valid = low_position and technical and len(interrupted) >= 3
                candidate_actual = {"代码": candidate["code"], "名称": candidate["name"], "日期": candidate["date"].isoformat(), "低位": low, "前20日最低": min(lows) if len(lows) == 20 and all(value is not None for value in lows) else None, "成交量": volume, "前5日均量": mean5, "收盘": close, "前收": previous, "低位条件": low_position, "重启条件": technical, "当前首板": current_one_board, "通过": valid}
                step4_actual.setdefault("重启候选", []).append(candidate_actual)
                step4_sources.extend([_source("limit_history", candidate["date"]), _source("stock_bars", candidate["date"])])
                if valid:
                    valid_restarts.append(candidate_actual)
            restart_membership_gap = any(not _members_on(day, target, theme, history)[1] for day in window)
            step4_pass = True if valid_restarts else (None if restart_membership_gap else (False if restart_rows or len(interrupted) >= 3 else None))
            step4_reason = "旧龙前轮≥4板、中断≥3个实际观测日，且重启窗口内首板满足低位与量价条件。" if step4_pass else "旧龙已找到，但中断、低位、量价或重启首板条件未完整满足。"
            step4_reason_code = None
    if trigger is None:
        step4_reason_code = "STEP4_TRIGGER_UNKNOWN"
    elif old_leaders and step4_pass is None:
        step4_reason_code = "STEP4_RESTART_WINDOW_UNKNOWN"
    elif old_leaders and step4_pass is False:
        step4_reason_code = "STEP4_RESTART_CONDITION_FAILED"
    step4 = {"passed": step4_pass, "actual": step4_actual, "sources": _dedupe_sources(step4_sources), "reason": step4_reason, "reason_code": step4_reason_code}

    # Step 5: inspect exactly the next observed session after trigger.
    leader_stocks: list[dict[str, Any]] = []
    step5_sources: list[dict[str, Any]] = []
    validation = None
    if trigger is not None:
        future_observed = [day for day in observed if day > trigger]
        validation = future_observed[0] if future_observed else None
    step5_actual: dict[str, Any] = {"触发日": trigger.isoformat() if trigger else None, "验证日": validation.isoformat() if validation else None, "最高板候选": [], "首板存活率": None, "容量核心开盘均不低于0": None}
    if trigger is None or validation is None:
        step5_pass, step5_reason = None, "没有触发日或触发日后的第一个实际观测交易日，次日验证未知，不替换为同日或更晚日期。"
    else:
        trigger_rows = _limit_rows_for_theme(limit_history, trigger, target, theme, history)
        heights = [int(_number(row.get("continuous_days")) or 0) for row in trigger_rows]
        max_height = max(heights, default=0)
        leaders = [row for row in trigger_rows if int(_number(row.get("continuous_days")) or 0) == max_height and max_height > 0 and _stock_name_allowed(row.get("name"))]
        firstboards = [row for row in trigger_rows if int(_number(row.get("continuous_days")) or 0) == 1]
        openings = {}
        for row in leaders + firstboards:
            code = _code(row.get("code") or row.get("symbol"))
            if code not in openings:
                bars = _bars_for_code(stock_bars, code)
                openings[code] = _opening(bars, validation, auctions.get(code) or auctions.get(str(code)))
                step5_sources.append(_source(openings[code]["来源"], validation))
        for row in leaders:
            code = _code(row.get("code") or row.get("symbol"))
            opening = openings[code]
            leader_name = row.get("name") or code
            leader_stocks.append({"代码": code, "名称": leader_name, "交易日": trigger.isoformat(), "验证日": validation.isoformat(), "symbol": code, "name": leader_name, "trade_date": trigger.isoformat(), "最高板": max_height, "开盘": opening["开盘"], "前收": opening["前收"], "开盘溢价": opening["开盘溢价"]})
        first_values = [openings[_code(row.get("code") or row.get("symbol"))].get("开盘溢价") for row in firstboards if _code(row.get("code") or row.get("symbol")) in openings]
        leader_values = [item.get("开盘溢价") for item in leader_stocks]
        capacity_codes = [item["代码"] for item in core_stocks]
        for code in capacity_codes:
            if code not in openings:
                bars = _bars_for_code(stock_bars, code)
                openings[code] = _opening(bars, validation, auctions.get(code) or auctions.get(str(code)))
                step5_sources.append(_source(openings[code]["来源"], validation))
        capacity_values = [openings[code].get("开盘溢价") for code in capacity_codes if code in openings]
        survival_known = len(first_values) == len(firstboards) and all(value is not None for value in first_values)
        capacity_known = len(capacity_values) == len(capacity_codes) and all(value is not None for value in capacity_values)
        leader_known = len(leader_values) == len(leaders) and all(value is not None for value in leader_values)
        survival = sum(value >= 0 for value in first_values) / len(first_values) if survival_known and first_values else None
        capacity = all(value >= 0 for value in capacity_values) if capacity_known and capacity_values else None
        leader_premium = all(value >= 2 for value in leader_values) if leader_known and leader_values else None
        step5_actual.update({"最高板候选": leader_stocks, "首板存活率": survival, "容量核心开盘均不低于0": capacity, "容量核心": [{"代码": code, "开盘溢价": openings[code].get("开盘溢价")} for code in capacity_codes], "首板数": len(firstboards), "最高板开盘溢价": leader_values})
        step5_pass = True if leader_premium is True and survival is not None and survival >= 0.7 and capacity is True else False if leader_premium is False or (survival is not None and survival < 0.7) or capacity is False else None
        step5_reason = "触发日最高板（含并列）次日开盘溢价≥2%，首板开盘不低于0的存活率≥70%，容量核心全部不低于0；未使用次日收盘。" if step5_pass else "次日开盘样本不足或未同时满足最高板、首板存活率和容量核心条件。"
    step5 = {"passed": step5_pass, "actual": step5_actual, "sources": _dedupe_sources(step5_sources), "reason": step5_reason}

    sources = _dedupe_sources([*step1["sources"], *step2["sources"], *step3["sources"], *step4["sources"], *step5["sources"]])
    return {
        "mainline_five_steps": {"step1": step1, "step2": step2, "step3": step3, "step4": step4, "step5": step5},
        "trigger_date": trigger.isoformat() if trigger else None,
        "validation_date": validation.isoformat() if validation else None,
        "core_stocks": core_stocks,
        "leader_stocks": leader_stocks,
        "thresholds": {
            "step1_message_groups": list(_MESSAGE_GROUPS),
            "step2_firstboard_window": "09:30-10:30",
            "step2_firstboard_count": 5,
            "step2_board_breakout_lookback": 20,
            "step2_volume_multiple": 1.2,
            "step3_market_cap_yuan": 10_000_000_000,
            "step3_volume_multiple": 1.5,
            "step4_prior_wave_boards": 4,
            "step4_interrupted_observed_days": 3,
            "step4_restart_sessions": 3,
            "step4_low_position_multiple": 1.3,
            "step4_volume_multiple": 1.2,
            "step5_leader_open_premium_pct": 2,
            "step5_firstboard_survival": 0.7,
            "disclosure": "以上为工程判定口径，用于可复核筛选，不声称是原文逐字阈值；日期只来自输入中实际观测记录。",
        },
        "sources": sources,
    }


__all__ = ["candidate_name_allowed", "derive_mainline_steps", "select_trigger_date"]
