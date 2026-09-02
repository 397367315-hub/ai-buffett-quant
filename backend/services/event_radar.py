"""Free-source AI event radar with deterministic scoring and auditable fallbacks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, AsyncIterator
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, desc, func, select

from database import async_session
from models import (
    RadarAlert,
    RadarEvent,
    RadarEventStock,
    RadarEventTopic,
    RadarProviderHealth,
    RadarRawSource,
    StockDailyBar,
    StockUniverseSnapshot,
)
from services.data_collector import collector, is_a_share_market_session, shanghai_now
from services.macro_dashboard import macro_dashboard_service
from services.macro_policy_news import macro_policy_news_collector


SOURCE_PRIORITY = {
    "exchange_official": 100,
    "government_official": 98,
    "company_announcement": 96,
    "cninfo": 96,
    "cls": 85,
    "mainstream_finance": 75,
    "industry_media": 65,
    "market_snapshot": 72,
    "social_media": 35,
    "unknown": 20,
}

CLS_ROLL_URL = "https://www.cls.cn/v1/roll/get_roll_list"

TOPIC_DICTIONARY: dict[str, tuple[str, ...]] = {
    "AI算力": ("AI算力", "算力", "服务器", "液冷", "CPO", "光模块", "PCB", "存储", "GPU", "数据中心"),
    "半导体": ("半导体", "芯片", "先进封装", "国产算力", "晶圆", "光刻", "存储"),
    "机器人": ("机器人", "人形机器人", "减速器", "丝杠", "伺服"),
    "低空经济": ("低空", "无人机", "航空器"),
    "商业航天": ("商业航天", "卫星互联网", "卫星", "火箭"),
    "新能源": ("锂矿", "锂电池", "固态电池", "钠电池", "储能", "光伏", "风电", "充电"),
    "资源": ("稀土", "黄金", "铜", "铝", "有色", "贵金属", "金属"),
    "能源": ("煤炭", "石油", "原油", "天然气", "电力", "核电", "特高压", "电网"),
    "医药": ("创新药", "医药", "mRNA", "CXO", "医疗器械", "生物"),
    "消费": ("消费", "白酒", "食品", "家电", "旅游", "零售"),
    "农业": ("农业", "猪肉", "养殖", "种植", "农产品", "化肥"),
    "汽车": ("汽车", "自动驾驶", "汽车零部件", "智能驾驶"),
    "金融": ("银行", "券商", "证券", "保险", "金融"),
    "宏观政策": ("政策", "规划", "行动计划", "专项", "改革", "货币政策", "财政政策"),
}

EVENT_TYPE_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rumor", ("传闻", "网传", "据说", "消息人士")),
    ("clarification", ("澄清", "说明", "回复问询")),
    ("risk_event", ("处罚", "立案", "诉讼", "违规", "退市", "风险提示", "减持")),
    ("policy", ("政策", "规划", "行动计划", "专项", "国务院", "发改委", "央行")),
    ("earnings", ("业绩", "预增", "预亏", "盈利", "净利润", "财报")),
    ("contract_order", ("中标", "订单", "合同", "签署", "签订")),
    ("merger_acquisition", ("并购", "收购", "重组", "重大资产")),
    ("price_increase", ("涨价", "提价", "价格上调")),
    ("supply_disruption", ("停产", "减产", "供应中断", "事故")),
    ("technology_breakthrough", ("突破", "发布", "首个", "量产", "技术")),
    ("market_abnormal_move", ("异动", "快速上涨", "快速下跌", "涨停", "跌停")),
    ("macro", ("GDP", "通胀", "利率", "就业", "经济数据", "海外")),
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _epoch_datetime(value: Any) -> datetime | None:
    """Convert a provider epoch timestamp without treating it as a date string."""
    number = _number(value)
    if number is None:
        return _datetime(value)
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _json_safe(value: Any) -> Any:
    """Make nested provider payloads safe for SQLAlchemy JSON columns."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _source_kind(item: dict[str, Any]) -> str:
    explicit = _clean(item.get("source_kind") or item.get("scope"))
    if explicit in SOURCE_PRIORITY:
        return explicit
    source = _clean(item.get("source") or "").lower()
    if any(term in source for term in ("政府", "发改", "央行", "交易所", "官方")):
        return "government_official"
    if any(term in source for term in ("公告", "eastmoney", "ftshare", "巨潮")):
        return "company_announcement"
    if "cls" in source or "财联社" in source:
        return "cls"
    if "market" in source or "行情" in source:
        return "market_snapshot"
    return "unknown"


