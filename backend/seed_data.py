import json
import asyncio
from datetime import date
from sqlalchemy import text
from database import async_session, init_db
from models import KnowledgeTerm, LearningCase, ConceptBoard


def load_seed_data():
    data = {
        "terms": [],
        "cases": [],
        "boards": [],
    }

    try:
        with open("seed_terms.json", "r", encoding="utf-8") as f:
            data["terms"] = json.load(f)
    except FileNotFoundError:
        print("seed_terms.json not found")

    try:
        with open("seed_cases.json", "r", encoding="utf-8") as f:
            data["cases"] = json.load(f)
    except FileNotFoundError:
        print("seed_cases.json not found")

    try:
        with open("seed_boards.json", "r", encoding="utf-8") as f:
            data["boards"] = json.load(f)
    except FileNotFoundError:
        print("seed_boards.json not found")

    return data


async def seed():
    await init_db()

    data = load_seed_data()

    async with async_session() as session:
        for model in [KnowledgeTerm, LearningCase, ConceptBoard]:
            await session.execute(text(f"DELETE FROM {model.__tablename__}"))
        await session.commit()

        for t in data["terms"]:
            session.add(KnowledgeTerm(**t))

        for c in data["cases"]:
            # Convert event_date string to date
            if "event_date" in c and isinstance(c["event_date"], str):
                c["event_date"] = date.fromisoformat(c["event_date"])
            session.add(LearningCase(**c))

        for b in data["boards"]:
            session.add(ConceptBoard(**b))

        await session.commit()

    print(f'种子数据已导入: {len(data["terms"])}个术语, {len(data["cases"])}个案例, {len(data["boards"])}个板块')


if __name__ == "__main__":
    asyncio.run(seed())
