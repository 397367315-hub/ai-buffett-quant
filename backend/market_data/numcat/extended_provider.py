"""Complete, bounded access layer for the documented NumCat stock APIs.

The existing market provider keeps the contracts used by legacy collectors.
This module adds the remaining documented endpoints without creating a second
database warehouse: responses are returned on demand and use the gateway's
bounded in-process cache only.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from typing import Any

import httpx

from config import settings

from .gateway import NumCatGatewayError, numcat_gateway
from .market_provider import _rows, _symbol


# The official 0.0.481 catalog. Several documentation paths intentionally
# share one apiname (for example the five minute-bar paths).
DOCUMENTED_APINAMES = frozenset({
    "adj_factor", "anomaly_forecast", "auc_kp", "daily", "daily_auc",
    "daily_auc_detail", "daily_auc_fd", "emoindic_daily", "fengk_kp",
    "finance_announcement", "finance_balance_sheet", "finance_capital",
    "finance_cash_flow", "finance_disclosure_date", "finance_dividend",
    "finance_express", "finance_forecast", "finance_holder_number",
    "finance_income_statement", "finance_indicator", "finance_top10_float_holder",
    "finance_top10_holder", "fundflow_kp", "hotstock", "interactive_qa",
    "level2_order_history", "level2_quote_history", "level2_trade_history",
    "limit_event_v2_history", "limit_pool", "limit_pool_yes",
    "longhubang_hot_money", "longhubang_seat", "longhubang_stock",
    "margin_detail", "margin_securities", "margin_summary", "minute", "monthly",
    "new_share", "news", "northbound_flow", "northbound_holding",
    "northbound_top10", "point_monitor", "pricelimit", "screening",
    "southbound_daily", "southbound_flow_minute", "southbound_top10", "st",
    "stk_factor_pro", "stockbasic", "suspend", "theme_auc_kp", "theme_concept",
    "theme_daily", "theme_industry", "theme_lib_detail_kp", "theme_lib_kp",
    "theme_members", "theme_reason", "themedaily_jx", "themefundflow_jx",
    "thememembers_jx", "thememinute_jx", "tick", "tick_fd", "tick_history",
    "tradecal", "transfer_financing", "valuation", "weekly",
})

TYPED_PROVIDER_APINAMES = frozenset({
    "daily", "stk_factor_pro", "stockbasic", "screening", "valuation",
    "finance_indicator", "daily_auc", "daily_auc_detail", "minute", "fundflow_kp",
    "emoindic_daily", "limit_pool", "longhubang_stock", "longhubang_seat",
    "margin_summary", "margin_detail", "margin_securities", "themedaily_jx",
    "themefundflow_jx", "thememembers_jx", "theme_auc_kp", "fengk_kp", "hotstock",
    "theme_lib_kp", "theme_lib_detail_kp", "theme_reason", "theme_daily", "news",
    "finance_announcement", "level2_quote_history", "level2_trade_history",
    "level2_order_history",
})

MISSING_TYPED_APINAMES = DOCUMENTED_APINAMES - TYPED_PROVIDER_APINAMES

API_GROUPS: dict[str, str] = {
    "tradecal": "交易日历",
    "tick": "实时快照",
    "tick_history": "Tick历史与竞价边界",
    "daily_auc": "集合竞价",
    "auc_kp": "竞价增强",
    "theme_industry": "板块基础",
    "theme_concept": "板块基础",
    "theme_members": "板块基础",
    "thememinute_jx": "板块盘中",
    "weekly": "多周期行情",
    "monthly": "多周期行情",
    "pricelimit": "交易约束",
    "suspend": "交易约束",
    "st": "交易约束",
    "adj_factor": "复权",
    "limit_event_v2_history": "异动历史",
    "point_monitor": "交易监管",
    "anomaly_forecast": "异动预测",
    "new_share": "新股",
    "longhubang_hot_money": "龙虎榜",
    "northbound_flow": "互联互通",
    "northbound_top10": "互联互通",
    "northbound_holding": "互联互通",
    "southbound_flow_minute": "互联互通",
    "southbound_daily": "互联互通",
    "southbound_top10": "互联互通",
    "transfer_financing": "两融",
    "finance_balance_sheet": "财务PIT",
    "finance_income_statement": "财务PIT",
    "finance_cash_flow": "财务PIT",
    "finance_capital": "财务PIT",
    "finance_holder_number": "财务PIT",
    "finance_top10_holder": "财务PIT",
    "finance_top10_float_holder": "财务PIT",
    "finance_forecast": "财务PIT",
    "finance_express": "财务PIT",
    "finance_disclosure_date": "财务PIT",
    "finance_dividend": "财务PIT",
    "interactive_qa": "公司问答",
}

DEFAULT_TTLS: dict[str, int] = {
    "tradecal": 12 * 60 * 60,
    "stockbasic": 6 * 60 * 60,
    "theme_industry": 6 * 60 * 60,
    "theme_concept": 6 * 60 * 60,
    "theme_members": 60 * 60,
    "weekly": 30 * 60,
    "monthly": 30 * 60,
    "adj_factor": 30 * 60,
    "finance_balance_sheet": 6 * 60 * 60,
    "finance_income_statement": 6 * 60 * 60,
    "finance_cash_flow": 6 * 60 * 60,
    "finance_indicator": 6 * 60 * 60,
    "finance_capital": 6 * 60 * 60,
    "finance_holder_number": 6 * 60 * 60,
    "finance_top10_holder": 6 * 60 * 60,
    "finance_top10_float_holder": 6 * 60 * 60,
    "finance_forecast": 6 * 60 * 60,
    "finance_express": 6 * 60 * 60,
    "finance_disclosure_date": 6 * 60 * 60,
    "finance_dividend": 6 * 60 * 60,
    "longhubang_hot_money": 6 * 60 * 60,
    "interactive_qa": 6 * 60 * 60,
    "news": 10 * 60,
    "finance_announcement": 10 * 60,
}

REALTIME_APINAMES = frozenset({
    "tick", "daily_auc", "daily_auc_detail", "auc_kp", "daily_auc_fd", "tick_fd",
    "thememinute_jx", "themefundflow_jx", "themedaily_jx", "screening",
})


def _clean_list(value: Any, *, max_items: int = 200) -> list[Any]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()][:max_items]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [item for item in list(value)[:max_items] if isinstance(item, (str, int, float, bool))]


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any]:
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise ValueError("params必须是对象")
    output: dict[str, Any] = {}
    for raw_key, value in list(params.items())[:80]:
        key = str(raw_key).strip()
        if not key or len(key) > 80:
            raise ValueError("params包含无效字段名")
        if key in {"symbols", "symbol", "theme_symbols", "theme_symbol", "groups", "types", "event_types"}:
            values = _clean_list(value, max_items=200 if key not in {"groups", "event_types"} else 20)
            if key in {"symbols", "symbol", "theme_symbols", "theme_symbol"}:
                values = [str(item).strip()[:32] for item in values if str(item).strip()]
            output[key] = values if isinstance(value, (list, tuple, set)) else ",".join(map(str, values))
        elif key in {"limit", "offset", "page", "page_size", "recentdays"}:
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key}必须是整数") from exc
            maximum = 6000 if key in {"limit", "page_size"} else 200000 if key == "offset" else 800
            output[key] = min(max(number, 0 if key == "offset" else 1), maximum)
        elif key in {"event_types"}:
            output[key] = _clean_list(value, max_items=12)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            text = value if not isinstance(value, str) else value[:2000]
            output[key] = text
        else:
            raise ValueError(f"{key}必须是简单JSON值")
    try:
        if len(json.dumps(output, ensure_ascii=True, default=str).encode("utf-8")) > 48 * 1024:
            raise ValueError("params不能超过48KB")
    except TypeError as exc:
        raise ValueError("params不是有效JSON") from exc
    return output


def _payload_data(payload: dict[str, Any]) -> Any:
    data = payload.get("data")
    if data is not None:
        return data
    result = payload.get("result")
    return result if result is not None else payload


class NumCatExtendedProvider:
    """Official-catalog adapter with one safe generic escape hatch."""

    name = "numcat"

    @property
    def configured(self) -> bool:
        return numcat_gateway.configured

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "apiname": name,
                "group": API_GROUPS.get(name, "已接入").strip(),
                "typed_provider": name in TYPED_PROVIDER_APINAMES,
                "generic_query": True,
                "realtime": name in REALTIME_APINAMES,
                "cache_ttl_seconds": DEFAULT_TTLS.get(name, 30 if name in REALTIME_APINAMES else 900),
            }
            for name in sorted(DOCUMENTED_APINAMES)
        ]

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "configured": self.configured,
            "official_catalog_version": "0.0.481",
            "official_apiname_count": len(DOCUMENTED_APINAMES),
            "typed_provider_count": len(TYPED_PROVIDER_APINAMES),
            "generic_query_count": len(DOCUMENTED_APINAMES),
            "typed_provider_missing": sorted(MISSING_TYPED_APINAMES),
            "websocket_ticket_supported": True,
            "persistent_raw_storage": False,
            "gateway": numcat_gateway.status(),
        }

    async def query(
        self,
        apiname: str,
        *,
        params: dict[str, Any] | None = None,
        fields: str | list[str] | None = None,
        refresh: bool = False,
        cache_ttl: int | None = None,
    ) -> dict[str, Any]:
        name = str(apiname or "").strip().lower()
        if name not in DOCUMENTED_APINAMES:
            raise ValueError(f"不支持的猫爪接口: {name or '空'}")
        clean_params = _clean_params(params)
        clean_fields: str | list[str] | None = fields
        if isinstance(fields, list):
            clean_fields = [str(item).strip()[:80] for item in fields[:120] if str(item).strip()]
        elif isinstance(fields, str):
            clean_fields = fields[:8000]
        elif fields is not None:
            raise ValueError("fields必须是字符串或数组")
        ttl = DEFAULT_TTLS.get(name, 30 if name in REALTIME_APINAMES else 900) if cache_ttl is None else max(0, min(int(cache_ttl), 24 * 60 * 60))
        payload = await numcat_gateway.query(
            name,
            params=clean_params,
            fields=clean_fields,
            market=_market_from_params(clean_params),
            cache_ttl=0 if refresh else ttl,
            bypass_cache=refresh,
            affinity_key=f"extended:{name}:{json.dumps(clean_params, sort_keys=True, default=str)}",
        )
        data = _payload_data(payload)
        rows = _rows(payload)
        return {
            "apiname": name,
            "source": "numcat",
            "fetched_at": datetime.now().astimezone().isoformat(),
            "cache_policy": "memory_only_bounded",
            "persistent_raw_storage": False,
            "row_count": len(rows),
            "fields": data.get("fields") if isinstance(data, dict) else None,
            "data": data,
        }

    async def rows(self, apiname: str, *, params: dict[str, Any] | None = None, fields: str | list[str] | None = None, refresh: bool = False) -> list[dict[str, Any]]:
        result = await self.query(apiname, params=params, fields=fields, refresh=refresh)
        data = result.get("data")
        if not isinstance(data, dict):
            return []
        fields_value = data.get("fields") or []
        items = data.get("items")
        if items is None:
            items = data.get("rows") or data.get("data") or []
        if not isinstance(items, list):
            return []
        if isinstance(fields_value, list) and fields_value:
            return [
                item if isinstance(item, dict) else {
                    str(key): item[index] if index < len(item) else None
                    for index, key in enumerate(fields_value)
                }
                for item in items
                if isinstance(item, (dict, list, tuple))
            ]
        return [item for item in items if isinstance(item, dict)]

    async def calendar(self, *, mode: str = "latest", params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        allowed = {"latest", "range", "start_to_latest", "recent_list", "recent_days", "by_date"}
        if mode not in allowed:
            raise ValueError("交易日历模式无效")
        clean = dict(params or {})
        if mode == "latest":
            clean = {}
        elif mode == "start_to_latest":
            clean = {"startdate": clean.get("startdate")}
        elif mode == "recent_list":
            clean = {"recentdays": clean.get("recentdays", 5)}
        elif mode == "recent_days":
            clean = {"tradedate_offset": clean.get("tradedate_offset", 0)}
        elif mode == "by_date":
            clean = {"tradedate": clean.get("tradedate")}
        return await self.rows("tradecal", params=clean)

    async def industry_boards(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("theme_industry", params=params)

    async def concept_boards(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("theme_concept", params=params)

    async def board_members(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("theme_members", params=params)

    async def theme_minute(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("thememinute_jx", params=params)

    async def tick_snapshot(self, symbols: list[str], *, tradedate: date | None = None, auction: bool = False) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"asset": "stock", "symbols": symbols, "type": 0 if auction else 1}
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        return await self.rows("tick", params=params)

    async def tick_history(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return await self.rows("tick_history", params=params)

    async def last_tick(self, symbols: list[str], *, tradedate: date | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"asset": "stock", "symbols": symbols, "trademin": "0925", "side": "before"}
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        return await self.rows("tick_history", params=params)

    async def tick_fd(self, symbol: str, *, tradedate: date | None = None, trademin: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": _symbol(symbol)}
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        if trademin:
            params["trademin"] = str(trademin).replace(":", "")[:4]
        return await self.rows("tick_fd", params=params)

    async def auction_limit_buy(self, symbols: list[str], *, tradedate: date | None = None) -> list[dict[str, Any]]:
        return await self.rows("auc_kp", params=_dated_symbols(symbols, tradedate))

    async def auction_one_price(self, symbols: list[str], *, tradedate: date | None = None) -> list[dict[str, Any]]:
        return await self.rows("daily_auc_fd", params=_dated_symbols(symbols, tradedate))

    async def weekly(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("weekly", params=params)

    async def monthly(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("monthly", params=params)

    async def adjustment_factor(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("adj_factor", params=params)

    async def limit_pool_yesterday(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("limit_pool_yes", params=params)

    async def price_limit(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("pricelimit", params=params)

    async def suspend(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("suspend", params=params)

    async def st(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("st", params=params)

    async def limit_event_history(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("limit_event_v2_history", params=params)

    async def point_monitor(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("point_monitor", params=params)

    async def anomaly_forecast(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("anomaly_forecast", params=params)

    async def new_share(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("new_share", params=params)

    async def hot_money(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("longhubang_hot_money", params=params)

    async def finance(self, apiname: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if apiname not in {
            "finance_balance_sheet", "finance_income_statement", "finance_cash_flow", "finance_indicator",
            "finance_capital", "finance_holder_number", "finance_top10_holder", "finance_top10_float_holder",
            "finance_forecast", "finance_express", "finance_disclosure_date", "finance_dividend",
        }:
            raise ValueError("不是受支持的猫爪财务接口")
        # Keep financial research point-in-time safe: callers may narrow the
        # query, but cannot silently request an obsolete/ambiguous revision.
        return await self.rows(apiname, params={**(params or {}), "version": "latest"})

    async def northbound(self, apiname: str = "northbound_flow", params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if apiname not in {"northbound_flow", "northbound_top10", "northbound_holding"}:
            raise ValueError("不是北向资金接口")
        return await self.rows(apiname, params=params)

    async def southbound(self, apiname: str = "southbound_daily", params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if apiname not in {"southbound_flow_minute", "southbound_daily", "southbound_top10"}:
            raise ValueError("不是南向资金接口")
        return await self.rows(apiname, params=params)

    async def transfer_financing(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.rows("transfer_financing", params=params)

    async def research_bundle(
        self,
        symbols: list[str],
        *,
        tradedate: date | None = None,
        include_finance: bool = True,
        include_regulatory: bool = True,
        include_microstructure: bool = True,
    ) -> dict[str, Any]:
        codes = list(dict.fromkeys(_symbol(item) for item in symbols if _symbol(item)))[:20]
        if not codes:
            raise ValueError("symbols不能为空")
        dated = _dated_symbols(codes, tradedate)
        jobs: dict[str, Any] = {
            "trade_calendar": self.calendar(mode="recent_list", params={"recentdays": 5}),
            "security_basic": self.rows("stockbasic", params={"symbols": codes, "list_status": "L"}),
            "tick": self.tick_snapshot(codes),
            "auction": self.rows("daily_auc", params=dated),
            "last_auction_tick": self.last_tick(codes, tradedate=tradedate),
            "auction_limit_buy": self.auction_limit_buy(codes, tradedate=tradedate),
            "auction_one_price": self.auction_one_price(codes, tradedate=tradedate),
            "daily": self.rows("stk_factor_pro", params=dated),
        }
        if include_regulatory:
            jobs.update({
                "price_limit": self.price_limit(params=dated),
                "st": self.st(params={"symbols": codes, **_date_range(tradedate)}),
                "suspend": self.suspend(params={"symbols": codes, **_date_range(tradedate)}),
            })
        if include_microstructure:
            jobs["limit_events"] = self.limit_event_history(params={"symbols": codes, **_date_range(tradedate), "limit": 200})
        if include_finance:
            for name in (
                "finance_indicator", "finance_income_statement", "finance_cash_flow",
                "finance_forecast", "finance_disclosure_date", "finance_dividend",
            ):
                jobs[name] = self.finance(name, params={"symbols": codes, "limit": 200})
        keys = list(jobs)
        results = await asyncio.gather(*(jobs[key] for key in keys), return_exceptions=True)
        output: dict[str, Any] = {
            "symbols": codes,
            "trade_date": tradedate.isoformat() if tradedate else None,
            "source": "numcat",
            "persistent_raw_storage": False,
            "updated_at": datetime.now().astimezone().isoformat(),
            "sections": {},
            "errors": [],
        }
        for key, result in zip(keys, results):
            if isinstance(result, Exception):
                output["sections"][key] = {"available": False, "rows": [], "error": type(result).__name__}
                output["errors"].append(f"{key}:{type(result).__name__}")
            else:
                rows = result if isinstance(result, list) else []
                output["sections"][key] = {"available": bool(rows), "rows": rows, "count": len(rows), "source": "numcat"}
        output["available"] = any(item.get("available") for item in output["sections"].values())
        output["partial"] = bool(output["errors"])
        return output

    async def realtime_ticket(
        self,
        stream: str,
        *,
        symbols: list[str] | None = None,
        groups: list[str] | None = None,
        event_types: list[int] | None = None,
        fields: list[str] | None = None,
        ttl_seconds: int = 120,
    ) -> dict[str, Any]:
        if stream not in {"tick_stream_v1", "stream_limit_event_v2"}:
            raise ValueError("实时流类型无效")
        ttl = min(max(int(ttl_seconds), 30), 600)
        body: dict[str, Any] = {
            "apikey": numcat_gateway.api_key,
            "channel": "tick_stream",
            "stream_version": "tick_v1" if stream == "tick_stream_v1" else "stream_limit_event_v2",
            "symbols": [str(item).strip() for item in (symbols or []) if str(item).strip()][:200],
            "groups": [str(item).strip().upper() for item in (groups or []) if str(item).strip()][:20],
            "ttl_seconds": ttl,
        }
        if event_types is not None:
            body["event_types"] = [int(item) for item in event_types[:12]]
        if fields is not None:
            body["fields"] = [str(item).strip() for item in fields[:20] if str(item).strip()]
        base = str(settings.numcat_public_base_url or settings.numcat_api_base or "").rstrip("/")
        if not base:
            raise NumCatGatewayError("NumCat公网地址未配置")
        url = f"{base}/reference-proxy/stock/{stream}"
        timeout = min(max(float(settings.numcat_timeout or 20), 2), 60)
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=True) as client:
            response = await client.post(url, json=body, headers={"Accept": "application/json", "Content-Type": "application/json"})
        if response.status_code >= 400:
            raise NumCatGatewayError(f"NumCat实时流票据请求失败（HTTP {response.status_code}）")
        payload = response.json()
        if not isinstance(payload, dict):
            raise NumCatGatewayError("NumCat实时流票据格式不可识别")
        return {
            "stream": stream,
            "source": "numcat",
            "persistent_raw_storage": False,
            "data": _payload_data(payload),
        }


def _dated_symbols(symbols: list[str], tradedate: date | None) -> dict[str, Any]:
    params: dict[str, Any] = {"symbols": symbols}
    if tradedate:
        params["tradedate"] = tradedate.strftime("%Y%m%d")
    return params


def _date_range(tradedate: date | None) -> dict[str, Any]:
    if tradedate:
        text = tradedate.strftime("%Y%m%d")
        return {"startdate": text, "enddate": text}
    return {}


def _market_from_params(params: dict[str, Any]) -> str | None:
    values = params.get("symbols") or params.get("symbol")
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, list) or not values:
        return None
    value = str(values[0]).upper()
    return "sh" if value.endswith(".SH") or value.startswith(("600", "601", "603", "605", "688", "689")) else "sz" if value.endswith(".SZ") or value.startswith(("000", "001", "002", "003", "300", "301", "302")) else None


numcat_extended_provider = NumCatExtendedProvider()

__all__ = [
    "DOCUMENTED_APINAMES", "TYPED_PROVIDER_APINAMES", "MISSING_TYPED_APINAMES",
    "NumCatExtendedProvider", "numcat_extended_provider",
]