def _event_type(title: str, source_kind: str) -> str:
    for event_type, terms in EVENT_TYPE_TERMS:
        if any(term.lower() in title.lower() for term in terms):
            return event_type
    if source_kind == "company_announcement":
        return "company_announcement"
    if source_kind == "government_official":
        return "policy"
    return "industry_data"


def _topics(title: str) -> list[dict[str, Any]]:
    matched = []
    for topic, terms in TOPIC_DICTIONARY.items():
        hits = [term for term in terms if term.lower() in title.lower()]
        if hits:
            matched.append({"name": topic, "relevance_score": min(100, 58 + len(hits) * 12), "reason": "标题出现关键词：" + "、".join(hits[:4]), "direction": "mixed"})
    return sorted(matched, key=lambda item: item["relevance_score"], reverse=True)[:5]


def _novelty(published_at: datetime | None, now: datetime) -> float:
    if published_at is None:
        return 35.0
    age = max(0.0, (now - published_at).total_seconds() / 3600)
    if age <= 1:
        return 100.0
    if age <= 6:
        return 88.0
    if age <= 24:
        return 70.0
    if age <= 72:
        return 40.0
    return 20.0


def _certainty(title: str, source_kind: str) -> float:
    score = {"government_official": 96, "exchange_official": 96, "company_announcement": 94, "cninfo": 94, "cls": 68, "market_snapshot": 82, "mainstream_finance": 72, "unknown": 25}.get(source_kind, 25)
    if any(term in title for term in ("传闻", "网传", "据说", "消息人士")):
        score = min(score, 35)
    if any(term in title for term in ("公告", "正式", "发布", "披露")):
        score = min(100, score + 5)
    return float(score)


def _impact(title: str, event_type: str) -> float:
    strong = ("重大", "首次", "突破", "中标", "并购", "涨价", "停产", "制裁", "降息", "加息", "政策")
    negative = ("处罚", "立案", "亏损", "减持", "退市", "事故", "停产", "风险提示")
    base = 48.0 + sum(9 for term in strong if term in title) + sum(8 for term in negative if term in title)
    if event_type in {"policy", "risk_event", "merger_acquisition", "supply_disruption"}:
        base += 10
    return min(100.0, base)


def _direction(title: str) -> str:
    positive = ("支持", "促进", "上涨", "涨价", "中标", "预增", "突破", "回购", "增持", "降息")
    negative = ("处罚", "下跌", "亏损", "预亏", "减持", "停产", "制裁", "加息", "风险")
    plus = sum(title.count(term) for term in positive)
    minus = sum(title.count(term) for term in negative)
    return "positive" if plus > minus else "negative" if minus > plus else "mixed"


def _level(score: float) -> str:
    return "S" if score >= 85 else "A" if score >= 75 else "B" if score >= 60 else "C"


def _lifecycle(score: float, confirmation: float, novelty: float) -> str:
    if novelty < 30:
        return "FADING"
    if confirmation >= 70 and score >= 75:
        return "MARKET_REACTING"
    if score >= 75:
        return "CONFIRMED"
    if confirmation >= 45:
        return "EXPANDING"
    return "DISCOVERED"


