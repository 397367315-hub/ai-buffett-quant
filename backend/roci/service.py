"""ROCI orchestration, persistence and public read model."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, desc, select

from database import async_session
from models import (
    MarketDataCache,
    RociAction,
    RociActionEvidence,
    RociAsymmetryScore,
    RociBattlefieldSnapshot,
    RociForce,
    RociForceHistory,
    RociModelRiskEvent,
    RociExplanation,
    RociExplanationAlternative,
    RociExplanationChain,
    RociExplanationDriver,
    RociExplanationEvidence,
    RociExplanationValidation,
    RociOpportunityPattern,
    RociPatternHit,
    RociPrimaryContradiction,
    RociReplay,
    RociRiskOpportunityConversion,
    RociRiskPricing,
    RociSkill,
    RociSkillRun,
    RociSourceRegistry,
    RociSourceSkillLink,
    RociStressEvent,
    RociStressResponse,
    RociUserFeedback,
)
from services.data_collector import normalize_stock_code, shanghai_now

from .adapters import ROCI_CACHE_KEY, load_existing_context
from .explanation import attach_explanations, build_explanation
from .intraday import roci_intraday_service
from .engines import (
    ACTIONS,
    UNKNOWN,
    action,
    asymmetry,
    battlefield,
    completeness,
    contradiction,
    cognitive_risk,
    evidence,
    expectation_gap,
    forces,
    num,
    opportunities,
    risk_adapted_recommendations,
    risk_pricing,
    stress_test,
    supply_absorption,
)
from .registry import (
    ROCI_VERSION,
    SOURCE_DEFINITIONS,
    all_pattern_definitions,
    all_skill_definitions,
    skill_by_id,
)


def _json(value: Any) -> Any:
    """Convert datetime/date values while preserving JSON-safe payloads."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _date_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _now() -> datetime:
    return shanghai_now().replace(tzinfo=None)


def _snapshot_key(trade_date: date, symbol: str | None, cutoff: str) -> str:
    raw = f"{trade_date.isoformat()}|{symbol or 'market'}|{cutoff[:19]}|{ROCI_VERSION}"
    return f"roci-{hashlib.sha256(raw.encode()).hexdigest()[:28]}"


def _requirement_state(skill: dict[str, Any], context: dict[str, Any], *, triggered: bool) -> dict[str, Any]:
    """Expose per-Skill data availability without inventing a proxy value."""
    available: set[str] = set()
    if context.get("workbench"):
        available.update({"market_regime", "breadth", "structure", "crowding", "sector_context", "sector_breadth", "sector_leadership", "contradiction", "reward_punishment", "emotion"})
    forecast = context.get("forecast") or {}
    daily = context.get("daily") or {}
    micro = context.get("microstructure") or {}
    if forecast.get("timeline"):
        available.add("forecast_v5")
    if daily.get("data_date"):
        available.add("daily_bars")
    if daily.get("bars") or daily.get("bars_by_code"):
        available.update({"volume", "volatility", "relative_strength", "risk_levels", "asymmetry", "stress_events"})
    if micro.get("engines"):
        available.update({"auction", "intraday_evidence", "minute_bars", "vwap", "liquidity"})
    intraday = context.get("intraday") or {}
    if intraday:
        available.update({"intraday_evidence", "breadth", "equal_weight", "sector_leadership", "sector_breadth", "sector_history", "fund_flow", "weekly_scenario", "validation", "state_history", "volume"})
    if context.get("source_status", {}).get("quant_snapshot") == "available":
        available.update({"fund_flow", "sector_history"})
    requirements = list(skill.get("data_requirements") or [])
    missing = [item for item in requirements if item not in available]
    enabled = bool(skill.get("enabled", True)) and skill.get("status") != "DISABLED"
    usable = enabled and not missing
    return {
        "available": usable,
        "enabled": enabled,
        "requirements": requirements,
        "missing": missing,
        "triggered": triggered,
        "reason": "满足注册的数据依赖" if usable else "禁用状态" if not enabled else "缺少：" + "、".join(missing),
    }


def _skill_trigger(skill: dict[str, Any], ctx: dict[str, Any], battle: dict[str, Any], risk: dict[str, Any], stress: dict[str, Any], opp: dict[str, Any], asym: dict[str, Any]) -> tuple[bool, float | None, float, list[dict[str, Any]], dict[str, Any]]:
    skill_id = skill["skill_id"]
    regime = battle.get("regime")
    wb = ctx.get("workbench") or {}
    structure = num(((wb.get("structure_health") or {}).get("score")))
    crowd = num(((wb.get("crowding_risk") or {}).get("score")))
    data_pct, missing = completeness(ctx)
    bars = (ctx.get("daily") or {}).get("bars") or []
    triggered = False
    score: float | None = None
    reasons: list[dict[str, Any]] = []
    if not skill.get("enabled", True) or skill.get("status") == "DISABLED":
        reasons.append({"type": "FACT", "label": "运行状态", "value": "DISABLED", "source": "roci_skill_registry", "supports": False})
    elif skill_id in {"ROCI-S002", "ROCI-S003", "ROCI-S004", "ROCI-S007", "ROCI-S037"}:
        triggered = True
        score = data_pct
        reasons.append({"type": "FACT", "label": "适配器数据完整度", "value": data_pct, "source": "roci_adapters"})
    elif skill_id in {"ROCI-S020", "ROCI-S022"}:
        observed = bool((ctx.get("microstructure") or {}).get("engines", {}).get("auction_microstructure"))
        triggered, score = observed, 60.0 if observed else None
        reasons.append({"type": "FACT", "label": "竞价快照", "value": "available" if observed else UNKNOWN, "source": "v51_microstructure", "supports": observed})
    elif skill_id in {f"ROCI-S{number:03d}" for number in range(90, 98)}:
        intraday_items = {str(item.get("skill_id")): item for item in (ctx.get("intraday") or {}).get("shadow_skills") or []}
        runtime = intraday_items.get(skill_id) or {}
        triggered, score = bool(runtime.get("triggered")), num(runtime.get("score"))
        reasons.extend(runtime.get("evidence") or [{"type": "FACT", "label": "盘中技能状态", "value": runtime.get("state", UNKNOWN), "source": "roci_intraday_snapshots", "supports": triggered}])
        confidence = num(runtime.get("confidence"))
        state = {"regime": regime, "missing_inputs": runtime.get("availability", {}).get("missing", missing), "status": skill.get("status"), "shadow_excluded_from_action": True, "availability": runtime.get("availability") or _requirement_state(skill, ctx, triggered=triggered)}
        return triggered, score, confidence if confidence is not None else round(min(95.0, data_pct * .7 + (25 if triggered else 0)), 1), reasons, state
    elif skill_id == "ROCI-S023":
        triggered, score = regime in {"RECOVERY", "NORMAL_OFFENSE"}, 62.0 if triggered else None
        reasons.append({"type": "INFERENCE", "label": "战场状态迁移", "value": regime, "source": "roci_battlefield"})
    elif skill_id in {"ROCI-S027", "ROCI-S033", "ROCI-S046"}:
        triggered, score = regime != UNKNOWN, 70.0 if triggered else None
        reasons.append({"type": "FACT", "label": "生态状态", "value": regime, "source": "roci_battlefield"})
    elif skill_id in {"ROCI-S028"}:
        observed = bool((ctx.get("microstructure") or {}).get("engines", {}).get("intraday_relative_strength"))
        triggered, score = observed, 55.0 if observed else None
        reasons.append({"type": "FACT", "label": "分钟/分时证据", "value": "available" if observed else UNKNOWN, "source": "v51_microstructure", "supports": observed})
    elif skill_id in {"ROCI-S030", "ROCI-S050", "ROCI-S051", "ROCI-S052", "ROCI-S056", "ROCI-S057"}:
        triggered, score = asym.get("status") != UNKNOWN, asym.get("score")
        reasons.append({"type": "INFERENCE", "label": "风险边界与赔率", "value": asym.get("status"), "source": "roci_asymmetry"})
    elif skill_id in {"ROCI-S035", "ROCI-S059", "ROCI-S060", "ROCI-S061", "ROCI-S062", "ROCI-S063", "ROCI-S064"}:
        triggered, score = len(bars) >= 3, 60.0 if len(bars) >= 3 else None
        reasons.append({"type": "FACT", "label": "日线样本数", "value": len(bars), "source": "stock_daily_bars", "supports": len(bars) >= 3})
    elif skill_id == "ROCI-S038":
        triggered, score = stress.get("state") in {"RESILIENT", "ANTIFRAGILE"}, 65.0 if triggered else None
        reasons.append({"type": "INFERENCE", "label": "压力响应状态", "value": stress.get("state"), "source": "roci_stress_test"})
    elif skill_id in {"ROCI-S041", "ROCI-S045"}:
        lines = wb.get("main_lines") or []
        triggered, score = bool(lines), 65.0 if lines else None
        reasons.append({"type": "FACT", "label": "主线板块记录", "value": len(lines), "source": "topic_strength"})
    elif skill_id in {"ROCI-S042", "ROCI-S043", "ROCI-S047"} or (skill_id.startswith("ROCI-S") and 67 <= int(skill_id.split("S")[-1]) <= 76):
        matches = [item for item in opp.get("patterns") or [] if item.get("pattern_id") == skill_id and item.get("triggered")]
        triggered, score = bool(matches), (matches[0].get("score") if matches else None)
        reasons.append({"type": "INFERENCE", "label": "机会检测器", "value": "triggered" if triggered else "not_triggered", "source": "roci_opportunity_arsenal", "supports": triggered})
    elif skill_id in {"ROCI-S065", "ROCI-S066"}:
        triggered, score = crowd is not None, crowd
        reasons.append({"type": "FACT", "label": "拥挤风险", "value": crowd if crowd is not None else UNKNOWN, "source": "market_decision_workbench", "supports": crowd is not None})
    elif skill_id in {"ROCI-S001", "ROCI-S008", "ROCI-S009", "ROCI-S015", "ROCI-S016", "ROCI-S017", "ROCI-S018", "ROCI-S021", "ROCI-S025", "ROCI-S026", "ROCI-S029", "ROCI-S031", "ROCI-S032", "ROCI-S036", "ROCI-S039", "ROCI-S040", "ROCI-S044", "ROCI-S049", "ROCI-S053", "ROCI-S054", "ROCI-S055", "ROCI-S058", "ROCI-S066"}:
        triggered, score = len(bars) >= 3 or bool(wb), 55.0 if (len(bars) >= 3 or bool(wb)) else None
        reasons.append({"type": "FACT", "label": "可观测输入", "value": "available" if triggered else UNKNOWN, "source": "roci_adapters", "supports": triggered})
    else:
        # Knowledge-only Skills remain visible but do not pretend to have a
        # runtime trigger.  Shadow patterns are handled by the explicit lab.
        triggered, score = False, None
        reasons.append({"type": "SOURCE_CLAIM", "label": "资料主张", "value": skill.get("source_claim"), "source": skill.get("source_name"), "supports": False})
    confidence = round(min(95.0, data_pct * 0.7 + (25 if triggered else 0)), 1)
    state = {"regime": regime, "missing_inputs": missing, "status": skill.get("status"), "shadow_excluded_from_action": skill.get("status") == "SHADOW", "availability": _requirement_state(skill, ctx, triggered=triggered)}
    return triggered, score, confidence, reasons, state


