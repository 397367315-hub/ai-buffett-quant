"""ROCI V1.1.2 intraday market reasoning sidecar.

The service is deliberately read-only.  It combines source-labelled index
quotes, minute bars and the latest verified breadth snapshot.  When a source
is stale or missing, the state is downgraded instead of being presented as
live market data.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select

from database import async_session
from models import MarketDataCache, MarketSentimentDaily, RociIntradaySnapshot
from services.data_collector import collector, is_a_share_market_session, shanghai_now
from services.market_decision_workbench import market_decision_workbench_service

from .explanation import build_explanation
from .intraday_skills import build_shadow_skill_outputs


INTRADAY_CACHE_KEY = "roci_intraday_current_v1_1_2"
INTRADAY_VERSION = "roci-intraday-v1.1.2"


def _fact(
    claim: str,
    value: Any,
    *,
    source: str,
    field: str | None = None,
    strength: float | None = None,
    supports: bool = True,
    evidence_type: str = "FACT",
    timestamp: Any = None,
) -> dict[str, Any]:
    """Build a local, serializable evidence record for intraday explanations."""
    if isinstance(timestamp, (datetime, date)):
        timestamp = timestamp.isoformat()
    return {
        "type": evidence_type,
        "claim": claim,
        "value": value,
        "source_table": source,
        "source_field": field,
        "evidence_strength": strength,
        "supports": supports,
        "source_timestamp": timestamp,
    }


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _has_index_quotes(indexes: dict[str, dict[str, Any]]) -> bool:
    return any(_num((item or {}).get("value")) is not None for item in indexes.values())


def _has_breadth(breadth: dict[str, Any]) -> bool:
    return _num(breadth.get("up_count")) is not None or _num(breadth.get("down_count")) is not None


def _has_turnover(turnover: dict[str, Any]) -> bool:
    return _num(turnover.get("sh_amount") or turnover.get("market_amount")) is not None


def _minute_structure(index_minute: dict[str, Any], now: datetime) -> tuple[dict[str, Any], str | None]:
    """Derive transparent, index-only opening and absorption proxies."""
    bars = [item for item in (index_minute or {}).get("bars") or [] if isinstance(item, dict)]
    parsed: list[tuple[datetime, dict[str, Any]]] = []
    for item in bars:
        stamp = _dt(item.get("bar_time"))
        if stamp is not None:
            parsed.append((stamp, item))
    if not parsed:
        return {}, None
    parsed.sort(key=lambda pair: pair[0])
    session_bars = [item for stamp, item in parsed if stamp.date() == now.date()]
    opening_bars = [item for stamp, item in parsed if stamp.date() == now.date() and stamp.hour == 9 and 30 <= stamp.minute < 45]
    opening: dict[str, Any] = {}
    if len(opening_bars) >= 3:
        first = _num(opening_bars[0].get("open"))
        last = _num(opening_bars[-1].get("close"))
        change = (last / first - 1) * 100 if first and last else None
        opening = {
            "state": "OPENING_ATTACK" if change is not None and change > .35 else "OPENING_PANIC" if change is not None and change < -.35 else "OPENING_NOISE",
            "bar_count": len(opening_bars),
            "change_pct": round(change, 4) if change is not None else None,
            "source": "shanghai_index_minute",
        }
    absorption: str | None = None
    if len(session_bars) >= 3:
        closes = [_num(item.get("close")) for item in session_bars]
        closes = [item for item in closes if item is not None]
        if len(closes) >= 3:
            low, high, latest = min(closes), max(closes), closes[-1]
            span = high - low
            position = (latest - low) / span if span > 0 else .5
            absorption = "BUY_ABSORPTION_DOMINANT" if position >= .72 else "SELL_PRESSURE_DOMINANT" if position <= .28 else "BALANCED"
    return opening, absorption


def classify_data_status(
    indexes: dict[str, dict[str, Any]],
    *,
    index_realtime: bool,
    turnover: dict[str, Any],
    breadth: dict[str, Any],
    breadth_status: str,
) -> str:
    """Classify the freshness of the critical intraday inputs.

    A live index quote cannot upgrade a prior-day breadth snapshot.  The
    dashboard therefore distinguishes a complete live set from a partial set
    and never labels an incomplete set as REALTIME.
    """
    available = (_has_index_quotes(indexes), _has_turnover(turnover), _has_breadth(breadth))
    if not all(available):
        return "INSUFFICIENT_DATA"
    live_components = (bool(index_realtime), bool(turnover.get("is_realtime")), breadth_status == "OBSERVED")
    if all(live_components):
        return "REALTIME"
    if any(live_components):
        return "PARTIAL_REALTIME"
    return "CACHED"


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


async def _safe(awaitable: Any, fallback: Any, timeout: float = 15.0) -> Any:
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except Exception as exc:
        return {"status": "UNAVAILABLE", "error": type(exc).__name__, **fallback} if isinstance(fallback, dict) else fallback


def _state_from_inputs(indexes: dict[str, dict], breadth: dict[str, Any]) -> tuple[str, str, float | None]:
    sh = _num((indexes.get("shanghai") or {}).get("change_pct"))
    cyb = _num((indexes.get("chinext") or {}).get("change_pct"))
    hs = _num((indexes.get("hs300") or {}).get("change_pct"))
    up = _num(breadth.get("up_count"))
    down = _num(breadth.get("down_count"))
    breadth_ratio = up / (up + down) * 100 if up is not None and down is not None and up + down else None
    if cyb is None and hs is None and breadth_ratio is None:
        return "INSUFFICIENT_DATA", "UNKNOWN", None
    if breadth_ratio is not None and breadth_ratio < 30 and (sh is None or sh < 0) and (hs is None or hs < 0):
        return "BROAD_RISK_OFF", "BREADTH_COLLAPSING", breadth_ratio
    if cyb is not None and hs is not None and cyb < hs - 0.8 and (breadth_ratio is None or breadth_ratio < 48):
        return "DEFENSIVE_ROTATION", "BREADTH_WEAKENING", breadth_ratio
    if cyb is not None and hs is not None and cyb > hs + 0.8 and (breadth_ratio is None or breadth_ratio >= 55):
        return "RISK_ON_RECLAIM", "BREADTH_EXPANDING", breadth_ratio
    if breadth_ratio is not None and breadth_ratio >= 55:
        return "BROAD_RISK_ON", "BREADTH_STABLE", breadth_ratio
    return "MIXED", "BREADTH_DIVERGING" if breadth_ratio is not None else "UNKNOWN", breadth_ratio


def _volume_state(turnover: dict[str, Any], breadth_ratio: float | None, previous_amount: Any) -> tuple[str, float | None]:
    amount = _num(turnover.get("sh_amount") or turnover.get("market_amount"))
    prior = _num(previous_amount)
    if amount is None:
        return "UNKNOWN", None
    change = (amount / prior - 1) * 100 if prior and prior > 0 else None
    if change is not None and change >= 8 and breadth_ratio is not None and breadth_ratio < 48:
        return "MIGRATION_VOLUME", change
    if change is not None and change >= 8 and (breadth_ratio is None or breadth_ratio >= 55):
        return "ATTACK_VOLUME", change
    if change is not None and change <= -12:
        return "VOLUME_DRYUP", change
    return "BALANCED_VOLUME", change


def _leadership(workbench: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    rows = [item for item in (workbench.get("main_lines") or []) if isinstance(item, dict) and item.get("name")]
    if not rows:
        return "NO_LEADERSHIP", []
    selected = []
    for row in rows[:5]:
        selected.append({
            "name": row.get("name"),
            "strength": row.get("strength_score"),
            "breadth": row.get("breadth"),
            "flow": row.get("main_net_inflow"),
            "lifecycle": row.get("lifecycle"),
            "leader": row.get("leader"),
            "source": "market_decision_workbench.main_lines",
        })
    top_breadths = [_num(item.get("breadth")) for item in selected]
    top_breadths = [item for item in top_breadths if item is not None]
    if top_breadths and sum(item >= 60 for item in top_breadths) / len(top_breadths) < .4:
        return "NARROW_LEADERSHIP", selected
    if any(str(item.get("lifecycle")) in {"退潮", "衰退", "风险释放"} for item in selected[:2]):
        return "LEADERSHIP_ROTATION", selected
    return "STRONG_LEADERSHIP", selected


def _migration(workbench: dict[str, Any], volume_state: str, market_state: str) -> dict[str, Any]:
    rows = [item for item in (workbench.get("main_lines") or []) if isinstance(item, dict) and item.get("name")]
    inflow = sorted([item for item in rows if _num(item.get("main_net_inflow")) is not None], key=lambda item: _num(item.get("main_net_inflow")) or 0, reverse=True)
    outflow = sorted([item for item in rows if (_num(item.get("main_net_inflow")) or 0) < 0], key=lambda item: _num(item.get("main_net_inflow")) or 0)
    destination = [item.get("name") for item in inflow[:3]]
    source = [item.get("name") for item in outflow[:3]]
    observed = bool(destination or source)
    return {
        "state": "GROWTH_TO_RESOURCE_OR_DEFENSE" if observed and volume_state == "MIGRATION_VOLUME" else "SECTOR_DIFFERENTIATION" if observed else "UNKNOWN",
        "source_sectors": source,
        "destination_sectors": destination,
        "intensity": 70 if volume_state == "MIGRATION_VOLUME" else 45 if observed else None,
        "persistence": "待下一观察窗口确认",
        "method": "板块净流向与相对强度代理；不能识别真实账户身份",
        "market_state": market_state,
    }


async def _breadth_snapshot() -> tuple[dict[str, Any], str]:
    try:
        async with async_session() as session:
            rows = list((await session.execute(select(MarketSentimentDaily).order_by(desc(MarketSentimentDaily.trade_date)).limit(2))).scalars().all())
        if not rows:
            return {}, "UNAVAILABLE"
        row = rows[0]
        payload = {
            "data_date": row.trade_date.isoformat(),
            "up_count": row.up_count,
            "down_count": row.down_count,
            "flat_count": row.flat_count,
            "stock_count": row.stock_count,
            "market_amount": row.market_amount,
            "limit_up_count": row.limit_up_count,
            "limit_down_count": row.limit_down_count,
            "failed_limit_count": row.failed_limit_count,
            "failed_limit_rate": row.failed_limit_rate,
            "previous_amount": rows[1].market_amount if len(rows) > 1 else None,
            "source_updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "source": row.source,
        }
        current = shanghai_now()
        source_text = str(row.source or "").lower()
        updated = row.updated_at.replace(tzinfo=None) if row.updated_at else None
        age_seconds = (current.replace(tzinfo=None) - updated).total_seconds() if updated else None
        observed = bool(
            row.trade_date == current.date()
            and is_a_share_market_session(current)
            and age_seconds is not None
            and 0 <= age_seconds <= 15 * 60
            and ("intraday" in source_text or "realtime" in source_text or "实时" in source_text)
        )
        return payload, "OBSERVED" if observed else "CACHED"
    except Exception:
        return {}, "UNAVAILABLE"


class RociIntradayService:
    async def _cached(self) -> dict[str, Any] | None:
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, INTRADAY_CACHE_KEY)
            return dict(row.payload) if row and isinstance(row.payload, dict) else None
        except Exception:
            return None

    async def current(self, *, force: bool = False, context: dict[str, Any] | None = None) -> dict[str, Any]:
        now = shanghai_now()
        cached = await self._cached()
        cached_at = _dt((cached or {}).get("ingest_timestamp"))
        ttl = 45 if is_a_share_market_session(now) else 1800
        if not force and cached and cached_at and (now.replace(tzinfo=None) - cached_at).total_seconds() <= ttl:
            cached_view = {**cached, "cache_used": True, "refresh_policy": {"ttl_seconds": ttl, "cache_age_seconds": round((now.replace(tzinfo=None) - cached_at).total_seconds(), 1)}}
            if not is_a_share_market_session(now):
                cached_view["data_status"] = "CACHED"
                cached_view["is_realtime"] = False
                cached_view["latency_status"] = "OFF_SESSION"
            return cached_view

        workbench = (context or {}).get("workbench") if context else None
        workbench = workbench if isinstance(workbench, dict) else await _safe(market_decision_workbench_service.get(force=force), {}, 30.0)
        quotes, turnover, index_minute, breadth_result = await asyncio.gather(
            _safe(collector.fetch_tencent_index_quotes(), {}, 12.0),
            _safe(collector.fetch_market_turnover(), {}, 12.0),
            _safe(collector.fetch_shanghai_index_minute_trends(), {}, 12.0),
            _breadth_snapshot(),
        )
        breadth, breadth_status = breadth_result if isinstance(breadth_result, tuple) else ({}, "UNAVAILABLE")
        indexes = (quotes or {}).get("indices") or {}
        opening, absorption_state = _minute_structure(index_minute or {}, now)
        market_state, breadth_state, breadth_ratio = _state_from_inputs(indexes, breadth)
        volume_state, _ = _volume_state(turnover or {}, breadth_ratio, breadth.get("previous_amount"))
        leadership_state, leadership = _leadership(workbench if isinstance(workbench, dict) else {})
        migration = _migration(workbench if isinstance(workbench, dict) else {}, volume_state, market_state)
        risk_score = None
        if breadth_ratio is not None:
            risk_score = max(0.0, min(100.0, round(100 - breadth_ratio, 1)))
            if market_state == "BROAD_RISK_OFF":
                risk_score = min(100.0, risk_score + 15)
        opportunity_score = None
        strengths = [_num(item.get("strength")) for item in leadership]
        strengths = [item for item in strengths if item is not None]
        if strengths:
            opportunity_score = round(sum(strengths) / len(strengths), 1)
        provider_timestamp = max(
            [item for item in (_dt((quotes or {}).get("source_updated_at")), _dt((turnover or {}).get("source_updated_at"))) if item is not None],
            default=None,
        )
        ingest = now.replace(tzinfo=None)
        latency = round(max(0.0, (ingest - provider_timestamp).total_seconds() * 1000), 1) if provider_timestamp else None
        quote_realtime = bool((quotes or {}).get("is_realtime")) or bool((turnover or {}).get("is_realtime"))
        resolution = "1m" if (index_minute or {}).get("bars") else "daily_proxy"
        # Keep index and turnover freshness separate.  A Tencent quote may be
        # current while MarketSentimentDaily is still the previous session.
        data_status = classify_data_status(
            indexes,
            index_realtime=bool((quotes or {}).get("is_realtime")),
            turnover=turnover or {},
            breadth=breadth,
            breadth_status=breadth_status,
        )
        component_score = (40 if indexes else 0) + (25 if _has_turnover(turnover or {}) else 0) + (25 if _has_breadth(breadth) else 0) + (10 if (index_minute or {}).get("bars") else 0)
        if data_status == "CACHED":
            component_score -= 20
        elif data_status == "PARTIAL_REALTIME":
            component_score -= 10
        confidence = round(max(0.0, min(100.0, component_score)), 1)
        latency_status = "UNKNOWN"
        if latency is not None:
            latency_status = "STALE" if latency > 60_000 else "FRESH"
            if latency_status == "STALE":
                confidence = max(0.0, confidence - 20)

        previous = None
        try:
            async with async_session() as session:
                previous = (await session.execute(select(RociIntradaySnapshot).where(RociIntradaySnapshot.trade_date == (date.fromisoformat(str((quotes or {}).get("data_date") or breadth.get("data_date") or now.date())[:10]))).order_by(desc(RociIntradaySnapshot.snapshot_time)).limit(1))).scalar_one_or_none()
        except Exception:
            previous = None
        previous_payload = previous.payload if previous else {}
        previous_state = previous_payload.get("states") or {}
        previous_amount = _num(((previous_payload.get("turnover") or {}).get("sh_amount")) or ((previous_payload.get("turnover") or {}).get("market_amount")))
        volume_state, volume_change = _volume_state(turnover or {}, breadth_ratio, previous_amount if previous_amount is not None else breadth.get("previous_amount"))
        migration = _migration(workbench if isinstance(workbench, dict) else {}, volume_state, market_state)
        state_changes = []
        for key, value in {"market_state": market_state, "breadth_state": breadth_state, "volume_state": volume_state, "leadership_state": leadership_state, "migration_state": migration.get("state"), "data_status": data_status}.items():
            old = previous_state.get(key)
            if old and old != value:
                state_changes.append({"time": ingest.isoformat(), "field": key, "from": old, "to": value, "status": "OBSERVED"})
        if state_changes:
            events = [{"time": ingest.isoformat(), "event": f"{item['field']} 状态变化", "change": f"{item['from']} → {item['to']}", "impact": "盘中解释需要重新验证", "scenario_impact": "仅建议调整，不修改正式周度概率"} for item in state_changes]
        else:
            events = []
        scenario_state = "SUPPORT_BASE" if market_state in {"DEFENSIVE_ROTATION", "MIXED"} and volume_state in {"MIGRATION_VOLUME", "BALANCED_VOLUME"} else "SUPPORT_BULL" if market_state in {"RISK_ON_RECLAIM", "BROAD_RISK_ON"} else "SUPPORT_BEAR" if market_state == "BROAD_RISK_OFF" else "NO_SIGNAL"
        next_watch = [
            {"scenario": "迁移确认", "trigger": "成长相对强度继续下降且防御成交占比上升", "confirmation": "连续两个观察窗口状态不变", "invalidation": "成长核心止跌并重新获得宽度"},
            {"scenario": "风险修复", "trigger": "创业板相对沪深300改善且市场中位数回升", "confirmation": "领导力由窄变宽", "invalidation": "防御方向继续扩大承接"},
            {"scenario": "整体去风险", "trigger": "成长与防御同步转弱", "confirmation": "宽度、等权和跌停统计共同恶化", "invalidation": "防御板块重新获得承接"},
        ]
        payload = {
            "version": INTRADAY_VERSION,
            "trade_date": (quotes or {}).get("data_date") or breadth.get("data_date") or now.date().isoformat(),
            "snapshot_time": ingest.isoformat(),
            "provider_timestamp": provider_timestamp.isoformat() if provider_timestamp else None,
            "ingest_timestamp": ingest.isoformat(),
            "latency_ms": latency,
            "latency_status": latency_status,
            "data_status": data_status,
            "is_realtime": data_status == "REALTIME",
            "realtime_components": {
                "index_quotes": bool((quotes or {}).get("is_realtime")),
                "market_turnover": bool((turnover or {}).get("is_realtime")),
                "breadth": breadth_status == "OBSERVED",
            },
            "resolution": resolution,
            "states": {
                "market_state": market_state,
                "breadth_state": breadth_state,
                "volume_state": volume_state,
                "leadership_state": leadership_state,
                "migration_state": migration.get("state"),
                "risk_state": "ELEVATED" if risk_score is not None and risk_score >= 65 else "CONTROLLED" if risk_score is not None else "UNKNOWN",
                "opportunity_state": "SELECTIVE" if opportunity_score is not None and opportunity_score >= 55 else "UNKNOWN",
                "scenario_validation_state": scenario_state,
            },
            "risk_score": risk_score,
            "opportunity_score": opportunity_score,
            "indexes": indexes,
            "breadth": breadth,
            "breadth_ratio": breadth_ratio,
            "turnover": {**(turnover or {}), "change_vs_previous_pct": volume_change, "change_vs_snapshot_pct": volume_change},
            "index_minute": {
                "bar_count": (index_minute or {}).get("bar_count", 0),
                "latest_bar_at": (index_minute or {}).get("latest_bar_at"),
                "source": (index_minute or {}).get("source", "unavailable"),
                "data_status": "available" if (index_minute or {}).get("bars") else "unavailable",
            },
            "opening": opening,
            "absorption_state": absorption_state,
            "leadership": leadership,
            "migration": migration,
            "scenario_validation": {
                "state": scenario_state,
                "supporting_facts": [market_state, breadth_state, volume_state],
                "contradictions": ["盘中建议不修改正式周度概率"],
                "intraday_probability_suggestion": "仅提供方向性建议，收盘后由正式验证流程更新",
            },
            "shadow_skills": [],
            "previous_snapshot": {
                "snapshot_time": previous_payload.get("snapshot_time") if previous_payload else None,
                "states": previous_state,
                "risk_score": previous_payload.get("risk_score") if previous_payload else None,
                "opportunity_score": previous_payload.get("opportunity_score") if previous_payload else None,
            },
            "state_changes": state_changes,
            "events": events,
            "alerts": [
                {"level": "HIGH", "type": "STRUCTURE_CHANGE", "message": f"{item['field']} 从 {item['from']} 切换为 {item['to']}"}
                for item in state_changes
                if item["field"] in {"market_state", "volume_state", "leadership_state"}
            ],
            "next_30_60m": next_watch,
            "source_status": {
                "tencent_index_quotes": "REALTIME" if (quotes or {}).get("is_realtime") else "CACHED" if indexes else "UNAVAILABLE",
                "market_turnover": "REALTIME" if (turnover or {}).get("is_realtime") else "CACHED" if _has_turnover(turnover or {}) else "UNAVAILABLE",
                "index_minute": "REALTIME" if (index_minute or {}).get("is_realtime") else "CACHED" if (index_minute or {}).get("bars") else "UNAVAILABLE",
                "breadth": breadth_status,
                "workbench": "AVAILABLE" if isinstance(workbench, dict) and not workbench.get("error") else "UNAVAILABLE",
            },
            "method": "结构化规则 + 来源标记的分钟/日线代理；不把大单或盘口数据直接解释为机构行为。",
            "note": "盘中剧本只提供建议验证方向，不修改正式周度概率，也不连接交易执行。",
        }
        payload["shadow_skills"] = build_shadow_skill_outputs(payload, previous_payload)
        explanation_payload = {
            "battlefield": {"regime": market_state, "label": market_state, "market_reward": "盘中状态", "market_penalty": breadth_state},
            "primary_contradiction": {"statement": "盘中状态是否由真实宽度和资金持续确认", "supporting_evidence": [_fact("盘中市场状态", market_state, source="roci_intraday_snapshots", field="market_state", strength=.75, timestamp=ingest)], "opposing_evidence": [_fact("数据源状态", data_status, source="roci_intraday_snapshots", field="data_status", strength=.65, supports=data_status == "REALTIME", evidence_type="COUNTER_EVIDENCE", timestamp=ingest)]},
            "risk_pricing": {"status": payload["states"]["risk_state"]},
            "stress_test": {"state": payload["states"]["risk_state"]},
            "facts": [_fact("盘中市场状态", market_state, source="roci_intraday_snapshots", field="market_state", strength=.78, timestamp=ingest), _fact("盘中广度状态", breadth_state, source="roci_intraday_snapshots", field="breadth_state", strength=.72, timestamp=ingest), _fact("成交性质", volume_state, source="roci_intraday_snapshots", field="volume_state", strength=.7, timestamp=ingest)],
            "data_completeness_pct": confidence,
            "data_cutoff_time": provider_timestamp.isoformat() if provider_timestamp else ingest.isoformat(),
            "source_status": payload["source_status"],
            "action": {"confidence": confidence},
        }
        payload["explanation"] = build_explanation(explanation_payload, context={"workbench": workbench}, entity_type="intraday", entity_id="market")
        payload["cache_used"] = False
        await self._persist(payload)
        return payload

    async def _persist(self, payload: dict[str, Any]) -> None:
        trade_date = date.fromisoformat(str(payload.get("trade_date"))[:10])
        snapshot_time = _dt(payload.get("snapshot_time")) or shanghai_now().replace(tzinfo=None)
        provider_time = _dt(payload.get("provider_timestamp"))
        try:
            async with async_session() as session:
                states = payload.get("states") or {}
                session.add(RociIntradaySnapshot(
                    trade_date=trade_date,
                    snapshot_time=snapshot_time,
                    resolution=payload.get("resolution") or "daily_proxy",
                    market_state=states.get("market_state") or "UNKNOWN",
                    breadth_state=states.get("breadth_state") or "UNKNOWN",
                    volume_state=states.get("volume_state") or "UNKNOWN",
                    leadership_state=states.get("leadership_state") or "UNKNOWN",
                    migration_state=states.get("migration_state") or "UNKNOWN",
                    risk_score=payload.get("risk_score"),
                    opportunity_score=payload.get("opportunity_score"),
                    confidence=(payload.get("explanation") or {}).get("data_quality", {}).get("score_pct"),
                    provider_timestamp=provider_time,
                    ingest_timestamp=snapshot_time,
                    latency_ms=payload.get("latency_ms"),
                    data_status=payload.get("data_status") or "INSUFFICIENT_DATA",
                    is_realtime=bool(payload.get("is_realtime")),
                    payload=payload,
                ))
                cache = await session.get(MarketDataCache, INTRADAY_CACHE_KEY)
                if cache is None:
                    session.add(MarketDataCache(key=INTRADAY_CACHE_KEY, payload=payload))
                else:
                    cache.payload = payload
                    cache.updated_at = snapshot_time
                await session.commit()
        except Exception as exc:
            print(f"ROCI intraday persistence failed: {type(exc).__name__}")

    async def timeline(self, *, trade_date: date | None = None, limit: int = 96) -> dict[str, Any]:
        try:
            async with async_session() as session:
                target = trade_date
                if target is None:
                    target = (await session.execute(
                        select(RociIntradaySnapshot.trade_date)
                        .order_by(desc(RociIntradaySnapshot.trade_date), desc(RociIntradaySnapshot.snapshot_time))
                        .limit(1)
                    )).scalar_one_or_none()
                target = target or shanghai_now().date()
                rows = list((await session.execute(select(RociIntradaySnapshot).where(RociIntradaySnapshot.trade_date == target).order_by(RociIntradaySnapshot.snapshot_time).limit(min(max(limit, 1), 240)))).scalars().all())
            return {"trade_date": target.isoformat(), "count": len(rows), "items": [{"snapshot_time": row.snapshot_time.isoformat(), "states": (row.payload or {}).get("states") or {}, "risk_score": row.risk_score, "opportunity_score": row.opportunity_score, "data_status": row.data_status, "is_realtime": row.is_realtime} for row in rows], "source": "roci_intraday_snapshots"}
        except Exception as exc:
            return {"trade_date": target.isoformat(), "count": 0, "items": [], "source": f"unavailable:{type(exc).__name__}"}

    async def section(self, name: str, *, force: bool = False) -> dict[str, Any]:
        current = await self.current(force=force)
        if name == "events":
            return {"items": current.get("events") or [], "data_status": current.get("data_status"), "snapshot_time": current.get("snapshot_time")}
        if name == "alerts":
            return {"items": current.get("alerts") or [], "data_status": current.get("data_status"), "snapshot_time": current.get("snapshot_time")}
        if name == "breadth":
            return {"state": (current.get("states") or {}).get("breadth_state"), "breadth": current.get("breadth"), "breadth_ratio": current.get("breadth_ratio"), "changes": current.get("state_changes") or []}
        if name == "volume-regime":
            return {"state": (current.get("states") or {}).get("volume_state"), "turnover": current.get("turnover"), "explanation": current.get("explanation")}
        if name == "leadership":
            return {"state": (current.get("states") or {}).get("leadership_state"), "items": current.get("leadership") or [], "explanation": current.get("explanation")}
        if name == "migration":
            return {**(current.get("migration") or {}), "explanation": current.get("explanation")}
        if name == "scenario-validation":
            return {**(current.get("scenario_validation") or {}), "next_30_60m": current.get("next_30_60m") or [], "explanation": current.get("explanation")}
        return current

    async def stock(self, symbol: str, *, force: bool = False) -> dict[str, Any]:
        from services.data_collector import normalize_stock_code

        code = normalize_stock_code(symbol)
        current = await self.current(force=force)
        quote = await _safe(collector.fetch_tencent_quotes([code]), {}, 12.0)
        item = next((row for row in (quote.get("stocks") or []) if str(row.get("code")) == code), {}) if isinstance(quote, dict) else {}
        market_change = _num(((current.get("indexes") or {}).get("hs300") or {}).get("change_pct"))
        stock_change = _num(item.get("change_pct"))
        state = "INDIVIDUAL_STRENGTH" if stock_change is not None and market_change is not None and stock_change > market_change + .8 else "MARKET_BETA" if stock_change is not None and market_change is not None and stock_change <= market_change + .8 else "INSUFFICIENT_DATA"
        return {"symbol": code, "quote": item, "market_change_pct": market_change, "state": state, "market_context": current.get("states"), "risk_permission": "YELLOW" if current.get("risk_score") is None or current.get("risk_score") < 70 else "RED", "note": "个股强弱必须同时比较市场和板块；盘中状态为只读观察。", "data_status": current.get("data_status"), "source": "tencent+roci_intraday"}


roci_intraday_service = RociIntradayService()
