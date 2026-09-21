"""Bounded NumCat evidence acquisition for the screenshot's five-step radar."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select

from database import async_session
from models import PolicyTransmissionRecord
from market_data.numcat.extended_provider import numcat_extended_provider
from market_data.numcat.market_provider import numcat_market_provider
from services.wildman_classic_service import fetch_numcat_history_batch
from wildman.mainline import candidate_name_allowed, derive_mainline_steps, select_trigger_date
from wildman.history import normalize_code, normalize_trade_date


MAX_THEMES = 20
MAX_CORES_PER_THEME = 5
MAX_EXTRA_HISTORIES = 240
BOARD_FIELDS = "symbol,name,type,tradedate,open,high,low,close,pre_close,pct_chg,vol,amount,servertime"
QUOTE_FIELDS = "symbol,name,tradedate,open,high,low,close,pre_close,pct_chg,total_mv,amount"


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0


def _board_key(value: Any) -> str:
    # Only orthographic suffixes, never substitute a related industry index.
    return str(value or "").strip().removesuffix("概念").removesuffix("板块").casefold()


def _bars(rows: list[Any]) -> list[dict]:
    result = []
    for row in rows:
        get = row.get if isinstance(row, dict) else lambda name, default=None: getattr(row, name, default)
        day = normalize_trade_date(get("trade_date") or get("tradedate") or get("date"))
        if day is None:
            continue
        result.append({
            "trade_date": day.isoformat(),
            "code": get("code") or get("symbol") or get("stock_code"),
            "name": get("name"),
            "is_index": get("type") in {"gn", "hy", "yjhy"} or bool(get("is_index")),
            "open": get("open") if get("open") is not None else get("open_price"),
            "high": get("high") if get("high") is not None else get("high_price"),
            "low": get("low") if get("low") is not None else get("low_price"),
            "close": get("close") if get("close") is not None else get("close_price"),
            "volume": get("volume") if get("volume") is not None else get("vol"),
            "change_pct": get("change_pct") if get("change_pct") is not None else get("pct_chg"),
            "previous_close": get("previous_close") if get("previous_close") is not None else get("pre_close"),
            "source": get("source") or "numcat_theme_daily",
        })
    return sorted(result, key=lambda row: row["trade_date"])


def candidate_themes(target: date, legacy: list[dict], inputs: dict) -> list[dict]:
    """Use dated full topic associations, not a stock's one winning label."""
    old = {row["theme_name"]: row for row in legacy if candidate_name_allowed(row.get("theme_name"))}
    up = {
        normalize_code(row.get("code") or row.get("symbol")): row
        for row in inputs.get("limit_history", [])
        if normalize_trade_date(row.get("trade_date") or row.get("tradedate")) == target
    }
    # Retain current pool names/capitalization when history returns fewer fields.
    current = {normalize_code(row.get("code")): row for theme in legacy for row in theme.get("rows", [])}
    found = {}
    for row in inputs.get("member_history", []):
        name = str(row.get("theme_name") or "").strip()
        if not candidate_name_allowed(name) or normalize_trade_date(row.get("trade_date") or row.get("tradedate")) != target:
            continue
        codes = {normalize_code(code) for code in row.get("symbols", [])}
        matched = [current.get(code, up[code]) for code in codes & up.keys()]
        if not matched:
            continue
        heights = [int(_number(item.get("continuous_days") or item.get("limit_times"))) for item in matched]
        found[name] = {
            **old.get(name, {}), "theme_id": name, "theme_name": name,
            "theme_symbol": row.get("theme_symbol") or inputs.get("theme_symbols", {}).get(name),
            "member_codes": sorted(codes), "rows": matched,
            "limit_up_count": len(matched), "max_limit_height": max(heights, default=1),
            "candidate_eligible": True,
        }
    for name, row in old.items():
        if row.get("rows"):
            found.setdefault(name, {**row, "member_codes": [], "candidate_eligible": True})
    return sorted(found.values(), key=lambda row: (row.get("limit_up_count", 0), row.get("max_limit_height", 0)), reverse=True)[:MAX_THEMES]


