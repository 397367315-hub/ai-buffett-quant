"""Verified global-market, calendar, and domestic-liquidity dashboard inputs."""

from __future__ import annotations

import asyncio
import csv
import math
from datetime import datetime, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

import httpx

from config import settings
from database import async_session
from models import MarketDataCache
from services.data_collector import collector, shanghai_now
from services.macro_policy_news import macro_policy_news_collector


SINA_QUOTES_URL = "https://hq.sinajs.cn/list=gb_inx,gb_dji,gb_ixic,hf_GC,hf_CL,DINIW"
ECONOMIC_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _float(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _change_pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current / previous - 1) * 100, 2)


def _parse_sina_lines(text: str) -> dict[str, list[str]]:
    parsed: dict[str, list[str]] = {}
    for raw_line in text.splitlines():
        prefix, separator, raw_payload = raw_line.partition("=")
        if not separator or "hq_str_" not in prefix:
            continue
        symbol = prefix.rsplit("hq_str_", 1)[-1].strip()
        payload = raw_payload.strip().rstrip(";").strip()
        if len(payload) < 2 or payload[0] != '"' or payload[-1] != '"':
            continue
        values = next(csv.reader(StringIO(payload[1:-1])), [])
        if values:
            parsed[symbol] = values
    return parsed


def parse_sina_market_payload(text: str) -> list[dict]:
    rows = _parse_sina_lines(text)
    result = []
    us_config = {
        "gb_inx": ("sp500", "标普500"),
        "gb_dji": ("dow", "道琼斯"),
        "gb_ixic": ("nasdaq", "纳斯达克"),
    }
    for symbol, (key, label) in us_config.items():
        values = rows.get(symbol) or []
        current = _float(values[1]) if len(values) > 1 else None
        pct = _float(values[2]) if len(values) > 2 else None
        result.append({
            "key": key,
            "label": label,
            "value": current,
            "change_pct": pct,
            "currency": "USD",
            "source_time": values[3] if len(values) > 3 else None,
            "available": current is not None,
            "source": "新浪财经",
        })

    futures_config = {
        "hf_GC": ("gold", "纽约黄金", "USD/盎司"),
        "hf_CL": ("oil", "纽约原油", "USD/桶"),
    }
    for symbol, (key, label, unit) in futures_config.items():
        values = rows.get(symbol) or []
        current = _float(values[0]) if values else None
        previous = _float(values[7]) if len(values) > 7 else None
        source_time = f"{values[12]} {values[6]}" if len(values) > 12 else None
        result.append({
            "key": key,
            "label": label,
            "value": current,
            "change_pct": _change_pct(current, previous),
            "currency": unit,
            "source_time": source_time,
            "available": current is not None,
            "source": "新浪财经",
        })

    dxy = rows.get("DINIW") or []
    current = _float(dxy[1]) if len(dxy) > 1 else None
    previous = _float(dxy[5]) if len(dxy) > 5 else None
    result.append({
        "key": "dxy",
        "label": "美元指数",
        "value": current,
        "change_pct": _change_pct(current, previous),
        "currency": "index",
        "source_time": f"{dxy[10]} {dxy[0]}" if len(dxy) > 10 else None,
        "available": current is not None,
        "source": "新浪财经",
    })
    return result


