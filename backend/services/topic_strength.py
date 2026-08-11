"""Auditable market-regime and topic-strength research service.

The module deliberately separates observed facts, bounded inference and data
gaps.  It never turns a missing intraday field into a positive signal and it
never exposes an order-execution action.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import desc, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database import async_session
from models import (
    IndustryFundFlowDaily,
    MarketBoard,
    MarketDataCache,
    MarketSentimentDaily,
    NorthboundDealDaily,
    StockDailyBar,
    StockIntradayEvidence,
    StockMinuteBar,
)
from quant.market_cache import load_quant_market_snapshot, save_quant_market_snapshot
from services.ai_service import ai_service
from services.data_collector import (
    collector,
    is_a_share_market_session,
    normalize_stock_code,
    shanghai_now,
)


TOPIC_CACHE_PREFIX = "topic_strength_v2:"
TOPIC_LATEST_CACHE_KEY = "topic_strength_latest_v2"
LEGACY_TOPIC_CACHE_PREFIXES = ("topic_strength_v1:",)
MAX_TOPICS = 12
MAX_MEMBERS = 8
MAX_INTRADAY_STOCKS = 8


def _number(value: object) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: object) -> int | None:
    parsed = _number(value)
    return int(parsed) if parsed is not None else None


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _sector_key(value: object) -> str:
    text = re.sub(r"\s+", "", str(value or "").strip())
    return re.sub(r"(?:行业)?[ⅠⅡⅢIV]+$", "", text, flags=re.IGNORECASE)


def _limit_pct(code: str) -> float:
    if code.startswith(("300", "301", "302", "688", "689")):
        return 20.0
    if code.startswith(("4", "8", "920")):
        return 30.0
    return 10.0


def _normalize_trade_date(value: object) -> str | None:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    parsed = _date(text)
    return parsed.isoformat() if parsed else None


def _topic_date_metadata(rows: Iterable[object]) -> dict[date, dict]:
    """Read date counters from the exact topic snapshots shown by the page."""
    output: dict[date, dict] = {}
    priorities: dict[date, int] = {}
    accepted_prefixes = (*LEGACY_TOPIC_CACHE_PREFIXES, TOPIC_CACHE_PREFIX)
    for row in rows:
        payload = getattr(row, "payload", None)
        data_date = _date(payload.get("data_date")) if isinstance(payload, dict) else None
        key = str(getattr(row, "key", None) or "")
        matched_prefix = next(
            (prefix for prefix in accepted_prefixes if key == f"{prefix}{data_date.isoformat()}"),
            None,
        ) if data_date else None
        if data_date is None or matched_prefix is None:
            continue
        priority = accepted_prefixes.index(matched_prefix)
        if priority < priorities.get(data_date, -1):
            continue
        market = payload.get("market") or {}
        emotion = market.get("emotion") or {}
        sentiment = market.get("sentiment") or {}
        output[data_date] = {
            "limit_up_count": _integer(emotion.get("zt_count")),
            "failed_limit_count": _integer(emotion.get("zb_count")),
            "stock_count": _integer(sentiment.get("total")),
            "source": "topic_strength_cache",
        }
        priorities[data_date] = priority
    return output


def _breadth_label(up: int, down: int) -> tuple[float | None, str]:
    directional = up + down
    if directional <= 0:
        return None, "数据不足"
    ratio = round(up / directional * 100, 1)
    label = "普涨" if ratio >= 65 else "普跌" if ratio <= 35 else "分化"
    return ratio, label


def _aggregate_daily_rows(rows: Iterable[dict], category: int) -> list[dict]:
    """Aggregate normalized daily rows to weekly or monthly OHLCV bars."""
    if category not in {5, 6}:
        return list(rows)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        parsed = _date(row.get("date") or row.get("trade_date"))
        if parsed is None:
            continue
        key = parsed.isocalendar()[:2] if category == 5 else (parsed.year, parsed.month)
        groups[key].append({**row, "_date": parsed})

    output = []
    previous_close: float | None = None
    for key in sorted(groups):
        items = sorted(groups[key], key=lambda item: item["_date"])
        opens = [_number(item.get("open")) for item in items]
        closes = [_number(item.get("close")) for item in items]
        highs = [_number(item.get("high")) for item in items]
        lows = [_number(item.get("low")) for item in items]
        open_price = next((value for value in opens if value is not None), None)
        close_price = next((value for value in reversed(closes) if value is not None), None)
        high_values = [value for value in highs if value is not None]
        low_values = [value for value in lows if value is not None]
        change_pct = (
            (close_price / previous_close - 1) * 100
            if close_price is not None and previous_close not in (None, 0)
            else None
        )
        output.append({
            "date": items[-1]["_date"].isoformat(),
            "period_start": items[0]["_date"].isoformat(),
            "open": open_price,
            "close": close_price,
            "high": max(high_values) if high_values else None,
            "low": min(low_values) if low_values else None,
            "volume": sum(_integer(item.get("volume")) or 0 for item in items) or None,
            "amount": sum(_integer(item.get("amount")) or 0 for item in items) or None,
            "change_pct": round(change_pct, 4) if change_pct is not None else None,
        })
        if close_price is not None:
            previous_close = close_price
    return output


class TopicStrengthService:
    _LIVE_CACHE_SECONDS = 60

    @staticmethod
    async def _read_cache(key: str) -> dict | None:
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, key)
            return dict(row.payload) if row and isinstance(row.payload, dict) else None
        except Exception:
            return None

    @staticmethod
    async def _write_cache(key: str, payload: dict) -> None:
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, key)
                if row is None:
                    session.add(MarketDataCache(key=key, payload=payload))
                else:
                    row.payload = payload
                    row.updated_at = datetime.utcnow()
                await session.commit()
        except Exception as exc:
            print(f"Topic strength cache write failed: {type(exc).__name__}")

    @staticmethod
    def _cache_fresh(payload: dict) -> bool:
        parsed = None
        try:
            parsed = datetime.fromisoformat(str(payload.get("updated_at") or ""))
        except ValueError:
            pass
        if parsed is None:
            return False
        now = shanghai_now()
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=now.tzinfo)
        return 0 <= (now - parsed).total_seconds() <= TopicStrengthService._LIVE_CACHE_SECONDS

    @staticmethod
    async def _safe(awaitable, fallback, timeout: float = 12.0):
        try:
            return await asyncio.wait_for(awaitable, timeout=timeout)
        except Exception:
            return fallback

    async def _latest_cached_date(self) -> date | None:
        candidates: list[date] = []
        try:
            async with async_session() as session:
                sentiment_date, bar_date = (await session.execute(
                    select(
                        select(func.max(MarketSentimentDaily.trade_date)).scalar_subquery(),
                        select(func.max(StockDailyBar.trade_date)).scalar_subquery(),
                    )
                )).one()
            candidates.extend(item for item in (sentiment_date, bar_date) if item)
        except Exception:
            pass
        snapshot = await load_quant_market_snapshot()
        snapshot_date = _date(snapshot.get("data_date"))
        if snapshot_date:
            candidates.append(snapshot_date)
        return max(candidates, default=None)

    async def _resolve_date(self, requested: date | None) -> date:
        today = shanghai_now().date()
        if requested and requested > today:
            raise ValueError("不能查询未来交易日")
        if requested:
            return requested
        if is_a_share_market_session(shanghai_now()):
            return today
        cached = await self._latest_cached_date()
        if cached:
            return cached
        fallback = today
        while fallback.weekday() >= 5:
            fallback -= timedelta(days=1)
        return fallback

    async def dates(self, limit: int = 120) -> list[dict]:
        try:
            async with async_session() as session:
                sentiment_rows = list((await session.execute(
                    select(MarketSentimentDaily)
                    .order_by(desc(MarketSentimentDaily.trade_date))
                    .limit(limit)
                )).scalars().all())
                bar_dates = list((await session.execute(
                    select(StockDailyBar.trade_date)
                    .distinct()
                    .order_by(desc(StockDailyBar.trade_date))
                    .limit(limit)
                )).scalars().all())
                topic_cache_rows = list((await session.execute(
                    select(MarketDataCache)
                    .where(or_(
                        MarketDataCache.key.like(f"{TOPIC_CACHE_PREFIX}%"),
                        *(MarketDataCache.key.like(f"{prefix}%") for prefix in LEGACY_TOPIC_CACHE_PREFIXES),
                    ))
                    .order_by(desc(MarketDataCache.key))
                    .limit(limit)
                )).scalars().all())
        except Exception:
            sentiment_rows = []
            bar_dates = []
            topic_cache_rows = []
        sentiment_by_date = {row.trade_date: row for row in sentiment_rows}
        topic_by_date = _topic_date_metadata(topic_cache_rows)
        available_dates = sorted(
            set(bar_dates) | set(sentiment_by_date) | set(topic_by_date),
            reverse=True,
        )[:limit]
        output = []
        for trade_date in reversed(available_dates):
            snapshot = topic_by_date.get(trade_date)
            sentiment = sentiment_by_date.get(trade_date)
            output.append({
                "date": trade_date.isoformat(),
                "limit_up_count": snapshot["limit_up_count"] if snapshot else sentiment.limit_up_count if sentiment else None,
                "failed_limit_count": snapshot["failed_limit_count"] if snapshot else sentiment.failed_limit_count if sentiment else None,
                "stock_count": snapshot["stock_count"] if snapshot else sentiment.stock_count if sentiment else None,
                "source": snapshot["source"] if snapshot else "market_sentiment_cache" if sentiment else "stock_daily_bars",
            })
        return output

    @staticmethod
    def _verified_pool(payload: dict, target: date) -> dict:
        """Discard provider rows unless their data date matches the request."""
        actual_date = _normalize_trade_date(payload.get("trade_date"))
        verified = actual_date == target.isoformat()
        return {
            **payload,
            "stocks": list(payload.get("stocks") or []) if verified else [],
            "total": _integer(payload.get("total")) if verified else None,
            "trade_date": actual_date,
            "verified": verified,
            "requested_trade_date": target.isoformat(),
        }

    async def _cached_sentiment(self, target: date) -> dict:
        try:
            async with async_session() as session:
                row = await session.get(MarketSentimentDaily, target)
        except Exception:
            row = None
        if not row:
            return {}
        return {
            "up_count": row.up_count,
            "down_count": row.down_count,
            "flat_count": row.flat_count,
            "stock_count": row.stock_count,
            "market_amount": row.market_amount,
            "amount_count": row.amount_count,
            "average_turnover": row.average_turnover,
            "turnover_count": row.turnover_count,
            "limit_up_count": row.limit_up_count,
            "limit_down_count": row.limit_down_count,
            "failed_limit_count": row.failed_limit_count,
            "failed_limit_rate": row.failed_limit_rate,
            "max_streak_height": row.max_streak_height,
            "source": row.source,
        }

    async def _northbound_snapshot(self, target: date) -> dict:
        """Return the latest disclosed northbound deal record at the target date."""
        try:
            async with async_session() as session:
                row = (await session.execute(
                    select(NorthboundDealDaily)
                    .where(NorthboundDealDaily.trade_date <= target)
                    .order_by(desc(NorthboundDealDaily.trade_date))
                    .limit(1)
                )).scalar_one_or_none()
        except Exception:
            row = None
        if row is None:
            return {
                "available": False,
                "data_date": None,
                "deal_amount": None,
                "net_inflow": None,
                "net_inflow_available": False,
                "source": "unavailable",
                "frequency": "盘后披露",
            }
        return {
            "available": True,
            "data_date": row.trade_date.isoformat(),
            "deal_amount": row.deal_amount,
            "net_inflow": row.net_inflow,
            "net_inflow_available": row.net_inflow is not None,
            "source": row.source,
            "frequency": "盘后披露",
            "is_target_date": row.trade_date == target,
            "note": "现行公开口径优先展示北向成交额；净买入停止公开时保持为空。",
        }

    async def _cached_industry_flow(self, target: date) -> list[dict]:
        try:
            async with async_session() as session:
                rows = list((await session.execute(
                    select(IndustryFundFlowDaily, MarketBoard.name)
                    .outerjoin(
                        MarketBoard,
                        (MarketBoard.board_type == "industry")
                        & (MarketBoard.code == IndustryFundFlowDaily.board_code),
                    )
                    .where(IndustryFundFlowDaily.trade_date == target)
                    .order_by(desc(IndustryFundFlowDaily.main_net_inflow))
                )).all())
        except Exception:
            rows = []
        return [{
            "code": row.board_code,
            "name": name or row.board_code,
            "change_pct": row.change_pct,
            "main_net_inflow": row.main_net_inflow,
            "main_net_inflow_pct": row.main_net_inflow_pct,
            "super_large_net_inflow": row.super_large_net_inflow,
            "large_net_inflow": row.large_net_inflow,
            "up_count": row.up_count,
            "down_count": row.down_count,
            "data_date": target.isoformat(),
            "source": "database_cache",
        } for row, name in rows]

    async def _history_features(self, codes: list[str], target: date) -> dict[str, dict]:
        if not codes:
            return {}
        try:
            async with async_session() as session:
                rows = list((await session.execute(
                    select(StockDailyBar)
                    .where(
                        StockDailyBar.stock_code.in_(codes),
                        StockDailyBar.trade_date <= target,
                        StockDailyBar.trade_date >= target - timedelta(days=20),
                    )
                    .order_by(StockDailyBar.stock_code, StockDailyBar.trade_date)
                )).scalars().all())
        except Exception:
            rows = []
        grouped: dict[str, list[StockDailyBar]] = defaultdict(list)
        for row in rows:
            grouped[row.stock_code].append(row)
        output = {}
        for code, items in grouped.items():
            recent = items[-6:]
            first_close = _number(recent[0].close_price) if len(recent) >= 6 else None
            last_close = _number(recent[-1].close_price) if recent else None
            return_5d = (
                (last_close / first_close - 1) * 100
                if last_close is not None and first_close not in (None, 0)
                else None
            )
            output[code] = {
                "return_5d_pct": round(return_5d, 2) if return_5d is not None else None,
                "history_sessions": len(recent),
                "history_source": "stock_daily_bars",
            }
        return output

    @staticmethod
    def _intraday_view(row: StockIntradayEvidence) -> dict:
        return {
            "stock_code": row.stock_code,
            "stock_name": row.stock_name,
            "trade_date": row.trade_date.isoformat(),
            "latest_bar_at": row.latest_bar_at.isoformat(timespec="minutes") if row.latest_bar_at else None,
            "last_price": row.last_price,
            "vwap": row.vwap,
            "vwap_distance_pct": row.vwap_distance_pct,
            "above_vwap": row.above_vwap,
            "minute_bar_count": row.minute_bar_count,
            "active_buy_amount": row.active_buy_amount,
            "active_sell_amount": row.active_sell_amount,
            "neutral_amount": row.neutral_amount,
            "active_net_amount": row.active_net_amount,
            "active_buy_ratio": row.active_buy_ratio,
            "active_direction": row.active_direction,
            "trade_detail_count": row.trade_detail_count,
            "trade_detail_complete": row.trade_detail_complete,
            "source": row.source,
            "is_realtime": bool(row.is_realtime),
        }

    async def _cached_intraday_evidence(self, codes: list[str], target: date) -> dict[str, dict]:
        if not codes:
            return {}
        try:
            async with async_session() as session:
                rows = list((await session.execute(
                    select(StockIntradayEvidence).where(
                        StockIntradayEvidence.stock_code.in_(codes),
                        StockIntradayEvidence.trade_date == target,
                    )
                )).scalars().all())
        except Exception:
            rows = []
        return {row.stock_code: self._intraday_view(row) for row in rows}

    @staticmethod
    async def _persist_intraday(minute_payloads: list[dict], evidence_rows: list[dict]) -> None:
        minute_rows = []
        for payload in minute_payloads:
            for item in payload.get("bars") or []:
                try:
                    bar_time = datetime.fromisoformat(str(item.get("bar_time") or ""))
                except ValueError:
                    continue
                minute_rows.append({
                    "stock_code": str(item.get("stock_code") or payload.get("stock_code") or ""),
                    "stock_name": str(item.get("stock_name") or payload.get("stock_name") or ""),
                    "bar_time": bar_time.replace(tzinfo=None),
                    "interval_minutes": int(item.get("interval_minutes") or 1),
                    "open_price": _number(item.get("open")),
                    "close_price": _number(item.get("close")),
                    "high_price": _number(item.get("high")),
                    "low_price": _number(item.get("low")),
                    "volume": _integer(item.get("volume")),
                    "amount": _integer(item.get("amount")),
                    "average_price": _number(item.get("average")),
                    "source": str(payload.get("source") or "eastmoney"),
                    "updated_at": datetime.utcnow(),
                })
        try:
            async with async_session() as session:
                insert = postgresql_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
                for model, rows, keys in (
                    (StockMinuteBar, minute_rows, ["stock_code", "bar_time", "interval_minutes"]),
                    (StockIntradayEvidence, evidence_rows, ["stock_code", "trade_date"]),
                ):
                    for start in range(0, len(rows), 500):
                        batch = rows[start:start + 500]
                        if not batch:
                            continue
                        statement = insert(model).values(batch)
                        updates = {
                            column.name: getattr(statement.excluded, column.name)
                            for column in model.__table__.columns
                            if column.name not in {"id", *keys}
                        }
                        await session.execute(statement.on_conflict_do_update(index_elements=keys, set_=updates))
                await session.commit()
        except Exception as exc:
            print(f"Topic intraday evidence persistence failed: {type(exc).__name__}")

    async def _intraday_evidence(
        self,
        topics: list[dict],
        target: date,
        live_requested: bool,
    ) -> tuple[dict[str, dict], dict]:
        codes: list[str] = []
        for topic in topics:
            for stock in [topic.get("leader") or {}, *(topic.get("members") or [])]:
                code = str(stock.get("code") or "")
                if code and code not in codes:
                    codes.append(code)
                if len(codes) >= MAX_INTRADAY_STOCKS:
                    break
            if len(codes) >= MAX_INTRADAY_STOCKS:
                break
        candidate_codes = list(dict.fromkeys(
            str(stock.get("code") or "")
            for topic in topics
            for stock in topic.get("members") or []
            if stock.get("code")
        ))
        cached = await self._cached_intraday_evidence(candidate_codes, target)
        if not live_requested or not codes:
            vwap_covered = sum(item.get("vwap") is not None for item in cached.values())
            active_covered = sum(item.get("active_direction") is not None for item in cached.values())
            return cached, {
                "candidate_count": len(candidate_codes),
                "requested": 0,
                "captured": len(cached),
                "vwap_covered": vwap_covered,
                "active_direction_covered": active_covered,
                "capture_scope": "database_cache",
            }

        async def fetch_one(code: str) -> tuple[str, dict, dict]:
            minute, trades = await asyncio.gather(
                self._safe(collector.fetch_stock_minute_trends(code, days=1), {}, 7.0),
                self._safe(collector.fetch_stock_trade_details(code, limit=500), {}, 7.0),
            )
            return code, minute if isinstance(minute, dict) else {}, trades if isinstance(trades, dict) else {}

        results = await asyncio.gather(*(fetch_one(code) for code in codes))
        minute_payloads: list[dict] = []
        rows: list[dict] = []
        fresh: dict[str, dict] = {}
        now = shanghai_now()
        for code, minute, trades in results:
            if _date(minute.get("data_date")) != target:
                minute = {}
            bars = list(minute.get("bars") or [])
            if bars:
                minute_payloads.append(minute)
            latest = bars[-1] if bars else {}
            last_price = _number(latest.get("close"))
            vwap = _number(latest.get("average"))
            if vwap is None and bars:
                total_amount = sum(_number(item.get("amount")) or 0 for item in bars)
                total_volume = sum(_number(item.get("volume")) or 0 for item in bars)
                vwap = total_amount / total_volume if total_amount > 0 and total_volume > 0 else None
            distance = (
                (last_price / vwap - 1) * 100
                if last_price is not None and vwap not in (None, 0) else None
            )
            latest_bar_at = None
            try:
                latest_bar_at = datetime.fromisoformat(str(latest.get("bar_time") or "")).replace(tzinfo=None)
            except ValueError:
                pass
            active_direction = trades.get("active_direction") if trades.get("detail_count") else None
            evidence = {
                "stock_code": code,
                "stock_name": str(minute.get("stock_name") or ""),
                "trade_date": target,
                "latest_bar_at": latest_bar_at,
                "last_price": last_price,
                "vwap": vwap,
                "vwap_distance_pct": round(distance, 4) if distance is not None else None,
                "above_vwap": last_price >= vwap if last_price is not None and vwap is not None else None,
                "minute_bar_count": len(bars),
                "active_buy_amount": _integer(trades.get("active_buy_amount")),
                "active_sell_amount": _integer(trades.get("active_sell_amount")),
                "neutral_amount": _integer(trades.get("neutral_amount")),
                "active_net_amount": _integer(trades.get("active_net_amount")),
                "active_buy_ratio": _number(trades.get("active_buy_ratio")),
                "active_direction": active_direction,
                "trade_detail_count": int(trades.get("detail_count") or 0),
                "trade_detail_complete": bool(trades.get("complete")),
                "source": "+".join(filter(None, [minute.get("source"), trades.get("source")])) or "unavailable",
                "is_realtime": bool(minute.get("is_realtime") and target == now.date()),
                "updated_at": datetime.utcnow(),
            }
            if bars or trades.get("detail_count"):
                rows.append(evidence)
                view = dict(evidence)
                view["trade_date"] = target.isoformat()
                view["latest_bar_at"] = latest_bar_at.isoformat(timespec="minutes") if latest_bar_at else None
                view.pop("updated_at", None)
                fresh[code] = view
        if rows:
            await self._persist_intraday(minute_payloads, rows)
        merged = {**cached, **fresh}
        return merged, {
            "candidate_count": len(candidate_codes),
            "requested": len(codes),
            "captured": len(merged),
            "vwap_covered": sum(item.get("vwap") is not None for item in merged.values()),
            "active_direction_covered": sum(item.get("active_direction") is not None for item in merged.values()),
            "capture_scope": f"题材核心与前排最多{MAX_INTRADAY_STOCKS}只",
        }

    @staticmethod
    def _attach_intraday(topics: list[dict], evidence: dict[str, dict]) -> None:
        for topic in topics:
            for stock in topic.get("members") or []:
                intraday = evidence.get(str(stock.get("code") or ""))
                stock["intraday"] = intraday
                if not intraday or intraday.get("vwap") is None:
                    stock["data_gaps"].append("分时均价线")
                if not intraday or intraday.get("active_direction") is None:
                    stock["data_gaps"].append("主动买卖方向")
            leader_code = str((topic.get("leader") or {}).get("code") or "")
            topic["leader"] = next(
                (item for item in topic.get("members") or [] if str(item.get("code") or "") == leader_code),
                topic.get("leader"),
            )

    @staticmethod
    def _approximate_limit_rows(stocks: list[dict], direction: str) -> list[dict]:
        rows = []
        for stock in stocks:
            code = str(stock.get("code") or "")
            change = _number(stock.get("change_pct"))
            if not code or change is None:
                continue
            limit_pct = _limit_pct(code)
            matched = change >= limit_pct - 0.25 if direction == "up" else change <= -limit_pct + 0.25
            if not matched:
                continue
            rows.append({
                **stock,
                "continuous_days": None,
                "first_limit_time": None,
                "last_limit_time": None,
                "limit_direction": direction,
                "event_source": "daily_quote_approximation",
            })
        return rows

    async def _previous_topic_names(self, target: date) -> set[str] | None:
        key = f"{TOPIC_CACHE_PREFIX}{target.isoformat()}"
        try:
            async with async_session() as session:
                row = (await session.execute(
                    select(MarketDataCache)
                    .where(
                        MarketDataCache.key.like(f"{TOPIC_CACHE_PREFIX}%"),
                        MarketDataCache.key < key,
                    )
                    .order_by(desc(MarketDataCache.key))
                    .limit(1)
                )).scalar_one_or_none()
        except Exception:
            row = None
        if not row or not isinstance(row.payload, dict):
            return None
        return {
            str(item.get("name") or "")
            for item in row.payload.get("topics") or []
            if str(item.get("name") or "")
        }

    @staticmethod
    def _snapshot_sector_metrics(stocks: list[dict]) -> dict[str, dict]:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for stock in stocks:
            sector = str(stock.get("sector") or "").strip()
            if sector and _number(stock.get("change_pct")) is not None:
                grouped[_sector_key(sector)].append(stock)
        output = {}
        for key, members in grouped.items():
            changes = [_number(item.get("change_pct")) for item in members]
            changes = [item for item in changes if item is not None]
            up = sum(item > 0 for item in changes)
            down = sum(item < 0 for item in changes)
            breadth, _ = _breadth_label(up, down)
            output[key] = {
                "member_count": len(changes),
                "up_count": up,
                "down_count": down,
                "breadth": breadth,
                "avg_change_pct": round(sum(changes) / len(changes), 2) if changes else None,
                "source": "complete_market_snapshot",
            }
        return output

    def _build_topics(
        self,
        limit_rows: list[dict],
        snapshot_stocks: list[dict],
        industry_flow: list[dict],
        history_features: dict[str, dict],
        previous_names: set[str] | None,
    ) -> list[dict]:
        quote_by_code = {str(item.get("code") or ""): item for item in snapshot_stocks}
        flow_by_sector = {_sector_key(item.get("name")): item for item in industry_flow if item.get("name")}
        flow_rank = {
            _sector_key(item.get("name")): rank
            for rank, item in enumerate(
                sorted(industry_flow, key=lambda row: _number(row.get("main_net_inflow")) or -math.inf, reverse=True),
                start=1,
            )
        }
        sector_metrics = self._snapshot_sector_metrics(snapshot_stocks)

        groups: dict[str, list[dict]] = defaultdict(list)
        for raw in limit_rows:
            code = str(raw.get("code") or "")
            quote = quote_by_code.get(code, {})
            sector = str(raw.get("sector") or quote.get("sector") or "未标注题材").strip()
            history = history_features.get(code, {})
            return_5d = _number(history.get("return_5d_pct"))
            turnover = _number(raw.get("turnover"))
            if turnover is None:
                turnover = _number(quote.get("turnover"))
            raw_boards = _integer(raw.get("continuous_days"))
            boards_verified = raw_boards is not None and raw_boards > 0
            boards = max(raw_boards or 1, 1)
            heat_fields_available = sum(item is not None for item in (return_5d, turnover, raw_boards))
            overheated = bool(
                (return_5d is not None and return_5d >= 25)
                or (turnover is not None and turnover >= 35)
                or (boards_verified and boards >= 4)
            )
            heat_status = "过热" if overheated else "可观察" if heat_fields_available >= 2 else "待核验"
            stock = {
                "code": code,
                "name": str(raw.get("name") or quote.get("name") or code),
                "boards": boards,
                "boards_verified": boards_verified,
                "price": _number(raw.get("price")) if _number(raw.get("price")) is not None else _number(quote.get("price")),
                "pct": _number(raw.get("change_pct")) if _number(raw.get("change_pct")) is not None else _number(quote.get("change_pct")),
                "amount": _integer(raw.get("amount")) if _integer(raw.get("amount")) is not None else _integer(quote.get("amount")),
                "turnover": turnover,
                "industry": sector,
                "first_limit_time": raw.get("first_limit_time"),
                "main_net_inflow": _integer(quote.get("main_net_inflow")),
                "seal_amount": _integer(raw.get("seal_amount")),
                "return_5d_pct": return_5d,
                "heat_status": heat_status,
                "overheated": overheated if heat_fields_available >= 2 else None,
                "event_source": raw.get("event_source") or "eastmoney_limit_pool",
                "data_gaps": [
                    label for value, label in (
                        (return_5d, "近5日涨幅"),
                        (turnover, "换手率"),
                        (raw_boards, "连板高度"),
                        (raw.get("first_limit_time"), "首次触板时间"),
                    ) if value in (None, "")
                ],
            }
            groups[sector].append(stock)

        topics = []
        for name, members in groups.items():
            members.sort(
                key=lambda item: (
                    item["boards"] if item["boards_verified"] else 0,
                    item["amount"] if item["amount"] is not None else -1,
                    item["pct"] if item["pct"] is not None else -math.inf,
                ),
                reverse=True,
            )
            leader = members[0]
            key = _sector_key(name)
            flow = flow_by_sector.get(key, {})
            metrics = sector_metrics.get(key, {})
            up_count = _integer(flow.get("up_count"))
            down_count = _integer(flow.get("down_count"))
            flow_breadth, _ = _breadth_label(up_count or 0, down_count or 0)
            breadth = _number(metrics.get("breadth"))
            breadth_source = metrics.get("source")
            if breadth is None:
                breadth = flow_breadth
                breadth_source = "industry_flow_cache" if flow_breadth is not None else None
            rank = flow_rank.get(key)
            flow_bonus = max(0, 16 - rank) if rank is not None else 0
            verified_board_height = leader["boards"] if leader["boards_verified"] else 0
            strength_score = min(
                100.0,
                verified_board_height * 14
                + min(len(members), 5) * 10
                + (breadth or 0) * 0.25
                + flow_bonus,
            )
            linked = len(members) >= 2
            pool_verified = all(item["event_source"] == "eastmoney_limit_pool" for item in members)
            strong = pool_verified and linked and breadth is not None and breadth >= 55 and strength_score >= 55
            novelty = (
                "待核验" if previous_names is None
                else "新出现" if name not in previous_names
                else "延续"
            )
            topics.append({
                "name": name,
                "members": members[:MAX_MEMBERS],
                "leader": leader,
                "member_count": len(members),
                "breadth": round(breadth, 1) if breadth is not None else None,
                "breadth_source": breadth_source,
                "sector_flow_rank": rank,
                "sector_main_net_inflow": _integer(flow.get("main_net_inflow")),
                "sector_change_pct": _number(flow.get("change_pct")),
                "strength_score": round(strength_score, 1),
                "status": "强" if strong else "观察",
                "novelty": novelty,
                "evidence": (
                    f"涨停联动{len(members)}只，"
                    + (f"最高{leader['boards']}连板，" if leader["boards_verified"] else "连板高度待核验，")
                    + (f"板块上涨宽度{breadth:.1f}%" if breadth is not None else "板块上涨宽度待补")
                    + (f"，资金排名第{rank}" if rank is not None else "，资金排名待补")
                ),
                "audit": {
                    "facts": [
                        f"涨停池同标签股票{len(members)}只",
                        f"最高连板高度{leader['boards']}" if leader["boards_verified"] else "连板高度待核验",
                    ],
                    "inferences": [f"综合强度{strength_score:.1f}，当前标记为{'强' if strong else '观察'}"],
                    "gaps": [
                        label for value, label in (
                            (breadth, "板块上涨宽度"),
                            (rank, "板块资金排名"),
                        ) if value is None
                    ],
                },
            })
        topics.sort(
            key=lambda item: (
                item["status"] == "强",
                item["strength_score"],
                item["leader"]["boards"],
                item["member_count"],
            ),
            reverse=True,
        )
        for rank, topic in enumerate(topics[:MAX_TOPICS], start=1):
            topic["rank"] = rank
        return topics[:MAX_TOPICS]

    @staticmethod
    def _market_payload(
        sentiment: dict,
        snapshot_stocks: list[dict],
        limit_up: dict,
        limit_down: dict,
        failed: dict,
        industry_flow: list[dict],
        northbound: dict | None = None,
        intraday_coverage: dict | None = None,
    ) -> dict:
        northbound = northbound or {
            "available": False,
            "data_date": None,
            "deal_amount": None,
            "net_inflow": None,
            "net_inflow_available": False,
            "source": "unavailable",
            "frequency": "盘后披露",
        }
        intraday_coverage = intraday_coverage or {
            "candidate_count": 0,
            "requested": 0,
            "captured": 0,
            "vwap_covered": 0,
            "active_direction_covered": 0,
            "capture_scope": "unavailable",
        }
        if snapshot_stocks:
            changes = [_number(item.get("change_pct")) for item in snapshot_stocks]
            changes = [item for item in changes if item is not None]
            up = sum(item > 0 for item in changes)
            down = sum(item < 0 for item in changes)
            flat = len(changes) - up - down
            total = len(changes)
            sentiment_source = "complete_market_snapshot"
        else:
            up = _integer(sentiment.get("up_count")) or 0
            down = _integer(sentiment.get("down_count")) or 0
            flat = _integer(sentiment.get("flat_count")) or 0
            total = _integer(sentiment.get("stock_count")) or up + down + flat
            sentiment_source = sentiment.get("source") or "unavailable"
        up_ratio, breadth = _breadth_label(up, down)

        snapshot_amounts = [
            value for item in snapshot_stocks
            if (value := _integer(item.get("amount"))) is not None and value >= 0
        ]
        market_amount = sum(snapshot_amounts) if snapshot_amounts else _integer(sentiment.get("market_amount"))
        amount_count = len(snapshot_amounts) if snapshot_amounts else _integer(sentiment.get("amount_count")) or 0
        amount_complete = bool(total >= 1_000 and amount_count >= total * 0.9)

        zt = _integer(limit_up.get("total")) if limit_up.get("verified") else None
        dt = _integer(limit_down.get("total")) if limit_down.get("verified") else None
        zb = _integer(failed.get("total")) if failed.get("verified") else None
        if zt is None:
            zt = _integer(sentiment.get("limit_up_count"))
        if dt is None:
            dt = _integer(sentiment.get("limit_down_count"))
        if zb is None:
            zb = _integer(sentiment.get("failed_limit_count"))
        break_rate = (
            round((zb or 0) / max((zt or 0) + (zb or 0), 1) * 100, 2)
            if zt is not None and zb is not None
            else _number(sentiment.get("failed_limit_rate"))
        )
        up_seals = [
            value for item in limit_up.get("stocks") or []
            if (value := _integer(item.get("seal_amount"))) is not None
        ] if limit_up.get("verified") else []
        down_seals = [
            value for item in limit_down.get("stocks") or []
            if (value := _integer(item.get("seal_amount"))) is not None
        ] if limit_down.get("verified") else []
        up_pool_count = len(limit_up.get("stocks") or []) if limit_up.get("verified") else 0
        down_pool_count = len(limit_down.get("stocks") or []) if limit_down.get("verified") else 0

        top_sectors = []
        for rank, row in enumerate(
            sorted(industry_flow, key=lambda item: _number(item.get("main_net_inflow")) or -math.inf, reverse=True)[:10],
            start=1,
        ):
            top_sectors.append({
                "rank": rank,
                "code": str(row.get("code") or ""),
                "name": str(row.get("name") or ""),
                "change_pct": _number(row.get("change_pct")),
                "main_net_inflow": _integer(row.get("main_net_inflow")),
                "up_count": _integer(row.get("up_count")),
                "down_count": _integer(row.get("down_count")),
                "source": row.get("source") or "eastmoney",
            })
        return {
            "sentiment": {
                "up": up,
                "down": down,
                "flat": flat,
                "total": total,
                "up_ratio": up_ratio,
                "breadth": breadth,
                "source": sentiment_source,
            },
            "liquidity": {
                "market_amount": market_amount,
                "amount_count": amount_count,
                "amount_complete": amount_complete,
                "average_turnover": _number(sentiment.get("average_turnover")),
                "source": "complete_market_snapshot" if snapshot_amounts else sentiment.get("source") or "unavailable",
            },
            "emotion": {
                "zt_count": zt,
                "dt_count": dt,
                "zb_count": zb,
                "break_rate": break_rate,
                "limit_up_seal_amount": sum(up_seals) if up_seals else None,
                "limit_down_seal_amount": sum(down_seals) if down_seals else None,
                "limit_up_seal_coverage": {
                    "covered": len(up_seals),
                    "total": up_pool_count,
                },
                "limit_down_seal_coverage": {
                    "covered": len(down_seals),
                    "total": down_pool_count,
                },
                "seal_amount_semantic": "涨跌停池fund字段：盘口封单资金，不是主力净流入",
                "source": "limit_pool" if any(item.get("verified") for item in (limit_up, limit_down, failed)) else sentiment_source,
            },
            "northbound": northbound,
            "intraday": intraday_coverage,
            "top_sectors": top_sectors,
            "note": "交易时段优先实时源；闭市、周末和上游不可用时读取最近核验缓存。",
        }

    @staticmethod
    def _steps(topics: list[dict], market: dict, previous_names: set[str] | None) -> list[dict]:
        strong = [item for item in topics if item["status"] == "强"]
        new_topics = [item for item in topics if item["novelty"] == "新出现"]
        linked = [item for item in topics if item["member_count"] >= 2]
        leaders = [item["leader"] for item in topics[:3]]
        overheated = [stock for topic in topics for stock in topic["members"] if stock.get("overheated")]
        heat_unknown = [stock for topic in topics for stock in topic["members"] if stock.get("overheated") is None]
        intraday = market.get("intraday") or {}
        intraday_candidates = int(intraday.get("candidate_count") or 0)
        vwap_covered = int(intraday.get("vwap_covered") or 0)
        active_covered = int(intraday.get("active_direction_covered") or 0)
        intraday_available = bool(vwap_covered and active_covered)
        return [
            {
                "step": 1,
                "title": "新题材是否出现",
                "classification": "数据缺口" if previous_names is None else "事实",
                "result": (
                    "缺少上一交易日题材快照，不能把当前热点宣称为新题材。"
                    if previous_names is None
                    else f"相对上一缓存交易日，新出现{len(new_topics)}个题材："
                    + ("、".join(item["name"] for item in new_topics[:4]) or "暂无")
                ),
            },
            {
                "step": 2,
                "title": "新题材强度",
                "classification": "推断",
                "result": f"按连板高度、涨停成员、板块宽度和资金排名，强题材{len(strong)}个。",
            },
            {
                "step": 3,
                "title": "板块联动",
                "classification": "事实",
                "result": f"同标签至少2只涨停的题材{len(linked)}个；缺少宽度的题材不会判为强。",
            },
            {
                "step": 4,
                "title": "最先弱转强核心",
                "classification": "推断",
                "result": "观察顺序：" + (
                    "、".join(
                        f"{item['name']}({item['boards']}板)" if item.get("boards_verified") else f"{item['name']}(连板待核验)"
                        for item in leaders
                    ) or "暂无可核验核心"
                ),
            },
            {
                "step": 5,
                "title": "排除过热",
                "classification": "事实" if not heat_unknown else "数据缺口",
                "result": f"已标记过热{len(overheated)}只，过热字段不完整{len(heat_unknown)}只；未知不按通过处理。",
            },
            {
                "step": 6,
                "title": "分时与资金确认",
                "classification": "事实" if intraday_available else "数据缺口",
                "result": (
                    f"候选共{intraday_candidates}只，已核验分时均价线{vwap_covered}只、"
                    f"主动买卖明细方向{active_covered}只；逐股结果见题材明细。"
                    if intraday_available
                    else f"候选共{intraday_candidates}只，分时均价线覆盖{vwap_covered}只、"
                    f"主动买卖方向覆盖{active_covered}只；未覆盖标的必须盘中人工确认。"
                ),
            },
            {
                "step": 7,
                "title": "执行窗口",
                "classification": "边界",
                "result": "只生成研究观察顺序，不自动荐股、不自动下单。",
            },
            {
                "step": 8,
                "title": "强度失效退出",
                "classification": "规则",
                "result": "板块宽度跌破50%、资金排名明显回落、龙头断板或市场普跌时，强度标记失效。",
            },
        ]

    async def get(self, target_date: date | None = None, force: bool = False) -> dict:
        effective = await self._resolve_date(target_date)
        cache_key = f"{TOPIC_CACHE_PREFIX}{effective.isoformat()}"
        cached = await self._read_cache(cache_key)
        live_requested = effective == shanghai_now().date() and is_a_share_market_session(shanghai_now())
        if cached and not force and (not live_requested or self._cache_fresh(cached)):
            return {**cached, "source": "database_cache", "is_realtime": False if not live_requested else bool(cached.get("is_realtime")), "cache_hit": True}

        snapshot = await load_quant_market_snapshot()
        if _date(snapshot.get("data_date")) != effective:
            snapshot = {}
        if live_requested and (force or not snapshot.get("is_realtime")):
            fetched = await self._safe(collector.fetch_quant_market_snapshot(include_special=True), {}, 15.0)
            if fetched.get("stocks"):
                snapshot = fetched
                await save_quant_market_snapshot(fetched)
        snapshot_stocks = list(snapshot.get("stocks") or [])

        sentiment_task = self._cached_sentiment(effective)
        if live_requested:
            industry_task = self._safe(collector.fetch_industry_flow(page_size=100), [], 10.0)
        else:
            industry_task = self._cached_industry_flow(effective)
        limit_up_task = self._safe(collector.fetch_limit_up_pool(page_size=500, target_date=effective), {"stocks": [], "total": 0, "trade_date": None})
        limit_down_task = self._safe(collector.fetch_limit_down_pool(page_size=500, target_date=effective), {"stocks": [], "total": 0, "trade_date": None})
        failed_task = self._safe(collector.fetch_failed_limit_pool(page_size=500, target_date=effective), {"stocks": [], "total": 0, "trade_date": None})
        sentiment, industry_flow, limit_up, limit_down, failed = await asyncio.gather(
            sentiment_task, industry_task, limit_up_task, limit_down_task, failed_task,
        )
        limit_up = self._verified_pool(limit_up, effective)
        limit_down = self._verified_pool(limit_down, effective)
        failed = self._verified_pool(failed, effective)

        if not industry_flow:
            industry_flow = await self._cached_industry_flow(effective)
        limit_rows = list(limit_up.get("stocks") or [])
        event_source = "eastmoney_limit_pool" if limit_up.get("verified") else "unavailable"
        if not limit_rows and not limit_up.get("verified") and snapshot_stocks:
            limit_rows = self._approximate_limit_rows(snapshot_stocks, "up")
            event_source = "daily_quote_approximation"
            limit_up = {
                "stocks": limit_rows,
                "total": len(limit_rows),
                "trade_date": effective.isoformat(),
                "approximated": True,
                "verified": False,
            }

        codes = [str(item.get("code") or "") for item in limit_rows if item.get("code")]
        history_features, previous_names = await asyncio.gather(
            self._history_features(codes, effective),
            self._previous_topic_names(effective),
        )
        topics = self._build_topics(limit_rows, snapshot_stocks, industry_flow, history_features, previous_names)
        intraday_result, northbound = await asyncio.gather(
            self._intraday_evidence(topics, effective, live_requested),
            self._northbound_snapshot(effective),
        )
        evidence_by_code, intraday_coverage = intraday_result
        self._attach_intraday(topics, evidence_by_code)
        market = self._market_payload(
            sentiment,
            snapshot_stocks,
            limit_up,
            limit_down,
            failed,
            industry_flow,
            northbound,
            intraday_coverage,
        )
        steps = self._steps(topics, market, previous_names)
        gaps = []
        if previous_names is None:
            gaps.append("上一交易日题材快照")
        if not snapshot_stocks:
            gaps.append("完整全市场个股快照")
        if not industry_flow:
            gaps.append("行业资金与上涨宽度")
        if event_source != "eastmoney_limit_pool":
            gaps.append("源生涨停池与连板高度")
        candidate_count = int(intraday_coverage.get("candidate_count") or 0)
        vwap_covered = int(intraday_coverage.get("vwap_covered") or 0)
        active_covered = int(intraday_coverage.get("active_direction_covered") or 0)
        if candidate_count and vwap_covered < candidate_count:
            gaps.append(f"逐股分时均价线（{vwap_covered}/{candidate_count}）")
        if candidate_count and active_covered < candidate_count:
            gaps.append(f"盘中主动买卖方向（{active_covered}/{candidate_count}）")
        if market["liquidity"].get("market_amount") is None:
            gaps.append("全市场成交额")
        seal_coverage = market["emotion"].get("limit_up_seal_coverage") or {}
        seal_total = int(seal_coverage.get("total") or 0)
        seal_covered = int(seal_coverage.get("covered") or 0)
        if seal_total and seal_covered < seal_total:
            gaps.append(f"涨停封单资金（{seal_covered}/{seal_total}）")
        if not northbound.get("available"):
            gaps.append("北向盘后成交披露")

        now = shanghai_now()
        is_realtime = bool(
            live_requested
            and snapshot.get("is_realtime")
            and _normalize_trade_date(limit_up.get("trade_date")) == effective.isoformat()
        )
        payload = {
            "available": bool(topics or market["sentiment"]["total"] or industry_flow),
            "updated": now.strftime("%Y-%m-%d %H:%M"),
            "updated_at": now.isoformat(),
            "data_date": effective.isoformat(),
            "is_realtime": is_realtime,
            "source": "+".join(filter(None, [
                snapshot.get("source") if snapshot_stocks else None,
                event_source if event_source != "unavailable" else None,
                "industry_flow_cache" if industry_flow and not live_requested else "eastmoney_industry_flow" if industry_flow else None,
            ])) or "unavailable",
            "cache_hit": False,
            "market": market,
            "topics": topics,
            "steps": steps,
            "risk": [
                "本模块只做客观数据聚合与研究观察，不自动荐股、不自动下单。",
                "题材标签来自个股行业字段，不等同于完整概念题材成分表。",
                "分时与主动买卖明细按实际覆盖显示；未采集标的不按满足处理。",
                "连板与资金数据可能来自不同更新时间，必须以数据日和来源标记为准。",
                "大盘普跌、板块宽度收缩或龙头断板时，当前强度结论失效。",
            ],
            "data_quality": {
                "complete_market_snapshot": bool(snapshot_stocks and snapshot.get("complete")),
                "limit_pool": bool(limit_up.get("verified")),
                "industry_flow": bool(industry_flow),
                "sentiment_cache": bool(sentiment),
                "market_amount": market["liquidity"].get("market_amount") is not None,
                "northbound_disclosure": bool(northbound.get("available")),
                "limit_seal_amount": market["emotion"].get("limit_up_seal_amount") is not None,
                "intraday": intraday_coverage,
                "missing_fields": list(dict.fromkeys(gaps)),
                "missing_policy": "未知字段不计为通过，事实、推断与数据缺口分层展示。",
            },
            "method": "按行业标签聚合涨停池，以连板高度、涨停联动、板块上涨宽度和资金排名生成可审计强度。",
        }
        if payload["available"]:
            await asyncio.gather(
                self._write_cache(cache_key, payload),
                self._write_cache(TOPIC_LATEST_CACHE_KEY, payload),
            )
        elif cached:
            return {**cached, "source": "database_cache", "is_realtime": False, "cache_hit": True}
        return payload

    @staticmethod
    def _fallback_report(snapshot: dict) -> str:
        market = snapshot.get("market") or {}
        sentiment = market.get("sentiment") or {}
        emotion = market.get("emotion") or {}
        liquidity = market.get("liquidity") or {}
        northbound = market.get("northbound") or {}
        topics = snapshot.get("topics") or []
        leaders = "、".join(
            f"{item['leader']['name']}（{item['name']}，{item['leader']['boards']}板）"
            for item in topics[:3]
        ) or "暂无可核验龙头"
        return (
            "## 市场环境\n"
            f"数据日 {snapshot.get('data_date')}，市场{sentiment.get('breadth', '数据不足')}；"
            f"涨停 {emotion.get('zt_count')}、炸板 {emotion.get('zb_count')}；"
            f"全市场成交额 {liquidity.get('market_amount') if liquidity.get('market_amount') is not None else '待核验'} 元；"
            f"涨停封单资金 {emotion.get('limit_up_seal_amount') if emotion.get('limit_up_seal_amount') is not None else '待核验'} 元；"
            f"北向成交额 {northbound.get('deal_amount') if northbound.get('available') else '待核验'} 元"
            f"（披露日 {northbound.get('data_date') or '--'}）。\n\n"
            "## 题材强弱排序\n"
            + ("\n".join(f"{item['rank']}. {item['name']}：{item['status']}，{item['evidence']}。" for item in topics[:6]) or "暂无完整题材样本。")
            + "\n\n## 候选龙头（只作观察）\n"
            + leaders
            + "\n\n## 风险与待确认\n"
            + "\n".join(f"- {item}" for item in snapshot.get("risk") or [])
        )

    async def analyze(
        self,
        target_date: date | None = None,
        *,
        force: bool = False,
        use_ai: bool = True,
    ) -> dict:
        snapshot = await self.get(target_date, force=force)
        compact = {
            "data_date": snapshot.get("data_date"),
            "market": snapshot.get("market"),
            "topics": [
                {**topic, "members": topic.get("members", [])[:5]}
                for topic in snapshot.get("topics", [])[:8]
            ],
            "steps": snapshot.get("steps"),
            "risk": snapshot.get("risk"),
            "data_quality": snapshot.get("data_quality"),
        }
        report = None
        if use_ai and ai_service.client and snapshot.get("available"):
            prompt = (
                "严格按八步分析A股市场环境与题材强弱。只依据JSON，明确区分【事实】【推断】【数据缺口】；"
                "禁止自动下单、禁止承诺收益、禁止把缺失数据当满足。输出Markdown，结构为：市场环境、"
                "题材强弱排序、候选龙头（只作观察）、八步逐项结论、风险与待确认清单。\n"
                + json.dumps(compact, ensure_ascii=False)
            )
            try:
                generated = await asyncio.wait_for(
                    ai_service.generate(
                        prompt,
                        "你是A股题材强弱审计分析师，所有结论必须能追溯到输入数据。",
                    ),
                    timeout=25,
                )
                if generated and not generated.startswith("[AI服务"):
                    report = generated
            except Exception:
                report = None
        return {
            "available": snapshot.get("available", False),
            "data_date": snapshot.get("data_date"),
            "report": report or self._fallback_report(snapshot),
            "ai_generated": bool(report),
            "analysis_basis": compact,
            "snapshot": snapshot,
        }

    @staticmethod
    async def _cached_daily_rows(code: str, limit: int) -> list[dict]:
        try:
            async with async_session() as session:
                rows = list((await session.execute(
                    select(StockDailyBar)
                    .where(StockDailyBar.stock_code == code)
                    .order_by(desc(StockDailyBar.trade_date))
                    .limit(limit)
                )).scalars().all())
        except Exception:
            rows = []
        return [{
            "date": row.trade_date.isoformat(),
            "stock_name": row.stock_name,
            "open": row.open_price,
            "close": row.close_price,
            "high": row.high_price,
            "low": row.low_price,
            "volume": row.volume,
            "amount": row.amount,
            "change_pct": row.change_pct,
        } for row in reversed(rows)]

    async def kline(self, stock_code: str, category: int = 4, offset: int = 60) -> dict:
        code = normalize_stock_code(stock_code)
        if category not in {4, 5, 6, 11}:
            raise ValueError("category 仅支持 4(日)、5(周)、6(月)、11(60分钟)")
        limit = min(max(int(offset), 1), 800)
        warning = None
        if category == 11:
            try:
                payload = await asyncio.wait_for(
                    collector.fetch_stock_minute_history(code, interval_minutes=60, limit=limit),
                    timeout=15,
                )
            except Exception:
                payload = {}
            rows = [{
                "date": item.get("bar_time"),
                "open": item.get("open"),
                "close": item.get("close"),
                "high": item.get("high"),
                "low": item.get("low"),
                "volume": item.get("volume"),
                "amount": item.get("amount"),
                "change_pct": None,
            } for item in payload.get("bars") or []]
            source = payload.get("source") or "unavailable"
            name = payload.get("stock_name") or ""
            warning = payload.get("warning")
        else:
            requested_daily = 800 if category in {5, 6} else min(max(limit + 30, 120), 800)
            try:
                payload = await asyncio.wait_for(
                    collector.fetch_stock_price_history(code, requested_daily),
                    timeout=15,
                )
            except Exception:
                payload = {}
            rows = [{
                "date": item.get("trade_date") or item.get("date"),
                "open": item.get("open"),
                "close": item.get("close"),
                "high": item.get("high"),
                "low": item.get("low"),
                "volume": item.get("volume"),
                "amount": item.get("amount"),
                "change_pct": item.get("change_pct"),
            } for item in payload.get("history") or []]
            source = payload.get("source") or "unavailable"
            name = payload.get("name") or ""
            if not rows:
                rows = await self._cached_daily_rows(code, requested_daily)
                source = "database_cache" if rows else "unavailable"
                if rows and not name:
                    name = str(rows[-1].get("stock_name") or "")
                for row in rows:
                    row.pop("stock_name", None)
            if category in {5, 6}:
                rows = _aggregate_daily_rows(rows, category)
                source = f"{source}+daily_aggregation" if rows else source
        rows = rows[-limit:]
        data_date = str(rows[-1].get("date") or "")[:10] if rows else None
        return {
            "stock_code": code,
            "stock_name": name,
            "category": category,
            "category_label": {4: "日K", 5: "周K", 6: "月K", 11: "60分钟"}[category],
            "rows": rows,
            "count": len(rows),
            "available": bool(rows),
            "source": source,
            "data_date": data_date,
            "is_realtime": bool(category == 11 and data_date == shanghai_now().date().isoformat() and is_a_share_market_session(shanghai_now())),
            "warning": warning,
            "updated_at": shanghai_now().isoformat(),
        }


topic_strength_service = TopicStrengthService()
