"""东方财富公开行情采集器。

所有金额保持数据源原始的人民币单位，页面层再负责格式化。主数据源失败时，
只会使用配置明确启用的 FTShare 结构化日线补源，不会制造行情数据。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from config import settings
from services.ftshare_mcp import ftshare_mcp_client


EASTMONEY_UT = "b2884a393a59ad6402e4dd90d24e112f"
# The application uses the same validated security-code path for A-shares and
# the ETF pool.  Keep the exchange mapping explicit so an ETF is never sent to
# the wrong EastMoney market namespace.
SHANGHAI_STOCK_PREFIXES = ("600", "601", "603", "605", "688", "689", "900")
SHENZHEN_STOCK_PREFIXES = ("000", "001", "002", "003", "200", "300", "301", "302")
SHANGHAI_FUND_PREFIXES = ("510", "511", "512", "513", "515", "516", "517", "518", "560", "561", "562", "588")
SHENZHEN_FUND_PREFIXES = ("150", "159", "160", "161", "162", "163", "164", "165", "166", "167", "168", "169")
SHANGHAI_PREFIXES = SHANGHAI_STOCK_PREFIXES + SHANGHAI_FUND_PREFIXES
SHENZHEN_PREFIXES = SHENZHEN_STOCK_PREFIXES + SHENZHEN_FUND_PREFIXES
BEIJING_PREFIXES = ("4", "8", "92")
SCI_TECH_PREFIXES = ("688", "689")
STOCK_CODE_RE = re.compile(r"^(?:(SH|SZ|BJ)[.:-]?)?(\d{6})(?:\.(SH|SZ|BJ))?$")
BOARD_CODE_RE = re.compile(r"^BK\d{4}$")
JSONP_ASSIGNMENT_RE = re.compile(
    r"^\s*[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\s*=\s*(\{.*\})\s*;?\s*$",
    re.DOTALL,
)


def decode_json_or_jsonp(text: str) -> dict:
    """Decode plain JSON or Tencent's assignment-style JSONP response."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = JSONP_ASSIGNMENT_RE.fullmatch(text)
        if not match:
            raise ValueError("upstream response is neither JSON nor supported JSONP")
        payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        raise ValueError("upstream response must be a JSON object")
    return payload


def shanghai_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def is_a_share_market_session(moment: datetime | None = None) -> bool:
    """Return whether a timestamp is inside the regular weekday A-share session."""
    current = moment or shanghai_now()
    if current.weekday() >= 5:
        return False
    minute = current.hour * 60 + current.minute
    return 9 * 60 + 15 <= minute <= 11 * 60 + 30 or 13 * 60 <= minute <= 15 * 60


def as_float(value: object, default: float = 0.0) -> float:
    if value in (None, "", "-"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: object, default: int = 0) -> int:
    return int(as_float(value, float(default)))


