"""Durable persistence for user-authored quantitative strategies.

The quantitative engine still consumes a synchronous JSON cache, but the
database is the source of truth for strategy CRUD.  This keeps the existing
scan/backtest code compatible while making strategies survive Render
restarts, sleeps, and redeploys.
"""

from __future__ import annotations

import copy
import uuid
from typing import Iterable

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


def _strategy_from_row(row: QuantStrategy) -> dict:
    payload = copy.deepcopy(row.payload or {})
    payload.update({
        "id": row.id,
        "name": row.name,
        "builtin": bool(row.is_builtin),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    })
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
    return strategy


async def _load_rows() -> list[QuantStrategy]:
    async with async_session() as session:
        result = await session.execute(
            select(QuantStrategy).order_by(QuantStrategy.updated_at.desc(), QuantStrategy.id.asc())
        )
        return list(result.scalars().all())


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

    return _write_cache(_strategy_from_row(row) for row in rows)


async def list_strategies_persisted() -> list[dict]:
    rows = await _load_rows()
    return _write_cache(_strategy_from_row(row) for row in rows)


async def get_strategy_persisted(strategy_id: str) -> dict | None:
    async with async_session() as session:
        row = await session.get(QuantStrategy, strategy_id)
    if row is None:
        return None
    strategy = _strategy_from_row(row)
    _write_cache([_strategy_from_row(item) for item in await _load_rows()])
    return strategy


async def create_strategy_persisted(payload: StrategyCreate | dict) -> dict:
    strategy = build_strategy(payload)
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
