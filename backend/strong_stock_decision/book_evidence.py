"""Read-only evidence helpers for *猎取强势股*.

The book is treated as reference material.  This module reports observable
price/volume proxies and never upgrades them to a book-confirmed trading rule.
It intentionally has no dependency on the decision engines or database layer.
"""

from __future__ import annotations

from datetime import date, datetime
from statistics import mean
from typing import Any, Iterable


PROVENANCE = {
    "book": "股是股非之一：猎取强势股",
    "pages": {
        "three_yang_control_three_yin": "033-034",
        "wash_then_reattack": "038",
        "volume_price_ma": "045-046, 051-052, 061",
        "best_trading_zone": "067, 076-077, 083-084",
    },
    "source_type": "SCANNED_PDF_OCR",
}


def _value(row: Any, key: str) -> float | None:
    aliases = {"close": "close_price", "volume": "volume", "change_pct": "change_pct"}
    raw = row.get(key) if isinstance(row, dict) else getattr(row, aliases.get(key, key), None)
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _date_value(row: Any) -> date | None:
    raw = row.get("trade_date") if isinstance(row, dict) else getattr(row, "trade_date", None)
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw or "")[:10])
    except (TypeError, ValueError):
        return None


def _normalise(rows: Iterable[Any] | None) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    previous: float | None = None
    for row in rows or []:
        close = _value(row, "close")
        volume = _value(row, "volume")
        change = _value(row, "change_pct")
        if change is None and close is not None and previous not in (None, 0):
            change = (close / previous - 1.0) * 100.0
        output.append({"trade_date": _date_value(row), "close": close, "volume": volume, "change_pct": change})
        if close is not None:
            previous = close
    return output


def _unknown(reason: str) -> dict[str, Any]:
    return {
        "status": "UNKNOWN",
        "book_confirmed": False,
        "knowledge_layer": "ENGINE_OBSERVATION_PROXY",
        "reason": reason,
    }


def _observation(observed: bool, *, reason: str, metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "OBSERVED_PROXY" if observed else "NOT_OBSERVED",
        "book_confirmed": False,
        "knowledge_layer": "ENGINE_OBSERVATION_PROXY",
        "reason": reason,
        "metrics": metrics,
    }


def _three_yang(rows: list[dict[str, float | None]]) -> dict[str, Any]:
    usable = [row for row in rows if row["change_pct"] is not None and row["volume"] is not None and row["volume"] >= 0]
    positive = [row["volume"] for row in usable if row["change_pct"] > 0]
    negative = [row["volume"] for row in usable if row["change_pct"] < 0]
    base = {"positive_days": len(positive), "negative_days": len(negative)}
    if not positive or not negative:
        unknown = _unknown("需要同时存在可观测阳量与阴量")
        return {key: {**unknown, "metrics": base} for key in ("yang_more_than_yin", "yang_expand_yin_contract", "yang_cluster_yin_scatter")}

    positive_mean, negative_mean = mean(positive), mean(negative)
    positive_share = sum(positive) / sum(positive + negative) if sum(positive + negative) else None
    negative_share = sum(negative) / sum(positive + negative) if sum(positive + negative) else None
    signs = [1 if row["change_pct"] > 0 else -1 for row in usable]
    runs: list[tuple[int, int]] = []
    for sign in signs:
        if runs and runs[-1][0] == sign:
            runs[-1] = (sign, runs[-1][1] + 1)
        else:
            runs.append((sign, 1))
    positive_runs = [length for sign, length in runs if sign == 1]
    negative_runs = [length for sign, length in runs if sign == -1]
    positive_run_mean, negative_run_mean = mean(positive_runs), mean(negative_runs)
    sequence_metrics = {
        "positive_run_count": len(positive_runs),
        "negative_run_count": len(negative_runs),
        "positive_run_max": max(positive_runs),
        "negative_run_max": max(negative_runs),
        "positive_run_mean": positive_run_mean,
        "negative_run_mean": negative_run_mean,
        "sequence": [{"sign": "阳" if sign == 1 else "阴", "length": length} for sign, length in runs],
    }
    metrics = {**base, "positive_volume_mean": positive_mean, "negative_volume_mean": negative_mean, "positive_volume_share": positive_share, "negative_volume_share": negative_share, "run_sequence_proxy": sequence_metrics}
    return {
        "yang_more_than_yin": _observation(len(positive) > len(negative), reason="阳量日数量多于阴量日数量" if len(positive) > len(negative) else "可观测阳量日未多于阴量日", metrics=metrics),
        "yang_expand_yin_contract": _observation(positive_mean > negative_mean, reason="阳量平均成交量大于阴量平均成交量" if positive_mean > negative_mean else "阳量平均成交量未大于阴量平均成交量", metrics=metrics),
        "yang_cluster_yin_scatter": _observation(
            positive_run_mean > negative_run_mean and max(positive_runs) > max(negative_runs),
            reason="以连续阳量段较长、阴量段更分散作聚散代理" if positive_run_mean > negative_run_mean and max(positive_runs) > max(negative_runs) else "连续阳量段未表现出相对聚合、阴量段分散",
            metrics={**metrics, "cluster_proxy": "run_length_and_run_count"},
        ),
    }


