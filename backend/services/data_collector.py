"""东方财富公开行情采集器。

所有金额保持数据源原始的人民币单位，页面层再负责格式化。这里不制造兜底
行情：上游不可用时返回空结果，由 API 明确标记不可用。
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from config import settings


EASTMONEY_UT = "b2884a393a59ad6402e4dd90d24e112f"
SHANGHAI_PREFIXES = ("600", "601", "603", "605", "688", "689", "900")
SHENZHEN_PREFIXES = ("000", "001", "002", "003", "200", "300", "301", "302")
BEIJING_PREFIXES = ("4", "8", "92")
STOCK_CODE_RE = re.compile(r"^(?:SH|SZ|BJ)?[.:-]?(\d{6})(?:\.(?:SH|SZ|BJ))?$")
BOARD_CODE_RE = re.compile(r"^BK\d{4}$")


def shanghai_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


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
    """Return a validated six-digit A-share code or raise ValueError."""
    candidate = str(value or "").strip().upper()
    if candidate.startswith("BK"):
        raise ValueError("板块编码不能作为股票代码")

    match = STOCK_CODE_RE.fullmatch(candidate)
    if not match:
        raise ValueError("股票代码必须是有效的六位数字代码")

    code = match.group(1)
    if code.startswith(SHANGHAI_PREFIXES + SHENZHEN_PREFIXES + BEIJING_PREFIXES):
        return code
    raise ValueError(f"不支持的股票代码前缀: {code}")


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
    DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
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
        "f104": "up_count",
        "f105": "down_count",
        "f128": "leading_stock",
        "f140": "leading_stock_code",
        "f136": "leading_stock_change_pct",
    }

    async def fetch_json(self, url: str, params: dict, headers: dict | None = None) -> dict:
        """Fetch market JSON, preferring the Singapore/China data proxy."""
        request_headers = headers or self.HEADERS
        if settings.data_proxy_base_url:
            try:
                return await self._fetch_via_proxy(url, params, request_headers)
            except Exception as exc:
                # The API result carries availability metadata; this log is only for operators.
                print(f"[Data Proxy] failed, trying direct source: {type(exc).__name__}")
        return await self._fetch_direct(url, params, request_headers)

    async def _fetch_direct(self, url: str, params: dict, headers: dict) -> dict:
        async with httpx.AsyncClient(timeout=settings.data_proxy_timeout) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()

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
        async with httpx.AsyncClient(timeout=settings.data_proxy_timeout) as client:
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
            "pn": str(page), "pz": str(page_size), "po": str(sort_order), "np": "1",
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
                "fid": "f62", "fs": board_filter,
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
        limit_days = item.get("zttj", {}).get("days") if isinstance(item.get("zttj"), dict) else item.get("days")
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
            "continuous_days": as_int(limit_days),
            "sector": item.get("hybk", ""),
            "main_net_inflow": as_int(item.get("fund")),
            "first_limit_time": item.get("fbt"),
            "last_limit_time": item.get("lbt"),
            "limit_direction": direction,
        }

    async def _fetch_limit_pool(self, endpoint: str, direction: str, page: int, page_size: int) -> dict:
        params = {
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "dpt": "wz.ztzt",
            "Pageindex": str(max(page - 1, 0)),
            "pagesize": str(page_size),
            "sort": "fbt:asc" if direction == "up" else "fund:asc",
            "date": shanghai_now().strftime("%Y%m%d"),
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

    async def fetch_limit_up_pool(self, page: int = 1, page_size: int = 200) -> dict:
        return await self._fetch_limit_pool("https://push2ex.eastmoney.com/getTopicZTPool", "up", page, page_size)

    async def fetch_limit_down_pool(self, page: int = 1, page_size: int = 200) -> dict:
        return await self._fetch_limit_pool("https://push2ex.eastmoney.com/getTopicDTPool", "down", page, page_size)

    async def fetch_limit_up_stocks(self, page: int = 1, page_size: int = 200) -> list[dict]:
        return (await self.fetch_limit_up_pool(page, page_size))["stocks"]

    async def fetch_limit_down_stocks(self, page: int = 1, page_size: int = 200) -> list[dict]:
        return (await self.fetch_limit_down_pool(page, page_size))["stocks"]

    async def fetch_board_stocks(self, board_code: str, page: int = 1, page_size: int = 100) -> dict:
        try:
            code = normalize_board_code(board_code)
        except ValueError as exc:
            return {"total": 0, "stocks": [], "error": str(exc)}
        params = {
            "pn": str(page), "pz": str(page_size), "po": "0", "np": "1", "fltt": "2", "invt": "2",
            "fid": "f62", "fs": f"b:{code}",
            "fields": "f2,f3,f5,f6,f8,f9,f10,f12,f14,f15,f16,f20,f21,f23,f37,f45,f62,f184",
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
                "volume": as_int(item.get("f5")),
                "amount": as_int(item.get("f6")),
                "turnover": as_float(item.get("f8")),
                "pe": item.get("f9") if item.get("f9") not in (None, "-") else "",
                "pb": item.get("f23") if item.get("f23") not in (None, "-") else "",
                "roe": item.get("f37") if item.get("f37") not in (None, "-") else "",
                "market_cap": as_int(item.get("f20")),
                "total_market_cap": as_int(item.get("f21")),
                "volume_ratio": as_float(item.get("f10")),
                "main_net_inflow": as_int(item.get("f62")),
                "main_net_inflow_pct": as_float(item.get("f184")),
                "high": as_float(item.get("f15")),
                "low": as_float(item.get("f16")),
            })
        return {
            "total": as_int(payload.get("total")), "stocks": stocks,
            "page": page, "page_size": page_size, "board_code": code,
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
        Beijing-listed shares. Fields absent from that source remain null.
        """
        code = normalize_stock_code(stock_code)
        symbol = self._tencent_symbol(code)
        params = {
            # The spare rows let us calculate the first retained bar's daily
            # change against the preceding close.
            "param": f"{symbol},day,,,{min(max(days + 20, 30), 800)},qfq",
        }
        data = await self.fetch_json(self.TENCENT_KLINE_URL, params, self.TENCENT_HEADERS)
        payload = ((data.get("data") or {}).get(symbol) or {})
        series = payload.get("qfqday") or payload.get("day") or []
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
                # Tencent reports daily volume in lots; the database contract
                # uses shares, matching the realtime EastMoney fields.
                "volume": None if volume_lots is None else int(volume_lots * 100),
                "amount": None, "amplitude": amplitude, "change_pct": change_pct,
                "change_amount": change_amount, "turnover": None,
            })
            previous_close = close_price
        quote_payload = payload.get("qt") or []
        quote = quote_payload.get(symbol, []) if isinstance(quote_payload, dict) else quote_payload
        name = str(quote[1]) if isinstance(quote, list) and len(quote) > 1 else ""
        return {
            "code": code,
            "name": name,
            "source": "tencent",
            "history": self._history_in_window(history, days),
        }

    async def fetch_stock_universe(self) -> list[dict]:
        page_size = self.MAX_LIST_PAGE_SIZE

        async def fetch_page(page: int) -> tuple[list[dict], int]:
            params = {
                "pn": str(page), "pz": str(page_size), "po": "0", "np": "1", "fid": "f3",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                "fields": "f12,f13,f14", "fltt": "2", "ut": EASTMONEY_UT,
            }
            data = await self.fetch_json(self.BASE_URL, params)
            payload = data.get("data") or {}
            return payload.get("diff") or [], as_int(payload.get("total"))

        try:
            first_page, total = await fetch_page(1)
            if not first_page:
                raise RuntimeError("股票清单为空")
            pages = max(1, (total + page_size - 1) // page_size)
            rows = list(first_page)
            for start in range(2, pages + 1, self.PAGE_FETCH_CONCURRENCY):
                page_numbers = range(start, min(start + self.PAGE_FETCH_CONCURRENCY, pages + 1))
                responses = await asyncio.gather(*(fetch_page(page) for page in page_numbers))
                for page_rows, _ in responses:
                    rows.extend(page_rows)
        except Exception as exc:
            raise RuntimeError(f"获取全市场股票清单失败: {type(exc).__name__}") from exc

        records = []
        seen_codes: set[str] = set()
        for item in rows:
            try:
                code = normalize_stock_code(item.get("f12"))
            except ValueError:
                continue
            if code in seen_codes:
                continue
            seen_codes.add(code)
            records.append({"code": code, "name": item.get("f14", ""), "market": item.get("f13")})
        if total and len(records) < total:
            raise RuntimeError(f"股票清单不完整: expected={total}, received={len(records)}")
        return records

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

    async def fetch_market_breadth(self) -> dict:
        result: dict[str, dict] = {}
        for market_filter, name in (("m:1+t:2", "沪市"), ("m:0+t:6,m:0+t:80", "深市"), ("m:0+t:80", "创业板")):
            params = {
                "pn": "1", "pz": "1", "po": "0", "np": "1", "fltt": "2", "invt": "2", "fs": market_filter,
                "fields": "f104,f105", "ut": EASTMONEY_UT,
            }
            try:
                data = await self.fetch_json(self.BASE_URL, params)
                row = ((data.get("data") or {}).get("diff") or [None])[0]
                if not row:
                    continue
                up_count, down_count = as_int(row.get("f104")), as_int(row.get("f105"))
                total = up_count + down_count
                result[name] = {"up": up_count, "down": down_count, "total": total, "ratio": round(up_count / total * 100, 1) if total else 0}
            except Exception as exc:
                print(f"Error fetching market breadth for {name}: {type(exc).__name__}")
        return result

    async def fetch_market_turnover(self) -> dict:
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": "1.000001", "fields": "f43,f47,f48,f57,f58,f170,f171", "ut": EASTMONEY_UT,
        }
        try:
            data = await self.fetch_json(url, params)
        except Exception as exc:
            print(f"Error fetching market turnover: {type(exc).__name__}")
            return {}
        row = data.get("data") or {}
        if not row:
            return {}
        return {
            "sh_index": round(as_float(row.get("f43")) / 100, 2),
            "sh_change": round(as_float(row.get("f170")) / 100, 2),
            "sh_change_pct": round(as_float(row.get("f171")) / 100, 2),
            "sh_volume": as_int(row.get("f47")),
            "sh_amount": as_int(row.get("f48")),
            "data_date": shanghai_now().date().isoformat(),
        }

    async def fetch_dragon_board(self, page_size: int = 50) -> list[dict]:
        params = {
            "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW", "columns": "ALL", "pageNumber": "1", "pageSize": str(page_size),
            "sortTypes": "-1,-1", "sortColumns": "TRADE_DATE,BILLBOARD_NET_AMT", "source": "WEB", "client": "WEB",
        }
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
            institutions = re.search(r"(\d+)家机构", str(item.get("EXPLAIN") or ""))
            stocks.append({
                "code": code, "name": item.get("SECURITY_NAME_ABBR", ""),
                "date": str(item.get("TRADE_DATE") or "")[:10], "price": as_float(item.get("CLOSE_PRICE")),
                "change_pct": as_float(item.get("CHANGE_RATE")), "turnover": as_float(item.get("TURNOVERRATE")),
                "amount": as_int(item.get("BILLBOARD_DEAL_AMT")), "main_net_inflow": as_int(item.get("BILLBOARD_NET_AMT")),
                "buy_amount": as_int(item.get("BILLBOARD_BUY_AMT")), "sell_amount": as_int(item.get("BILLBOARD_SELL_AMT")),
                "market_cap": as_int(item.get("FREE_MARKET_CAP")), "institution_count": as_int(institutions.group(1)) if institutions else 0,
                "reason": item.get("EXPLANATION", ""),
            })
        return stocks

    async def fetch_block_trades(self, page: int = 1, page_size: int = 50) -> list[dict]:
        params = {
            "reportName": "RPT_DATA_BLOCKTRADE", "columns": "ALL", "pageNumber": str(page), "pageSize": str(page_size),
            "sortTypes": "-1,-1", "sortColumns": "TRADE_DATE,DEAL_AMT", "source": "WEB", "client": "WEB",
        }
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
        return result

    async def fetch_sector_rotation(self, lookback_days: int = 5) -> dict:
        del lookback_days
        sectors = []
        for item in await self.fetch_concept_flow(page_size=100):
            sectors.append({
                "code": item.get("code", ""), "name": item.get("name", ""),
                "change_pct": as_float(item.get("change_pct")), "main_net_inflow": as_int(item.get("main_net_inflow")),
                "super_large_inflow": as_int(item.get("super_large_net_inflow")), "large_inflow": as_int(item.get("large_net_inflow")),
                "up_count": as_int(item.get("up_count")), "down_count": as_int(item.get("down_count")),
            })
        return {
            "sectors": sectors,
            "hot_inflow": sorted(sectors, key=lambda item: item["main_net_inflow"], reverse=True)[:5],
            "hot_outflow": sorted(sectors, key=lambda item: item["main_net_inflow"])[:5],
            "hot_gainers": sorted(sectors, key=lambda item: item["change_pct"], reverse=True)[:5],
        }

    async def fetch_technical_screener(self, filters: dict | None = None) -> dict:
        criteria = {"min_change": 2, "max_pe": 100, "min_turnover": 3, **(filters or {})}
        params = {
            "pn": "1", "pz": "500", "po": "0", "np": "1", "fltt": "2", "invt": "2", "fid": "f10",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23", "fields": "f2,f3,f5,f8,f9,f10,f12,f14,f20,f23,f37,f62,f184",
            "ut": EASTMONEY_UT,
        }
        try:
            data = await self.fetch_json(self.BASE_URL, params)
        except Exception as exc:
            print(f"Error fetching technical screener: {type(exc).__name__}")
            return {"total": 0, "stocks": []}
        results = []
        for item in ((data.get("data") or {}).get("diff") or []):
            try:
                code = normalize_stock_code(item.get("f12"))
            except ValueError:
                continue
            change_pct, turnover = as_float(item.get("f3")), as_float(item.get("f8"))
            pe_raw = item.get("f9")
            pe = None if pe_raw in (None, "-") else as_float(pe_raw)
            if change_pct < criteria["min_change"] or turnover < criteria["min_turnover"]:
                continue
            if pe is not None and criteria.get("max_pe") and pe > criteria["max_pe"]:
                continue
            results.append({
                "code": code, "name": item.get("f14", ""), "price": as_float(item.get("f2")), "change_pct": change_pct,
                "volume": as_int(item.get("f5")), "turnover": turnover, "pe": "" if pe is None else pe,
                "pb": item.get("f23") if item.get("f23") not in (None, "-") else "", "roe": item.get("f37") if item.get("f37") not in (None, "-") else "",
                "volume_ratio": as_float(item.get("f10")), "market_cap": as_int(item.get("f20")),
                "main_net_inflow": as_int(item.get("f62")), "main_net_inflow_pct": as_float(item.get("f184")),
            })
        return {"total": len(results), "stocks": results}


collector = EastMoneyDataCollector()
