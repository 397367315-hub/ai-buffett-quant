"""Configurable technical/fundamental screener with persistent quote fallback."""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from typing import Any

from quant.market_cache import load_quant_market_snapshot
from services.data_collector import (
    as_float,
    as_int,
    as_optional_float,
    collector,
    is_a_share_market_session,
    shanghai_now,
)


SORT_FIELDS = {
    "volume_ratio": ("f10", "volume_ratio"),
    "main_inflow": ("f62", "main_net_inflow"),
    "change_pct": ("f3", "change_pct"),
    "turnover": ("f8", "turnover"),
    "roe": ("f62", "roe"),
}

BASE_CRITERIA: dict[str, Any] = {
    "preset": "basic",
    "change_pct": [2.0, 20.0],
    "turnover_pct": [3.0, 100.0],
    "volume_ratio": [1.2, 20.0],
    "pe_ttm": [-1000.0, 100.0],
    "pb_max": 100.0,
    "roe_min": -100.0,
    "market_cap_yi": [0.0, 100000.0],
    "main_net_inflow_yi_min": -1000.0,
    "main_net_inflow_pct_min": -100.0,
    "amount_yi_min": 0.0,
    "amplitude_pct_max": 100.0,
    "price": [0.01, 10000.0],
    "require_profitable": False,
    "exclude_special": True,
    "exclude_star_market": False,
    "exclude_gem": False,
    "exclude_bse": True,
    "sort_by": "volume_ratio",
    "limit": 100,
}

SCREENER_PRESETS: dict[str, dict[str, Any]] = {
    "basic": {**BASE_CRITERIA},
    "short": {
        **BASE_CRITERIA,
        "preset": "short",
        "change_pct": [0.5, 7.0],
        "turnover_pct": [2.0, 18.0],
        "volume_ratio": [1.2, 6.0],
        "pe_ttm": [-1000.0, 120.0],
        "market_cap_yi": [20.0, 1500.0],
        "main_net_inflow_yi_min": 0.0,
        "main_net_inflow_pct_min": 0.0,
        "amount_yi_min": 0.5,
        "amplitude_pct_max": 12.0,
        "sort_by": "volume_ratio",
    },
    "long": {
        **BASE_CRITERIA,
        "preset": "long",
        "change_pct": [-4.0, 6.0],
        "turnover_pct": [0.2, 10.0],
        "volume_ratio": [1.2, 4.0],
        "pe_ttm": [0.01, 50.0],
        "pb_max": 6.0,
        "roe_min": 8.0,
        "market_cap_yi": [50.0, 10000.0],
        "main_net_inflow_yi_min": -5.0,
        "main_net_inflow_pct_min": -10.0,
        "amount_yi_min": 0.2,
        "amplitude_pct_max": 10.0,
        "require_profitable": True,
        "sort_by": "roe",
    },
}

SCREENER_SCHEMA = [
    {"key": "change_pct", "label": "当日涨跌幅", "type": "range", "unit": "%", "min": -20, "max": 20, "step": 0.5},
    {"key": "turnover_pct", "label": "换手率", "type": "range", "unit": "%", "min": 0, "max": 100, "step": 0.5},
    {"key": "volume_ratio", "label": "量比（下限严格大于）", "type": "range", "min": 0, "max": 20, "step": 0.1, "lower_bound_operator": "gt"},
    {"key": "pe_ttm", "label": "PE(TTM)", "type": "range", "min": -1000, "max": 1000, "step": 1},
    {"key": "pb_max", "label": "最高PB", "type": "number", "min": 0, "max": 1000, "step": 0.1},
    {"key": "roe_min", "label": "最低ROE", "type": "number", "unit": "%", "min": -100, "max": 200, "step": 0.5},
    {"key": "market_cap_yi", "label": "总市值", "type": "range", "unit": "亿元", "min": 0, "max": 100000, "step": 10},
    {"key": "main_net_inflow_yi_min", "label": "最低主力净流入", "type": "number", "unit": "亿元", "min": -1000, "max": 1000, "step": 0.1},
    {"key": "main_net_inflow_pct_min", "label": "最低主力净流入占比", "type": "number", "unit": "%", "min": -100, "max": 100, "step": 0.5},
    {"key": "amount_yi_min", "label": "最低成交额", "type": "number", "unit": "亿元", "min": 0, "max": 10000, "step": 0.1},
    {"key": "amplitude_pct_max", "label": "最高振幅", "type": "number", "unit": "%", "min": 0, "max": 100, "step": 0.5},
    {"key": "price", "label": "股价", "type": "range", "unit": "元", "min": 0.01, "max": 10000, "step": 0.1},
]