def _wash_then_reattack(rows: list[dict[str, float | None]]) -> dict[str, Any]:
    if len(rows) < 3:
        return _unknown("至少需要洗盘、缩量和再攻击的连续可观测样本")
    usable = rows[-3:]
    if any(row["volume"] is None or row["change_pct"] is None for row in usable):
        return _unknown("序列中缺少成交量或涨跌幅")
    first, second, attack = usable
    contraction = second["volume"] < first["volume"] and second["change_pct"] <= 0
    reattack = attack["change_pct"] > 0 and attack["volume"] > second["volume"]
    return _observation(contraction and reattack, reason="前段缩量回落后出现放量上涨，作为再攻击代理" if contraction and reattack else "未观察到完整的缩量洗盘后放量再攻击序列", metrics={"wash_volume": first["volume"], "contracted_volume": second["volume"], "attack_volume": attack["volume"], "contraction": contraction, "reattack": reattack})


def summarize_hunter_evidence(context: dict[str, Any] | None = None, bars: Iterable[Any] | None = None, zone: Any = None) -> dict[str, Any]:
    """Summarize point-in-time book-rule evidence without issuing a trade action."""
    context = context or {}
    rows = _normalise(bars if bars is not None else context.get("bars"))
    selected_zone = zone if zone is not None else context.get("zone")
    dates = [row["trade_date"] for row in rows]
    as_of = _date_value({"trade_date": context.get("as_of")}) if context.get("as_of") is not None else None
    date_valid = bool(dates) and all(item is not None for item in dates) and all(left <= right for left, right in zip(dates, dates[1:]))
    if as_of is not None:
        date_valid = date_valid and all(item is not None and item <= as_of for item in dates)
    three = _three_yang(rows)
    wash = _wash_then_reattack(rows)
    if selected_zone == "风险C区":
        gate = {"status": "RISK_PRIORITY_BLOCK", "book_confirmed": False, "reason": "书内 C 区为风险区/最佳退出区，不输出介入确认"}
    elif selected_zone == "强势B区":
        gate = {"status": "B_SMALL_A_WAITING_CONFIRMATION", "book_confirmed": False, "reason": "B 区介入点仍需等待其中的小 A 点", "required_confirmation": ["重新转强", "量价与结构继续同向"]}
    elif selected_zone == "强势A区":
        gate = {"status": "A_ZONE_OBSERVATION", "book_confirmed": False, "reason": "A 区仅表示区域语义，仍需点位和后续确认"}
    else:
        gate = {"status": "UNKNOWN", "book_confirmed": False, "reason": "缺少可识别的 A/B/C 区"}
    return {
        "provenance": PROVENANCE,
        "data_quality": {"bar_count": len(rows), "status": "AVAILABLE" if date_valid else "UNKNOWN", "point_in_time": date_valid, "date_order": "MONOTONIC" if date_valid else "UNKNOWN", "as_of": as_of.isoformat() if as_of else None},
        "three_yang_control_three_yin": three,
        "wash_then_reattack": wash,
        "zone_gate": gate,
        "trade_action": "NONE",
    }


__all__ = ["PROVENANCE", "summarize_hunter_evidence"]
