"""V5 trading-skill runtime: data -> permissions -> skills -> auditable candidates."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select

from database import async_session
from models import (
    MarketDataCache,
    StockAuctionSnapshot,
    StockDailyBar,
    TradingSkillScanSnapshot,
)
from quant.market_cache import load_quant_market_snapshot
from quant.reflexivity_skill import build_reflexivity_diagnosis
from quant.trading_skill_features import build_skill_features
from quant.trading_skills import evaluate_all_skills, evaluate_skill
from services.data_collector import collector, is_a_share_market_session, shanghai_now
from services.trading_skill_registry import (
    get_registered_skill,
    list_registered_skills,
)


SCAN_CACHE_KEY_PREFIX = "trading_skill_runtime_latest_v3"
MAX_SCAN_STOCKS = 120


def _scan_cache_key(*, exclude_star_market: bool, exclude_gem: bool) -> str:
    """Keep candidate caches isolated by the user's tradable-board scope."""
    return f"{SCAN_CACHE_KEY_PREFIX}_star{int(exclude_star_market)}_gem{int(exclude_gem)}"


def _market_segment(code: Any) -> str:
    normalized = str(code or "").upper().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    if normalized.startswith(("688", "689")):
        return "科创板"
    if normalized.startswith(("300", "301", "302")):
        return "创业板"
    if normalized.startswith(("8", "4")):
        return "北交所"
    if normalized.startswith("6"):
        return "沪市主板"
    if normalized.startswith(("000", "001", "002", "003")):
        return "深市主板/中小板"
    return "其他A股"


