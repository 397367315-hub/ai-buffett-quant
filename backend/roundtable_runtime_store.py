"""Durable PostgreSQL checkpoint storage for the isolated roundtable runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib

from sqlalchemy import bindparam, select, update

from database import async_session
from models import RoundtableRuntimeBlob, RoundtableRuntimeSnapshot


class RoundtableRuntimeStoreError(RuntimeError):
    """Base error for checkpoint persistence."""


class RoundtableRuntimeCASConflict(RoundtableRuntimeStoreError):
    """Another process wrote a newer checkpoint."""


@dataclass(frozen=True)
class RoundtableRuntimeState:
    payload: bytes
    revision: int
    sha256: str
    updated_at: datetime | None = None

    def __iter__(self):
        # Convenient for callers that use ``payload, revision = await load()``.
        yield self.payload
        yield self.revision


@dataclass(frozen=True)
class RoundtableRuntimeBlobState:
    sha256: str
    size: int
    payload: bytes


class RoundtableRuntimeStore:
    """Async CAS store; PostgreSQL is the source of truth for checkpoints."""

    def __init__(self, key: str = "default") -> None:
        self.key = str(key).strip() or "default"

    async def load(self) -> RoundtableRuntimeState | None:
        async with async_session() as session:
            row = await session.get(RoundtableRuntimeSnapshot, self.key)
            if row is None:
                return None
            payload = bytes(row.payload)
            digest = hashlib.sha256(payload).hexdigest()
            if digest != row.sha256:
                raise RoundtableRuntimeStoreError("roundtable runtime checkpoint hash mismatch")
            return RoundtableRuntimeState(payload, int(row.revision), row.sha256, row.updated_at)

    async def save(self, payload: bytes, expected_revision: int | None) -> int:
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise TypeError("payload must be bytes")
        blob = bytes(payload)
        digest = hashlib.sha256(blob).hexdigest()
        async with async_session() as session:
            if expected_revision is None:
                row = RoundtableRuntimeSnapshot(
                    key=self.key, revision=1, payload=blob, sha256=digest,
                )
                session.add(row)
                try:
                    await session.commit()
                except Exception as exc:
                    await session.rollback()
                    # Preserve a stable conflict error for duplicate initial writes.
                    if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                        raise RoundtableRuntimeCASConflict("roundtable runtime checkpoint already exists") from exc
                    raise
                return 1

            next_revision = int(expected_revision) + 1
            result = await session.execute(
                update(RoundtableRuntimeSnapshot)
                .where(
                    RoundtableRuntimeSnapshot.key == self.key,
                    RoundtableRuntimeSnapshot.revision == int(expected_revision),
                )
                .values(revision=next_revision, payload=bindparam("checkpoint_blob"), sha256=digest,
                        updated_at=datetime.now(timezone.utc).replace(tzinfo=None)),
                {"checkpoint_blob": blob},
            )
            if result.rowcount != 1:
                await session.rollback()
                raise RoundtableRuntimeCASConflict(
                    f"roundtable runtime checkpoint revision conflict (expected {expected_revision})"
                )
            await session.commit()
            return next_revision

    async def blob_exists(self, sha256: str) -> bool:
        """Check the CAS namespace without loading the potentially large payload."""
        async with async_session() as session:
            result = await session.execute(
                select(RoundtableRuntimeBlob.sha256).where(RoundtableRuntimeBlob.sha256 == sha256)
            )
            return result.scalar_one_or_none() is not None

    async def save_blob(self, sha256: str, size: int, payload: bytes) -> None:
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise TypeError("blob payload must be bytes")
        async with async_session() as session:
            # The digest is the identity.  A duplicate is a successful no-op,
            # which also makes retries after a failed checkpoint safe.
            row = RoundtableRuntimeBlob(sha256=sha256, size=int(size), payload=bytes(payload))
            session.add(row)
            try:
                await session.commit()
            except Exception as exc:
                await session.rollback()
                if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                    return
                raise

    async def load_blob(self, sha256: str) -> RoundtableRuntimeBlobState | None:
        async with async_session() as session:
            row = await session.get(RoundtableRuntimeBlob, sha256)
            if row is None:
                return None
            return RoundtableRuntimeBlobState(str(row.sha256), int(row.size), bytes(row.payload))
