"""Persistent assistant memory and selective, source-labelled market context."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from sqlalchemy import delete, desc, func, select

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
)
from services.data_collector import normalize_stock_code, shanghai_now
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

    async def _resolve_stock_codes(self, message: str) -> list[str]:
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

        if not self._has(message, "股票", "股价", "走势", "行情", "持仓", "股票池", "分析", "能买吗"):
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

    async def _stock_context(self, codes: list[str]) -> dict[str, Any]:
        if not codes:
            return {}
        quote = await quote_snapshot_service.fetch(codes, async_session)
        history: dict[str, list[dict[str, Any]]] = {}
        async with async_session() as session:
            for code in codes:
                rows = list((await session.execute(
                    select(StockDailyBar)
                    .where(StockDailyBar.stock_code == code)
                    .order_by(desc(StockDailyBar.trade_date))
                    .limit(30)
                )).scalars().all())
                history[code] = [{
                    "date": row.trade_date.isoformat(),
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
        return {
            "quotes": quote.get("stocks") or [],
            "quote_metadata": {
                "source": quote.get("source"),
                "data_date": quote.get("data_date"),
                "is_realtime": bool(quote.get("is_realtime")),
                "cache_used": bool(quote.get("cache_used")),
                "complete": bool(quote.get("complete")),
            },
            "daily_bars": history,
        }

    async def _cache_payload(self, key: str) -> dict[str, Any]:
        async with async_session() as session:
            row = await session.get(MarketDataCache, key)
        return dict(row.payload) if row and isinstance(row.payload, dict) else {}

    async def _flow_context(self) -> dict[str, Any]:
        output: dict[str, Any] = {}
        async with async_session() as session:
            for board_type, model in (("industry", IndustryFundFlowDaily), ("concept", ConceptFundFlowDaily)):
                latest = (await session.execute(select(func.max(model.trade_date)))).scalar_one_or_none()
                rows = list((await session.execute(
                    select(model)
                    .where(model.trade_date == latest)
                    .order_by(desc(model.main_net_inflow))
                    .limit(8)
                )).scalars().all()) if latest else []
                codes = [row.board_code for row in rows]
                names = {
                    row.code: row.name
                    for row in (await session.execute(
                        select(MarketBoard).where(MarketBoard.board_type == board_type, MarketBoard.code.in_(codes))
                    )).scalars().all()
                } if codes else {}
                output[board_type] = {
                    "data_date": latest.isoformat() if latest else None,
                    "source": "database_cache",
                    "top_net_inflow": [{
                        "code": row.board_code,
                        "name": names.get(row.board_code, row.board_code),
                        "main_net_inflow": row.main_net_inflow,
                        "change_pct": row.change_pct,
                    } for row in rows],
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
