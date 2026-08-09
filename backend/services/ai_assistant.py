"""Persistent assistant memory and selective, source-labelled market context."""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, desc, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from config import settings
from database import async_session
from models import (
    AIChatHistory,
    AIRobotJournal,
    ConceptFundFlowDaily,
    DragonBoardDaily,
    IndustryFundFlowDaily,
    MarketBoard,
    MarketDataCache,
    PersonalPoolItem,
    StockDailyBar,
    StockFundFlowDaily,
)
from services.data_collector import (
    collector,
    is_a_share_market_session,
    normalize_stock_code,
    shanghai_now,
)
from services.macro_dashboard import macro_dashboard_service
from services.macro_policy_news import macro_policy_news_collector
from services.quote_cache import quote_snapshot_service


MAX_HISTORY_MESSAGES = 80
MAX_CONTEXT_STOCKS = 5
CODE_PATTERN = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def _history_dict(row: AIChatHistory) -> dict[str, Any]:
    return {
        "id": row.id,
        "role": row.role,
        "content": row.content,
        "context_type": row.context_type,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class AIAssistantService:
    @staticmethod
    def normalize_user_id(value: object) -> str:
        candidate = re.sub(r"[^a-zA-Z0-9_.@-]", "", str(value or "web_user"))[:100]
        return candidate or "web_user"

    async def history(self, user_id: str, limit: int = MAX_HISTORY_MESSAGES) -> list[dict[str, Any]]:
        normalized = self.normalize_user_id(user_id)
        bounded = min(max(int(limit), 1), MAX_HISTORY_MESSAGES)
        async with async_session() as session:
            rows = list((await session.execute(
                select(AIChatHistory)
                .where(AIChatHistory.user_id == normalized)
                .order_by(desc(AIChatHistory.id))
                .limit(bounded)
            )).scalars().all())
        return [_history_dict(row) for row in reversed(rows)]

    async def save_message(self, user_id: str, role: str, content: str, context_type: str) -> dict[str, Any]:
        if role not in {"user", "assistant"}:
            raise ValueError("role 必须是 user 或 assistant")
        normalized = self.normalize_user_id(user_id)
        text = str(content or "").strip()
        if not text:
            raise ValueError("消息不能为空")
        async with async_session() as session:
            row = AIChatHistory(
                user_id=normalized,
                role=role,
                content=text[:30000],
                context_type=str(context_type or "beginner")[:50],
            )
            session.add(row)
            await session.flush()
            stale_ids = list((await session.execute(
                select(AIChatHistory.id)
                .where(AIChatHistory.user_id == normalized)
                .order_by(desc(AIChatHistory.id))
                .offset(MAX_HISTORY_MESSAGES)
            )).scalars().all())
            if stale_ids:
                await session.execute(delete(AIChatHistory).where(AIChatHistory.id.in_(stale_ids)))
            await session.commit()
            await session.refresh(row)
        return _history_dict(row)

    async def clear_history(self, user_id: str) -> int:
        normalized = self.normalize_user_id(user_id)
        async with async_session() as session:
            result = await session.execute(delete(AIChatHistory).where(AIChatHistory.user_id == normalized))
            await session.commit()
        return int(result.rowcount or 0)

    @staticmethod
    def _has(message: str, *terms: str) -> bool:
        lowered = message.lower()
        return any(term.lower() in lowered for term in terms)

    async def _resolve_stock_codes(self, message: str, *, force_name_lookup: bool = False) -> list[str]:
        codes = []
        for raw in CODE_PATTERN.findall(message):
            try:
                code = normalize_stock_code(raw)
            except ValueError:
                continue
            if code not in codes:
                codes.append(code)
        if len(codes) >= MAX_CONTEXT_STOCKS:
            return codes[:MAX_CONTEXT_STOCKS]

        if not force_name_lookup and not self._has(
            message, "股票", "股价", "走势", "行情", "持仓", "股票池", "分析", "能买吗", "研判", "买入", "卖出",
        ):
            return codes
        async with async_session() as session:
            latest_date = (await session.execute(select(func.max(StockDailyBar.trade_date)))).scalar_one_or_none()
            names = list((await session.execute(
                select(StockDailyBar.stock_code, StockDailyBar.stock_name)
                .where(StockDailyBar.trade_date == latest_date, StockDailyBar.stock_name.is_not(None))
            )).all()) if latest_date else []
            pool_names = list((await session.execute(
                select(PersonalPoolItem.code, PersonalPoolItem.name)
            )).all())
        for code, name in [*pool_names, *names]:
            if name and str(name) in message and str(code) not in codes:
                codes.append(str(code))
            if len(codes) >= MAX_CONTEXT_STOCKS:
                break
        return codes

    async def resolve_stock_codes(self, message: str) -> list[str]:
        """Resolve explicit codes and cached stock names for strategy tools."""
        return await self._resolve_stock_codes(message, force_name_lookup=True)

    async def _stock_context(self, codes: list[str], daily_limit: int = 30) -> dict[str, Any]:
        if not codes:
            return {}
        bounded_limit = min(max(int(daily_limit), 5), 260)

        async def load_history() -> dict[str, list[dict[str, Any]]]:
            history: dict[str, list[dict[str, Any]]] = {}
            async with async_session() as session:
                for code in codes:
                    rows = list((await session.execute(
                        select(StockDailyBar)
                        .where(StockDailyBar.stock_code == code)
                        .order_by(desc(StockDailyBar.trade_date))
                        .limit(bounded_limit)
                    )).scalars().all())
                    history[code] = [{
                        "date": row.trade_date.isoformat(),
                        "name": row.stock_name or "",
                        "open": row.open_price,
                        "close": row.close_price,
                        "high": row.high_price,
                        "low": row.low_price,
                        "change_pct": row.change_pct,
                        "turnover": row.turnover,
                        "volume": row.volume,
                        "amount": row.amount,
                        "source": row.source,
                    } for row in reversed(rows)]
            return history

        quote_timeout = min(max(float(settings.market_aggregate_timeout), 2.0), 8.0)
        quote_result, history_result = await asyncio.gather(
            asyncio.wait_for(
                quote_snapshot_service.fetch(codes, async_session),
                timeout=quote_timeout,
            ),
            load_history(),
            return_exceptions=True,
        )
        history = {} if isinstance(history_result, Exception) else history_result
        if isinstance(quote_result, Exception):
            fallback_quotes = []
            for code in codes:
                rows = history.get(code) or []
                latest = rows[-1] if rows else {}
                price = latest.get("close")
                if price is None or price <= 0:
                    continue
                fallback_quotes.append({
                    "code": code,
                    "name": latest.get("name") or "",
                    "price": price,
                    "change_pct": latest.get("change_pct"),
                    "high": latest.get("high"),
                    "low": latest.get("low"),
                    "volume": latest.get("volume"),
                    "amount": latest.get("amount"),
                    "turnover": latest.get("turnover"),
                    "cache_trade_date": latest.get("date"),
                    "quote_source": latest.get("source") or "database_cache",
                })
            fallback_dates = [item.get("cache_trade_date") for item in fallback_quotes if item.get("cache_trade_date")]
            quote = {
                "stocks": fallback_quotes,
                "source": "database_cache" if fallback_quotes else "unavailable",
                "data_date": max(fallback_dates, default=None),
                "is_realtime": False,
                "cache_used": bool(fallback_quotes),
                "complete": len(fallback_quotes) == len(codes),
                "error": type(quote_result).__name__,
            }
        else:
            quote = quote_result
        return {
            "quotes": quote.get("stocks") or [],
            "quote_metadata": {
                "source": quote.get("source"),
                "data_date": quote.get("data_date"),
                "is_realtime": bool(quote.get("is_realtime")),
                "cache_used": bool(quote.get("cache_used")),
                "complete": bool(quote.get("complete")),
                "error": quote.get("error"),
            },
            "daily_bars": history,
            "history_coverage": {
                code: {
                    "count": len(rows),
                    "start": rows[0]["date"] if rows else None,
                    "end": rows[-1]["date"] if rows else None,
                }
                for code, rows in history.items()
            },
        }

    async def _cache_payload(self, key: str) -> dict[str, Any]:
        async with async_session() as session:
            row = await session.get(MarketDataCache, key)
        return dict(row.payload) if row and isinstance(row.payload, dict) else {}

    async def _cache_snapshot(self, key: str) -> dict[str, Any]:
        """Read a persisted snapshot without carrying a stale realtime flag."""
        async with async_session() as session:
            row = await session.get(MarketDataCache, key)
        if row is None or not isinstance(row.payload, dict):
            return {}
        payload = dict(row.payload)
        payload["cache_used"] = True
        payload["is_realtime"] = False
        payload["_cache_metadata"] = {
            "key": key,
            "source": "database_cache",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        return payload

    async def _save_cache_snapshot(self, key: str, payload: dict[str, Any]) -> None:
        async with async_session() as session:
            row = await session.get(MarketDataCache, key)
            if row is None:
                session.add(MarketDataCache(key=key, payload=payload))
            else:
                row.payload = payload
                row.updated_at = datetime.utcnow()
            await session.commit()

    async def _index_history_context(self) -> dict[str, Any]:
        """Return a verified Shanghai-index history with an off-session cache."""
        cache_key = "ai_strategy_index_history_v1"
        cached = await self._cache_snapshot(cache_key)
        now = shanghai_now()
        if cached.get("history") and not is_a_share_market_session(now):
            return cached
        timeout = min(max(float(settings.market_aggregate_timeout), 2.0), 8.0)
        try:
            rows = await asyncio.wait_for(
                collector.fetch_shanghai_index_history(days=120),
                timeout=timeout,
            )
        except Exception as exc:
            if cached.get("history"):
                cached["refresh_error"] = type(exc).__name__
                return cached
            return {
                "history": [], "available": False, "source": "unavailable",
                "error": type(exc).__name__, "is_realtime": False,
            }
        payload = {
            "history": rows,
            "available": bool(rows),
            "source": "tencent",
            "data_date": str(rows[-1].get("date") or "")[:10] if rows else None,
            "fetched_at": now.isoformat(),
            "is_realtime": False,
            "cache_used": False,
        }
        if rows:
            try:
                await self._save_cache_snapshot(cache_key, payload)
            except Exception as exc:
                payload["cache_write_error"] = type(exc).__name__
        return payload

    @staticmethod
    def _stock_flow_view(row: StockFundFlowDaily) -> dict[str, Any]:
        return {
            "date": row.trade_date.isoformat(),
            "main_net_inflow": row.main_net_inflow,
            "super_large_net_inflow": row.super_large_net_inflow,
            "large_net_inflow": row.large_net_inflow,
            "medium_net_inflow": row.medium_net_inflow,
            "small_net_inflow": row.small_net_inflow,
            "close_price": row.close_price,
            "change_pct": row.change_pct,
        }

    async def _load_stock_flow_cache(self, codes: list[str], limit: int = 30) -> dict[str, list[dict[str, Any]]]:
        output = {code: [] for code in codes}
        if not codes:
            return output
        async with async_session() as session:
            rows = list((await session.execute(
                select(StockFundFlowDaily)
                .where(StockFundFlowDaily.stock_code.in_(codes))
                .order_by(StockFundFlowDaily.stock_code.asc(), desc(StockFundFlowDaily.trade_date))
            )).scalars().all())
        for row in rows:
            series = output.setdefault(row.stock_code, [])
            if len(series) < limit:
                series.append(self._stock_flow_view(row))
        for series in output.values():
            series.reverse()
        return output

    async def _persist_stock_flow(self, code: str, rows: list[dict[str, Any]], stock_name: str = "") -> int:
        records = []
        for item in rows:
            try:
                trade_date = date.fromisoformat(str(item.get("date") or "")[:10])
            except ValueError:
                continue
            records.append({
                "stock_code": code,
                "stock_name": stock_name,
                "trade_date": trade_date,
                "close_price": item.get("close_price"),
                "change_pct": item.get("change_pct"),
                "main_net_inflow": item.get("main_net_inflow"),
                "super_large_net_inflow": item.get("super_large_net_inflow"),
                "large_net_inflow": item.get("large_net_inflow"),
                "medium_net_inflow": item.get("medium_net_inflow"),
                "small_net_inflow": item.get("small_net_inflow"),
                "created_at": datetime.utcnow(),
            })
        if not records:
            return 0
        async with async_session() as session:
            insert = postgresql_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
            statement = insert(StockFundFlowDaily).values(records)
            updates = {
                column.name: getattr(statement.excluded, column.name)
                for column in StockFundFlowDaily.__table__.columns
                if column.name not in {"id", "stock_code", "trade_date", "created_at"}
            }
            if not stock_name:
                updates.pop("stock_name", None)
            await session.execute(statement.on_conflict_do_update(
                index_elements=["stock_code", "trade_date"],
                set_=updates,
            ))
            await session.commit()
        return len(records)

    async def _stock_flow_context(self, codes: list[str], names: dict[str, str] | None = None) -> dict[str, Any]:
        if not codes:
            return {"series": {}, "source": "unavailable", "available": False}
        names = names or {}
        cached = await self._load_stock_flow_cache(codes)
        now = shanghai_now()
        cached_codes = {code for code, rows in cached.items() if rows}
        requested_codes = codes if is_a_share_market_session(now) else [code for code in codes if code not in cached_codes]
        fetched_codes: set[str] = set()
        errors: dict[str, str] = {}
        timeout = min(max(float(settings.market_aggregate_timeout), 2.0), 8.0)

        async def fetch_one(code: str) -> tuple[str, list[dict[str, Any]]]:
            rows = await asyncio.wait_for(collector.fetch_stock_fund_flow(code), timeout=timeout)
            return code, rows

        if requested_codes:
            results = await asyncio.gather(*(fetch_one(code) for code in requested_codes), return_exceptions=True)
            for code, result in zip(requested_codes, results):
                if isinstance(result, Exception):
                    errors[code] = type(result).__name__
                    continue
                _, rows = result
                if not rows:
                    errors[code] = "EmptySource"
                    continue
                try:
                    await self._persist_stock_flow(code, rows, names.get(code, ""))
                    fetched_codes.add(code)
                except Exception as exc:
                    errors[code] = f"CacheWrite:{type(exc).__name__}"
        series = await self._load_stock_flow_cache(codes)
        latest_dates = [rows[-1]["date"] for rows in series.values() if rows]
        data_date = max(latest_dates, default=None)
        is_realtime = bool(
            fetched_codes
            and data_date == now.date().isoformat()
            and is_a_share_market_session(now)
        )
        return {
            "series": series,
            "source": "eastmoney" if fetched_codes else "database_cache" if latest_dates else "unavailable",
            "data_date": data_date,
            "fetched_at": now.isoformat(),
            "is_realtime": is_realtime,
            "cache_used": bool(latest_dates) and not is_realtime,
            "available": bool(latest_dates),
            "errors": errors,
        }

    async def _flow_context(self) -> dict[str, Any]:
        output: dict[str, Any] = {}
        async with async_session() as session:
            for board_type, model in (("industry", IndustryFundFlowDaily), ("concept", ConceptFundFlowDaily)):
                latest = (await session.execute(select(func.max(model.trade_date)))).scalar_one_or_none()
                inflow_rows = list((await session.execute(
                    select(model)
                    .where(model.trade_date == latest)
                    .order_by(desc(model.main_net_inflow))
                    .limit(8)
                )).scalars().all()) if latest else []
                outflow_rows = list((await session.execute(
                    select(model)
                    .where(model.trade_date == latest)
                    .order_by(model.main_net_inflow.asc())
                    .limit(8)
                )).scalars().all()) if latest else []
                all_rows = [*inflow_rows, *outflow_rows]
                codes = list(dict.fromkeys(row.board_code for row in all_rows))
                names = {
                    row.code: row.name
                    for row in (await session.execute(
                        select(MarketBoard).where(MarketBoard.board_type == board_type, MarketBoard.code.in_(codes))
                    )).scalars().all()
                } if codes else {}
                output[board_type] = {
                    "data_date": latest.isoformat() if latest else None,
                    "source": "database_cache",
                    "is_realtime": False,
                    "cache_used": True,
                    "top_net_inflow": [{
                        "code": row.board_code,
                        "name": names.get(row.board_code, row.board_code),
                        "main_net_inflow": row.main_net_inflow,
                        "change_pct": row.change_pct,
                    } for row in inflow_rows],
                    "top_net_outflow": [{
                        "code": row.board_code,
                        "name": names.get(row.board_code, row.board_code),
                        "main_net_inflow": row.main_net_inflow,
                        "change_pct": row.change_pct,
                    } for row in outflow_rows],
                }
        return output

    async def _dragon_context(self) -> dict[str, Any]:
        async with async_session() as session:
            latest = (await session.execute(select(func.max(DragonBoardDaily.trade_date)))).scalar_one_or_none()
            rows = list((await session.execute(
                select(DragonBoardDaily)
                .where(DragonBoardDaily.trade_date == latest)
                .order_by(desc(DragonBoardDaily.net_amount))
                .limit(12)
            )).scalars().all()) if latest else []
        return {
            "data_date": latest.isoformat() if latest else None,
            "source": "database_cache",
            "is_realtime": False,
            "cache_used": True,
            "stocks": [{
                "code": row.stock_code,
                "name": row.stock_name,
                "net_amount": row.net_amount,
                "buy_amount": row.buy_amount,
                "sell_amount": row.sell_amount,
                "institution_count": row.institution_count,
                "reason": row.reason,
            } for row in rows],
        }

    async def _personal_context(self) -> dict[str, Any]:
        async with async_session() as session:
            rows = list((await session.execute(
                select(PersonalPoolItem).order_by(desc(PersonalPoolItem.updated_at)).limit(80)
            )).scalars().all())
        return {"source": "personal_database", "items": [{
            "pool": row.pool_key,
            "code": row.code,
            "name": row.name,
            "status": row.status,
            "cost": row.cost,
            "position_pct": row.position_pct,
            "thesis": row.thesis,
            "risk_note": row.risk_note,
        } for row in rows]}

    @staticmethod
    def _source_entry(
        name: str,
        payload: dict[str, Any],
        *,
        available: bool | None = None,
        source: str | None = None,
        data_date: str | None = None,
        is_realtime: bool | None = None,
        cache_used: bool | None = None,
    ) -> dict[str, Any]:
        resolved_available = bool(payload) if available is None else bool(available)
        return {
            "name": name,
            "available": resolved_available,
            "source": source or str(payload.get("source") or "unavailable"),
            "data_date": data_date or payload.get("data_date"),
            "is_realtime": bool(payload.get("is_realtime")) if is_realtime is None else bool(is_realtime),
            "cache_used": bool(payload.get("cache_used")) if cache_used is None else bool(cache_used),
        }

    async def build_strategy_context(self, message: str) -> dict[str, Any]:
        """Load a broad, time-labelled evidence pack for strategic analysis."""
        codes = await self._resolve_stock_codes(message, force_name_lookup=True)
        context: dict[str, Any] = {
            "generated_at": shanghai_now().isoformat(),
            "stock_codes": codes,
            "evidence_policy": (
                "Only supplied records may be treated as current facts. Cached snapshots are never labelled realtime, "
                "and missing evidence must block deterministic trade language."
            ),
        }
        async def announcements() -> dict[str, Any]:
            if not codes:
                return {"announcements": {}, "status": {}, "requested": 0, "covered": 0}
            timeout = min(max(float(settings.macro_news_timeout), 2.0), 8.0)
            return await asyncio.wait_for(
                macro_policy_news_collector.get_stock_announcements_audit(codes, max_stocks=len(codes)),
                timeout=timeout,
            )

        async def macro_context() -> dict[str, Any]:
            cached = await self._cache_snapshot("macro_dashboard_v1")
            if cached:
                return cached
            return await asyncio.wait_for(macro_dashboard_service.dashboard(), timeout=8.0)

        stock_task = self._stock_context(codes, daily_limit=120) if codes else asyncio.sleep(0, result={})
        market_task = self._cache_snapshot("market_overview_v1")
        flow_task = self._flow_context()
        dragon_task = self._dragon_context()
        macro_task = macro_context()
        personal_task = self._personal_context()
        index_history_task = self._index_history_context()
        stock_flow_task = self._stock_flow_context(codes)
        announcements_task = announcements()

        (
            stock_result,
            market_result,
            flow_result,
            dragon_result,
            macro_result,
            personal_result,
            index_history_result,
            stock_flow_result,
            announcements_result,
        ) = await asyncio.gather(
            stock_task,
            market_task,
            flow_task,
            dragon_task,
            macro_task,
            personal_task,
            index_history_task,
            stock_flow_task,
            announcements_task,
            return_exceptions=True,
        )
        if not isinstance(stock_result, Exception):
            context["stocks"] = stock_result
        if not isinstance(market_result, Exception):
            context["market_overview"] = market_result
        if not isinstance(flow_result, Exception):
            context["sector_flow"] = flow_result
        if not isinstance(dragon_result, Exception):
            context["dragon_board"] = dragon_result
        if not isinstance(macro_result, Exception):
            context["macro"] = macro_result
        if not isinstance(personal_result, Exception):
            context["personal_pool"] = personal_result
        if not isinstance(index_history_result, Exception):
            context["index_history"] = index_history_result
        if not isinstance(stock_flow_result, Exception):
            context["stock_fund_flow"] = stock_flow_result
        else:
            context["stock_fund_flow"] = {
                "series": {},
                "available": False,
                "error": type(stock_flow_result).__name__,
            }
        if not isinstance(announcements_result, Exception):
            context["announcements"] = announcements_result
        else:
            context["announcements"] = {
                "announcements": {},
                "status": {},
                "requested": len(codes),
                "covered": 0,
                "error": type(announcements_result).__name__,
            }

        stocks = context.get("stocks") or {}
        quote_meta = stocks.get("quote_metadata") or {}
        history_dates = [
            row.get("date")
            for rows in (stocks.get("daily_bars") or {}).values()
            for row in rows[-1:]
            if row.get("date")
        ]
        market = context.get("market_overview") or {}
        sector = context.get("sector_flow") or {}
        dragon = context.get("dragon_board") or {}
        macro = context.get("macro") or {}
        stock_flow = context.get("stock_fund_flow") or {}
        announcement_audit = context.get("announcements") or {}
        index_history = context.get("index_history") or {}
        sector_dates = [
            item.get("data_date")
            for item in sector.values()
            if isinstance(item, dict) and item.get("data_date")
        ]
        context["source_audit"] = [
            self._source_entry(
                "个股行情",
                quote_meta,
                available=bool(stocks.get("quotes")),
                source=str(quote_meta.get("source") or "unavailable"),
                data_date=quote_meta.get("data_date"),
            ),
            self._source_entry(
                "个股日线",
                {},
                available=bool(history_dates),
                source="database_cache",
                data_date=max(history_dates, default=None),
                is_realtime=False,
                cache_used=True,
            ),
            self._source_entry(
                "个股资金流",
                stock_flow,
                available=bool(stock_flow.get("available")),
            ),
            self._source_entry(
                "市场总览",
                market,
                available=bool(market),
                source="database_cache" if market else "unavailable",
                data_date=market.get("data_date") or (market.get("market_index") or {}).get("data_date"),
                is_realtime=False,
                cache_used=bool(market),
            ),
            self._source_entry(
                "指数日线",
                index_history,
                available=bool(index_history.get("history")),
                source=str(index_history.get("source") or "unavailable"),
                data_date=index_history.get("data_date"),
                is_realtime=False,
                cache_used=bool(index_history.get("cache_used")),
            ),
            self._source_entry(
                "板块资金流",
                {},
                available=any(bool((item or {}).get("top_net_inflow")) for item in sector.values() if isinstance(item, dict)),
                source="database_cache" if sector else "unavailable",
                data_date=max(sector_dates, default=None),
                is_realtime=False,
                cache_used=bool(sector),
            ),
            self._source_entry(
                "龙虎榜",
                dragon,
                available=bool(dragon.get("stocks")),
            ),
            self._source_entry(
                "宏观与政策",
                macro,
                available=bool(macro),
                source="database_cache" if macro.get("cache_used") else "verified_sources" if macro else "unavailable",
                data_date=str(macro.get("snapshot_updated_at") or macro.get("updated_at") or "")[:10] or None,
                is_realtime=False,
                cache_used=bool(macro.get("cache_used") or (macro.get("_cache_metadata"))),
            ),
            self._source_entry(
                "公司公告",
                {},
                available=(not codes) or int(announcement_audit.get("covered") or 0) == len(codes),
                source="eastmoney/ftshare_mcp" if announcement_audit.get("covered") else "unavailable",
                data_date=max(
                    (
                        str(item.get("published_at") or "")[:10]
                        for rows in (announcement_audit.get("announcements") or {}).values()
                        for item in rows
                        if item.get("published_at")
                    ),
                    default=None,
                ),
                is_realtime=False,
                cache_used=False,
            ),
        ]
        context["sources"] = [item["name"] for item in context["source_audit"] if item["available"]]
        context["available"] = bool(context["sources"])
        return context

    async def _robot_context(self) -> dict[str, Any]:
        async with async_session() as session:
            rows = list((await session.execute(
                select(AIRobotJournal).order_by(desc(AIRobotJournal.journal_date), desc(AIRobotJournal.id)).limit(6)
            )).scalars().all())
        return {"source": "robot_journal_database", "journals": [{
            "pool_type": row.pool_type,
            "journal_date": row.journal_date.isoformat(),
            "source_data_date": row.source_data_date.isoformat() if row.source_data_date else None,
            "action_summary": row.action_summary,
            "pnl_reflection": row.pnl_reflection,
            "metrics": row.metrics,
        } for row in rows]}

    async def build_context(self, message: str) -> dict[str, Any]:
        codes = await self._resolve_stock_codes(message)
        context: dict[str, Any] = {
            "generated_at": shanghai_now().isoformat(),
            "evidence_policy": "Only supplied records may be treated as current facts; every value carries its source or data date.",
        }
        sources = []
        if codes:
            context["stocks"] = await self._stock_context(codes)
            sources.append("股票行情与近30条日线")
        if self._has(message, "大盘", "市场", "指数", "涨停", "跌停", "成交额", "今天", "今日", "现在", "实时"):
            context["market_overview"] = await self._cache_payload("market_overview_v1")
            sources.append("市场总览缓存")
        if self._has(message, "板块", "资金流", "主力", "行业", "概念", "轮动"):
            context["sector_flow"] = await self._flow_context()
            sources.append("板块资金流缓存")
        if self._has(message, "龙虎榜", "机构席位", "游资"):
            context["dragon_board"] = await self._dragon_context()
            sources.append("龙虎榜缓存")
        if self._has(message, "宏观", "政策", "经济", "美元", "美股", "黄金", "原油", "利率", "汇率"):
            context["macro"] = await self._cache_payload("macro_dashboard_v1")
            sources.append("宏观看板缓存")
        if self._has(message, "个人池", "股票池", "自选", "我的股票", "我的持仓"):
            context["personal_pool"] = await self._personal_context()
            sources.append("个人股票池")
        if self._has(message, "机器人", "模拟交易", "模拟盈亏", "复盘日记"):
            context["robot"] = await self._robot_context()
            sources.append("机器人复盘记录")
        context["sources"] = sources
        context["available"] = bool(sources)
        return context


ai_assistant_service = AIAssistantService()
