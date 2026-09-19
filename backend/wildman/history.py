"""Bounded, date-aware historical evidence for the Wildman adapters.

This module deliberately computes observations from dated limit-up and theme
membership rows. It does not turn same-day sealing order into causation and
never treats an absent historical value as zero.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from math import isfinite
from typing import Any, Iterable


MAX_HISTORY_TRADING_DAYS = 20


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def normalize_trade_date(value: Any) -> date | None:
    """Normalize provider dates without assigning a date to missing data."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        text = text[:10]
    elif len(text) >= 8 and text[:8].isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def normalize_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text.split(".", 1)[0].zfill(6) if text else ""


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in ("stocks", "rows", "items", "data"):
            if isinstance(value.get(key), list):
                return [row for row in value[key] if isinstance(row, dict)]
        return [value]
    if isinstance(value, (list, tuple)):
        return [row for row in value if isinstance(row, dict)]
    return []


def bounded_trade_dates(
    target: Any,
    values: Iterable[Any] = (),
    *,
    limit: int = MAX_HISTORY_TRADING_DAYS,
) -> list[date]:
    """Return latest distinct observed dates at or before target.

    Weekdays are not treated as trading days merely because they look like
    trading days.
    """
    target_date = normalize_trade_date(target)
    if target_date is None:
        return []
    dates = set()
    for value in values:
        day = normalize_trade_date(value)
        if day is not None and day <= target_date:
            dates.add(day)
    dates.add(target_date)
    return sorted(dates)[-max(int(limit), 1):]


def normalize_limit_up_history(
    value: Any,
    *,
    requested_date: Any = None,
    direction: str = "up",
) -> list[dict[str, Any]]:
    """Normalize NumCat/EastMoney limit-pool rows."""
    fallback = normalize_trade_date(requested_date)
    output: list[dict[str, Any]] = []
    for raw in _rows(value):
        code = normalize_code(raw.get("code") or raw.get("symbol"))
        day = normalize_trade_date(raw.get("trade_date") or raw.get("tradedate"))
        date_quality = raw.get("_date_quality") or "observed_row_date"
        if day is None and fallback is not None:
            day = fallback
            date_quality = "request_scoped_date"
        if not code or day is None:
            continue
        height = _number(raw.get("continuous_days") or raw.get("limit_times"))
        output.append({
            **raw,
            "code": code,
            "trade_date": day.isoformat(),
            "date_quality": date_quality,
            "continuous_days": int(height) if height is not None and height >= 0 else None,
            "limit_direction": raw.get("limit_direction") or direction,
        })
    return output


def normalize_theme_member_history(
    value: Any,
    *,
    requested_date: Any = None,
) -> list[dict[str, Any]]:
    """Normalize dated theme_members responses to one row per theme/day."""
    fallback = normalize_trade_date(requested_date)
    output: list[dict[str, Any]] = []
    for raw in _rows(value):
        theme_name = str(raw.get("theme_name") or "").strip()
        theme_symbol = str(raw.get("theme_symbol") or raw.get("theme_id") or "").strip()
        theme = theme_name or theme_symbol
        members = raw.get("symbols") or raw.get("member_codes") or raw.get("members") or []
        if isinstance(members, str):
            members = members.replace(";", ",").split(",")
        if not isinstance(members, (list, tuple)):
            members = []
        codes = list(dict.fromkeys(normalize_code(item) for item in members if normalize_code(item)))
        day = normalize_trade_date(raw.get("trade_date") or raw.get("tradedate"))
        date_quality = raw.get("_date_quality") or "observed_row_date"
        if day is None and fallback is not None:
            day = fallback
            date_quality = "request_scoped_date"
        if not theme or day is None:
            continue
        output.append({
            **raw,
            "theme_key": theme,
            "theme_name": theme_name or None,
            "theme_name_verified": bool(theme_name),
            "symbols": codes,
            "trade_date": day.isoformat(),
            "date_quality": date_quality,
        })
    return output


