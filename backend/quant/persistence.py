"""Durable persistence for user-authored quantitative strategies.

The quantitative engine still consumes a synchronous JSON cache, but the
database is the source of truth for strategy CRUD.  This keeps the existing
scan/backtest code compatible while making strategies survive Render
restarts, sleeps, and redeploys.
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database import async_session
from models import QuantStrategy
from quant.engine import _validate_strategy, build_strategy
from quant.schemas import StrategyCreate, StrategyUpdate
from quant.storage import quant_store
from quant.templates import BUILTIN_STRATEGIES
from services.data_collector import shanghai_now


class StrategyPersistenceError(RuntimeError):
    """Raised when the durable strategy store cannot be synchronized."""


STRATEGY_METADATA_FIELDS = (
    "description",
    "horizon",
    "target_win_rate",
    "validation_note",
)

GOVERNANCE_DRAFT = "DRAFT"
GOVERNANCE_APPROVED = "APPROVED"
GOVERNANCE_MODIFIED = "MODIFIED_PENDING_REVIEW"
GOVERNANCE_LEGACY = "LEGACY_GRANDFATHERED"
SUBSTANTIVE_STRATEGY_FIELDS = ("filter", "entry", "exit", "position", "scan_schedule")


def strategy_fingerprint(strategy: dict[str, Any]) -> str:
    """Stable hash of fields that change what a scan can do."""
    body = {key: copy.deepcopy(strategy.get(key)) for key in SUBSTANTIVE_STRATEGY_FIELDS}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(uuid.uuid5(uuid.NAMESPACE_OID, encoded))


def _governance_template(
    strategy: dict[str, Any],
    *,
    status: str,
    approved: bool,
    source: str,
    legacy_grandfathered: bool = False,
) -> dict[str, Any]:
    now = str(strategy.get("updated_at") or shanghai_now().isoformat())
    return {
        "status": status,
        "approved": approved,
        "legacy_grandfathered": legacy_grandfathered,
        "source": source,
        "approved_at": now if approved else None,
        "approval_note": "存量策略兼容：沿用原有定时扫描许可" if legacy_grandfathered else None,
        "approved_fingerprint": strategy_fingerprint(strategy) if approved else None,
        "rules_updated_at": now,
        "last_auditable_backtest": None,
        "revoked_at": None,
        "revocation_note": None,
    }


def _normalize_governance(strategy: dict[str, Any], *, legacy_if_missing: bool = True) -> dict[str, Any]:
    current = strategy.get("governance")
    if not isinstance(current, dict):
        if legacy_if_missing:
            return _governance_template(
                strategy,
                status=GOVERNANCE_LEGACY,
                approved=True,
                source="builtin" if strategy.get("builtin") else "legacy",
                legacy_grandfathered=True,
            )
        return _governance_template(
            strategy,
            status=GOVERNANCE_DRAFT,
            approved=False,
            source="user",
        )
    normalized = copy.deepcopy(current)
    normalized.setdefault("status", GOVERNANCE_DRAFT)
    normalized.setdefault("approved", normalized.get("status") in {GOVERNANCE_APPROVED, GOVERNANCE_LEGACY})
    normalized.setdefault("legacy_grandfathered", normalized.get("status") == GOVERNANCE_LEGACY)
    normalized.setdefault("source", "builtin" if strategy.get("builtin") else "user")
    normalized.setdefault("approved_at", None)
    normalized.setdefault("approval_note", None)
    normalized.setdefault("approved_fingerprint", None)
    normalized.setdefault("rules_updated_at", str(strategy.get("updated_at") or shanghai_now().isoformat()))
    normalized.setdefault("last_auditable_backtest", None)
    normalized.setdefault("revoked_at", None)
    normalized.setdefault("revocation_note", None)
    return normalized


def _compact_backtest_summary(result: dict[str, Any]) -> dict[str, Any]:
    data_quality = result.get("data_quality") or {}
    return {
        "job_id": result.get("job_id"),
        "strategy_fingerprint": result.get("strategy_fingerprint"),
        "completed_at": result.get("completed_at"),
        "period": copy.deepcopy(result.get("period") or {}),
        "total_return": result.get("total_return"),
        "annual_return": result.get("annual_return"),
        "win_rate": result.get("win_rate"),
        "profit_loss_ratio": result.get("profit_loss_ratio"),
        "max_drawdown": result.get("max_drawdown"),
        "sharpe_ratio": result.get("sharpe_ratio"),
        "trade_count": result.get("trade_count"),
        "completed_trade_count": result.get("completed_trade_count"),
        "passed": result.get("passed"),
        "data_quality": {
            "grade": data_quality.get("grade"),
            "audit_eligible": bool(data_quality.get("audit_eligible")),
            "cached_bar_stocks": data_quality.get("cached_bar_stocks"),
            "warnings": list(data_quality.get("warnings") or [])[:8],
        },
    }


def _latest_auditable_backtest(strategy: dict[str, Any]) -> dict[str, Any] | None:
    strategy_id = str(strategy["id"])
    current_fingerprint = strategy_fingerprint(strategy)
    governance = _normalize_governance(strategy)
    rules_updated_at = str(governance.get("rules_updated_at") or "")
    candidates = [
        item for item in quant_store.list_backtest_results()
        if item.get("strategy_id") == strategy_id
        and bool((item.get("data_quality") or {}).get("audit_eligible"))
        and (
            item.get("strategy_fingerprint") == current_fingerprint
            or (
                not item.get("strategy_fingerprint")
                and rules_updated_at
                and _timestamp_is_not_earlier(item.get("completed_at"), rules_updated_at)
            )
        )
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: str(item.get("completed_at") or ""))
    return _compact_backtest_summary(latest)


def _timestamp_is_not_earlier(value: object, baseline: object) -> bool:
    """Compare ISO timestamps only when both values are explicit and valid."""
    try:
        value_text = str(value or "").strip()
        baseline_text = str(baseline or "").strip()
        if not value_text or not baseline_text:
            return False
        value_dt = datetime.fromisoformat(value_text)
        baseline_dt = datetime.fromisoformat(baseline_text)
        if value_dt.tzinfo is None:
            value_dt = value_dt.replace(tzinfo=baseline_dt.tzinfo)
        if baseline_dt.tzinfo is None:
            baseline_dt = baseline_dt.replace(tzinfo=value_dt.tzinfo)
        return value_dt >= baseline_dt
    except (TypeError, ValueError):
        return False


def _strategy_from_row(row: QuantStrategy) -> dict:
    payload = copy.deepcopy(row.payload or {})
    payload.update({
        "id": row.id,
        "name": row.name,
        "builtin": bool(row.is_builtin),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    })
    payload["governance"] = _normalize_governance(payload)
    return payload


def _row_from_strategy(strategy: dict) -> QuantStrategy:
    return QuantStrategy(
        id=str(strategy["id"]),
        name=str(strategy["name"]),
        is_builtin=bool(strategy.get("builtin")),
        payload=copy.deepcopy(strategy),
        created_at=str(strategy["created_at"]),
        updated_at=str(strategy["updated_at"]),
    )


def _legacy_strategy(value: object, *, builtin: bool = False) -> dict | None:
    """Validate one JSON strategy without allowing bad seed data to block boot."""
    if not isinstance(value, dict):
        return None
    try:
        body = StrategyCreate.model_validate(value)
        normalized = body.model_dump(mode="json")
        _validate_strategy(normalized)
    except (TypeError, ValueError):
        return None

    now = shanghai_now().isoformat()
    strategy_id = str(value.get("id") or f"strat_{uuid.uuid4().hex[:12]}")
    strategy = {
        "id": strategy_id,
        "created_at": str(value.get("created_at") or now),
        "updated_at": str(value.get("updated_at") or now),
        **normalized,
    }
    if builtin:
        strategy["builtin"] = True
        for key in STRATEGY_METADATA_FIELDS:
            if key in value:
                strategy[key] = copy.deepcopy(value[key])
    strategy["governance"] = _governance_template(
        strategy,
        status=GOVERNANCE_LEGACY,
        approved=True,
        source="builtin" if builtin else "legacy",
        legacy_grandfathered=True,
    )
    return strategy


async def _load_rows() -> list[QuantStrategy]:
    async with async_session() as session:
        result = await session.execute(
            select(QuantStrategy).order_by(QuantStrategy.updated_at.desc(), QuantStrategy.id.asc())
        )
        rows = list(result.scalars().all())
        changed = False
        for row in rows:
            payload = copy.deepcopy(row.payload or {})
            governance = _normalize_governance({**payload, "builtin": bool(row.is_builtin), "updated_at": row.updated_at})
            if payload.get("governance") != governance:
                payload["governance"] = governance
                row.payload = payload
                changed = True
        if changed:
            await session.commit()
        return rows


def _write_cache(strategies: Iterable[dict]) -> list[dict]:
    values = [copy.deepcopy(item) for item in strategies]
    quant_store.write("strategies", {"version": 2, "strategies": values})
    return values


async def hydrate_strategy_store() -> list[dict]:
    """Load database strategies and migrate any legacy JSON-only records once."""
    legacy_document = quant_store.read("strategies")
    legacy_values = legacy_document.get("strategies") or []

    async with async_session() as session:
        result = await session.execute(
            select(QuantStrategy).order_by(QuantStrategy.updated_at.desc(), QuantStrategy.id.asc())
        )
        rows = list(result.scalars().all())
        known_ids = {row.id for row in rows}
        known_names = {row.name for row in rows}
        migrated = False

        migration_candidates = [
            *((value, False) for value in legacy_values if not rows),
            *((value, True) for value in BUILTIN_STRATEGIES),
        ]
        for value, builtin in migration_candidates:
            strategy = _legacy_strategy(value, builtin=builtin)
            if strategy is None or strategy["id"] in known_ids or strategy["name"] in known_names:
                continue
            session.add(_row_from_strategy(strategy))
            known_ids.add(strategy["id"])
            known_names.add(strategy["name"])
            migrated = True

        if migrated:
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise StrategyPersistenceError("量化策略迁移失败，请检查策略名称是否重复") from exc
            result = await session.execute(
                select(QuantStrategy).order_by(QuantStrategy.updated_at.desc(), QuantStrategy.id.asc())
            )
            rows = list(result.scalars().all())

    return _write_cache(_strategy_from_row(row) for row in await _load_rows())


async def list_strategies_persisted() -> list[dict]:
    rows = await _load_rows()
    return _write_cache(_strategy_from_row(row) for row in rows)


async def get_strategy_persisted(strategy_id: str) -> dict | None:
    rows = await _load_rows()
    row = next((item for item in rows if item.id == strategy_id), None)
    if row is None:
        return None
    strategy = _strategy_from_row(row)
    _write_cache(_strategy_from_row(item) for item in rows)
    return strategy


async def create_strategy_persisted(payload: StrategyCreate | dict) -> dict:
    strategy = build_strategy(payload)
    strategy["active"] = False
    strategy["governance"] = _governance_template(
        strategy,
        status=GOVERNANCE_DRAFT,
        approved=False,
        source="user",
    )
    async with async_session() as session:
        existing = await session.scalar(
            select(QuantStrategy.id).where(QuantStrategy.name == strategy["name"])
        )
        if existing is not None:
            raise ValueError("策略名称已存在")
        session.add(_row_from_strategy(strategy))
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ValueError("策略名称已存在") from exc

    persisted = await get_strategy_persisted(strategy["id"])
    if persisted is None:
        raise StrategyPersistenceError("策略已提交但读取校验失败")
    return persisted


async def update_strategy_persisted(
    strategy_id: str,
    payload: StrategyUpdate | dict,
) -> dict | None:
    updates = (
        payload.model_dump(mode="json", exclude_none=True)
        if isinstance(payload, StrategyUpdate)
        else StrategyUpdate.model_validate(payload).model_dump(mode="json", exclude_none=True)
    )
    async with async_session() as session:
        row = await session.get(QuantStrategy, strategy_id)
        if row is None:
            return None
        current = _strategy_from_row(row)
        if "name" in updates:
            duplicate = await session.scalar(
                select(QuantStrategy.id).where(
                    QuantStrategy.name == updates["name"], QuantStrategy.id != strategy_id
                )
            )
            if duplicate is not None:
                raise ValueError("策略名称已存在")
        candidate = build_strategy(
            {**current, **updates},
            strategy_id=strategy_id,
            created_at=current["created_at"],
            updated_at=shanghai_now().isoformat(),
        )
        for key in STRATEGY_METADATA_FIELDS:
            if key in current:
                candidate[key] = copy.deepcopy(current[key])
        current_governance = _normalize_governance(current)
        changed_substantive = strategy_fingerprint(current) != strategy_fingerprint(candidate)
        if changed_substantive:
            candidate["active"] = False
            rules_updated_at = candidate["updated_at"]
            candidate["governance"] = {
                **current_governance,
                "status": GOVERNANCE_MODIFIED,
                "approved": False,
                "approved_at": None,
                "approved_fingerprint": None,
                "rules_updated_at": rules_updated_at,
                "revoked_at": rules_updated_at,
                "revocation_note": "策略实质规则发生变化，需重新人工批准",
            }
        else:
            candidate["governance"] = current_governance
            if not current_governance.get("approved"):
                candidate["active"] = False
        row.name = candidate["name"]
        row.payload = copy.deepcopy(candidate)
        row.updated_at = candidate["updated_at"]
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ValueError("策略名称已存在") from exc

    persisted = await get_strategy_persisted(strategy_id)
    if persisted is None:
        raise StrategyPersistenceError("策略已更新但读取校验失败")
    return persisted


async def approve_strategy_persisted(strategy_id: str, note: str) -> dict | None:
    cleaned_note = str(note or "").strip()
    if not cleaned_note:
        raise ValueError("人工确认说明不能为空")
    async with async_session() as session:
        row = await session.get(QuantStrategy, strategy_id)
        if row is None:
            return None
        current = _strategy_from_row(row)
        audit_summary = _latest_auditable_backtest(current)
        if audit_summary is None:
            raise ValueError("没有与当前策略规则匹配的可审计回测，策略不能批准进入定时扫描")
        now = shanghai_now().isoformat()
        current["active"] = True
        current["governance"] = {
            **_normalize_governance(current),
            "status": GOVERNANCE_APPROVED,
            "approved": True,
            "approved_at": now,
            "approval_note": cleaned_note,
            "approved_fingerprint": strategy_fingerprint(current),
            "last_auditable_backtest": audit_summary,
            "revoked_at": None,
            "revocation_note": None,
        }
        row.payload = copy.deepcopy(current)
        row.name = current["name"]
        row.updated_at = now
        await session.commit()
    return await get_strategy_persisted(strategy_id)


async def revoke_strategy_approval_persisted(strategy_id: str, note: str | None = None) -> dict | None:
    async with async_session() as session:
        row = await session.get(QuantStrategy, strategy_id)
        if row is None:
            return None
        current = _strategy_from_row(row)
        now = shanghai_now().isoformat()
        current["active"] = False
        current["governance"] = {
            **_normalize_governance(current),
            "status": GOVERNANCE_DRAFT,
            "approved": False,
            "approved_at": None,
            "approved_fingerprint": None,
            "revoked_at": now,
            "revocation_note": str(note or "").strip() or "人工撤销定时扫描批准",
        }
        row.payload = copy.deepcopy(current)
        row.updated_at = now
        await session.commit()
    return await get_strategy_persisted(strategy_id)


async def delete_strategy_persisted(strategy_id: str) -> bool:
    async with async_session() as session:
        row = await session.get(QuantStrategy, strategy_id)
        if row is None:
            return False
        if row.is_builtin:
            raise ValueError("内置策略不能删除，可在策略编辑中停用")
        await session.delete(row)
        await session.commit()

    await list_strategies_persisted()
    return True
