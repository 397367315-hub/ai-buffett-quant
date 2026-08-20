import json
import asyncio
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, desc, asc, text

from services.data_collector import (
    as_float,
    as_int,
    collector,
    is_a_share_market_session,
    normalize_board_code,
    normalize_stock_code,
    shanghai_now,
)
from services.admin_auth import create_admin_token
from config import settings
from services.ai_service import ai_service
from services.ai_assistant import MAX_HISTORY_MESSAGES, ai_assistant_service
from services.ai_prompts import BEGINNER_SYSTEM_PROMPT, PROFESSIONAL_SYSTEM_PROMPT, DAILY_REPORT_PROMPT_TEMPLATE
from services.mao_strategy_agent import mao_strategy_agent
from services.stock_selection_agents import (
    VALID_RISK_PROFILES,
    VALID_SELECTION_MODES,
    stock_selection_agents,
)
from services.technical_screener import SCREENER_PRESETS, SCREENER_SCHEMA, technical_screener_service
from services.flow_analysis import FLOW_WINDOWS, flow_analysis_service
from services.dragon_board import DRAGON_WINDOWS, dragon_board_service
from services.horizon_analysis import VALID_HORIZONS
from models import (
    KnowledgeTerm, LearningCase, ConceptBoard,
    ConceptFundFlowDaily, IndustryFundFlowDaily, MarketFundFlowDaily, AIChatHistory, MarketBoard,
    MarketDataCache, PersonalSystemConfig, StockSelectionRun,
)
from database import async_session
from services.history_cache import history_cache
from services.sector_flow_network import build_inferred_transfers
from services.topic_strength import topic_strength_service
from services.market_decision_workbench import market_decision_workbench_service
from services.decision_workbench_2026 import decision_workbench_2026_service
from services.market_way_v4 import market_way_v4_service
from services.block_trade_analysis import block_trade_analysis_service
from services.stock_essence_decision import stock_essence_decision_service
from quant.market_cache import load_quant_market_snapshot

router = APIRouter(prefix="/api/v1")

FLOW_SORT_FIELDS = {
    "main_net_inflow": "f62",
    "change_pct": "f3",
    "close_price": "f2",
}


def _market_metadata(*, available: bool, data_date: str | None, is_realtime: bool, source: str = "eastmoney") -> dict:
    return {
        "available": available,
        "source": source,
        "is_realtime": is_realtime,
        "data_date": data_date,
        "updated_at": shanghai_now().isoformat(),
    }


def _quote_metadata(
    *,
    available: bool,
    data_date: str | None = None,
    source: str = "eastmoney",
) -> dict:
    """Mark undated quote snapshots live only while the A-share market is open."""
    now = shanghai_now()
    is_realtime = (
        bool(available)
        and is_a_share_market_session(now)
        and (data_date is None or data_date == now.date().isoformat())
    )
    verified_date = data_date or (now.date().isoformat() if is_realtime else None)
    return _market_metadata(
        available=available,
        data_date=verified_date,
        is_realtime=is_realtime,
        source=source,
    )


async def _fetch_market_component(name: str, awaitable, fallback):
    """Keep one blocked overseas upstream from delaying an aggregate page."""
    try:
        return await asyncio.wait_for(awaitable, timeout=settings.market_aggregate_timeout)
    except asyncio.TimeoutError:
        print(f"Market component timed out: {name}")
    except Exception as exc:
        print(f"Market component failed: {name}: {type(exc).__name__}")
    return fallback


async def _attach_tencent_index_history(turnover: dict) -> dict:
    """Attach a short real close series when the index snapshot is available."""
    if not isinstance(turnover, dict) or not turnover.get("indices") or turnover.get("index_series"):
        return turnover
    history = await _fetch_market_component(
        "tencent-index-history",
        collector.fetch_tencent_index_history(days=10),
        {},
    )
    series = history.get("index_series") if isinstance(history, dict) else None
    if not isinstance(series, dict) or not series:
        return turnover
    return {**turnover, "index_series": series}


async def _latest_cached_trade_date(model) -> date | None:
    try:
        async with async_session() as session:
            return (await session.execute(select(func.max(model.trade_date)))).scalar_one_or_none()
    except Exception as exc:
        # A fresh deployment may receive traffic before the cache tables exist;
        # the live observer still has a verified upstream fallback.
        print(f"Cached trade-date lookup failed: {type(exc).__name__}")
        return None


async def _read_json_snapshot(key: str) -> dict | None:
    try:
        async with async_session() as session:
            row = await session.get(MarketDataCache, key)
        return dict(row.payload) if row and isinstance(row.payload, dict) else None
    except Exception:
        return None


async def _write_json_snapshot(key: str, payload: dict) -> str | None:
    saved_at = shanghai_now().isoformat()
    try:
        async with async_session() as session:
            row = await session.get(MarketDataCache, key)
            snapshot = {**payload, "snapshot_saved_at": saved_at}
            if row is None:
                session.add(MarketDataCache(key=key, payload=snapshot))
            else:
                row.payload = snapshot
            await session.commit()
        return saved_at
    except Exception:
        return None


def _normalize_market_overview_payload(payload: dict) -> dict:
    """Keep cached and live market-overview responses on one stable contract."""
    market_index = payload.get("market_index")
    north_bound = payload.get("north_bound")
    fund_flow = payload.get("fund_flow")
    limit_board = payload.get("limit_board")
    return {
        **payload,
        "market_index": {
            "sh_index": None,
            "sh_change": None,
            "sh_change_pct": None,
            "sh_volume": None,
            "sh_amount": None,
            **(market_index if isinstance(market_index, dict) else {}),
        },
        "north_bound": {
            "latest_deal_amount": None,
            "latest_inflow": None,
            "net_inflow_available": False,
            **(north_bound if isinstance(north_bound, dict) else {}),
        },
        "fund_flow": {
            "top_inflow": [],
            "top_outflow": [],
            **(fund_flow if isinstance(fund_flow, dict) else {}),
        },
        "limit_board": {
            "limit_up": None,
            "limit_down": None,
            **(limit_board if isinstance(limit_board, dict) else {}),
        },
        "hot_sectors": payload.get("hot_sectors") if isinstance(payload.get("hot_sectors"), list) else [],
    }


def _cached_market_overview_payload(payload: dict) -> dict:
    """Annotate a verified snapshot without implying it is a live quote."""
    cached = _normalize_market_overview_payload(payload)
    cached.update({
        "update_time": shanghai_now().isoformat(),
        "available": True,
        "source": "cache",
        "is_realtime": False,
        "cache_used": True,
    })
    return cached


async def _enrich_cached_market_indices(payload: dict) -> tuple[dict, bool]:
    """Backfill old cache rows with Tencent's dated index snapshot.

    Older cache rows predate the three-index contract and may contain only a
    null or partial Shanghai quote. Tencent is used here as the explicitly
    supported 24-hour fallback; the existing breadth and flow rows stay tied
    to the cache's own verified data date.
    """
    cached = _normalize_market_overview_payload(payload)
    market_index = dict(cached.get("market_index") or {})
    existing_indices = market_index.get("indices")
    required = {"shanghai", "chinext", "hs300"}
    if isinstance(existing_indices, dict) and required.issubset(existing_indices):
        enriched_index = await _attach_tencent_index_history(market_index)
        changed = enriched_index.get("index_series") != market_index.get("index_series")
        return ({**cached, "market_index": enriched_index} if changed else cached), changed

    snapshot = await _fetch_market_component(
        "tencent-index-cache-enrichment",
        collector.fetch_tencent_index_quotes(),
        {},
    )
    indices = snapshot.get("indices") if isinstance(snapshot, dict) else None
    if not isinstance(indices, dict) or not indices:
        return cached, False

    cached_date = _market_date(cached.get("data_date"))
    quote_date = _market_date(snapshot.get("data_date"))
    if cached_date and quote_date and quote_date < cached_date:
        return cached, False

    shanghai = indices.get("shanghai") or {}
    merged_index = {
        **market_index,
        "indices": indices,
        "data_date": quote_date or market_index.get("data_date"),
        "source_updated_at": snapshot.get("source_updated_at"),
        "source": snapshot.get("source") or "tencent",
        "is_realtime": bool(snapshot.get("is_realtime")),
    }
    # Keep the legacy Shanghai fields populated for older consumers.
    for target, source in (
        ("sh_index", "value"),
        ("sh_change", "change"),
        ("sh_change_pct", "change_pct"),
        ("sh_volume", "volume"),
        ("sh_amount", "amount"),
    ):
        if merged_index.get(target) in (None, 0) and shanghai.get(source) is not None:
            merged_index[target] = shanghai.get(source)
    merged_index = await _attach_tencent_index_history(merged_index)
    enriched = {**cached, "market_index": merged_index}
    return enriched, True


def _market_date(value: object) -> str | None:
    candidate = str(value or "").strip()[:10]
    if len(candidate) == 8 and candidate.isdigit():
        candidate = f"{candidate[:4]}-{candidate[4:6]}-{candidate[6:8]}"
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return None


def _flow_ranking(item: dict, rank: int) -> dict:
    return {
        "rank": rank,
        "code": item.get("code", ""),
        "name": item.get("name", ""),
        "close_price": as_float(item.get("close_price")),
        "change_pct": as_float(item.get("change_pct")),
        "main_net_inflow": as_int(item.get("main_net_inflow")),
        "main_net_inflow_pct": as_float(item.get("main_net_inflow_pct")),
        "super_large_net_inflow": as_int(item.get("super_large_net_inflow")),
        "large_net_inflow": as_int(item.get("large_net_inflow")),
        "medium_net_inflow": as_int(item.get("medium_net_inflow")),
        "up_count": as_int(item.get("up_count")),
        "down_count": as_int(item.get("down_count")),
        "leading_stock": item.get("leading_stock", ""),
        "leading_stock_code": item.get("leading_stock_code", ""),
        "leading_stock_change_pct": as_float(item.get("leading_stock_change_pct")),
    }


async def _realtime_concept_extremes(limit: int = 50) -> list[dict]:
    """Fetch verified high and low fund-flow rankings without a full directory scan."""
    inflows, outflows = await asyncio.gather(
        collector.fetch_concept_flow(sort_order=0, page_size=limit),
        collector.fetch_concept_flow(sort_order=1, page_size=limit),
    )
    by_code = {
        str(item.get("code")): item
        for item in [*inflows, *outflows]
        if item.get("code")
    }
    ordered = sorted(by_code.values(), key=lambda item: as_int(item.get("main_net_inflow")), reverse=True)
    return [_flow_ranking(item, index + 1) for index, item in enumerate(ordered)]


def _assemble_flow_observer(
    board_type: str,
    rows: list[dict],
    limit: int,
    *,
    market: dict | None,
    source: str,
    data_date: str | None,
    is_realtime: bool,
    history_coverage: dict | None = None,
) -> dict:
    """Shape board rows into the same two-sided contract for live and cache data."""
    # Keep one source row per board, then enforce the sign of each side. This
    # protects the visual direction when an upstream ranking contains zeros.
    by_code = {
        str(row.get("code")): row
        for row in rows
        if row.get("code")
    }
    inflows = sorted(
        (row for row in by_code.values() if as_int(row.get("main_net_inflow")) > 0),
        key=lambda row: as_int(row.get("main_net_inflow")),
        reverse=True,
    )[:limit]
    outflows = sorted(
        (row for row in by_code.values() if as_int(row.get("main_net_inflow")) < 0),
        key=lambda row: as_int(row.get("main_net_inflow")),
    )[:limit]

    inflow_data = [_flow_ranking(row, index + 1) for index, row in enumerate(inflows)]
    outflow_data = [_flow_ranking(row, index + 1) for index, row in enumerate(outflows)]
    inflow_total = sum(row["main_net_inflow"] for row in inflow_data)
    outflow_total = sum(row["main_net_inflow"] for row in outflow_data)
    network = build_inferred_transfers(inflow_data, outflow_data)
    market = market or {}
    available = bool(inflow_data or outflow_data or market)
    result = {
        "board_type": board_type,
        "board_label": "行业板块" if board_type == "industry" else "概念板块",
        "inflows": inflow_data,
        "outflows": outflow_data,
        "transfers": network["transfers"],
        "flow_inference": network["inference"],
        "market": market,
        "summary": {
            "inflow_total": inflow_total,
            "outflow_total": outflow_total,
            "shown_net_flow": inflow_total + outflow_total,
            "inflow_count": len(inflow_data),
            "outflow_count": len(outflow_data),
            "requested_limit": limit,
        },
        "source_status": {
            "inflows": bool(inflow_data),
            "outflows": bool(outflow_data),
            "market": bool(market),
        },
        **_market_metadata(
            available=available,
            data_date=data_date if available else None,
            is_realtime=is_realtime,
            source=source,
        ),
    }
    if history_coverage is not None:
        result["history_coverage"] = history_coverage
    return result


async def _realtime_flow_observer(board_type: str, limit: int) -> dict:
    """Build one timestamped, bidirectional board-flow snapshot.

    The two directional rankings are requested together so the animation never
    combines an inflow list from one refresh with an outflow list from another.
    """
    fetcher = collector.fetch_industry_flow if board_type == "industry" else collector.fetch_concept_flow
    request_size = min(max(limit * 4, 24), 100)
    inflow_rows, outflow_rows, turnover = await asyncio.gather(
        _fetch_market_component(
            "flow-observer-inflow",
            fetcher(sort_order=0, page_size=request_size),
            [],
        ),
        _fetch_market_component(
            "flow-observer-outflow",
            fetcher(sort_order=1, page_size=request_size),
            [],
        ),
        _fetch_market_component("flow-observer-turnover", collector.fetch_market_turnover(), {}),
    )

    # A sleeping regional proxy can use the first bounded request only to wake
    # up. Retry missing components together once; successful components are
    # retained and an unavailable upstream still remains visibly unavailable.
    retry_slots = []
    retry_components = []
    if not inflow_rows:
        retry_slots.append("inflows")
        retry_components.append(_fetch_market_component(
            "flow-observer-inflow-retry",
            fetcher(sort_order=0, page_size=request_size),
            [],
        ))
    if not outflow_rows:
        retry_slots.append("outflows")
        retry_components.append(_fetch_market_component(
            "flow-observer-outflow-retry",
            fetcher(sort_order=1, page_size=request_size),
            [],
        ))
    if not turnover:
        retry_slots.append("turnover")
        retry_components.append(_fetch_market_component(
            "flow-observer-turnover-retry",
            collector.fetch_market_turnover(),
            {},
        ))
    if retry_components:
        retry_results = dict(zip(retry_slots, await asyncio.gather(*retry_components)))
        inflow_rows = inflow_rows or retry_results.get("inflows", [])
        outflow_rows = outflow_rows or retry_results.get("outflows", [])
        turnover = turnover or retry_results.get("turnover", {})

    now = shanghai_now()
    is_realtime = is_a_share_market_session(now)
    return _assemble_flow_observer(
        board_type,
        [*inflow_rows, *outflow_rows],
        limit,
        market=turnover,
        source="eastmoney",
        data_date=now.date().isoformat() if is_realtime else None,
        is_realtime=is_realtime,
    )