def _theme_key(row: dict[str, Any]) -> str | None:
    values = _theme_values(row)
    return values[0] if values else None


def _explicit_theme_key(row: dict[str, Any]) -> str | None:
    for key in ("primary_theme_name", "theme_name", "theme_symbol", "theme_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return None


def _theme_values(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    keys = (
        "primary_theme_name", "theme_name", "theme_symbol", "theme_id",
        "theme", "sector", "industry", "theme_names_xgb",
        "theme_names_kpl", "theme_names_jygs",
    )
    for key in keys:
        raw = row.get(key)
        items = raw if isinstance(raw, (list, tuple)) else str(raw or "").replace(";", ",").split(",")
        for item in items:
            value = str(item or "").strip()
            if value and value not in values:
                values.append(value)
    return values


def _date_string(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _bar_map(bars: Any) -> dict[str, dict[date, dict[str, Any]]]:
    result: dict[str, dict[date, dict[str, Any]]] = defaultdict(dict)
    groups = bars.items() if isinstance(bars, dict) else [(None, bars)]
    for group_code, group in groups:
        if isinstance(group, dict):
            group = group.get("bars") or group.get("rows") or []
        elif not isinstance(group, (list, tuple)):
            group = [group]
        if not isinstance(group, (list, tuple)):
            continue
        for raw in group:
            if isinstance(raw, dict):
                code = normalize_code(raw.get("code") or raw.get("symbol") or group_code)
                values = raw
            else:
                code = normalize_code(group_code)
                values = {
                    key: getattr(raw, key, None)
                    for key in (
                        "trade_date", "open_price", "close_price", "high_price",
                        "low_price", "volume", "open", "close", "high", "low", "vol",
                    )
                }
            day = normalize_trade_date(values.get("trade_date") or values.get("tradedate") or values.get("date"))
            if code and day:
                result[code][day] = values
    return result


def _bar_value(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


def _sentiment_rows(value: Any) -> list[Any]:
    if isinstance(value, dict):
        value = value.get("rows") or value.get("items") or value.get("data") or []
    return list(value or []) if isinstance(value, (list, tuple)) else []


def _sentiment_value(row: Any, *keys: str) -> str:
    for key in keys:
        value = row.get(key) if isinstance(row, dict) else getattr(row, key, None)
        text = str(value or "").strip().lower()
        if text:
            return text
    return ""


def _cross_cycle_window(
    sentiments: Any,
    target_date: date | None,
) -> tuple[date, date] | None:
    rows: list[tuple[date, str]] = []
    for row in _sentiment_rows(sentiments):
        raw_day = row.get("trade_date") if isinstance(row, dict) else getattr(row, "trade_date", None)
        day = normalize_trade_date(raw_day)
        stage = _sentiment_value(row, "cycle", "stage", "phase", "market_cycle", "emotion_phase")
        if day and target_date and day <= target_date and stage:
            rows.append((day, stage))
    rows.sort()
    early = {"ice", "ice_proxy", "冰点", "退潮", "retreat", "retreat_proxy", "cold"}
    later = {
        "start", "启动", "启动/试错", "主升", "发酵/主升",
        "main_rise", "main_rise_proxy", "rise", "上升",
    }
    for index, (early_day, stage) in enumerate(rows):
        if stage not in early:
            continue
        for later_day, later_stage in rows[index + 1:]:
            if later_stage in later:
                return early_day, later_day
    return None


def _has_cross_cycle_transition(sentiments: Any, target_date: date | None) -> bool:
    return _cross_cycle_window(sentiments, target_date) is not None


def _fact_basis(
    *,
    code: str,
    theme: str | None,
    dates: list[date],
    event_dates: list[date],
    membership_dates: list[date],
    missing: list[str],
) -> dict[str, Any]:
    return {
        "history_source": ["numcat_limit_pool", "numcat_thememembers_jx"],
        "source_refs": [
            {"source": "numcat_limit_pool", "scope": "dated limit-up/failed-limit rows"},
            {"source": "numcat_thememembers_jx", "scope": "dated membership snapshots"},
        ],
        "symbol": code,
        "theme": theme,
        "observed_dates": [_date_string(day) for day in dates],
        "limit_up_dates": [_date_string(day) for day in event_dates],
        "membership_dates": [_date_string(day) for day in membership_dates],
        "missing_reasons": list(dict.fromkeys(missing)),
        "association_basis": "multi_day_leadership_proxy",
        "causal_boundary": "correlation_only_no_causal_claim; same_day_seal_order_is_not_causation",
    }


def derive_historical_overrides(
    target: Any,
    current_rows: Any,
    limit_up_rows: Any = (),
    theme_member_rows: Any = (),
    *,
    failed_limit_rows: Any = (),
    bars: Any = None,
    sentiments: Any = None,
    requested_dates: Iterable[Any] = (),
) -> dict[str, Any]:
    """Build compact stock/theme/market overrides from dated observations."""
    target_date = normalize_trade_date(target)
    current = _rows(current_rows)
    current_by_code = {
        normalize_code(row.get("code") or row.get("symbol")): row
        for row in current
        if normalize_code(row.get("code") or row.get("symbol"))
    }
    current_theme = {code: _explicit_theme_key(row) for code, row in current_by_code.items()}
    industry_by_code = {
        code: str(row.get("industry") or row.get("sector") or "").strip() or None
        for code, row in current_by_code.items()
    }
    up = normalize_limit_up_history(limit_up_rows)
    failed = normalize_limit_up_history(failed_limit_rows, direction="failed")
    # Candidates include failed limits and yesterday's leaders; only the
    # dated up-limit history proves an actual limit-up observation.
    members = normalize_theme_member_history(theme_member_rows)
    observed_values = [row.get("trade_date") for row in (*up, *failed, *members)]
    observed_values.extend(requested_dates)
    dates = bounded_trade_dates(target_date, observed_values)
    if not dates and target_date:
        dates = [target_date]
    allowed_dates = set(dates)

    pool_by_day: dict[date, dict[str, dict[str, Any]]] = defaultdict(dict)
    failed_by_day: dict[date, set[str]] = defaultdict(set)
    event_dates_by_code: dict[str, set[date]] = defaultdict(set)
    heights_by_code: dict[str, list[tuple[date, int | None]]] = defaultdict(list)
    for row in up:
        day = normalize_trade_date(row.get("trade_date"))
        code = normalize_code(row.get("code"))
        if day and code and day in allowed_dates:
            pool_by_day[day][code] = row
            event_dates_by_code[code].add(day)
            heights_by_code[code].append((day, row.get("continuous_days")))
    for row in failed:
        day = normalize_trade_date(row.get("trade_date"))
        code = normalize_code(row.get("code"))
        if day and code and day in allowed_dates:
            failed_by_day[day].add(code)

    membership: dict[date, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    themes_by_code: dict[str, set[str]] = defaultdict(set)
    verified_theme_names: set[str] = set()
    for row in members:
        day = normalize_trade_date(row.get("trade_date"))
        theme = str(row.get("theme_key") or "").strip()
        if day is None or not theme or day > (target_date or day):
            continue
        if day not in allowed_dates:
            continue
        membership[day][theme].update(row.get("symbols") or [])
        if row.get("theme_name_verified") is True:
            verified_theme_names.add(theme)
        for code in row.get("symbols") or []:
            themes_by_code[code].add(theme)

    for code in current_by_code:
        if target_date in allowed_dates and code in pool_by_day.get(target_date, {}):
            for theme, codes in membership.get(target_date, {}).items():
                if code in codes:
                    themes_by_code[code].add(theme)

    theme_codes: dict[str, set[str]] = defaultdict(set)
    for day in membership:
        for theme, codes in membership[day].items():
            theme_codes[theme].update(codes)
    for code, themes in themes_by_code.items():
        for theme in themes:
            theme_codes[theme].add(code)

    bar_map = _bar_map(bars)
    all_codes = set(current_by_code)
    stock_overrides: dict[str, dict[str, Any]] = {}
    theme_overrides: dict[str, dict[str, Any]] = {}
    old_dragon_codes: list[str] = []
    cross_cycle_codes: list[str] = []
    first_divergence_codes: list[str] = []

    for code in sorted(all_codes):
        row = current_by_code[code]
        stock_days = sorted(event_dates_by_code.get(code, set()))
        code_themes = sorted(themes_by_code.get(code, set()))
        target_membership = membership.get(target_date, {}) if target_date else {}
        target_up_codes = set(pool_by_day.get(target_date, {})) if target_date else set()
        target_themes = [
            candidate for candidate, codes in target_membership.items()
            if code in codes
        ]
        named_target_themes = [
            candidate for candidate in target_themes
            if candidate in verified_theme_names
        ]
        target_themes = named_target_themes or target_themes
        if target_themes:
            theme = max(
                target_themes,
                key=lambda candidate: (
                    len(target_membership[candidate] & target_up_codes),
                    candidate == current_theme.get(code),
                    candidate,
                ),
            )
        else:
            theme = current_theme.get(code) or industry_by_code.get(code)
        theme_is_industry_proxy = theme is not None and theme == industry_by_code.get(code) and theme not in code_themes
        theme_days = sorted({day for day, by_theme in membership.items() if theme and code in by_theme.get(theme, set())})
        heights = [height for _, height in heights_by_code.get(code, []) if height is not None]
        max_height = max(heights, default=None)
        theme_heights = [
            item.get("continuous_days")
            for day in dates
            for item in pool_by_day.get(day, {}).values()
            if theme
            and item.get("code") in membership.get(day, {}).get(theme, set())
            and item.get("continuous_days") is not None
        ]
        leader_height = max([int(value) for value in theme_heights], default=None)

        leader_established: date | None = None
        if theme:
            qualifying = [
                day
                for day in dates
                if any(
                    item.get("code") in theme_codes.get(theme, set())
                    and item.get("code") in membership.get(day, {}).get(theme, set())
                    and _number(item.get("continuous_days")) is not None
                    and _number(item.get("continuous_days")) >= 4
                    for item in pool_by_day.get(day, {}).values()
                )
            ]
            leader_established = min(qualifying) if qualifying else None
        stock_start = min(stock_days) if stock_days else None
        earliest_theme_start: date | None = None
        if theme:
            theme_event_dates = [
                day for day in dates
                if any(
                    item.get("code") in membership.get(day, {}).get(theme, set())
                    for item in pool_by_day.get(day, {}).values()
                )
            ]
            earliest_theme_start = min(theme_event_dates) if theme_event_dates else None
        after_leader = stock_start > leader_established if stock_start and leader_established else None
        early_start = stock_start == earliest_theme_start if stock_start and earliest_theme_start else None
        if early_start is True and after_leader is True:
            early_start = False

        mature_dates = sorted(
            day for day, height in heights_by_code.get(code, [])
            if height is not None and height >= 4
        )
        session_index = {day: index for index, day in enumerate(dates)}
        heights_by_day = {
            day: height
            for day, height in heights_by_code.get(code, [])
            if height is not None
        }
        reappears_after_gap = False
        for previous_day, current_day in zip(stock_days, stock_days[1:]):
            interrupted_sessions = (
                session_index.get(current_day, -1)
                - session_index.get(previous_day, -1)
                - 1
            )
            previous_wave_mature = any(
                mature_day <= previous_day for mature_day in mature_dates
            )
            current_round_started = heights_by_day.get(current_day) == 1
            if interrupted_sessions >= 3 and previous_wave_mature and current_round_started:
                reappears_after_gap = True
                break
        old_dragon = True if reappears_after_gap else None
        mature_phases = [
            day for day in mature_dates
            if any(
                session_index.get(other, -1) - session_index.get(day, -1) - 1 >= 3
                for other in mature_dates
                if other > day and other in session_index and day in session_index
            )
        ]
        transition_window = _cross_cycle_window(sentiments, target_date)
        cross_missing: list[str] = []
        prior_mature_day: date | None = None
        post_mature_day: date | None = None
        anchor_low: float | None = None
        transition_low: float | None = None
        transition_bar_dates: list[date] = []
        if transition_window is None:
            cross_missing.append("missing_observed_retreat_to_start_transition")
        else:
            transition_start, transition_end = transition_window
            prior_mature_candidates = [
                day for day in mature_dates if day < transition_start
            ]
            post_mature_candidates = [
                day for day in mature_dates if day > transition_end
            ]
            prior_mature_day = max(prior_mature_candidates, default=None)
            post_mature_day = max(post_mature_candidates, default=None)
            if prior_mature_day is None:
                cross_missing.append("missing_prior_wave_mature_4_board")
            if post_mature_day is None:
                cross_missing.append("missing_post_transition_4_board")
            if post_mature_day is not None and (
                not stock_days
                or stock_days[-1] != post_mature_day
                or (heights_by_day.get(post_mature_day) or 0) < 4
            ):
                cross_missing.append("latest_observation_not_post_transition_4_board")
            transition_dates = [
                day for day in dates
                if transition_start <= day <= transition_end
            ]
            transition_bar_dates = [
                day for day in transition_dates
                if code in bar_map and day in bar_map[code]
                and _bar_value(bar_map[code][day], "low_price", "low") is not None
            ]
            if len(transition_dates) < 2 or len(transition_bar_dates) != len(transition_dates):
                cross_missing.append("incomplete_transition_daily_bar_coverage")
            if prior_mature_day is not None:
                prior_wave_days = [
                    day for day in stock_days if day <= prior_mature_day
                ]
                prior_wave_start = min(prior_wave_days, default=prior_mature_day)
                anchor_dates = [
                    day for day in sorted(bar_map.get(code, {}))
                    if day < prior_wave_start
                ][-5:]
                anchor_values = [
                    _bar_value(bar_map[code][day], "low_price", "low")
                    for day in anchor_dates
                ]
                anchor_values = [value for value in anchor_values if value is not None]
                anchor_low = min(anchor_values, default=None)
                if anchor_low is None:
                    cross_missing.append("missing_prior_wave_anchor_daily_bar")
            transition_values = [
                _bar_value(bar_map[code][day], "low_price", "low")
                for day in transition_bar_dates
            ]
            transition_values = [value for value in transition_values if value is not None]
            transition_low = min(transition_values, default=None)
            if (
                anchor_low is not None
                and transition_low is not None
                and transition_low < anchor_low
            ):
                cross_missing.append("transition_low_broke_prior_wave_anchor")
        cross_cycle = True if not cross_missing else None
        if old_dragon is True:
            old_dragon_codes.append(code)
        if cross_cycle is True:
            cross_cycle_codes.append(code)

        recent_divergence_dates = set(dates[-2:])
        divergence_days = sorted(
            day for day in failed_by_day
            if day in recent_divergence_dates
            and code in failed_by_day[day]
            and any(previous < day for previous in stock_days)
        )
        first_divergence = True if divergence_days else None
        if isinstance(row.get("first_divergence"), bool):
            first_divergence = row["first_divergence"]
        support: bool | None = None
        first_volume: bool | None = None
        divergence_low: float | None = None
        if divergence_days:
            divergence_day = divergence_days[0]
            candle = bar_map.get(code, {}).get(divergence_day)
            prior_days = [day for day in bar_map.get(code, {}) if day < divergence_day]
            prior = bar_map.get(code, {}).get(max(prior_days)) if prior_days else None
            close = _bar_value(candle or {}, "close_price", "close")
            opening = _bar_value(candle or {}, "open_price", "open")
            low = _bar_value(candle or {}, "low_price", "low")
            divergence_low = low
            previous_close = _bar_value(prior or {}, "close_price", "close")
            if None not in (close, opening, low):
                support = close >= opening and close > low
                if previous_close is not None:
                    support = support and close >= previous_close
            volume = _bar_value(candle or {}, "volume", "vol")
            previous_volume = _bar_value(prior or {}, "volume", "vol")
            first_volume = volume >= previous_volume * 1.5 if volume is not None and previous_volume and previous_volume > 0 else None
            if len([day for day in failed_by_day if code in failed_by_day[day]]) != 1:
                first_volume = None
        if isinstance(row.get("first_divergence_support"), bool):
            support = row["first_divergence_support"]
        if first_divergence is True:
            first_divergence_codes.append(code)

        linkage: bool | None = None
        if theme and theme_days and len(dates) >= 2:
            for leader_day in theme_days:
                later_days = [day for day in dates if day > leader_day and (day - leader_day).days <= 5]
                followers = any(
                    other != code
                    and other in membership.get(day, {}).get(theme, set())
                    and other in pool_by_day.get(day, {})
                    for day in later_days
                    for other in pool_by_day.get(day, {})
                )
                if followers:
                    linkage = True
                    break
            if linkage is None and len(stock_days) >= 2 and leader_height is not None and max_height is not None and max_height >= leader_height:
                linkage = False
        if linkage is None and isinstance(row.get("theme_linkage"), bool):
            linkage = row["theme_linkage"]

        lead_days: list[date] = []
        follower_codes: set[str] = set()
        if theme:
            for day in dates:
                members = membership.get(day, {}).get(theme, set())
                own = pool_by_day.get(day, {}).get(code)
                own_height = _number((own or {}).get("continuous_days"))
                if not members or own_height is None or code not in members:
                    continue
                other_heights = [
                    _number((pool_by_day.get(day, {}).get(other) or {}).get("continuous_days"))
                    for other in members
                    if other != code and other in pool_by_day.get(day, {})
                ]
                other_heights = [value for value in other_heights if value is not None]
                if other_heights and own_height <= max(other_heights):
                    continue
                lead_days.append(day)
                for later_day in dates:
                    if later_day <= day or (later_day - day).days > 5:
                        continue
                    later_members = membership.get(later_day, {}).get(theme, set())
                    for other in later_members:
                        if other != code and other in pool_by_day.get(later_day, {}):
                            follower_codes.add(other)
        early_lead_start = (
            stock_start is not None
            and earliest_theme_start is not None
            and stock_start == earliest_theme_start
        )
        leadership_proxy: bool | None = None
        leadership_missing: list[str] = []
        if len(lead_days) < 3:
            leadership_missing.append("fewer_than_3_complete_lead_days")
        if early_lead_start is not True:
            leadership_missing.append("missing_early_theme_start_for_leadership_proxy")
        if len(follower_codes) < 2:
            leadership_missing.append("fewer_than_2_dated_theme_followers")
        if not leadership_missing:
            leadership_proxy = True

        independent = row.get("independent_theme_leadership") if isinstance(row.get("independent_theme_leadership"), bool) else None
        missing: list[str] = []
        if len(dates) < 2:
            missing.append("insufficient_theme_history")
        if not stock_days:
            missing.append("missing_dated_limit_up_history")
        if not theme_days:
            missing.append("missing_dated_theme_membership")
        if leader_established is None:
            missing.append("missing_leader_height_4_date")
        if first_divergence is None:
            missing.append("missing_prior_limit_up_or_divergence_history")
        if old_dragon is None:
            missing.append("missing_prior_mature_reappearance_sequence")
        if cross_cycle is None:
            missing.extend(cross_missing or ["missing_cycle_transition_survival_evidence"])
        if theme_is_industry_proxy:
            missing.append("missing_verified_theme_symbol_membership")
        if support is None and first_divergence is True:
            missing.append("missing_divergence_day_bar")
        if linkage is None:
            missing.append("insufficient_follower_sequence_for_linkage_proxy")
        if leadership_proxy is None:
            missing.extend(leadership_missing)
        basis = _fact_basis(
            code=code,
            theme=theme,
            dates=dates,
            event_dates=stock_days,
            membership_dates=theme_days,
            missing=missing,
        )
        basis["same_day_seal_order"] = "association_only; not used to infer independent_theme_leadership"
        basis["theme_resolution"] = (
            "target_day_membership_primary_theme"
            if theme in target_themes and theme in verified_theme_names
            else "industry_proxy_or_unverified_theme"
        )
        stock_overrides[code] = {
            "primary_theme_name": theme,
            "industry_name": industry_by_code.get(code),
            "industry_theme_proxy": theme_is_industry_proxy,
            "stock_start_date": _date_string(stock_start),
            "leader_established_date": _date_string(leader_established),
            "early_theme_start": early_start,
            "supplement_started_after_leader": after_leader,
            "leader_height": leader_height,
            "old_dragon": old_dragon,
            "cross_cycle": cross_cycle,
            "cross_cycle_evidence": {
                "status": "confirmed" if cross_cycle is True else "待确认",
                "transition_start": _date_string(transition_window[0]) if transition_window else None,
                "transition_end": _date_string(transition_window[1]) if transition_window else None,
                "prior_wave_mature_date": _date_string(prior_mature_day),
                "post_transition_mature_date": _date_string(post_mature_day),
                "prior_wave_anchor_low": anchor_low,
                "transition_low": transition_low,
                "transition_bar_coverage": {
                    "required_dates": [_date_string(day) for day in (
                        [
                            day for day in dates
                            if transition_window
                            and transition_window[0] <= day <= transition_window[1]
                        ]
                    )],
                    "observed_dates": [_date_string(day) for day in transition_bar_dates],
                    "complete": not any(
                        reason == "incomplete_transition_daily_bar_coverage"
                        for reason in cross_missing
                    ),
                },
                "missing_reasons": cross_missing,
                "basis": "daily_low_anchor_and_post_transition_height_proxy",
                "causal_claim": False,
            },
            "first_divergence": first_divergence,
            "first_volume_divergence": first_volume,
            "first_divergence_support": support,
            "divergence_low": divergence_low,
            "yesterday_divergence": (
                True
                if len(dates) >= 2
                and dates[-2] in failed_by_day
                and code in failed_by_day[dates[-2]]
                else None
            ),
            "theme_linkage": linkage,
            "leadership_proxy": leadership_proxy,
            "leadership_proxy_evidence": {
                "status": "confirmed" if leadership_proxy is True else "待确认",
                "lead_days": [_date_string(day) for day in lead_days],
                "lead_day_count": len(lead_days),
                "early_theme_start": early_lead_start,
                "follower_codes": sorted(follower_codes),
                "follower_count": len(follower_codes),
                "requirements": {
                    "minimum_complete_lead_days": 3,
                    "minimum_distinct_followers": 2,
                    "causal_claim": False,
                },
                "missing_reasons": leadership_missing,
                "basis": "multi_day_theme_height_leadership_proxy_only",
            },
            "independent_theme_leadership": independent,
            "fact_basis": basis,
        }

    for theme in sorted(theme_codes):
        theme_event_dates = sorted({
            day for day in dates
            if any(
                item.get("code") in membership.get(day, {}).get(theme, set())
                for item in pool_by_day.get(day, {}).values()
            )
        })
        heights = [
            _number(item.get("continuous_days"))
            for day in dates
            for item in pool_by_day.get(day, {}).values()
            if item.get("code") in membership.get(day, {}).get(theme, set())
        ]
        theme_leader_date = min(
            (
                day for day in theme_event_dates
                if any(
                    item.get("code") in theme_codes[theme]
                    and item.get("code") in membership.get(day, {}).get(theme, set())
                    and _number(item.get("continuous_days")) is not None
                    and _number(item.get("continuous_days")) >= 4
                    for item in pool_by_day.get(day, {}).values()
                )
            ),
            default=None,
        )
        theme_stocks = [stock_overrides[code] for code in theme_codes[theme] if code in stock_overrides]
        theme_overrides[theme] = {
            "history_dates": [_date_string(day) for day in theme_event_dates],
            "earliest_start_date": _date_string(min(theme_event_dates)) if theme_event_dates else None,
            "leader_established_date": _date_string(theme_leader_date),
            "leader_height": int(max((value for value in heights if value is not None), default=0)) or None,
            "old_dragon_active": True if any(item.get("old_dragon") is True for item in theme_stocks) else None,
            "supplement_started": True if any(item.get("supplement_started_after_leader") is True for item in theme_stocks) else None,
            "historical_member_count": len(theme_codes[theme]),
            "history_fact_basis": {
                "source": ["numcat_thememembers_jx", "numcat_limit_pool"],
                "dates": [_date_string(day) for day in theme_event_dates],
                "member_sequence": "dated membership and limit-up overlap only",
                "causal_boundary": "correlation_only_no_causal_claim",
                "missing_reasons": ["insufficient_theme_history"] if len(theme_event_dates) < 2 else [],
            },
        }

    if not theme_overrides:
        theme_overrides = {
            str(_theme_key(row)): {
                "history_dates": [],
                "earliest_start_date": None,
                "leader_established_date": None,
                "leader_height": None,
                "old_dragon_active": None,
                "supplement_started": None,
                "history_fact_basis": {
                    "source": ["numcat_thememembers_jx", "numcat_limit_pool"],
                    "dates": [],
                    "missing_reasons": ["missing_dated_theme_membership"],
                    "causal_boundary": "correlation_only_no_causal_claim",
                },
            }
            for row in current
            if _theme_key(row)
        }
    for facts in stock_overrides.values():
        primary = facts.get("primary_theme_name")
        if primary and primary not in theme_overrides:
            theme_overrides[primary] = {
                "history_dates": [],
                "earliest_start_date": None,
                "leader_established_date": None,
                "leader_height": None,
                "old_dragon_active": None,
                "supplement_started": None,
                "history_fact_basis": {
                    "source": ["numcat_thememembers_jx"],
                    "dates": [],
                    "missing_reasons": ["missing_dated_theme_membership"],
                    "theme_resolution": "industry_proxy_or_unverified_theme",
                    "causal_boundary": "correlation_only_no_causal_claim",
                },
            }

    market_overrides = {
        "history_dates": [_date_string(day) for day in dates],
        "historical_limit_up_days": len({day for day in pool_by_day}),
        "first_divergence": True if first_divergence_codes else None,
        "old_dragon_codes": sorted(old_dragon_codes),
        "cross_cycle_codes": sorted(cross_cycle_codes),
        "history_fact_basis": {
            "source": ["numcat_limit_pool", "numcat_thememembers_jx"],
            "window_days": len(dates),
            "bounded_to": MAX_HISTORY_TRADING_DAYS,
            "causal_boundary": "correlation_only_no_causal_claim",
            "missing_reasons": ["insufficient_market_history"] if len(dates) < 2 else [],
        },
    }
    return {
        "stock_overrides": stock_overrides,
        "theme_overrides": theme_overrides,
        "market_overrides": market_overrides,
    }


__all__ = [
    "MAX_HISTORY_TRADING_DAYS",
    "bounded_trade_dates",
    "derive_historical_overrides",
    "normalize_code",
    "normalize_limit_up_history",
    "normalize_theme_member_history",
    "normalize_trade_date",
]
