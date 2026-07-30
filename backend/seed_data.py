"""幂等导入教学内容和已核验的板块目录。"""

import asyncio
import json
from datetime import date
from pathlib import Path

from sqlalchemy import delete, select

from database import async_session, init_db
from models import ConceptBoard, KnowledgeTerm, LearningCase


BASE_DIR = Path(__file__).resolve().parent
LEGACY_BOARD_CODES = ("BK1187", "BK1188", "BK1189", "BK1190", "BK1191")


def _load_json(filename: str) -> list[dict]:
    try:
        return json.loads((BASE_DIR / filename).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"{filename} not found")
        return []


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
        await session.commit()

    print(f"种子数据已同步: {len(terms)}个术语, {len(cases)}个案例, {len(boards)}个板块")


if __name__ == "__main__":
    asyncio.run(seed())
