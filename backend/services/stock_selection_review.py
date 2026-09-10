"""Read-only review of saved stock-selection snapshots.

This module deliberately does not refresh market data.  A review is an audit
of what was persisted at selection time and of daily bars that were already in
the local database when the review is requested.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from statistics import median
from typing import Any

from sqlalchemy import and_, or_, select

from database import async_session
from models import StockDailyBar, StockSelectionRun


REVIEW_WINDOWS = (5, 10, 20)
SHANGHAI = ZoneInfo("Asia/Shanghai")
SELECTION_PARAMETER_KEYS = (
    "mode",
    "risk_profile",
    "horizon",
    "top_n",
    "sector",
    "sector_code",
    "selection_style",
    "sector_limit",
    "factor_filters",
)


def _safe_text(value: object) -> str:
    return str(value or "").strip()


def _code(value: object) -> str:
    return _safe_text(value).upper()[:10]


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _freeze(value: object) -> object:
    """Make JSON parameter trees comparable without depending on key order."""
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    number = _finite(value)
    if isinstance(value, (int, float)) and number is not None:
        return number
    return value


def _parameters(result: object) -> tuple[dict[str, object] | None, list[str]]:
    if not isinstance(result, Mapping):
        return None, list(SELECTION_PARAMETER_KEYS)
    raw = result.get("selection_parameters")
    if not isinstance(raw, Mapping):
        return None, list(SELECTION_PARAMETER_KEYS)
    missing = [key for key in SELECTION_PARAMETER_KEYS if key not in raw]
    if missing:
        return None, missing
    return {key: raw[key] for key in SELECTION_PARAMETER_KEYS}, []


def _parameter_difference(left: Mapping[str, object], right: Mapping[str, object]) -> list[str]:
    return [
        key for key in SELECTION_PARAMETER_KEYS
        if _freeze(left.get(key)) != _freeze(right.get(key))
    ]


def _items(result: object, *, include_watch: bool = True) -> list[dict[str, Any]]:
    """Return the persisted selected/watchlist names, including zero picks."""
    if not isinstance(result, Mapping):
        return []
    values: list[object] = []
    # A watchlist is useful even when the run produced zero recommendations.
    # Keep recommendations first so a duplicate code keeps its selected name.
    keys = ("recommendations", "watchlist") if include_watch else ("recommendations",)
    for key in keys:
        candidate = result.get(key)
        if isinstance(candidate, list):
            values.extend(candidate)
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, Mapping):
            continue
        code = _code(item.get("code") or item.get("stock_code") or item.get("symbol"))
        if not code or code in seen:
            continue
        seen.add(code)
        name = _safe_text(item.get("name") or item.get("stock_name"))
        reason = _item_reason(item)
        qualification = item.get("qualification") or {}
        output.append({"code": code, "name": name, "reason": reason,
                       "status": qualification.get("status", "legacy")})
    return output


def _item_reason(item: Mapping[str, object]) -> str:
    qualification = item.get("qualification")
    if isinstance(qualification, Mapping) and qualification.get("reasons"):
        return "；".join(str(value) for value in qualification["reasons"])[:300]
    for key in ("reason", "selection_reason", "verdict", "judgement", "message"):
        value = _safe_text(item.get(key))
        if value:
            return value[:200]
    sources = item.get("selection_sources")
    if isinstance(sources, list):
        labels = [_safe_text(value) for value in sources if _safe_text(value)]
        if labels:
            return "选股来源：" + "、".join(labels[:5])
    return "已保存于本轮名单"


def _comparison(current: object, previous: object | None) -> dict[str, Any]:
    current_params, current_missing = _parameters(current)
    if current_params is None:
        return {
            "comparable": False,
            "reason": "当前记录缺少完整筛选条件，无法进行同条件对比",
            "added": [], "retained": [], "removed": [],
        }
    if previous is None:
        return {
            "comparable": False,
            "reason": "最近100次记录中未找到筛选条件相同的较早记录",
            "added": [], "retained": [], "removed": [],
        }
    previous_params, previous_missing = _parameters(previous)
    if previous_params is None:
        return {
            "comparable": False,
            "reason": "较早记录缺少完整筛选条件，无法进行同条件对比",
            "added": [], "retained": [], "removed": [],
        }
    differences = _parameter_difference(current_params, previous_params)
    if differences:
        return {
            "comparable": False,
            "reason": "两次筛选条件不同，暂不直接比较名单变化",
            "added": [], "retained": [], "removed": [],
        }
    current_items = {item["code"]: item for item in _items(current)}
    previous_items = {item["code"]: item for item in _items(previous)}
    added = [current_items[key] for key in current_items.keys() - previous_items.keys()]
    retained = [current_items[key] for key in current_items.keys() & previous_items.keys()]
    removed = [previous_items[key] for key in previous_items.keys() - current_items.keys()]
    labels = {"qualified": "合格候选", "watch": "待验证观察", "excluded": "已排除", "legacy": "历史未复核"}
    for item in retained:
        prior_status = previous_items[item["code"]]["status"]
        if prior_status != item["status"]:
            item["reason"] = f"{labels.get(prior_status, prior_status)} → {labels.get(item['status'], item['status'])}；{item['reason']}"
    excluded = {str(item.get("code")): item for item in (current.get("excluded") or [])}
    for item in removed:
        item["reason"] = _item_reason(excluded[item["code"]]) if item["code"] in excluded else "本轮未进入候选与观察名单；可能与召回范围或排名变化有关，不能据此判定原逻辑失效"
    for values in (added, retained, removed):
        values.sort(key=lambda item: item["code"])
    return {
        "comparable": True,
        "reason": "已与最近一次相同筛选条件的记录比较",
        "added": added,
        "retained": retained,
        "removed": removed,
    }


def _is_adjusted_source(source: object) -> bool:
    """StockDailyBar has no adjustment column; only explicit qfq sources qualify."""
    return "qfq" in _safe_text(source).lower()


def _date_value(value: object) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_safe_text(value)[:10])
    except ValueError:
        return None


def _drawdown_pct(closes: list[float]) -> float | None:
    if not closes or any(value <= 0 or not math.isfinite(value) for value in closes):
        return None
    peak = closes[0]
    worst = 0.0
    for close in closes:
        peak = max(peak, close)
        worst = min(worst, (close / peak - 1.0) * 100.0)
    return worst


def _empty_window() -> dict[str, Any]:
    return {
        "sample_count": 0,
        "pending_count": 0,
        "missing_count": 0,
        "positive_rate_pct": None,
        "mean_return_pct": None,
        "median_return_pct": None,
        "mean_max_drawdown_pct": None,
    }


def _performance(
    run: StockSelectionRun,
    selected: list[dict[str, Any]],
    bars: list[StockDailyBar],
    market_dates: list[date],
    *, now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    closed_through = current.date() if current.time().replace(tzinfo=None) >= time(15, 30) else current.date() - timedelta(days=1)
    created = run.created_at
    if created is not None:
        created = created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created
    # A scan of an old cached quote did not exist on the quote's original day.
    snapshot_date = max(filter(None, (run.data_date, created.astimezone(SHANGHAI).date() if created else None)), default=None)
    market_dates = sorted({value for value in market_dates if value <= closed_through})
    methodology = (
        "仅统计当时保存的入选名单，观察名单不计入成绩。以记录创建日期与行情日期中较晚者之后的首个已记录市场交易日收盘为基准，"
        "只使用已收盘且复权来源一致的历史日线；缺少首日或中间日线不会顺延窗口。"
        "5/10/20期按本地已记录市场交易日计数，尚未核对完整交易日历；数据缺失、复权口径不明与观察期未完成分别标记。"
        "这里是收盘观察收益，未扣交易成本，不代表可成交收益或实盘成绩。"
    )
    aggregate: dict[int, dict[str, Any]] = {sessions: _empty_window() for sessions in REVIEW_WINDOWS}
    by_code: dict[str, list[StockDailyBar]] = {}
    for bar in bars:
        if bar.trade_date > closed_through:
            continue
        by_code.setdefault(_code(bar.stock_code), []).append(bar)
    for rows in by_code.values():
        rows.sort(key=lambda row: row.trade_date)

    stock_outputs: list[dict[str, Any]] = []
    for selected_item in selected:
        code = selected_item["code"]
        rows = by_code.get(code, [])
        adjusted = [
            row for row in rows
            if _is_adjusted_source(row.source) and _finite(row.close_price) is not None
        ]
        expected_baseline_date = next((value for value in market_dates if snapshot_date and value > snapshot_date), None)
        baseline = next((row for row in adjusted if row.trade_date == expected_baseline_date), None)
        observed_date = baseline.trade_date.isoformat() if baseline else None
        windows: list[dict[str, Any]] = []
        for sessions in REVIEW_WINDOWS:
            summary = aggregate[sessions]
            result: dict[str, Any] = {
                "sessions": sessions,
                "status": "missing",
                "return_pct": None,
                "max_drawdown_pct": None,
            }
            if snapshot_date is None:
                result["status"] = "missing"
                summary["missing_count"] += 1
            elif expected_baseline_date is None:
                result["status"] = "pending"
                summary["pending_count"] += 1
            elif baseline is None:
                result["status"] = "missing"
                summary["missing_count"] += 1
            else:
                future_dates = [item for item in market_dates if item > baseline.trade_date]
                expected_dates = future_dates[:sessions]
                if len(expected_dates) < sessions:
                    result["status"] = "pending"
                    summary["pending_count"] += 1
                else:
                    row_by_date = {row.trade_date: row for row in adjusted}
                    window_rows = [row_by_date.get(item) for item in expected_dates]
                    if any(row is None or row.source != baseline.source for row in window_rows):
                        result["status"] = "missing"
                        summary["missing_count"] += 1
                    else:
                        closes = [float(baseline.close_price)] + [float(row.close_price) for row in window_rows if row]
                        if any(close <= 0 or not math.isfinite(close) for close in closes):
                            result["status"] = "missing"
                            summary["missing_count"] += 1
                        else:
                            result["status"] = "complete"
                            result["return_pct"] = round((closes[-1] / closes[0] - 1.0) * 100.0, 4)
                            result["max_drawdown_pct"] = round(_drawdown_pct(closes), 4)
                            summary["sample_count"] += 1
                            summary.setdefault("_returns", []).append(result["return_pct"])
                            summary.setdefault("_drawdowns", []).append(result["max_drawdown_pct"])
            windows.append(result)
        stock_outputs.append({
            "code": code,
            "name": selected_item.get("name") or _safe_text(baseline.stock_name if baseline else ""),
            "observed_date": observed_date,
            "windows": windows,
        })

    summaries: list[dict[str, Any]] = []
    for sessions in REVIEW_WINDOWS:
        summary = aggregate[sessions]
        returns = summary.pop("_returns", [])
        drawdowns = summary.pop("_drawdowns", [])
        summary.update({
            "positive_rate_pct": round(sum(value > 0 for value in returns) / len(returns) * 100.0, 2) if returns else None,
            "mean_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
            "median_return_pct": round(float(median(returns)), 4) if returns else None,
            "mean_max_drawdown_pct": round(sum(drawdowns) / len(drawdowns), 4) if drawdowns else None,
        })
        summaries.append({"sessions": sessions, **summary})
    return {"methodology": methodology, "windows": summaries, "stocks": stock_outputs}


class StockSelectionReviewService:
    async def review(self, run_id: int) -> dict[str, Any] | None:
        async with async_session() as session:
            run = await session.get(StockSelectionRun, run_id)
            if run is None:
                return None
            prior_rows = list((await session.execute(
                select(StockSelectionRun)
                .where(or_(
                    StockSelectionRun.created_at < run.created_at,
                    and_(
                        StockSelectionRun.created_at == run.created_at,
                        StockSelectionRun.id < run.id,
                    ),
                ))
                .order_by(StockSelectionRun.created_at.desc(), StockSelectionRun.id.desc())
                .limit(100)
            )).scalars().all())
            selected = _items(run.result, include_watch=False)
            codes = [item["code"] for item in selected]
            bars = list((await session.execute(
                select(StockDailyBar)
                .where(StockDailyBar.stock_code.in_(codes))
                .order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
            )).scalars().all()) if codes else []
            market_dates = list((await session.execute(
                select(StockDailyBar.trade_date).distinct().order_by(StockDailyBar.trade_date)
            )).scalars().all())

        current_params, current_missing = _parameters(run.result)
        compatible: StockSelectionRun | None = None
        mismatch_fields: list[str] = []
        incomplete_previous = False
        if current_params is not None:
            for candidate in prior_rows:
                prior_params, _ = _parameters(candidate.result)
                if prior_params is None:
                    incomplete_previous = True
                    continue
                differences = _parameter_difference(current_params, prior_params)
                if not differences:
                    compatible = candidate
                    break
                mismatch_fields.extend(differences)
        comparison = _comparison(run.result, compatible.result if compatible else None)
        if compatible is None and current_params is not None and mismatch_fields:
            unique_fields = list(dict.fromkeys(mismatch_fields))
            comparison["reason"] = "最近100次记录中未找到筛选条件相同的较早记录"
        elif compatible is None and current_params is not None and incomplete_previous:
            comparison["reason"] = "较早记录缺少完整筛选条件，无法进行同条件对比"
        performance = _performance(run, selected, bars, market_dates)
        return {
            "run_id": run.id,
            "previous_run_id": compatible.id if compatible else None,
            "comparison": comparison,
            "performance": performance,
        }


stock_selection_review_service = StockSelectionReviewService()
