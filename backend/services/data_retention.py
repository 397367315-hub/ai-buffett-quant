"""Bounded retention for derived and high-frequency research snapshots.

The long-lived daily-bar, valuation and user research tables are deliberately
not part of this policy.  Only reproducible high-frequency/derived snapshots
are pruned so a personal deployment does not grow without an upper bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select

from database import async_session
from models import (
    BehaviorSnapshotV5,
    CausalChainActivationV5,
    ForecastSnapshotV5,
    MarketDataCache,
    StockMinuteBar,
    TradingSkillScanSnapshot,
)
from services.data_collector import shanghai_now


@dataclass(frozen=True)
class RetentionRule:
    key: str
    label: str
    model: type
    timestamp_column: Any
    keep_days: int


RETENTION_RULES: tuple[RetentionRule, ...] = (
    RetentionRule("stock_minute_bars", "候选与持仓分钟线", StockMinuteBar, StockMinuteBar.bar_time, 120),
    RetentionRule(
        "trading_skill_scan_snapshots",
        "交易技能扫描快照",
        TradingSkillScanSnapshot,
        TradingSkillScanSnapshot.generated_at,
        180,
    ),
    RetentionRule("market_forecasts", "V5预测快照", ForecastSnapshotV5, ForecastSnapshotV5.generated_at, 730),
    RetentionRule(
        "chain_activations",
        "因果链激活快照",
        CausalChainActivationV5,
        CausalChainActivationV5.generated_at,
        730,
    ),
    RetentionRule("behavior_history", "行为博弈快照", BehaviorSnapshotV5, BehaviorSnapshotV5.generated_at, 730),
)

AUDIT_CACHE_KEY = "data_retention:last_run"


def policy_payload() -> dict[str, Any]:
    return {
        "rules": [
            {
                "dataset": rule.key,
                "label": rule.label,
                "keep_days": rule.keep_days,
                "scope": "derived_or_high_frequency",
            }
            for rule in RETENTION_RULES
        ],
        "protected_datasets": [
            "stock_daily_bars",
            "stock_valuation_histories",
            "security_master",
            "financial_pit_snapshots",
            "user_strategies_and_research",
        ],
        "principle": "只清理可重建的高频/派生快照，不删除长期研究主数据和用户记录",
    }


class DataRetentionService:
    async def run(self, *, dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
        current = now or shanghai_now()
        current_naive = current.replace(tzinfo=None) if current.tzinfo else current
        rows: list[dict[str, Any]] = []

        async with async_session() as session:
            for rule in RETENTION_RULES:
                cutoff = current_naive - timedelta(days=rule.keep_days)
                count = int(
                    await session.scalar(
                        select(func.count()).select_from(rule.model).where(rule.timestamp_column < cutoff)
                    )
                    or 0
                )
                deleted = 0
                if count and not dry_run:
                    result = await session.execute(delete(rule.model).where(rule.timestamp_column < cutoff))
                    deleted = int(result.rowcount or 0)
                rows.append({
                    "dataset": rule.key,
                    "label": rule.label,
                    "keep_days": rule.keep_days,
                    "cutoff": cutoff.isoformat(),
                    "matched_rows": count,
                    "deleted_rows": deleted,
                })

            payload = {
                "status": "DRY_RUN" if dry_run else "COMPLETED",
                "ran_at": current.isoformat(),
                "dry_run": dry_run,
                "matched_rows": sum(item["matched_rows"] for item in rows),
                "deleted_rows": sum(item["deleted_rows"] for item in rows),
                "datasets": rows,
                **policy_payload(),
            }
            if not dry_run:
                cached = await session.get(MarketDataCache, AUDIT_CACHE_KEY)
                if cached is None:
                    session.add(MarketDataCache(key=AUDIT_CACHE_KEY, payload=payload, updated_at=current_naive))
                else:
                    cached.payload = payload
                    cached.updated_at = current_naive
                await session.commit()
            else:
                await session.rollback()
        return payload

    async def status(self) -> dict[str, Any]:
        async with async_session() as session:
            cached = await session.get(MarketDataCache, AUDIT_CACHE_KEY)
        return {
            "last_run": cached.payload if cached else None,
            **policy_payload(),
        }


data_retention_service = DataRetentionService()
