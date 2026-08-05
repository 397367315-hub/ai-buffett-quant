"""Persistent complete-market snapshot used when live A-share quotes are unavailable."""

from __future__ import annotations

import copy
from datetime import datetime

from database import async_session
from models import MarketDataCache
from services.data_collector import shanghai_now


QUANT_MARKET_CACHE_KEY = "quant_complete_market_snapshot_v1"


async def load_quant_market_snapshot() -> dict:
    try:
        async with async_session() as session:
            row = await session.get(MarketDataCache, QUANT_MARKET_CACHE_KEY)
        payload = copy.deepcopy(row.payload) if row and isinstance(row.payload, dict) else {}
    except Exception as exc:
        print(f"Quant market snapshot cache load failed: {type(exc).__name__}")
        return {}
    if not payload.get("stocks") or not payload.get("data_date"):
        return {}
    return {
        **payload,
        "source": "cache",
        "cache_source": payload.get("source") or "eastmoney",
        "is_realtime": False,
    }


async def save_quant_market_snapshot(snapshot: dict) -> bool:
    if not snapshot.get("complete") or not snapshot.get("stocks") or not snapshot.get("data_date"):
        return False
    payload = copy.deepcopy({**snapshot, "cached_at": shanghai_now().isoformat()})
    try:
        async with async_session() as session:
            row = await session.get(MarketDataCache, QUANT_MARKET_CACHE_KEY)
            if row is None:
                session.add(MarketDataCache(key=QUANT_MARKET_CACHE_KEY, payload=payload))
            else:
                cached_date = str((row.payload or {}).get("data_date") or "")
                incoming_date = str(payload.get("data_date") or "")
                if cached_date and incoming_date < cached_date:
                    return False
                row.payload = payload
                row.updated_at = datetime.utcnow()
            await session.commit()
        return True
    except Exception as exc:
        print(f"Quant market snapshot cache save failed: {type(exc).__name__}")
        return False