def _num(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value


def _date(raw: Any) -> date | None:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def _pct_field_to_decimal(value: Any) -> float | None:
    """Convert snapshot percentage points to the feature engine's decimal return."""
    parsed = _num(value)
    return parsed / 100 if parsed is not None else None


def _json_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _flatten_behavior(behavior: dict[str, Any]) -> dict[str, Any]:
    scores = behavior.get("scores") or {}
    signals = {str(item.get("id")): item for item in behavior.get("bias_signals") or []}
    return {
        "behavior_imbalance_score": _num(behavior.get("behavior_imbalance_score") or scores.get("imbalance")),
        "fomo_score": _num(scores.get("fomo")), "panic_score": _num(scores.get("panic")),
        "crowding_score": _num(scores.get("consensus")),
        "false_breakout_score": _num(scores.get("false_breakout")),
        "market_psychology_state": behavior.get("market_psychology_state"),
        "behavior_signals": signals,
    }


def _sector_state(change: float | None, breadth: float | None, flow: float | None) -> str:
    values = [item for item in (change, breadth, flow) if item is not None]
    if not values:
        return "未验证"
    score = (50 + _clamp((change or 0) * 8, -35, 35) + (breadth - 50 if breadth is not None else 0) * 0.35 + _clamp((flow or 0) / 1e8, -15, 15)) / 2
    if score >= 72:
        return "强化"
    if score >= 58:
        return "启势"
    if score <= 34:
        return "退潮"
    if score <= 46:
        return "分歧"
    return "震荡"


class TradingSkillService:
    """Runtime service; all public results retain source and cutoff metadata."""

    async def _snapshot(self, *, force: bool = False) -> dict[str, Any]:
        cached = await load_quant_market_snapshot()
        cached_at = _date_time(cached.get("cached_at") or cached.get("fetched_at"))
        cache_age = (shanghai_now().replace(tzinfo=None) - cached_at).total_seconds() if cached_at else None
        live_window = is_a_share_market_session()
        cache_acceptable = bool(
            cached.get("stocks") and not force
            and (not live_window or cache_age is None or cache_age <= 90)
        )
        if cache_acceptable:
            # Cached complete snapshots are the default outside the trading
            # session and during a brief upstream outage.
            return cached
        try:
            result = await asyncio.wait_for(collector.fetch_quant_market_snapshot(), timeout=28)
            if result.get("stocks"):
                return result
        except Exception as exc:
            if cached.get("stocks"):
                return {**cached, "source": "cache", "cache_reason": type(exc).__name__}
        return cached

    async def _bars(self, codes: list[str], target: date | None) -> dict[str, list[dict[str, Any]]]:
        if not codes:
            return {}
        cutoff = target or shanghai_now().date()
        start = cutoff - timedelta(days=420)
        try:
            async with async_session() as session:
                rows = (await session.execute(
                    select(StockDailyBar).where(
                        StockDailyBar.stock_code.in_(codes),
                        StockDailyBar.trade_date >= start,
                        StockDailyBar.trade_date <= cutoff,
                    ).order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
                )).scalars().all()
        except Exception:
            return {}
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row.stock_code].append({
                "trade_date": row.trade_date, "open_price": row.open_price,
                "close_price": row.close_price, "high_price": row.high_price,
                "low_price": row.low_price, "volume": row.volume,
                "amount": row.amount, "turnover": row.turnover,
                "available_time": row.updated_at.isoformat() if row.updated_at else row.trade_date.isoformat(),
            })
        return dict(grouped)

    async def _auctions(self, codes: list[str], target: date | None) -> dict[str, dict[str, Any]]:
        if not codes:
            return {}
        try:
            async with async_session() as session:
                rows = (await session.execute(
                    select(StockAuctionSnapshot).where(
                        StockAuctionSnapshot.stock_code.in_(codes),
                        StockAuctionSnapshot.trade_date <= (target or shanghai_now().date()),
                    ).order_by(desc(StockAuctionSnapshot.trade_date), desc(StockAuctionSnapshot.quote_at))
                )).scalars().all()
        except Exception:
            return {}
        output: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row.stock_code in output:
                continue
            output[row.stock_code] = {
                "trade_date": row.trade_date, "quote_at": row.quote_at,
                "auction_price": row.auction_price, "previous_close": row.previous_close,
                "auction_volume": row.auction_volume, "auction_amount": row.auction_amount,
                "auction_volume_ratio": row.auction_volume_ratio,
            }
        return output

    async def _workbench(self) -> dict[str, Any]:
        try:
            from services.market_decision_workbench import market_decision_workbench_service
            return await asyncio.wait_for(market_decision_workbench_service.get(), timeout=8)
        except Exception:
            return {}

    @staticmethod
    def _market_permission(forecast: dict[str, Any], workbench: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        health = forecast.get("data_health") or {}
        behavior = forecast.get("behavior") or {}
        behavior_view = _flatten_behavior(behavior)
        v4 = workbench.get("market_way_v4") or {}
        final = v4.get("final_decision") or {}
        code = str(final.get("code") or "")
        completeness = _num(health.get("completeness_pct"))
        reasons: list[str] = []
        if not snapshot.get("stocks"):
            return {"code": "BLOCK", "label": "数据不足，不扫描", "reasons": ["全市场行情快照为空"], "max_candidates": 0}
        if code == "NO_TRADE":
            reasons.append("V4市场决策层当前为NO_TRADE")
            return {"code": "BLOCK", "label": "市场不许可", "reasons": reasons, "max_candidates": 0}
        if completeness is not None and completeness < 45:
            reasons.append(f"V5数据完整度{completeness:.1f}%低于研究阈值")
        if (_num(behavior_view.get("panic_score")) or 0) >= 82:
            reasons.append("恐慌行为分数处于极端观察区")
        if (_num(behavior_view.get("behavior_imbalance_score")) or 0) >= 88:
            reasons.append("市场行为失衡度极高")
        if reasons:
            return {"code": "CAUTION", "label": "仅研究/谨慎观察", "reasons": reasons, "max_candidates": 8}
        return {"code": "ALLOW", "label": "市场许可通过", "reasons": ["市场数据、V5状态与行为层未触发硬阻断"], "max_candidates": 12}

    @staticmethod
    def _active_skill_ids(forecast: dict[str, Any], permission: dict[str, Any]) -> list[str]:
        behavior = forecast.get("behavior") or {}
        state = str((forecast.get("risk_preference") or {}).get("state") or "")
        psychology = str(behavior.get("market_psychology_state") or "")
        if permission.get("code") == "BLOCK":
            return ["skill_08_behavior_imbalance", "skill_03_abnormal_turnover", "skill_10_behavior_reflexivity"]
        if psychology in {"恐慌", "绝望"} or "contraction" in state:
            return ["skill_02_absorption_pressure", "skill_04_false_breakdown_reclaim", "skill_06_low_position_relaunch", "skill_08_behavior_imbalance", "skill_10_behavior_reflexivity"]
        if psychology in {"亢奋", "追逐"} or "enhancement" not in state:
            return ["skill_01_price_volume_efficiency", "skill_03_abnormal_turnover", "skill_07_breakout_quality", "skill_08_behavior_imbalance", "skill_10_behavior_reflexivity"]
        return [
            "skill_01_price_volume_efficiency", "skill_02_absorption_pressure", "skill_03_abnormal_turnover",
            "skill_05_trend_reacceleration", "skill_06_low_position_relaunch", "skill_07_breakout_quality",
            "skill_08_behavior_imbalance", "skill_09_auction_intraday_confirm", "skill_10_behavior_reflexivity",
        ]

    @staticmethod
    def _rank_snapshot(
        stocks: list[dict[str, Any]],
        *,
        exclude_star_market: bool = True,
        exclude_gem: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        # The validation universe and runtime funnel share the default A-share
        # safety boundary: ST/*ST, delisting names and explicit suspension
        # flags cannot become Skill candidates merely because their turnover
        # is large.
        excluded = {"科创板": 0, "创业板": 0, "ST/退市/停牌": 0}
        filtered: list[dict[str, Any]] = []
        for stock in stocks:
            segment = _market_segment(stock.get("code"))
            if exclude_star_market and segment == "科创板":
                excluded[segment] += 1
                continue
            if exclude_gem and segment == "创业板":
                excluded[segment] += 1
                continue
            if (
                "ST" in str(stock.get("name") or "").upper()
                or "退" in str(stock.get("name") or "")
                or bool(stock.get("is_suspended"))
            ):
                excluded["ST/退市/停牌"] += 1
                continue
            filtered.append(stock)
        def rank(stock: dict[str, Any]) -> float:
            return (
                abs(_num(stock.get("change_pct")) or 0) * 2
                + (_num(stock.get("volume_ratio")) or 0) * 4
                + max(0, (_num(stock.get("main_net_inflow")) or 0) / 1e8) * 2
                + min(10, (_num(stock.get("amount")) or 0) / 1e9)
            )
        return sorted(filtered, key=rank, reverse=True)[:MAX_SCAN_STOCKS], excluded

    async def _build(
        self,
        *,
        force: bool = False,
        requested_skill_ids: list[str] | None = None,
        exclude_star_market: bool = True,
        exclude_gem: bool = True,
    ) -> dict[str, Any]:
        now = shanghai_now().replace(tzinfo=None)
        snapshot, workbench = await asyncio.gather(self._snapshot(force=force), self._workbench())
        try:
            from services.forecast_v5 import forecast_v5_service
            forecast = await asyncio.wait_for(forecast_v5_service.dashboard(force=force, include_skills=False), timeout=18)
        except Exception as exc:
            forecast = {"data_health": {}, "behavior": {}, "error": type(exc).__name__}
        source_stocks = snapshot.get("stocks") or []
        stocks, excluded_counts = self._rank_snapshot(
            source_stocks,
            exclude_star_market=exclude_star_market,
            exclude_gem=exclude_gem,
        )
        target = _date(snapshot.get("data_date")) or _date(forecast.get("forecast_date")) or now.date()
        codes = [str(item.get("code") or "") for item in stocks if str(item.get("code") or "")]
        bars, auctions = await asyncio.gather(self._bars(codes, target), self._auctions(codes, target))
        market_returns = [_num(item.get("change_pct")) for item in stocks]
        market_return = sum(item for item in market_returns if item is not None) / max(1, len([item for item in market_returns if item is not None]))
        sectors: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for stock in stocks:
            sector = str(stock.get("sector") or "").strip() or "未分类"
            sectors[sector].append(stock)
        sector_context: dict[str, dict[str, Any]] = {}
        for name, members in sectors.items():
            changes = [_num(item.get("change_pct")) for item in members]
            changes = [item for item in changes if item is not None]
            flows = [_num(item.get("main_net_inflow")) for item in members]
            flows = [item for item in flows if item is not None]
            breadth = sum(item > 0 for item in changes) / len(changes) * 100 if changes else None
            avg_change = sum(changes) / len(changes) if changes else None
            flow = sum(flows) if flows else None
            sector_context[name] = {
                "sector_return_1d": _pct_field_to_decimal(avg_change), "sector_return_20d": None,
                "sector_breadth": breadth, "sector_strength": _clamp(50 + (avg_change or 0) * 8 + ((breadth or 50) - 50) * 0.35),
                "sector_flow": flow, "sector_state": _sector_state(avg_change, breadth, flow),
                "alpha_density": sum((_num(item.get("change_pct")) or 0) > (avg_change or 0) for item in members) / len(members) * 100 if members else None,
            }
        behavior_context = _flatten_behavior(forecast.get("behavior") or {})
        market_state = (workbench.get("market_state") or {}).get("state_label") or (forecast.get("phase") or "未知")
        behavior_context["market_state"] = market_state
        permission = self._market_permission(forecast, workbench, snapshot)
        selected_ids = requested_skill_ids or self._active_skill_ids(forecast, permission)
        registry = {item["skill_id"]: item for item in await list_registered_skills()}
        selected_ids = [skill_id for skill_id in selected_ids if registry.get(skill_id, {}).get("enabled", True)]
        candidates: list[dict[str, Any]] = []
        observed_results: list[dict[str, Any]] = []
        for stock in stocks:
            code = str(stock.get("code") or "")
            sector = str(stock.get("sector") or "").strip() or "未分类"
            context = {
                **behavior_context, **sector_context.get(sector, {}),
                "market_return_1d": _pct_field_to_decimal(market_return),
                "market_return_20d": None,
                "sector_state": sector_context.get(sector, {}).get("sector_state"),
                # The live quote has no cross-sectional Alpha model of its
                # own yet.  Keep this as a transparent relative-strength
                # proxy rather than labelling it institutional Alpha.
                "stock_alpha_score": _clamp(
                    50 + (
                        (_pct_field_to_decimal(stock.get("change_pct")) or 0)
                        - (sector_context.get(sector, {}).get("sector_return_1d") or 0)
                    ) * 1200
                ),
            }
            rows = list(bars.get(code) or [])
            # A live quote is an observation at the cutoff. Add it only when it
            # is newer than the cached daily bar; missing OHLC fields remain
            # missing, so structure skills cannot silently use a fake range.
            price = _num(stock.get("price"))
            if price and target:
                latest_date = _date(rows[-1].get("trade_date")) if rows else None
                if latest_date != target:
                    rows.append({
                        "trade_date": target, "close_price": price,
                        "open_price": _num(stock.get("open")),
                        "high_price": _num(stock.get("high")), "low_price": _num(stock.get("low")),
                        "volume": _num(stock.get("volume")), "amount": _num(stock.get("amount")),
                        "turnover": _num(stock.get("turnover")), "available_time": snapshot.get("fetched_at") or snapshot.get("cached_at") or now.isoformat(),
                    })
            features = build_skill_features(rows, as_of=target, context=context, auction=auctions.get(code))
            reflexivity = None
            if "skill_10_behavior_reflexivity" in selected_ids:
                reflexivity = build_reflexivity_diagnosis(
                    rows,
                    as_of=target,
                    context=context,
                    auction=auctions.get(code),
                    symbol=code,
                    name=stock.get("name") or code,
                )
                features["reflexivity_diagnosis"] = reflexivity
            results = evaluate_all_skills(features, selected_ids)
            detected = [item for item in results if item.get("detected")]
            if detected:
                best = max(detected, key=lambda item: item.get("score") or 0)
                skill10_risk = bool(
                    best.get("skill_id") == "skill_10_behavior_reflexivity"
                    and best.get("signal_type") == "RISK"
                )
                sector_ok = permission.get("code") != "BLOCK" and not skill10_risk and context.get("sector_state") not in {"退潮", "未验证", "分歧"}
                if permission.get("code") == "CAUTION":
                    sector_ok = sector_ok and best["skill_id"] in {"skill_02_absorption_pressure", "skill_04_false_breakdown_reclaim", "skill_06_low_position_relaunch", "skill_08_behavior_imbalance"}
                record = {
                    "code": code, "name": stock.get("name") or code, "sector": sector,
                    "market_segment": _market_segment(code),
                    "price": price, "change_pct": _num(stock.get("change_pct")),
                    "sector_state": context.get("sector_state"), "sector_permitted": sector_ok,
                    "best_skill": best["skill_id"], "best_stage": best["stage"],
                    "best_stage_label": best.get("stage_label") or best["stage"],
                    "skill_score": best.get("score"), "skill_confidence_pct": best.get("confidence_pct"),
                    "skills": results, "data_date": features.get("data_date"),
                    "history_sessions": features.get("history_sessions", 0),
                    "reflexivity": reflexivity,
                    "candidate_type": (reflexivity or {}).get("candidate_type"),
                    "candidate_label": (reflexivity or {}).get("candidate_label"),
                    "diagnosis_level": (reflexivity or {}).get("diagnosis_level"),
                    "source": snapshot.get("source") or "cache",
                    "invalidation_conditions": best.get("invalidation_conditions") or [],
                    "action": "CANDIDATE" if sector_ok and permission.get("code") != "BLOCK" else "WATCH_ONLY",
                }
                observed_results.append(record)
                if sector_ok and permission.get("code") != "BLOCK":
                    candidates.append(record)
        candidates.sort(key=lambda item: (item.get("skill_score") or 0, item.get("history_sessions") or 0), reverse=True)
        max_candidates = int(permission.get("max_candidates") or 12)
        candidates = candidates[:max_candidates]
        if permission.get("code") == "BLOCK":
            action = "NO_TRADE"
        elif not candidates:
            action = "NO_TRADE"
        elif permission.get("code") == "CAUTION":
            action = "WATCH"
        else:
            action = "CANDIDATE_RESEARCH"
        active = []
        for skill_id in selected_ids:
            definition = registry.get(skill_id) or {}
            active.append({
                "skill_id": skill_id, "skill_name": definition.get("skill_name", skill_id),
                "lifecycle_state": definition.get("lifecycle_state", "EXPERIMENTAL"),
                "validation_status": definition.get("validation_status", "NOT_TESTED"),
                "runtime": "ON" if permission.get("code") != "BLOCK" or skill_id == "skill_08_behavior_imbalance" else "OFF",
                "data_level": definition.get("required_data_level"),
            })
        missing = sorted({
            factor for row in observed_results for skill in row.get("skills") or [] for factor in skill.get("missing_factors") or []
        })
        result = {
            "version": "v5-trading-skill-runtime-3",
            "generated_at": now.isoformat(), "trade_date": target.isoformat(),
            "phase": forecast.get("phase") or "unknown", "data_cutoff_time": forecast.get("data_cutoff_time") or snapshot.get("fetched_at") or snapshot.get("cached_at"),
            "market_permission": permission, "action": action,
            "active_skills": active, "candidates": candidates,
            "watchlist": sorted(observed_results, key=lambda item: item.get("skill_score") or 0, reverse=True)[:20],
            "scanned_count": len(stocks), "candidate_count": len(candidates),
            "universe_count": len(source_stocks), "excluded_count": sum(excluded_counts.values()),
            "filters": {
                "exclude_star_market": bool(exclude_star_market),
                "exclude_gem": bool(exclude_gem),
                "excluded_board_labels": [
                    label for label, enabled in (("科创板", exclude_star_market), ("创业板", exclude_gem)) if enabled
                ],
                "excluded_counts": excluded_counts,
                "description": "默认只扫描可交易的沪深主板；勾选开关后可纳入科创板或创业板研究候选。",
            },
            "source": snapshot.get("source") or "cache", "upstream_source": snapshot.get("cache_source") or snapshot.get("source"),
            "is_realtime": bool(snapshot.get("is_realtime")), "snapshot_data_date": snapshot.get("data_date"),
            "market_state": market_state, "behavior": behavior_context,
            "missing_factors": missing,
            "reflexivity": {
                "skill_id": "skill_10_behavior_reflexivity",
                "candidate_count": sum(1 for item in observed_results if item.get("candidate_type") in {"PANIC_ABSORPTION_CANDIDATE", "ALPHA_SEED_REFLEXIVITY", "POSITIVE_REFLEXIVITY_CANDIDATE"}),
                "risk_count": sum(1 for item in observed_results if item.get("candidate_type") in {"HIGH_LEVEL_REFLEXIVITY_DECAY", "NEGATIVE_REFLEXIVITY_ACCELERATION"}),
                "short_cover_policy": "disabled_without_verified_short_series",
                "l2_policy": "not_inferred",
                "model_version": "reflexivity-daily-v1",
            },
            "data_quality": {
                "history_coverage": sum(bool(bars.get(code)) for code in codes),
                "auction_coverage": len(auctions), "auction_history_is_forward_only": True,
                "warnings": [
                    "Skill 09历史竞价不足时仅以真实09:25快照运行Shadow，不用日K还原。",
                    "未覆盖的板块成员、L2和社交热度保持缺失，不补造。",
                ],
            },
            "funnel": {
                "market": "通过" if permission.get("code") == "ALLOW" else permission.get("label"),
                "sector": "个股Skill不得越过退潮/未验证板块" if permission.get("code") != "BLOCK" else "市场阻断",
                "skill": f"{len(observed_results)}只出现可观测Skill结构",
                "execution": "候选研究，不连接券商下单；NO_TRADE时不输出执行候选",
            },
            "audit": {"no_future_data": True, "available_time_rule": "bars/quotes/auction quote_at <= data_cutoff_time", "payload_hash": None},
        }
        result["audit"]["payload_hash"] = _json_hash(result)
        await self._persist(result)
        return result

    async def _persist(self, payload: dict[str, Any]) -> None:
        try:
            async with async_session() as session:
                session.add(TradingSkillScanSnapshot(
                    trade_date=_date(payload.get("trade_date")) or shanghai_now().date(),
                    phase=str(payload.get("phase") or "unknown"),
                    market_permission=str((payload.get("market_permission") or {}).get("code") or "BLOCK"),
                    data_cutoff_time=_date_time(payload.get("data_cutoff_time")) or datetime.utcnow(),
                    source=str(payload.get("source") or "unknown"),
                    scanned_count=int(payload.get("scanned_count") or 0),
                    candidate_count=int(payload.get("candidate_count") or 0),
                    payload=payload,
                ))
                filters = payload.get("filters") or {}
                cache_key = _scan_cache_key(
                    exclude_star_market=bool(filters.get("exclude_star_market", True)),
                    exclude_gem=bool(filters.get("exclude_gem", True)),
                )
                row = await session.get(MarketDataCache, cache_key)
                if row is None:
                    session.add(MarketDataCache(key=cache_key, payload=payload))
                else:
                    row.payload = payload
                await session.commit()
        except Exception as exc:
            print(f"Trading skill scan persistence failed: {type(exc).__name__}")

    async def dashboard(
        self,
        *,
        force: bool = False,
        exclude_star_market: bool = True,
        exclude_gem: bool = True,
    ) -> dict[str, Any]:
        cache_key = _scan_cache_key(
            exclude_star_market=exclude_star_market,
            exclude_gem=exclude_gem,
        )
        if not force:
            try:
                async with async_session() as session:
                    row = await session.get(MarketDataCache, cache_key)
                cached = row.payload if row and isinstance(row.payload, dict) else None
                generated = _date_time(cached.get("generated_at")) if cached else None
                age = (shanghai_now().replace(tzinfo=None) - generated).total_seconds() if generated else None
                max_age = 60 if is_a_share_market_session() else 300
                cached_filters = cached.get("filters") if cached else None
                if (
                    cached
                    and cached.get("candidates") is not None
                    and cached_filters
                    and bool(cached_filters.get("exclude_star_market")) == bool(exclude_star_market)
                    and bool(cached_filters.get("exclude_gem")) == bool(exclude_gem)
                    and (age is None or age <= max_age)
                ):
                    cached = deepcopy(cached)
                    cached["cache_used"] = True
                    return cached
            except Exception:
                pass
        return await self._build(
            force=force,
            exclude_star_market=exclude_star_market,
            exclude_gem=exclude_gem,
        )

    async def scan(
        self,
        *,
        skill_ids: list[str] | None = None,
        force: bool = False,
        exclude_star_market: bool = True,
        exclude_gem: bool = True,
    ) -> dict[str, Any]:
        return await self._build(
            force=force,
            requested_skill_ids=skill_ids,
            exclude_star_market=exclude_star_market,
            exclude_gem=exclude_gem,
        )

    async def stock(
        self,
        code: str,
        *,
        force: bool = False,
        exclude_star_market: bool = True,
        exclude_gem: bool = True,
    ) -> dict[str, Any]:
        data = await self._build(
            force=force,
            requested_skill_ids=None,
            exclude_star_market=exclude_star_market,
            exclude_gem=exclude_gem,
        )
        normalized = str(code).upper().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
        matches = [item for item in data.get("watchlist") or [] if str(item.get("code")) == normalized]
        return {**data, "stock_code": normalized, "stock": matches[0] if matches else None, "candidates": matches}

    async def latest(self) -> dict[str, Any] | None:
        try:
            async with async_session() as session:
                row = (await session.execute(select(TradingSkillScanSnapshot).order_by(desc(TradingSkillScanSnapshot.generated_at)).limit(1))).scalar_one_or_none()
            return row.payload if row else None
        except Exception:
            return None


def _date_time(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=None)
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return value.replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


trading_skill_service = TradingSkillService()
