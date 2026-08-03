"""Strategy CRUD and stock-to-strategy matching."""

from __future__ import annotations

import uuid

from quant.rules import evaluate_rules, validate_group
from quant.schemas import StrategyCreate, StrategyUpdate
from quant.storage import quant_store
from services.data_collector import shanghai_now


def _validate_strategy(strategy: dict) -> None:
    validate_group(strategy.get("filter") or {}, allow_empty=True)
    validate_group(strategy.get("entry") or {}, allow_empty=False)
    for rule in (strategy.get("exit") or {}).get("rules") or []:
        validate_group({"logic": "OR", "rules": [rule]}, allow_empty=False)


def create_strategy(payload: StrategyCreate | dict) -> dict:
    body = payload.model_dump(mode="json") if isinstance(payload, StrategyCreate) else StrategyCreate.model_validate(payload).model_dump(mode="json")
    _validate_strategy(body)
    now = shanghai_now().isoformat()
    strategy = {
        "id": f"strat_{uuid.uuid4().hex[:12]}",
        "created_at": now,
        "updated_at": now,
        **body,
    }

    def append(document: dict) -> None:
        if any(item.get("name") == strategy["name"] for item in document.get("strategies", [])):
            raise ValueError("策略名称已存在")
        document.setdefault("strategies", []).append(strategy)

    quant_store.update("strategies", append)
    return strategy


def list_strategies() -> list[dict]:
    return quant_store.read("strategies").get("strategies", [])


def get_strategy(strategy_id: str) -> dict | None:
    return next((item for item in list_strategies() if item.get("id") == strategy_id), None)


def update_strategy(strategy_id: str, payload: StrategyUpdate | dict) -> dict | None:
    updates = payload.model_dump(mode="json", exclude_none=True) if isinstance(payload, StrategyUpdate) else StrategyUpdate.model_validate(payload).model_dump(mode="json", exclude_none=True)
    updated: dict | None = None

    def mutate(document: dict) -> None:
        nonlocal updated
        strategies = document.get("strategies", [])
        for strategy in strategies:
            if strategy.get("id") != strategy_id:
                continue
            if "name" in updates and any(
                item.get("id") != strategy_id and item.get("name") == updates["name"]
                for item in strategies
            ):
                raise ValueError("策略名称已存在")
            candidate = {**strategy, **updates, "updated_at": shanghai_now().isoformat()}
            StrategyCreate.model_validate({key: candidate[key] for key in StrategyCreate.model_fields})
            _validate_strategy(candidate)
            strategy.clear()
            strategy.update(candidate)
            updated = dict(strategy)
            return

    quant_store.update("strategies", mutate)
    return updated


def delete_strategy(strategy_id: str) -> bool:
    deleted = False

    def mutate(document: dict) -> None:
        nonlocal deleted
        strategies = document.get("strategies", [])
        retained = [item for item in strategies if item.get("id") != strategy_id]
        deleted = len(retained) != len(strategies)
        document["strategies"] = retained

    quant_store.update("strategies", mutate)
    return deleted


def match_stock(strategy: dict, stock: dict) -> dict | None:
    filter_group = strategy.get("filter") or {"logic": "AND", "rules": []}
    entry_group = strategy.get("entry") or {"logic": "AND", "rules": []}
    filter_match, filter_passed, filter_failed = evaluate_rules(
        filter_group.get("rules") or [], stock, filter_group.get("logic", "AND")
    )
    if not filter_match:
        return None
    entry_match, entry_passed, entry_failed = evaluate_rules(
        entry_group.get("rules") or [], stock, entry_group.get("logic", "AND")
    )
    if not entry_match:
        return None
    total = len(filter_group.get("rules") or []) + len(entry_group.get("rules") or [])
    passed = filter_passed + entry_passed
    return {
        "strategy_id": strategy["id"],
        "strategy_name": strategy["name"],
        "type": "buy",
        "stock_code": stock["code"],
        "stock_name": stock.get("name", ""),
        "match_score": round(len(passed) / total * 100) if total else 100,
        "price": stock.get("price"),
        "change_pct": stock.get("change_pct"),
        "turnover": stock.get("turnover"),
        "pe_ttm": stock.get("pe_ttm"),
        "main_inflow": stock.get("main_inflow"),
        "vol_ratio": stock.get("vol_ratio"),
        "sector": stock.get("sector", ""),
        "matched_rules": passed,
        "unmatched_rules": filter_failed + entry_failed,
        "generated_at": shanghai_now().isoformat(),
    }
