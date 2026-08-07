"""幂等导入教学内容和已核验的板块目录。"""

import asyncio
import json
from datetime import date
from pathlib import Path

from sqlalchemy import delete, select

from database import async_session, init_db
from config import settings
from models import (
    ConceptBoard,
    KnowledgeTerm,
    LearningCase,
    PersonalPoolItem,
    PersonalSystemConfig,
)
from services.data_collector import normalize_stock_code


BASE_DIR = Path(__file__).resolve().parent
LEGACY_BOARD_CODES = ("BK1187", "BK1188", "BK1189", "BK1190", "BK1191")
PERSONAL_POOL_FILE = "seed_personal_pool.json"
PERSONAL_DELETION_CONFIG_KEY = "personal_pool_deletions_v1"

PERSONAL_POOL_KEYS = {
    "核心持仓池": "core",
    "长期观察池": "watchlist",
    "行业龙头池": "leaders",
    "ETF池": "etf",
}


def _load_json(filename: str) -> list[dict]:
    try:
        return json.loads((BASE_DIR / filename).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"{filename} not found")
        return []


def _load_object(filename: str) -> dict:
    try:
        payload = json.loads((BASE_DIR / filename).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except FileNotFoundError:
        print(f"{filename} not found")
        return {}


def _as_date(value: object):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


async def _seed_personal_pool(session) -> tuple[int, int]:
    """Insert the supplied personal pool once without overwriting user edits."""
    payload = _load_object(PERSONAL_POOL_FILE)
    if not payload:
        return 0, 0

    private_positions = payload.get("current_positions") or []
    if settings.personal_positions_json.strip():
        try:
            configured_positions = json.loads(settings.personal_positions_json)
            if isinstance(configured_positions, list):
                private_positions = configured_positions
            else:
                print("PERSONAL_POSITIONS_JSON must contain a JSON array")
        except json.JSONDecodeError:
            print("PERSONAL_POSITIONS_JSON is not valid JSON")

    positions = {
        normalize_stock_code(item.get("code")): item
        for item in private_positions
        if isinstance(item, dict)
        and item.get("code")
        and _valid_code(item.get("code"))
    }
    candidates: list[dict] = []

    for label, pool in (payload.get("pools") or {}).items():
        pool_key = PERSONAL_POOL_KEYS.get(label)
        if not pool_key or not isinstance(pool, dict):
            continue
        if pool_key == "leaders":
            source_items = []
            for industry, item in (pool.get("industries") or {}).items():
                if not isinstance(item, dict):
                    continue
                source_items.append({**item, "industry": industry})
        else:
            source_items = pool.get("stocks") if pool_key != "etf" else pool.get("etfs")
        for item in source_items or []:
            if not isinstance(item, dict) or not _valid_code(item.get("code")):
                continue
            position = positions.get(normalize_stock_code(item["code"]))
            applies_position = pool_key in {"core", "watchlist"}
            if not applies_position:
                position = None
            is_holding = bool(position and position.get("status") == "持有")
            candidates.append({
                "pool_key": pool_key,
                "code": normalize_stock_code(item["code"]),
                "name": str(item.get("name") or item["code"]),
                "asset_type": "etf" if pool_key == "etf" else "stock",
                "industry": str(item.get("industry") or ""),
                "status": "holding" if is_holding else (
                    "reduce" if position and applies_position else ("planned" if pool_key == "core" else "watching")
                ),
                "thesis": str(item.get("bullish") or ""),
                "warning": str(item.get("warning") or ""),
                "etf_type": str(item.get("type") or "") if pool_key == "etf" else None,
                "source": "document_seed",
                "position": position,
            })

    # 京东方在文档中属于待清理持仓，保留在观察池并带上风险参数。
    for code, position in positions.items():
        if not any(item["code"] == code and item["pool_key"] in {"core", "watchlist"} for item in candidates):
            candidates.append({
                "pool_key": "watchlist",
                "code": code,
                "name": str(position.get("name") or code),
                "asset_type": "stock",
                "industry": "",
                "status": "reduce" if position.get("status") != "持有" else "holding",
                "thesis": "",
                "warning": "待清理持仓，先复核逻辑和仓位",
                "etf_type": None,
                "source": "document_seed",
                "position": position,
            })

    deletion_row = await session.get(PersonalSystemConfig, PERSONAL_DELETION_CONFIG_KEY)
    deletion_payload = (
        deletion_row.payload
        if deletion_row and isinstance(deletion_row.payload, dict)
        else {}
    )
    deleted_tokens = {
        str(item) for item in deletion_payload.get("items") or [] if item
    }

    inserted = 0
    valid = 0
    for item in candidates:
        valid += 1
        position = item.pop("position") or {}
        if f"{item['pool_key']}:{item['code']}" in deleted_tokens:
            continue
        query = await session.execute(
            select(PersonalPoolItem).where(
                PersonalPoolItem.pool_key == item["pool_key"],
                PersonalPoolItem.code == item["code"],
            )
        )
        existing = query.scalar_one_or_none()
        if existing is not None:
            # Seed data is a starting point. Never reset costs or risk controls
            # after the user has edited them in the personal workspace.
            if not existing.name:
                existing.name = item["name"]
            if not existing.industry and item.get("industry"):
                existing.industry = item["industry"]
            continue

        values = {
            **item,
            "cost": _number_or_none(position.get("cost")),
            "entry_date": _as_date(position.get("date")),
            "position_pct": _number_or_none(position.get("position_pct")),
            "stop_loss": _number_or_none(position.get("stop_loss")),
            "targets": position.get("targets") or [],
            "max_position": _number_or_none(position.get("max_position")),
        }
        session.add(PersonalPoolItem(**values))
        inserted += 1

    config_payload = {
        "version": payload.get("version", "1.0"),
        "created": payload.get("created"),
        "screening_criteria": payload.get("screening_criteria") or [],
        "blacklist_reasons": payload.get("blacklist_reasons") or [],
        "management_rules": payload.get("management_rules") or {},
        "constitution": payload.get("constitution") or [],
        "disciplines": payload.get("disciplines") or [],
        "pool_limits": {
            PERSONAL_POOL_KEYS.get(label): pool.get("max_count")
            for label, pool in (payload.get("pools") or {}).items()
            if PERSONAL_POOL_KEYS.get(label) and isinstance(pool, dict) and pool.get("max_count") is not None
        },
    }
    config = await session.get(PersonalSystemConfig, "default")
    if config is None:
        session.add(PersonalSystemConfig(key="default", payload=config_payload))
    elif not config.payload:
        config.payload = config_payload
    await session.flush()
    return inserted, valid


def _valid_code(value: object) -> bool:
    try:
        normalize_stock_code(value)
        return True
    except ValueError:
        return False


def _number_or_none(value: object):
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _upsert_by(session, model, key: str, payload: dict) -> None:
    existing = (await session.execute(select(model).where(getattr(model, key) == payload[key]))).scalar_one_or_none()
    if existing is None:
        session.add(model(**payload))
        return
    for field, value in payload.items():
        setattr(existing, field, value)


async def seed() -> None:
    await init_db()
    terms = _load_json("seed_terms.json")
    cases = _load_json("seed_cases.json")
    boards = _load_json("seed_boards.json")

    async with async_session() as session:
        # These were placeholder board identifiers; deleting only them preserves user data.
        await session.execute(delete(ConceptBoard).where(ConceptBoard.code.in_(LEGACY_BOARD_CODES)))

        for term in terms:
            await _upsert_by(session, KnowledgeTerm, "term", term)
        for case in cases:
            payload = dict(case)
            if isinstance(payload.get("event_date"), str):
                payload["event_date"] = date.fromisoformat(payload["event_date"])
            await _upsert_by(session, LearningCase, "title", payload)
        for board in boards:
            await _upsert_by(session, ConceptBoard, "code", board)
        personal_inserted, personal_valid = await _seed_personal_pool(session)
        await session.commit()

    print(
        f"种子数据已同步: {len(terms)}个术语, {len(cases)}个案例, "
        f"{len(boards)}个板块, 个人池新增{personal_inserted}项/校验{personal_valid}项"
    )


if __name__ == "__main__":
    asyncio.run(seed())