class RociService:
    def __init__(self) -> None:
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._persist_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None
        self._refresh_status: dict[str, Any] = {
            "status": "idle",
            "stage": "idle",
            "progress": 0,
            "message": "等待统一刷新",
            "sources": {},
            "updated_at": None,
        }

    async def status(self) -> dict[str, Any]:
        await self.ensure_initialized()
        try:
            async with async_session() as session:
                skills = list((await session.execute(select(RociSkill))).scalars().all())
                latest = (await session.execute(select(RociBattlefieldSnapshot).order_by(desc(RociBattlefieldSnapshot.created_at)).limit(1))).scalar_one_or_none()
            return {
                "status": "READY",
                "version": ROCI_VERSION,
                "sidecar": True,
                "read_only_legacy_adapters": True,
                "skill_count": len(skills),
                "active_count": sum(row.status == "ACTIVE" and row.enabled for row in skills),
                "shadow_count": sum(row.status == "SHADOW" and row.enabled for row in skills),
                "latest_snapshot": {
                    "snapshot_key": latest.snapshot_key,
                    "trade_date": latest.trade_date.isoformat(),
                    "data_cutoff_time": latest.data_cutoff_time.isoformat(),
                    "regime": latest.regime,
                } if latest else None,
                "action_policy": list(ACTIONS),
            }
        except Exception as exc:
            return {"status": "DEGRADED", "version": ROCI_VERSION, "sidecar": True, "error": type(exc).__name__}

    async def ensure_initialized(self) -> None:
        """Seed registry metadata idempotently without touching legacy rows."""
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            try:
                async with async_session() as session:
                    for source in SOURCE_DEFINITIONS:
                        row = await session.get(RociSourceRegistry, source["key"])
                        values = {"name": source["name"], "source_type": source["type"], "active": True, "trust_note": "资料来源仅提供 SOURCE_CLAIM，不直接产生交易结论。"}
                        if row is None:
                            session.add(RociSourceRegistry(source_key=source["key"], **values))
                        else:
                            for key, value in values.items():
                                setattr(row, key, value)
                    for item in all_skill_definitions():
                        row = await session.get(RociSkill, item["skill_id"])
                        values = {key: item.get(key) for key in ("name", "category", "source_name", "source_section", "source_pages", "source_claim", "engineered_definition", "status", "version", "data_requirements", "applicable_regimes", "forbidden_regimes", "default_weight", "validation_status")}
                        if row is None:
                            session.add(RociSkill(skill_id=item["skill_id"], enabled=True, **values))
                        else:
                            # Runtime promotion/disable decisions are preserved;
                            # definition edits and source metadata are refreshed.
                            current_status = row.status
                            for key, value in values.items():
                                if key != "status" or current_status in {None, "", "DETECT_ONLY"}:
                                    setattr(row, key, value)
                    for pattern in all_pattern_definitions():
                        row = await session.get(RociOpportunityPattern, pattern["id"])
                        values = {"name": pattern["name"], "category": pattern["category"], "source_name": pattern["source"], "definition": pattern["definition"], "detection_rule": pattern["rule"], "status": pattern.get("status", "DETECT_ONLY"), "applicable_regimes": []}
                        if row is None:
                            session.add(RociOpportunityPattern(pattern_id=pattern["id"], enabled=True, **values))
                        else:
                            for key, value in values.items():
                                if key != "status" or row.status in {None, "", "DETECT_ONLY"}:
                                    setattr(row, key, value)
                    await session.flush()
                    for item in all_skill_definitions():
                        existing = (await session.execute(select(RociSourceSkillLink).where(RociSourceSkillLink.source_key == item["source_key"], RociSourceSkillLink.skill_id == item["skill_id"]))).scalar_one_or_none()
                        if existing is None:
                            session.add(RociSourceSkillLink(source_key=item["source_key"], skill_id=item["skill_id"], section=item["source_section"], relation="derived_from"))
                    await session.commit()
                self._initialized = True
            except Exception as exc:
                print(f"ROCI registry seed failed: {type(exc).__name__}")

    async def _registered_skills(self) -> list[dict[str, Any]]:
        definitions = {item["skill_id"]: item for item in all_skill_definitions()}
        try:
            async with async_session() as session:
                rows = list((await session.execute(select(RociSkill))).scalars().all())
        except Exception:
            return list(definitions.values())
        for row in rows:
            item = definitions.get(row.skill_id)
            if item is None:
                continue
            item.update({
                "status": row.status,
                "enabled": row.enabled,
                "validation_status": row.validation_status,
                "sample_size": row.sample_size,
                "hit_rate": row.hit_rate,
                "profit_factor": row.profit_factor,
                "expectancy_r": row.expectancy_r,
                "max_drawdown": row.max_drawdown,
            })
        return list(definitions.values())

    async def _battlefield_history(self, *, symbol: str | None, current_key: str, limit: int = 10) -> list[dict[str, Any]]:
        try:
            async with async_session() as session:
                query = select(RociBattlefieldSnapshot).where(RociBattlefieldSnapshot.snapshot_key != current_key)
                query = query.where(RociBattlefieldSnapshot.symbol == symbol) if symbol else query.where(RociBattlefieldSnapshot.symbol.is_(None))
                rows = list((await session.execute(query.order_by(desc(RociBattlefieldSnapshot.trade_date), desc(RociBattlefieldSnapshot.data_cutoff_time)).limit(limit * 4))).scalars().all())
        except Exception:
            return []
        history: list[dict[str, Any]] = []
        seen_dates: set[str] = set()
        for row in rows:
            trade_date = row.trade_date.isoformat()
            if trade_date in seen_dates:
                continue
            seen_dates.add(trade_date)
            history.append({
                "trade_date": trade_date,
                "data_cutoff_time": row.data_cutoff_time.isoformat(),
                "regime": row.regime,
                "market_reward": row.market_reward,
                "market_penalty": row.market_penalty,
            })
            if len(history) >= limit:
                break
        return list(reversed(history))

    async def _context(
        self,
        *,
        force: bool = False,
        symbol: str | None = None,
        as_of: date | None = None,
        refresh_inputs: bool | None = None,
    ) -> dict[str, Any]:
        context = await load_existing_context(
            force=force,
            symbol=symbol,
            as_of=as_of,
            refresh_inputs=refresh_inputs,
        )
        if context.get("cached_roci") and not force and not symbol:
            return context
        context["pattern_definitions"] = all_pattern_definitions()
        return context

    async def _refresh_linked_sources(self) -> dict[str, dict[str, Any]]:
        """Warm source caches used by a direct ROCI sub-page refresh.

        The cockpit uses ``request_refresh`` below. This compatibility path is
        kept for individual detail pages, but its writes are deliberately
        serialized because Render's small database pool cannot safely absorb a
        full-market event, auction and PIT fan-out at the same time.
        """
        from services.event_radar import event_radar_service
        from services.market_way_v4 import market_way_v4_service
        from services.v51_microstructure_service import v51_microstructure_service

        jobs = (
            ("event_radar", lambda: event_radar_service.refresh(force=True), 50),
            ("v51_auction", lambda: v51_microstructure_service.auction_dashboard(refresh=True), 70),
            ("v4_data_pipeline", lambda: market_way_v4_service.refresh_sources(background=True), 20),
        )
        result_map: dict[str, dict[str, Any]] = {
            "topic_strength": {"status": "delegated_to_workbench"},
            "v4_policy": {"status": "delegated_to_v4_context"},
        }
        for label, factory, timeout in jobs:
            try:
                result = await asyncio.wait_for(factory(), timeout=timeout)
                if isinstance(result, dict) and result.get("status") in {"error", "unavailable", "UNAVAILABLE"}:
                    result_map[label] = {"status": "degraded", "detail": result.get("status")}
                elif isinstance(result, dict) and result.get("status"):
                    result_map[label] = {
                        "status": str(result.get("status")),
                        "progress": result.get("progress"),
                        "stage": result.get("stage"),
                        "message": result.get("message"),
                        "warnings": result.get("warnings") or [],
                        "data_date": result.get("data_date"),
                        "source": result.get("source"),
                    }
                else:
                    result_map[label] = {
                        "status": "available",
                        "data_date": result.get("data_date") if isinstance(result, dict) else None,
                        "source": result.get("source") if isinstance(result, dict) else None,
                    }
            except Exception as exc:
                result_map[label] = {"status": "unavailable", "error": type(exc).__name__}
        return result_map

    async def build(
        self,
        *,
        force: bool = False,
        symbol: str | None = None,
        as_of: date | None = None,
        persist: bool = True,
        include_intraday: bool = False,
        refresh_linked: bool = True,
        refresh_inputs: bool | None = None,
        context_override: dict[str, Any] | None = None,
        linked_refresh_override: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        await self.ensure_initialized()
        linked_refresh: dict[str, dict[str, Any]] = linked_refresh_override or {}
        if context_override is not None:
            context = context_override
        elif force and symbol is None and refresh_linked:
            # Keep the legacy direct-refresh path deterministic as well. The
            # dashboard uses the background coordinator, while detail pages
            # should not run their context and source writers concurrently.
            context = await self._context(force=True, symbol=symbol, as_of=as_of)
            linked_refresh = await self._refresh_linked_sources()
        else:
            context = await self._context(
                force=force,
                symbol=symbol,
                as_of=as_of,
                refresh_inputs=refresh_inputs,
            )
        if include_intraday and symbol is None and "intraday" not in context:
            # Use the already loaded workbench as the intraday adapter input so
            # the dashboard refresh does not perform a second workbench call.
            try:
                context["intraday"] = await asyncio.wait_for(
                    roci_intraday_service.current(
                        force=force and refresh_inputs is not False,
                        context={"workbench": context.get("workbench")},
                    ),
                    timeout=25.0,
                )
            except Exception:
                context["intraday"] = {}
        if context.get("cached_roci") and not force and not symbol:
            payload = deepcopy(context["cached_roci"])
            payload["cache_used"] = True
            attach_explanations(payload, context)
            return payload
        now = _now()
        skill_definitions = await self._registered_skills()
        daily_date = _date((context.get("daily") or {}).get("data_date"))
        trade_date = as_of or daily_date or _date(((context.get("workbench") or {}).get("meta") or {}).get("decision_date")) or now.date()
        cutoff = str((context.get("forecast") or {}).get("data_cutoff_time") or (context.get("workbench") or {}).get("meta", {}).get("updated_at") or context.get("collected_at") or now.isoformat())
        battle = battlefield(context)
        force_map = forces(context, battle)
        primary = contradiction(context, battle, force_map)
        pricing = risk_pricing(context, battle, primary)
        stress = stress_test(context)
        expectation = expectation_gap(context)
        supply = supply_absorption(context)
        complete_pct, missing = completeness(context)
        preliminary_asym = asymmetry(context, battle, pricing)
        opp = opportunities(context, battle, primary, pricing, stress, preliminary_asym)
        source_status = {
            **(context.get("source_status") or {}),
            **{f"linked_{key}": value.get("status") for key, value in linked_refresh.items()},
        }
        facts = []
        facts.extend(battle.get("facts") or [])
        facts.extend(primary.get("supporting_evidence") or [])
        inferences = [evidence("战场生态", battle.get("regime"), "roci_battlefield", "INFERENCE"), evidence("主要矛盾", primary.get("statement"), "roci_contradiction", "INFERENCE"), evidence("风险定价", pricing.get("status"), "roci_risk_pricing", "INFERENCE"), evidence("压力测试", stress.get("state"), "roci_stress_test", "INFERENCE")]
        source_claims = [{"type": "SOURCE_CLAIM", "skill_id": item["skill_id"], "source": item["source_name"], "claim": item["source_claim"]} for item in skill_definitions if item["status"] in {"KNOWLEDGE_ONLY", "SHADOW"}][:12]
        snapshot_key = _snapshot_key(trade_date, symbol, cutoff)
        battle["history"] = await self._battlefield_history(symbol=symbol, current_key=snapshot_key)
        skill_runs = []
        for skill in skill_definitions:
            triggered, score, confidence, reasons, state = _skill_trigger(skill, context, battle, pricing, stress, opp, preliminary_asym)
            contribution = round((score or 0) * confidence / 100, 1) if skill["status"] == "ACTIVE" and skill.get("enabled", True) and triggered else None
            skill_runs.append({"skill_id": skill["skill_id"], "name": skill["name"], "category": skill["category"], "status": skill["status"], "enabled": skill.get("enabled", True), "triggered": triggered, "score": score, "confidence": confidence, "contribution": contribution, "evidence": reasons, "state": state, "source_key": skill.get("source_key"), "source_name": skill["source_name"], "source_section": skill["source_section"], "source_pages": skill.get("source_pages"), "source_claim": skill["source_claim"], "engineered_definition": skill.get("engineered_definition"), "data_requirements": skill.get("data_requirements") or [], "applicable_regimes": skill.get("applicable_regimes") or [], "forbidden_regimes": skill.get("forbidden_regimes") or [], "default_weight": skill.get("default_weight"), "validation_status": skill["validation_status"], "sample_size": skill.get("sample_size", 0), "hit_rate": skill.get("hit_rate"), "profit_factor": skill.get("profit_factor"), "expectancy_r": skill.get("expectancy_r"), "max_drawdown": skill.get("max_drawdown"), "current_impact": contribution, "recent_decay": UNKNOWN})
        directional_active_skills = {"ROCI-S023", "ROCI-S027", "ROCI-S028", "ROCI-S038", "ROCI-S045", "ROCI-S046", "ROCI-S059", "ROCI-S063", "ROCI-S064"}
        active_confirmation_categories = {
            item["category"] for item in skill_runs
            if item["skill_id"] in directional_active_skills and item["status"] == "ACTIVE" and item["triggered"] and (item.get("score") or 0) >= 60
        }
        cognition = cognitive_risk(context, completeness_pct=complete_pct, missing_inputs=missing, skill_runs=skill_runs)
        decision = action(
            battle,
            primary,
            pricing,
            stress,
            preliminary_asym,
            complete_pct,
            active_confirmation_count=len(active_confirmation_categories),
        )
        opp["risk_adapted"] = risk_adapted_recommendations(
            context,
            battle,
            pricing,
            stress,
            decision,
        )
        stocks = []
        if symbol:
            stocks = [{"code": symbol, "name": (context.get("reflexivity") or {}).get("name") or symbol, "sector": (context.get("reflexivity") or {}).get("sector")}]
        else:
            stocks = opp.get("candidates") or []
        payload = {
            "version": ROCI_VERSION,
            "snapshot_key": snapshot_key,
            "symbol": symbol,
            "trade_date": trade_date.isoformat(),
            "data_cutoff_time": cutoff,
            "generated_at": now.isoformat(),
            "is_realtime": bool((context.get("workbench") or {}).get("meta", {}).get("is_realtime")),
            "cache_used": bool(context.get("cache_used")),
            "data_completeness_pct": complete_pct,
            "missing_inputs": missing,
            "source_status": source_status,
            "battlefield": battle,
            "forces": force_map,
            "primary_contradiction": primary,
            "risk_pricing": pricing,
            "stress_test": stress,
            "expectation_gap": expectation,
            "supply_absorption": supply,
            "opportunities": opp,
            "asymmetry": preliminary_asym,
            "cognitive_risk": cognition,
            "action": decision,
            "skills": {"items": skill_runs, "count": len(skill_runs), "active_count": sum(item["status"] == "ACTIVE" and item.get("enabled", True) for item in skill_runs), "shadow_count": sum(item["status"] == "SHADOW" and item.get("enabled", True) for item in skill_runs)},
            "stocks": stocks[:20],
            "facts": facts,
            "inferences": inferences,
            "source_claims": source_claims,
            "refresh_report": {
                "requested": bool(force),
                "linked_sources": linked_refresh,
                "core_sources": context.get("source_status") or {},
                "policy": "主看板刷新会先预热所有 ROCI 依赖源，再生成同一截止时间的只读快照。",
            },
            "intraday": context.get("intraday") or {},
            "agent_report": {"facts": facts, "inferences": inferences + [evidence("认知与模型风险", cognition.get("level"), "roci_cognitive_risk", "INFERENCE")], "source_claims": source_claims, "skills_used": [item["skill_id"] for item in skill_runs if item["triggered"] and item["status"] == "ACTIVE" and item.get("enabled", True)], "data_cutoff": cutoff, "confidence": decision.get("confidence"), "invalidations": decision.get("invalidations") or [], "risk_flags": cognition.get("model_risks") or []},
            "audit": {"no_future_data": True, "read_only_adapters": True, "shadow_excluded_from_action": True, "unknown_policy": "缺失关键输入不补造中性数值", "model_version": ROCI_VERSION},
        }
        attach_explanations(payload, context)
        if persist:
            # Several ROCI pages may request the same source cutoff together.
            # Serialize replacement of that derived graph so both requests do
            # not race between the existence check and the unique-key insert.
            async with self._persist_lock:
                await self._persist(payload, context, battle, force_map, primary, pricing, stress, opp, preliminary_asym, cognition, decision, skill_runs)
        return payload

    async def _persist(self, payload: dict[str, Any], context: dict[str, Any], battle: dict[str, Any], force_map: dict[str, Any], primary: dict[str, Any], pricing: dict[str, Any], stress: dict[str, Any], opp: dict[str, Any], asym: dict[str, Any], cognition: dict[str, Any], decision: dict[str, Any], skill_runs: list[dict[str, Any]]) -> None:
        snapshot_key = payload["snapshot_key"]
        trade_date = _date(payload["trade_date"]) or _now().date()
        cutoff = datetime.fromisoformat(str(payload["data_cutoff_time"]).replace("Z", "+00:00")).replace(tzinfo=None) if str(payload.get("data_cutoff_time", "")).startswith(("20", "19")) else _now()
        try:
            async with async_session() as session:
                row = (await session.execute(select(RociBattlefieldSnapshot).where(RociBattlefieldSnapshot.snapshot_key == snapshot_key))).scalar_one_or_none()
                values = {"symbol": payload.get("symbol"), "trade_date": trade_date, "data_cutoff_time": cutoff, "data_completeness_pct": payload.get("data_completeness_pct"), "is_realtime": bool(payload.get("is_realtime")), "cache_used": bool(payload.get("cache_used")), "regime": battle.get("regime", UNKNOWN), "market_reward": battle.get("market_reward"), "market_penalty": battle.get("market_penalty"), "payload": _json(payload)}
                if row is None:
                    session.add(RociBattlefieldSnapshot(snapshot_key=snapshot_key, **values))
                else:
                    for key, value in values.items():
                        setattr(row, key, value)
                # Recomputing an identical source cutoff replaces the derived
                # graph instead of appending duplicates. This keeps refreshes
                # idempotent and bounds storage growth on a personal database.
                stress_event_ids = list((await session.execute(select(RociStressEvent.id).where(RociStressEvent.snapshot_key == snapshot_key))).scalars().all())
                if stress_event_ids:
                    await session.execute(delete(RociStressResponse).where(RociStressResponse.stress_event_id.in_(stress_event_ids)))
                action_ids = list((await session.execute(select(RociAction.id).where(RociAction.snapshot_key == snapshot_key))).scalars().all())
                if action_ids:
                    await session.execute(delete(RociActionEvidence).where(RociActionEvidence.action_id.in_(action_ids)))
                for model in (
                    RociSkillRun,
                    RociForce,
                    RociForceHistory,
                    RociPrimaryContradiction,
                    RociRiskPricing,
                    RociStressEvent,
                    RociRiskOpportunityConversion,
                    RociPatternHit,
                    RociAsymmetryScore,
                    RociAction,
                    RociModelRiskEvent,
                ):
                    await session.execute(delete(model).where(model.snapshot_key == snapshot_key))
                explanation_ids = list((await session.execute(select(RociExplanation.id).where(RociExplanation.snapshot_key == snapshot_key))).scalars().all())
                if explanation_ids:
                    for model in (RociExplanationDriver, RociExplanationEvidence, RociExplanationAlternative, RociExplanationChain, RociExplanationValidation):
                        await session.execute(delete(model).where(model.explanation_id.in_(explanation_ids)))
                await session.execute(delete(RociExplanation).where(RociExplanation.snapshot_key == snapshot_key))
                for item in skill_runs:
                    session.add(RociSkillRun(snapshot_key=snapshot_key, skill_id=item["skill_id"], symbol=payload.get("symbol"), trade_date=trade_date, snapshot_time=_now(), triggered=item["triggered"], score=item["score"], confidence=item["confidence"], contribution=item["contribution"], evidence=_json(item["evidence"]), state=_json(item["state"])))
                for item in force_map.get("forces") or []:
                    session.add(RociForce(snapshot_key=snapshot_key, force_id=item["force_id"], scope=item["scope"], name=item["name"], side=item["side"], strength=item.get("strength"), direction=item.get("direction", UNKNOWN), confidence=item.get("confidence"), persistence=item.get("persistence"), relevance=item.get("relevance"), evidence=_json(item.get("evidence") or []), skills=item.get("skills") or []))
                    session.add(RociForceHistory(force_id=item["force_id"], snapshot_key=snapshot_key, observed_at=_now(), side=item["side"], strength=item.get("strength"), direction=item.get("direction", UNKNOWN), evidence=_json(item.get("evidence") or [])))
                session.add(RociPrimaryContradiction(snapshot_key=snapshot_key, statement=primary.get("statement", UNKNOWN), candidate_key=primary.get("candidate_key", UNKNOWN), confidence=primary.get("confidence"), secondary_risks=primary.get("secondary_risks") or [], supporting_evidence=_json(primary.get("supporting_evidence") or []), opposing_evidence=_json(primary.get("opposing_evidence") or []), what_would_resolve=primary.get("what_would_resolve") or [], what_would_worsen=primary.get("what_would_worsen") or [], status=primary.get("status", "OBSERVING")))
                for item in pricing.get("risks") or []:
                    session.add(RociRiskPricing(snapshot_key=snapshot_key, risk_key=item["risk"], risk_name=item["risk"], event_strength=item.get("value"), price_response=None, relative_response=None, recovery_speed=None, pricing_state=item.get("state", UNKNOWN), evidence=_json(item.get("evidence") or [])))
                stress_ids: list[int] = []
                for item in stress.get("events") or []:
                    event = RociStressEvent(snapshot_key=snapshot_key, event_key=f"{item.get('date')}:{item.get('event')}", event_name=item.get("event", "压力事件"), event_date=_date(item.get("date")), severity=item.get("severity"), source="stock_daily_bars", expected_response="压力后相对承接与恢复", evidence=_json(item.get("evidence") or []))
                    session.add(event)
                    await session.flush()
                    stress_ids.append(event.id)
                    session.add(RociStressResponse(stress_event_id=event.id, actual_response=item.get("actual_response"), relative_response=item.get("relative_response"), recovery_speed=item.get("recovery_speed"), post_stress_followthrough=item.get("post_stress_followthrough"), resilience_state=item.get("state", UNKNOWN), evidence=_json(item.get("evidence") or [])))
                session.add(RociRiskOpportunityConversion(snapshot_key=snapshot_key, risk_event=primary.get("statement", UNKNOWN), price_response=stress.get("summary"), supply_demand_response=(context.get("daily") or {}).get("source"), relative_strength=asym.get("score"), follow_through=stress.get("confidence"), conversion_state="RISK_TO_OPPORTUNITY" if stress.get("state") == "ANTIFRAGILE" and asym.get("status") == "FAVORABLE" else "RISK_BEING_ABSORBED" if stress.get("state") == "RESILIENT" else "RISK_UNRESOLVED", evidence=_json((stress.get("events") or [])[:3]), invalidations=decision.get("invalidations") or []))
                for pattern in opp.get("patterns") or []:
                    session.add(RociPatternHit(pattern_id=pattern["pattern_id"], snapshot_key=snapshot_key, symbol=payload.get("symbol"), observed_at=_now(), triggered=bool(pattern.get("triggered")), score=pattern.get("score"), confidence=pattern.get("confidence"), evidence=_json(pattern.get("evidence") or [])))
                session.add(RociAsymmetryScore(snapshot_key=snapshot_key, symbol=payload.get("symbol"), invalidation_distance=asym.get("invalidation_distance"), expected_upside=asym.get("expected_upside"), expected_downside=asym.get("expected_downside"), estimated_win_probability=asym.get("estimated_win_probability"), reward_risk_ratio=asym.get("reward_risk_ratio"), liquidity_risk=asym.get("liquidity_risk"), gap_risk=asym.get("gap_risk"), tail_risk=asym.get("tail_risk"), time_cost=asym.get("time_cost"), score=asym.get("score"), status=asym.get("status", UNKNOWN), evidence=_json(asym.get("evidence") or [])))
                action_row = RociAction(snapshot_key=snapshot_key, symbol=payload.get("symbol"), action=decision["action"], reason=decision["reason"], confidence=decision.get("confidence"), risk_budget=decision.get("risk_budget"), invalidations=decision.get("invalidations") or [], next_checks=decision.get("next_checks") or [], shadow_excluded=decision.get("shadow_excluded") or [])
                session.add(action_row)
                await session.flush()
                for item in decision.get("evidence") or []:
                    session.add(RociActionEvidence(action_id=action_row.id, evidence_type=item.get("type", "INFERENCE"), label=item.get("label", ""), value=item.get("value"), source=item.get("source"), as_of=cutoff, supports=bool(item.get("supports", True))))
                for risk_item in cognition.get("model_risks") or []:
                    session.add(RociModelRiskEvent(snapshot_key=snapshot_key, risk_type=risk_item.get("risk_type", "MODEL_RISK"), severity=risk_item.get("severity", "MEDIUM"), status="OPEN", message=str(risk_item.get("risk") or "ROCI模型风险"), evidence=_json(risk_item.get("evidence") or [])))
                for entity_type, explanation in (payload.get("explanations") or {}).items():
                    if not isinstance(explanation, dict):
                        continue
                    result = explanation.get("result") or {}
                    why = explanation.get("why") or {}
                    quality = explanation.get("data_quality") or {}
                    explanation_row = RociExplanation(
                        snapshot_key=snapshot_key,
                        entity_type=str(entity_type),
                        entity_id=str(explanation.get("entity_id") or payload.get("symbol") or "market"),
                        as_of=cutoff,
                        conclusion_code=result.get("type"),
                        conclusion_label=result.get("label"),
                        summary=why.get("summary"),
                        confidence=result.get("confidence"),
                        explanation_version=str(explanation.get("version") or "roci-explanation-v1.1.2"),
                        data_quality=_json(quality),
                    )
                    session.add(explanation_row)
                    await session.flush()
                    for driver in why.get("primary_drivers") or []:
                        session.add(RociExplanationDriver(explanation_id=explanation_row.id, driver_name=str(driver.get("name") or "UNKNOWN"), direction=driver.get("direction"), importance=driver.get("importance"), evidence_strength=driver.get("evidence_strength"), description=driver.get("description"), metrics=driver.get("source_metrics") or []))
                    for item in (why.get("supporting_evidence") or []) + (why.get("counter_evidence") or []):
                        session.add(RociExplanationEvidence(explanation_id=explanation_row.id, evidence_type=str(item.get("type") or "EVIDENCE"), claim=str(item.get("claim") or "UNKNOWN"), evidence_strength=item.get("evidence_strength"), evidence_grade=item.get("evidence_grade"), source_table=item.get("source_table"), source_field=item.get("source_field"), source_timestamp=_date_time(item.get("source_timestamp")), raw_data=_json({"value": item.get("value"), "formula": item.get("formula")}), supports=bool(item.get("supports", True))))
                    for alternative in why.get("alternative_hypotheses") or []:
                        session.add(RociExplanationAlternative(explanation_id=explanation_row.id, hypothesis=str(alternative.get("hypothesis") or "UNKNOWN"), support_score=alternative.get("support_score"), supporting_evidence=alternative.get("supporting_evidence") or [], contradictions=alternative.get("contradictions") or [], required_confirmation=alternative.get("required_confirmation") or []))
                    for index, link in enumerate(why.get("transmission_chain") or [], 1):
                        session.add(RociExplanationChain(explanation_id=explanation_row.id, step_order=index, from_node=str(link.get("from") or "UNKNOWN"), to_node=str(link.get("to") or "UNKNOWN"), status=str(link.get("status") or "INFERRED"), confidence=link.get("confidence"), evidence=link.get("evidence") or []))
                    for item in why.get("validation_signals") or []:
                        session.add(RociExplanationValidation(explanation_id=explanation_row.id, validation_type="VALIDATE", condition_text=str(item), horizon="next_observation", source_metric=None))
                    for item in why.get("invalidation_signals") or []:
                        session.add(RociExplanationValidation(explanation_id=explanation_row.id, validation_type="INVALIDATE", condition_text=str(item), horizon="next_observation", source_metric=None))
                cache = await session.get(MarketDataCache, ROCI_CACHE_KEY)
                if payload.get("symbol") is None:
                    if cache is None:
                        session.add(MarketDataCache(key=ROCI_CACHE_KEY, payload=_json(payload)))
                    else:
                        cache.payload = _json(payload)
                        cache.updated_at = _now()
                await session.commit()
        except Exception as exc:
            print(f"ROCI persistence failed: {type(exc).__name__}")

    def refresh_status(self) -> dict[str, Any]:
        """Return the single in-process refresh job used by all dashboards."""
        status = deepcopy(self._refresh_status)
        task = self._refresh_task
        if task is not None and not task.done() and status.get("status") not in {"queued", "running"}:
            status.update({"status": "running", "stage": "running", "message": "统一数据刷新进行中"})
        return status

    @staticmethod
    def _refresh_result(result: Any, *, fallback_status: str = "available") -> dict[str, Any]:
        if not isinstance(result, dict):
            return {"status": fallback_status}
        status = result.get("status")
        if status is None:
            status = "available" if result.get("count") is not None or result.get("timeline") or result.get("available") else fallback_status
        summary = {
            "status": str(status),
            "progress": result.get("progress"),
            "stage": result.get("stage"),
            "message": result.get("message"),
            "data_date": result.get("data_date") or result.get("forecast_date") or result.get("trade_date"),
            "generated_at": result.get("generated_at"),
            "cache_used": result.get("cache_used"),
            "warnings": result.get("warnings") or [],
        }
        if result.get("error"):
            summary["error"] = result.get("error")
        if result.get("count") is not None:
            summary["count"] = result.get("count")
        return {key: value for key, value in summary.items() if value is not None}

    async def _refresh_step(
        self,
        label: str,
        awaitable: Any,
        *,
        stage: str,
        progress: int,
        timeout: float,
        return_raw: bool = False,
        source_key: str | None = None,
    ) -> dict[str, Any]:
        self._refresh_status.update({
            "status": "running",
            "stage": stage,
            "progress": progress,
            "message": f"正在刷新{label}",
            "updated_at": _now().isoformat(),
        })
        raw_result: dict[str, Any] = {}
        try:
            result = await asyncio.wait_for(awaitable, timeout=timeout)
            raw_result = result if isinstance(result, dict) else {}
            summary = self._refresh_result(result)
        except asyncio.TimeoutError:
            summary = {"status": "timeout", "error": "TimeoutError", "fallback": "保留最近同口径缓存"}
        except Exception as exc:
            summary = {"status": "unavailable", "error": type(exc).__name__, "fallback": "保留最近同口径缓存"}
        self._refresh_status.setdefault("sources", {})[source_key or label] = summary
        self._refresh_status["updated_at"] = _now().isoformat()
        return raw_result if return_raw else summary

    async def request_refresh(self) -> dict[str, Any]:
        """Queue one serialized refresh for every board feeding the cockpit.

        The HTTP request only schedules the work and returns the current
        snapshot. This keeps a slow free data provider from blocking the page,
        while the status endpoint and the frontend poll expose the real
        progress and the final snapshot is persisted automatically.
        """
        await self.ensure_initialized()
        async with self._refresh_lock:
            if self._refresh_task is not None and not self._refresh_task.done():
                return self.refresh_status()
            self._refresh_status = {
                "status": "queued",
                "stage": "queued",
                "progress": 0,
                "message": "统一数据刷新已排队",
                "sources": {},
                "updated_at": _now().isoformat(),
            }
            self._refresh_task = asyncio.create_task(self._run_refresh_all())
        await asyncio.sleep(0)
        return self.refresh_status()

    async def _run_refresh_all(self) -> None:
        """Refresh linked sources serially, then rebuild one coherent ROCI snapshot."""
        from services.event_radar import event_radar_service
        from services.forecast_v5 import forecast_v5_service
        from services.market_decision_workbench import market_decision_workbench_service
        from services.market_way_v4 import market_way_v4_service
        from services.v51_microstructure_service import v51_microstructure_service

        try:
            await self._refresh_step(
                "事件雷达", event_radar_service.refresh(force=True),
                stage="event_radar", progress=8, timeout=50, source_key="event_radar",
            )
            await self._refresh_step(
                "竞价与微结构", v51_microstructure_service.auction_dashboard(refresh=True),
                stage="v51_auction", progress=18, timeout=70, source_key="v51_auction",
            )

            # V4 owns the long financial/PIT acquisition. Start it once and
            # wait through its service-level task so no second request can
            # start the same full-market scan concurrently.
            await self._refresh_step(
                "V4数据管线", market_way_v4_service.refresh_sources(background=True),
                stage="v4_pipeline", progress=25, timeout=20, source_key="v4_data_pipeline_start",
            )
            v4_status = await self._refresh_step(
                "V4数据管线收尾", market_way_v4_service.wait_for_refresh(timeout=180),
                stage="v4_pipeline", progress=32, timeout=185, source_key="v4_data_pipeline",
            )
            # The start marker is useful while the task is being queued, but
            # should not remain as a second contradictory status after the
            # authoritative V4 task has completed.
            self._refresh_status.setdefault("sources", {}).pop("v4_data_pipeline_start", None)
            self._refresh_status["sources"]["v4_data_pipeline"] = v4_status

            workbench_payload = await self._refresh_step(
                "V4决策工作台", market_decision_workbench_service.get(force=True),
                stage="workbench", progress=48, timeout=150, return_raw=True, source_key="workbench",
            )
            workbench = workbench_payload if workbench_payload.get("available") else None

            forecast_payload = await self._refresh_step(
                "V5多因子预测", forecast_v5_service.dashboard(
                    force=True,
                    include_skills=False,
                    workbench_override=workbench,
                ),
                stage="forecast_v5", progress=64, timeout=180, return_raw=True, source_key="forecast_v5",
            )

            # Re-read persisted caches after the upstream jobs. This is the
            # only context used for the final derived snapshot, so every ROCI
            # section shares one cutoff instead of mixing page-open times.
            context = await self._context(force=True, refresh_inputs=False)
            if workbench:
                context["workbench"] = workbench
                context.setdefault("source_status", {})["workbench"] = "available"
            if forecast_payload.get("timeline"):
                context["forecast"] = forecast_payload
                context.setdefault("source_status", {})["forecast_v5"] = "available"
            try:
                context["intraday"] = await asyncio.wait_for(
                    roci_intraday_service.current(force=False, context={"workbench": context.get("workbench")}),
                    timeout=35,
                )
            except Exception as exc:
                self._refresh_status["sources"]["intraday"] = {"status": "degraded", "error": type(exc).__name__, "fallback": "保留最近盘中快照"}
                context["intraday"] = {}

            payload = await self.build(
                force=True,
                include_intraday=True,
                refresh_linked=False,
                refresh_inputs=False,
                context_override=context,
                linked_refresh_override=self.refresh_status().get("sources") or {},
                persist=True,
            )
            self._refresh_status.update({
                "status": "completed",
                "stage": "completed",
                "progress": 100,
                "message": "所有看板数据源已完成统一刷新；失败源保留同口径缓存",
                "snapshot_key": payload.get("snapshot_key"),
                "updated_at": _now().isoformat(),
            })
        except asyncio.CancelledError:
            self._refresh_status.update({"status": "cancelled", "stage": "cancelled", "message": "统一刷新被取消", "updated_at": _now().isoformat()})
            raise
        except Exception as exc:
            self._refresh_status.update({
                "status": "completed_with_gaps",
                "stage": self._refresh_status.get("stage") or "unknown",
                "message": "统一刷新部分完成，页面继续使用最近同口径缓存",
                "error": type(exc).__name__,
                "updated_at": _now().isoformat(),
            })

    def _attach_refresh_report(self, payload: dict[str, Any], *, requested: bool = False) -> dict[str, Any]:
        result = deepcopy(payload)
        report = dict(result.get("refresh_report") or {})
        report["requested"] = requested or bool(report.get("requested"))
        report["coordinator"] = self.refresh_status()
        report["linked_sources"] = {
            **(report.get("linked_sources") or {}),
            **(self.refresh_status().get("sources") or {}),
        }
        report["policy"] = "看板一次刷新所有依赖源；实时源失败时显示真实降级状态并使用最近同口径缓存。"
        result["refresh_report"] = report
        return result

    async def _cached_dashboard(self) -> dict[str, Any] | None:
        """Read the last coherent snapshot without waking upstream services."""
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, ROCI_CACHE_KEY)
            payload = dict(row.payload) if row and isinstance(row.payload, dict) else None
            return payload if payload and payload.get("snapshot_key") else None
        except Exception:
            return None

    async def dashboard(self, *, force: bool = False) -> dict[str, Any]:
        if force:
            await self.request_refresh()
        # A forced click never waits for a network provider. It returns the
        # latest coherent snapshot immediately; the background coordinator
        # replaces it and the frontend reloads once the job reaches completed.
        payload = await self._cached_dashboard() if force else None
        if payload is None:
            payload = await self.build(force=False, include_intraday=True)
        return self._attach_refresh_report(payload, requested=force)

    async def battlefield(self, *, force: bool = False) -> dict[str, Any]:
        payload = await self.build(force=force)
        return {"snapshot_key": payload.get("snapshot_key"), "trade_date": payload.get("trade_date"), "data_cutoff_time": payload.get("data_cutoff_time"), "explanation": (payload.get("explanations") or {}).get("battlefield"), **(payload.get("battlefield") or {})}

    async def forces(self, *, force: bool = False) -> dict[str, Any]:
        payload = await self.build(force=force)
        return {"snapshot_key": payload.get("snapshot_key"), **(payload.get("forces") or {})}

    async def contradiction(self, *, force: bool = False) -> dict[str, Any]:
        payload = await self.build(force=force)
        return {"snapshot_key": payload.get("snapshot_key"), "explanation": (payload.get("explanations") or {}).get("contradiction"), **(payload.get("primary_contradiction") or {})}

    async def risk_pricing(self, *, force: bool = False) -> dict[str, Any]:
        payload = await self.build(force=force)
        return {"snapshot_key": payload.get("snapshot_key"), "explanation": (payload.get("explanations") or {}).get("risk_pricing"), **(payload.get("risk_pricing") or {})}

    async def stress_tests(self, *, force: bool = False, symbol: str | None = None) -> dict[str, Any]:
        payload = await self.build(force=force, symbol=symbol)
        return {"snapshot_key": payload.get("snapshot_key"), "explanation": (payload.get("explanations") or {}).get("market"), **(payload.get("stress_test") or {})}

    async def cognitive_risk(self, *, force: bool = False) -> dict[str, Any]:
        payload = await self.build(force=force)
        return {"snapshot_key": payload.get("snapshot_key"), **(payload.get("cognitive_risk") or {})}

    async def opportunities(self, *, force: bool = False) -> dict[str, Any]:
        payload = await self.build(force=force)
        return {
            "snapshot_key": payload.get("snapshot_key"),
            "trade_date": payload.get("trade_date"),
            "data_cutoff_time": payload.get("data_cutoff_time"),
            "audit": payload.get("audit") or {},
            "explanation": (payload.get("explanations") or {}).get("opportunities"),
            **(payload.get("opportunities") or {}),
        }

    async def recommendations(self, *, force: bool = False) -> dict[str, Any]:
        payload = await self.build(force=force)
        return {
            "snapshot_key": payload.get("snapshot_key"),
            "data_cutoff_time": payload.get("data_cutoff_time"),
            "explanation": (payload.get("explanations") or {}).get("recommendations"),
            **((payload.get("opportunities") or {}).get("risk_adapted") or {}),
        }

    async def explanation(self, entity_type: str, entity_id: str = "market", *, force: bool = False) -> dict[str, Any]:
        payload = await self.build(force=force, symbol=entity_id if entity_type == "stock" else None)
        explanations = payload.get("explanations") or {}
        return explanations.get(entity_type) or explanations.get("market") or {
            "version": "roci-explanation-v1.1.2",
            "entity_type": entity_type,
            "entity_id": entity_id,
            "result": {"type": "UNKNOWN", "label": "UNKNOWN", "score": None, "confidence": None},
            "why": {"summary": "当前没有可用解释快照。", "facts": [], "primary_drivers": [], "supporting_evidence": [], "counter_evidence": [], "alternative_hypotheses": [], "transmission_chain": [], "validation_signals": [], "invalidation_signals": []},
            "data_quality": {"score": 0, "score_pct": 0, "missing_fields": ["explanation"], "conflicting_sources": []},
        }

    async def explanation_section(self, entity_type: str, entity_id: str, section: str, *, force: bool = False) -> dict[str, Any]:
        explanation = await self.explanation(entity_type, entity_id, force=force)
        if section == "drivers":
            return {"entity_type": entity_type, "entity_id": entity_id, "items": (explanation.get("why") or {}).get("primary_drivers") or [], "contribution_note": (explanation.get("why") or {}).get("contribution_note")}
        if section == "evidence":
            why = explanation.get("why") or {}
            return {"entity_type": entity_type, "entity_id": entity_id, "supporting": why.get("supporting_evidence") or [], "counter": why.get("counter_evidence") or [], "lineage": explanation.get("lineage") or []}
        if section == "alternatives":
            return {"entity_type": entity_type, "entity_id": entity_id, "items": (explanation.get("why") or {}).get("alternative_hypotheses") or []}
        if section == "chain":
            return {"entity_type": entity_type, "entity_id": entity_id, "items": (explanation.get("why") or {}).get("transmission_chain") or []}
        if section == "lineage":
            return {"entity_type": entity_type, "entity_id": entity_id, "items": explanation.get("lineage") or []}
        return explanation

    async def weekly_scenario_explanation(self, forecast_id: str, *, force: bool = False) -> dict[str, Any]:
        """Build an evidence-bound explanation for one V5 weekly scenario.

        The forecast service remains the only owner of formal probabilities.
        This method reads its result and adds reasons, counter-evidence and
        validation conditions without changing the forecast payload.
        """
        from services.forecast_v5 import forecast_v5_service

        forecast = await forecast_v5_service.dashboard(force=force, include_skills=False)
        timeline = forecast.get("timeline") or []
        selected_horizon = next((item for item in timeline if str(item.get("horizon") or item.get("id")) == forecast_id or str(item.get("id")) == forecast_id), None)
        scenario = None
        if selected_horizon:
            scenario = next((item for item in selected_horizon.get("scenarios") or [] if item.get("id") == "main"), None)
        if scenario is None:
            for horizon in timeline:
                candidate = next((item for item in horizon.get("scenarios") or [] if str(item.get("id")) == forecast_id), None)
                if candidate:
                    selected_horizon, scenario = horizon, candidate
                    break
        if scenario is None:
            return {"status": "UNKNOWN", "forecast_id": forecast_id, "reason": "没有找到该周度剧本快照；系统不使用当前数据替代历史剧本。"}

        factors = forecast.get("factors") or {}
        leading = factors.get("leading") or []
        propagation = factors.get("propagation") or []
        all_factors = [item for item in [*leading, *propagation, *(factors.get("confirmation") or [])] if isinstance(item, dict)]
        observed_factors = [item for item in all_factors if item.get("observed") or item.get("value") is not None]
        fact_items = [
            {"type": "FACT", "claim": f"剧本概率 {scenario.get('probability_pct', 'UNKNOWN')}%", "value": scenario.get("probability_pct"), "source_table": "forecast_v5", "source_field": "probability_pct", "supports": True},
            *[{"type": "FACT", "claim": str(item.get("name") or item.get("factor_id") or "因子"), "value": item.get("value", item.get("state", "UNKNOWN")), "source_table": str(item.get("source") or "factor_registry"), "source_field": str(item.get("factor_id") or "value"), "supports": True} for item in observed_factors[:8]],
        ]
        drivers = []
        for index, item in enumerate(observed_factors[:4]):
            drivers.append({
                "name": item.get("name") or item.get("factor_id") or "主要因子",
                "direction": item.get("direction") or "MIXED",
                "importance": round(max(0.0, 0.34 - index * 0.06), 2),
                "evidence_strength": item.get("reliability") or item.get("data_quality") or 0.6,
                "description": item.get("explanation") or item.get("definition") or "当前因子已被结构化引擎纳入剧本判断。",
                "source_metrics": [item.get("factor_id") or item.get("id") or "UNKNOWN"],
            })
        while len(drivers) < 3:
            drivers.append({"name": "剧本验证条件", "direction": "MIXED", "importance": 0.1, "evidence_strength": None, "description": "该驱动尚缺足够可审计输入，保持 UNKNOWN。", "source_metrics": []})
        scenario_payload = {
            "regime": (forecast.get("risk_preference") or {}).get("label") or "UNKNOWN",
            "battlefield": {"regime": (forecast.get("risk_preference") or {}).get("state") or "UNKNOWN"},
            "primary_contradiction": {"statement": "周度剧本能否由关键因子与后续市场响应共同验证", "supporting_evidence": fact_items[:4], "opposing_evidence": [{"type": "COUNTER_EVIDENCE", "claim": "未来事件和未观测因子可能改变当前路径", "value": "UNKNOWN", "source_table": "forecast_v5", "source_field": "audit", "supports": False}]},
            "risk_pricing": {"status": (forecast.get("risk_preference") or {}).get("label") or "UNKNOWN"},
            "stress_test": {"state": "UNKNOWN"},
            "facts": fact_items,
            "data_completeness_pct": ((forecast.get("data_health") or {}).get("completeness_pct") or (forecast.get("data_health") or {}).get("coverage_pct")),
            "source_status": {"forecast_v5": "available" if forecast else "unavailable"},
            "action": {"confidence": None},
        }
        explanation = build_explanation(
            scenario_payload,
            entity_type="weekly_scenario",
            entity_id=forecast_id,
            result_override={"type": "WEEKLY_SCENARIO", "label": scenario.get("label") or forecast_id, "score": scenario.get("probability_pct"), "confidence": None},
        )
        why = explanation.setdefault("why", {})
        why["summary"] = f"{scenario.get('label') or '当前剧本'}的正式概率为 {scenario.get('probability_pct', 'UNKNOWN')}%；以下是结构化因子支持、反证与验证条件，不代表未来必然重复。"
        why["primary_drivers"] = drivers
        why["supporting_evidence"] = fact_items[:10]
        why["counter_evidence"] = [{"type": "COUNTER_EVIDENCE", "claim": item, "value": "待验证", "source": "forecast_v5.scenario", "supports": False} for item in (scenario.get("invalidation_points") or [])[:6]] or why.get("counter_evidence")
        why["validation_signals"] = scenario.get("verification_points") or scenario.get("trigger_conditions") or ["等待下一个交易窗口验证"]
        why["invalidation_signals"] = scenario.get("invalidation_points") or ["关键因子方向反转且市场响应不再支持该剧本"]
        why["transmission_chain"] = [{"from": "领先/传播因子", "to": scenario.get("label") or "周度剧本", "status": "SUPPORTED", "confidence": None, "evidence": scenario.get("key_factors") or []}, {"from": scenario.get("label") or "周度剧本", "to": "验证或失效", "status": "HYPOTHESIS", "confidence": None, "evidence": scenario.get("verification_points") or []}]
        return {"status": "AVAILABLE", "forecast_id": forecast_id, "horizon": selected_horizon.get("horizon") if selected_horizon else None, "scenario": scenario, "formal_probability_unchanged": True, "explanation": explanation}

    async def opportunity(self, pattern_id: str, *, force: bool = False) -> dict[str, Any]:
        data = await self.opportunities(force=force)
        item = next((item for item in data.get("patterns") or [] if item.get("pattern_id") == pattern_id or item.get("name") == pattern_id), None)
        if item is None:
            return {"error": "PATTERN_NOT_FOUND", "pattern": pattern_id}
        async with async_session() as session:
            hits = list((await session.execute(select(RociPatternHit).where(RociPatternHit.pattern_id == item["pattern_id"]).order_by(desc(RociPatternHit.observed_at)).limit(30))).scalars().all())
            registry = await session.get(RociOpportunityPattern, item["pattern_id"])
        return {**item, "detection_rule": registry.detection_rule if registry else {}, "applicable_regimes": registry.applicable_regimes if registry else [], "validation": registry.validation_summary if registry else {}, "history": [{"snapshot_key": row.snapshot_key, "symbol": row.symbol, "observed_at": row.observed_at.isoformat() if row.observed_at else None, "triggered": row.triggered, "score": row.score, "confidence": row.confidence, "evidence": row.evidence, "outcome": row.outcome} for row in hits], "unverified_label": "未验证" if not registry or not (registry.validation_summary or {}).get("sample_size") else None}

    async def stock(self, symbol: str, *, force: bool = False, as_of: date | None = None) -> dict[str, Any]:
        code = normalize_stock_code(symbol)
        payload = await self.build(force=force, symbol=code, as_of=as_of)
        return payload

    async def skills(self, *, force: bool = False, status: str | None = None, category: str | None = None) -> dict[str, Any]:
        payload = await self.build(force=force)
        items = list((payload.get("skills") or {}).get("items") or [])
        if status:
            items = [item for item in items if item.get("status") == status]
        if category:
            items = [item for item in items if item.get("category") == category]
        source_summary: dict[str, dict[str, Any]] = {}
        for item in items:
            key = item.get("source_key") or "unknown"
            summary = source_summary.setdefault(key, {"source_key": key, "name": item.get("source_name") or key, "skill_count": 0, "triggered_count": 0, "active_count": 0})
            summary["skill_count"] += 1
            summary["triggered_count"] += int(bool(item.get("triggered")))
            summary["active_count"] += int(item.get("status") == "ACTIVE" and item.get("enabled", True))
        return {"version": ROCI_VERSION, "count": len(items), "items": items, "data_cutoff_time": payload.get("data_cutoff_time"), "source_registry": [{"source_key": item["key"], "name": item["name"], "type": item["type"]} for item in SOURCE_DEFINITIONS], "source_summary": list(source_summary.values()), "note": "Skill 是证据，不是投票；SHADOW 不影响 ACTION。"}

    async def skill_detail(self, skill_id: str, *, force: bool = False) -> dict[str, Any]:
        skill = skill_by_id(skill_id)
        if skill is None:
            return {"error": "SKILL_NOT_FOUND", "skill_id": skill_id}
        payload = await self.build(force=force)
        runtime = next((item for item in (payload.get("skills") or {}).get("items") or [] if item.get("skill_id") == skill_id), None)
        async with async_session() as session:
            runs = list((await session.execute(select(RociSkillRun).where(RociSkillRun.skill_id == skill_id).order_by(desc(RociSkillRun.snapshot_time)).limit(30))).scalars().all())
            row = await session.get(RociSkill, skill_id)
        return {**skill, "runtime": runtime, "availability": (runtime or {}).get("state", {}).get("availability") or {"available": False, "requirements": skill.get("data_requirements") or [], "missing": skill.get("data_requirements") or [], "reason": "尚未运行"}, "performance": {"validation_status": row.validation_status if row else "NOT_TESTED", "sample_size": row.sample_size if row else 0, "hit_rate": row.hit_rate if row else None, "profit_factor": row.profit_factor if row else None, "expectancy_r": row.expectancy_r if row else None, "max_drawdown": row.max_drawdown if row else None, "unverified_label": "未验证" if not row or not row.sample_size else None}, "runs": [{"snapshot_key": item.snapshot_key, "symbol": item.symbol, "trade_date": item.trade_date.isoformat() if item.trade_date else None, "snapshot_time": item.snapshot_time.isoformat() if item.snapshot_time else None, "triggered": item.triggered, "score": item.score, "confidence": item.confidence, "contribution": item.contribution, "evidence": item.evidence, "state": item.state} for item in runs]}

    async def lab_skills(self) -> dict[str, Any]:
        async with async_session() as session:
            rows = list((await session.execute(select(RociSkill).order_by(RociSkill.skill_id))).scalars().all())
        return {"items": [{"skill_id": row.skill_id, "name": row.name, "status": row.status, "validation_status": row.validation_status, "sample_size": row.sample_size, "hit_rate": row.hit_rate, "profit_factor": row.profit_factor, "expectancy_r": row.expectancy_r, "max_drawdown": row.max_drawdown, "eligible_for_promotion": False if row.status in {"SHADOW", "DETECT_ONLY", "KNOWLEDGE_ONLY"} else False} for row in rows], "promotion_policy": ["规则可计算", "PIT", "不可成交与成本", "Walk-forward", "样本外", "跨生态稳定", "衰减监测"]}

    async def backtest_skill(self, skill_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        skill = skill_by_id(skill_id)
        if skill is None:
            return {"status": "NOT_FOUND", "skill_id": skill_id}
        # A generic backtest must not turn an unimplemented source claim into
        # a fabricated statistic.  Return a machine-readable gate report.
        return {"status": "NOT_VALIDATED", "skill_id": skill_id, "skill_status": skill["status"], "parameters": params or {}, "metrics": {"sample_size": None, "hit_rate": None, "profit_factor": None, "expectancy_r": None, "max_drawdown": None}, "gates": {"rule_computable": False, "pit": False, "cost_model": False, "walk_forward": False, "out_of_sample": False, "regime_coverage": False, "decay_monitor": False}, "reason": "该 Skill 尚未绑定专用 PIT 规则和验证数据，系统拒绝伪造回测结果。"}

    async def promote_skill(self, skill_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        async with async_session() as session:
            row = await session.get(RociSkill, skill_id)
            if row is None:
                return {"status": "NOT_FOUND", "skill_id": skill_id}
            return {"status": "REJECTED", "skill_id": skill_id, "current_status": row.status, "reason": "未满足规则可计算、PIT、成本、Walk-forward、样本外和衰减全部通过的晋级门槛。", "required_gates": ["rule_computable", "pit", "cost_model", "walk_forward", "out_of_sample", "regime_coverage", "decay_monitor"], "submitted": payload or {}}

    async def disable_skill(self, skill_id: str) -> dict[str, Any]:
        async with async_session() as session:
            row = await session.get(RociSkill, skill_id)
            if row is None:
                return {"status": "NOT_FOUND", "skill_id": skill_id}
            row.status = "DISABLED"
            row.enabled = False
            await session.commit()
            # A cached market payload may contain the previous runtime status;
            # invalidate it so a disable takes effect immediately.
            try:
                async with async_session() as cache_session:
                    cache = await cache_session.get(MarketDataCache, ROCI_CACHE_KEY)
                    if cache is not None:
                        await cache_session.delete(cache)
                        await cache_session.commit()
            except Exception:
                pass
            return {"status": "DISABLED", "skill_id": skill_id}

    async def replay(self, *, symbol: str | None, trade_date: date) -> dict[str, Any]:
        async with async_session() as session:
            query = select(RociBattlefieldSnapshot).where(RociBattlefieldSnapshot.trade_date <= trade_date)
            if symbol:
                query = query.where(RociBattlefieldSnapshot.symbol == normalize_stock_code(symbol))
            else:
                query = query.where(RociBattlefieldSnapshot.symbol.is_(None))
            row = (await session.execute(query.order_by(desc(RociBattlefieldSnapshot.trade_date), desc(RociBattlefieldSnapshot.created_at)).limit(1))).scalar_one_or_none()
        if row is None:
            return {"status": "UNKNOWN", "trade_date": trade_date.isoformat(), "symbol": symbol, "reason": "没有该日期已保存的 ROCI 快照；为避免未来数据污染，系统不使用当前数据冒充历史。"}
        payload = deepcopy(row.payload or {})
        payload["replay"] = {"status": "RECONSTRUCTED_FROM_SNAPSHOT", "requested_date": trade_date.isoformat(), "source_snapshot_date": row.trade_date.isoformat(), "no_future_data": True}
        replay_id = f"replay-{uuid4().hex[:20]}"
        try:
            async with async_session() as session:
                session.add(RociReplay(replay_id=replay_id, symbol=row.symbol, trade_date=trade_date, requested_at=_now(), data_cutoff_time=row.data_cutoff_time, status="COMPLETED", snapshot_payload=_json(payload)))
                await session.commit()
        except Exception:
            pass
        return payload

    async def feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot_key = str(payload.get("snapshot_key") or "")
        if not snapshot_key:
            return {"status": "REJECTED", "reason": "snapshot_key必填"}
        try:
            async with async_session() as session:
                session.add(RociUserFeedback(snapshot_key=snapshot_key, user_key=str(payload.get("user_key") or "default"), target=payload.get("target"), rating=payload.get("rating"), action=payload.get("action"), note=payload.get("note")))
                await session.commit()
            return {"status": "RECORDED", "snapshot_key": snapshot_key}
        except Exception as exc:
            return {"status": "ERROR", "reason": type(exc).__name__}


roci_service = RociService()