class EventRadarService:
    CACHE_SECONDS = 30

    def __init__(self) -> None:
        self._cache: tuple[float, dict[str, Any]] | None = None
        self._lock = asyncio.Lock()
        self._memory_health: dict[str, dict[str, Any]] = {}

    async def _provider_call(self, provider: str, call) -> list[dict[str, Any]]:
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(call, timeout=8.0)
            rows = result if isinstance(result, list) else []
            self._memory_health[provider] = {
                "provider": provider,
                "status": "HEALTHY" if rows else "DEGRADED",
                "last_success_at": shanghai_now().isoformat() if rows else None,
                "last_failure_at": None,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "error_count": 0,
                "empty_count": 1 if not rows else 0,
                "last_record_time": rows[0].get("published_at") if rows else None,
                "source_mode": "live_or_cache",
            }
            return rows
        except Exception as exc:
            previous = self._memory_health.get(provider, {})
            self._memory_health[provider] = {
                **previous,
                "provider": provider,
                "status": "FAILED" if not previous else "DEGRADED",
                "last_failure_at": shanghai_now().isoformat(),
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "error_count": int(previous.get("error_count") or 0) + 1,
                "last_error": type(exc).__name__,
            }
            return []

    async def _policy_items(self) -> list[dict[str, Any]]:
        context = await macro_policy_news_collector.get_context()
        result = []
        for item in [
            *(context.get("policy_items") or []),
            *(context.get("international_items") or []),
            *(context.get("market_news_items") or []),
        ]:
            result.append({
                **item,
                "provider": item.get("provider") or "macro_policy_news",
                "source_kind": (
                    "government_official"
                    if item.get("scope") == "domestic_policy"
                    else "mainstream_finance"
                ),
                "summary": item.get("summary") or item.get("title"),
            })
        return result

    async def _cls_items(self) -> list[dict[str, Any]]:
        """Fetch CLS telegraph data through a dependency-free public adapter.

        AKShare is retained as a compatibility fallback, but the production
        path must not depend on a particular AKShare version exposing the same
        function name.  Both adapters read the public CLS feed and preserve
        the provider timestamp; neither promotes an unverified item above C.
        """
        params = {
            "app": "CailianpressWeb",
            "category": "",
            "last_time": int(time.time()),
            "os": "web",
            "refresh_type": "1",
            "rn": "40",
            "sv": "8.4.6",
        }
        params["sign"] = hashlib.md5(
            hashlib.sha1(urlencode(params).encode("utf-8")).hexdigest().encode("utf-8")
        ).hexdigest()
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; AIBuffettResearch/1.0)",
            "Referer": "https://www.cls.cn/telegraph",
            "Accept": "application/json,text/plain,*/*",
        }
        try:
            async with httpx.AsyncClient(timeout=7.0, headers=headers, follow_redirects=True) as client:
                response = await client.get(CLS_ROLL_URL, params=params)
                response.raise_for_status()
                payload = response.json()
            rows = ((payload.get("data") or {}).get("roll_data") or []) if isinstance(payload, dict) else []
            normalized = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                title = _clean(row.get("title") or row.get("content"))
                if not title:
                    continue
                item_id = _clean(row.get("id"))
                normalized.append({
                    "source": "财联社公开电报",
                    "source_kind": "cls",
                    "title": title,
                    "summary": _clean(row.get("content") or row.get("brief") or title),
                    "published_at": _epoch_datetime(row.get("ctime")),
                    "url": f"https://www.cls.cn/detail/{item_id}" if item_id else "https://www.cls.cn/telegraph",
                    "provider": "cls_http",
                })
            if normalized:
                return normalized[:80]
            raise RuntimeError("CLS public feed returned no rows")
        except Exception as direct_error:
            # AKShare remains useful on installations where its own request
            # policy or parser has already been configured by the operator.
            try:
                import akshare as ak  # type: ignore

                frame = await asyncio.wait_for(asyncio.to_thread(ak.stock_info_global_cls), timeout=7.0)
                if frame is None or not hasattr(frame, "to_dict"):
                    raise RuntimeError("AKShare CLS returned no frame")
                rows = frame.to_dict("records")[-80:]
                normalized = [{
                    "source": "财联社/AKShare",
                    "source_kind": "cls",
                    "title": _clean(row.get("标题") or row.get("title") or row.get("content")),
                    "summary": _clean(row.get("内容") or row.get("content")),
                    "published_at": _datetime(row.get("发布时间") or row.get("date") or row.get("time")),
                    "url": row.get("链接") or row.get("url") or "https://www.cls.cn/telegraph",
                    "provider": "akshare_cls",
                } for row in rows if _clean(row.get("标题") or row.get("title") or row.get("content"))]
                if normalized:
                    return normalized
                raise RuntimeError("AKShare CLS returned no rows")
            except Exception as fallback_error:
                raise RuntimeError("CLS providers unavailable") from fallback_error

    async def _announcement_items(self) -> list[dict[str, Any]]:
        async with async_session() as session:
            latest = (await session.execute(select(func.max(StockDailyBar.trade_date)))).scalar_one_or_none()
            codes = list((await session.execute(select(StockDailyBar.stock_code).where(StockDailyBar.trade_date == latest).order_by(desc(StockDailyBar.amount)).limit(12))).scalars().all()) if latest else []
        if not codes:
            return []
        audit = await macro_policy_news_collector.get_stock_announcements_audit(codes, max_stocks=12)
        result = []
        for code, items in (audit.get("announcements") or {}).items():
            for item in items[:3]:
                result.append({
                    **item,
                    "provider": "company_announcements",
                    "source_kind": "company_announcement",
                    "stock_code": code,
                    "summary": item.get("title"),
                })
        return result

    async def _market_items(self) -> list[dict[str, Any]]:
        target = None
        async with async_session() as session:
            target = (await session.execute(select(func.max(StockDailyBar.trade_date)))).scalar_one_or_none()
            rows = list((await session.execute(select(StockDailyBar).where(StockDailyBar.trade_date == target).order_by(desc(StockDailyBar.amount)).limit(80))).scalars().all()) if target else []

        # During the session, refresh a bounded active-universe sample.  The
        # radar must expose that this is a live sample, not claim full-market
        # real-time coverage from a stale daily table.
        if rows and is_a_share_market_session():
            try:
                live_payload = await asyncio.wait_for(
                    collector.fetch_stock_quotes([row.stock_code for row in rows]),
                    timeout=8.0,
                )
                live_rows = live_payload.get("stocks") or []
                live_items = []
                for item in live_rows:
                    change = _number(item.get("change_pct"))
                    if change is None or abs(change) < 5:
                        continue
                    live_items.append({
                        "provider": "market_snapshot_live_sample",
                        "source": "实时行情异动样本",
                        "source_kind": "market_snapshot",
                        "title": f"{item.get('name') or item.get('code')} 实时涨跌幅 {change:+.2f}%",
                        "summary": "交易时段从实时行情源获取的活跃股票异动样本；不是全市场覆盖。",
                        "published_at": _epoch_datetime(item.get("quote_timestamp")) or shanghai_now().replace(tzinfo=None),
                        "stock_code": item.get("code"),
                        "market_confirmed": True,
                        "cached": False,
                        "coverage_scope": "bounded_active_universe",
                    })
                if live_items:
                    return live_items[:20]
            except Exception:
                # The daily snapshot below is still useful outside the session
                # or while the live provider is rate-limited, but is labelled
                # cached so downstream scoring cannot mistake it for live data.
                pass
        result = []
        for row in rows:
            change = _number(row.change_pct)
            if change is None or abs(change) < 5:
                continue
            result.append({
                "provider": "market_snapshot_cache",
                "source": "行情缓存异动",
                "source_kind": "market_snapshot",
                "title": f"{row.stock_name or row.stock_code} 日内涨跌幅 {change:+.2f}%",
                "summary": "来自最近可核验行情快照的异常波动观察，不代表实时盘中数据。",
                "published_at": datetime.combine(row.trade_date, datetime.min.time()),
                "stock_code": row.stock_code,
                "market_confirmed": True,
                "cached": True,
                "coverage_scope": "latest_daily_snapshot",
            })
        return result[:20]

    async def _collect(self) -> list[dict[str, Any]]:
        results = await asyncio.gather(
            self._provider_call("official_policy", self._policy_items()),
            self._provider_call("cls_news", self._cls_items()),
            self._provider_call("company_announcements", self._announcement_items()),
            self._provider_call("market_snapshot", self._market_items()),
            return_exceptions=False,
        )
        return [item for group in results for item in group if isinstance(item, dict)]

    async def _stock_mapping(self, event: dict[str, Any], topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        direct_code = _clean(event.get("stock_code"))
        if direct_code:
            async with async_session() as session:
                row = (await session.execute(select(StockUniverseSnapshot).where(StockUniverseSnapshot.stock_code == direct_code).order_by(desc(StockUniverseSnapshot.trade_date)).limit(1))).scalar_one_or_none()
            return [{
                "stock_code": direct_code,
                "stock_name": row.stock_name if row else direct_code,
                "relation_type": "direct_announcement",
                "relation_score": 96,
                "benefit_score": 50,
                "business_evidence": "公司公告来源直接关联；方向仍需阅读原文与市场确认。",
                "market_score": 50,
                "total_score": 72,
                "evidence_tag": "FACT",
            }]
        if not topics:
            return []
        terms = tuple(term for topic in topics for term in TOPIC_DICTIONARY.get(topic["name"], ()))
        if not terms:
            return []
        async with async_session() as session:
            latest = (await session.execute(select(func.max(StockUniverseSnapshot.trade_date)))).scalar_one_or_none()
            rows = list((await session.execute(select(StockUniverseSnapshot).where(StockUniverseSnapshot.trade_date == latest).limit(1200))).scalars().all()) if latest else []
        mapped = []
        title = _clean(event.get("title"))
        for row in rows:
            sector = _clean(row.industry)
            hits = [term for term in terms if term and term in sector]
            if not hits:
                continue
            relation = min(78, 52 + len(hits) * 8)
            mapped.append({
                "stock_code": row.stock_code,
                "stock_name": row.stock_name,
                "relation_type": "sector_inferred",
                "relation_score": relation,
                "benefit_score": relation - 8,
                "business_evidence": f"仅有行业字段与题材关键词匹配：{'、'.join(hits[:3])}；未验证主营收入或公告业务关系。",
                "market_score": 50,
                "total_score": round((relation + relation - 8 + 50) / 3, 2),
                "evidence_tag": "INFERRED",
            })
        return sorted(mapped, key=lambda item: item["total_score"], reverse=True)[:12]

    async def _normalize(self, raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = shanghai_now().replace(tzinfo=None)
        normalized = []
        seen: set[str] = set()
        # Prefer the strongest source when several providers publish the same
        # headline. Raw provider records are still retained separately, while
        # the user-facing event remains one canonical object.
        ordered_items = sorted(
            raw_items,
            key=lambda item: SOURCE_PRIORITY.get(_source_kind(item), 20),
            reverse=True,
        )
        for item in ordered_items:
            title = _clean(item.get("title") or item.get("canonical_title"))
            if len(title) < 4:
                continue
            source_kind = _source_kind(item)
            published_at = _datetime(item.get("published_at"))
            canonical = re.sub(r"[^\w\u4e00-\u9fff]+", "", title.lower())[:300]
            fingerprint = _hash(canonical)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            topics = _topics(title)
            event_type = _event_type(title, source_kind)
            source_score = float(SOURCE_PRIORITY.get(source_kind, 20))
            certainty = _certainty(title, source_kind)
            novelty = _novelty(published_at, now)
            impact = _impact(title, event_type)
            topic_relevance = max((float(topic["relevance_score"]) for topic in topics), default=25.0)
            market_confirm = 75.0 if item.get("market_confirmed") else 35.0 if source_kind in {"market_snapshot", "company_announcement"} else 20.0
            urgency = 85.0 if event_type in {"risk_event", "market_abnormal_move", "supply_disruption"} else 55.0
            score = source_score * 0.20 + certainty * 0.15 + novelty * 0.15 + impact * 0.15 + topic_relevance * 0.10 + market_confirm * 0.20 + urgency * 0.05
            if event_type == "rumor" or certainty < 45:
                score = min(score, 59.0)
            if novelty < 30:
                score = min(score, 69.0)
            event_id = f"EV{now.strftime('%Y%m%d')}_{fingerprint[:12]}"
            normalized.append({
                "event_id": event_id,
                "canonical_title": title,
                "summary": _clean(item.get("summary") or title),
                "event_type": event_type,
                "source": _clean(item.get("source") or source_kind),
                "source_kind": source_kind,
                "source_level": "A" if source_score >= 85 else "B" if source_score >= 65 else "C",
                "source_score": round(source_score, 2),
                "certainty_score": round(certainty, 2),
                "novelty_score": round(novelty, 2),
                "impact_score": round(impact, 2),
                "topic_relevance_score": round(topic_relevance, 2),
                "market_confirmation_score": round(market_confirm, 2),
                "urgency_score": round(urgency, 2),
                "event_score": round(score, 2),
                "alert_level": _level(score),
                "direction": _direction(title),
                "first_seen_at": now,
                "last_updated_at": now,
                "published_at": published_at,
                "status": _lifecycle(score, market_confirm, novelty),
                "topics": topics,
                "url": item.get("url"),
                "provider": item.get("provider") or source_kind,
                "stock_code": item.get("stock_code"),
                "cached": bool(item.get("cached")),
            })
        return sorted(normalized, key=lambda item: (item["event_score"], item["published_at"] or datetime.min), reverse=True)

    async def _persist(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        async with async_session() as session:
            for event in events:
                content_hash = _hash(f"{event['provider']}:{event['canonical_title']}")
                raw = (await session.execute(select(RadarRawSource).where(RadarRawSource.content_hash == content_hash))).scalar_one_or_none()
                if raw is None:
                    raw = RadarRawSource(
                        provider=event["provider"], title=event["canonical_title"], content=event["summary"], url=event.get("url"),
                        published_at=event.get("published_at"), fetched_at=event["last_updated_at"], content_hash=content_hash,
                        raw_json={"source_kind": event["source_kind"], "cached": event["cached"]},
                    )
                    session.add(raw)
                row = await session.get(RadarEvent, event["event_id"])
                if row is None:
                    row = RadarEvent(event_id=event["event_id"], canonical_title=event["canonical_title"], summary=event["summary"], event_type=event["event_type"], source_score=event["source_score"], certainty_score=event["certainty_score"], novelty_score=event["novelty_score"], impact_score=event["impact_score"], topic_relevance_score=event["topic_relevance_score"], market_confirmation_score=event["market_confirmation_score"], urgency_score=event["urgency_score"], event_score=event["event_score"], alert_level=event["alert_level"], direction=event["direction"], first_seen_at=event["first_seen_at"], last_updated_at=event["last_updated_at"], status=event["status"], source_level=event["source_level"], data_cutoff_time=event["last_updated_at"], payload=_json_safe(event))
                    session.add(row)
                else:
                    row.last_updated_at = event["last_updated_at"]
                    row.payload = _json_safe(event)
                    row.event_score = event["event_score"]
                    row.alert_level = event["alert_level"]
                    row.status = event["status"]
                    row.market_confirmation_score = event["market_confirmation_score"]
                # Replacing a small set of child rows avoids duplicates while
                # retaining the canonical event itself.
                await session.execute(delete(RadarEventTopic).where(RadarEventTopic.event_id == event["event_id"]))
                await session.execute(delete(RadarEventStock).where(RadarEventStock.event_id == event["event_id"]))
                # Flush the replacements before inserting children with the
                # same unique keys. This matters on SQLite and PostgreSQL,
                # where a delete and insert in one unit of work may otherwise
                # be ordered as two conflicting inserts.
                await session.flush()
                for topic in event["topics"]:
                    session.add(RadarEventTopic(event_id=event["event_id"], topic_name=topic["name"], relevance_score=topic["relevance_score"], direction=topic["direction"], reason=topic["reason"]))
                for stock in await self._stock_mapping(event, event["topics"]):
                    session.add(RadarEventStock(event_id=event["event_id"], **stock))
                if event["alert_level"] in {"S", "A"}:
                    dedupe_key = f"{event['event_id']}:{event['alert_level']}"
                    existing = (await session.execute(select(RadarAlert).where(RadarAlert.dedupe_key == dedupe_key))).scalar_one_or_none()
                    if existing is None:
                        session.add(RadarAlert(alert_id=f"AL{event['event_id'][2:]}", event_id=event["event_id"], level=event["alert_level"], title=event["canonical_title"], message=event["summary"], created_at=event["last_updated_at"], channel="in_app", status="NEW", dedupe_key=dedupe_key))
            for provider, health in self._memory_health.items():
                row = await session.get(RadarProviderHealth, provider)
                if row is None:
                    row = RadarProviderHealth(provider=provider)
                    session.add(row)
                row.status = health.get("status") or "UNKNOWN"
                row.latency_ms = health.get("latency_ms")
                row.error_count = int(health.get("error_count") or 0)
                row.empty_count = int(health.get("empty_count") or 0)
                row.last_success_at = _datetime(health.get("last_success_at"))
                row.last_failure_at = _datetime(health.get("last_failure_at"))
                row.last_record_time = _datetime(health.get("last_record_time"))
                row.details = _json_safe(health)
            await session.commit()

    async def refresh(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if self._cache and not force and now - self._cache[0] < self.CACHE_SECONDS:
            payload = dict(self._cache[1])
            payload["cache_used"] = True
            return payload
        async with self._lock:
            now = time.monotonic()
            if self._cache and not force and now - self._cache[0] < self.CACHE_SECONDS:
                payload = dict(self._cache[1])
                payload["cache_used"] = True
                return payload
            raw = await self._collect()
            events = await self._normalize(raw)
            await self._persist(events)
            payload = {
                "generated_at": shanghai_now().isoformat(),
                "data_cutoff_time": shanghai_now().isoformat(),
                "events": [self._public_event(event) for event in events[:80]],
                "count": len(events),
                "source_count": len({event["provider"] for event in events}),
                "provider_health": list(self._memory_health.values()),
                "quality": {
                    "status": "LIVE_AND_CACHE_MIXED" if any(event.get("cached") for event in events) else "OBSERVED_OR_SOURCE_EMPTY",
                    "source_policy": "官方/公司公告优先；未证实传闻最高C级",
                    "realtime": bool(is_a_share_market_session()),
                    "warning": "公开免费源可能延迟或限流；市场确认分不会用旧行情冒充实时。",
                },
                "cache_used": False,
            }
            self._cache = (time.monotonic(), payload)
            return payload

    @staticmethod
    def _public_event(event: dict[str, Any]) -> dict[str, Any]:
        return {
            key: (value.isoformat() if isinstance(value, datetime) else value)
            for key, value in event.items()
            if key not in {"stock_code"}
        }

    async def events(self, *, level: str | None = None, topic: str | None = None, event_type: str | None = None, status: str | None = None, limit: int = 50, refresh: bool = False) -> dict[str, Any]:
        if refresh or not self._cache:
            await self.refresh(force=refresh)
        async with async_session() as session:
            statement = select(RadarEvent).order_by(desc(RadarEvent.last_updated_at)).limit(max(1, min(limit, 200)))
            if level:
                statement = statement.where(RadarEvent.alert_level == level.upper())
            if event_type:
                statement = statement.where(RadarEvent.event_type == event_type)
            if status:
                statement = statement.where(RadarEvent.status == status)
            rows = list((await session.execute(statement)).scalars().all())
            result = []
            for row in rows:
                if topic:
                    topic_row = (await session.execute(select(RadarEventTopic.id).where(RadarEventTopic.event_id == row.event_id, RadarEventTopic.topic_name == topic).limit(1))).scalar_one_or_none()
                    if topic_row is None:
                        continue
                result.append(self._row_public(row))
        return {"events": result, "count": len(result), "generated_at": shanghai_now().isoformat(), "quality": {"status": "DATABASE_CACHE", "source": "radar_events"}}

    @staticmethod
    def _row_public(row: RadarEvent) -> dict[str, Any]:
        payload = dict(row.payload or {})
        payload.update({"event_id": row.event_id, "event_score": row.event_score, "alert_level": row.alert_level, "status": row.status, "last_updated_at": row.last_updated_at.isoformat() if row.last_updated_at else None})
        return payload

    async def detail(self, event_id: str) -> dict[str, Any] | None:
        async with async_session() as session:
            row = await session.get(RadarEvent, event_id)
            if row is None:
                return None
            topics = list((await session.execute(select(RadarEventTopic).where(RadarEventTopic.event_id == event_id).order_by(desc(RadarEventTopic.relevance_score)))).scalars().all())
            stocks = list((await session.execute(select(RadarEventStock).where(RadarEventStock.event_id == event_id).order_by(desc(RadarEventStock.total_score)))).scalars().all())
            alerts = list((await session.execute(select(RadarAlert).where(RadarAlert.event_id == event_id).order_by(desc(RadarAlert.created_at)))).scalars().all())
        result = self._row_public(row)
        result["topics"] = [{"name": item.topic_name, "relevance_score": item.relevance_score, "direction": item.direction, "reason": item.reason} for item in topics]
        result["stocks"] = [{"code": item.stock_code, "name": item.stock_name, "relation_type": item.relation_type, "relation_score": item.relation_score, "benefit_score": item.benefit_score, "business_evidence": item.business_evidence, "market_score": item.market_score, "total_score": item.total_score, "evidence_tag": item.evidence_tag} for item in stocks]
        result["alerts"] = [{"alert_id": item.alert_id, "level": item.level, "status": item.status, "created_at": item.created_at.isoformat()} for item in alerts]
        result["quality"] = {"ai_explanation_policy": "AI只能解释这些结构化事实，不能改变事件分数或补造业务关系。"}
        return result

    async def hot_topics(self, limit: int = 20) -> dict[str, Any]:
        async with async_session() as session:
            rows = list((await session.execute(
                select(RadarEventTopic.topic_name, func.count(RadarEventTopic.id), func.max(RadarEventTopic.relevance_score))
                .join(RadarEvent, RadarEvent.event_id == RadarEventTopic.event_id)
                .where(RadarEvent.last_updated_at >= datetime.utcnow() - timedelta(days=3))
                .group_by(RadarEventTopic.topic_name)
                .order_by(desc(func.count(RadarEventTopic.id)), desc(func.max(RadarEventTopic.relevance_score)))
                .limit(max(1, min(limit, 50)))
            )).all())
        return {"topics": [{"name": row[0], "event_count": row[1], "max_relevance": row[2]} for row in rows], "generated_at": shanghai_now().isoformat()}

    async def alerts(self, limit: int = 30) -> dict[str, Any]:
        async with async_session() as session:
            rows = list((await session.execute(
                select(RadarAlert).order_by(desc(RadarAlert.created_at)).limit(max(1, min(limit, 100)))
            )).scalars().all())
        return {"alerts": [{"alert_id": row.alert_id, "event_id": row.event_id, "level": row.level, "title": row.title, "message": row.message, "created_at": row.created_at.isoformat(), "status": row.status} for row in rows]}

    async def providers(self) -> dict[str, Any]:
        async with async_session() as session:
            rows = list((await session.execute(select(RadarProviderHealth).order_by(RadarProviderHealth.provider))).scalars().all())
        result = [{"provider": row.provider, "status": row.status, "latency_ms": row.latency_ms, "error_count": row.error_count, "empty_count": row.empty_count, "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None, "last_failure_at": row.last_failure_at.isoformat() if row.last_failure_at else None, "details": row.details} for row in rows]
        for provider, health in self._memory_health.items():
            if not any(item["provider"] == provider for item in result):
                result.append(health)
        return {"providers": result, "generated_at": shanghai_now().isoformat(), "policy": "失败自动降级到缓存/其他公开源，降级状态不冒充实时。"}

    async def replay(self, *, start: str | None = None, end: str | None = None, limit: int = 100) -> dict[str, Any]:
        async with async_session() as session:
            statement = select(RadarEvent).order_by(RadarEvent.first_seen_at).limit(max(1, min(limit, 500)))
            if start:
                parsed = _datetime(start)
                if parsed:
                    statement = statement.where(RadarEvent.first_seen_at >= parsed)
            if end:
                parsed = _datetime(end)
                if parsed:
                    statement = statement.where(RadarEvent.first_seen_at <= parsed)
            rows = list((await session.execute(statement)).scalars().all())
        return {"mode": "REPLAY", "events": [self._row_public(row) for row in rows], "count": len(rows), "warning": "历史回放用于结构验证，不代表未来收益。"}

    async def stream(self) -> AsyncIterator[str]:
        """Small SSE stream that works without Redis/WebSocket infrastructure."""
        last_signature = ""
        for _ in range(30):
            payload = await self.events(limit=10)
            signature = _hash(json.dumps(payload.get("events") or [], ensure_ascii=False, default=str))
            if signature != last_signature:
                last_signature = signature
                yield f"event: radar\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
            else:
                yield ": heartbeat\n\n"
            await asyncio.sleep(5)


event_radar_service = EventRadarService()