def _number(value: object, key: str, lower: float, upper: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} 必须是数字") from exc
    if not math.isfinite(result) or not lower <= result <= upper:
        raise ValueError(f"{key} 必须在 {lower:g} 到 {upper:g} 之间")
    return round(result, 4)


def normalize_screener_criteria(raw: dict | None) -> dict[str, Any]:
    if raw is not None and not isinstance(raw, dict):
        raise ValueError("筛选条件必须是对象")
    raw = raw or {}
    preset = str(raw.get("preset") or "basic").strip().lower()
    if preset not in {*SCREENER_PRESETS, "custom"}:
        raise ValueError("preset 仅支持 basic、short、long 或 custom")
    config = dict(SCREENER_PRESETS.get(preset, BASE_CRITERIA))
    unknown = sorted(set(raw) - set(BASE_CRITERIA))
    if unknown:
        raise ValueError(f"筛选条件包含未知字段：{', '.join(unknown)}")
    config.update(raw)
    config["preset"] = preset

    range_limits = {
        "change_pct": (-20.0, 20.0),
        "turnover_pct": (0.0, 100.0),
        "volume_ratio": (0.0, 20.0),
        "pe_ttm": (-1000.0, 1000.0),
        "market_cap_yi": (0.0, 100000.0),
        "price": (0.01, 10000.0),
    }
    for key, (lower, upper) in range_limits.items():
        values = config.get(key)
        if not isinstance(values, (list, tuple)) or len(values) != 2:
            raise ValueError(f"{key} 必须是包含上下限的数组")
        start = _number(values[0], f"{key}[0]", lower, upper)
        end = _number(values[1], f"{key}[1]", lower, upper)
        if start > end:
            raise ValueError(f"{key} 下限不能高于上限")
        config[key] = [start, end]

    scalar_limits = {
        "pb_max": (0.0, 1000.0),
        "roe_min": (-100.0, 200.0),
        "main_net_inflow_yi_min": (-1000.0, 1000.0),
        "main_net_inflow_pct_min": (-100.0, 100.0),
        "amount_yi_min": (0.0, 10000.0),
        "amplitude_pct_max": (0.0, 100.0),
    }
    for key, (lower, upper) in scalar_limits.items():
        config[key] = _number(config.get(key), key, lower, upper)
    for key in ("require_profitable", "exclude_special", "exclude_star_market", "exclude_gem", "exclude_bse"):
        if not isinstance(config.get(key), bool):
            raise ValueError(f"{key} 必须是布尔值")
    sort_by = str(config.get("sort_by") or "volume_ratio").strip().lower()
    if sort_by not in SORT_FIELDS:
        raise ValueError(f"sort_by 仅支持 {', '.join(SORT_FIELDS)}")
    config["sort_by"] = sort_by
    try:
        limit = int(config.get("limit", 100))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit 必须是整数") from exc
    if not 10 <= limit <= 200:
        raise ValueError("limit 必须在 10 到 200 之间")
    config["limit"] = limit
    return config


