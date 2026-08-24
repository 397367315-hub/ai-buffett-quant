"""Read-only adapters for the existing A-share research system."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any, Awaitable

from sqlalchemy import case, desc, func, select

from database import async_session
from models import MarketDataCache, StockDailyBar
from quant.market_cache import load_quant_market_snapshot
from services.data_collector import is_a_share_market_session, normalize_stock_code, shanghai_now
from services.market_decision_contract import WORKBENCH_CACHE_KEY
from services.forecast_v5 import forecast_v5_service
from services.market_decision_workbench import market_decision_workbench_service
from services.reflexivity_service import reflexivity_service
from services.v51_microstructure_service import v51_microstructure_service


ROCI_CACHE_KEY = "roci_dashboard_v1"
ROCI_TIMEOUT_SECONDS = 18.0
ROCI_WORKBENCH_TIMEOUT_SECONDS = 35.0
ROCI_MARKET_CACHE_SECONDS = 90
ROCI_OFF_HOURS_CACHE_SECONDS = 1800


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def cache_freshness(payload: dict[str, Any], now: datetime | None = None) -> tuple[bool, float | None, int]:
    """Return cache validity with a shorter TTL during the A-share session."""
    current = (now or shanghai_now()).replace(tzinfo=None)
    generated_at = _datetime(payload.get("generated_at"))
    ttl = ROCI_MARKET_CACHE_SECONDS if is_a_share_market_session(now or shanghai_now()) else ROCI_OFF_HOURS_CACHE_SECONDS
    if generated_at is None:
        return False, None, ttl
    age_seconds = max(0.0, (current - generated_at).total_seconds())
    return age_seconds <= ttl, round(age_seconds, 1), ttl


def roci_cache_has_usable_recommendations(payload: dict[str, Any] | None) -> bool:
    """Only reuse a ROCI snapshot when the recommendation layer produced data.

    An adapter timeout is intentionally represented as UNKNOWN, but that
    result must not become a long-lived cache hit.  Otherwise one cold-start
    timeout can hide a later healthy workbench indefinitely.
    """
    risk_adapted = ((payload or {}).get("opportunities") or {}).get("risk_adapted") or {}
    return bool(
        risk_adapted.get("status") == "AVAILABLE"
        and (risk_adapted.get("sectors") or risk_adapted.get("stocks"))
    )


def _workbench_has_recommendation_inputs(payload: dict[str, Any] | None) -> bool:
    """Check for observed sector rows before using a workbench fallback."""
    if not isinstance(payload, dict) or payload.get("__adapter_error__"):
        return False
    return bool(payload.get("main_lines"))


async def _safe(label: str, call: Awaitable[Any], fallback: Any, timeout: float = ROCI_TIMEOUT_SECONDS) -> Any:
    try:
        return await asyncio.wait_for(call, timeout=timeout)
    except Exception as exc:
        # The ROCI payload exposes the source health separately; an adapter
        # failure must not turn into a fabricated neutral score.
        return {"__adapter_error__": f"{label}:{type(exc).__name__}", "value": fallback} if isinstance(fallback, dict) else fallback


async def _cached(key: str) -> dict[str, Any] | None:
    try:
        async with async_session() as session:
            row = await session.get(MarketDataCache, key)
            return dict(row.payload) if row and isinstance(row.payload, dict) else None
    except Exception:
        return None


async def _load_workbench_context(*, force: bool = False) -> tuple[dict[str, Any], bool]:
    """Read the canonical V4 cache before waiting on a cold workbench call.

    The scheduler persists the same-date V4 workbench in PostgreSQL.  ROCI is
    a read-only consumer, so that cache is the correct fallback when a live
    rebuild is slow or one of its optional adapters is unavailable.
    """
    cached = await _cached(WORKBENCH_CACHE_KEY)
    if not force and _workbench_has_recommendation_inputs(cached):
        return cached or {}, True

    live = await _safe(
        "workbench",
        market_decision_workbench_service.get(force=force),
        {},
        timeout=ROCI_WORKBENCH_TIMEOUT_SECONDS,
    )
    if _workbench_has_recommendation_inputs(live):
        return live, bool((live.get("meta") or {}).get("cache_used"))
    if _workbench_has_recommendation_inputs(cached):
        return cached or {}, True
    return live if isinstance(live, dict) else {}, False


async def load_daily_context(symbol: str | None = None, target: date | None = None, limit: int = 90) -> dict[str, Any]:
    """Load only existing PIT daily bars; never build bars from a quote."""
    try:
        async with async_session() as session:
            latest = target or (await session.execute(select(StockDailyBar.trade_date).order_by(desc(StockDailyBar.trade_date)).limit(1))).scalar_one_or_none()
            if latest is None:
                return {"bars": [], "data_date": None, "source": "unavailable"}
            start = latest - timedelta(days=max(limit * 2, 30))
            if not symbol:
                rows = list((await session.execute(
                    select(
                        StockDailyBar.trade_date.label("trade_date"),
                        func.avg(StockDailyBar.change_pct).label("change_pct"),
                        func.count(StockDailyBar.stock_code).label("sample_size"),
                        func.sum(case((StockDailyBar.change_pct < 0, 1), else_=0)).label("down_count"),
                        func.sum(StockDailyBar.amount).label("amount"),
                        func.sum(StockDailyBar.volume).label("volume"),
                    )
                    .where(StockDailyBar.trade_date <= latest, StockDailyBar.trade_date >= start)
                    .group_by(StockDailyBar.trade_date)
                    .order_by(StockDailyBar.trade_date)
                )).all())
                synthetic_close = 100.0
                market_bars: list[dict[str, Any]] = []
                for row in rows[-limit:]:
                    change_pct = float(row.change_pct) if row.change_pct is not None else None
                    if change_pct is not None:
                        synthetic_close *= max(0.01, 1 + change_pct / 100)
                    sample_size = int(row.sample_size or 0)
                    market_bars.append({
                        "trade_date": row.trade_date.isoformat() if row.trade_date else None,
                        "close": round(synthetic_close, 6) if change_pct is not None else None,
                        "change_pct": round(change_pct, 4) if change_pct is not None else None,
                        "down_ratio": round(float(row.down_count or 0) / sample_size, 4) if sample_size else None,
                        "sample_size": sample_size,
                        "amount": float(row.amount) if row.amount is not None else None,
                        "volume": float(row.volume) if row.volume is not None else None,
                        "source": "stock_daily_bars_equal_weight",
                    })
                return {"market_bars": market_bars, "data_date": latest.isoformat(), "source": "stock_daily_bars_equal_weight"}
            statement = select(StockDailyBar).where(
                StockDailyBar.trade_date <= latest,
                StockDailyBar.trade_date >= start,
            )
            statement = statement.where(StockDailyBar.stock_code == normalize_stock_code(symbol))
            rows = list((await session.execute(statement.order_by(StockDailyBar.stock_code, StockDailyBar.trade_date))).scalars().all())
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row.stock_code, []).append({
                "code": row.stock_code,
                "name": row.stock_name,
                "trade_date": row.trade_date.isoformat() if row.trade_date else None,
                "open": row.open_price,
                "close": row.close_price,
                "high": row.high_price,
                "low": row.low_price,
                "volume": row.volume,
                "amount": row.amount,
                "change_pct": row.change_pct,
                "turnover": row.turnover,
                "source": row.source,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            })
        code = normalize_stock_code(symbol)
        return {"bars": grouped.get(code, [])[-limit:], "data_date": latest.isoformat(), "source": "stock_daily_bars"}
    except Exception as exc:
        return {"bars": [], "bars_by_code": {}, "data_date": None, "source": f"unavailable:{type(exc).__name__}"}


async def load_existing_context(
    *,
    force: bool = False,
    symbol: str | None = None,
    as_of: date | None = None,
    refresh_inputs: bool | None = None,
) -> dict[str, Any]:
    """Collect the legacy outputs behind a stable, read-only contract.

    ``force`` has two jobs in the public API: bypass the derived ROCI snapshot
    cache and ask upstream services to refresh.  The refresh coordinator needs
    the first behaviour without starting a second fan-out after its source
    jobs have completed, so callers can explicitly set ``refresh_inputs=False``
    and rebuild from the latest persisted upstream caches.
    """
    if refresh_inputs is None:
        refresh_inputs = force
    cached = await _cached(ROCI_CACHE_KEY)
    # Ignore an older ROCI cache after the risk-adapted recommendation layer
    # was introduced. Otherwise an off-hours process could serve the previous
    # payload for up to 30 minutes after a deploy.
    cache_has_recommendations = roci_cache_has_usable_recommendations(cached)
    if not force and cached and cache_has_recommendations and not symbol:
        fresh, age_seconds, ttl_seconds = cache_freshness(cached)
        if fresh:
            return {
                "cached_roci": cached,
                "cache_used": True,
                "cache_age_seconds": age_seconds,
                "cache_ttl_seconds": ttl_seconds,
            }

    if symbol:
        normalized = normalize_stock_code(symbol)
        workbench_call = _load_workbench_context(force=bool(refresh_inputs))
        forecast_call = forecast_v5_service.dashboard(force=bool(refresh_inputs), include_skills=False)
        micro_call = v51_microstructure_service.diagnose(normalized, refresh=bool(refresh_inputs), as_of=as_of)
        reflex_call = reflexivity_service.diagnose(normalized, as_of=as_of, force=bool(refresh_inputs))
        workbench_result, forecast, micro, reflex = await asyncio.gather(
            workbench_call,
            _safe("forecast", forecast_call, {}),
            _safe("v51", micro_call, {}),
            _safe("reflexivity", reflex_call, {}),
        )
        workbench, workbench_from_cache = workbench_result if isinstance(workbench_result, tuple) else (workbench_result, False)
    else:
        workbench_call = _load_workbench_context(force=bool(refresh_inputs))
        # Forecast already consumes the legacy workbench internally. Running
        # both in parallel avoids introducing a dependency from old code into
        # the new sidecar and keeps the response useful if either fails.
        forecast_call = forecast_v5_service.dashboard(force=bool(refresh_inputs), include_skills=False)
        micro_call = v51_microstructure_service.leadership_sectors(refresh=bool(refresh_inputs))
        workbench_result, forecast, micro = await asyncio.gather(
            workbench_call,
            _safe("forecast", forecast_call, {}),
            _safe("v51", micro_call, {}),
        )
        workbench, workbench_from_cache = workbench_result if isinstance(workbench_result, tuple) else (workbench_result, False)
        reflex = {}

    daily = await load_daily_context(symbol, as_of)
    quant_snapshot = await _safe("quant_snapshot", load_quant_market_snapshot(), {}, timeout=8.0)
    return {
        "workbench": workbench if isinstance(workbench, dict) else {},
        "forecast": forecast if isinstance(forecast, dict) else {},
        "microstructure": micro if isinstance(micro, dict) else {},
        "reflexivity": reflex if isinstance(reflex, dict) else {},
        "daily": daily if isinstance(daily, dict) else {},
        "quant_snapshot": quant_snapshot if isinstance(quant_snapshot, dict) else {},
        "cache_used": bool(workbench_from_cache),
        "collected_at": shanghai_now().replace(tzinfo=None).isoformat(),
        "source_status": {
            "workbench": "cached" if workbench_from_cache else "available" if isinstance(workbench, dict) and not workbench.get("__adapter_error__") else "unavailable",
            "forecast_v5": "available" if isinstance(forecast, dict) and not forecast.get("__adapter_error__") else "unavailable",
            "v51": "available" if isinstance(micro, dict) and not micro.get("__adapter_error__") else "unavailable",
            "reflexivity": "available" if isinstance(reflex, dict) and not reflex.get("__adapter_error__") else "unavailable",
            "daily_bars": "available" if daily.get("data_date") else "unavailable",
            "quant_snapshot": "available" if quant_snapshot.get("data_date") else "unavailable",
        },
    }