class MacroDashboardService:
    _CACHE_KEY = "macro_dashboard_v1"

    @classmethod
    async def _load_cache(cls) -> dict:
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, cls._CACHE_KEY)
            return dict(row.payload) if row and isinstance(row.payload, dict) else {}
        except Exception:
            return {}

    @classmethod
    async def _save_cache(cls, payload: dict) -> None:
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, cls._CACHE_KEY)
                if row is None:
                    session.add(MarketDataCache(key=cls._CACHE_KEY, payload=payload))
                else:
                    row.payload = payload
                await session.commit()
        except Exception:
            pass

    @staticmethod
    def _timeout() -> float:
        try:
            return min(max(float(settings.macro_news_timeout), 2.0), 12.0)
        except (TypeError, ValueError):
            return 8.0

    async def _global_markets(self) -> list[dict]:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn/",
            "Accept": "*/*",
        }
        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            response = await client.get(SINA_QUOTES_URL, headers=headers)
            response.raise_for_status()
        return parse_sina_market_payload(response.content.decode("gb18030", errors="replace"))

    async def _economic_calendar(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=self._timeout()) as client:
            response = await client.get(ECONOMIC_CALENDAR_URL, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            return []
        now = shanghai_now()
        end = now + timedelta(days=14)
        country_labels = {"USD": "美国", "CNY": "中国", "All": "全球"}
        result = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            country = str(item.get("country") or "")
            impact = str(item.get("impact") or "")
            if country not in country_labels or impact not in {"High", "Medium"}:
                continue
            try:
                event_at = datetime.fromisoformat(str(item.get("date") or "")).astimezone(SHANGHAI_TZ)
            except (TypeError, ValueError):
                continue
            if event_at < now or event_at > end:
                continue
            result.append({
                "title": str(item.get("title") or ""),
                "country": country_labels[country],
                "country_code": country,
                "impact": "高" if impact == "High" else "中",
                "event_at": event_at.isoformat(),
                "forecast": str(item.get("forecast") or ""),
                "previous": str(item.get("previous") or ""),
                "source": "Forex Factory calendar feed",
            })
        return sorted(result, key=lambda item: item["event_at"])

    async def dashboard(self) -> dict:
        cached = await self._load_cache()
        global_result, calendar_result, north_result, turnover_result, policy_result = await asyncio.gather(
            self._global_markets(),
            self._economic_calendar(),
            collector.fetch_north_bound_daily(days=10),
            collector.fetch_market_turnover(),
            macro_policy_news_collector.get_context(),
            return_exceptions=True,
        )
        global_markets = [] if isinstance(global_result, Exception) else global_result
        calendar = [] if isinstance(calendar_result, Exception) else calendar_result
        north = [] if isinstance(north_result, Exception) else north_result
        turnover = {} if isinstance(turnover_result, Exception) else turnover_result
        policy = macro_policy_news_collector.empty_context() if isinstance(policy_result, Exception) else policy_result
        if not isinstance(policy, dict):
            policy = macro_policy_news_collector.empty_context()
        cache_used = False
        global_from_cache = False
        calendar_from_cache = False
        policy_from_cache = False
        if not global_markets and cached.get("global_markets"):
            global_markets = list(cached["global_markets"])
            cache_used = True
            global_from_cache = True
        if not calendar and cached.get("economic_calendar"):
            calendar = list(cached["economic_calendar"])
            cache_used = True
            calendar_from_cache = True
        if not policy.get("available") and cached.get("policy", {}).get("available"):
            policy = {**cached["policy"], "source_status": {"宏观政策快照": "cache"}}
            cache_used = True
            policy_from_cache = True

        north_available = [item for item in north if item.get("net_inflow") is not None]
        latest_north = north_available[-1] if north_available else (north[-1] if north else None)
        consecutive_inflow_days = 0
        for item in reversed(north_available):
            if (item.get("net_inflow") or 0) > 0:
                consecutive_inflow_days += 1
            else:
                break
        domestic = {
            "northbound": {
                "available": latest_north is not None and latest_north.get("net_inflow") is not None,
                "date": latest_north.get("date") if latest_north else None,
                "net_inflow": latest_north.get("net_inflow") if latest_north else None,
                "consecutive_inflow_days": consecutive_inflow_days,
                "source": latest_north.get("source", "eastmoney") if latest_north else "eastmoney",
            },
            "turnover": {
                "available": bool(turnover),
                "date": turnover.get("data_date"),
                "sh_amount": turnover.get("sh_amount"),
                "sh_index": turnover.get("sh_index"),
                "sh_change_pct": turnover.get("sh_change_pct"),
                "source": "东方财富",
            },
            "margin_balance": {
                "available": False,
                "value": None,
                "message": "当前数据源未提供可核验的全市场融资余额，未用示例值替代。",
            },
        }
        cached_domestic = cached.get("domestic_liquidity") or {}
        north_from_cache = False
        turnover_from_cache = False
        if not domestic["northbound"]["available"] and cached_domestic.get("northbound", {}).get("date"):
            domestic["northbound"] = dict(cached_domestic["northbound"])
            cache_used = True
            north_from_cache = True
        if not domestic["turnover"]["available"] and cached_domestic.get("turnover", {}).get("date"):
            domestic["turnover"] = dict(cached_domestic["turnover"])
            cache_used = True
            turnover_from_cache = True

        market_by_key = {item["key"]: item for item in global_markets}
        sp500 = market_by_key.get("sp500") or {}
        gold = market_by_key.get("gold") or {}
        oil = market_by_key.get("oil") or {}
        dxy = market_by_key.get("dxy") or {}
        north_view = domestic["northbound"]
        premarket_questions = [
            {
                "id": "overseas",
                "question": "隔夜海外市场风险偏好如何？",
                "answer": f"标普500 {sp500.get('change_pct'):+.2f}%" if sp500.get("change_pct") is not None else "海外指数源暂不可用",
                "status": "positive" if (sp500.get("change_pct") or 0) > 0.3 else "negative" if (sp500.get("change_pct") or 0) < -0.3 else "neutral",
            },
            {
                "id": "commodities",
                "question": "黄金和原油是否释放通胀或避险信号？",
                "answer": (
                    f"黄金 {gold.get('change_pct'):+.2f}% · 原油 {oil.get('change_pct'):+.2f}%"
                    if gold.get("change_pct") is not None and oil.get("change_pct") is not None else "商品行情源暂不完整"
                ),
                "status": "neutral",
            },
            {
                "id": "dollar",
                "question": "美元强弱是否影响风险资产？",
                "answer": f"美元指数 {dxy.get('value'):.2f} ({dxy.get('change_pct'):+.2f}%)" if dxy.get("value") is not None and dxy.get("change_pct") is not None else "美元指数暂不可用",
                "status": "negative" if (dxy.get("change_pct") or 0) > 0.4 else "neutral",
            },
            {
                "id": "liquidity",
                "question": "国内资金面是否支持风险偏好？",
                "answer": (
                    f"北向净流入 {north_view['net_inflow'] / 1e8:+.2f}亿元，连续流入 {north_view.get('consecutive_inflow_days', 0)} 日"
                    if north_view.get("net_inflow") is not None else "北向净流入字段当前不可核验"
                ),
                "status": "positive" if (north_view.get("net_inflow") or 0) > 0 else "neutral",
            },
            {
                "id": "calendar",
                "question": "未来两周有哪些高影响事件？",
                "answer": f"{len([item for item in calendar if item['impact'] == '高'])} 项高影响事件" if calendar else "经济日历暂未返回未来事件",
                "status": "warning" if any(item["impact"] == "高" for item in calendar[:3]) else "neutral",
            },
        ]
        source_status = {
            "新浪财经": "cache" if global_from_cache else "available" if any(item.get("available") for item in global_markets) else "unavailable",
            "经济日历": "cache" if calendar_from_cache else "available" if calendar else "unavailable",
            "东方财富资金": "cache" if north_from_cache or turnover_from_cache else "available" if north or turnover else "unavailable",
            **(policy.get("source_status") or {}),
        }
        if policy_from_cache:
            source_status["宏观政策快照"] = "cache"
        updated_at = shanghai_now().isoformat()
        output = {
            "updated_at": updated_at,
            "global_markets": global_markets,
            "economic_calendar": calendar,
            "domestic_liquidity": domestic,
            "policy": {
                "available": bool(policy.get("available")),
                "summary": policy.get("summary"),
                "international_items": policy.get("international_items") or [],
                "policy_items": policy.get("policy_items") or [],
            },
            "premarket_questions": premarket_questions,
            "source_status": source_status,
            "cache_used": cache_used,
            "snapshot_updated_at": (
                cached.get("snapshot_updated_at") or cached.get("updated_at")
                if cache_used else updated_at
            ),
            "disclaimer": "不同市场交易时段不同；页面按各源时间戳展示，不把隔夜收盘标记为A股盘中实时。",
        }
        await self._save_cache(output)
        return output


macro_dashboard_service = MacroDashboardService()