class TechnicalScreenerService:
    @staticmethod
    def _filter(stocks: list[dict], config: dict[str, Any]) -> tuple[list[dict], dict[str, int]]:
        selected: list[dict] = []
        rejected: dict[str, int] = defaultdict(int)

        def reject(reasons: list[str]) -> None:
            for reason in set(reasons):
                rejected[reason] += 1

        for stock in stocks:
            code = str(stock.get("code") or "")
            name = str(stock.get("name") or "")
            price = as_optional_float(stock.get("price"))
            reasons: list[str] = []
            if price is None or price <= 0:
                reasons.append("price_missing")
            elif not config["price"][0] <= price <= config["price"][1]:
                reasons.append("price")
            if config["exclude_special"] and ("ST" in name.upper() or "退" in name):
                reasons.append("special")
            if config["exclude_star_market"] and code.startswith(("688", "689")):
                reasons.append("star_market")
            if config["exclude_gem"] and code.startswith(("300", "301", "302")):
                reasons.append("gem")
            if config["exclude_bse"] and code.startswith(("4", "8", "920")):
                reasons.append("bse")

            numeric_ranges = {
                "change_pct": ("change_pct", config["change_pct"], 1.0),
                "turnover": ("turnover", config["turnover_pct"], 1.0),
                "volume_ratio": ("volume_ratio", config["volume_ratio"], 1.0),
                "market_cap": ("market_cap", config["market_cap_yi"], 1e8),
            }
            for reason, (field, bounds, divisor) in numeric_ranges.items():
                value = as_optional_float(stock.get(field))
                if value is None:
                    reasons.append(f"{reason}_missing")
                elif reason == "volume_ratio":
                    # The user-facing volume-ratio threshold is exclusive: 1.20
                    # is rejected when the configured lower bound is 1.20.
                    actual = value / divisor
                    if not bounds[0] < actual <= bounds[1]:
                        reasons.append(reason)
                elif not bounds[0] <= value / divisor <= bounds[1]:
                    reasons.append(reason)

            pe = as_optional_float(stock.get("pe"))
            if pe is None:
                reasons.append("pe_missing")
            elif not config["pe_ttm"][0] <= pe <= config["pe_ttm"][1]:
                reasons.append("pe")
            if config["require_profitable"] and (pe is None or pe <= 0):
                reasons.append("profitable")

            scalar_minimums = {
                "roe": ("roe", config["roe_min"], 1.0),
                "main_inflow": ("main_net_inflow", config["main_net_inflow_yi_min"], 1e8),
                "main_inflow_pct": ("main_net_inflow_pct", config["main_net_inflow_pct_min"], 1.0),
                "amount": ("amount", config["amount_yi_min"], 1e8),
            }
            for reason, (field, minimum, divisor) in scalar_minimums.items():
                value = as_optional_float(stock.get(field))
                if value is None:
                    reasons.append(f"{reason}_missing")
                elif value / divisor < minimum:
                    reasons.append(reason)

            pb = as_optional_float(stock.get("pb"))
            if pb is None:
                reasons.append("pb_missing")
            elif pb > config["pb_max"]:
                reasons.append("pb")
            amplitude = as_optional_float(stock.get("amplitude"))
            if amplitude is None:
                reasons.append("amplitude_missing")
            elif amplitude > config["amplitude_pct_max"]:
                reasons.append("amplitude")

            if reasons:
                reject(reasons)
            else:
                selected.append(stock)

        sort_field = SORT_FIELDS[config["sort_by"]][1]
        selected.sort(key=lambda stock: as_float(stock.get(sort_field), float("-inf")), reverse=True)
        return selected, dict(sorted(rejected.items()))

    async def _source_snapshot(self, config: dict[str, Any]) -> tuple[dict, bool]:
        market_open = is_a_share_market_session()
        if not market_open:
            cached = await load_quant_market_snapshot()
            if cached.get("stocks"):
                return cached, True

        upstream_sort = SORT_FIELDS[config["sort_by"]][0]
        try:
            live = await asyncio.wait_for(
                collector.fetch_technical_screener({
                    "min_change": -100,
                    "max_pe": 0,
                    "min_turnover": 0,
                    "sort_field": upstream_sort,
                    "page_size": 500,
                    "exclude_special": False,
                }),
                timeout=8.0,
            )
        except Exception as exc:
            print(f"Technical screener live snapshot failed: {type(exc).__name__}")
            live = {}
        if live.get("stocks"):
            return {"source": "eastmoney", **live, "complete": False}, False

        cached = await load_quant_market_snapshot()
        if cached.get("stocks"):
            return cached, True
        return {"stocks": [], "source": "eastmoney", "is_realtime": False}, False

    async def run(self, raw: dict | None = None) -> dict[str, Any]:
        config = normalize_screener_criteria(raw)
        snapshot, cache_used = await self._source_snapshot(config)
        candidates = [stock for stock in snapshot.get("stocks") or [] if isinstance(stock, dict)]
        matches, rejection_counts = self._filter(candidates, config)
        returned = matches[:config["limit"]]
        now = shanghai_now()
        return {
            "stocks": returned,
            "total": len(matches),
            "returned_count": len(returned),
            "candidate_count": len(candidates),
            "criteria": config,
            "rejection_counts": rejection_counts,
            "source": "cache" if cache_used else str(snapshot.get("source") or "eastmoney"),
            "cache_used": cache_used,
            "coverage_complete": bool(snapshot.get("complete")),
            "data_date": snapshot.get("data_date"),
            "source_updated_at": snapshot.get("source_updated_at") or snapshot.get("cached_at"),
            "is_realtime": bool(snapshot.get("is_realtime")) and not cache_used,
            "updated_at": now.isoformat(),
            "schema": SCREENER_SCHEMA,
            "presets": SCREENER_PRESETS,
        }


technical_screener_service = TechnicalScreenerService()