async def _historical_flow_observer(board_type: str, target_date: date, limit: int) -> dict:
    """Load a single daily board snapshot from the verified local cache."""
    model = IndustryFundFlowDaily if board_type == "industry" else ConceptFundFlowDaily
    async with async_session() as session:
        rows = (await session.execute(
            select(model)
            .where(model.trade_date == target_date)
            .order_by(model.main_net_inflow.desc())
        )).scalars().all()
        codes = [row.board_code for row in rows]
        board_rows = (await session.execute(
            select(MarketBoard).where(
                MarketBoard.board_type == board_type,
                MarketBoard.code.in_(codes),
            )
        )).scalars().all() if codes else []
        directory_board_count = (await session.execute(
            select(func.count()).select_from(MarketBoard).where(MarketBoard.board_type == board_type)
        )).scalar_one()

    names = {row.code: row.name for row in board_rows}
    records = [
        {
            "code": row.board_code,
            "name": names.get(row.board_code, row.board_code),
            "close_price": row.close_price,
            "change_pct": row.change_pct,
            "main_net_inflow": row.main_net_inflow,
            "main_net_inflow_pct": row.main_net_inflow_pct,
            "super_large_net_inflow": row.super_large_net_inflow,
            "large_net_inflow": row.large_net_inflow,
            "medium_net_inflow": row.medium_net_inflow,
            "up_count": row.up_count,
            "down_count": row.down_count,
            "leading_stock": getattr(row, "leading_stock", "") or "",
        }
        for row in rows
    ]
    required_board_count = max(1, (directory_board_count * 95 + 99) // 100)
    coverage = {
        "snapshot_board_count": len(rows),
        "directory_board_count": directory_board_count,
        "is_complete": bool(directory_board_count) and len(rows) >= required_board_count,
    }
    return _assemble_flow_observer(
        board_type,
        records,
        limit,
        market={},
        source="cache",
        data_date=target_date.isoformat(),
        is_realtime=False,
        history_coverage=coverage,
    )


async def _flow_observer_history_dates(board_type: str) -> list[dict]:
    """List cached daily snapshots in chronological order for playback."""
    model = IndustryFundFlowDaily if board_type == "industry" else ConceptFundFlowDaily
    cutoff = shanghai_now().date() - timedelta(days=365)
    async with async_session() as session:
        directory_board_count = (await session.execute(
            select(func.count()).select_from(MarketBoard).where(MarketBoard.board_type == board_type)
        )).scalar_one()
        rows = (await session.execute(
            select(model.trade_date, func.count(func.distinct(model.board_code)))
            .where(model.trade_date >= cutoff)
            .group_by(model.trade_date)
            .order_by(model.trade_date.asc())
        )).all()

    required_board_count = max(1, (directory_board_count * 95 + 99) // 100)
    return [
        {
            "date": trade_date.isoformat(),
            "board_count": board_count,
            "is_complete": bool(directory_board_count) and board_count >= required_board_count,
        }
        for trade_date, board_count in rows
    ]


async def _concept_history_rankings(
    target_date: date,
    limit: int | None = None,
    *,
    ascending: bool = False,
) -> list[dict]:
    async with async_session() as session:
        statement = (
            select(ConceptFundFlowDaily)
            .where(ConceptFundFlowDaily.trade_date == target_date)
            .order_by(
                ConceptFundFlowDaily.main_net_inflow.asc()
                if ascending
                else ConceptFundFlowDaily.main_net_inflow.desc()
            )
        )
        if limit is not None:
            statement = statement.limit(limit)
        rows = (await session.execute(statement)).scalars().all()
        codes = [row.board_code for row in rows]
        names = {
            row.code: row.name
            for row in (await session.execute(
                select(MarketBoard).where(MarketBoard.board_type == "concept", MarketBoard.code.in_(codes))
            )).scalars().all()
        } if codes else {}
        legacy_names = {
            row.code: row.name
            for row in (await session.execute(select(ConceptBoard).where(ConceptBoard.code.in_(codes)))).scalars().all()
        } if codes else {}

    return [
        {
            "rank": index + 1,
            "code": row.board_code,
            "name": names.get(row.board_code) or legacy_names.get(row.board_code) or row.board_code,
            "close_price": row.close_price or 0,
            "change_pct": row.change_pct or 0,
            "main_net_inflow": row.main_net_inflow or 0,
            "main_net_inflow_pct": row.main_net_inflow_pct or 0,
            "super_large_net_inflow": row.super_large_net_inflow or 0,
            "large_net_inflow": row.large_net_inflow or 0,
            "medium_net_inflow": row.medium_net_inflow or 0,
            "up_count": row.up_count or 0,
            "down_count": row.down_count or 0,
            "leading_stock": row.leading_stock or "",
            "leading_stock_code": "",
            "leading_stock_change_pct": 0,
        }
        for index, row in enumerate(rows)
    ]


async def _concept_snapshot_coverage(target_date: date) -> dict:
    """Measure whether a cached date covers the current concept directory."""
    async with async_session() as session:
        cached_board_count = (await session.execute(
            select(func.count(func.distinct(ConceptFundFlowDaily.board_code))).where(
                ConceptFundFlowDaily.trade_date == target_date
            )
        )).scalar_one()
        directory_board_count = (await session.execute(
            select(func.count()).select_from(MarketBoard).where(MarketBoard.board_type == "concept")
        )).scalar_one()

    # Board taxonomies can change. A 95% threshold prevents a single retired
    # board from hiding an otherwise complete daily snapshot while keeping
    # sparse historical rows visibly incomplete.
    required_board_count = max(1, (directory_board_count * 95 + 99) // 100)
    return {
        "cached_board_count": cached_board_count,
        "directory_board_count": directory_board_count,
        "is_complete": bool(directory_board_count) and cached_board_count >= required_board_count,
    }

# ── 认证接口 ──


@router.post("/auth/login")
async def auth_login(request: dict):
    """账号密码登录验证"""
    username = request.get("username", "")
    password = request.get("password", "")

    if username == settings.admin_username and password == settings.admin_password:
        return {
            "code": 0,
            "data": {
                "token": create_admin_token(username),
                "username": username,
                "role": "admin",
            },
        }
    return {"code": 401, "message": "账号或密码错误"}


# ── 资金流向接口 ──


@router.get("/flow/concept/rank")
async def get_concept_rank(
    sort: str = Query("main_net_inflow"),
    order: str = Query("desc"),
    limit: int = Query(20, ge=1, le=100),
    trade_date: Optional[str] = Query(None, alias="date"),
):
    today = shanghai_now().date()
    try:
        target = date.fromisoformat(trade_date) if trade_date else today
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    if target == today and not is_a_share_market_session(shanghai_now()):
        target = await _latest_cached_trade_date(ConceptFundFlowDaily) or target

    if target != today or not is_a_share_market_session(shanghai_now()):
        result = await _concept_history_rankings(target, limit, ascending=order == "asc")
        coverage = await _concept_snapshot_coverage(target)
        return {
            "code": 0,
            "data": {
                "trade_date": target.isoformat(),
                "rankings": result,
                "summary": {
                    "total_main_inflow": sum(row["main_net_inflow"] for row in result),
                    "inflow_board_count": sum(row["main_net_inflow"] > 0 for row in result),
                    "outflow_board_count": sum(row["main_net_inflow"] < 0 for row in result),
                    "rankings_are_complete": coverage["is_complete"],
                },
                "coverage": coverage,
                **_market_metadata(available=bool(result), data_date=target.isoformat(), is_realtime=False, source="cache"),
            },
            "message": "success",
        }

    sort_order = 1 if order == "asc" else 0
    data = await collector.fetch_concept_flow(
        sort_field=FLOW_SORT_FIELDS.get(sort, "f62"), sort_order=sort_order, page_size=limit
    )
    result = [_flow_ranking(item, index + 1) for index, item in enumerate(data[:limit])]
    total_inflow = sum(row["main_net_inflow"] for row in result)
    inflow_count = sum(row["main_net_inflow"] > 0 for row in result)
    outflow_count = sum(row["main_net_inflow"] < 0 for row in result)
    metadata = _quote_metadata(available=bool(result))

    return {
        "code": 0,
        "data": {
            "trade_date": metadata["data_date"],
            "update_time": shanghai_now().isoformat(),
            "rankings": result,
            "summary": {
                "total_main_inflow": total_inflow,
                "inflow_board_count": inflow_count,
                "outflow_board_count": outflow_count,
            },
            **metadata,
        },
        "message": "success",
    }


@router.get("/flow/industry/rank")
async def get_industry_rank(
    sort: str = Query("main_net_inflow"),
    order: str = Query("desc"),
    limit: int = Query(20, ge=1, le=100),
):
    if not is_a_share_market_session(shanghai_now()):
        latest = await _latest_cached_trade_date(IndustryFundFlowDaily)
        if latest:
            snapshot = await _historical_flow_observer("industry", latest, min(limit, 100))
            records = [*snapshot.get("inflows", []), *snapshot.get("outflows", [])]
            field = "main_net_inflow" if sort == "main_net_inflow" else sort
            records.sort(key=lambda item: item.get(field) or 0, reverse=order != "asc")
            return {
                "code": 0,
                "data": {
                    "trade_date": latest.isoformat(),
                    "rankings": records[:limit],
                    **_market_metadata(available=bool(records), data_date=latest.isoformat(), is_realtime=False, source="cache"),
                },
            }
    sort_order = 1 if order == "asc" else 0
    data = await collector.fetch_industry_flow(
        sort_field=FLOW_SORT_FIELDS.get(sort, "f62"), sort_order=sort_order, page_size=limit
    )
    result = [_flow_ranking(item, index + 1) for index, item in enumerate(data[:limit])]
    metadata = _quote_metadata(available=bool(result))
    return {"code": 0, "data": {"trade_date": metadata["data_date"], "rankings": result, **metadata}}


@router.get("/flow/market/summary")
async def get_market_summary():
    data = await collector.fetch_market_summary()
    data_date = max(
        (str(item.get("date")) for item in data.values() if item.get("date")),
        default=None,
    )
    return {"code": 0, "data": {"markets": data, **_quote_metadata(available=bool(data), data_date=data_date)}}


@router.get("/flow/stock/{stock_code}")
async def get_stock_flow(stock_code: str):
    try:
        code = normalize_stock_code(stock_code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    data = await collector.fetch_stock_fund_flow(code)
    latest_date = data[-1]["date"] if data else None
    now = shanghai_now()
    is_realtime = bool(data) and latest_date == now.date().isoformat() and is_a_share_market_session(now)
    return {"code": 0, "data": {"stock_code": code, "flow_data": data, **_market_metadata(available=bool(data), data_date=latest_date, is_realtime=is_realtime)}}


def _stock_profile_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="日期必须使用 YYYY-MM-DD 格式") from exc


async def _load_stock_decision_profile(
    symbol: str,
    as_of: str | None,
    refresh: bool,
) -> dict:
    try:
        return await stock_essence_decision_service.get(
            symbol,
            as_of=_stock_profile_date(as_of),
            force=refresh,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        print(f"Stock decision profile failed for {symbol}: {type(exc).__name__}")
        raise HTTPException(status_code=503, detail="个股公开数据核验暂时失败，请稍后重试") from exc


async def _stock_component_response(
    symbol: str,
    component: str,
    as_of: str | None,
    refresh: bool,
) -> dict:
    profile = await _load_stock_decision_profile(symbol, as_of, refresh)
    if component == "earnings_quality":
        fundamentals = profile.get("fundamentals") or {}
        data = {
            "available": fundamentals.get("available"),
            "report_date": fundamentals.get("report_date"),
            "disclosed_at": fundamentals.get("disclosed_at"),
            "earnings_state": fundamentals.get("earnings_state"),
            "earnings_quality": fundamentals.get("earnings_quality"),
            "earnings_quality_score": fundamentals.get("earnings_quality_score"),
            "earnings_sustainability": fundamentals.get("earnings_sustainability"),
            "operating_vs_non_recurring": fundamentals.get("operating_vs_non_recurring"),
            "metrics": fundamentals.get("metrics") or {},
            "formula": fundamentals.get("formula"),
            "source": fundamentals.get("source"),
        }
    else:
        data = profile.get(component)
    return {
        "code": 0,
        "data": data,
        "meta": profile.get("meta") or {},
        "decision": profile.get("decision") or {},
    }


@router.get("/stocks/{symbol}/profile")
async def get_stock_company_profile(
    symbol: str,
    as_of: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _stock_component_response(symbol, "company", as_of, refresh)


@router.get("/stocks/{symbol}/fundamentals")
async def get_stock_fundamentals(
    symbol: str,
    as_of: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _stock_component_response(symbol, "fundamentals", as_of, refresh)


@router.get("/stocks/{symbol}/earnings-quality")
async def get_stock_earnings_quality(
    symbol: str,
    as_of: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _stock_component_response(symbol, "earnings_quality", as_of, refresh)


@router.get("/stocks/{symbol}/valuation")
async def get_stock_valuation(
    symbol: str,
    as_of: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _stock_component_response(symbol, "valuation", as_of, refresh)


@router.get("/stocks/{symbol}/capital-impact")
async def get_stock_capital_impact(
    symbol: str,
    as_of: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _stock_component_response(symbol, "capital_impact", as_of, refresh)


@router.get("/stocks/{symbol}/attribution")
async def get_stock_attribution(
    symbol: str,
    as_of: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _stock_component_response(symbol, "attribution", as_of, refresh)


@router.get("/stocks/{symbol}/alpha")
async def get_stock_alpha(
    symbol: str,
    as_of: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _stock_component_response(symbol, "alpha", as_of, refresh)


@router.get("/stocks/{symbol}/sector-role")
async def get_stock_sector_role(
    symbol: str,
    as_of: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _stock_component_response(symbol, "sector_role", as_of, refresh)


@router.get("/stocks/{symbol}/sector-dependency")
async def get_stock_sector_dependency(
    symbol: str,
    as_of: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _stock_component_response(symbol, "sector_dependency", as_of, refresh)


@router.get("/stocks/{symbol}/emotion")
async def get_stock_emotion(
    symbol: str,
    as_of: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _stock_component_response(symbol, "emotion", as_of, refresh)


@router.get("/stocks/{symbol}/catalysts")
async def get_stock_catalysts(
    symbol: str,
    as_of: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _stock_component_response(symbol, "catalysts", as_of, refresh)


@router.get("/stocks/{symbol}/expectation-gap")
async def get_stock_expectation_gap(
    symbol: str,
    as_of: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _stock_component_response(symbol, "expectation_gap", as_of, refresh)


@router.get("/stocks/{symbol}/risk-reward")
async def get_stock_risk_reward(
    symbol: str,
    as_of: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _stock_component_response(symbol, "risk_reward", as_of, refresh)


@router.get("/stocks/{symbol}/strategy-fit")
async def get_stock_strategy_fit(
    symbol: str,
    as_of: str | None = Query(None),
    refresh: bool = Query(False),
):
    return await _stock_component_response(symbol, "strategy_fit", as_of, refresh)


@router.get("/stocks/{symbol}/decision-profile/history")
async def get_stock_decision_profile_history(
    symbol: str,
    limit: int = Query(30, ge=1, le=120),
):
    try:
        rows = await stock_essence_decision_service.history(symbol, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"code": 0, "data": {"history": rows, "count": len(rows)}}


@router.get("/stocks/{symbol}/decision-profile")
async def get_stock_decision_profile(
    symbol: str,
    as_of: str | None = Query(None),
    refresh: bool = Query(False),
):
    return {"code": 0, "data": await _load_stock_decision_profile(symbol, as_of, refresh)}


@router.post("/ai/stocks/{symbol}/decision")
async def explain_stock_decision(symbol: str, request: dict):
    as_of = str(request.get("as_of") or "").strip() or None
    refresh = bool(request.get("refresh"))
    profile = await _load_stock_decision_profile(symbol, as_of, refresh)
    decision = profile.get("decision") or {}
    company = profile.get("company") or {}
    fundamentals = profile.get("fundamentals") or {}
    attribution = profile.get("attribution") or {}
    sector_role = profile.get("sector_role") or {}
    risk_reward = profile.get("risk_reward") or {}
    structured = {
        "facts": profile.get("evidence") or [],
        "rise_attribution": attribution,
        "company": company,
        "earnings": fundamentals,
        "valuation": profile.get("valuation") or {},
        "sector": {
            "role": sector_role,
            "dependency": profile.get("sector_dependency") or {},
        },
        "emotion": profile.get("emotion") or {},
        "expectation_gap": profile.get("expectation_gap") or {},
        "catalysts": profile.get("catalysts") or {},
        "risk_reward": risk_reward,
        "strategy_fit": profile.get("strategy_fit") or {},
        "decision": decision,
    }
    system_prompt = (
        "你是A股个股本质决策解释器。只能解释输入中的已核验事实、计算和情景，"
        "不得修改decision.state，不得虚构客户、财务、资金或实时数据，不得把评分说成必涨。"
        "禁止直接说可以买；按事实、上涨归因、公司本体、盈利、估值、板块、情绪、预期差、"
        "催化剂、风险收益、策略适配、当前状态、失效条件的顺序，用简洁中文输出。"
    )
    prompt = json.dumps({
        "contract": (profile.get("meta") or {}).get("contract_version"),
        "data_date": (profile.get("meta") or {}).get("data_date"),
        "structured_decision": structured,
    }, ensure_ascii=False, default=str)[:28000]
    narrative = await ai_service.generate(prompt, system_prompt=system_prompt)
    if not narrative or narrative.startswith("[AI服务"):
        narrative = (
            f"{company.get('stock_name') or symbol}当前结构化状态为“{decision.get('label') or '观察'}”。"
            f"盈利质量为{fundamentals.get('earnings_quality') or '以已披露财报核验'}，"
            f"板块角色为{sector_role.get('role') or '按板块成分核验'}，"
            f"风险收益比为{risk_reward.get('risk_reward_ratio') if risk_reward.get('risk_reward_ratio') is not None else '按情景边界观察'}。"
            "最终状态由结构化规则锁定，AI说明不构成交易指令。"
        )
    return {
        "code": 0,
        "data": {
            "stock_code": company.get("stock_code"),
            "stock_name": company.get("stock_name"),
            "meta": profile.get("meta") or {},
            "decision": decision,
            "analysis": structured,
            "narrative": narrative,
            "guard": "AI解释不得修改结构化决策状态",
        },
    }


@router.get("/flow/north/today")
async def get_north_today():
    data = await collector.fetch_north_fund_flow()
    return {"code": 0, "data": {"record": data, **_market_metadata(available=bool(data), data_date=data.get("date") if data else None, is_realtime=False)}}


# ── 涨跌停板接口 ──


@router.get("/flow/limit-up")
async def get_limit_up():
    """获取涨停股票列表"""
    pool = await collector.fetch_limit_up_pool()
    data = pool["stocks"]
    stats = {
        "total": pool["total"],
        "continuous_boards": sum(1 for d in data if int(float(d.get("continuous_days", 0) or 0)) >= 2),
        "by_sector": {},
    }
    for d in data:
        sector = d.get("sector", "其他") or "其他"
        stats["by_sector"][sector] = stats["by_sector"].get(sector, 0) + 1
    return {"code": 0, "data": {"stocks": data, "stats": stats, **_quote_metadata(available=pool["trade_date"] is not None, data_date=pool["trade_date"])}}


@router.get("/flow/limit-down")
async def get_limit_down():
    """获取跌停股票列表"""
    pool = await collector.fetch_limit_down_pool()
    data = pool["stocks"]
    stats = {
        "total": pool["total"],
        "by_sector": {},
    }
    for d in data:
        sector = d.get("sector", "其他") or "其他"
        stats["by_sector"][sector] = stats["by_sector"].get(sector, 0) + 1
    return {"code": 0, "data": {"stocks": data, "stats": stats, **_quote_metadata(available=pool["trade_date"] is not None, data_date=pool["trade_date"])}}


# ── 美联储利率分析接口 ──

FED_RATE_HISTORY = [
    {"date": "2026-06-18", "rate": "4.25-4.50%", "change": "-25bp", "direction": "降息", "decision": "降息25个基点至4.25-4.50%", "reason": "通胀持续回落，就业市场降温"},
    {"date": "2026-05-07", "rate": "4.50-4.75%", "change": "0", "direction": "维持", "decision": "维持利率不变", "reason": "等待更多数据确认通胀趋势"},
    {"date": "2026-03-19", "rate": "4.50-4.75%", "change": "0", "direction": "维持", "decision": "维持利率不变", "reason": "经济数据好坏参半，维持观望"},
    {"date": "2026-01-29", "rate": "4.50-4.75%", "change": "0", "direction": "维持", "decision": "维持利率不变", "reason": "通胀粘性超预期，暂缓降息"},
    {"date": "2025-12-18", "rate": "4.50-4.75%", "change": "-25bp", "direction": "降息", "decision": "降息25个基点", "reason": "经济温和放缓，预防性降息"},
    {"date": "2025-11-07", "rate": "4.75-5.00%", "change": "-25bp", "direction": "降息", "decision": "降息25个基点", "reason": "就业数据不及预期"},
    {"date": "2025-09-18", "rate": "5.00-5.25%", "change": "-50bp", "direction": "降息", "decision": "大幅降息50个基点", "reason": "经济下行风险加大，启动降息周期"},
    {"date": "2025-07-31", "rate": "5.50-5.75%", "change": "0", "direction": "维持", "decision": "维持利率不变", "reason": "等待通胀进一步回落"},
    {"date": "2025-06-19", "rate": "5.50-5.75%", "change": "0", "direction": "维持", "decision": "维持利率不变", "reason": "核心通胀仍高于目标"},
    {"date": "2025-05-08", "rate": "5.50-5.75%", "change": "0", "direction": "维持", "decision": "维持利率不变", "reason": "关税不确定性影响经济前景"},
    {"date": "2025-03-20", "rate": "5.50-5.75%", "change": "0", "direction": "维持", "decision": "维持利率不变", "reason": "劳动力市场保持韧性"},
    {"date": "2025-01-30", "rate": "5.50-5.75%", "change": "0", "direction": "维持", "decision": "维持利率不变", "reason": "通胀仍处高位，不急于降息"},
    {"date": "2024-12-19", "rate": "5.50-5.75%", "change": "0", "direction": "维持", "decision": "维持利率不变", "reason": "观望政策滞后效应"},
    {"date": "2024-11-08", "rate": "5.50-5.75%", "change": "0", "direction": "维持", "decision": "维持利率不变", "reason": "经济韧性超预期"},
    {"date": "2024-09-20", "rate": "5.50-5.75%", "change": "0", "direction": "维持", "decision": "维持利率不变", "reason": "通胀持续降温但未达目标"},
    {"date": "2024-07-27", "rate": "5.50-5.75%", "change": "+25bp", "direction": "加息", "decision": "加息25个基点", "reason": "核心通胀反弹"},
    {"date": "2024-06-14", "rate": "5.25-5.50%", "change": "0", "direction": "维持", "decision": "维持利率不变", "reason": "等待更多数据"},
    {"date": "2024-05-03", "rate": "5.25-5.50%", "change": "+25bp", "direction": "加息", "decision": "加息25个基点", "reason": "劳动力市场过热"},
    {"date": "2024-03-22", "rate": "5.00-5.25%", "change": "+25bp", "direction": "加息", "decision": "加息25个基点", "reason": "通胀粘性强于预期"},
    {"date": "2024-01-31", "rate": "4.75-5.00%", "change": "+25bp", "direction": "加息", "decision": "加息25个基点", "reason": "继续抗通胀"},
    {"date": "2023-12-13", "rate": "4.50-4.75%", "change": "+25bp", "direction": "加息", "decision": "加息25个基点", "reason": "经济仍有过热风险"},
    {"date": "2023-11-01", "rate": "4.25-4.50%", "change": "+25bp", "direction": "加息", "decision": "加息25个基点", "reason": "通胀回落速度放缓"},
    {"date": "2023-09-20", "rate": "4.00-4.25%", "change": "+25bp", "direction": "加息", "decision": "加息25个基点", "reason": "油价上涨推升通胀预期"},
]

# 美联储利率对A股板块的具体影响矩阵
FED_SECTOR_IMPACT = {
    "加息": {
        "positive": [
            {"sector": "银行", "reason": "加息扩大净息差，银行利润增厚", "impact": 3},
            {"sector": "保险", "reason": "利率上行提升固收类资产收益", "impact": 2},
            {"sector": "券商", "reason": "若因经济好而加息，交易活跃利好券商", "impact": 1},
        ],
        "negative": [
            {"sector": "科技/半导体", "reason": "高利率压制成长股估值，融资成本上升", "impact": -3},
            {"sector": "新能源", "reason": "新能源企业负债率高，利息负担加重", "impact": -3},
            {"sector": "房地产", "reason": "房贷利率上升，购房需求下降", "impact": -3},
            {"sector": "黄金/有色", "reason": "美元走强，以美元计价的大宗商品承压", "impact": -2},
            {"sector": "消费", "reason": "借贷成本上升抑制消费意愿", "impact": -1},
        ],
    },
    "降息": {
        "positive": [
            {"sector": "科技/半导体", "reason": "折现率下降提升成长股估值，融资成本降低", "impact": 3},
            {"sector": "新能源", "reason": "低利率降低项目融资成本，利好资本开支", "impact": 3},
            {"sector": "黄金/有色", "reason": "美元走弱，大宗商品价格受益", "impact": 3},
            {"sector": "房地产", "reason": "房贷利率下降刺激购房需求", "impact": 2},
            {"sector": "消费", "reason": "借贷成本下降释放消费潜力", "impact": 2},
        ],
        "negative": [
            {"sector": "银行", "reason": "净息差收窄，利润空间被压缩", "impact": -3},
            {"sector": "保险", "reason": "固收类资产收益率下行", "impact": -2},
        ],
    },
    "维持": {
        "note": "利率维持不变时，市场更多受其他因素驱动。关注鲍威尔的措辞偏鹰还是偏鸽。",
    },
}


@router.get("/fed/history")
async def get_fed_history():
    return {"code": 0, "data": {"history": FED_RATE_HISTORY}}


@router.get("/fed/sector-impact")
async def get_fed_sector_impact():
    return {"code": 0, "data": FED_SECTOR_IMPACT}


@router.post("/fed/analysis")
async def get_fed_analysis(request: dict):
    """AI 分析美联储决策对A股的影响"""
    decision_date = request.get("date", "")
    decision = None
    for d in FED_RATE_HISTORY:
        if d["date"] == decision_date:
            decision = d
            break

    if not decision:
        decision = FED_RATE_HISTORY[0]

    direction = decision["direction"]
    impacts = FED_SECTOR_IMPACT.get(direction, {})

    prompt = f"""请用通俗易懂的语言，分析美联储{decision['date']}的利率决策对A股的影响。

【美联储决策】
- 日期: {decision['date']}
- 决策: {decision['decision']}
- 利率区间: {decision['rate']}
- 变动方向: {direction}
- 背景: {decision['reason']}

请按以下格式输出：

## 🔔 事件概述
[1-2句话概括]

## 📈 利好板块
{json.dumps(impacts.get('positive', []), ensure_ascii=False)}
请用大白话解释为什么利好，并举一两个A股的具体例子。

## 📉 利空板块  
{json.dumps(impacts.get('negative', []), ensure_ascii=False)}
请用大白话解释为什么利空。

## 💡 操作策略
给A股新手的2-3条实用建议，每条不超过30字。

要求：语言通俗，用生活化比喻，不推荐具体股票。"""

    report = await ai_service.generate(prompt)
    return {"code": 0, "data": {"decision": decision, "analysis": report}}


# ── 板块潜力股分析接口 ──


@router.get("/board/stocks/{board_code}")
async def get_board_stocks(
    board_code: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """获取概念板块的成分股列表及关键指标"""
    try:
        board_code = normalize_board_code(board_code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    data = await collector.fetch_board_stocks(board_code, page=page, page_size=page_size)
    data.update(_quote_metadata(available=bool(data.get("stocks"))))
    return {"code": 0, "data": data}


@router.get("/board/list")
async def get_board_list():
    """获取全部概念板块，并将实时资金热点排在前面。"""
    async with async_session() as session:
        legacy_rows = (await session.execute(
            select(ConceptBoard).order_by(ConceptBoard.code)
        )).scalars().all()
        cached_rows = (await session.execute(
            select(MarketBoard).where(MarketBoard.board_type == "concept").order_by(MarketBoard.code)
        )).scalars().all()

    legacy_by_code = {row.code: row for row in legacy_rows}
    try:
        live_rows = await _fetch_market_component(
            "board-directory", collector.fetch_all_concept_flow(), []
        )
    except Exception:
        live_rows = []

    if live_rows:
        boards = []
        for row in live_rows:
            code = str(row.get("code") or "")
            name = str(row.get("name") or "")
            if not code or not name:
                continue
            up_count = as_int(row.get("up_count"))
            down_count = as_int(row.get("down_count"))
            flat_count = as_int(row.get("flat_count"))
            active_stock_count = up_count + down_count + flat_count
            legacy = legacy_by_code.get(code)
            boards.append({
                "code": code,
                "name": name,
                "category": legacy.category if legacy else "概念板块",
                "stock_count": active_stock_count or (legacy.stock_count if legacy else None),
                "change_pct": as_float(row.get("change_pct")),
                "main_net_inflow": as_int(row.get("main_net_inflow")),
                "main_net_inflow_pct": as_float(row.get("main_net_inflow_pct")),
                "up_count": up_count,
                "down_count": down_count,
                "flat_count": flat_count,
                "leading_stock": str(row.get("leading_stock") or ""),
                "leading_stock_code": str(row.get("leading_stock_code") or ""),
                "leading_stock_change_pct": as_float(row.get("leading_stock_change_pct")),
            })
        boards.sort(
            key=lambda item: (
                item["main_net_inflow"],
                item["change_pct"],
                item["up_count"] - item["down_count"],
                item["stock_count"] or 0,
            ),
            reverse=True,
        )
        for rank, board in enumerate(boards, start=1):
            board["heat_rank"] = rank
    else:
        cached_by_code = {row.code: row for row in cached_rows}
        codes = sorted(set(cached_by_code) | set(legacy_by_code))
        boards = [
            {
                "code": code,
                "name": cached_by_code[code].name if code in cached_by_code else legacy_by_code[code].name,
                "category": legacy_by_code[code].category if code in legacy_by_code else "概念板块",
                "stock_count": legacy_by_code[code].stock_count if code in legacy_by_code else None,
                "change_pct": None,
                "main_net_inflow": None,
                "main_net_inflow_pct": None,
                "up_count": None,
                "down_count": None,
                "flat_count": None,
                "leading_stock": "",
                "leading_stock_code": "",
                "leading_stock_change_pct": None,
                "heat_rank": None,
            }
            for code in codes
        ]
    return {
        "code": 0,
        "data": boards,
        "meta": (
            _quote_metadata(available=bool(boards))
            if live_rows
            else _market_metadata(available=bool(boards), data_date=None, is_realtime=False, source="cache")
        ),
    }


@router.post("/board/ai-analysis")
async def board_ai_analysis(request: dict):
    """AI 量化分析板块潜力股"""
    board_code = request.get("board_code", "")
    board_name = request.get("board_name", "")
    top_n = request.get("top_n", 15)

    # 获取板块成分股数据
    data = await collector.fetch_board_stocks(board_code, page_size=min(top_n * 2, 100))
    stocks = data.get("stocks", [])

    if not stocks:
        return {"code": 0, "data": {"analysis": "当前数据源未返回该板块成分股，无法进行真实行情分析。", "available": False}}

    # 按主力净流入排序取前N只
    sorted_stocks = sorted(
        stocks,
        key=lambda s: float(s.get("main_net_inflow", "0") or 0),
        reverse=True,
    )[:top_n]

    # 构建分析提示词
    stock_list_text = ""
    for s in sorted_stocks:
        stock_list_text += (
            f"- {s['name']}({s['code']}): 价格{s['price']}, "
            f"涨跌幅{s['change_pct']}%, PE{s.get('pe','N/A')}, "
            f"换手率{s.get('turnover','N/A')}%, 量比{s.get('volume_ratio','N/A')}, "
            f"主力净流入{(float(s.get('main_net_inflow','0') or 0)/1e8):.2f}亿\n"
        )

    prompt = f"""你是一位A股量化分析师。请分析以下【{board_name}】概念板块的成分股数据，筛选出最具潜力的股票并给出评分。

## 板块概况
- 板块: {board_name}
- 成分股总数: {data.get('total', 0)}只
- 以下按主力资金净流入排序的TOP{len(sorted_stocks)}只股票:

{stock_list_text}

## 请按以下格式输出分析结果：

### 📊 综合研判
[2-3句话点评该板块当前整体状态]

### 🏆 潜力股排行榜（TOP5）

对每只股票给出：
- 股票名称(代码)
- 综合评分(1-10分)
- 一句话推荐理由
- 一个风险提示

### 💡 操作策略
2-3条实用建议，针对该板块的特点

### ⚠️ 风险提示
1-2条该板块特有的风险

## 评分标准
- 主力净流入大 +2分
- PE合理 +1.5分
- 换手率适中(3-10%) +1分
- 量比>1 +1分
- ROE高 +1.5分
- 涨跌幅适中(非追高) +1分

要求：语言客观理性，评分有依据，风险提示要具体。不要只说好话，要实事求是。"""

    analysis = await ai_service.generate(prompt)
    return {
        "code": 0,
        "data": {
            "board_name": board_name,
            "board_code": board_code,
            "stocks_analyzed": len(sorted_stocks),
            "analysis": analysis,
            "raw_stocks": sorted_stocks,
        },
    }


# ── 北向资金接口 ──


@router.get("/flow/north/daily")
async def get_north_daily(days: int = Query(10, ge=1, le=60)):
    """获取北向历史成交。汇总净买入未公开时明确返回 null。"""
    data = await collector.fetch_north_bound_daily(days=days)
    net_values = [row["net_inflow"] for row in data if row["net_inflow"] is not None]
    net_inflow_available = bool(data) and len(net_values) == len(data)

    return {
        "code": 0,
        "data": {
            "history": data,
            "summary": {
                "total_deal_amount": sum(row["deal_amount"] for row in data),
                "latest_deal_amount": data[-1]["deal_amount"] if data else None,
                "net_inflow_available": net_inflow_available,
                "total_inflow": sum(net_values) if net_inflow_available else None,
                "latest_inflow": net_values[-1] if net_inflow_available else None,
            },
            **_market_metadata(available=bool(data), data_date=data[-1]["date"] if data else None, is_realtime=False),
        },
    }


# ── 市场情绪接口 ──


@router.get("/market/sentiment")
async def get_market_sentiment():
    """市场情绪综合仪表盘"""
    breadth, turnover, concept, limit_up, limit_down = await asyncio.gather(
        _fetch_market_component("breadth", collector.fetch_market_breadth(), {}),
        _fetch_market_component("turnover", collector.fetch_market_turnover(), {}),
        _fetch_market_component("concept-flow", collector.fetch_concept_flow(page_size=20), []),
        _fetch_market_component("limit-up", collector.fetch_limit_up_pool(), {"stocks": [], "total": 0, "trade_date": None}),
        _fetch_market_component("limit-down", collector.fetch_limit_down_pool(), {"stocks": [], "total": 0, "trade_date": None}),
    )
    available = bool(breadth or turnover or concept)
    score = 50
    details: list[str] = []
    for market, market_data in breadth.items():
        if market_data["total"] <= 0:
            continue
        ratio = market_data["ratio"]
        if ratio > 70:
            score += 10
            details.append(f"{market}涨跌比{ratio}%，偏乐观")
        elif ratio < 30:
            score -= 10
            details.append(f"{market}涨跌比{ratio}%，偏悲观")
        else:
            details.append(f"{market}涨跌比{ratio}%，中性")
    up_count, down_count = limit_up["total"], limit_down["total"]
    if limit_up["trade_date"]:
        details.append(f"涨停{up_count}只，跌停{down_count}只")
    if up_count > 100:
        score += 15
    elif up_count > 50:
        score += 5
    if down_count > 50:
        score -= 15
    elif down_count > 10:
        score -= 5
    total_inflow = sum(as_int(row.get("main_net_inflow")) for row in concept)
    if concept:
        if total_inflow > 5_000_000_000:
            score += 10
            details.append("主力资金大幅流入")
        elif total_inflow < -5_000_000_000:
            score -= 10
            details.append("主力资金大幅流出")
    score = max(0, min(100, score)) if available else None
    label = "数据暂不可用" if score is None else (
        "极度乐观" if score >= 75 else "偏乐观" if score >= 60 else "中性" if score >= 45 else "偏悲观" if score >= 30 else "极度悲观"
    )

    main_flow_trend = None if not concept else (
        "流入" if total_inflow > 0 else "流出" if total_inflow < 0 else "平衡"
    )
    return {
        "code": 0,
        "data": {
            "score": score,
            "label": label,
            "details": details,
            "breadth": breadth,
            "turnover": turnover,
            "limit_counts": {"up": up_count, "down": down_count},
            "main_flow_trend": main_flow_trend,
            "main_flow_amount": total_inflow,
            **_quote_metadata(available=available),
        },
    }


# ── 市场环境与题材强弱 ──


@router.get("/topic-strength")
async def get_topic_strength(
    target_date: Optional[str] = Query(None, alias="date"),
    refresh: bool = Query(False),
):
    parsed_date = None
    if target_date:
        try:
            parsed_date = date.fromisoformat(target_date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="date 必须是 YYYY-MM-DD") from exc
    try:
        data = await topic_strength_service.get(parsed_date, force=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"code": 0, "data": data}


@router.get("/topic-strength/dates")
async def get_topic_strength_dates(limit: int = Query(120, ge=1, le=500)):
    dates = await topic_strength_service.dates(limit)
    return {"code": 0, "data": {"dates": dates, "count": len(dates), "source": "database_cache"}}


@router.post("/topic-strength/analysis")
async def analyze_topic_strength(request: dict | None = None):
    payload = request or {}
    parsed_date = None
    if payload.get("date"):
        try:
            parsed_date = date.fromisoformat(str(payload["date"])[:10])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="date 必须是 YYYY-MM-DD") from exc
    try:
        data = await topic_strength_service.analyze(
            parsed_date,
            force=bool(payload.get("refresh", False)),
            use_ai=bool(payload.get("use_ai", True)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"code": 0, "data": data}


@router.get("/kline")
async def get_kline(
    code: str = Query(..., min_length=6, max_length=16),
    category: int = Query(4),
    offset: int = Query(60, ge=1, le=800),
    as_of: str | None = Query(None),
):
    try:
        parsed_as_of = date.fromisoformat(as_of) if as_of else None
        data = await topic_strength_service.kline(
            code,
            category=category,
            offset=offset,
            as_of=parsed_as_of,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"code": 0, "data": data}


# ── 轮动追踪接口 ──


@router.get("/flow/observer")
async def get_flow_observer(
    board_type: str = Query("industry"),
    limit: int = Query(9, ge=4, le=12),
    target_date: Optional[str] = Query(None, alias="date"),
):
    """Return a real-time or cached daily snapshot for the animated observer."""
    if not isinstance(target_date, str):
        target_date = None
    normalized_type = board_type.strip().lower()
    if normalized_type not in {"industry", "concept"}:
        raise HTTPException(status_code=422, detail="board_type 仅支持 industry 或 concept")
    now = shanghai_now()
    if target_date:
        try:
            requested_date = date.fromisoformat(target_date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="date 必须是 YYYY-MM-DD") from exc
        if requested_date > now.date():
            raise HTTPException(status_code=422, detail="不能查询未来交易日")
        if requested_date < now.date():
            return {"code": 0, "data": await _historical_flow_observer(normalized_type, requested_date, limit)}
    if not is_a_share_market_session(now):
        model = IndustryFundFlowDaily if normalized_type == "industry" else ConceptFundFlowDaily
        latest = await _latest_cached_trade_date(model)
        if latest:
            return {"code": 0, "data": await _historical_flow_observer(normalized_type, latest, limit)}
    return {"code": 0, "data": await _realtime_flow_observer(normalized_type, limit)}


@router.get("/flow/observer/dates")
async def get_flow_observer_dates(board_type: str = Query("industry")):
    """List cached dates and coverage for the historical observer playback."""
    normalized_type = board_type.strip().lower()
    if normalized_type not in {"industry", "concept"}:
        raise HTTPException(status_code=422, detail="board_type 仅支持 industry 或 concept")
    dates = await _flow_observer_history_dates(normalized_type)
    return {
        "code": 0,
        "data": {
            "board_type": normalized_type,
            "dates": dates,
            "count": len(dates),
            "source": "cache",
        },
    }


@router.post("/flow/observer/analysis")
async def analyze_flow_observer(request: dict | None = None):
    payload = request or {}
    board_type = str(payload.get("board_type") or "industry").strip().lower()
    window = str(payload.get("window") or "week").strip().lower()
    try:
        result = await flow_analysis_service.analyze(board_type, window)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"code": 0, "data": result, "windows": FLOW_WINDOWS}


@router.get("/flow/rotation")
async def get_sector_rotation():
    """获取板块轮动数据"""
    data = await collector.fetch_sector_rotation()
    data.update(_quote_metadata(available=bool(data.get("sectors"))))
    return {"code": 0, "data": data}


@router.post("/flow/rotation/analysis")
async def analyze_sector_rotation(request: dict | None = None):
    """Run a cached multi-session analysis for the concept rotation page."""
    window = str((request or {}).get("window") or "week").strip().lower()
    try:
        result = await flow_analysis_service.analyze("concept", window)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"code": 0, "data": result, "windows": FLOW_WINDOWS}


@router.get("/dragon/board")
async def get_dragon_board(
    target_date: Optional[str] = Query(None, alias="date"),
    refresh: bool = Query(False),
):
    """Return one persisted Dragon-Tiger List session, with an upstream cache fill when missing."""
    requested_date = None
    if target_date:
        try:
            requested_date = date.fromisoformat(target_date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="date 必须是 YYYY-MM-DD") from exc
        if requested_date > shanghai_now().date():
            raise HTTPException(status_code=422, detail="不能查询未来交易日")
    data = await dragon_board_service.get_board(requested_date, force_refresh=refresh)
    return {"code": 0, "data": data}


@router.get("/dragon/board/dates")
async def get_dragon_board_dates(limit: int = Query(250, ge=1, le=500)):
    dates = await dragon_board_service.list_dates(limit)
    return {"code": 0, "data": {"dates": dates, "count": len(dates), "source": "database_cache"}}


@router.post("/dragon/board/analysis")
async def analyze_dragon_board(request: dict | None = None):
    window = str((request or {}).get("window") or "week").strip().lower()
    try:
        result = await dragon_board_service.analyze(window)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"code": 0, "data": result, "windows": DRAGON_WINDOWS}


@router.post("/dragon/board/refresh")
async def refresh_dragon_board(request: dict | None = None):
    raw_date = (request or {}).get("date")
    target_date = None
    if raw_date:
        try:
            target_date = date.fromisoformat(str(raw_date))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="date 必须是 YYYY-MM-DD") from exc
    result = await dragon_board_service.refresh(target_date)
    data = await dragon_board_service.get_board(target_date)
    return {"code": 0, "data": data, "refresh": result}


@router.get("/block-trade/list")
async def get_block_trades():
    """获取大宗交易列表"""
    trades = await collector.fetch_block_trades()
    data_date = max((trade["date"] for trade in trades if trade.get("date")), default=None)
    return {"code": 0, "data": {
        "trades": trades,
        "summary": {"total": len(trades), "total_amount": sum(trade["amount"] for trade in trades), "premium_count": sum(trade["premium"] > 0 for trade in trades)},
        **_market_metadata(available=bool(trades), data_date=data_date, is_realtime=False),
    }}


@router.post("/block-trade/analysis")
async def analyze_block_trades(request: dict | None = None):
    """Analyze the current block-trade snapshot against verified/cached quotes."""
    payload = request or {}
    trades = await collector.fetch_block_trades(
        page=int(payload.get("page") or 1),
        page_size=min(max(int(payload.get("page_size") or 50), 1), 100),
    )
    result = await block_trade_analysis_service.analyze(
        trades,
        selected_code=str(payload.get("code") or "") or None,
        use_ai=bool(payload.get("use_ai", True)),
    )
    return {"code": 0, "data": result}


@router.get("/screener/technical")
async def get_technical_screener(
    min_change: float = Query(2), max_pe: int = Query(100), min_turnover: float = Query(3),
):
    """Backward-compatible technical screen backed by the configurable service."""
    try:
        data = await technical_screener_service.run({
            "preset": "custom",
            "change_pct": [min_change, 20.0],
            "pe_ttm": [-1000.0, float(max_pe)],
            "turnover_pct": [min_turnover, 100.0],
        })
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"code": 0, "data": data}


@router.get("/screener/technical/config")
async def get_technical_screener_config():
    return {"code": 0, "data": {"schema": SCREENER_SCHEMA, "presets": SCREENER_PRESETS}}


@router.post("/screener/technical/run")
async def run_technical_screener(request: dict | None = None):
    try:
        data = await technical_screener_service.run(request or {})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"code": 0, "data": data}


# ── 智能选股 Agent ──


@router.get("/stock-selection/sectors")
async def get_stock_selection_sectors():
    """List every live industry board available to the stock-selection pipeline."""
    cache_key = "stock_selection_sector_directory_v1"
    seed_sectors: list[dict] = []
    classification_refreshed_at: str | None = None
    directory_stale = False
    try:
        async with async_session() as session:
            cache_row = await session.get(MarketDataCache, cache_key)
            if cache_row is None:
                cache_row = await session.get(PersonalSystemConfig, cache_key)
        payload = cache_row.payload if cache_row and isinstance(cache_row.payload, dict) else {}
        raw_refreshed_at = payload.get("classification_refreshed_at")
        refreshed_at = datetime.fromisoformat(raw_refreshed_at) if raw_refreshed_at else None
        cached_sectors = payload.get("sectors")
        if isinstance(cached_sectors, list):
            seed_sectors = [item for item in cached_sectors if isinstance(item, dict)]
            classification_refreshed_at = raw_refreshed_at
            directory_stale = not refreshed_at or datetime.utcnow() - refreshed_at > timedelta(hours=24)
    except Exception as exc:
        print(f"Sector directory cache load failed: {type(exc).__name__}")

    universe_data_date = None
    universe_updated_at = None
    try:
        market_snapshot = await load_quant_market_snapshot()
        snapshot_stocks = (
            market_snapshot.get("stocks") or []
            if isinstance(market_snapshot, dict) and market_snapshot.get("complete")
            else []
        )
        live_counts: dict[str, int] = {}
        for stock in snapshot_stocks:
            sector_name = str(stock.get("sector") or "").strip()
            if sector_name:
                live_counts[sector_name] = live_counts.get(sector_name, 0) + 1
        if live_counts:
            cached_by_name = {
                str(item.get("name") or "").strip(): item
                for item in seed_sectors
                if str(item.get("name") or "").strip()
            }
            seed_sectors = [
                {
                    **cached_by_name.get(name, {}),
                    "name": name,
                    "candidate_count": count,
                    "stock_count": count,
                    "count_source": "stock_universe",
                }
                for name, count in live_counts.items()
            ]
            universe_data_date = market_snapshot.get("data_date")
            universe_updated_at = market_snapshot.get("fetched_at") or market_snapshot.get("source_updated_at")
            classification_refreshed_at = universe_updated_at or classification_refreshed_at
            directory_stale = False
    except Exception as exc:
        print(f"Dynamic stock universe count load failed: {type(exc).__name__}")

    sectors = await collector.fetch_intelligent_selection_sectors(seed_sectors=seed_sectors)
    coverage_complete = bool(sectors) and all(
        item.get("count_source") == "stock_universe" for item in sectors
    )
    covered_stock_count = (
        sum(as_int(item.get("stock_count")) for item in sectors)
        if coverage_complete
        else None
    )
    if coverage_complete:
        classification_refreshed_at = classification_refreshed_at or datetime.utcnow().isoformat()
        try:
            async with async_session() as session:
                cache_row = await session.get(MarketDataCache, cache_key)
                payload = {
                    "classification_refreshed_at": classification_refreshed_at,
                    "sectors": sectors,
                }
                if cache_row:
                    cache_row.payload = payload
                    cache_row.updated_at = datetime.utcnow()
                else:
                    session.add(MarketDataCache(key=cache_key, payload=payload))
                await session.commit()
        except Exception as exc:
            print(f"Sector directory cache save failed: {type(exc).__name__}")
    return {
        "code": 0,
        "data": {
            "sectors": sectors,
            "sector_count": len(sectors),
            "covered_stock_count": covered_stock_count,
            "coverage_complete": coverage_complete,
            "mapped_sector_count": sum(bool(item.get("code")) for item in sectors),
            "classification_refreshed_at": classification_refreshed_at,
            "universe_data_date": universe_data_date,
            "universe_updated_at": universe_updated_at,
            "directory_cache_used": bool(seed_sectors),
            "directory_stale": directory_stale,
            **_quote_metadata(available=bool(sectors)),
        },
    }


async def _store_stock_selection_run(result: dict) -> int | None:
    """Persist the complete agent trace without making a live scan depend on DB health."""
    try:
        raw_date = result.get("data_date")
        run = StockSelectionRun(
            mode=result["mode"],
            risk_profile=result["risk_profile"],
            candidate_count=as_int((result.get("candidate_summary") or {}).get("analyzed")),
            selected_count=as_int((result.get("candidate_summary") or {}).get("selected")),
            source=result.get("source", "eastmoney"),
            data_date=date.fromisoformat(raw_date) if raw_date else None,
            is_realtime=bool(result.get("is_realtime")),
            result=result,
        )
        async with async_session() as session:
            session.add(run)
            await session.commit()
            await session.refresh(run)
            return run.id
    except Exception as exc:
        print(f"Stock selection trace save failed: {type(exc).__name__}")
        return None


@router.post("/stock-selection/run")
async def run_stock_selection(request: dict | None = None):
    """Run a source-backed, multi-agent A-share selection workflow."""
    payload = request or {}
    mode = str(payload.get("mode", "quick")).strip().lower()
    risk_profile = str(payload.get("risk_profile", "balanced")).strip().lower()
    horizon = str(payload.get("horizon", "week")).strip().lower()
    try:
        top_n = int(payload.get("top_n", 5))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="top_n 必须是整数") from exc

    if mode not in VALID_SELECTION_MODES:
        raise HTTPException(status_code=422, detail="mode 仅支持 quick 或 full")
    if risk_profile not in VALID_RISK_PROFILES:
        raise HTTPException(status_code=422, detail="risk_profile 仅支持 conservative、balanced 或 aggressive")
    if horizon not in VALID_HORIZONS:
        raise HTTPException(status_code=422, detail="horizon 仅支持 week、half_month 或 month")
    if not 3 <= top_n <= 10:
        raise HTTPException(status_code=422, detail="top_n 必须在 3 到 10 之间")
    raw_sector = payload.get("sector")
    if raw_sector is not None and not isinstance(raw_sector, str):
        raise HTTPException(status_code=422, detail="sector 必须是行业名称字符串")
    sector = raw_sector.strip() if isinstance(raw_sector, str) else None
    if sector and len(sector) > 60:
        raise HTTPException(status_code=422, detail="sector 长度不能超过 60 个字符")
    raw_sector_code = payload.get("sector_code")
    if raw_sector_code is not None and not isinstance(raw_sector_code, str):
        raise HTTPException(status_code=422, detail="sector_code 必须是行业板块编码")
    try:
        sector_code = normalize_board_code(raw_sector_code) if raw_sector_code else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if sector_code and not sector:
        raise HTTPException(status_code=422, detail="sector_code 必须与 sector 一起提交")
    factor_filters = payload.get("factor_filters")
    if factor_filters is not None and not isinstance(factor_filters, dict):
        raise HTTPException(status_code=422, detail="factor_filters 必须是对象")

    try:
        result = await stock_selection_agents.run(
            mode=mode,
            risk_profile=risk_profile,
            top_n=top_n,
            sector=sector,
            sector_code=sector_code,
            horizon=horizon,
            factor_filters=factor_filters,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    run_id = await _store_stock_selection_run(result)
    result["run_id"] = run_id
    result["trace_available"] = run_id is not None
    return {"code": 0, "data": result}


@router.get("/stock-selection/runs")
async def list_stock_selection_runs(limit: int = Query(10, ge=1, le=30)):
    """List recent selection runs so a user can identify an auditable snapshot."""
    async with async_session() as session:
        rows = (await session.execute(
            select(StockSelectionRun)
            .order_by(StockSelectionRun.created_at.desc())
            .limit(limit)
        )).scalars().all()
    return {"code": 0, "data": [
        {
            "id": row.id,
            "mode": row.mode,
            "risk_profile": row.risk_profile,
            "candidate_count": row.candidate_count,
            "selected_count": row.selected_count,
            "source": row.source,
            "data_date": row.data_date.isoformat() if row.data_date else None,
            "is_realtime": row.is_realtime,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]}


@router.get("/stock-selection/runs/{run_id}")
async def get_stock_selection_run(run_id: int):
    """Load the saved source data and every agent output for one run."""
    async with async_session() as session:
        row = await session.get(StockSelectionRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到该次智能选股记录")
    return {"code": 0, "data": {"id": row.id, "created_at": row.created_at.isoformat(), **row.result}}


# ── 历史数据查询接口 ──


async def _build_market_overview(
    *,
    bypass_cache: bool = False,
    expected_data_date: str | None = None,
):
    """今日速览：聚合所有看板的核心数据（小白友好首页）"""
    cache_key = "market_overview_v1"
    # During evenings, weekends, and holidays the live endpoints can spend
    # several seconds timing out even though the last verified snapshot is
    # already available. Return that snapshot first; scheduled cache jobs keep
    # it current and live sessions still use the source-backed path below.
    if not bypass_cache and not is_a_share_market_session(shanghai_now()):
        cached = await _read_json_snapshot(cache_key)
        if cached:
            enriched, changed = await _enrich_cached_market_indices(cached)
            if changed:
                saved_at = await _write_json_snapshot(cache_key, enriched)
                if saved_at:
                    enriched["snapshot_saved_at"] = saved_at
            return {"code": 0, "data": _cached_market_overview_payload(enriched)}

    expected_data_date = _market_date(expected_data_date)
    target_date = date.fromisoformat(expected_data_date) if expected_data_date else None

    north, concept_inflow, concept_outflow, limit_up, limit_down, breadth, turnover = await asyncio.gather(
        _fetch_market_component("northbound", collector.fetch_north_bound_daily(days=5), []),
        _fetch_market_component("concept-inflow", collector.fetch_concept_flow(page_size=20), []),
        _fetch_market_component("concept-outflow", collector.fetch_concept_flow(sort_order=1, page_size=20), []),
        _fetch_market_component("limit-up", collector.fetch_limit_up_pool(target_date=target_date), {"stocks": [], "total": 0, "trade_date": None}),
        _fetch_market_component("limit-down", collector.fetch_limit_down_pool(target_date=target_date), {"stocks": [], "total": 0, "trade_date": None}),
        _fetch_market_component("breadth", collector.fetch_market_breadth(), {}),
        _fetch_market_component("turnover", collector.fetch_market_turnover(), {}),
    )
    turnover = await _attach_tencent_index_history(turnover)
    top_inflow = sorted(concept_inflow, key=lambda row: as_int(row.get("main_net_inflow")), reverse=True)[:3]
    top_outflow = sorted(concept_outflow, key=lambda row: as_int(row.get("main_net_inflow")))[:3]
    latest_north = north[-1] if north else {}
    hot_sectors = [
        {
            "code": row.get("code", ""),
            "name": row.get("name", ""),
            "change_pct": as_float(row.get("change_pct")),
            "main_net_inflow": as_int(row.get("main_net_inflow")),
            "super_large_inflow": as_int(row.get("super_large_net_inflow")),
            "large_inflow": as_int(row.get("large_net_inflow")),
            "up_count": as_int(row.get("up_count")),
            "down_count": as_int(row.get("down_count")),
        }
        for row in top_inflow
    ]
    latest_north_date = _market_date(latest_north.get("date"))
    turnover_date = _market_date(turnover.get("data_date"))
    breadth_date = _market_date((breadth.get("全市场") or {}).get("data_date"))
    limit_up_date = _market_date(limit_up.get("trade_date"))
    limit_down_date = _market_date(limit_down.get("trade_date"))
    data_date = (
        expected_data_date
        or turnover_date
        or breadth_date
        or limit_up_date
        or limit_down_date
        or latest_north_date
    )
    component_dates = {
        "market_index": turnover_date,
        "market_breadth": breadth_date,
        "concept_flow": data_date if concept_inflow or concept_outflow else None,
        "limit_up": limit_up_date,
        "limit_down": limit_down_date,
        "northbound": latest_north_date,
    }
    date_warnings: list[str] = []

    def same_snapshot(component: str, component_date: str | None) -> bool:
        if not component_date:
            return False
        if data_date and component_date != data_date:
            date_warnings.append(f"{component}数据日{component_date}与看板数据日{data_date}不一致，已从本次看板剔除")
            return False
        return True

    turnover_available = bool(turnover) and same_snapshot("上证指数", turnover_date)
    breadth_available = bool(breadth) and same_snapshot("市场宽度", breadth_date)
    limit_up_available = same_snapshot("涨停池", limit_up_date)
    limit_down_available = same_snapshot("跌停池", limit_down_date)
    north_available = bool(north) and same_snapshot("北向成交额", latest_north_date)
    if not turnover_available:
        turnover = {}
    if not breadth_available:
        breadth = {}
    if not north_available:
        latest_north = {}
    available = bool(turnover or concept_inflow or concept_outflow or breadth or limit_up_available or limit_down_available)
    now = shanghai_now()
    is_realtime = bool(
        available
        and data_date == now.date().isoformat()
        and is_a_share_market_session(now)
        and turnover.get("is_realtime")
    )

    response = {
        "code": 0,
        "data": _normalize_market_overview_payload({
            "update_time": shanghai_now().isoformat(),
            "market_index": turnover,
            "north_bound": {
                "latest_deal_amount": latest_north.get("deal_amount"),
                "latest_inflow": latest_north.get("net_inflow"),
                "net_inflow_available": latest_north.get("net_inflow") is not None,
            },
            "fund_flow": {
                "top_inflow": [{"name": row["name"], "inflow": as_int(row.get("main_net_inflow"))} for row in top_inflow],
                "top_outflow": [{"name": row["name"], "outflow": as_int(row.get("main_net_inflow"))} for row in top_outflow],
            },
            "limit_board": {
                "limit_up": limit_up["total"] if limit_up_available else None,
                "limit_down": limit_down["total"] if limit_down_available else None,
            },
            "market_breadth": breadth,
            "hot_sectors": hot_sectors,
            "source_status": {
                "northbound": north_available,
                "concept_inflow": bool(concept_inflow),
                "concept_outflow": bool(concept_outflow),
                "limit_up": limit_up_available,
                "limit_down": limit_down_available,
                "market_breadth": breadth_available,
                "market_turnover": turnover_available,
            },
            "component_dates": component_dates,
            "data_warnings": date_warnings,
            "snapshot_status": "complete" if all((
                turnover_available,
                breadth_available,
                bool(concept_inflow),
                bool(concept_outflow),
                limit_up_available,
                limit_down_available,
            )) else "partial",
            **_market_metadata(
                available=available,
                data_date=data_date if available else None,
                is_realtime=is_realtime,
                source=turnover.get("source") or "eastmoney",
            ),
            "source_updated_at": turnover.get("source_updated_at"),
            "cache_used": False,
        }),
    }
    source_status = response["data"]["source_status"]
    turnover_complete = all(
        key in turnover
        for key in ("sh_index", "sh_change", "sh_change_pct", "sh_amount")
    ) and as_float(turnover.get("sh_index")) > 0 and as_float(turnover.get("sh_amount")) > 0
    cacheable = (
        source_status["concept_inflow"]
        and source_status["concept_outflow"]
        and turnover_complete
    )
    if cacheable and not any("上证指数" in warning for warning in date_warnings):
        saved_at = await _write_json_snapshot(cache_key, response["data"])
        response["data"]["snapshot_saved_at"] = saved_at
        response["data"]["refresh_status"] = "updated" if saved_at else "write_failed"
        return response
    cached = await _read_json_snapshot(cache_key)
    if cached:
        payload = _cached_market_overview_payload(cached)
        live_index = response["data"].get("market_index") or {}
        live_indices = live_index.get("indices") if isinstance(live_index, dict) else None
        if isinstance(live_indices, dict) and live_indices:
            cached_index = payload.get("market_index") or {}
            merged_index = {**cached_index, **live_index}
            # Auction snapshots have valid prices but no full-session amount;
            # retain the last complete amount until the close snapshot arrives.
            if as_float(live_index.get("sh_amount")) <= 0 and as_float(cached_index.get("sh_amount")) > 0:
                merged_index["sh_amount"] = cached_index["sh_amount"]
            if as_float(live_index.get("sh_volume")) <= 0 and as_float(cached_index.get("sh_volume")) > 0:
                merged_index["sh_volume"] = cached_index["sh_volume"]
            payload["market_index"] = merged_index
            payload["refresh_status"] = "partial_live_index"
        if bypass_cache:
            payload["refresh_status"] = "unchanged"
            payload["data_warnings"] = list(dict.fromkeys([
                *(payload.get("data_warnings") or []),
                *date_warnings,
                "本次数据源未形成同日完整快照，已保留上一次核验缓存",
            ]))
        return {"code": 0, "data": payload}
    response["data"]["refresh_status"] = "not_cached"
    return response


async def refresh_market_overview_after_sync(sync_result: dict) -> dict:
    expected_data_date = _market_date(
        sync_result.get("data_date")
        or (sync_result.get("stock_bars") or {}).get("data_date")
    )
    return await _build_market_overview(
        bypass_cache=True,
        expected_data_date=expected_data_date,
    )


@router.get("/market/overview")
async def get_market_overview(refresh: bool = False):
    return await _build_market_overview(bypass_cache=refresh)


@router.get("/market/workbench")
async def get_market_decision_workbench(refresh: bool = Query(False)):
    """Return one date-aligned decision contract for the market workbench."""
    data = await market_decision_workbench_service.get(force=refresh)
    data = await decision_workbench_2026_service.decorate(data)
    return {"code": 0, "data": data}


@router.get("/market/workbench/snapshots")
async def list_market_decision_snapshots(
    limit: int = Query(30, ge=1, le=100),
    phase: str | None = Query(None),
):
    data = await decision_workbench_2026_service.list_snapshots(limit=limit, phase=phase)
    return {"code": 0, "data": data}


@router.get("/market/workbench/snapshots/{snapshot_id}")
async def get_market_decision_snapshot(snapshot_id: int):
    try:
        data = await decision_workbench_2026_service.get_snapshot(snapshot_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"code": 0, "data": data}


@router.post("/market/workbench/snapshots")
async def capture_market_decision_snapshot(request: dict | None = None):
    body = request or {}
    try:
        data = await decision_workbench_2026_service.capture(
            str(body.get("phase") or "manual"),
            force=bool(body.get("force", True)),
            user_judgment=str(body.get("user_judgment") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"code": 0, "data": data}


@router.post("/market/workbench/validate")
async def validate_market_decision_snapshot(request: dict | None = None):
    raw_date = str((request or {}).get("decision_date") or "")
    target = None
    if raw_date:
        normalized_date = _market_date(raw_date)
        if normalized_date is None:
            raise HTTPException(status_code=422, detail="decision_date 必须是有效日期")
        target = date.fromisoformat(normalized_date)
    data = await decision_workbench_2026_service.validate(target)
    return {"code": 0, "data": data}


@router.get("/truth/status")
async def get_truth_status(refresh: bool = Query(False)):
    """Return the four-time, source-grade, conflict, and PIT audit."""
    return {"code": 0, "data": await market_way_v4_service.truth_status(force=refresh)}


@router.get("/truth/conflicts")
async def get_truth_conflicts(limit: int = Query(100, ge=1, le=500)):
    records = await market_way_v4_service.conflicts(limit=limit)
    return {"code": 0, "data": {"records": records, "count": len(records)}}


@router.get("/truth/quality-events")
async def get_truth_quality_events(limit: int = Query(100, ge=1, le=500)):
    records = await market_way_v4_service.quality_events(limit=limit)
    return {"code": 0, "data": {"records": records, "count": len(records)}}


async def _current_market_way(refresh: bool = False) -> tuple[dict, dict]:
    payload = await market_way_v4_service.current(force=refresh)
    return payload, payload.get("market_way_v4") or {}


@router.get("/way/market")
async def get_market_way(refresh: bool = Query(False)):
    _, v4 = await _current_market_way(refresh)
    return {"code": 0, "data": v4}


@router.get("/way/order")
async def get_market_order(refresh: bool = Query(False)):
    _, v4 = await _current_market_way(refresh)
    momentum = v4.get("momentum") or {}
    return {"code": 0, "data": {
        "state": momentum.get("order_state"), "score": momentum.get("order_score"),
        "change": momentum.get("order_change"), "trajectory": momentum.get("order_trajectory") or [],
        "trade_date": (v4.get("truth") or {}).get("research_trade_date"),
    }}


@router.get("/way/change")
async def get_market_way_change(refresh: bool = Query(False)):
    _, v4 = await _current_market_way(refresh)
    momentum = v4.get("momentum") or {}
    return {"code": 0, "data": {
        "momentum_state": momentum.get("state"), "direction": momentum.get("direction"),
        "marginal_change": momentum.get("marginal_change"),
        "persistence_sessions": momentum.get("persistence_sessions"),
        "order_change": momentum.get("order_change"), "evidence": momentum.get("evidence") or [],
    }}


@router.get("/way/data/status")
async def get_market_way_data_status():
    return {"code": 0, "data": await market_way_v4_service.data_status()}


@router.post("/way/data/refresh")
async def refresh_market_way_data(request: dict | None = None):
    background = bool((request or {}).get("background", True))
    return {"code": 0, "data": await market_way_v4_service.refresh_sources(background=background)}


@router.get("/national-directions")
async def get_national_directions(refresh: bool = Query(False)):
    _, v4 = await _current_market_way(refresh)
    return {"code": 0, "data": v4.get("national_direction_radar") or {}}


@router.get("/national-directions/{direction_id}")
async def get_national_direction(direction_id: str, refresh: bool = Query(False)):
    _, v4 = await _current_market_way(refresh)
    direction = next((item for item in (v4.get("national_direction_radar") or {}).get("directions") or [] if item.get("id") == direction_id), None)
    if direction is None:
        raise HTTPException(status_code=404, detail="国家方向不存在")
    return {"code": 0, "data": direction}


@router.get("/policies")
async def get_market_way_policies(refresh: bool = Query(False)):
    _, v4 = await _current_market_way(refresh)
    policies = [
        {**policy, "direction_id": item.get("id"), "direction_name": item.get("name")}
        for item in (v4.get("national_direction_radar") or {}).get("directions") or []
        for policy in item.get("policies") or []
    ]
    policies.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    return {"code": 0, "data": {"records": policies, "count": len(policies)}}


@router.get("/policies/{direction_id}/transmission")
async def get_policy_transmission(direction_id: str, refresh: bool = Query(False)):
    _, v4 = await _current_market_way(refresh)
    direction = next((item for item in (v4.get("national_direction_radar") or {}).get("directions") or [] if item.get("id") == direction_id), None)
    if direction is None:
        raise HTTPException(status_code=404, detail="政策方向不存在")
    return {"code": 0, "data": {
        "direction_id": direction_id, "direction_name": direction.get("name"),
        "marginal_state": direction.get("marginal_state"),
        "max_verified_level": direction.get("max_verified_level"),
        "transmission_state": direction.get("transmission_state"),
        "stages": direction.get("stages") or [], "policies": direction.get("policies") or [],
        "gap": direction.get("gap") or {},
    }}


@router.get("/industries/{direction_id}/validation")
async def get_industry_validation(direction_id: str, refresh: bool = Query(False)):
    _, v4 = await _current_market_way(refresh)
    direction = next((item for item in (v4.get("national_direction_radar") or {}).get("directions") or [] if item.get("id") == direction_id), None)
    if direction is None:
        raise HTTPException(status_code=404, detail="产业方向不存在")
    return {"code": 0, "data": {
        "direction_id": direction_id, "direction_name": direction.get("name"),
        "industry_validation": direction.get("industry_validation") or {},
        "market_validation": direction.get("market_validation") or {},
        "gap": direction.get("gap") or {},
    }}


@router.get("/capital/migration")
async def get_capital_migration(refresh: bool = Query(False)):
    _, v4 = await _current_market_way(refresh)
    return {"code": 0, "data": v4.get("capital_migration") or {}}


@router.get("/capital/risk-appetite")
async def get_capital_risk_appetite(refresh: bool = Query(False)):
    _, v4 = await _current_market_way(refresh)
    capital = v4.get("capital_migration") or {}
    return {"code": 0, "data": {
        "risk_appetite": capital.get("risk_appetite"), "stage": capital.get("stage"),
        "rotation_type": capital.get("rotation_type"), "evidence": capital.get("evidence") or [],
    }}


@router.get("/market/forces")
async def get_market_forces(refresh: bool = Query(False)):
    _, v4 = await _current_market_way(refresh)
    return {"code": 0, "data": v4.get("market_force") or {}}


@router.get("/decisions/current")
async def get_current_v4_decision(refresh: bool = Query(False)):
    payload, v4 = await _current_market_way(refresh)
    return {"code": 0, "data": {
        "meta": payload.get("meta") or {}, "market_way_v4": v4,
        "trading_permission": (payload.get("decision_2026") or {}).get("trading_permission") or {},
    }}


@router.post("/decisions/snapshot")
async def capture_v4_decision_snapshot(request: dict | None = None):
    body = request or {}
    try:
        data = await decision_workbench_2026_service.capture(
            str(body.get("phase") or "manual"), force=bool(body.get("force", True)),
            user_judgment=str(body.get("user_judgment") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"code": 0, "data": data}


@router.get("/decisions/judgments")
async def list_v4_judgments(limit: int = Query(50, ge=1, le=200)):
    records = await market_way_v4_service.judgments(limit=limit)
    return {"code": 0, "data": {"records": records, "count": len(records)}}


@router.post("/decisions/judgments")
async def save_v4_judgment(request: dict):
    try:
        data = await market_way_v4_service.save_judgment(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"code": 0, "data": data}


@router.post("/decisions/judgments/validate")
async def validate_v4_judgments():
    return {"code": 0, "data": await market_way_v4_service.validate_judgments()}


@router.get("/signals/preview-1040")
async def get_signal_preview_1040():
    rows = await decision_workbench_2026_service.list_snapshots(limit=1, phase="morning_1040")
    return {"code": 0, "data": rows[0] if rows else {"available": False, "message": "10:40快照尚未生成"}}


@router.get("/signals/preview-1455")
async def get_signal_preview_1455():
    rows = await decision_workbench_2026_service.list_snapshots(limit=1, phase="tail_1455")
    return {"code": 0, "data": rows[0] if rows else {"available": False, "message": "14:55快照尚未生成"}}


@router.get("/signals/auction")
async def get_signal_auction():
    from services.overnight_strategy import overnight_strategy_service

    dashboard = await overnight_strategy_service.dashboard()
    return {"code": 0, "data": {
        "auction": dashboard.get("latest_auction_run"),
        "positions": dashboard.get("positions") or [],
        "rule": "竞价量比与高开幅度必须来自09:24-09:27真实PIT快照；不以盘后数据回填。",
    }}


@router.get("/flow/concept/history")
async def get_concept_history(
    board_code: str = Query(...),
    days: int = Query(10, ge=1, le=60),
):
    try:
        board_code = normalize_board_code(board_code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    end_date = shanghai_now().date()
    start_date = end_date - timedelta(days=days)
    async with async_session() as session:
        stmt = (
            select(ConceptFundFlowDaily)
            .where(
                ConceptFundFlowDaily.board_code == board_code,
                ConceptFundFlowDaily.trade_date >= start_date,
                ConceptFundFlowDaily.trade_date <= end_date,
            )
            .order_by(ConceptFundFlowDaily.trade_date.desc())
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    data = []
    for row in rows:
        data.append({
            "date": row.trade_date.isoformat(),
            "main_net_inflow": row.main_net_inflow,
            "change_pct": row.change_pct,
            "super_large_net_inflow": row.super_large_net_inflow,
            "large_net_inflow": row.large_net_inflow,
            "medium_net_inflow": row.medium_net_inflow,
            "small_net_inflow": row.small_net_inflow,
        })
    return {"code": 0, "data": {"history": data, **_market_metadata(available=bool(data), data_date=data[0]["date"] if data else None, is_realtime=False, source="cache")}}


# ── 历史数据查询接口 ──


@router.get("/flow/concept/range")
async def get_concept_range(
    start_date: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end_date: str = Query(..., description="结束日期 YYYY-MM-DD"),
    limit: int = Query(50, ge=1, le=200),
):
    """查询指定日期范围内的概念板块数据"""
    async with async_session() as session:
        stmt = (
            select(ConceptFundFlowDaily)
            .where(
                ConceptFundFlowDaily.trade_date >= date.fromisoformat(start_date),
                ConceptFundFlowDaily.trade_date <= date.fromisoformat(end_date),
            )
            .order_by(ConceptFundFlowDaily.trade_date.desc(), ConceptFundFlowDaily.main_net_inflow.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    from collections import defaultdict
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.trade_date.isoformat()].append({
            "code": row.board_code,
            "trade_date": row.trade_date.isoformat(),
            "close_price": row.close_price,
            "change_pct": row.change_pct,
            "main_net_inflow": row.main_net_inflow,
            "main_net_inflow_pct": row.main_net_inflow_pct,
            "super_large_net_inflow": row.super_large_net_inflow,
            "large_net_inflow": row.large_net_inflow,
            "medium_net_inflow": row.medium_net_inflow,
            "small_net_inflow": row.small_net_inflow,
            "up_count": row.up_count,
            "down_count": row.down_count,
            "leading_stock": row.leading_stock,
        })

    return {"code": 0, "data": {"grouped_by_date": dict(grouped), "total_items": len(rows)}}


@router.get("/flow/concept/dates")
async def get_concept_available_dates():
    """Return only dates with enough rows for a full concept ranking."""
    async with async_session() as session:
        directory_board_count = (await session.execute(
            select(func.count()).select_from(MarketBoard).where(MarketBoard.board_type == "concept")
        )).scalar_one()
        stmt = (
            select(
                ConceptFundFlowDaily.trade_date,
                func.count(func.distinct(ConceptFundFlowDaily.board_code)),
            )
            .group_by(ConceptFundFlowDaily.trade_date)
            .order_by(ConceptFundFlowDaily.trade_date.desc())
            .limit(365)
        )
        result = await session.execute(stmt)
        date_rows = result.all()

    required_board_count = max(1, (directory_board_count * 95 + 99) // 100)
    dates = [
        trade_date.isoformat()
        for trade_date, count in date_rows
        if directory_board_count and count >= required_board_count
    ]
    incomplete_dates = [
        trade_date.isoformat()
        for trade_date, count in date_rows
        if not directory_board_count or count < required_board_count
    ]

    return {
        "code": 0,
        "data": {
            "dates": dates,
            "count": len(dates),
            "incomplete_dates": incomplete_dates,
            "directory_board_count": directory_board_count,
        },
    }


@router.get("/flow/concept/by-date/{target_date}")
async def get_concept_by_date(
    target_date: str,
):
    """查询指定日期的完整概念板块目录。"""
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    if d == shanghai_now().date() and is_a_share_market_session(shanghai_now()):
        rankings = await _realtime_concept_extremes()
        return {"code": 0, "data": {"trade_date": target_date, "rankings": rankings, "rankings_are_complete": False, **_quote_metadata(available=bool(rankings))}}

    rankings = await _concept_history_rankings(d)
    coverage = await _concept_snapshot_coverage(d)
    return {"code": 0, "data": {"trade_date": target_date, "rankings": rankings, "rankings_are_complete": coverage["is_complete"], "coverage": coverage, **_market_metadata(available=bool(rankings), data_date=target_date, is_realtime=False, source="cache")}}


@router.get("/flow/concept/summary")
async def get_concept_summary(
    range: str = Query("today", description="today|yesterday|week|month|3month|year"),
    board_code: Optional[str] = Query(None),
):
    """获取指定时间范围内的概念板块汇总数据"""
    today = shanghai_now().date()
    range_map = {
        "today": (today, today),
        "yesterday": (today - timedelta(days=1), today - timedelta(days=1)),
        "week": (today - timedelta(days=7), today),
        "month": (today - timedelta(days=30), today),
        "3month": (today - timedelta(days=90), today),
        "year": (today - timedelta(days=365), today),
    }
    start, end = range_map.get(range, (today, today))
    if range == "today" and not is_a_share_market_session(shanghai_now()):
        latest = await _latest_cached_trade_date(ConceptFundFlowDaily)
        if latest:
            start = end = latest

    if board_code:
        try:
            board_code = normalize_board_code(board_code)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    # 交易时段读取实时行情；收盘后“今日”回读最近交易日快照。
    if range == "today" and is_a_share_market_session(shanghai_now()):
        result = await _realtime_concept_extremes()
        if result:
            total = sum(r["main_net_inflow"] for r in result)
            return {
                "code": 0,
                "data": {
                    "range": range,
                    "period": {"start": start.isoformat(), "end": end.isoformat()},
                    "rankings": result,
                    "summary": {
                        "total_main_inflow": total,
                        "avg_change_pct": sum(r["change_pct"] for r in result) / len(result) if result else 0,
                        "inflow_board_count": sum(r["main_net_inflow"] > 0 for r in result),
                        "outflow_board_count": sum(r["main_net_inflow"] < 0 for r in result),
                        "shown_board_count": len(result),
                        "rankings_are_complete": False,
                    },
                    "has_data": True,
                    **_quote_metadata(available=True),
                },
            }

    # 从数据库查询历史数据
    async with async_session() as session:
        stmt = select(ConceptFundFlowDaily).where(
            ConceptFundFlowDaily.trade_date >= start,
            ConceptFundFlowDaily.trade_date <= end,
        )
        if board_code:
            stmt = stmt.where(ConceptFundFlowDaily.board_code == board_code)
        stmt = stmt.order_by(ConceptFundFlowDaily.trade_date.desc())
        result = await session.execute(stmt)
        rows = result.scalars().all()

    from collections import defaultdict
    board_aggregates = defaultdict(lambda: {"total_inflow": 0, "days": 0, "total_change": 0.0, "name": ""})
    for row in rows:
        key = row.board_code
        board_aggregates[key]["total_inflow"] += row.main_net_inflow or 0
        board_aggregates[key]["total_change"] += row.change_pct or 0
        board_aggregates[key]["days"] += 1
        board_aggregates[key]["name"] = ""

    # 获取板块名称（实时目录优先，教学目录兼容旧数据）。
    if board_aggregates:
        async with async_session() as session:
            codes = list(board_aggregates.keys())
            market_rows = (await session.execute(
                select(MarketBoard).where(MarketBoard.board_type == "concept", MarketBoard.code.in_(codes))
            )).scalars().all()
            legacy_rows = (await session.execute(select(ConceptBoard).where(ConceptBoard.code.in_(codes)))).scalars().all()
            for board in [*market_rows, *legacy_rows]:
                if board.code in board_aggregates and not board_aggregates[board.code]["name"]:
                    board_aggregates[board.code]["name"] = board.name

    rankings = sorted(
        [{
            "code": code,
            "name": agg["name"] or code,
            "avg_daily_inflow": agg["total_inflow"] // max(agg["days"], 1),
            "total_inflow": agg["total_inflow"],
            "avg_change_pct": round(agg["total_change"] / max(agg["days"], 1), 2),
            "days_count": agg["days"],
        } for code, agg in board_aggregates.items()],
        key=lambda x: x["total_inflow"],
        reverse=True,
    )

    total_inflow = sum(r["total_inflow"] for r in rankings)

    return {
        "code": 0,
        "data": {
            "range": range,
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "rankings": rankings,
            "summary": {
                "total_main_inflow": total_inflow,
                "board_count": len(rankings),
                # The date-level query above exposes complete daily rankings.
                # A multi-day aggregate remains conservative until every
                # historical session is independently verified.
                "rankings_are_complete": False,
            },
            "has_data": len(rows) > 0,
            **_market_metadata(available=bool(rows), data_date=end.isoformat() if rows else None, is_realtime=False, source="cache"),
        },
    }


@router.post("/flow/concept/generate-history")
async def generate_history(days: int = Query(365, ge=1, le=365), include_stock_bars: bool = True):
    """兼容旧入口：改为排队真实的一年期历史回补。"""
    result = await history_cache.queue_backfill(days=days, include_stock_bars=include_stock_bars)
    return {"code": 0, "data": result, "message": "真实历史数据回补任务已启动"}


@router.post("/flow/concept/archive")
async def archive_today():
    """手动归档今日数据"""
    result = await history_cache.cache_current_concept_flow()
    return {"code": 0, "data": result, "message": "已同步真实概念板块数据"}


# ── 新手学堂接口 ──


@router.get("/learn/terms")
async def get_terms(
    category: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    async with async_session() as session:
        stmt = select(KnowledgeTerm)
        if category:
            stmt = stmt.where(KnowledgeTerm.category == category)
        total_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(total_stmt)).scalar() or 0
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await session.execute(stmt)
        rows = result.scalars().all()

    terms = []
    for row in rows:
        terms.append({
            "id": row.id,
            "term": row.term,
            "category": row.category,
            "simple_explanation": row.simple_explanation,
            "professional_explanation": row.professional_explanation,
            "usage_guide": row.usage_guide,
            "related_terms": row.related_terms or [],
            "difficulty_level": row.difficulty_level,
        })

    return {
        "code": 0,
        "data": terms,
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@router.get("/learn/terms/{term_id}")
async def get_term_detail(term_id: int):
    async with async_session() as session:
        stmt = select(KnowledgeTerm).where(KnowledgeTerm.id == term_id)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="术语不存在")

    return {
        "code": 0,
        "data": {
            "id": row.id,
            "term": row.term,
            "category": row.category,
            "simple_explanation": row.simple_explanation,
            "professional_explanation": row.professional_explanation,
            "usage_guide": row.usage_guide,
            "related_terms": row.related_terms or [],
            "examples": row.examples or [],
            "difficulty_level": row.difficulty_level,
        },
    }


@router.get("/learn/cases")
async def get_cases(
    category: Optional[str] = None,
    difficulty: Optional[int] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
):
    async with async_session() as session:
        stmt = select(LearningCase)
        if category:
            stmt = stmt.where(LearningCase.category == category)
        if difficulty:
            stmt = stmt.where(LearningCase.difficulty_level == difficulty)
        total_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(total_stmt)).scalar() or 0
        stmt = stmt.order_by(LearningCase.event_date.desc()).offset((page - 1) * page_size).limit(page_size)
        result = await session.execute(stmt)
        rows = result.scalars().all()

    cases = []
    for row in rows:
        cases.append({
            "id": row.id,
            "title": row.title,
            "summary": row.summary,
            "event_date": row.event_date.isoformat() if row.event_date else None,
            "category": row.category,
            "difficulty_level": row.difficulty_level,
            "steps": row.steps or [],
            "quiz": row.quiz or {},
            "related_terms": row.related_terms or [],
            "key_learnings": row.key_learnings or [],
            "view_count": row.view_count,
        })

    return {
        "code": 0,
        "data": cases,
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@router.get("/learn/cases/{case_id}")
async def get_case_detail(case_id: int):
    async with async_session() as session:
        stmt = select(LearningCase).where(LearningCase.id == case_id)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="案例不存在")

    return {
        "code": 0,
        "data": {
            "id": row.id,
            "title": row.title,
            "summary": row.summary,
            "event_date": row.event_date.isoformat() if row.event_date else None,
            "category": row.category,
            "difficulty_level": row.difficulty_level,
            "steps": row.steps or [],
            "quiz": row.quiz or {},
            "related_terms": row.related_terms or [],
            "key_learnings": row.key_learnings or [],
            "view_count": row.view_count,
        },
    }


@router.get("/learn/board/{board_code}")
async def get_board_encyclopedia(board_code: str):
    async with async_session() as session:
        stmt = select(ConceptBoard).where(ConceptBoard.code == board_code)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="板块不存在")

    return {
        "code": 0,
        "data": {
            "code": row.code,
            "name": row.name,
            "description": row.description,
            "one_liner": row.one_liner,
            "simple_explanation": row.simple_explanation,
            "industry_chain": row.industry_chain or {},
            "key_companies": row.key_companies or [],
            "leading_stocks": row.leading_stocks or [],
            "stock_count": row.stock_count,
            "triggers": row.triggers or [],
            "beginner_tip": row.beginner_tip,
            "related_reading": row.related_reading or [],
        },
    }


# ── AI 助手接口 ──


@router.post("/ai/mao-strategy/analyze")
async def analyze_mao_strategy(request: dict):
    message = str(request.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="消息不能为空")
    if len(message) > 4000:
        raise HTTPException(status_code=422, detail="单条消息不能超过4000字")
    report = await mao_strategy_agent.analyze(message)
    return {"code": 0, "data": report}


@router.post("/ai/chat")
async def ai_chat(request: dict):
    user_id = ai_assistant_service.normalize_user_id(request.get("user_id", "web_user"))
    message = str(request.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="消息不能为空")
    if len(message) > 4000:
        raise HTTPException(status_code=422, detail="单条消息不能超过4000字")
    context = request.get("context") if isinstance(request.get("context"), dict) else {}
    raw_mode = str(context.get("mode") or "beginner").strip().lower()
    mode_aliases = {"strategy": "mao_strategy", "mao": "mao_strategy"}
    mode = mode_aliases.get(raw_mode, raw_mode)
    if mode not in {"beginner", "professional", "mao_strategy"}:
        mode = "professional"

    if mode == "mao_strategy":
        async def generate_strategy():
            yield f"data: {json.dumps({'type': 'start', 'message_id': f'msg_{datetime.now().timestamp()}', 'mode': mode, 'sources': []}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'progress', 'progress': 8, 'label': '正在识别标的与对话上下文'}, ensure_ascii=False)}\n\n"
            try:
                history = await ai_assistant_service.history(user_id, MAX_HISTORY_MESSAGES)
                await ai_assistant_service.save_message(user_id, "user", message, mode)
            except Exception as exc:
                print(f"AI strategy history load failed: {type(exc).__name__}")
                history = []
            try:
                yield f"data: {json.dumps({'type': 'progress', 'progress': 22, 'label': '正在读取实时行情与最近有效缓存'}, ensure_ascii=False)}\n\n"
                report = await mao_strategy_agent.analyze(message)
                sources = [
                    str(item.get("name"))
                    for item in report.get("data_audit", {}).get("sources", [])
                    if item.get("available") and item.get("name")
                ]
                yield f"data: {json.dumps({'type': 'progress', 'progress': 78, 'label': '正在审计主要矛盾、阵营与周期'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'strategy_report', 'report': report, 'sources': sources, 'history_messages': len(history)}, ensure_ascii=False, default=str)}\n\n"
                content = mao_strategy_agent.render_report(report)
                yield f"data: {json.dumps({'type': 'text', 'content': content}, ensure_ascii=False)}\n\n"
                try:
                    await ai_assistant_service.save_message(user_id, "assistant", content, mode)
                except Exception as exc:
                    print(f"AI strategy history save failed: {type(exc).__name__}")
                yield f"data: {json.dumps({'type': 'progress', 'progress': 100, 'label': '战略报告已完成'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'end', 'content': content}, ensure_ascii=False)}\n\n"
            except Exception as exc:
                print(f"Mao strategy analysis failed: {type(exc).__name__}")
                yield f"data: {json.dumps({'type': 'error', 'content': f'战略研判失败：{type(exc).__name__}'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            generate_strategy(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    is_beginner = mode == "beginner"
    system_prompt = BEGINNER_SYSTEM_PROMPT if is_beginner else PROFESSIONAL_SYSTEM_PROMPT
    try:
        history = await ai_assistant_service.history(user_id, MAX_HISTORY_MESSAGES)
        await ai_assistant_service.save_message(user_id, "user", message, mode)
    except Exception as exc:
        print(f"AI history load failed: {type(exc).__name__}")
        history = []
    try:
        data_context = await ai_assistant_service.build_context(message)
    except Exception as exc:
        print(f"AI context build failed: {type(exc).__name__}")
        data_context = {
            "available": False,
            "sources": [],
            "generated_at": shanghai_now().isoformat(),
            "error": "数据检索暂时不可用",
        }
    grounded_prompt = (
        system_prompt
        + "\n\n你可以使用系统提供的 DATA_CONTEXT 回答实时或历史数据问题。严格遵守："
        "1. 只把 DATA_CONTEXT 中存在的数据当作事实；2. 明确区分实时、收盘缓存和历史数据；"
        "3. 回答具体数值时说明数据日期与来源；4. 数据缺失时直接说明，不猜测、不补造；"
        "5. 不承诺收益，不把评分、龙虎榜或资金流单独解释为必涨；6. 结合此前对话保持上下文连续。"
        "\nDATA_CONTEXT:\n"
        + json.dumps(data_context, ensure_ascii=False, default=str)[:24000]
    )

    async def generate():
        start = {
            "type": "start",
            "message_id": f"msg_{datetime.now().timestamp()}",
            "sources": data_context.get("sources") or [],
            "history_messages": len(history),
        }
        yield f"data: {json.dumps(start, ensure_ascii=False)}\n\n"
        full_content = ""
        async for chunk in ai_service.chat_stream(
            message=message,
            system_prompt=grounded_prompt,
            user_id=user_id,
            history=history,
        ):
            if chunk.get("type") == "text":
                full_content += str(chunk.get("content") or "")
            elif chunk.get("type") == "end" and not full_content:
                full_content = str(chunk.get("content") or "")
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        if full_content.strip():
            try:
                await ai_assistant_service.save_message(user_id, "assistant", full_content, mode)
            except Exception as exc:
                print(f"AI history save failed: {type(exc).__name__}")
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/ai/history")
async def get_ai_history(
    user_id: str = Query("web_user"),
    limit: int = Query(MAX_HISTORY_MESSAGES, ge=1, le=MAX_HISTORY_MESSAGES),
):
    messages = await ai_assistant_service.history(user_id, limit)
    return {"code": 0, "data": {"messages": messages, "count": len(messages)}}


@router.delete("/ai/history")
async def clear_ai_history(user_id: str = Query("web_user")):
    deleted = await ai_assistant_service.clear_history(user_id)
    return {"code": 0, "data": {"deleted": deleted}, "message": "对话记录已清空"}


@router.post("/ai/daily-report")
async def generate_daily_report(request: dict):
    trade_date = request.get("date", date.today().isoformat())
    concept_data = await collector.fetch_concept_flow(page_size=10)
    market_summary = await collector.fetch_market_summary()

    top_inflow = [d for d in concept_data if int(float(d.get("main_net_inflow", 0))) > 0][:3]
    top_outflow = sorted(concept_data, key=lambda x: int(float(x.get("main_net_inflow", 0))))[:3]

    data_json = json.dumps({
        "trade_date": trade_date,
        "market_summary": market_summary,
        "top_inflow": [{"name": d["name"], "main_net_inflow": d["main_net_inflow"], "change_pct": d["change_pct"]} for d in top_inflow],
        "top_outflow": [{"name": d["name"], "main_net_inflow": d["main_net_inflow"], "change_pct": d["change_pct"]} for d in top_outflow],
    }, ensure_ascii=False)

    prompt = DAILY_REPORT_PROMPT_TEMPLATE.format(data_json=data_json)
    report = await ai_service.generate(prompt)
    report = report.replace("###", "").replace("**", "").replace("##", "").strip()
    return {"code": 0, "data": {"date": trade_date, "report": report}}


# ── 数据同步 + 缓存 ──

from services.data_sync import data_sync
from quant.jobs import create_job, get_job, latest_running_job, spawn, update_job


async def _run_data_sync_job(job_id: str) -> None:
    update_job(
        "market_sync",
        job_id,
        status="running",
        phase="starting",
        progress=2,
        message="正在启动市场数据同步",
        started_at=shanghai_now().isoformat(),
    )

    def report(progress: int, phase: str, message: str) -> None:
        update_job(
            "market_sync",
            job_id,
            progress=progress,
            phase=phase,
            message=message,
        )

    try:
        result = await data_sync.sync_market_snapshot(progress=report)
        overview_response = await refresh_market_overview_after_sync(result)
        overview = overview_response.get("data") or {}
        overview_summary = {
            "refresh_status": overview.get("refresh_status"),
            "snapshot_status": overview.get("snapshot_status"),
            "data_date": overview.get("data_date"),
            "source_updated_at": overview.get("source_updated_at"),
            "snapshot_saved_at": overview.get("snapshot_saved_at"),
            "is_realtime": bool(overview.get("is_realtime")),
            "cache_used": bool(overview.get("cache_used")),
            "warnings": overview.get("data_warnings") or [],
        }
        result["overview"] = overview_summary
        refreshed = overview_summary["refresh_status"] == "updated"
        data_date = overview_summary.get("data_date") or result.get("data_date") or "未知日期"
        update_job(
            "market_sync",
            job_id,
            status="completed",
            phase="completed",
            progress=100,
            message=(
                f"市场速览已更新至 {data_date}"
                if refreshed
                else f"基础数据已同步；速览快照仍保留在 {data_date}，请查看数据源提示"
            ),
            result=result,
            completed_at=shanghai_now().isoformat(),
        )
    except Exception as exc:
        update_job(
            "market_sync",
            job_id,
            status="failed",
            phase="failed",
            progress=100,
            message="市场数据同步失败",
            error=f"{type(exc).__name__}: {exc}"[:500],
            completed_at=shanghai_now().isoformat(),
        )


@router.post("/data/sync", status_code=202)
async def trigger_data_sync(force: bool = False):
    """Queue a full verified snapshot without holding the browser request open."""
    running = latest_running_job("market_sync")
    if running:
        return {"code": 0, "data": running, "message": "已有市场同步任务正在运行"}
    job = create_job("market_sync", "market_sync", {"force": bool(force)})
    spawn(_run_data_sync_job(job["job_id"]))
    return {"code": 0, "data": job, "message": "市场数据同步任务已提交"}


@router.get("/data/sync/status/{job_id}")
async def get_data_sync_status(job_id: str):
    job = get_job("market_sync", job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="市场同步任务不存在或已过期")
    return {"code": 0, "data": job}


@router.get("/data/cache-stats")
async def get_cache_stats():
    """获取数据缓存统计"""
    stats = await data_sync.get_cache_stats()
    return {"code": 0, "data": stats}


@router.post("/data/backfill")
async def start_history_backfill(
    days: int = Query(365, ge=1, le=365),
    include_stock_bars: bool = Query(True),
    max_stocks: Optional[int] = Query(None, ge=1, le=8000),
):
    """异步回补系统使用的真实历史数据。"""
    result = await history_cache.queue_backfill(
        days=days,
        include_stock_bars=include_stock_bars,
        max_stocks=max_stocks,
    )
    return {"code": 0, "data": result}


@router.get("/data/backfill/{run_id}")
async def get_history_backfill(run_id: int):
    run = await history_cache.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="回补任务不存在")
    return {"code": 0, "data": run}


@router.get("/data/stock/{stock_code}/history")
async def get_cached_stock_history(stock_code: str, days: int = Query(365, ge=1, le=365)):
    try:
        code = normalize_stock_code(stock_code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    history = await history_cache.get_stock_history(code, days)
    return {
        "code": 0,
        "data": {
            "stock_code": code,
            "history": history,
            **_market_metadata(
                available=bool(history),
                data_date=history[-1]["date"] if history else None,
                is_realtime=False,
                source="cache",
            ),
        },
    }


@router.post("/data/sync-local")
async def sync_local_data(request: dict):
    del request
    raise HTTPException(status_code=410, detail="任意数据写入已禁用；请使用 /data/backfill 或 /data/sync")


@router.post("/data/push")
async def push_local_data(request: dict):
    del request
    raise HTTPException(status_code=410, detail="任意数据写入已禁用；请使用 /data/backfill 或 /data/sync")


# ── AI模拟炒股接口 ──

from sim_models import SimAccount, SimPosition, SimTradeRecord, SimDailySummary
from services.trading_engine import trading_engine


@router.get("/sim/account")
async def get_sim_account():
    """获取模拟账户概览"""
    async with async_session() as session:
        account = await session.get(SimAccount, 1)
        if not account:
            return {"code": 0, "data": {"exists": False}}

        stmt = select(SimPosition).where(SimPosition.account_id == 1, SimPosition.shares > 0)
        result = await session.execute(stmt)
        positions = result.scalars().all()

        positions_list = []
        for p in positions:
            positions_list.append({
                "stock_code": p.stock_code,
                "stock_name": p.stock_name,
                "shares": p.shares,
                "avg_cost": p.avg_cost,
                "current_price": p.current_price,
                "market_value": p.market_value,
                "pnl": p.pnl,
                "pnl_pct": p.pnl_pct,
                "hold_days": p.hold_days,
            })

        return {
            "code": 0,
            "data": {
                "exists": True,
                "name": account.name,
                "initial_capital": account.initial_capital,
                "cash": account.cash,
                "total_value": account.total_value,
                "daily_pnl": account.daily_pnl,
                "total_pnl": account.total_pnl,
                "total_pnl_pct": account.total_pnl_pct,
                "trade_count": account.trade_count,
                "win_count": account.win_count,
                "positions": positions_list,
                "positions_count": len(positions_list),
            },
        }


@router.get("/sim/trades")
async def get_sim_trades(days: int = Query(10, ge=1, le=60)):
    """获取交易记录"""
    since = date.today() - timedelta(days=days)
    async with async_session() as session:
        stmt = (
            select(SimTradeRecord)
            .where(
                SimTradeRecord.account_id == 1,
                SimTradeRecord.trade_date >= since,
            )
            .order_by(SimTradeRecord.traded_at.desc())
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    trades = []
    for r in rows:
        trades.append({
            "id": r.id,
            "trade_type": r.trade_type,
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "shares": r.shares,
            "price": r.price,
            "amount": r.amount,
            "pnl": r.pnl,
            "pnl_pct": r.pnl_pct,
            "ai_reason": r.ai_reason,
            "ai_score": r.ai_score,
            "trade_date": r.trade_date.isoformat() if r.trade_date else "",
            "traded_at": r.traded_at.isoformat() if r.traded_at else "",
        })
    return {"code": 0, "data": trades}


@router.get("/sim/daily-summary")
async def get_sim_daily_summary(days: int = Query(30, ge=1, le=365)):
    """获取每日收益摘要（用于收益曲线）"""
    since = date.today() - timedelta(days=days)
    async with async_session() as session:
        stmt = (
            select(SimDailySummary)
            .where(
                SimDailySummary.account_id == 1,
                SimDailySummary.summary_date >= since,
            )
            .order_by(SimDailySummary.summary_date.asc())
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    daily = []
    cumulative_pnl = 0
    for r in rows:
        cumulative_pnl += r.daily_pnl or 0
        daily.append({
            "date": r.summary_date.isoformat(),
            "daily_pnl": r.daily_pnl,
            "daily_pnl_pct": r.daily_pnl_pct,
            "total_value": r.total_value,
            "cumulative_pnl": cumulative_pnl,
            "trade_count": r.trade_count,
            "positions_count": r.positions_count,
            "top_gainer": r.top_gainer,
            "top_loser": r.top_loser,
            "ai_summary": r.ai_summary,
        })
    return {"code": 0, "data": daily}


@router.post("/sim/execute-trading")
async def execute_sim_trading(request: dict = None):
    """手动触发AI交易（多策略并行）"""
    dry_run = request.get("dry_run", False) if request else False
    all_strategies = request.get("all_strategies", True) if request else True

    if all_strategies:
        result = await trading_engine.execute_all_strategies(dry_run=dry_run)
    else:
        result = await trading_engine.execute_daily_trading(dry_run=dry_run)
    return {"code": 0, "data": result}


# ── 量化评分接口 ──

from services.quant_scorer import enhanced_scorer, DynamicWeights, MarketRegime, RiskParity
from services.quant_research import quant_research_engine


@router.get("/quant/score-board")
async def get_quant_score_board():
    """增强版量化评分：含动态权重 + 市场状态识别"""
    regime_info = await enhanced_scorer.update_regime()
    weights = DynamicWeights.get_weights(regime_info["regime"])
    weight_explanation = DynamicWeights.explain(regime_info["regime"])

    tech_data = await collector.fetch_technical_screener({"min_change": 1, "max_pe": 200, "min_turnover": 1})
    stocks = tech_data.get("stocks", [])

    concepts = await collector.fetch_concept_flow(page_size=30)
    sector_map = {}
    for c in (concepts or []):
        sector_map[c.get("name", "")] = {
            "change_pct": float(c.get("change_pct", 0) or 0),
            "flow_yi": int(float(c.get("main_net_inflow", 0) or 0) / 1e8),
        }

    scored = []
    for s in stocks[:30]:
        name = s.get("name", "")
        sector_info = {"change_pct": 0, "flow_yi": 0}
        for sector_name, info in sector_map.items():
            if sector_name in name or any(kw in name for kw in [sector_name[:2]]):
                sector_info = info
                break

        score = enhanced_scorer.compute(
            s,
            sector_change=sector_info["change_pct"],
            sector_flow=sector_info["flow_yi"],
            weights=weights,
        )

        scored.append({
            "code": s.get("code", ""),
            "name": name,
            "price": s.get("price", ""),
            "change_pct": s.get("change_pct", ""),
            "turnover": s.get("turnover", ""),
            "pe": s.get("pe", ""),
            "volume_ratio": s.get("volume_ratio", ""),
            "main_inflow_yi": round(as_int(s.get("main_net_inflow")) / 1e8, 2),
            "quant_score": score["composite_score"],
            "grade": score["grade"],
            "grade_label": score["grade_label"],
            "factor_detail": score["factors"],
            "weights": weights,
            "regime": score["regime"],
        })

    scored.sort(key=lambda x: x["quant_score"], reverse=True)

    # 风险平价分配
    if scored:
        top_picks = [s for s in scored[:15] if s["grade"] in ("S", "A", "B")]
        if len(top_picks) >= 5:
            top_picks = top_picks[:8]
        risk_allocated = RiskParity.allocate(top_picks, 400000)

        for ra in risk_allocated:
            for s in scored:
                if s["code"] == ra.get("code"):
                    s["risk_parity"] = {
                        "weight": ra.get("risk_parity_weight"),
                        "volatility": ra.get("volatility_proxy"),
                        "allocated": ra.get("allocated_capital"),
                        "shares": ra.get("suggested_shares"),
                    }
                    break

    return {
        "code": 0,
        "data": {
            "available": bool(stocks),
            "stocks": scored,
            "regime": regime_info,
            "weights": {k: f"{v*100:.0f}%" for k, v in weights.items()},
            "weight_explanation": weight_explanation,
            "summary": {
                "top_grade": scored[0]["grade"] if scored else "N/A",
                "avg_score": round(sum(s["quant_score"] for s in scored) / len(scored), 1) if scored else 0,
                "s_recommend": sum(1 for s in scored if s["grade"] == "S"),
                "a_recommend": sum(1 for s in scored if s["grade"] == "A"),
            },
        },
    }


@router.get("/quant/research-backtest")
async def get_quant_research_backtest(
    days: int = Query(365, ge=30, le=730),
    top_n: int = Query(10, ge=1, le=50),
    lookback_days: int = Query(20, ge=10, le=120),
    holding_days: int = Query(5, ge=1, le=20),
    capital: float = Query(400000, ge=10000, le=100000000),
):
    """Run the auditable point-in-time stock daily-bar research experiment."""
    result = await quant_research_engine.run(
        days=days,
        top_n=top_n,
        lookback_days=lookback_days,
        holding_days=holding_days,
        capital=capital,
    )
    return {"code": 0, "data": result}


@router.get("/quant/backtest")
async def get_backtest(
    days: int = Query(365, ge=30, le=730),
    top_n: int = Query(10, ge=1, le=50),
    lookback_days: int = Query(20, ge=10, le=120),
    holding_days: int = Query(5, ge=1, le=20),
):
    """Backward-compatible alias for the point-in-time research backtest."""
    result = await quant_research_engine.run(
        days=days,
        top_n=top_n,
        lookback_days=lookback_days,
        holding_days=holding_days,
    )
    return {"code": 0, "data": result}


@router.get("/quant/regime")
async def get_market_regime():
    """获取当前市场状态"""
    info = await MarketRegime.detect()
    weights = DynamicWeights.get_weights(info["regime"])
    return {
        "code": 0,
        "data": {
            "regime": info,
            "dynamic_weights": {k: f"{v*100:.0f}%" for k, v in weights.items()},
            "explanation": DynamicWeights.explain(info["regime"]),
        },
    }


router_quant = router  # keep existing router reference


@router.post("/sim/execute-trading")


@router.post("/sim/reset")
async def reset_sim_account():
    """重置模拟账户"""
    async with async_session() as session:
        for model in [SimTradeRecord, SimPosition, SimDailySummary]:
            await session.execute(text(f"DELETE FROM {model.__tablename__}"))
        account = await session.get(SimAccount, 1)
        if account:
            account.cash = account.initial_capital
            account.total_value = account.initial_capital
            account.daily_pnl = 0
            account.total_pnl = 0
            account.total_pnl_pct = 0
            account.trade_count = 0
            account.win_count = 0
        await session.commit()
    return {"code": 0, "message": "账户已重置"}


# ── 多策略 + 风控 + 归因 + 基准对比 ──

from services.risk_analysis import StrategyProfile, RiskMetrics, Attribution, Benchmark


@router.get("/sim/strategies")
async def get_strategies():
    """获取所有策略配置"""
    return {"code": 0, "data": StrategyProfile.STRATEGIES}


@router.get("/sim/risk-metrics/{account_id}")
async def get_risk_metrics(account_id: int = 1, days: int = 30):
    """获取风控指标"""
    metrics = await RiskMetrics.calculate(account_id, days=days)
    return {"code": 0, "data": metrics}


@router.get("/sim/attribution/{account_id}")
async def get_attribution(account_id: int = 1):
    """收益归因分析"""
    attr = await Attribution.analyze(account_id)
    return {"code": 0, "data": attr}


@router.get("/sim/benchmark")
async def get_benchmark(days: int = 30):
    """获取大盘基准数据"""
    bench = await Benchmark.get_benchmark_data(days=days)
    return {"code": 0, "data": bench}


@router.get("/sim/compare")
async def compare_strategies():
    """三策略收益对比"""
    result = {}
    for key, strategy in StrategyProfile.STRATEGIES.items():
        aid = strategy["account_id"]
        try:
            async with async_session() as session:
                stmt = (
                    select(SimDailySummary)
                    .where(SimDailySummary.account_id == aid)
                    .order_by(SimDailySummary.summary_date.asc())
                )
                db_result = await session.execute(stmt)
                daily = db_result.scalars().all()

            account = None
            async with async_session() as session:
                account = await session.get(SimAccount, aid)

            if not daily and not account:
                continue

            values = []
            cumulative = 0
            for d in daily:
                cumulative += d.daily_pnl or 0
                values.append({
                    "date": d.summary_date.isoformat(),
                    "total_value": d.total_value or account.initial_capital if account else 1000000,
                    "daily_pnl": d.daily_pnl,
                    "cumulative_pnl": cumulative,
                })

            result[key] = {
                "name": strategy["name"],
                "account_id": aid,
                "description": strategy["description"],
                "total_value": account.total_value if account else 1000000,
                "total_pnl": account.total_pnl if account else 0,
                "total_pnl_pct": account.total_pnl_pct if account else 0,
                "trade_count": account.trade_count if account else 0,
                "positions_count": sum(1 for d in daily if d.positions_count > 0) if daily else 0,
                "daily_data": values,
            }
        except Exception as e:
            result[key] = {"name": strategy["name"], "error": str(e), "account_id": aid}

    return {"code": 0, "data": result}


@router.get("/sim/ai-daily-report")
async def get_ai_trading_report():
    """AI生成今日交易总结"""
    async with async_session() as session:
        # 获取今日交易
        today = date.today()
        stmt = (
            select(SimTradeRecord)
            .where(SimTradeRecord.trade_date == today, SimTradeRecord.account_id == 1)
            .order_by(SimTradeRecord.traded_at.desc())
        )
        result = await session.execute(stmt)
        trades = result.scalars().all()

        account = await session.get(SimAccount, 1)

    if not account:
        return {"code": 0, "data": {"report": "账户未初始化，请先执行AI交易。"}}

    trade_summary = ""
    for t in trades[:10]:
        trade_summary += f"- {t.trade_type} {t.stock_name}({t.stock_code}): {t.shares}股 @ ¥{t.price:.2f}, "
        if t.pnl:
            trade_summary += f"盈亏{t.pnl/1e4:+.2f}万, "
        if t.ai_reason:
            trade_summary += f"理由: {t.ai_reason[:50]}\n"
        else:
            trade_summary += "\n"

    prompt = f"""请为AI量化交易系统生成一份简洁的每日交易报告。

## 账户概况
- 总资产: {account.total_value/1e4:.1f}万
- 今日盈亏: {account.daily_pnl/1e4:+.2f}万
- 累计收益: {account.total_pnl/1e4:+.2f}万 ({account.total_pnl_pct:+.2f}%)
- 累计交易: {account.trade_count}笔, 胜率: {account.win_count}/{max(account.trade_count,1)}

## 今日操作
{trade_summary or '今日无操作'}

    请输出格式（纯文本，不要用markdown标记，100字以内）：
    今日总结：[今日AI操作总结+收益说明]
    明日计划：[1-2句明日关注方向]"""

    report = await ai_service.generate(prompt)
    # 清除markdown标记
    report = report.replace("###", "").replace("**", "").replace("##", "").strip()
    return {"code": 0, "data": {"date": today.isoformat(), "report": report}}
