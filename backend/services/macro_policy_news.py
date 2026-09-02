"""Verified macro, policy, and company-announcement inputs for stock research.

The collector deliberately keeps source titles, publication dates, and URLs in
the returned data. It extracts structured facts from official public sources;
it does not infer unverified news content or create an LLM-generated summary.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from datetime import date
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from config import settings
from services.data_collector import collector, shanghai_now
from services.ftshare_mcp import ftshare_mcp_client
from market_data.numcat.market_provider import numcat_market_provider


GOVERNMENT_POLICY_URL = "https://www.gov.cn/zhengce/"
NDRC_NEWS_URL = "https://www.ndrc.gov.cn/xwdt/xwfb/"
PBOC_NEWS_URL = "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html"
IMF_GROWTH_URL = "https://www.imf.org/external/datamapper/api/v1/NGDP_RPCH/CHN/USA"
IMF_GROWTH_PAGE_URL = "https://www.imf.org/external/datamapper/NGDP_RPCH"
WORLD_BANK_GROWTH_URL = (
    "https://api.worldbank.org/v2/country/CHN;USA;WLD/indicator/"
    "NY.GDP.MKTP.KD.ZG?format=json&per_page=300"
)
WORLD_BANK_GROWTH_PAGE_URL = "https://data.worldbank.org/indicator/NY.GDP.MKTP.KD.ZG"
EASTMONEY_ANNOUNCEMENT_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"

POLICY_SOURCE_CONFIG = (
    ("中国政府网", GOVERNMENT_POLICY_URL, "domestic_policy"),
    ("国家发展改革委", NDRC_NEWS_URL, "domestic_policy"),
    ("中国人民银行", PBOC_NEWS_URL, "domestic_policy"),
)

_ANCHOR_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r"\bhref\s*=\s*[\"'](?P<value>[^\"']+)[\"']", re.IGNORECASE)
_TITLE_RE = re.compile(r"\btitle\s*=\s*[\"'](?P<value>[^\"']+)[\"']", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(20\d{2})[/-](\d{1,2})[/-](\d{1,2})\b")
_TAG_RE = re.compile(r"<[^>]+>")
_SAFE_STOCK_CODE_RE = re.compile(r"^\d{6}$")

_NEGATIVE_POLICY_WORDS = (
    "风险提示", "处罚", "整治", "收紧", "下调", "波动", "关税", "制裁", "衰退", "通胀压力",
)
_SUPPORT_POLICY_WORDS = (
    "支持", "促进", "行动计划", "规划", "专项", "扩大", "推进", "实施", "发展", "投资",
    "再贷款", "科技金融", "设备更新", "消费", "高质量",
)
_POSITIVE_ANNOUNCEMENT_WORDS = (
    "中标", "签订", "签署", "回购", "增持", "预增", "扭亏", "盈利", "分红", "权益分派",
    "重大合同", "战略合作", "项目投资", "取得", "通过",
)
_NEGATIVE_ANNOUNCEMENT_WORDS = (
    "风险提示", "减持", "立案", "处罚", "亏损", "预亏", "终止", "诉讼", "违规", "退市",
    "质押", "问询", "不确定性", "下滑",
)

_SECTOR_TERMS: dict[str, tuple[str, ...]] = {
    "科技": ("半导体", "软件", "计算机", "通信", "电子", "人工智能", "AI", "数据", "算力", "芯片"),
    "金融": ("银行", "证券", "保险", "金融", "信托", "多元金融", "资本市场"),
    "消费": ("白酒", "酿酒", "食品", "饮料", "乳业", "家电", "旅游", "酒店", "商贸", "消费"),
    "新能源": ("新能源", "电动", "汽车", "光伏", "储能", "锂电", "风电", "充电", "电力设备"),
    "能源": ("石油", "煤炭", "天然气", "油气", "电力", "公用事业", "炼化"),
    "工业": ("机械", "工程", "建筑", "基建", "水泥", "玻纤", "制造", "轨交", "军工"),
    "地产": ("房地产", "地产", "物业", "建材", "家居", "住房"),
    "医药": ("医药", "医疗", "生物", "药品", "中药"),
    "农业": ("农业", "种植", "养殖", "农产品", "饲料", "化肥"),
    "资源": ("黄金", "有色", "稀土", "铜", "铝", "金属", "贵金属"),
    "外贸物流": ("外贸", "出口", "港口", "航运", "物流", "贸易", "纺织"),
}


def _strip_html(value: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", value)).split())


def _normalise_date(value: str | None) -> str | None:
    if not value:
        return None
    match = _DATE_RE.search(value)
    if not match:
        return None
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _number(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class MacroPolicyNewsCollector:
    """Fetches evidence once and reuses it across a stock-selection run."""

    _CONTEXT_CACHE_SECONDS = 15 * 60
    _ANNOUNCEMENT_CACHE_SECONDS = 15 * 60
    _ANNOUNCEMENT_CONCURRENCY = 8

    def __init__(self) -> None:
        self._context_cache: tuple[float, dict] | None = None
        self._announcement_cache: dict[str, tuple[float, list[dict]]] = {}
        self._announcement_status_cache: dict[str, tuple[float, dict]] = {}
        self._context_lock = asyncio.Lock()

    @staticmethod
    def empty_context() -> dict:
        return {
            "available": False,
            "updated_at": shanghai_now().isoformat(),
            "summary": "宏观、政策与公告源当前不可用，本轮不计入新闻政策评分。",
            "international_items": [],
            "policy_items": [],
            "market_news_items": [],
            "source_status": {},
            "macro_adjustment": 0.0,
        }

    @staticmethod
    def _timeout() -> float:
        try:
            configured = float(settings.macro_news_timeout)
        except (TypeError, ValueError):
            configured = 8.0
        return min(max(configured, 2.0), 15.0)

    @staticmethod
    def _cache_seconds() -> int:
        try:
            configured = int(settings.macro_news_cache_seconds)
        except (TypeError, ValueError):
            configured = MacroPolicyNewsCollector._CONTEXT_CACHE_SECONDS
        return min(max(configured, 60), 3600)

    async def _fetch_text(self, client: httpx.AsyncClient, url: str) -> str:
        response = await client.get(url)
        response.raise_for_status()
        return response.text

    async def _fetch_json(self, client: httpx.AsyncClient, url: str) -> Any:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _parse_policy_items(document: str, source: str, page_url: str, limit: int = 8) -> list[dict]:
        items: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for match in _ANCHOR_RE.finditer(document):
            attrs = match.group("attrs")
            href_match = _HREF_RE.search(attrs)
            if not href_match:
                continue
            title_match = _TITLE_RE.search(attrs)
            title = _strip_html(title_match.group("value") if title_match else match.group("body"))
            if len(title) < 6 or title in {"更多", "首页", "政策"}:
                continue
            date_value = _normalise_date(document[match.end():match.end() + 360])
            if not date_value:
                continue
            url = urljoin(page_url, html.unescape(href_match.group("value")))
            if urlparse(url).scheme not in {"http", "https"}:
                continue
            key = (title, url)
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "source": source,
                "scope": "domestic_policy",
                "title": title,
                "published_at": date_value,
                "url": url,
                "impact": "neutral",
            })
            if len(items) >= limit:
                break
        return items

    @staticmethod
    def _series_value(values: object, target_year: str) -> float | None:
        if not isinstance(values, dict):
            return None
        direct = _number(values.get(target_year))
        if direct is not None:
            return direct
        valid = [(str(key), _number(value)) for key, value in values.items()]
        valid = [(key, value) for key, value in valid if value is not None]
        if not valid:
            return None
        return sorted(valid, key=lambda item: item[0], reverse=True)[0][1]

    @staticmethod
    def _latest_world_bank_values(payload: object) -> tuple[dict[str, float], str | None]:
        if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
            return {}, None
        metadata = payload[0] if isinstance(payload[0], dict) else {}
        values: dict[str, float] = {}
        for row in payload[1]:
            if not isinstance(row, dict):
                continue
            country = str(row.get("countryiso3code") or "")
            value = _number(row.get("value"))
            if country and value is not None and country not in values:
                values[country] = value
        return values, str(metadata.get("lastupdated") or "") or None

    @staticmethod
    def _format_growth(label: str, value: float | None) -> str | None:
        return f"{label} {value:.1f}%" if value is not None else None

    def _international_items(self, imf_payload: object, world_bank_payload: object) -> tuple[list[dict], float]:
        target_year = str(shanghai_now().year)
        items: list[dict] = []
        adjustment = 0.0

        imf_values = {}
        if isinstance(imf_payload, dict):
            imf_values = ((imf_payload.get("values") or {}).get("NGDP_RPCH") or {})
        china_forecast = self._series_value(imf_values.get("CHN"), target_year) if isinstance(imf_values, dict) else None
        usa_forecast = self._series_value(imf_values.get("USA"), target_year) if isinstance(imf_values, dict) else None
        imf_parts = [
            self._format_growth("中国", china_forecast),
            self._format_growth("美国", usa_forecast),
        ]
        if any(imf_parts):
            items.append({
                "source": "IMF WEO DataMapper",
                "scope": "international_macro",
                "title": f"IMF {target_year} 年实际 GDP 增长预测：" + "，".join(part for part in imf_parts if part),
                "published_at": shanghai_now().date().isoformat(),
                "url": IMF_GROWTH_PAGE_URL,
                "impact": "neutral",
            })
            if china_forecast is not None:
                adjustment += 2.0 if china_forecast >= 4.0 else -2.0 if china_forecast < 3.0 else 0.0

        world_bank_values, world_bank_date = self._latest_world_bank_values(world_bank_payload)
        china_actual = world_bank_values.get("CHN")
        world_actual = world_bank_values.get("WLD")
        wb_parts = [
            self._format_growth("中国", china_actual),
            self._format_growth("全球", world_actual),
        ]
        if any(wb_parts):
            items.append({
                "source": "世界银行指标库",
                "scope": "international_macro",
                "title": "世界银行最近实际 GDP 增长：" + "，".join(part for part in wb_parts if part),
                "published_at": world_bank_date or shanghai_now().date().isoformat(),
                "url": WORLD_BANK_GROWTH_PAGE_URL,
                "impact": "neutral",
            })
            if world_actual is not None:
                adjustment += 1.0 if world_actual >= 2.0 else -1.0 if world_actual < 1.0 else 0.0

        return items, adjustment

    async def _build_context(self) -> dict:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; AIBuffettResearch/1.0)",
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        }
        timeout = httpx.Timeout(self._timeout())
        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
            results = await asyncio.gather(
                *(self._fetch_text(client, source_url) for _, source_url, _ in POLICY_SOURCE_CONFIG),
                self._fetch_json(client, IMF_GROWTH_URL),
                self._fetch_json(client, WORLD_BANK_GROWTH_URL),
                return_exceptions=True,
            )

        policy_items: list[dict] = []
        source_status: dict[str, str] = {}
        for index, (source, source_url, _) in enumerate(POLICY_SOURCE_CONFIG):
            result = results[index]
            if isinstance(result, Exception):
                source_status[source] = "unavailable"
                continue
            parsed = self._parse_policy_items(result, source, source_url)
            source_status[source] = "available" if parsed else "unavailable"
            policy_items.extend(parsed)
        policy_items.sort(key=lambda item: (item.get("published_at") or "", item.get("source") or ""), reverse=True)

        imf_result = results[len(POLICY_SOURCE_CONFIG)]
        world_bank_result = results[len(POLICY_SOURCE_CONFIG) + 1]
        imf_items, imf_adjustment = self._international_items(
            {} if isinstance(imf_result, Exception) else imf_result,
            [],
        )
        world_bank_items, world_bank_adjustment = self._international_items(
            {},
            [] if isinstance(world_bank_result, Exception) else world_bank_result,
        )
        international_items = [*imf_items, *world_bank_items]
        macro_adjustment = imf_adjustment + world_bank_adjustment
        source_status["IMF WEO DataMapper"] = "available" if imf_items else "unavailable"
        source_status["世界银行指标库"] = "available" if world_bank_items else "unavailable"

        market_news_items: list[dict] = []
        if numcat_market_provider.configured:
            try:
                for row in await numcat_market_provider.news(limit=80):
                    title = str(row.get("title") or "").strip()
                    if not title:
                        continue
                    market_news_items.append({
                        "source": str(row.get("source_name") or "猫爪新闻聚合"),
                        "scope": "market_news",
                        "title": title,
                        "summary": str(row.get("summary") or "").strip() or None,
                        "published_at": row.get("published_at") or row.get("display_at"),
                        "url": row.get("url"),
                        "impact": "neutral",
                        "provider": "numcat_news",
                    })
                source_status["猫爪新闻聚合"] = "available"
            except Exception as exc:
                print(f"NumCat news fallback failed: {type(exc).__name__}")
                source_status["猫爪新闻聚合"] = "unavailable"

        available = bool(policy_items or international_items or market_news_items)
        summary_parts = []
        if international_items:
            summary_parts.append(f"国际宏观数据源 {len(international_items)} 个")
        if policy_items:
            summary_parts.append(f"国内政策与发展信息 {len(policy_items)} 条")
        if market_news_items:
            summary_parts.append(f"市场动态 {len(market_news_items)} 条")
        summary = "已核验 " + "，".join(summary_parts) + "。" if summary_parts else self.empty_context()["summary"]
        return {
            "available": available,
            "updated_at": shanghai_now().isoformat(),
            "summary": summary,
            "international_items": international_items,
            "policy_items": policy_items[:18],
            "market_news_items": market_news_items[:80],
            "source_status": source_status,
            "macro_adjustment": max(-4.0, min(4.0, macro_adjustment)),
        }

    async def get_context(self) -> dict:
        now = time.monotonic()
        cached = self._context_cache
        if cached and now - cached[0] < self._cache_seconds():
            return cached[1]
        async with self._context_lock:
            cached = self._context_cache
            now = time.monotonic()
            if cached and now - cached[0] < self._cache_seconds():
                return cached[1]
            try:
                context = await self._build_context()
            except Exception as exc:
                print(f"Macro policy context fetch failed: {type(exc).__name__}")
                context = self.empty_context()
            self._context_cache = (now, context)
            return context

    async def _get_stock_announcements(self, stock_code: str) -> list[dict]:
        now = time.monotonic()
        cached = self._announcement_cache.get(stock_code)
        if cached and now - cached[0] < self._ANNOUNCEMENT_CACHE_SECONDS:
            return cached[1]
        params = {
            "sr": "-1",
            "page_size": "6",
            "page_index": "1",
            "ann_type": "A",
            "client_source": "web",
            "stock_list": stock_code,
            "f_node": "0",
            "s_node": "0",
        }
        eastmoney_failed = False
        source_status = {"available": False, "source": "none", "error": None}
        try:
            payload = await collector.fetch_json(
                EASTMONEY_ANNOUNCEMENT_URL,
                params,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; AIBuffettResearch/1.0)",
                    "Referer": "https://data.eastmoney.com/notices/",
                    "Accept": "application/json,text/plain,*/*",
                },
            )
            rows = ((payload.get("data") or {}).get("list") or [])
            announcements: list[dict] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                title = str(row.get("title_ch") or row.get("title") or "").strip()
                art_code = str(row.get("art_code") or "").strip()
                if not title or not art_code:
                    continue
                categories = [str(column.get("column_name") or "") for column in (row.get("columns") or []) if isinstance(column, dict)]
                notice_date = str(row.get("notice_date") or row.get("display_time") or "")[:10]
                announcements.append({
                    "source": "东方财富公告聚合",
                    "scope": "company_announcement",
                    "title": title,
                    "published_at": notice_date or None,
                    "url": f"https://data.eastmoney.com/notices/detail/{stock_code}/{art_code}.html",
                    "category": " / ".join(category for category in categories if category),
                    "impact": "neutral",
                })
            announcements.sort(key=lambda item: item.get("published_at") or "", reverse=True)
            source_status = {"available": True, "source": "eastmoney", "error": None}
        except Exception as exc:
            print(f"Announcement fetch failed for {stock_code}: {type(exc).__name__}")
            announcements = []
            eastmoney_failed = True
            source_status = {"available": False, "source": "eastmoney", "error": type(exc).__name__}

        if eastmoney_failed and numcat_market_provider.configured:
            try:
                numcat_rows = await numcat_market_provider.announcements(
                    [stock_code], limit=6,
                )
                for row in numcat_rows:
                    title = str(row.get("title") or "").strip()
                    if not title:
                        continue
                    announcements.append({
                        "source": "猫爪公司公告",
                        "scope": "company_announcement",
                        "title": title,
                        "summary": str(row.get("summary") or "").strip() or None,
                        "published_at": str(row.get("event_date") or "")[:10] or None,
                        "url": row.get("content_url"),
                        "category": str(row.get("announcement_type") or ""),
                        "impact": "neutral",
                    })
                if announcements:
                    announcements.sort(key=lambda item: item.get("published_at") or "", reverse=True)
                    source_status = {"available": True, "source": "numcat", "error": None}
                    eastmoney_failed = False
            except Exception as exc:
                print(f"NumCat announcement fallback failed for {stock_code}: {type(exc).__name__}")

        if eastmoney_failed:
            try:
                fallback_rows = await ftshare_mcp_client.get_stock_announcements(stock_code, page_size=6)
                for row in fallback_rows:
                    title = str(row.get("announcement_title") or "").strip()
                    url = ftshare_mcp_client.announcement_document_url(row.get("url_hash"))
                    if not title or not url:
                        continue
                    announcements.append({
                        "source": "FTShare MCP 公告",
                        "scope": "company_announcement",
                        "title": title,
                        "published_at": str(row.get("announcement_time") or "")[:10] or None,
                        "url": url,
                        "category": str(row.get("column_type") or ""),
                        "impact": "neutral",
                    })
                announcements.sort(key=lambda item: item.get("published_at") or "", reverse=True)
                source_status = {"available": True, "source": "ftshare_mcp", "error": None}
            except Exception as exc:
                print(f"FTShare announcement fallback failed for {stock_code}: {type(exc).__name__}")
                source_status = {"available": False, "source": "none", "error": type(exc).__name__}
        self._announcement_cache[stock_code] = (now, announcements)
        self._announcement_status_cache[stock_code] = (now, source_status)
        return announcements

    async def get_stock_announcements(self, stock_codes: list[str], max_stocks: int) -> dict[str, list[dict]]:
        codes = []
        for raw_code in stock_codes:
            code = str(raw_code or "").strip()
            if _SAFE_STOCK_CODE_RE.fullmatch(code) and code not in codes:
                codes.append(code)
        codes = codes[:max(0, max_stocks)]
        semaphore = asyncio.Semaphore(self._ANNOUNCEMENT_CONCURRENCY)

        async def fetch_one(code: str) -> tuple[str, list[dict]]:
            async with semaphore:
                return code, await self._get_stock_announcements(code)

        pairs = await asyncio.gather(*(fetch_one(code) for code in codes), return_exceptions=True)
        return {
            code: announcements
            for pair in pairs
            if not isinstance(pair, Exception)
            for code, announcements in [pair]
        }

    async def get_stock_announcements_audit(self, stock_codes: list[str], max_stocks: int) -> dict:
        """Return records and source availability so an empty result is unambiguous."""
        announcements = await self.get_stock_announcements(stock_codes, max_stocks)
        now = time.monotonic()
        status = {}
        for code in announcements:
            cached = self._announcement_status_cache.get(code)
            if cached and now - cached[0] < self._ANNOUNCEMENT_CACHE_SECONDS:
                status[code] = dict(cached[1])
            else:
                status[code] = {
                    "available": False,
                    "source": "none",
                    "error": "SourceStatusMissing",
                }
        return {
            "announcements": announcements,
            "status": status,
            "requested": min(len(list(dict.fromkeys(stock_codes))), max(0, max_stocks)),
            "covered": sum(bool(item.get("available")) for item in status.values()),
        }

    @staticmethod
    def sector_terms(sector: object) -> tuple[str, ...]:
        value = str(sector or "").strip()
        matched = [value] if value else []
        for terms in _SECTOR_TERMS.values():
            if value and any(term in value for term in terms):
                matched.extend(terms)
        return tuple(dict.fromkeys(term for term in matched if term))

    @staticmethod
    def policy_impact(title: str, sector_terms: tuple[str, ...]) -> tuple[float, str, list[str]]:
        text = title or ""
        matches = [term for term in sector_terms if term and term in text]
        if not matches:
            return 0.0, "neutral", []
        if any(word in text for word in _NEGATIVE_POLICY_WORDS):
            return -6.0, "negative", matches[:3]
        if any(word in text for word in _SUPPORT_POLICY_WORDS):
            return 6.0, "positive", matches[:3]
        return 1.5, "neutral", matches[:3]

    @staticmethod
    def announcement_impact(title: str) -> tuple[float, str]:
        text = title or ""
        has_negative = any(word in text for word in _NEGATIVE_ANNOUNCEMENT_WORDS)
        has_positive = any(word in text for word in _POSITIVE_ANNOUNCEMENT_WORDS)
        if has_negative and not has_positive:
            return -8.0, "negative"
        if has_positive and not has_negative:
            return 7.0, "positive"
        return 0.0, "neutral"

    @staticmethod
    def is_recent(value: object, days: int = 90) -> bool:
        try:
            published = date.fromisoformat(str(value)[:10])
        except (TypeError, ValueError):
            return False
        return 0 <= (shanghai_now().date() - published).days <= days


macro_policy_news_collector = MacroPolicyNewsCollector()