def as_optional_float(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_stock_code(value: object) -> str:
    """Return a validated six-digit mainland security code or raise ValueError."""
    candidate = str(value or "").strip().upper()
    if candidate.startswith("BK"):
        raise ValueError("板块编码不能作为股票代码")

    match = STOCK_CODE_RE.fullmatch(candidate)
    if not match:
        raise ValueError("股票代码必须是有效的六位数字代码")

    prefix_exchange, code, suffix_exchange = match.groups()
    if prefix_exchange and suffix_exchange and prefix_exchange != suffix_exchange:
        raise ValueError("股票代码的交易所前缀和后缀不一致")

    if code.startswith(SHANGHAI_PREFIXES):
        expected_exchange = "SH"
    elif code.startswith(SHENZHEN_PREFIXES):
        expected_exchange = "SZ"
    elif code.startswith(BEIJING_PREFIXES):
        expected_exchange = "BJ"
    else:
        raise ValueError(f"不支持的股票代码前缀: {code}")

    declared_exchange = prefix_exchange or suffix_exchange
    if declared_exchange and declared_exchange != expected_exchange:
        raise ValueError(f"股票代码与交易所不匹配: {code} 应为 {expected_exchange}")
    return code


def stock_secid(value: object) -> str:
    """Map a validated stock code to EastMoney's ``market.code`` identifier."""
    code = normalize_stock_code(value)
    market = "1" if code.startswith(SHANGHAI_PREFIXES) else "0"
    return f"{market}.{code}"


def normalize_board_code(value: object) -> str:
    code = str(value or "").strip().upper()
    if not BOARD_CODE_RE.fullmatch(code):
        raise ValueError("板块编码必须是 BK 加四位数字")
    return code


class EastMoneyDataCollector:
    BASE_URL = "https://push2.eastmoney.com/api/qt/clist/get"
    HISTORY_BASE_URL = "https://push2his.eastmoney.com"
    DELAY_BASE_URL = "https://push2delay.eastmoney.com"
    DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    TENCENT_COMPLETE_KLINE_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
    TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    TENCENT_MINUTE_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
    TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
    MAX_LIST_PAGE_SIZE = 100
    PAGE_FETCH_CONCURRENCY = 8
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://data.eastmoney.com/",
        "Accept": "application/json,text/plain,*/*",
    }
    TENCENT_HEADERS = {
        "User-Agent": HEADERS["User-Agent"],
        "Referer": "https://gu.qq.com/",
        "Accept": HEADERS["Accept"],
    }
    STOCK_SCREENER_FILTER = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
    STOCK_SCREENER_FIELDS = (
        "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,"
        "f20,f21,f23,f37,f62,f66,f69,f72,f75,f100,f124,f184"
    )
    SECTOR_COUNTS_CACHE_SECONDS = 3600
    BLOCK_TRADE_CACHE_SECONDS = 60

    def __init__(self):
        self._sector_counts_cache: tuple[float, dict[str, int]] | None = None
        self._sector_counts_lock = asyncio.Lock()
        self._block_trade_cache: dict[tuple[int, int], tuple[float, list[dict]]] = {}
        self._block_trade_cache_lock = asyncio.Lock()

    @staticmethod
    def _request_timeout() -> float:
        """Keep a stale deployment setting from holding an API request open."""
        return min(max(float(settings.data_proxy_timeout), 1.0), 20.0)

    FLOW_FIELD_MAP = {
        "f2": "close_price",
        "f3": "change_pct",
        "f4": "change_amount",
        "f12": "code",
        "f14": "name",
        "f62": "main_net_inflow",
        "f184": "main_net_inflow_pct",
        "f66": "super_large_net_inflow",
        "f69": "super_large_net_inflow_pct",
        "f72": "large_net_inflow",
        "f75": "large_net_inflow_pct",
        "f78": "medium_net_inflow",
        "f81": "medium_net_inflow_pct",
        "f84": "small_net_inflow",
        "f104": "up_count",
        "f105": "down_count",
        "f106": "flat_count",
        "f128": "leading_stock",
        "f140": "leading_stock_code",
        "f136": "leading_stock_change_pct",
    }

    async def fetch_json(self, url: str, params: dict, headers: dict | None = None) -> dict:
        """Fetch market JSON through the configured China data proxy.

        A direct fallback from the overseas application instance adds a second
        long timeout and is not a reliable way to reach mainland market APIs.
        The proxy has its own upstream failover, so when it is configured it is
        the single source of truth for external market requests.
        """
        request_headers = headers or self.HEADERS
        if settings.data_proxy_base_url:
            return await self._fetch_via_proxy(url, params, request_headers)
        return await self._fetch_direct(url, params, request_headers)

    async def _fetch_direct(self, url: str, params: dict, headers: dict) -> dict:
        parsed = urlparse(url)
        candidates = [url]
        if parsed.hostname in {"push2.eastmoney.com", "push2his.eastmoney.com"}:
            candidates.append(parsed._replace(netloc="push2delay.eastmoney.com").geturl())

        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=self._request_timeout()) as client:
            for candidate in dict.fromkeys(candidates):
                try:
                    response = await client.get(candidate, params=params, headers=headers)
                    response.raise_for_status()
                    payload = decode_json_or_jsonp(response.text)
                    if isinstance(payload, dict) and str(payload.get("rc", "0")) == "0":
                        return payload
                    raise RuntimeError(f"EastMoney returned rc={payload.get('rc') if isinstance(payload, dict) else 'invalid'}")
                except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                    last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("No EastMoney direct candidate was attempted")

    async def _fetch_via_proxy(self, url: str, params: dict, headers: dict) -> dict:
        proxy_headers: dict[str, str] = {}
        if settings.data_proxy_token:
            proxy_headers["X-Data-Proxy-Token"] = settings.data_proxy_token

        payload = {
            "url": url,
            "params": params,
            "headers": {
                "User-Agent": headers.get("User-Agent", self.HEADERS["User-Agent"]),
                "Referer": headers.get("Referer", self.HEADERS["Referer"]),
                "Accept": headers.get("Accept", self.HEADERS["Accept"]),
            },
        }
        proxy_url = f"{settings.data_proxy_base_url.rstrip('/')}/fetch"
        async with httpx.AsyncClient(timeout=self._request_timeout()) as client:
            response = await client.post(proxy_url, json=payload, headers=proxy_headers)
            response.raise_for_status()
            return response.json()

    async def check_data_source(self) -> dict:
        params = {
            "pn": "1", "pz": "1", "po": "0", "np": "1", "fid": "f62",
            "fs": "m:90+t:3", "fields": "f12,f14,f62", "fltt": "2", "ut": EASTMONEY_UT,
        }
        source = "proxy" if settings.data_proxy_base_url else "direct"
        started_at = time.monotonic()
        data = await self.fetch_json(self.BASE_URL, params)
        records = (data.get("data") or {}).get("diff") or []
        if not records:
            raise RuntimeError("行情源未返回有效板块记录")
        return {
            "status": "ok",
            "source": source,
            "records": len(records),
            "latency_ms": round((time.monotonic() - started_at) * 1000),
        }

    def _map_flow_row(self, item: dict) -> dict:
        record = {}
        for source_key, target_key in self.FLOW_FIELD_MAP.items():
            value = item.get(source_key)
            record[target_key] = 0 if value in (None, "-") else value
        return record

    async def _fetch_board_flow(
        self,
        board_filter: str,
        sort_field: str,
        sort_order: int,
        page: int,
        page_size: int,
    ) -> list[dict]:
        params = {
            # Public callers use 0 for descending and 1 for ascending. The
            # EastMoney endpoint uses the inverse ``po`` convention.
            "pn": str(page),
            "pz": str(page_size),
            "po": "1" if sort_order == 0 else "0",
            "np": "1",
            "fid": sort_field, "fs": board_filter,
            "fields": ",".join(self.FLOW_FIELD_MAP), "fltt": "2", "ut": EASTMONEY_UT,
        }
        try:
            data = await self.fetch_json(self.BASE_URL, params)
        except Exception as exc:
            print(f"Error fetching board flow: {type(exc).__name__}")
            return []
        return [self._map_flow_row(item) for item in ((data.get("data") or {}).get("diff") or [])]

    async def _fetch_all_board_flow(self, board_filter: str) -> list[dict]:
        """Fetch every board page because EastMoney caps a page at 100 rows."""
        page_size = self.MAX_LIST_PAGE_SIZE

        async def fetch_page(page: int) -> tuple[list[dict], int]:
            params = {
                "pn": str(page), "pz": str(page_size), "po": "0", "np": "1",
                # A live money-flow ranking can reorder between page requests,
                # creating duplicate and missing boards. Codes are stable.
                "fid": "f12", "fs": board_filter,
                "fields": ",".join(self.FLOW_FIELD_MAP), "fltt": "2", "ut": EASTMONEY_UT,
            }
            data = await self.fetch_json(self.BASE_URL, params)
            payload = data.get("data") or {}
            return [self._map_flow_row(item) for item in (payload.get("diff") or [])], as_int(payload.get("total"))

        first_page, total = await fetch_page(1)
        if not first_page:
            raise RuntimeError(f"板块清单为空: {board_filter}")

        pages = max(1, (total + page_size - 1) // page_size)
        rows = list(first_page)
        for start in range(2, pages + 1, self.PAGE_FETCH_CONCURRENCY):
            page_numbers = range(start, min(start + self.PAGE_FETCH_CONCURRENCY, pages + 1))
            responses = await asyncio.gather(*(fetch_page(page) for page in page_numbers))
            for page_rows, _ in responses:
                rows.extend(page_rows)

        by_code = {str(row.get("code") or ""): row for row in rows if row.get("code")}
        if total and len(by_code) < total:
            raise RuntimeError(f"板块清单不完整: expected={total}, received={len(by_code)}")
        return list(by_code.values())

    async def fetch_concept_flow(
        self, sort_field: str = "f62", sort_order: int = 0, page: int = 1, page_size: int = 100
    ) -> list[dict]:
        return await self._fetch_board_flow("m:90+t:3", sort_field, sort_order, page, page_size)

    async def fetch_industry_flow(
        self, sort_field: str = "f62", sort_order: int = 0, page: int = 1, page_size: int = 100
    ) -> list[dict]:
        return await self._fetch_board_flow("m:90+t:2", sort_field, sort_order, page, page_size)

    async def fetch_all_concept_flow(self) -> list[dict]:
        return await self._fetch_all_board_flow("m:90+t:3")

    async def fetch_all_industry_flow(self) -> list[dict]:
        return await self._fetch_all_board_flow("m:90+t:2")

    async def fetch_market_summary(self) -> dict:
        url = f"{self.HISTORY_BASE_URL}/api/qt/stock/fflow/daykline/get"
        result: dict[str, dict] = {}
        for secid, name in (("1.000001", "上证指数"), ("0.399001", "深证成指")):
            params = {
                "lmt": "5", "klt": "101", "secid": secid,
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                "ut": EASTMONEY_UT,
            }
            try:
                data = await self.fetch_json(url, params)
                lines = (data.get("data") or {}).get("klines") or []
                if not lines:
                    continue
                values = lines[-1].split(",")
                if len(values) < 6:
                    continue
                result[name] = {
                    "date": values[0],
                    "main_net_inflow": as_int(values[1]),
                    "small_net_inflow": as_int(values[2]),
                    "medium_net_inflow": as_int(values[3]),
                    "large_net_inflow": as_int(values[4]),
                    "super_large_net_inflow": as_int(values[5]),
                }
            except Exception as exc:
                print(f"Error fetching market summary for {name}: {type(exc).__name__}")
        return result

    async def fetch_north_fund_flow(self) -> dict:
        history = await self.fetch_north_bound_daily(days=1)
        return history[-1] if history else {}

    async def fetch_stock_fund_flow(self, stock_code: str) -> list[dict]:
        try:
            secid = stock_secid(stock_code)
        except ValueError:
            raise
        url = f"{self.HISTORY_BASE_URL}/api/qt/stock/fflow/daykline/get"
        params = {
            "lmt": "260", "klt": "101", "secid": secid,
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "ut": EASTMONEY_UT,
        }
        try:
            data = await self.fetch_json(url, params)
        except Exception as exc:
            print(f"Error fetching stock flow for {stock_code}: {type(exc).__name__}")
            return []
        results = []
        for line in ((data.get("data") or {}).get("klines") or []):
            values = line.split(",")
            if len(values) < 13:
                continue
            results.append({
                "date": values[0],
                "main_net_inflow": as_int(values[1]),
                "small_net_inflow": as_int(values[2]),
                "medium_net_inflow": as_int(values[3]),
                "large_net_inflow": as_int(values[4]),
                "super_large_net_inflow": as_int(values[5]),
                "close_price": as_float(values[11]),
                "change_pct": as_float(values[12]),
            })
        return results

    def _pool_item(self, item: dict, direction: str) -> dict:
        limit_stats = item.get("zttj") if isinstance(item.get("zttj"), dict) else {}
        streak_height = item.get("lbc") if direction == "up" else None
        # In the Topic ZT/DT pool ``fund`` is the amount resting on the limit
        # order book (封单资金), not an intraday main-fund net inflow.  Keeping
        # the semantic explicit prevents a sealed order from being presented as
        # a completed capital-flow transaction.
        seal_amount = as_int(item.get("fund")) if direction in {"up", "down"} else None
        return {
            "code": str(item.get("c", "")),
            "name": item.get("n", ""),
            # 涨跌停池的 p 字段采用千分位（例如 3200 表示 3.200 元）。
            "price": as_float(item.get("p")) / 1000,
            "change_pct": as_float(item.get("zdp")),
            # 该接口不提供成交量，不能把成交额重复写入成交量字段。
            "volume": None,
            "amount": as_int(item.get("amount")),
            "turnover": as_float(item.get("hs")),
            "pe": as_float(item.get("pe")),
            "market_cap": as_int(item.get("ltsz")),
            "continuous_days": as_int(streak_height),
            "limit_days_in_window": as_int(limit_stats.get("days")),
            "limit_count_in_window": as_int(limit_stats.get("ct")),
            "failed_attempts": as_int(item.get("zbc")),
            "sector": item.get("hybk", ""),
            "seal_amount": seal_amount,
            "seal_amount_source_field": "fund" if seal_amount is not None else None,
            "first_limit_time": item.get("fbt"),
            "last_limit_time": item.get("lbt"),
            "limit_direction": direction,
        }

    async def _fetch_limit_pool(
        self,
        endpoint: str,
        direction: str,
        page: int,
        page_size: int,
        target_date: date | str | None = None,
    ) -> dict:
        requested_date = (
            target_date.strftime("%Y%m%d")
            if isinstance(target_date, date)
            else str(target_date or shanghai_now().strftime("%Y%m%d")).replace("-", "")[:8]
        )
        params = {
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "dpt": "wz.ztzt",
            "Pageindex": str(max(page - 1, 0)),
            "pagesize": str(page_size),
            "sort": "fbt:asc" if direction == "up" else "fund:asc",
            "date": requested_date,
        }
        try:
            data = await self.fetch_json(endpoint, params)
        except Exception as exc:
            print(f"Error fetching {direction} limit pool: {type(exc).__name__}")
            return {"stocks": [], "total": 0, "trade_date": None}
        payload = data.get("data") or {}
        return {
            "stocks": [self._pool_item(item, direction) for item in (payload.get("pool") or [])],
            "total": as_int(payload.get("tc")),
            "trade_date": str(payload.get("qdate") or "") or None,
        }

    async def fetch_limit_up_pool(
        self,
        page: int = 1,
        page_size: int = 200,
        target_date: date | str | None = None,
    ) -> dict:
        return await self._fetch_limit_pool(
            "https://push2ex.eastmoney.com/getTopicZTPool", "up", page, page_size, target_date,
        )

    async def fetch_limit_down_pool(
        self,
        page: int = 1,
        page_size: int = 200,
        target_date: date | str | None = None,
    ) -> dict:
        return await self._fetch_limit_pool(
            "https://push2ex.eastmoney.com/getTopicDTPool", "down", page, page_size, target_date,
        )

    async def fetch_failed_limit_pool(
        self,
        page: int = 1,
        page_size: int = 200,
        target_date: date | str | None = None,
    ) -> dict:
        return await self._fetch_limit_pool(
            "https://push2ex.eastmoney.com/getTopicZBPool", "failed", page, page_size, target_date,
        )

    async def fetch_limit_up_stocks(self, page: int = 1, page_size: int = 200) -> list[dict]:
        return (await self.fetch_limit_up_pool(page, page_size))["stocks"]

    async def fetch_limit_down_stocks(self, page: int = 1, page_size: int = 200) -> list[dict]:
        return (await self.fetch_limit_down_pool(page, page_size))["stocks"]

    async def fetch_board_stocks(
        self,
        board_code: str,
        page: int = 1,
        page_size: int = 100,
        sort_field: str = "f62",
    ) -> dict:
        try:
            code = normalize_board_code(board_code)
        except ValueError as exc:
            return {"total": 0, "stocks": [], "error": str(exc)}
        if sort_field not in {"f12", "f62"}:
            raise ValueError("板块成分股仅支持按股票代码或主力资金排序")
        params = {
            "pn": str(page), "pz": str(page_size), "po": "0" if sort_field == "f12" else "1",
            "np": "1", "fltt": "2", "invt": "2",
            "fid": sort_field, "fs": f"b:{code}",
            "fields": f"{self.STOCK_SCREENER_FIELDS},f21",
            "ut": EASTMONEY_UT,
        }
        try:
            data = await self.fetch_json(self.BASE_URL, params)
        except Exception as exc:
            return {"total": 0, "stocks": [], "error": type(exc).__name__}
        payload = data.get("data") or {}
        stocks = []
        for item in payload.get("diff") or []:
            candidate = str(item.get("f12", ""))
            try:
                stock_code = normalize_stock_code(candidate)
            except ValueError:
                continue
            stocks.append({
                "code": stock_code,
                "name": item.get("f14", ""),
                "price": as_float(item.get("f2")),
                "change_pct": as_float(item.get("f3")),
                "change_amount": as_optional_float(item.get("f4")),
                "volume": self._eastmoney_volume_in_shares(item.get("f5")),
                "amount": as_int(item.get("f6")),
                "amplitude": as_optional_float(item.get("f7")),
                "turnover": as_float(item.get("f8")),
                "pe": item.get("f9") if item.get("f9") not in (None, "-") else "",
                "pb": item.get("f23") if item.get("f23") not in (None, "-") else "",
                "roe": item.get("f37") if item.get("f37") not in (None, "-") else "",
                "market_cap": as_int(item.get("f20")),
                "total_market_cap": as_int(item.get("f20")),
                "float_market_cap": as_int(item.get("f21")),
                "volume_ratio": as_optional_float(item.get("f10")),
                "main_net_inflow": as_int(item.get("f62")),
                "main_net_inflow_pct": as_float(item.get("f184")),
                "open": as_optional_float(item.get("f17")),
                "high": as_optional_float(item.get("f15")),
                "low": as_optional_float(item.get("f16")),
                "previous_close": as_optional_float(item.get("f18")),
                "quote_timestamp": as_int(item.get("f124")) or None,
            })
        return {
            "total": as_int(payload.get("total")), "stocks": stocks,
            "page": page, "page_size": page_size, "board_code": code,
        }

    async def fetch_all_board_stocks(self, board_code: str, sector_name: str = "") -> dict:
        """Fetch every verified, tradable constituent of one industry board."""
        code = normalize_board_code(board_code)
        page_size = self.MAX_LIST_PAGE_SIZE
        first_page = await self.fetch_board_stocks(
            code, page=1, page_size=page_size, sort_field="f12",
        )
        if first_page.get("error"):
            return {
                "total": 0,
                "tradable_total": 0,
                "stocks": [],
                "board_code": code,
                "complete": False,
                "error": first_page["error"],
                "source": "eastmoney",
            }

        upstream_total = as_int(first_page.get("total"))
        pages = max(1, (upstream_total + page_size - 1) // page_size)
        page_results = [first_page]
        for start in range(2, pages + 1, self.PAGE_FETCH_CONCURRENCY):
            page_numbers = range(start, min(start + self.PAGE_FETCH_CONCURRENCY, pages + 1))
            page_results.extend(await asyncio.gather(*(
                self.fetch_board_stocks(
                    code, page=page, page_size=page_size, sort_field="f12",
                )
                for page in page_numbers
            )))

        by_code: dict[str, dict] = {}
        complete = True
        for result in page_results:
            if result.get("error"):
                complete = False
                continue
            for stock in result.get("stocks") or []:
                stock_code = str(stock.get("code") or "")
                if not stock_code:
                    continue
                by_code[stock_code] = {
                    **stock,
                    "sector": sector_name.strip(),
                    "selection_sources": ["industry_constituent"],
                }

        stocks = list(by_code.values())
        if upstream_total and len(stocks) < upstream_total:
            complete = False
        return {
            "total": upstream_total or len(stocks),
            "tradable_total": len(stocks),
            "stocks": stocks,
            "board_code": code,
            "complete": complete,
            "source": "eastmoney",
            **self._quote_snapshot_metadata(stocks),
        }

    async def fetch_board_flow_history(self, board_code: str, days: int = 365) -> dict:
        code = normalize_board_code(board_code)
        url = f"{self.HISTORY_BASE_URL}/api/qt/stock/fflow/daykline/get"
        params = {
            "lmt": str(min(max(days + 20, 1), 1000)), "klt": "101", "secid": f"90.{code}",
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "ut": EASTMONEY_UT,
        }
        data = await self.fetch_json(url, params)
        payload = data.get("data") or {}
        history = []
        for line in payload.get("klines") or []:
            values = line.split(",")
            if len(values) < 13:
                continue
            history.append({
                "trade_date": values[0],
                "main_net_inflow": as_int(values[1]),
                "small_net_inflow": as_int(values[2]),
                "medium_net_inflow": as_int(values[3]),
                "large_net_inflow": as_int(values[4]),
                "super_large_net_inflow": as_int(values[5]),
                "main_net_inflow_pct": as_float(values[6]),
                "close_price": as_float(values[11]),
                "change_pct": as_float(values[12]),
            })
        return {
            "code": code,
            "name": payload.get("name", ""),
            "history": self._history_in_window(history, days),
        }

    async def fetch_stock_price_history(self, stock_code: str, days: int = 365) -> dict:
        """Fetch forward-adjusted daily bars from Tencent's public market API.

        EastMoney's historical endpoint is often unavailable from overseas
        service regions, even while its realtime endpoints remain reachable.
        Tencent supplies daily OHLCV coverage for Shanghai, Shenzhen, and
        Beijing-listed shares. If that request fails, an enabled FTShare MCP
        fallback supplies documented daily OHLC data. Fields absent from a
        source remain null.
        """
        code = normalize_stock_code(stock_code)
        symbol = self._tencent_symbol(code)
        count = min(max(days + 20, 30), 800)
        source = ""
        source_error: Exception | None = None
        data: dict = {}
        payload: dict = {}
        series: list = []
        source_candidates = (
            (
                self.TENCENT_COMPLETE_KLINE_URL,
                {"_var": "kline_dayqfq", "param": f"{symbol},day,,,{count},qfq"},
                "tencent_newfqkline_qfq",
            ),
            (
                self.TENCENT_KLINE_URL,
                {"param": f"{symbol},day,,,{count},qfq"},
                "tencent_qfq",
            ),
        )
        for source_url, params, source_name in source_candidates:
            try:
                data = await self.fetch_json(source_url, params, self.TENCENT_HEADERS)
                payload = ((data.get("data") or {}).get(symbol) or {})
                series = payload.get("qfqday") or payload.get("day") or []
                if not series:
                    raise RuntimeError("Tencent returned empty daily history")
                source = source_name
                break
            except Exception as exc:
                source_error = exc
                data = {}
                payload = {}
                series = []
        if not series:
            try:
                fallback = await self._fetch_ftshare_stock_price_history(code, days)
            except Exception as fallback_error:
                print(f"FTShare history fallback failed for {code}: {type(fallback_error).__name__}")
                raise RuntimeError(f"股票历史行情不可用: {code}") from source_error
            if fallback.get("history"):
                return fallback
            raise RuntimeError(f"股票历史行情不可用: {code}") from source_error
        history = []
        previous_close: float | None = None
        for values in series:
            if not isinstance(values, list) or len(values) < 6:
                continue
            raw_date = str(values[0] or "")[:10]
            try:
                trade_date = date.fromisoformat(raw_date)
            except ValueError:
                continue
            open_price = as_optional_float(values[1])
            close_price = as_optional_float(values[2])
            high_price = as_optional_float(values[3])
            low_price = as_optional_float(values[4])
            volume_lots = as_optional_float(values[5])
            turnover = as_optional_float(values[7]) if len(values) > 7 else None
            amount_wan = as_optional_float(values[8]) if len(values) > 8 else None
            change_amount = None
            change_pct = None
            amplitude = None
            if previous_close not in (None, 0) and close_price is not None:
                change_amount = close_price - previous_close
                change_pct = change_amount / previous_close * 100
            if previous_close not in (None, 0) and high_price is not None and low_price is not None:
                amplitude = (high_price - low_price) / previous_close * 100
            history.append({
                "trade_date": trade_date.isoformat(), "open": open_price, "close": close_price,
                "high": high_price, "low": low_price,
                "volume": self._tencent_volume_in_shares(code, volume_lots),
                "amount": int(round(amount_wan * 10_000)) if amount_wan is not None else None,
                "amplitude": amplitude, "change_pct": change_pct,
                "change_amount": change_amount, "turnover": turnover,
            })
            previous_close = close_price
        quote_payload = payload.get("qt") or []
        quote = quote_payload.get(symbol, []) if isinstance(quote_payload, dict) else quote_payload
        name = str(quote[1]) if isinstance(quote, list) and len(quote) > 1 else ""
        return {
            "code": code,
            "name": name,
            "source": source,
            "history": self._history_in_window(history, days),
        }

    async def _fetch_ftshare_stock_price_history(self, stock_code: str, days: int) -> dict:
        """Map FTShare's documented daily OHLC fallback into the cache schema."""
        rows = await ftshare_mcp_client.get_daily_ohlc(stock_code, min(max(days + 20, 30), 500))
        by_date: dict[str, dict] = {}
        for item in rows:
            timestamp = as_optional_float(item.get("ts_millis"))
            close_price = as_optional_float(item.get("close"))
            if timestamp is None or close_price is None or close_price <= 0:
                continue
            try:
                trade_date = datetime.fromtimestamp(timestamp / 1000, tz=ZoneInfo("Asia/Shanghai")).date().isoformat()
            except (OverflowError, OSError, ValueError):
                continue
            volume = as_optional_float(item.get("volume"))
            amount = as_optional_float(item.get("turnover"))
            by_date[trade_date] = {
                "trade_date": trade_date,
                "open": as_optional_float(item.get("open")),
                "close": close_price,
                "high": as_optional_float(item.get("high")),
                "low": as_optional_float(item.get("low")),
                "volume": int(volume) if volume is not None else None,
                "amount": int(amount) if amount is not None else None,
                "amplitude": None,
                "change_pct": None,
                "change_amount": None,
                "turnover": None,
            }

        history = []
        previous_close: float | None = None
        for trade_date in sorted(by_date):
            row = by_date[trade_date]
            close_price = row["close"]
            high_price = row["high"]
            low_price = row["low"]
            if previous_close not in (None, 0):
                row["change_amount"] = close_price - previous_close
                row["change_pct"] = row["change_amount"] / previous_close * 100
                if high_price is not None and low_price is not None:
                    row["amplitude"] = (high_price - low_price) / previous_close * 100
            previous_close = close_price
            history.append(row)
        return {
            "code": stock_code,
            "name": "",
            "source": "ftshare_mcp",
            "history": self._history_in_window(history, days),
        }

    async def fetch_shanghai_index_history(self, days: int = 365) -> list[dict]:
        """Fetch verified Shanghai Composite daily closes from Tencent."""
        symbol = "sh000001"
        count = min(max(days + 20, 30), 800)
        series: list = []
        last_error: Exception | None = None
        for source_url, params in (
            (
                self.TENCENT_COMPLETE_KLINE_URL,
                {"_var": "kline_dayqfq", "param": f"{symbol},day,,,{count},qfq"},
            ),
            (self.TENCENT_KLINE_URL, {"param": f"{symbol},day,,,{count},qfq"}),
        ):
            try:
                data = await self.fetch_json(source_url, params, self.TENCENT_HEADERS)
                payload = ((data.get("data") or {}).get(symbol) or {})
                series = payload.get("qfqday") or payload.get("day") or []
                if series:
                    break
            except Exception as exc:
                last_error = exc
        if not series and last_error is not None:
            raise RuntimeError("上证指数历史行情不可用") from last_error
        history = []
        for values in series:
            if not isinstance(values, list) or len(values) < 3:
                continue
            try:
                trade_date = date.fromisoformat(str(values[0] or "")[:10])
            except ValueError:
                continue
            close_price = as_optional_float(values[2])
            if close_price is not None:
                history.append({"trade_date": trade_date.isoformat(), "close": close_price})
        return [
            {"date": item["trade_date"], "close": item["close"]}
            for item in self._history_in_window(history, days)
        ]

    async def fetch_security_directory_snapshot(self, *, allow_partial: bool = False) -> dict:
        """Return a retried directory snapshot with explicit completeness metadata."""
        page_size = self.MAX_LIST_PAGE_SIZE

        async def fetch_page_once(page: int) -> tuple[list[dict], int]:
            params = {
                "pn": str(page), "pz": str(page_size), "po": "0", "np": "1", "fid": "f12",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                "fields": "f2,f12,f13,f14,f100", "fltt": "2", "ut": EASTMONEY_UT,
            }
            data = await self.fetch_json(self.BASE_URL, params)
            payload = data.get("data") or {}
            return payload.get("diff") or [], as_int(payload.get("total"))

        async def fetch_page(page: int) -> tuple[list[dict], int]:
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    rows, total = await fetch_page_once(page)
                    if not rows:
                        raise RuntimeError(f"股票清单第 {page} 页为空")
                    return rows, total
                except Exception as exc:
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep(0.3 * (2 ** attempt))
            raise RuntimeError(f"股票清单第 {page} 页重试失败") from last_error

        failed_pages: list[int] = []
        page_errors: dict[str, str] = {}
        try:
            first_page, total = await fetch_page(1)
        except Exception as exc:
            if not allow_partial:
                raise RuntimeError(f"获取全市场股票清单失败: {type(exc).__name__}") from exc
            return {
                "records": [], "total": 0, "complete": False,
                "failed_pages": [1], "errors": {"1": type(exc).__name__},
                "source": "eastmoney_directory",
            }

        pages = max(1, (total + page_size - 1) // page_size)
        rows = list(first_page)
        for start in range(2, pages + 1, self.PAGE_FETCH_CONCURRENCY):
            page_numbers = list(range(start, min(start + self.PAGE_FETCH_CONCURRENCY, pages + 1)))
            responses = await asyncio.gather(
                *(fetch_page(page) for page in page_numbers),
                return_exceptions=True,
            )
            for page, result in zip(page_numbers, responses):
                if isinstance(result, Exception):
                    failed_pages.append(page)
                    page_errors[str(page)] = type(result.__cause__ or result).__name__
                    continue
                page_rows, _ = result
                rows.extend(page_rows)

        records = []
        seen_codes: set[str] = set()
        complete = not failed_pages and (not total or len(rows) >= total)
        if not complete and not allow_partial:
            raise RuntimeError(f"股票清单不完整: expected={total}, received={len(rows)}")
        for item in rows:
            try:
                code = normalize_stock_code(item.get("f12"))
            except ValueError:
                continue
            price = as_optional_float(item.get("f2"))
            if code in seen_codes:
                continue
            seen_codes.add(code)
            records.append({
                "code": code,
                "name": item.get("f14", ""),
                "market": item.get("f13"),
                "sector": str(item.get("f100") or "").strip(),
                # Suspended securities retain a positive last close. Long-
                # inactive and delisted directory records are zero/missing.
                "is_currently_listed": bool(price is not None and price > 0),
                "last_price": price,
            })
        return {
            "records": records,
            "total": total,
            "complete": complete,
            "failed_pages": failed_pages,
            "errors": page_errors,
            "source": "eastmoney_directory",
        }

    async def fetch_security_directory(self) -> list[dict]:
        """Return the complete A-share directory, retaining inactive symbols."""
        snapshot = await self.fetch_security_directory_snapshot(allow_partial=False)
        return list(snapshot["records"])

    async def fetch_stock_universe(self) -> list[dict]:
        directory = await self.fetch_security_directory()
        return [
            {
                "code": item["code"],
                "name": item["name"],
                "market": item["market"],
                "sector": item["sector"],
            }
            for item in directory
            if item.get("is_currently_listed")
        ]

    async def fetch_north_bound_daily(self, days: int = 365) -> list[dict]:
        params = {
            "reportName": "RPT_MUTUAL_DEAL_HISTORY",
            "columns": "MUTUAL_TYPE,TRADE_DATE,FUND_INFLOW,NET_DEAL_AMT,DEAL_AMT,BUY_AMT,SELL_AMT,QUOTA_BALANCE,ACCUM_DEAL_AMT",
            "filter": '(MUTUAL_TYPE="005")', "pageNumber": "1", "pageSize": str(min(max(days + 20, 1), 1000)),
            "sortTypes": "-1", "sortColumns": "TRADE_DATE", "source": "WEB", "client": "WEB",
        }
        try:
            data = await self.fetch_json(self.DATACENTER_URL, params)
        except Exception as exc:
            print(f"Error fetching northbound history: {type(exc).__name__}")
            return []
        rows = ((data.get("result") or {}).get("data") or [])
        result = []
        cutoff = self._history_cutoff(days)
        for item in reversed(rows):
            raw_date = str(item.get("TRADE_DATE") or "")
            if not raw_date:
                continue
            try:
                trade_date = date.fromisoformat(raw_date[:10])
            except ValueError:
                continue
            if trade_date < cutoff:
                continue
            net_value = item.get("NET_DEAL_AMT")
            result.append({
                "date": trade_date.isoformat(),
                # 东方财富该字段单位为万元；北向汇总净买入已不再公开时为 null。
                "deal_amount": int(as_float(item.get("DEAL_AMT")) * 10_000),
                "net_inflow": None if net_value is None else int(as_float(net_value) * 10_000),
                "buy_amount": None if item.get("BUY_AMT") is None else int(as_float(item["BUY_AMT"]) * 10_000),
                "sell_amount": None if item.get("SELL_AMT") is None else int(as_float(item["SELL_AMT"]) * 10_000),
                "balance": None if item.get("QUOTA_BALANCE") is None else int(as_float(item["QUOTA_BALANCE"]) * 10_000),
                "source": "eastmoney",
            })
        return result

    @staticmethod
    def _history_cutoff(days: int) -> date:
        return shanghai_now().date() - timedelta(days=max(days, 1))

    @classmethod
    def _history_in_window(cls, history: list[dict], days: int) -> list[dict]:
        cutoff = cls._history_cutoff(days)
        rows = []
        for row in history:
            try:
                if date.fromisoformat(str(row["trade_date"])[:10]) >= cutoff:
                    rows.append(row)
            except (KeyError, TypeError, ValueError):
                continue
        return rows

    @staticmethod
    def _tencent_symbol(code: str) -> str:
        if code.startswith(SHANGHAI_PREFIXES):
            return f"sh{code}"
        if code.startswith(BEIJING_PREFIXES):
            return f"bj{code}"
        return f"sz{code}"

    @staticmethod
    def _tencent_volume_in_shares(code: str, volume: float | None) -> int | None:
        """Normalize Tencent's mixed volume units to shares.

        Main-board, ChiNext, and Beijing listings use lots. Tencent already
        returns STAR Market volumes in shares.
        """
        if volume is None:
            return None
        return int(volume if code.startswith(SCI_TECH_PREFIXES) else volume * 100)

    @staticmethod
    def _eastmoney_volume_in_shares(volume: object) -> int | None:
        """Normalize EastMoney clist volume from lots into individual shares."""
        lots = as_optional_float(volume)
        return int(lots * 100) if lots is not None else None

    async def fetch_market_breadth(self) -> dict:
        """Derive true advance/decline breadth from one complete stock snapshot."""
        from quant.market_cache import load_quant_market_snapshot, save_quant_market_snapshot

        snapshot = await load_quant_market_snapshot()
        if not snapshot.get("stocks"):
            try:
                snapshot = await self.fetch_quant_market_snapshot(include_special=True)
                await save_quant_market_snapshot(snapshot)
            except Exception as exc:
                print(f"Error fetching market breadth snapshot: {type(exc).__name__}")
                return {}
        groups: dict[str, list[dict]] = {"全市场": [], "沪市": [], "深市": [], "北交所": []}
        for stock in snapshot.get("stocks") or []:
            code = str(stock.get("code") or "")
            price = as_optional_float(stock.get("price"))
            change = as_optional_float(stock.get("change_pct"))
            if not code or price is None or price <= 0 or change is None:
                continue
            groups["全市场"].append(stock)
            market = "北交所" if code.startswith(BEIJING_PREFIXES) else "沪市" if code.startswith(SHANGHAI_PREFIXES) else "深市"
            groups[market].append(stock)
        output = {}
        for name, rows in groups.items():
            if not rows:
                continue
            up_count = sum(as_float(item.get("change_pct")) > 0 for item in rows)
            down_count = sum(as_float(item.get("change_pct")) < 0 for item in rows)
            flat_count = len(rows) - up_count - down_count
            directional = up_count + down_count
            output[name] = {
                "up": up_count,
                "down": down_count,
                "flat": flat_count,
                "total": len(rows),
                "ratio": round(up_count / directional * 100, 2) if directional else 50.0,
                "data_date": snapshot.get("data_date"),
                "source": "complete_market_snapshot",
            }
        return output

    async def fetch_market_turnover(self) -> dict:
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": "1.000001", "fields": "f43,f47,f48,f57,f58,f124,f169,f170", "ut": EASTMONEY_UT,
        }
        try:
            data = await self.fetch_json(url, params)
        except Exception as exc:
            print(f"Error fetching market turnover: {type(exc).__name__}")
            return {}
        row = data.get("data") or {}
        if not row:
            return {}
        quote_at = self._quote_timestamp_datetime(row.get("f124"))
        now = shanghai_now()
        quote_age_seconds = (now - quote_at).total_seconds() if quote_at else None
        return {
            "sh_index": round(as_float(row.get("f43")) / 100, 2),
            "sh_change": round(as_float(row.get("f169")) / 100, 2),
            "sh_change_pct": round(as_float(row.get("f170")) / 100, 2),
            "sh_volume": as_int(row.get("f47")),
            "sh_amount": as_int(row.get("f48")),
            "data_date": quote_at.date().isoformat() if quote_at else None,
            "source_updated_at": quote_at.isoformat() if quote_at else None,
            "is_realtime": bool(
                quote_at
                and quote_at.date() == now.date()
                and quote_age_seconds is not None
                and 0 <= quote_age_seconds <= 15 * 60
                and is_a_share_market_session(now)
            ),
            "source": "eastmoney",
        }

    async def fetch_dragon_board(
        self,
        page_size: int = 50,
        target_date: date | str | None = None,
    ) -> list[dict]:
        params = {
            "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW", "columns": "ALL", "pageNumber": "1", "pageSize": str(page_size),
            "sortTypes": "-1,-1", "sortColumns": "TRADE_DATE,BILLBOARD_NET_AMT", "source": "WEB", "client": "WEB",
        }
        if target_date:
            normalized_date = target_date.isoformat() if isinstance(target_date, date) else str(target_date)[:10]
            try:
                normalized_date = date.fromisoformat(normalized_date).isoformat()
            except ValueError as exc:
                raise ValueError("target_date must use YYYY-MM-DD") from exc
            params["filter"] = f"(TRADE_DATE='{normalized_date}')"
        try:
            data = await self.fetch_json(self.DATACENTER_URL, params)
        except Exception as exc:
            print(f"Error fetching dragon board: {type(exc).__name__}")
            return []
        rows = ((data.get("result") or {}).get("data") or [])
        stocks = []
        for item in rows:
            try:
                code = normalize_stock_code(item.get("SECURITY_CODE"))
            except ValueError:
                continue
            explanation = str(item.get("EXPLANATION") or item.get("EXPLAIN") or "").strip()
            institutions = re.search(r"(\d+)家机构", explanation)
            institution_count = as_int(
                item.get("ORG_BUY_COUNT") or item.get("BUY_TIMES") or (
                    institutions.group(1) if institutions else 0
                )
            )
            stocks.append({
                "code": code, "name": item.get("SECURITY_NAME_ABBR", ""),
                "date": str(item.get("TRADE_DATE") or "")[:10], "price": as_float(item.get("CLOSE_PRICE")),
                "change_pct": as_float(item.get("CHANGE_RATE")), "turnover": as_float(item.get("TURNOVERRATE")),
                "amount": as_int(item.get("BILLBOARD_DEAL_AMT")), "main_net_inflow": as_int(item.get("BILLBOARD_NET_AMT")),
                "net_amount": as_int(item.get("BILLBOARD_NET_AMT")),
                "buy_amount": as_int(item.get("BILLBOARD_BUY_AMT")), "sell_amount": as_int(item.get("BILLBOARD_SELL_AMT")),
                "market_cap": as_int(item.get("FREE_MARKET_CAP")), "institution_count": institution_count,
                "institution_buy_amount": as_int(item.get("ORG_BUY_AMT")),
                "institution_sell_amount": as_int(item.get("ORG_SELL_AMT")),
                "institution_net_amount": as_int(item.get("ORG_NET_BUY")),
                "reason": explanation,
            })
        return stocks

    async def fetch_block_trades(self, page: int = 1, page_size: int = 50) -> list[dict]:
        page = max(1, int(page))
        page_size = min(max(1, int(page_size)), self.MAX_LIST_PAGE_SIZE)
        cache_key = (page, page_size)
        cached = self._block_trade_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < self.BLOCK_TRADE_CACHE_SECONDS:
            return [dict(item) for item in cached[1]]

        params = {
            "reportName": "RPT_DATA_BLOCKTRADE", "columns": "ALL", "pageNumber": str(page), "pageSize": str(page_size),
            "sortTypes": "-1,-1", "sortColumns": "TRADE_DATE,DEAL_AMT", "source": "WEB", "client": "WEB",
        }
        async with self._block_trade_cache_lock:
            cached = self._block_trade_cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < self.BLOCK_TRADE_CACHE_SECONDS:
                return [dict(item) for item in cached[1]]
            try:
                data = await self.fetch_json(self.DATACENTER_URL, params)
            except Exception as exc:
                print(f"Error fetching block trades: {type(exc).__name__}")
                return []
            result = []
            for item in ((data.get("result") or {}).get("data") or []):
                try:
                    code = normalize_stock_code(item.get("SECURITY_CODE"))
                except ValueError:
                    continue
                result.append({
                    "code": code, "name": item.get("SECURITY_NAME_ABBR", ""), "date": str(item.get("TRADE_DATE") or "")[:10],
                    "amount": as_int(item.get("DEAL_AMT")), "price": as_float(item.get("DEAL_PRICE")),
                    "premium": round(as_float(item.get("PREMIUM_RATIO")) * 100, 2), "volume": as_int(item.get("DEAL_VOLUME")),
                    "buyer": item.get("BUYER_NAME", ""), "seller": item.get("SELLER_NAME", ""),
                    "change_pct": as_float(item.get("CHANGE_RATE")),
                })
            self._block_trade_cache[cache_key] = (time.monotonic(), result)
            return [dict(item) for item in result]

    async def fetch_sector_rotation(self, lookback_days: int = 5) -> dict:
        sectors = []
        try:
            rows = await self.fetch_all_concept_flow()
        except Exception:
            # Keep the live page usable when a full directory request is
            # throttled; this is explicitly marked partial below.
            rows = await self.fetch_concept_flow(page_size=100)
        for item in rows:
            sectors.append({
                "code": item.get("code", ""), "name": item.get("name", ""),
                "change_pct": as_float(item.get("change_pct")), "main_net_inflow": as_int(item.get("main_net_inflow")),
                "super_large_inflow": as_int(item.get("super_large_net_inflow")), "large_inflow": as_int(item.get("large_net_inflow")),
                "up_count": as_int(item.get("up_count")), "down_count": as_int(item.get("down_count")),
            })
        negative = [item for item in sectors if item["main_net_inflow"] < 0]
        return {
            "sectors": sectors,
            "hot_inflow": sorted(sectors, key=lambda item: item["main_net_inflow"], reverse=True)[:5],
            "hot_outflow": sorted(negative, key=lambda item: item["main_net_inflow"])[:5],
            "hot_gainers": sorted(sectors, key=lambda item: item["change_pct"], reverse=True)[:5],
            "outflow_data_available": bool(negative),
            "lookback_days": lookback_days,
        }

    async def _fetch_screener_rows(self, sort_field: str, page_size: int) -> list[dict]:
        """Fetch one descending, live A-share ranking for a screener workflow."""
        if sort_field not in {"f3", "f8", "f10", "f62"}:
            raise ValueError(f"Unsupported screener sort field: {sort_field}")
        params = {
            "pn": "1", "pz": str(min(max(page_size, 1), 500)),
            # EastMoney uses po=1 for descending rankings. Using po=0 here
            # surfaces inactive, zero-price symbols before tradable quotes.
            "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": sort_field,
            "fs": self.STOCK_SCREENER_FILTER, "fields": self.STOCK_SCREENER_FIELDS,
            "ut": EASTMONEY_UT,
        }
        data = await self.fetch_json(self.BASE_URL, params)
        return (data.get("data") or {}).get("diff") or []

    @staticmethod
    def _quote_timestamp_datetime(value: object) -> datetime | None:
        timestamp = as_int(value)
        if timestamp <= 0:
            return None
        # EastMoney's f124 is seconds today. Accept milliseconds defensively
        # because the same field appears in both formats on some endpoints.
        if timestamp >= 10_000_000_000:
            timestamp //= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz=ZoneInfo("Asia/Shanghai"))
        except (OverflowError, OSError, ValueError):
            return None

    @classmethod
    def _quote_snapshot_metadata(cls, stocks: list[dict]) -> dict:
        quote_times = [
            cls._quote_timestamp_datetime(stock.get("quote_timestamp"))
            for stock in stocks
        ]
        quote_at = max((item for item in quote_times if item is not None), default=None)
        now = shanghai_now()
        quote_age_seconds = (now - quote_at).total_seconds() if quote_at else None
        return {
            "data_date": quote_at.date().isoformat() if quote_at else None,
            "source_updated_at": quote_at.isoformat() if quote_at else None,
            "is_realtime": bool(
                quote_at
                and quote_at.date() == now.date()
                and quote_age_seconds is not None
                and 0 <= quote_age_seconds <= 15 * 60
                and is_a_share_market_session(now)
            ),
        }

    async def fetch_quant_market_snapshot(self, include_special: bool = False) -> dict:
        """Fetch a complete, code-sorted A-share quote snapshot for rule scans.

        Ranking endpoints only return market leaders and therefore cannot be
        used to claim an all-market scan. This method walks every upstream
        page using the immutable stock code as the sort key and rejects a
        partial response instead of presenting it as complete coverage.
        """
        page_size = self.MAX_LIST_PAGE_SIZE
        market_filter = f"{self.STOCK_SCREENER_FILTER},m:0+t:81+s:2048"

        async def fetch_page(page: int) -> tuple[list[dict], int]:
            params = {
                "pn": str(page), "pz": str(page_size), "po": "0", "np": "1",
                "fltt": "2", "invt": "2", "fid": "f12", "fs": market_filter,
                "fields": self.STOCK_SCREENER_FIELDS, "ut": EASTMONEY_UT,
            }
            data = await self.fetch_json(self.BASE_URL, params)
            payload = data.get("data") or {}
            return payload.get("diff") or [], as_int(payload.get("total"))

        try:
            first_rows, upstream_total = await fetch_page(1)
            if not first_rows:
                raise RuntimeError("全市场行情首批为空")
            page_count = max(1, (upstream_total + page_size - 1) // page_size)
            pages: dict[int, list[dict]] = {1: first_rows}
            for start in range(2, page_count + 1, self.PAGE_FETCH_CONCURRENCY):
                page_numbers = list(range(start, min(start + self.PAGE_FETCH_CONCURRENCY, page_count + 1)))
                page_results = await asyncio.gather(*(fetch_page(page) for page in page_numbers))
                for page, (rows, _) in zip(page_numbers, page_results):
                    if not rows:
                        raise RuntimeError(f"全市场行情第 {page} 页为空")
                    pages[page] = rows
        except Exception as exc:
            raise RuntimeError(f"获取量化全市场行情失败: {type(exc).__name__}") from exc

        by_code: dict[str, dict] = {}
        raw_count = 0
        for page in sorted(pages):
            for item in pages[page]:
                raw_count += 1
                stock = self._map_screener_stock(item)
                if stock is None or (
                    not include_special and self._is_special_treatment_stock(stock.get("name"))
                ):
                    continue
                by_code[stock["code"]] = stock

        if upstream_total and raw_count < upstream_total:
            raise RuntimeError(
                f"量化全市场行情不完整: expected={upstream_total}, received={raw_count}"
            )
        stocks = [by_code[code] for code in sorted(by_code)]
        quote_metadata = self._quote_snapshot_metadata(stocks)
        return {
            "stocks": stocks,
            "total": len(stocks),
            "upstream_total": upstream_total,
            "source": "eastmoney",
            **quote_metadata,
            "fetched_at": shanghai_now().isoformat(),
            "complete": True,
        }

    async def fetch_stock_quotes(self, stock_codes: list[str]) -> dict:
        """Fetch a bounded quote set with EastMoney/Tencent source failover.

        EastMoney's batch endpoint is preferred while A-shares are trading.
        Tencent supplies one compact 24-hour batch outside the session and for
        any symbols missing from the primary response.
        """
        codes = list(dict.fromkeys(normalize_stock_code(code) for code in stock_codes))
        by_code: dict[str, dict] = {}
        sources: list[str] = []

        if codes and is_a_share_market_session():
            try:
                for start in range(0, len(codes), 100):
                    batch = codes[start:start + 100]
                    data = await self.fetch_json(
                        "https://push2.eastmoney.com/api/qt/ulist.np/get",
                        {
                            "secids": ",".join(stock_secid(code) for code in batch),
                            "fields": self.STOCK_SCREENER_FIELDS,
                            "fltt": "2",
                            "invt": "2",
                            "ut": EASTMONEY_UT,
                        },
                    )
                    for row in (data.get("data") or {}).get("diff") or []:
                        stock = self._map_screener_stock(row)
                        if stock and stock["code"] in batch:
                            stock["quote_source"] = "eastmoney"
                            by_code[stock["code"]] = stock
                if by_code:
                    sources.append("eastmoney")
            except Exception as exc:
                print(f"EastMoney batch quotes failed: {type(exc).__name__}")

        missing = [code for code in codes if code not in by_code]
        if missing:
            try:
                fallback = await self.fetch_tencent_quotes(missing)
                for stock in fallback.get("stocks") or []:
                    by_code[str(stock["code"])] = stock
                if fallback.get("stocks"):
                    sources.append("tencent")
            except Exception as exc:
                print(f"Tencent batch quotes failed: {type(exc).__name__}")

        stocks = [by_code[code] for code in codes if code in by_code]
        if codes and not stocks:
            raise RuntimeError("持仓股票最新行情不可用")

        quote_metadata = self._quote_snapshot_metadata(stocks)
        return {
            "stocks": stocks,
            "total": len(stocks),
            "source": "+".join(dict.fromkeys(sources)) or "unavailable",
            **quote_metadata,
            "fetched_at": shanghai_now().isoformat(),
            "complete": len(stocks) == len(codes),
        }

    async def fetch_stock_auction_quotes(self, stock_codes: list[str]) -> dict:
        """Return timestamp-verified 09:24-09:27 call-auction observations.

        The quote endpoint is the source of the auction matched price and
        accumulated auction volume.  ``f10``/the Tencent compact field is
        retained as the provider's volume-ratio field; missing provider data
        stays missing instead of being inferred from a stale daily quote.
        """
        codes = list(dict.fromkeys(normalize_stock_code(code) for code in stock_codes))
        if not codes:
            return {
                "stocks": [], "total": 0, "requested": 0, "complete": True,
                "is_realtime": False, "data_date": None, "source": "unavailable",
            }

        payload = await self.fetch_stock_quotes(codes)
        now = shanghai_now()
        rows: list[dict] = []
        for stock in payload.get("stocks") or []:
            quote_at = self._quote_timestamp_datetime(stock.get("quote_timestamp"))
            previous_close = as_optional_float(stock.get("previous_close"))
            auction_price = as_optional_float(stock.get("price"))
            if auction_price is None or auction_price <= 0:
                auction_price = as_optional_float(stock.get("open"))
            auction_volume = as_optional_float(stock.get("volume"))
            auction_ratio = as_optional_float(stock.get("volume_ratio"))
            high_open_pct = (
                (auction_price / previous_close - 1) * 100
                if auction_price is not None and previous_close not in (None, 0)
                else None
            )
            quote_minute = quote_at.hour * 60 + quote_at.minute if quote_at else None
            fresh = bool(
                payload.get("is_realtime")
                and quote_at
                and quote_at.date() == now.date()
                and 9 * 60 + 24 <= quote_minute <= 9 * 60 + 27
                and 0 <= (now - quote_at).total_seconds() <= 5 * 60
            )
            rows.append({
                "code": str(stock.get("code") or ""),
                "name": str(stock.get("name") or ""),
                "auction_price": auction_price,
                "auction_volume": int(auction_volume) if auction_volume is not None else None,
                "auction_volume_ratio": auction_ratio,
                "high_open_pct": high_open_pct,
                "previous_close": previous_close,
                "quote_at": quote_at.isoformat() if quote_at else None,
                "source": str(stock.get("quote_source") or payload.get("source") or "unavailable"),
                "is_realtime": fresh,
            })
        by_code = {item["code"]: item for item in rows if item.get("code")}
        ordered = [by_code[code] for code in codes if code in by_code]
        complete = len(ordered) == len(codes) and all(item["is_realtime"] for item in ordered)
        return {
            "stocks": ordered,
            "total": len(ordered),
            "requested": len(codes),
            "complete": complete,
            "is_realtime": complete,
            "data_date": now.date().isoformat() if ordered else None,
            "source": payload.get("source") or "unavailable",
            "source_updated_at": max(
                (item["quote_at"] for item in ordered if item.get("quote_at")),
                default=None,
            ),
            "field_coverage": {
                "auction_price": sum(item.get("auction_price") is not None for item in ordered),
                "auction_volume": sum(item.get("auction_volume") is not None for item in ordered),
                "auction_volume_ratio": sum(item.get("auction_volume_ratio") is not None for item in ordered),
                "high_open_pct": sum(item.get("high_open_pct") is not None for item in ordered),
            },
            "fetched_at": now.isoformat(),
        }

    async def fetch_tencent_quotes(self, stock_codes: list[str]) -> dict:
        """Fetch and normalize Tencent's compact, batch A-share quote feed."""
        codes = list(dict.fromkeys(normalize_stock_code(code) for code in stock_codes))
        symbols = [self._tencent_symbol(code) for code in codes]
        texts: list[str] = []
        for start in range(0, len(symbols), 200):
            batch = symbols[start:start + 200]
            if settings.data_proxy_base_url:
                headers: dict[str, str] = {}
                if settings.data_proxy_token:
                    headers["X-Data-Proxy-Token"] = settings.data_proxy_token
                async with httpx.AsyncClient(timeout=self._request_timeout()) as client:
                    response = await client.post(
                        f"{settings.data_proxy_base_url.rstrip('/')}/tencent-quotes",
                        json={"symbols": batch},
                        headers=headers,
                    )
                    response.raise_for_status()
                    texts.append(str(response.json().get("text") or ""))
            else:
                async with httpx.AsyncClient(timeout=self._request_timeout()) as client:
                    response = await client.get(
                        self.TENCENT_QUOTE_URL + ",".join(batch),
                        headers=self.TENCENT_HEADERS,
                    )
                    response.raise_for_status()
                    texts.append(response.content.decode("gb18030", errors="replace"))

        stocks = self._parse_tencent_quote_text("\n".join(texts))
        by_code = {str(stock["code"]): stock for stock in stocks}
        ordered = [by_code[code] for code in codes if code in by_code]
        return {
            "stocks": ordered,
            "total": len(ordered),
            "source": "tencent",
            **self._quote_snapshot_metadata(ordered),
            "fetched_at": shanghai_now().isoformat(),
            "complete": len(ordered) == len(codes),
        }

    @classmethod
    def _parse_tencent_quote_text(cls, text: str) -> list[dict]:
        stocks = []
        pattern = re.compile(r'v_(?:sh|sz|bj)(\d{6})="([^"]*)"')
        for match in pattern.finditer(text or ""):
            raw_code, payload = match.groups()
            try:
                code = normalize_stock_code(raw_code)
            except ValueError:
                continue
            values = payload.split("~")
            if len(values) < 47:
                continue
            price = as_optional_float(values[3])
            if price is None or price <= 0:
                continue
            quote_timestamp = None
            try:
                quote_at = datetime.strptime(values[30], "%Y%m%d%H%M%S").replace(
                    tzinfo=ZoneInfo("Asia/Shanghai")
                )
                quote_timestamp = int(quote_at.timestamp())
            except (TypeError, ValueError):
                pass
            raw_volume = as_optional_float(values[36] or values[6])
            amount_wan = as_optional_float(values[57]) if len(values) > 57 else None
            total_cap_yi = as_optional_float(values[44]) if len(values) > 44 else None
            stocks.append({
                "code": code,
                "name": values[1],
                "price": price,
                "change_pct": as_optional_float(values[32]),
                "change_amount": as_optional_float(values[31]),
                "previous_close": as_optional_float(values[4]),
                "open": as_optional_float(values[5]),
                "high": as_optional_float(values[33]),
                "low": as_optional_float(values[34]),
                "volume": cls._tencent_volume_in_shares(code, raw_volume),
                "amount": int(amount_wan * 10_000) if amount_wan is not None else None,
                "turnover": as_optional_float(values[38]),
                "volume_ratio": as_optional_float(values[49]) if len(values) > 49 else None,
                "pe": as_optional_float(values[39]),
                "pb": as_optional_float(values[46]),
                "market_cap": int(total_cap_yi * 1e8) if total_cap_yi is not None else None,
                "sector": "",
                "quote_timestamp": quote_timestamp,
                "quote_source": "tencent",
            })
        return stocks

    async def fetch_stock_minute_trends(self, stock_code: str, days: int = 1) -> dict:
        """Fetch source-native one-minute bars used by live intraday decisions."""
        code = normalize_stock_code(stock_code)
        requested_days = min(max(int(days), 1), 5)
        data = await self.fetch_json(
            f"{self.HISTORY_BASE_URL}/api/qt/stock/trends2/get",
            {
                "secid": stock_secid(code),
                "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                "ndays": str(requested_days),
                "iscr": "0",
                "iscca": "0",
                "ut": EASTMONEY_UT,
            },
        )
        payload = data.get("data") or {}
        bars = []
        for line in payload.get("trends") or []:
            values = str(line).split(",")
            if len(values) < 8:
                continue
            try:
                bar_time = datetime.fromisoformat(values[0])
            except ValueError:
                continue
            bars.append({
                "stock_code": code,
                "stock_name": str(payload.get("name") or ""),
                "bar_time": bar_time.isoformat(timespec="minutes"),
                "interval_minutes": 1,
                "open": as_optional_float(values[1]),
                "close": as_optional_float(values[2]),
                "high": as_optional_float(values[3]),
                "low": as_optional_float(values[4]),
                "volume": self._eastmoney_volume_in_shares(values[5]),
                "amount": as_int(values[6]),
                "average": as_optional_float(values[7]),
            })
        bars.sort(key=lambda item: item["bar_time"])
        now = shanghai_now()
        latest_text = bars[-1]["bar_time"] if bars else None
        latest = datetime.fromisoformat(latest_text).replace(tzinfo=now.tzinfo) if latest_text else None
        age_seconds = (now - latest).total_seconds() if latest else None
        return {
            "stock_code": code,
            "stock_name": str(payload.get("name") or ""),
            "pre_close": as_optional_float(payload.get("preClose")),
            "bars": bars,
            "bar_count": len(bars),
            "source": "eastmoney",
            "data_date": latest.date().isoformat() if latest else None,
            "latest_bar_at": latest_text,
            "is_realtime": bool(
                latest
                and latest.date() == now.date()
                and age_seconds is not None
                and 0 <= age_seconds <= 10 * 60
                and is_a_share_market_session(now)
            ),
            "complete": bool(bars),
            "fetched_at": now.isoformat(),
        }

    async def fetch_stock_trade_details(self, stock_code: str, limit: int = 500) -> dict:
        """Aggregate recent source-labelled buy/sell prints for intraday audit.

        EastMoney's ``f55`` side flag is 2 for active buy, 1 for active sell,
        and any other value for neutral.  The public endpoint exposes a bounded
        recent window rather than a guaranteed full tape, so completeness is
        deliberately kept false and the returned sample size remains visible.
        """
        code = normalize_stock_code(stock_code)
        bounded = min(max(int(limit), 1), 2000)
        data = await self.fetch_json(
            "https://push2.eastmoney.com/api/qt/stock/details/get",
            {
                "secid": stock_secid(code),
                "fltt": "2",
                "fields1": "f1,f2,f3,f4",
                "fields2": "f51,f52,f53,f54,f55",
                "pos": str(-bounded),
                "iscr": "0",
                "ut": EASTMONEY_UT,
            },
        )
        payload = data.get("data") or {}
        details = []
        buy_amount = 0
        sell_amount = 0
        neutral_amount = 0
        for line in payload.get("details") or []:
            values = str(line).split(",")
            if len(values) < 5:
                continue
            price = as_optional_float(values[1])
            volume = self._eastmoney_volume_in_shares(values[2])
            if price is None or price <= 0 or volume is None or volume < 0:
                continue
            amount = int(round(price * volume))
            side_flag = str(values[4]).strip()
            side = "buy" if side_flag == "2" else "sell" if side_flag == "1" else "neutral"
            if side == "buy":
                buy_amount += amount
            elif side == "sell":
                sell_amount += amount
            else:
                neutral_amount += amount
            details.append({
                "time": values[0],
                "price": price,
                "volume": volume,
                "amount": amount,
                "side": side,
            })
        directional = buy_amount + sell_amount
        active_net = buy_amount - sell_amount if directional else None
        buy_ratio = buy_amount / directional * 100 if directional else None
        if active_net is None:
            direction = None
        elif abs(active_net) <= directional * 0.05:
            direction = "balanced"
        else:
            direction = "buy" if active_net > 0 else "sell"
        return {
            "stock_code": code,
            "pre_close": as_optional_float(payload.get("prePrice")),
            "details": details,
            "detail_count": len(details),
            "latest_trade_time": details[-1]["time"] if details else None,
            "active_buy_amount": buy_amount if details else None,
            "active_sell_amount": sell_amount if details else None,
            "neutral_amount": neutral_amount if details else None,
            "active_net_amount": active_net,
            "active_buy_ratio": round(buy_ratio, 2) if buy_ratio is not None else None,
            "active_direction": direction,
            "complete": False,
            "sample_limit": bounded,
            "source": "eastmoney_trade_details",
            "method": "f55主动买卖标志；金额按成交价×成交股数汇总",
            "warning": "公开源仅提供最近成交明细窗口，不代表全天逐笔完整成交",
            "fetched_at": shanghai_now().isoformat(),
        }

    async def fetch_shanghai_index_minute_trends(self, days: int = 1) -> dict:
        """Fetch Shanghai Composite one-minute bars for relative-strength checks."""
        requested_days = min(max(int(days), 1), 5)
        data = await self.fetch_json(
            f"{self.HISTORY_BASE_URL}/api/qt/stock/trends2/get",
            {
                "secid": "1.000001",
                "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                "ndays": str(requested_days),
                "iscr": "0",
                "iscca": "0",
                "ut": EASTMONEY_UT,
            },
        )
        payload = data.get("data") or {}
        bars = []
        for line in payload.get("trends") or []:
            values = str(line).split(",")
            if len(values) < 8:
                continue
            try:
                bar_time = datetime.fromisoformat(values[0])
            except ValueError:
                continue
            bars.append({
                "stock_code": "SH000001",
                "stock_name": "上证指数",
                "bar_time": bar_time.isoformat(timespec="minutes"),
                "interval_minutes": 1,
                "open": as_optional_float(values[1]),
                "close": as_optional_float(values[2]),
                "high": as_optional_float(values[3]),
                "low": as_optional_float(values[4]),
                "volume": as_int(values[5]),
                "amount": as_int(values[6]),
                "average": as_optional_float(values[7]),
            })
        bars.sort(key=lambda item: item["bar_time"])
        now = shanghai_now()
        latest_text = bars[-1]["bar_time"] if bars else None
        latest = datetime.fromisoformat(latest_text).replace(tzinfo=now.tzinfo) if latest_text else None
        age_seconds = (now - latest).total_seconds() if latest else None
        return {
            "stock_code": "SH000001",
            "stock_name": "上证指数",
            "pre_close": as_optional_float(payload.get("preClose")),
            "bars": bars,
            "bar_count": len(bars),
            "source": "eastmoney",
            "data_date": latest.date().isoformat() if latest else None,
            "latest_bar_at": latest_text,
            "is_realtime": bool(
                latest
                and latest.date() == now.date()
                and age_seconds is not None
                and 0 <= age_seconds <= 10 * 60
                and is_a_share_market_session(now)
            ),
            "complete": bool(bars),
            "fetched_at": now.isoformat(),
        }

    async def fetch_stock_minute_history(
        self,
        stock_code: str,
        *,
        interval_minutes: int = 5,
        limit: int = 1536,
    ) -> dict:
        """Fetch the provider's available minute window without claiming full history."""
        code = normalize_stock_code(stock_code)
        interval = int(interval_minutes)
        if interval not in {1, 5, 15, 30, 60}:
            raise ValueError("分钟周期仅支持 1、5、15、30、60")
        requested_limit = min(max(int(limit), 1), 1536)
        try:
            data = await self.fetch_json(
                f"{self.HISTORY_BASE_URL}/api/qt/stock/kline/get",
                {
                    "secid": stock_secid(code),
                    "klt": str(interval),
                    "fqt": "1",
                    "lmt": str(requested_limit),
                    "end": "20500101",
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "ut": EASTMONEY_UT,
                },
            )
        except Exception:
            data = {}
        payload = data.get("data") or {}
        bars = []
        for line in payload.get("klines") or []:
            values = str(line).split(",")
            if len(values) < 7:
                continue
            try:
                bar_time = datetime.fromisoformat(values[0])
            except ValueError:
                continue
            bars.append({
                "stock_code": code,
                "stock_name": str(payload.get("name") or ""),
                "bar_time": bar_time.isoformat(timespec="minutes"),
                "interval_minutes": interval,
                "open": as_optional_float(values[1]),
                "close": as_optional_float(values[2]),
                "high": as_optional_float(values[3]),
                "low": as_optional_float(values[4]),
                "volume": self._eastmoney_volume_in_shares(values[5]),
                "amount": as_int(values[6]),
                "average": None,
            })
        bars.sort(key=lambda item: item["bar_time"])
        if not bars:
            return await self._fetch_tencent_minute_history(code, interval, requested_limit)
        upstream_total = as_int(payload.get("dktotal"))
        return {
            "stock_code": code,
            "stock_name": str(payload.get("name") or ""),
            "bars": bars,
            "bar_count": len(bars),
            "upstream_total": upstream_total,
            "coverage_start": bars[0]["bar_time"] if bars else None,
            "coverage_end": bars[-1]["bar_time"] if bars else None,
            "source": "eastmoney",
            "complete_history": bool(bars) and (not upstream_total or len(bars) >= upstream_total),
            "warning": (
                None
                if bars and (not upstream_total or len(bars) >= upstream_total)
                else "公开源只返回有限分钟窗口，不能据此宣称全历史精确回测"
            ),
            "fetched_at": shanghai_now().isoformat(),
        }

    async def _fetch_tencent_minute_history(
        self,
        stock_code: str,
        interval_minutes: int,
        limit: int,
    ) -> dict:
        symbol = self._tencent_symbol(stock_code)
        requested_limit = min(max(int(limit), 1), 320)
        data = await self.fetch_json(
            self.TENCENT_MINUTE_URL,
            {"param": f"{symbol},m{interval_minutes},,{requested_limit}"},
            self.TENCENT_HEADERS,
        )
        payload = ((data.get("data") or {}).get(symbol) or {})
        bars = []
        for values in payload.get(f"m{interval_minutes}") or []:
            if not isinstance(values, list) or len(values) < 6:
                continue
            try:
                bar_time = datetime.strptime(str(values[0]), "%Y%m%d%H%M")
            except ValueError:
                continue
            bars.append({
                "stock_code": stock_code,
                "stock_name": "",
                "bar_time": bar_time.isoformat(timespec="minutes"),
                "interval_minutes": interval_minutes,
                "open": as_optional_float(values[1]),
                "close": as_optional_float(values[2]),
                "high": as_optional_float(values[3]),
                "low": as_optional_float(values[4]),
                "volume": self._tencent_volume_in_shares(
                    stock_code,
                    as_optional_float(values[5]),
                ),
                "amount": None,
                "average": None,
            })
        quote_payload = payload.get("qt") or {}
        quote = quote_payload.get(symbol, []) if isinstance(quote_payload, dict) else quote_payload
        name = str(quote[1]) if isinstance(quote, list) and len(quote) > 1 else ""
        for bar in bars:
            bar["stock_name"] = name
        return {
            "stock_code": stock_code,
            "stock_name": name,
            "bars": bars,
            "bar_count": len(bars),
            "upstream_total": None,
            "coverage_start": bars[0]["bar_time"] if bars else None,
            "coverage_end": bars[-1]["bar_time"] if bars else None,
            "source": "tencent_minute",
            "complete_history": False,
            "warning": "腾讯公开源仅提供最近分钟窗口，不能据此宣称全历史精确回测",
            "fetched_at": shanghai_now().isoformat(),
        }

    @staticmethod
    def _is_special_treatment_stock(name: object) -> bool:
        normalized = str(name or "").upper()
        return "ST" in normalized or "退" in normalized

    @staticmethod
    def _map_screener_stock(item: dict) -> dict | None:
        try:
            code = normalize_stock_code(item.get("f12"))
        except ValueError:
            return None

        price = as_optional_float(item.get("f2"))
        # The broad EastMoney A-share filter includes long-delisted records.
        # A stock picker must never treat a zero-price record as live market data.
        if price is None or price <= 0:
            return None

        pe_raw = item.get("f9")
        pe = None if pe_raw in (None, "-") else as_optional_float(pe_raw)
        return {
            "code": code,
            "name": str(item.get("f14") or ""),
            "price": price,
            "change_pct": as_float(item.get("f3")),
            "change_amount": as_optional_float(item.get("f4")),
            "volume": EastMoneyDataCollector._eastmoney_volume_in_shares(item.get("f5")),
            "amount": as_int(item.get("f6")),
            "amplitude": as_optional_float(item.get("f7")),
            "turnover": as_float(item.get("f8")),
            "pe": "" if pe is None else pe,
            "pb": item.get("f23") if item.get("f23") not in (None, "-") else "",
            "roe": item.get("f37") if item.get("f37") not in (None, "-") else "",
            "volume_ratio": as_optional_float(item.get("f10")),
            "market_cap": as_int(item.get("f20")),
            "circulating_market_cap": as_int(item.get("f21")),
            "sector": str(item.get("f100") or "").strip(),
            "main_net_inflow": as_int(item.get("f62")),
            "main_net_inflow_pct": as_float(item.get("f184")),
            "super_large_net_inflow": as_int(item.get("f66")),
            "super_large_net_inflow_pct": (
                as_optional_float(item.get("f69"))
                if item.get("f69") not in (None, "-") else None
            ),
            "large_net_inflow": as_int(item.get("f72")),
            "large_order_inflow_pct": (
                as_optional_float(item.get("f75"))
                if item.get("f75") not in (None, "-") else None
            ),
            "open": as_optional_float(item.get("f17")),
            "high": as_optional_float(item.get("f15")),
            "low": as_optional_float(item.get("f16")),
            "previous_close": as_optional_float(item.get("f18")),
            "quote_timestamp": as_int(item.get("f124")) or None,
        }

    async def fetch_technical_screener(self, filters: dict | None = None) -> dict:
        """Return only tradable live quotes matching technical screening criteria."""
        criteria = {
            "min_change": 2,
            "max_pe": 100,
            "min_turnover": 3,
            "sort_field": "f10",
            "page_size": 500,
            "exclude_special": False,
            "require_profitable": False,
            **(filters or {}),
        }
        try:
            rows = await self._fetch_screener_rows(
                str(criteria["sort_field"]), int(criteria["page_size"]),
            )
        except Exception as exc:
            print(f"Error fetching technical screener: {type(exc).__name__}")
            return {"total": 0, "stocks": []}

        results = []
        for item in rows:
            stock = self._map_screener_stock(item)
            if stock is None:
                continue
            pe_value = stock["pe"]
            pe = None if pe_value == "" else as_optional_float(pe_value)
            if stock["change_pct"] < as_float(criteria["min_change"]):
                continue
            if stock["turnover"] < as_float(criteria["min_turnover"]):
                continue
            if criteria.get("max_pe") and pe is not None and pe > as_float(criteria["max_pe"]):
                continue
            if criteria.get("require_profitable") and (pe is None or pe <= 0):
                continue
            if criteria.get("exclude_special") and self._is_special_treatment_stock(stock["name"]):
                continue
            results.append(stock)
        return {"total": len(results), "stocks": results, **self._quote_snapshot_metadata(results)}

    async def fetch_intelligent_selection_candidates(self, page_size: int = 180) -> dict:
        """Build a live candidate pool from capital flow, volume and momentum leaders.

        The union avoids a single sorting dimension dominating the stock picker.
        It intentionally uses only verified, non-zero-price A-share quotes.
        """
        base_filters = {
            "min_change": -100,
            "max_pe": 0,
            "min_turnover": 0,
            "page_size": min(max(page_size, 50), 300),
            "exclude_special": True,
        }
        snapshots = await asyncio.gather(
            self.fetch_technical_screener({**base_filters, "sort_field": "f62"}),
            self.fetch_technical_screener({**base_filters, "sort_field": "f10"}),
            self.fetch_technical_screener({**base_filters, "sort_field": "f3"}),
        )

        candidates: dict[str, dict] = {}
        source_names = ("fund_flow", "volume", "momentum")
        for source_name, snapshot in zip(source_names, snapshots):
            for stock in snapshot.get("stocks") or []:
                code = stock["code"]
                if code not in candidates:
                    candidates[code] = {**stock, "selection_sources": [source_name]}
                else:
                    candidates[code]["selection_sources"].append(source_name)

        def priority(stock: dict) -> float:
            flow_score = max(-20.0, min(35.0, as_int(stock.get("main_net_inflow")) / 1e8 * 2))
            volume_score = max(0.0, min(20.0, as_float(stock.get("volume_ratio")) * 5))
            change_score = max(-15.0, min(20.0, as_float(stock.get("change_pct")) * 3))
            return flow_score + volume_score + change_score + len(stock.get("selection_sources", [])) * 5

        stocks = sorted(candidates.values(), key=priority, reverse=True)
        if stocks:
            return {
                "total": len(stocks),
                "stocks": stocks,
                "source": "eastmoney",
                **self._quote_snapshot_metadata(stocks),
            }
        return await self._fetch_ftshare_intelligent_selection_candidates(page_size)

    async def _fetch_ftshare_intelligent_selection_candidates(self, page_size: int) -> dict:
        """Use FTShare quotes only when every primary candidate ranking is empty.

        FTShare's documented stock filter does not expose per-stock industry
        labels or EastMoney's capital-flow ranking fields. It can keep the
        unfiltered research pipeline available, but cannot truthfully satisfy
        an industry-specific scan on its own.
        """
        try:
            rows = await ftshare_mcp_client.get_stock_filter(min(max(page_size, 50), 300))
        except Exception as exc:
            print(f"FTShare candidate fallback failed: {type(exc).__name__}")
            return {"total": 0, "stocks": [], "source": "ftshare_mcp"}

        stocks = []
        for item in rows:
            raw_symbol = str(item.get("symbol") or "")
            code = raw_symbol.split(".", 1)[0]
            try:
                code = normalize_stock_code(code)
            except ValueError:
                continue
            price = as_optional_float(item.get("close"))
            if price is None or price <= 0:
                continue
            turnover_rate = as_optional_float(item.get("turnover_rate"))
            stocks.append({
                "code": code,
                "name": str(item.get("name") or ""),
                "price": price,
                # FTShare publishes ratios in decimal form for these fields.
                "change_pct": round(as_float(item.get("change_rate")) * 100, 4),
                "volume": as_int(item.get("volume")),
                "amount": as_int(item.get("turnover")),
                "turnover": round(turnover_rate * 100, 4) if turnover_rate is not None else 0.0,
                "pe": item.get("pe_ttm") if item.get("pe_ttm") not in (None, "-") else "",
                "pb": "",
                "roe": "",
                "volume_ratio": None,
                "market_cap": as_int(item.get("float_a_market_cap")),
                "sector": "",
                "main_net_inflow": None,
                "main_net_inflow_pct": None,
                "selection_sources": ["ftshare_market"],
            })
        def fallback_priority(stock: dict) -> float:
            change_score = max(-15.0, min(20.0, as_float(stock.get("change_pct")) * 3))
            turnover_score = max(0.0, min(15.0, as_float(stock.get("turnover")) * 2))
            return change_score + turnover_score

        stocks.sort(key=fallback_priority, reverse=True)
        return {"total": len(stocks), "stocks": stocks, "source": "ftshare_mcp"}

    async def fetch_intelligent_selection_sectors(
        self,
        page_size: int = 180,
        seed_sectors: list[dict] | None = None,
    ) -> list[dict]:
        """Return unique stock industries merged with their live board signals."""
        verified_seed = [
            item for item in (seed_sectors or [])
            if item.get("count_source") == "stock_universe"
            and as_int(item.get("stock_count")) > 0
            and str(item.get("name") or "").strip()
        ]
        if verified_seed:
            counts_result: dict[str, int] | Exception = {
                str(item["name"]).strip(): as_int(item["stock_count"])
                for item in verified_seed
            }
            try:
                flow_result: list[dict] | Exception = await asyncio.wait_for(
                    self.fetch_industry_flow(page_size=100),
                    timeout=settings.stock_selection_sector_refresh_timeout,
                )
            except asyncio.TimeoutError as exc:
                print("Industry hot-signal refresh timed out; using verified sector cache")
                flow_result = exc
            except Exception as exc:
                flow_result = exc
            mapping_rows = verified_seed
        else:
            counts_result, flow_result = await asyncio.gather(
                self._fetch_stock_sector_counts(),
                self.fetch_all_industry_flow(),
                return_exceptions=True,
            )
            mapping_rows = [] if isinstance(flow_result, Exception) else flow_result

        counts = {} if isinstance(counts_result, Exception) else counts_result
        if isinstance(counts_result, Exception):
            print(f"Stock sector counts failed: {type(counts_result).__name__}")
        rows = [] if isinstance(flow_result, Exception) else flow_result
        if isinstance(flow_result, Exception):
            print(f"Industry directory failed: {type(flow_result).__name__}")
            rows = verified_seed
        elif verified_seed:
            live_by_name = {
                str(item.get("name") or "").strip(): item
                for item in rows
                if str(item.get("name") or "").strip()
            }
            # A single ranking page only contains the hottest boards. Keep the
            # last verified signal for every other board instead of zeroing it.
            rows = [
                {**item, **live_by_name.get(str(item["name"]).strip(), {})}
                for item in verified_seed
            ]

        code_by_name: dict[str, str] = {}
        for row in [*mapping_rows, *rows]:
            code = str(row.get("code") or "").strip().upper()
            name = str(row.get("name") or "").strip()
            if BOARD_CODE_RE.fullmatch(code) and name:
                code_by_name.setdefault(name, code)

        rows_by_name: dict[str, list[dict]] = {}
        for row in rows:
            code = str(row.get("code") or "").strip().upper()
            name = str(row.get("name") or "").strip()
            if BOARD_CODE_RE.fullmatch(code) and name:
                rows_by_name.setdefault(name, []).append(row)

        sectors = []
        for name, stock_count in counts.items():
            matches = rows_by_name.get(name, [])
            row = min(
                matches,
                key=lambda item: abs(
                    as_int(item.get("up_count"))
                    + as_int(item.get("down_count"))
                    + as_int(item.get("flat_count"))
                    - stock_count
                ),
                default={},
            )
            up_count = as_int(row.get("up_count"))
            down_count = as_int(row.get("down_count"))
            flat_count = as_int(row.get("flat_count"))
            sectors.append({
                "code": code_by_name.get(name, str(row.get("code") or "")),
                "name": name,
                # Keep candidate_count during the frontend rollout for older clients.
                "candidate_count": stock_count,
                "stock_count": stock_count,
                "count_source": "stock_universe",
                "change_pct": as_float(row.get("change_pct")),
                "main_net_inflow": as_int(row.get("main_net_inflow")),
                "main_net_inflow_pct": as_float(row.get("main_net_inflow_pct")),
                "up_count": up_count,
                "down_count": down_count,
                "flat_count": flat_count,
                "leading_stock": str(row.get("leading_stock") or ""),
            })

        # If the full stock list is temporarily unavailable, keep every board
        # selectable but mark its overlapping active-member count explicitly.
        if not sectors:
            for name, matches in rows_by_name.items():
                row = max(matches, key=lambda item: as_int(item.get("main_net_inflow")))
                up_count = as_int(row.get("up_count"))
                down_count = as_int(row.get("down_count"))
                flat_count = as_int(row.get("flat_count"))
                stock_count = up_count + down_count + flat_count
                sectors.append({
                    "code": str(row.get("code") or ""),
                    "name": name,
                    "candidate_count": stock_count,
                    "stock_count": stock_count,
                    "count_source": "board_active_members",
                    "change_pct": as_float(row.get("change_pct")),
                    "main_net_inflow": as_int(row.get("main_net_inflow")),
                    "main_net_inflow_pct": as_float(row.get("main_net_inflow_pct")),
                    "up_count": up_count,
                    "down_count": down_count,
                    "flat_count": flat_count,
                    "leading_stock": str(row.get("leading_stock") or ""),
                })

        if sectors:
            sectors.sort(
                key=lambda item: (
                    item["main_net_inflow"],
                    item["change_pct"],
                    item["stock_count"],
                ),
                reverse=True,
            )
            for rank, sector in enumerate(sectors, start=1):
                sector["heat_rank"] = rank
            return sectors

        # Preserve a usable name-only fallback when both complete sources fail.
        snapshot = await self.fetch_intelligent_selection_candidates(page_size=page_size)
        counts: dict[str, int] = {}
        for stock in snapshot.get("stocks") or []:
            sector = str(stock.get("sector") or "").strip()
            if sector:
                counts[sector] = counts.get(sector, 0) + 1
        return [
            {
                "code": "",
                "name": sector,
                "candidate_count": count,
                "stock_count": count,
                "count_source": "leader_pool",
                "change_pct": 0.0,
                "main_net_inflow": 0,
                "main_net_inflow_pct": 0.0,
                "up_count": 0,
                "down_count": 0,
                "flat_count": 0,
                "leading_stock": "",
                "heat_rank": rank,
            }
            for rank, (sector, count) in enumerate(
                sorted(counts.items(), key=lambda item: (-item[1], item[0])),
                start=1,
            )
        ]

    async def _fetch_stock_sector_counts(self) -> dict[str, int]:
        now = time.monotonic()
        cached = self._sector_counts_cache
        if cached and now - cached[0] < self.SECTOR_COUNTS_CACHE_SECONDS:
            return dict(cached[1])

        async with self._sector_counts_lock:
            now = time.monotonic()
            cached = self._sector_counts_cache
            if cached and now - cached[0] < self.SECTOR_COUNTS_CACHE_SECONDS:
                return dict(cached[1])

            counts: dict[str, int] = {}
            for stock in await self.fetch_stock_universe():
                sector = str(stock.get("sector") or "").strip()
                if sector:
                    counts[sector] = counts.get(sector, 0) + 1
            if counts:
                self._sector_counts_cache = (now, dict(counts))
            return counts


collector = EastMoneyDataCollector()
