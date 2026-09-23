"""Independent, evidence-bound mainline candidate discovery.

This module is deliberately separate from :mod:`wildman.mainline`.  It adds a
research label for the radar while leaving the five-step rule facts untouched.
All inputs are treated as point-in-time facts; missing facts stay unknown.
"""

from __future__ import annotations

from datetime import date, datetime, time
from math import isfinite
from typing import Any, Iterable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from wildman.rules import MainlineEngine


DISCOVERY_VERSION = "mainline-discovery-v1"
_SHANGHAI = ZoneInfo("Asia/Shanghai")

_NEGATIVE = (
    "传闻", "据传", "网传", "市场传言", "或将", "可能将", "可能会",
    "否认", "辟谣", "不实", "未证实", "未经证实", "并非", "不涉及",
    "无关", "没有相关", "尚无相关", "澄清",
    "取消", "终止", "暂停", "延迟", "下修", "下调", "下滑", "下降",
    "亏损", "暴雷", "违约", "不及预期", "订单取消", "业绩预减",
)
_STRONG = (
    "国家战略", "国家级战略", "纳入国家战略", "中央经济工作会议",
    "中央政治局会议", "政治局会议", "国务院常务会议", "国常会",
    "全国两会", "中央金融工作会议", "重大事件", "重大工程", "重大突破",
    "正式落地", "项目落地", "获批", "发射成功",
)
_INDUSTRY = (
    "行业政策", "产业政策", "政策支持", "公司公告", "公告披露", "中标",
    "招标", "订单", "签署协议", "战略合作", "产品进展", "产品发布",
    "研发进展", "业绩", "预增", "扭亏", "产业价格", "涨价", "降价",
    "供需", "产能", "投产", "项目落地", "获批",
)
_UNRELIABLE = ("论坛", "贴吧", "微博", "社交媒体", "匿名", "自媒体", "群聊")
_RELIABLE_KINDS = {
    "government_official", "official_policy", "official_disclosure",
    "exchange_official", "company_announcement", "cninfo", "mainstream_finance",
    "cls",
}
_KNOWN_RELIABLE = {
    "gov_cn", "ndrc", "pboc", "exchange", "eastmoney", "ftshare_mcp",
    "research_media", "cls", "财联社", "新华社", "证券时报", "中国政府网",
    "国家发展改革委", "证监会", "上交所", "深交所", "北交所", "巨潮资讯",
    "公司公告",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = _text(value)
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        if len(raw) >= 8 and raw[:8].isdigit():
            try:
                return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
            except ValueError:
                return None
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(_SHANGHAI).replace(tzinfo=None)
        return parsed
    if isinstance(value, date):
        return None
    raw = _text(value)
    if not raw:
        return None
    if "T" not in raw and " " not in raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(_SHANGHAI).replace(tzinfo=None)
        return parsed
    except ValueError:
        # Date-only values are intentionally not represented as midnight:
        # same-day ordering against a trigger cannot be proven.
        return None


def _parse_intraday_time(value: Any) -> time | None:
    if isinstance(value, datetime):
        return value.astimezone(_SHANGHAI).time().replace(tzinfo=None) if value.tzinfo else value.time()
    if isinstance(value, time):
        return value
    raw = _text(value)
    if not raw:
        return None
    if "T" in raw or " " in raw:
        parsed = _parse_datetime(raw)
        return parsed.time() if parsed else None
    digits = raw.replace(":", "").replace("：", "").strip()
    if len(digits) >= 4 and digits[:4].isdigit():
        try:
            return time(int(digits[:2]), int(digits[2:4]))
        except ValueError:
            return None
    return None


def _as_of(target: Any, explicit: Any = None) -> date:
    parsed = _parse_date(explicit if explicit is not None else target)
    if parsed is None:
        raise TypeError("as_of/target must contain a valid date")
    return parsed


def _theme_terms(theme: dict[str, Any]) -> list[str]:
    result: list[str] = []
    aliases = theme.get("theme_aliases") or theme.get("aliases") or []
    values = [theme.get(key) for key in ("theme_name", "theme_id", "name")]
    if isinstance(aliases, str):
        aliases = [aliases]
    values.extend(aliases if isinstance(aliases, (list, tuple, set)) else [])
    for value in values:
        item = _text(value)
        for suffix in ("概念", "板块", "行业", "指数"):
            if item.endswith(suffix) and len(item) > len(suffix):
                item = item[:-len(suffix)]
        if item and item.casefold() not in {old.casefold() for old in result}:
            result.append(item)
    return result


def _source_name(item: dict[str, Any]) -> str:
    return _text(item.get("source") or item.get("source_name") or item.get("source_key"))


def _source_key(item: dict[str, Any]) -> str:
    return _text(item.get("source_key") or item.get("source_id") or item.get("source") or item.get("source_name"))


def _source_reliable(item: dict[str, Any]) -> bool:
    source = _source_name(item)
    if not source or any(token in source for token in _UNRELIABLE):
        return False
    kind = _text(item.get("source_kind") or item.get("scope")).casefold()
    if kind in _RELIABLE_KINDS:
        return True
    level = _text(item.get("source_level") or item.get("source_grade") or item.get("grade")).upper()
    if level in {"S", "A", "B"} and (item.get("source_level") is not None or item.get("source_grade") is not None or item.get("grade") is not None):
        return True
    normalized = {_text(value).casefold() for value in (source, _source_key(item)) if _text(value)}
    return any(value in {key.casefold() for key in _KNOWN_RELIABLE} for value in normalized)


def _url(item: dict[str, Any]) -> str | None:
    value = _text(item.get("url") or item.get("link") or item.get("content_url"))
    if not value:
        return None
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _title(item: dict[str, Any]) -> str:
    return _text(item.get("title") or item.get("headline") or item.get("summary"))


def _relevant(item: dict[str, Any], terms: list[str]) -> bool:
    haystack = " ".join(_text(item.get(key)) for key in ("title", "headline", "summary", "content", "topic"))
    if not haystack or not terms:
        return False
    return any(term.casefold() in haystack.casefold() for term in terms)


def _serial_time(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (datetime, date)) else value


def _source_record(item: dict[str, Any], published: Any) -> dict[str, Any]:
    # Keep the original published time; never substitute collection time.
    return {
        "title": _title(item) or None,
        "source": _source_name(item) or None,
        "url": _url(item),
        "published_at": _serial_time(published),
    }


def _dedupe_sources(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in items:
        key = (item.get("source"), item.get("url"), item.get("published_at"), item.get("title"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _time_source_date(source: dict[str, Any]) -> date | None:
    for key in ("触发日", "爆发观测日", "trade_date", "date", "first_limit_date", "earliest_first_limit_date"):
        parsed = _parse_date(source.get(key))
        if parsed is not None:
            return parsed
    return None


def _trigger_time(theme: dict[str, Any], facts: dict[str, Any], trigger: date) -> tuple[time, str]:
    steps = facts.get("mainline_five_steps") or {}
    step2 = steps.get("step2") if isinstance(steps, dict) else {}
    actual = step2.get("actual") if isinstance(step2, dict) and isinstance(step2.get("actual"), dict) else {}
    for source, keys in (
        (actual, ("最早首板时间", "首板最早时间", "最早封板时间", "触发时间", "first_limit_time", "earliest_first_limit_time")),
        (facts, ("earliest_first_limit_time", "first_limit_time")),
        (theme, ("earliest_first_limit_time", "first_limit_time")),
    ):
        if not isinstance(source, dict) or _time_source_date(source) != trigger:
            continue
        for key in keys:
            parsed = _parse_intraday_time(source.get(key))
            if parsed is not None:
                return parsed, key
    return time(9, 30), "conservative_window_start"


def _evidence_cutoff(theme: dict[str, Any], facts: dict[str, Any], trigger: date | None, as_of: date) -> tuple[datetime, str]:
    if trigger is not None and trigger <= as_of:
        trigger_time, basis = _trigger_time(theme, facts, trigger)
        return datetime.combine(trigger, trigger_time), basis
    return datetime.combine(as_of, time.max), "analysis_day_end"


def _valid_positive_source(item: dict[str, Any], terms: list[str], cutoff: datetime) -> tuple[dict[str, Any], str] | None:
    published_raw = item.get("published_at") or item.get("display_at") or item.get("publish_time") or item.get("date")
    published_dt = _parse_datetime(published_raw)
    if published_dt is None or published_dt > cutoff:
        return None
    available_raw = item.get("available_time") or item.get("available_at")
    if available_raw is not None:
        available_dt = _parse_datetime(available_raw)
        if available_dt is None or available_dt > cutoff:
            return None
    if not _relevant(item, terms):
        return None
    text = _title(item) + " " + _text(item.get("summary"))
    if any(term in text for term in _NEGATIVE):
        return None
    if not _source_reliable(item) or not _url(item):
        return None
    return _source_record(item, published_raw), text


def _classify_catalyst(theme: dict[str, Any], facts: dict[str, Any], news: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    terms = _theme_terms(theme)
    step1 = (facts.get("mainline_five_steps") or {}).get("step1") or {}
    actual = step1.get("actual") if isinstance(step1.get("actual"), dict) else {}
    trigger = _parse_date(facts.get("trigger_date"))
    cutoff, cutoff_basis = _evidence_cutoff(theme, facts, trigger, as_of)
    strong: list[dict[str, Any]] = []
    industry: list[dict[str, Any]] = []
    for item in news:
        if not isinstance(item, dict):
            continue
        valid = _valid_positive_source(item, terms, cutoff)
        if valid is None:
            continue
        record, text = valid
        if any(term in text for term in _STRONG):
            strong.append(record)
        elif any(term in text for term in _INDUSTRY):
            industry.append(record)

    if step1.get("passed") is True and not strong:
        step1_items: list[dict[str, Any]] = []
        if actual:
            step1_items.append({
                "title": actual.get("消息标题") or actual.get("title"),
                "summary": actual.get("摘要") or actual.get("summary"),
                "source": actual.get("来源") or actual.get("source"),
                "source_key": actual.get("source_key"),
                "source_kind": actual.get("source_kind"),
                "source_level": actual.get("source_level"),
                "url": actual.get("URL") or actual.get("url"),
                "published_at": actual.get("发布时间原文") or actual.get("消息日期") or actual.get("published_at"),
                "available_time": actual.get("available_time") or actual.get("available_at"),
            })
        source_rows = step1.get("sources") if isinstance(step1.get("sources"), list) else []
        step1_items.extend(row for row in source_rows if isinstance(row, dict))
        for row in step1_items:
            valid = _valid_positive_source(row, terms, cutoff)
            if valid is None:
                continue
            record, text = valid
            if any(term in text for term in (*_STRONG, *_INDUSTRY)):
                strong.append(record)
    if strong:
        return {"level": "strong", "label": "强催化", "reason": "存在触发前可核验的强催化消息。", "sources": _dedupe_sources(strong), "cutoff_time": cutoff.isoformat(), "cutoff_basis": cutoff_basis}
    if industry:
        return {"level": "industry", "label": "产业催化", "reason": "存在与题材直接关联、来源和发布时间可核验的产业催化。", "sources": _dedupe_sources(industry), "cutoff_time": cutoff.isoformat(), "cutoff_basis": cutoff_basis}
    return {"level": "unverified", "label": "待核实", "reason": "未取得同时满足题材关联、来源、URL、发布时间和触发前可用条件的正向催化证据。", "sources": [], "cutoff_time": cutoff.isoformat(), "cutoff_basis": cutoff_basis}


def _signal_value(fact: dict[str, Any] | None, key: str) -> bool | None:
    if not isinstance(fact, dict) or not isinstance(fact.get("passed"), bool):
        return None
    return fact["passed"]


def _fact_has_source_for_date(fact: dict[str, Any], expected: date) -> bool:
    sources = fact.get("sources") if isinstance(fact.get("sources"), list) else []
    for item in sources:
        if not isinstance(item, dict):
            continue
        if not _text(item.get("source") or item.get("source_name") or item.get("source_key")):
            continue
        if _parse_date(item.get("published_at") or item.get("date") or item.get("trade_date")) == expected:
            return True
    return False


def _breadth_signal(facts: dict[str, Any], as_of: date) -> bool | None:
    step2 = (facts.get("mainline_five_steps") or {}).get("step2") or {}
    passed = step2.get("passed")
    actual = step2.get("actual") if isinstance(step2.get("actual"), dict) else {}
    trigger = _parse_date(facts.get("trigger_date") or actual.get("触发日") or actual.get("爆发观测日"))
    if trigger is None:
        return None
    if trigger > as_of:
        return None
    source_ok = _fact_has_source_for_date(step2, trigger)
    if not source_ok:
        return None
    count = actual.get("首板数")
    has_count = count is not None
    try:
        count_value = None if isinstance(count, bool) or count is None else float(count)
    except (TypeError, ValueError):
        count_value = None
    if count_value is not None and isfinite(count_value):
        return count_value >= 5
    if has_count:
        return None
    if isinstance(passed, bool):
        return passed
    return None


def _capacity_signal(facts: dict[str, Any], as_of: date) -> bool | None:
    step3 = (facts.get("mainline_five_steps") or {}).get("step3") or {}
    passed = step3.get("passed")
    if not isinstance(passed, bool):
        return None
    trigger = _parse_date(facts.get("trigger_date"))
    if trigger is None or trigger > as_of:
        return None
    if not _fact_has_source_for_date(step3, trigger):
        return None
    return passed


def _pending_reasons(facts: dict[str, Any], breadth: bool | None, capacity: bool | None) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    pending: list[str] = []
    steps = facts.get("mainline_five_steps") or {}
    step1 = steps.get("step1") or {}
    step2 = steps.get("step2") or {}
    step3 = steps.get("step3") or {}
    step4 = steps.get("step4") or {}
    step5 = steps.get("step5") or {}
    if step1.get("passed") is False:
        reasons.append("原步骤1消息条件未通过")
    elif step1.get("passed") is None:
        pending.append("原步骤1消息待核验")
    if step2.get("passed") is False:
        reasons.append("原步骤2板块突破或量能条件未通过")
    elif step2.get("passed") is None:
        pending.append("原步骤2事实待核验")
    if breadth is True and step2.get("passed") is not True:
        reasons.append("真实触发日09:30–10:30首板扩散达到5只，新层保留扩散信号；原步骤2结论不变")
    if capacity is False:
        reasons.append("原步骤3容量中军条件未通过")
    elif capacity is None:
        pending.append("容量中军证据待核验")
    if step4.get("passed") is False:
        pending.append("老龙/重启未通过，作为增强信息保留")
    elif step4.get("passed") is None:
        pending.append("老龙/重启待核验，作为增强信息保留")
    if step5.get("passed") is False:
        pending.append("次日验证未通过")
    elif step5.get("passed") is None:
        pending.append("次日待验证")
    return reasons, pending


def discover_candidate(
    target: date | str,
    theme: dict[str, Any],
    facts: dict[str, Any] | None = None,
    *,
    news: list[dict[str, Any]] | None = None,
    as_of: date | str | None = None,
) -> dict[str, Any]:
    """Return the additive candidate-discovery contract for one theme."""
    theme = theme if isinstance(theme, dict) else {}
    facts = facts if isinstance(facts, dict) else theme
    analysis_date = _as_of(target, as_of)
    eligible = theme.get("candidate_eligible")
    eligible = eligible if isinstance(eligible, bool) else None
    try:
        reference = MainlineEngine().evaluate({**theme, **facts}).get("confirmed")
    except Exception:
        reference = None
    if not isinstance(reference, bool):
        reference = False
    catalyst = _classify_catalyst(theme, facts, news or [], analysis_date)
    breadth = _breadth_signal(facts, analysis_date)
    capacity = _capacity_signal(facts, analysis_date)
    evidence_date = _parse_date(facts.get("trigger_date"))
    if evidence_date is None or evidence_date > analysis_date:
        evidence_date = None
    response = evidence_date is not None and (breadth is True or capacity is True)
    catalyst_positive = catalyst["level"] in {"strong", "industry"}
    catalyst_path = catalyst_positive and response
    market_path = evidence_date is not None and breadth is True and capacity is True
    path = "both" if catalyst_path and market_path else "catalyst" if catalyst_path else "market" if market_path else None
    reasons, pending = _pending_reasons(facts, breadth, capacity)
    if catalyst_positive and not response:
        reasons.append("催化已记录，但尚无首板扩散、板块突破或容量中军市场响应")
    if market_path:
        reasons.append("首板扩散与容量中军同时具备，形成市场驱动候选")
    if catalyst_path:
        reasons.append("可核验催化与市场响应形成催化驱动候选")
    if not reasons and catalyst["level"] == "unverified":
        pending.append("催化待核实")
    if eligible is False:
        status, path = "excluded", None
        reasons = ["原题材candidate_eligible明确为false，独立发现层不得提升"]
        pending = []
    elif eligible is None:
        status, path = "unknown", None
        reasons = ["原题材candidate_eligible未知，独立发现层不得提升"]
        pending = ["等待candidate_eligible明确为true"]
    elif path:
        status = "candidate"
    elif breadth is None and capacity is None and catalyst["level"] == "unverified":
        status = "unknown"
    else:
        status = "watch"
    labels = {"candidate": "主线候选", "watch": "继续观察", "excluded": "已排除", "unknown": "待核验"}
    return {
        "version": DISCOVERY_VERSION,
        "status": status,
        "label": labels[status],
        "path": path,
        "as_of": analysis_date.isoformat(),
        "evidence_date": evidence_date.isoformat() if evidence_date else None,
        "reasons": list(dict.fromkeys(reasons)),
        "pending": list(dict.fromkeys(pending)),
        "catalyst": catalyst,
        "signals": {"breadth": breadth, "capacity": capacity},
        "reference_confirmed": reference,
    }


candidate_discovery = discover_candidate
build_candidate_discovery = discover_candidate

__all__ = [
    "DISCOVERY_VERSION", "discover_candidate", "candidate_discovery",
    "build_candidate_discovery",
]
