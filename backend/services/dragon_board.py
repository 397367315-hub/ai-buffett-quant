"""Persistent Dragon-Tiger List snapshots and source-grounded period analysis."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import desc, func, select

from config import settings
from database import async_session
from models import DragonBoardDaily
from services.ai_service import ai_service
from services.data_collector import collector, shanghai_now


DRAGON_WINDOWS = {
    "week": {"label": "近一周", "sessions": 5},
    "two_weeks": {"label": "近两周", "sessions": 10},
    "month": {"label": "近一个月", "sessions": 20},
}


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


class DragonBoardService:
    @staticmethod
    def _deduplicate(stocks: list[dict], target_date: date | None = None) -> list[dict]:
        grouped: dict[tuple[str, str], dict] = {}
        reasons: dict[tuple[str, str], list[str]] = defaultdict(list)
        for raw in stocks:
            code = str(raw.get("code") or "").strip()
            date_text = str(raw.get("date") or "")[:10]
            try:
                trade_date = date.fromisoformat(date_text)
            except ValueError:
                continue
            if target_date and trade_date != target_date:
                continue
            key = (date_text, code)
            reason = str(raw.get("reason") or "").strip()
            if reason and reason not in reasons[key]:
                reasons[key].append(reason)
            candidate = {
                **raw,
                "date": date_text,
                "net_amount": int(_number(raw.get("net_amount", raw.get("main_net_inflow")))),
                "main_net_inflow": int(_number(raw.get("net_amount", raw.get("main_net_inflow")))),
            }
            existing = grouped.get(key)
            if existing is None or (
                _number(candidate.get("amount")), abs(_number(candidate.get("net_amount")))
            ) > (
                _number(existing.get("amount")), abs(_number(existing.get("net_amount")))
            ):
                grouped[key] = candidate
            elif existing is not None:
                existing["institution_count"] = max(
                    int(_number(existing.get("institution_count"))),
                    int(_number(candidate.get("institution_count"))),
                )
        output = []
        for key, item in grouped.items():
            item["reason"] = "；".join(reasons[key])
            output.append(item)
        return sorted(output, key=lambda item: abs(_number(item.get("net_amount"))), reverse=True)

    @staticmethod
    def _row_to_stock(row: DragonBoardDaily) -> dict:
        return {
            "code": row.stock_code,
            "name": row.stock_name,
            "date": row.trade_date.isoformat(),
            "price": row.close_price,
            "change_pct": row.change_pct,
            "turnover": row.turnover,
            "amount": row.deal_amount,
            "buy_amount": row.buy_amount,
            "sell_amount": row.sell_amount,
            "net_amount": row.net_amount,
            # Kept during the API transition for existing clients.
            "main_net_inflow": row.net_amount,
            "market_cap": row.market_cap,
            "institution_count": row.institution_count,
            "institution_buy_amount": row.institution_buy_amount,
            "institution_sell_amount": row.institution_sell_amount,
            "institution_net_amount": row.institution_net_amount,
            "reason": row.reason or "",
        }

    async def _persist(self, stocks: list[dict]) -> dict:
        normalized = self._deduplicate(stocks)
        if not normalized:
            return {"written": 0, "dates": []}
        dates = sorted({date.fromisoformat(item["date"]) for item in normalized})
        codes = {item["code"] for item in normalized}
        async with async_session() as session:
            existing_rows = list((await session.execute(
                select(DragonBoardDaily).where(
                    DragonBoardDaily.trade_date.in_(dates),
                    DragonBoardDaily.stock_code.in_(codes),
                )
            )).scalars().all())
            existing = {(row.trade_date, row.stock_code): row for row in existing_rows}
            for item in normalized:
                trade_date = date.fromisoformat(item["date"])
                row = existing.get((trade_date, item["code"]))
                if row is None:
                    row = DragonBoardDaily(trade_date=trade_date, stock_code=item["code"], stock_name=item.get("name") or item["code"])
                    session.add(row)
                row.stock_name = item.get("name") or row.stock_name
                row.close_price = _number(item.get("price"))
                row.change_pct = _number(item.get("change_pct"))
                row.turnover = _number(item.get("turnover"))
                row.deal_amount = int(_number(item.get("amount")))
                row.buy_amount = int(_number(item.get("buy_amount")))
                row.sell_amount = int(_number(item.get("sell_amount")))
                row.net_amount = int(_number(item.get("net_amount", item.get("main_net_inflow"))))
                row.market_cap = int(_number(item.get("market_cap")))
                row.institution_count = int(_number(item.get("institution_count")))
                row.institution_buy_amount = int(_number(item.get("institution_buy_amount")))
                row.institution_sell_amount = int(_number(item.get("institution_sell_amount")))
                row.institution_net_amount = int(_number(item.get("institution_net_amount")))
                row.reason = str(item.get("reason") or "")
                row.source = "eastmoney"
            await session.commit()
        return {"written": len(normalized), "dates": [item.isoformat() for item in dates]}

    async def refresh(self, target_date: date | None = None) -> dict:
        try:
            rows = await asyncio.wait_for(
                collector.fetch_dragon_board(page_size=500, target_date=target_date),
                timeout=min(max(settings.market_aggregate_timeout, 3.0), 15.0),
            )
        except Exception as exc:
            return {"status": "failed", "written": 0, "error": type(exc).__name__}
        rows = self._deduplicate(rows, target_date)
        if not rows:
            return {"status": "empty", "written": 0, "error": None}
        persisted = await self._persist(rows)
        return {"status": "success", **persisted, "error": None}

    async def _latest_date(self) -> date | None:
        async with async_session() as session:
            return (await session.execute(select(func.max(DragonBoardDaily.trade_date)))).scalar_one_or_none()

    async def _read_date(self, target_date: date) -> list[dict]:
        async with async_session() as session:
            rows = list((await session.execute(
                select(DragonBoardDaily)
                .where(DragonBoardDaily.trade_date == target_date)
                .order_by(desc(DragonBoardDaily.net_amount))
            )).scalars().all())
        return [self._row_to_stock(row) for row in rows]

    @staticmethod
    def _payload(stocks: list[dict], requested_date: date | None = None) -> dict:
        # A requested date is not evidence by itself; expose a data date only
        # when at least one verified row was loaded for that session.
        data_date = max((item["date"] for item in stocks), default=None)
        net_total = sum(int(_number(item.get("net_amount"))) for item in stocks)
        return {
            "stocks": stocks,
            "summary": {
                "total": len(stocks),
                "institution_active": sum(int(_number(item.get("institution_count"))) for item in stocks),
                "institution_stock_count": sum(int(_number(item.get("institution_count"))) > 0 for item in stocks),
                "total_buy_amount": sum(int(_number(item.get("buy_amount"))) for item in stocks),
                "total_sell_amount": sum(int(_number(item.get("sell_amount"))) for item in stocks),
                "total_net_amount": net_total,
                "total_main_inflow": net_total,
            },
            "available": bool(stocks),
            "source": "database_cache" if stocks else "unavailable",
            "is_realtime": False,
            "data_date": data_date,
            "updated_at": shanghai_now().isoformat(),
        }

    async def get_board(self, target_date: date | None = None, force_refresh: bool = False) -> dict:
        if target_date:
            stocks = await self._read_date(target_date)
            if not stocks:
                await self.refresh(target_date)
                stocks = await self._read_date(target_date)
            return self._payload(stocks, target_date)

        latest = await self._latest_date()
        if force_refresh or latest is None:
            await self.refresh()
            latest = await self._latest_date()
        stocks = await self._read_date(latest) if latest else []
        return self._payload(stocks, latest)

    async def list_dates(self, limit: int = 250) -> list[dict]:
        async with async_session() as session:
            rows = (await session.execute(
                select(DragonBoardDaily.trade_date, func.count(DragonBoardDaily.id))
                .group_by(DragonBoardDaily.trade_date)
                .order_by(desc(DragonBoardDaily.trade_date))
                .limit(limit)
            )).all()
        return [{"date": trade_date.isoformat(), "stock_count": count} for trade_date, count in rows]

    async def _analysis_rows(self, sessions: int) -> tuple[list[DragonBoardDaily], list[date]]:
        async with async_session() as session:
            dates = list((await session.execute(
                select(DragonBoardDaily.trade_date)
                .distinct()
                .order_by(desc(DragonBoardDaily.trade_date))
                .limit(sessions)
            )).scalars().all())
            rows = list((await session.execute(
                select(DragonBoardDaily).where(DragonBoardDaily.trade_date.in_(dates))
            )).scalars().all()) if dates else []
        return rows, sorted(dates)

    @staticmethod
    def _deterministic_analysis(rows: list[DragonBoardDaily], dates: list[date], window: dict) -> dict:
        grouped: dict[str, list[DragonBoardDaily]] = defaultdict(list)
        for row in rows:
            grouped[row.stock_code].append(row)
        stocks = []
        for code, items in grouped.items():
            items.sort(key=lambda item: item.trade_date)
            stocks.append({
                "code": code,
                "name": items[-1].stock_name,
                "appearances": len(items),
                "net_amount": sum(int(item.net_amount or 0) for item in items),
                "buy_amount": sum(int(item.buy_amount or 0) for item in items),
                "sell_amount": sum(int(item.sell_amount or 0) for item in items),
                "positive_days": sum(int(item.net_amount or 0) > 0 for item in items),
                "institution_count": sum(int(item.institution_count or 0) for item in items),
                "latest_change_pct": round(float(items[-1].change_pct or 0), 2),
                "latest_date": items[-1].trade_date.isoformat(),
            })
        daily = []
        for target in dates:
            items = [row for row in rows if row.trade_date == target]
            daily.append({
                "date": target.isoformat(),
                "stock_count": len(items),
                "net_amount": sum(int(row.net_amount or 0) for row in items),
                "net_buy_count": sum(int(row.net_amount or 0) > 0 for row in items),
                "net_sell_count": sum(int(row.net_amount or 0) < 0 for row in items),
                "institution_count": sum(int(row.institution_count or 0) for row in items),
            })
        aggregate_net = sum(item["net_amount"] for item in stocks)
        gross = sum(abs(item["net_amount"]) for item in stocks)
        net_buy_rows = sum(int(row.net_amount or 0) > 0 for row in rows)
        positive_ratio = round(net_buy_rows / max(len(rows), 1) * 100, 1)
        score = round(max(0.0, min(100.0, 50 + (aggregate_net / gross * 25 if gross else 0) + (positive_ratio - 50) * 0.3)), 1)
        tone = "净买偏强" if score >= 62 else "净卖偏弱" if score <= 38 else "多空分化"
        top_net_buys = sorted(stocks, key=lambda item: item["net_amount"], reverse=True)[:5]
        top_net_sells = sorted(stocks, key=lambda item: item["net_amount"])[:5]
        recurring = sorted((item for item in stocks if item["appearances"] >= 2), key=lambda item: (item["appearances"], abs(item["net_amount"])), reverse=True)[:5]
        institutional = sorted((item for item in stocks if item["institution_count"] > 0), key=lambda item: (item["institution_count"], item["net_amount"]), reverse=True)[:5]
        leaders = "、".join(item["name"] for item in top_net_buys[:3] if item["net_amount"] > 0) or "暂无明显净买主线"
        suggestions = []
        if recurring:
            suggestions.append(f"优先复核重复上榜的{'、'.join(item['name'] for item in recurring[:3])}，观察上榜后量价是否延续。")
        if institutional:
            suggestions.append(f"机构席位较活跃的{'、'.join(item['name'] for item in institutional[:3])}可结合基本面继续核验。")
        suggestions.append("龙虎榜属于异常交易披露，需结合公告、位置、换手率和后续资金确认，不宜单独作为买入信号。")
        risks = []
        if len(dates) < window["sessions"]:
            risks.append(f"缓存仅覆盖 {len(dates)} 个交易日，少于目标 {window['sessions']} 日。")
        if top_net_sells and top_net_sells[0]["net_amount"] < 0:
            risks.append(f"{top_net_sells[0]['name']}周期净卖额居前，短线承接风险较高。")
        return {
            "score": score,
            "tone": tone,
            "headline": f"{window['label']}龙虎榜资金{tone}，净买居前为{leaders}。",
            "summary": f"覆盖 {len(dates)}/{window['sessions']} 个缓存交易日、{len(stocks)} 只股票，净买记录占比 {positive_ratio:.1f}%。",
            "aggregate_net_amount": aggregate_net,
            "positive_ratio_pct": positive_ratio,
            "top_net_buys": top_net_buys,
            "top_net_sells": top_net_sells,
            "recurring": recurring,
            "institutional": institutional,
            "suggestions": suggestions[:4],
            "risks": risks[:4],
            "daily": daily,
        }

    async def _ai_narrative(self, payload: dict) -> str | None:
        if not ai_service.client:
            return None
        compact = {
            "window": payload["window"],
            "coverage": payload["coverage"],
            "headline": payload["analysis"]["headline"],
            "top_net_buys": payload["analysis"]["top_net_buys"],
            "top_net_sells": payload["analysis"]["top_net_sells"],
            "recurring": payload["analysis"]["recurring"],
            "institutional": payload["analysis"]["institutional"],
        }
        prompt = (
            "请只依据下面龙虎榜缓存JSON，用中文输出三段：资金特征、重复/机构席位观察、风险。"
            "不得把上榜解释为必涨，不补充JSON之外的事实，每段不超过80字。\n"
            + json.dumps(compact, ensure_ascii=False)
        )
        try:
            result = await asyncio.wait_for(
                ai_service.generate(prompt, "你是A股龙虎榜审计分析师，所有结论必须能追溯到输入数据。"),
                timeout=15,
            )
        except Exception:
            return None
        return result if result and not result.startswith("[AI服务") else None

    async def analyze(self, window_key: str) -> dict:
        if window_key not in DRAGON_WINDOWS:
            raise ValueError("window 仅支持 week、two_weeks 或 month")
        window = DRAGON_WINDOWS[window_key]
        rows, dates = await self._analysis_rows(window["sessions"])
        analysis = self._deterministic_analysis(rows, dates, window)
        payload = {
            "available": bool(rows),
            "window": {"id": window_key, **window},
            "period": {"start": dates[0].isoformat() if dates else None, "end": dates[-1].isoformat() if dates else None},
            "coverage": {"actual_sessions": len(dates), "requested_sessions": window["sessions"], "stock_count": len({row.stock_code for row in rows}), "complete": len(dates) >= window["sessions"]},
            "analysis": analysis,
            "source": "database_cache",
            "is_realtime": False,
            "updated_at": shanghai_now().isoformat(),
            "ai_narrative": None,
            "ai_generated": False,
            "method": "按缓存交易日聚合龙虎榜净买卖额、重复上榜次数和机构席位次数。",
        }
        if rows:
            narrative = await self._ai_narrative(payload)
            payload["ai_narrative"] = narrative
            payload["ai_generated"] = bool(narrative)
        return payload


dragon_board_service = DragonBoardService()