async def _stored_policy(target: date) -> list[dict]:
    # Reuse existing research evidence; do not create a second news warehouse.
    async with async_session() as session:
        rows = (await session.execute(select(
            PolicyTransmissionRecord.policy_title, PolicyTransmissionRecord.policy_url,
            PolicyTransmissionRecord.source_key, PolicyTransmissionRecord.published_at,
            PolicyTransmissionRecord.available_time,
        ).where(
            PolicyTransmissionRecord.published_at >= datetime.combine(target - timedelta(days=35), time.min),
            PolicyTransmissionRecord.published_at <= datetime.combine(target, time.max),
            PolicyTransmissionRecord.available_time <= datetime.combine(target, time.max),
        ).order_by(PolicyTransmissionRecord.published_at.desc()).limit(160))).all()
    return [{"title": row.policy_title, "url": row.policy_url, "source": row.source_key,
             "published_at": row.published_at.isoformat(), "available_time": row.available_time.isoformat()}
            for row in rows]


async def enrich_mainlines(target: date, legacy: list[dict], history: dict, bars: dict) -> tuple[list[dict], dict]:
    inputs = history.get("mainline_inputs") or {}
    themes = candidate_themes(target, legacy, inputs)
    limit_history = [row for row in inputs.get("limit_history", [])
        if (day := normalize_trade_date(row.get("trade_date") or row.get("tradedate"))) and day <= target]
    member_history = [row for row in inputs.get("member_history", [])
        if (day := normalize_trade_date(row.get("trade_date") or row.get("tradedate"))) and day <= target]
    stock_bars = {code: _bars(rows[-80:]) for code, rows in bars.items()}
    failures: set[str] = set()
    semaphore = asyncio.Semaphore(3)

    async def read(label: str, request, *, timeout: int = 18):
        async with semaphore:
            try:
                return await asyncio.wait_for(request, timeout=timeout)
            except Exception as exc:
                failures.add(f"{label}:{type(exc).__name__}")
                return []

    def calculate(theme: dict, **kwargs) -> dict:
        return derive_mainline_steps(target, theme, limit_history=limit_history,
            member_history=kwargs.pop("member_history", member_history),
            stock_bars=stock_bars, stock_quotes=kwargs.pop("stock_quotes", {}),
            board_bars=kwargs.pop("board_bars", []), news=kwargs.pop("news", []), **kwargs)

    if not themes or not numcat_market_provider.configured:
        return [{**theme, **calculate(theme)} for theme in themes], {
            "status": "unavailable", "source": "numcat", "errors": ["未取得猫爪主线证据"],
        }

    industries, concepts, policy = await asyncio.gather(
        read("industry_catalog", numcat_extended_provider.industry_boards({"type": "hy"})),
        read("concept_catalog", numcat_extended_provider.concept_boards()),
        read("stored_policy", _stored_policy(target)),
    )
    catalog = {}
    for kind, rows in (("gn", concepts), ("hy", industries)):
        for row in rows:
            if candidate_name_allowed(row.get("name")):
                catalog[_board_key(row.get("name"))] = {**row, "type": kind}
    index_by_theme = {theme["theme_name"]: catalog.get(_board_key(theme["theme_name"])) for theme in themes}

    async def board_history(kind: str) -> list[dict]:
        codes = sorted({row["symbol"] for row in index_by_theme.values() if row and row["type"] == kind})
        if not codes:
            return []
        return await read("board_daily", numcat_extended_provider.rows("theme_daily", fields=BOARD_FIELDS, params={
            "type": kind, "symbols": ",".join(codes),
            "startdate": (target - timedelta(days=100)).strftime("%Y%m%d"), "enddate": target.strftime("%Y%m%d"),
        }))

    board_gn, board_hy = await asyncio.gather(board_history("gn"), board_history("hy"))
    index_bars = defaultdict(list)
    index_codes = {str(row["symbol"]) for row in index_by_theme.values() if row}
    for row in [*board_gn, *board_hy]:
        if str(row.get("symbol")) not in index_codes or row.get("type") not in {None, "gn", "hy", "yjhy"}:
            continue
        if (day := normalize_trade_date(row.get("tradedate"))) and day <= target:
            index_bars[str(row.get("symbol"))].append(row)

    trigger_groups: dict[date, list[dict]] = defaultdict(list)
    for theme in themes:
        if trigger := select_trigger_date(target, theme, limit_history, member_history):
            trigger_groups[trigger].append(theme)
    full_members: dict[tuple[str, date], list[str]] = {}
    quotes_by_day: dict[date, dict] = {}

    async def capacity_inputs(day: date, group: list[dict]):
        symbols = sorted({str(theme["theme_symbol"]) for theme in group if theme.get("theme_symbol")})
        if not symbols:
            return
        rows, quotes = await asyncio.gather(
            read("dated_members", numcat_market_provider.theme_members(level="parent", theme_symbols=symbols,
                tradedate=day, cache_result=False)),
            read("dated_capitalization", numcat_extended_provider.rows("screening", fields=QUOTE_FIELDS,
                params={"tradedate": day.strftime("%Y%m%d")})),
        )
        membership = {str(row.get("theme_symbol")): row.get("symbols", []) for row in rows
            if normalize_trade_date(row.get("trade_date") or row.get("tradedate")) in (None, day)}
        member_codes = {normalize_code(code) for codes in membership.values() for code in codes}
        quotes_by_day[day] = {normalize_code(row.get("symbol")): {
            **row, "code": normalize_code(row.get("symbol")), "market_cap": row.get("total_mv"),
            "trade_date": day.isoformat(), "source": "numcat_screening",
        } for row in quotes if normalize_trade_date(row.get("tradedate")) == day and normalize_code(row.get("symbol")) in member_codes}
        for theme in group:
            full_members[(theme["theme_name"], day)] = [normalize_code(code) for code in membership.get(str(theme.get("theme_symbol")), [])]

    await asyncio.gather(*(capacity_inputs(day, group) for day, group in trigger_groups.items()))
    core_codes: dict[str, list[str]] = {}
    extra_codes: set[str] = set()
    for day, group in trigger_groups.items():
        quotes = quotes_by_day.get(day, {})
        for theme in group:
            codes = full_members.get((theme["theme_name"], day), [])
            capacity = [code for code in codes if _number(quotes.get(code, {}).get("market_cap")) >= 10_000_000_000]
            capacity.sort(key=lambda code: _number(quotes[code].get("amount")), reverse=True)
            core_codes[theme["theme_name"]] = capacity[:MAX_CORES_PER_THEME]
            extra_codes.update(code for code in capacity[:MAX_CORES_PER_THEME] if code not in stock_bars)
            # The old wave and the original first-board cohort can disappear
            # from today's limit pool. Keep their histories for causal timing.
            dated_members = {normalize_trade_date(row.get("trade_date") or row.get("tradedate")):
                {normalize_code(code) for code in row.get("symbols", [])}
                for row in member_history if row.get("theme_name") == theme["theme_name"]}
            for row in limit_history:
                row_day = normalize_trade_date(row.get("trade_date") or row.get("tradedate"))
                code = normalize_code(row.get("code") or row.get("symbol"))
                if code in dated_members.get(row_day, set()) and (row_day == day or (row_day and row_day < day and _number(row.get("continuous_days")) >= 4)):
                    if code not in stock_bars:
                        extra_codes.add(code)
    if len(extra_codes) > MAX_EXTRA_HISTORIES:
        failures.add("历史样本超过单次240只上限，未覆盖样本不确认")
        extra_codes = set(sorted(extra_codes)[:MAX_EXTRA_HISTORIES])
    extra_history = await read("core_daily", fetch_numcat_history_batch(sorted(extra_codes), days=60, end_date=target), timeout=35) if extra_codes else {}
    if isinstance(extra_history, dict):
        stock_bars.update({code: _bars(rows) for code, rows in extra_history.items()})

    async def finish(theme: dict) -> dict:
        name = theme["theme_name"]
        news = await read("catalyst_news", numcat_market_provider.news(keyword=_board_key(name), limit=30))
        news = [{**row, "published_at": row.get("published_at") or row.get("display_at"),
                 "source": row.get("source") or row.get("source_name")} for row in news]
        index = index_by_theme.get(name)
        board_rows = _bars(index_bars.get(str(index["symbol"]), [])) if index else []
        trigger = select_trigger_date(target, theme, limit_history, member_history)
        codes = full_members.get((name, trigger), [])
        cores = core_codes.get(name, [])
        # Only selected representative cores need daily histories. The full
        # membership count and sample definition remain visible in evidence.
        quotes = {code: value for code, value in quotes_by_day.get(trigger, {}).items() if code in cores}
        own_members = [row for row in member_history if row.get("theme_name") == name]
        if trigger and codes:
            own_members = [row for row in own_members if normalize_trade_date(row.get("trade_date")) != trigger]
            own_members.append({"theme_name": name, "trade_date": trigger.isoformat(), "symbols": codes})
        facts = calculate({**theme, "member_codes": codes or theme.get("member_codes", []),
            "core_sample_codes": cores,
            "core_capitalization_complete": bool(codes) and all(
                quotes_by_day.get(trigger, {}).get(code, {}).get("market_cap") is not None for code in codes)},
            stock_quotes=quotes, board_bars=board_rows, member_history=own_members, news=[*news, *policy])
        step3 = facts.get("mainline_five_steps", {}).get("step3")
        if step3:
            step3.setdefault("actual", {}).update({
                "板块完整成分数": len(codes) or None, "中军样本数": len(cores),
                "中军范围": "异动当日市值不少于100亿元、成交额前5只",
            })
            if step3.get("passed") is False:
                step3["reason"] = "已核验的百亿市值成交额前5只中军样本未满足趋势和量价条件。"
        for step in facts.get("mainline_five_steps", {}).values():
            for source in step.get("sources", []):
                source_key = source.get("source")
                source.update({
                    "title": {"limit_history": "猫爪·历史涨停池", "board_bars": "猫爪·板块指数日线",
                        "numcat_theme_daily": "猫爪·板块指数日线", "theme_daily": "猫爪·板块指数日线",
                        "stock_quotes": "猫爪·异动日市值", "stock_bars": "猫爪·个股日线",
                        "stockdaily": "猫爪·次日开盘", "auction": "猫爪·竞价"}.get(source_key, source.get("title") or source_key),
                })
                if source_key in {"limit_history", "board_bars", "numcat_theme_daily", "theme_daily", "stock_quotes", "stock_bars", "stockdaily", "auction"}:
                    source["url"] = "https://numcat.net/api-reference?scope=stock"
        return {**theme, **facts, "board_index": {"code": index["symbol"], "name": index["name"], "source": "numcat_theme_daily"} if index else None}

    results = await asyncio.gather(*(finish(theme) for theme in themes))
    unmatched = [name for name, index in index_by_theme.items() if index is None]
    return results, {
        "source": "numcat", "status": "partial" if failures or unmatched else "ready", "errors": sorted(failures),
        "candidate_scope": "行业板块题材统一候选池", "candidate_count": len(results),
        "board_index_matched": sum(index is not None for index in index_by_theme.values()),
        "unmatched_board_indices": unmatched,
        "raw_history_persisted": False, "max_themes": MAX_THEMES, "max_cores_per_theme": MAX_CORES_PER_THEME,
    }
