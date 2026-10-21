"""13 席注册表（SEAT-001）。"""
from __future__ import annotations

import json

from ...config import SKILLS_DIR
from ...domain import clock, seat_registry
from ...domain.canonical import canonical_json
from ..app import Ctx, handler


def ensure_registry(db) -> dict:
    """计算当前注册表；指纹新出现时写入不可变的 seat_registry_versions。"""
    payload = seat_registry.registry_payload(SKILLS_DIR)
    with db.txn() as c:
        if not c.execute("SELECT 1 FROM seat_registry_versions WHERE fingerprint=?", (payload["fingerprint"],)).fetchone():
            c.execute("INSERT INTO seat_registry_versions(registry_version, fingerprint, created_at, canonical_payload) "
                      "VALUES(?,?,?,?)", (payload["registry_version"], payload["fingerprint"], clock.utc_iso(),
                                          canonical_json(payload)))
    return payload


def registry_by_version(db, version: str) -> dict | None:
    r = db.one("SELECT canonical_payload FROM seat_registry_versions WHERE registry_version=?", (version,))
    return json.loads(r["canonical_payload"]) if r else None


@handler("get_api_v1_seats_registry")
def registry(ctx: Ctx):
    return 200, ensure_registry(ctx.db)
