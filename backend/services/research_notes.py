"""Structured research notes and repeat-mistake detection."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, select

from database import async_session
from models import (
    PersonalErrorPattern,
    PersonalInvestmentLog,
    PersonalPoolItem,
    PersonalResearchNote,
)
from services.data_collector import normalize_stock_code


def _date(value: object, default: date | None = None) -> date | None:
    if value in (None, ""):
        return default
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValueError("日期格式必须是 YYYY-MM-DD") from exc


def _note_dict(row: PersonalResearchNote, *, error_count: int = 0, operations: list[dict] | None = None) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "first_researched_at": row.first_researched_at.isoformat() if row.first_researched_at else None,
        "why_follow": row.why_follow or "",
        "competitive_advantage": row.competitive_advantage or "",
        "risks": row.risks or "",
        "key_metrics": row.key_metrics or {},
        "latest_view": row.latest_view or "",
        "tags": row.tags or [],
        "error_count": error_count,
        "operations": operations or [],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _error_dict(row: PersonalErrorPattern, repeated_count: int) -> dict:
    return {
        "id": row.id,
        "occurred_on": row.occurred_on.isoformat(),
        "error_type": row.error_type,
        "code": row.code,
        "name": row.name or "",
        "lesson": row.lesson,
        "prevention": row.prevention,
        "context": row.context or {},
        "repeat_count": repeated_count,
        "requires_confirmation": repeated_count >= 3,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class ResearchNotesService:
    async def list_notes(self) -> list[dict]:
        async with async_session() as session:
            notes = (await session.execute(
                select(PersonalResearchNote).order_by(PersonalResearchNote.updated_at.desc())
            )).scalars().all()
            errors = (await session.execute(select(PersonalErrorPattern))).scalars().all()
        counts = Counter(row.code for row in errors if row.code)
        return [_note_dict(row, error_count=counts[row.code]) for row in notes]

    async def get_note(self, note_id: int) -> dict:
        async with async_session() as session:
            row = await session.get(PersonalResearchNote, note_id)
            if row is None:
                raise LookupError("研究笔记不存在")
            logs = (await session.execute(
                select(PersonalInvestmentLog)
                .where(PersonalInvestmentLog.code == row.code)
                .order_by(PersonalInvestmentLog.created_at.desc())
                .limit(30)
            )).scalars().all()
            error_count = len((await session.execute(
                select(PersonalErrorPattern.id).where(PersonalErrorPattern.code == row.code)
            )).all())
        operations = [
            {
                "id": item.id,
                "action": item.action,
                "price": item.price,
                "shares": item.shares,
                "reason": item.reason,
                "reflection": item.reflection or "",
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in logs
        ]
        return _note_dict(row, error_count=error_count, operations=operations)

    async def upsert_note(self, payload: dict[str, Any], note_id: int | None = None) -> dict:
        code = normalize_stock_code(payload.get("code"))
        async with async_session() as session:
            if note_id is not None:
                row = await session.get(PersonalResearchNote, note_id)
                if row is None:
                    raise LookupError("研究笔记不存在")
                duplicate = (await session.execute(
                    select(PersonalResearchNote).where(
                        PersonalResearchNote.code == code,
                        PersonalResearchNote.id != note_id,
                    )
                )).scalar_one_or_none()
                if duplicate:
                    raise ValueError("该股票已经有研究笔记")
            else:
                row = (await session.execute(
                    select(PersonalResearchNote).where(PersonalResearchNote.code == code)
                )).scalar_one_or_none()
            pool_item = (await session.execute(
                select(PersonalPoolItem)
                .where(PersonalPoolItem.code == code)
                .order_by(PersonalPoolItem.id.asc())
            )).scalars().first()
            name = str(payload.get("name") or (pool_item.name if pool_item else "") or code).strip()
            values = {
                "code": code,
                "name": name,
                "first_researched_at": _date(payload.get("first_researched_at"), date.today()),
                "why_follow": str(payload.get("why_follow") or "").strip(),
                "competitive_advantage": str(payload.get("competitive_advantage") or "").strip(),
                "risks": str(payload.get("risks") or "").strip(),
                "key_metrics": payload.get("key_metrics") if isinstance(payload.get("key_metrics"), dict) else {},
                "latest_view": str(payload.get("latest_view") or "").strip(),
                "tags": payload.get("tags") if isinstance(payload.get("tags"), list) else [],
            }
            if row is None:
                row = PersonalResearchNote(**values)
                session.add(row)
            else:
                for field, value in values.items():
                    setattr(row, field, value)
                row.updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(row)
        return await self.get_note(row.id)

    async def delete_note(self, note_id: int) -> None:
        async with async_session() as session:
            row = await session.get(PersonalResearchNote, note_id)
            if row is None:
                raise LookupError("研究笔记不存在")
            await session.delete(row)
            await session.commit()

    async def list_errors(self) -> dict[str, Any]:
        async with async_session() as session:
            rows = (await session.execute(
                select(PersonalErrorPattern)
                .order_by(PersonalErrorPattern.occurred_on.desc(), PersonalErrorPattern.id.desc())
            )).scalars().all()
        counts = Counter(row.error_type for row in rows)
        patterns = [
            {
                "error_type": error_type,
                "count": count,
                "requires_confirmation": count >= 3,
                "message": f"“{error_type}”已出现 {count} 次，交易前必须再次确认。" if count >= 3 else None,
            }
            for error_type, count in counts.most_common()
        ]
        return {
            "errors": [_error_dict(row, counts[row.error_type]) for row in rows],
            "patterns": patterns,
            "forced_warning_count": sum(item["requires_confirmation"] for item in patterns),
        }

    async def create_error(self, payload: dict[str, Any]) -> dict:
        error_type = str(payload.get("error_type") or "").strip()
        lesson = str(payload.get("lesson") or "").strip()
        prevention = str(payload.get("prevention") or "").strip()
        if not error_type or not lesson or not prevention:
            raise ValueError("错误类型、教训和避免方法都必须填写")
        code = normalize_stock_code(payload["code"]) if payload.get("code") else None
        async with async_session() as session:
            row = PersonalErrorPattern(
                occurred_on=_date(payload.get("occurred_on"), date.today()),
                error_type=error_type[:100],
                code=code,
                name=str(payload.get("name") or "").strip() or None,
                lesson=lesson,
                prevention=prevention,
                context=payload.get("context") if isinstance(payload.get("context"), dict) else {},
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            repeat_count = (await session.execute(
                select(PersonalErrorPattern.id).where(PersonalErrorPattern.error_type == error_type)
            )).all()
        return _error_dict(row, len(repeat_count))

    async def delete_error(self, error_id: int) -> None:
        async with async_session() as session:
            result = await session.execute(
                delete(PersonalErrorPattern).where(PersonalErrorPattern.id == error_id)
            )
            if not result.rowcount:
                raise LookupError("错误记录不存在")
            await session.commit()

    async def warnings(self, code: str | None = None) -> list[dict]:
        normalized = normalize_stock_code(code) if code else None
        async with async_session() as session:
            rows = (await session.execute(select(PersonalErrorPattern))).scalars().all()
        counts = Counter(row.error_type for row in rows)
        warnings = []
        for error_type, count in counts.items():
            relevant = [row for row in rows if row.error_type == error_type]
            code_match = normalized and any(row.code == normalized for row in relevant)
            if count >= 3 or code_match:
                warnings.append({
                    "error_type": error_type,
                    "count": count,
                    "code_match": bool(code_match),
                    "requires_confirmation": count >= 3,
                    "message": f"你过去犯过“{error_type}”{count}次，确定本次操作没有重复该错误吗？",
                    "prevention": next((row.prevention for row in reversed(relevant) if row.prevention), ""),
                })
        return sorted(warnings, key=lambda item: (item["requires_confirmation"], item["code_match"], item["count"]), reverse=True)


research_notes_service = ResearchNotesService()
