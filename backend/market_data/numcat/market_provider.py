"""Small NumCat market-data DTO adapter used by existing collectors.

The OpenAPI contract returns ``fields`` plus two-dimensional ``items``. This
module translates that shape once, at the provider boundary. It deliberately
does not write raw responses to PostgreSQL; callers may use the short-lived
gateway cache and persist only their existing decision snapshots.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from .gateway import NumCatGatewayError, numcat_gateway


STOCK_BASIC_FIELDS = "code,symbol,name,area,industry,fullname,enname,cnspell,market,exchange,curr_type,list_status,list_date,delist_date,is_hs,act_name,act_ent_type"
DAILY_FIELDS = "tradedate,symbol,name,open,high,low,close,pre_close,change,pct_chg,vol,amount"
# Valuation and financial fields are provided by separate endpoints. Keeping
# this list equal to the documented screening contract avoids silent nulls.
SCREENING_FIELDS = (
    "tradedate,symbol,name,open,high,low,close,pre_close,change,pct_chg,vol,amount,"
    "up_limit,down_limit,is_st,turnover_rate_f,volume_ratio,free_float_mv,circ_mv,total_mv,"
    "type,is_break,limit_times,pre_limit_times,fd_amount,pre_fd_amount,prev_fd_amount,"
    "fd_to_turnover,fd_to_yesterday,first_time,last_time,m_price,auc_pct_chg,auc_net_amount,"
    "auc_amt,auc_vol,auc_turnover,ztwme,ztwme20,fa_0915,fa_0920f,fa_0925l,"
    "theme_names_xgb,theme_names_kpl,theme_names_jygs,reason_xgb,reason_kpl,reason_jygs,"
    "reason_main_kpl,reason_main_jygs,hot_ths_normal_rank,hot_dfcf_biaos_rank,"
    "hot_xq_resou_rank,hot_tdx_resou_rank"
)
AUCTION_FIELDS = (
    "tradedate,symbol,name,m_price,auc_pct_chg,open_bid_pct,auc_amt,auc_vol,auc_vol_ratio,"
    "auc_to_pre_vol_pct,auc_to_pre_auc_vol_ratio,um_vol,um_side,auc_turnover_rate,auc_turnover"
)
MINUTE_FIELDS = "tradedate,symbol,trademin,time,open,high,low,close,vol,amount,vwap"
VALUATION_FIELDS = "tradedate,symbol,name,code,close,turnover_rate,turnover_rate_f,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,free_share,free_mv,total_mv,circ_mv"
STK_FACTOR_FIELDS = "tradedate,symbol,name,open,open_hfq,open_qfq,high,high_hfq,high_qfq,low,low_hfq,low_qfq,close,close_hfq,close_qfq,pre_close,change,pct_chg,vol,amount,turnover_rate,turnover_rate_f,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,free_share,total_mv,circ_mv,adj_factor"
FINANCE_INDICATOR_FIELDS = "symbol,report_date,announce_date,revision_no,version_ambiguous,eps,roe,roa,debt_to_assets,update_flag"
THEME_DAILY_JX_FIELDS = "tradedate,theme_symbol,theme_name,level,p_symbol,pct_chg,strength"
THEME_FUND_FLOW_FIELDS = "tradedate,trademin,theme_symbol,theme_name,level,p_symbol,main_net_amount,main_buy_amount,main_sell_amount"
THEME_MEMBERS_FIELDS = "theme_symbol,symbols"
THEME_AUCTION_FIELDS = (
    "source_day,group,group_rank,theme_symbol,theme_name,"
    "bid_volume_burst,abnormal_amount,bid_volume,main_net_amount"
)
FENGK_FIELDS = (
    "tradedate,symbol,name,rank,strength,pct_chg,main_net_amount,"
    "main_buy_amount,main_sell_amount,signal_time,selected_themes"
)
THEME_LIBRARY_FIELDS = "theme_id,name"
THEME_REASON_FIELDS = "tradedate,symbol,name,source,reason"
HOT_STOCK_LIVE_FIELDS = "s,n,pc"
HOT_STOCK_HISTORY_FIELDS = "s,rank,tradedate,type"
THEME_STYLE_FIELDS = (
    "tradedate,symbol,name,type,open,high,low,close,pre_close,"
    "change,pct_chg,vol,amount,servertime"
)
NEWS_FIELDS = "time_label,source_name,title,display_at,summary,url,published_at"
ANNOUNCEMENT_FIELDS = "symbol,event_date,title,summary,announcement_type,content_url"
FUND_FLOW_FIELDS = (
    "tradedate,symbol,name,main_net_amount,main_buy_amount,main_sell_amount,"
    "auction_main_net_amount,auction_main_buy_amount,auction_main_sell_amount"
)
MARKET_EMOTION_FIELDS = (
    "tradedate,s2,s3,s4,s5,s6,s7,s8,s9,s10,am,am_diff,mf_main,mf_auction_main,"
    "u,u5,yes_u5,promotion_candidate_count,u6,u12,d3,fp108,l1,l1_amount,l2,l2_amount,"
    "l3,l3_amount,l2up,l2up_amount,yes_l2up_amount,l3up,l3up_amount,yes_l3up_amount,"
    "l4up,l4up_amount,yes_l4up_amount,l17,l21,l22,l2up_rate,ztwme,ztwme20,"
    "ztwme_count,owfd_overnight,owfd_0920,owfd_0925,owfd_0925_count,deep_retrace_count,"
    "am_pred,am_pred_pct,am_pred_diff"
)
LIMIT_POOL_FIELDS = (
    "tradedate,symbol,name,type,is_break,limit_times,pre_limit_times,fd_amount,"
    "first_time,last_time,open_times,limit_detail,limit_type,close,pct_chg,amount"
)
DRAGON_STOCK_FIELDS = (
    "tradedate,symbol,name,close,pct_chg,turnover_rate,amount,lhb_sell,lhb_buy,"
    "lhb_amount,net_amount,net_rate,amount_rate,float_value,reason"
)
DRAGON_SEAT_FIELDS = (
    "tradedate,symbol,name,reason,side,seat_name,buy,buy_rate,sell,sell_rate,net_buy"
)
MARGIN_SUMMARY_FIELDS = (
    "tradedate,exchange,financing_balance,financing_buy_amount,financing_repayment_amount,"
    "securities_lending_balance,securities_lending_sell_quantity,"
    "securities_lending_outstanding_quantity,margin_balance"
)
MARGIN_DETAIL_FIELDS = (
    "tradedate,symbol,name,exchange,financing_balance,financing_buy_amount,"
    "financing_repayment_amount,securities_lending_balance,securities_lending_sell_quantity,"
    "securities_lending_outstanding_quantity,securities_lending_repayment_quantity,margin_balance"
)
MARGIN_SECURITIES_FIELDS = "tradedate,symbol,name,exchange"
AUCTION_DETAIL_FIELDS = (
    "tradedate,symbol,time,m_price,auc_pct_chg,auc_vol,auc_amt,um_vol,um_side,"
    "auc_turnover_rate,auc_turnover,auc_to_pre_vol_pct"
)


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data: Any = payload.get("data")
    if not isinstance(data, dict):
        result = payload.get("result")
        data = result.get("data") if isinstance(result, dict) else result
    if not isinstance(data, dict):
        return []
    fields = data.get("fields") or []
    items = data.get("items")
    if items is None:
        items = data.get("rows") or data.get("data") or []
    if not isinstance(items, list):
        return []
    if not isinstance(fields, list) or not fields:
        return [dict(item) for item in items if isinstance(item, dict)]
    names = [str(item) for item in fields]
    output = []
    for item in items:
        if isinstance(item, dict):
            output.append(item)
        elif isinstance(item, (list, tuple)):
            output.append({key: item[index] if index < len(item) else None for index, key in enumerate(names)})
    return output


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the provider data object for nested non-tabular contracts."""
    data: Any = payload.get("data")
    if isinstance(data, dict):
        return data
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("data"), dict):
        return result["data"]
    return {}


def _symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text.split(".", 1)[0].zfill(6) if text else ""


def _date_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text[:10] or None


class NumCatMarketProvider:
    """DTO-facing NumCat provider for the data already used by this app."""

    name = "numcat"

    @property
    def configured(self) -> bool:
        return numcat_gateway.configured

    async def daily(self, symbol: str, *, days: int = 365) -> list[dict[str, Any]]:
        code = _symbol(symbol)
        params = {"symbols": code, "recentdays": min(max(int(days), 1), 800)}
        try:
            payload = await numcat_gateway.query(
                "stk_factor_pro",
                fields=STK_FACTOR_FIELDS,
                params=params,
                market=_market(code),
                cache_ttl=900,
                affinity_key=f"factor-daily:{code}",
            )
        except NumCatGatewayError:
            payload = await numcat_gateway.query(
                "daily",
                fields=DAILY_FIELDS,
                params=params,
                market=_market(code),
                cache_ttl=900,
                affinity_key=f"daily:{code}",
            )
        output = []
        for row in _rows(payload):
            if _symbol(row.get("symbol")) not in {"", code}:
                continue
            output.append({
                "trade_date": _date_text(row.get("tradedate")),
                "name": row.get("name"),
                "open": row.get("open"), "close": row.get("close"),
                "high": row.get("high"), "low": row.get("low"),
                # NumCat documents vol as hands; the app contract uses shares.
                "volume": _multiply(row.get("vol"), 100),
                "amount": row.get("amount"), "change_pct": row.get("pct_chg"),
                "change_amount": row.get("change"), "previous_close": row.get("pre_close"),
                "turnover": row.get("turnover_rate_f"),
                "pe_ttm": row.get("pe_ttm"), "pb": row.get("pb"),
                "volume_ratio": row.get("volume_ratio"),
            })
        return sorted([item for item in output if item.get("trade_date")], key=lambda item: item["trade_date"])

    async def stock_basic(
        self,
        symbols: list[str] | None = None,
        *,
        market: str | None = None,
        industry: str | None = None,
        list_status: str = "L",
    ) -> list[dict[str, Any]]:
        normalized_status = str(list_status or "L").strip().upper()
        if normalized_status not in {"L", "D", "P"}:
            raise ValueError("list_status must be L, D or P")
        params: dict[str, Any] = {"list_status": normalized_status}
        if symbols:
            params["symbols"] = ",".join(_symbol(item) for item in symbols if _symbol(item))
        if market:
            params["market"] = str(market)
        if industry:
            params["industry"] = str(industry)
        payload = await numcat_gateway.query(
            "stockbasic",
            fields=STOCK_BASIC_FIELDS,
            params=params,
            cache_ttl=3600,
            affinity_key=(
                f"stockbasic:{normalized_status}:{params.get('symbols', 'all')}:"
                f"{market or ''}:{industry or ''}"
            ),
        )
        output = []
        for row in _rows(payload):
            code = _symbol(row.get("symbol") or row.get("code"))
            if not code or len(code) != 6 or not code.isdigit():
                continue
            output.append({
                "code": code,
                "name": str(row.get("name") or ""),
                "area": row.get("area"),
                "industry": row.get("industry"),
                "market": row.get("market"),
                "exchange": row.get("exchange"),
                "list_status": row.get("list_status"),
                "list_date": _date_text(row.get("list_date")),
                "delist_date": _date_text(row.get("delist_date")),
                "is_hs": row.get("is_hs"),
                "source": "numcat_stockbasic",
            })
        return output

    async def security_directory(self) -> list[dict[str, Any]]:
        """Return listed, delisted and suspended securities as one directory."""
        groups = await asyncio.gather(*(
            self.stock_basic(list_status=status)
            for status in ("L", "D", "P")
        ))
        merged: dict[str, dict[str, Any]] = {}
        for rows in groups:
            for row in rows:
                code = str(row.get("code") or "")
                if code:
                    merged[code] = row
        return list(merged.values())

    async def valuation(
        self,
        symbols: list[str] | None = None,
        *,
        tradedate: date | None = None,
        startdate: date | None = None,
        enddate: date | None = None,
        recentdays: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbols:
            params["symbols"] = ",".join(_symbol(item) for item in symbols if _symbol(item))
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        elif startdate:
            params["startdate"] = startdate.strftime("%Y%m%d")
            if enddate:
                params["enddate"] = enddate.strftime("%Y%m%d")
        elif recentdays:
            params["recentdays"] = min(max(int(recentdays), 1), 800)
        payload = await numcat_gateway.query(
            "valuation",
            fields=VALUATION_FIELDS,
            params=params,
            cache_ttl=900 if not startdate else 3600,
            affinity_key=f"valuation:{params.get('symbols', 'all')}:{params.get('tradedate') or params.get('startdate') or params.get('recentdays', 'latest')}",
        )
        output = []
        for row in _rows(payload):
            code = _symbol(row.get("symbol") or row.get("code"))
            if not code:
                continue
            output.append({
                "trade_date": _date_text(row.get("tradedate")),
                "code": code,
                "name": row.get("name"),
                "close": row.get("close"),
                "turnover_rate": row.get("turnover_rate"),
                "turnover_rate_f": row.get("turnover_rate_f"),
                "volume_ratio": row.get("volume_ratio"),
                "pe": row.get("pe"),
                "pe_ttm": row.get("pe_ttm"),
                "pb": row.get("pb"),
                "ps": row.get("ps"),
                "ps_ttm": row.get("ps_ttm"),
                "dividend_yield": row.get("dv_ttm") if row.get("dv_ttm") is not None else row.get("dv_ratio"),
                "total_share": row.get("total_share"),
                "float_share": row.get("float_share"),
                "free_share": row.get("free_share"),
                "free_market_cap": row.get("free_mv"),
                "total_market_cap": row.get("total_mv"),
                "circulating_market_cap": row.get("circ_mv"),
                "source": "numcat_valuation",
            })
        return output

    async def factor_pro(
        self,
        symbol: str,
        *,
        tradedate: date | None = None,
        recentdays: int | None = None,
    ) -> list[dict[str, Any]]:
        code = _symbol(symbol)
        params: dict[str, Any] = {"symbols": code}
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        elif recentdays:
            params["recentdays"] = min(max(int(recentdays), 1), 800)
        payload = await numcat_gateway.query(
            "stk_factor_pro",
            fields=STK_FACTOR_FIELDS,
            params=params,
            market=_market(code),
            cache_ttl=900,
            affinity_key=f"factor-pro:{code}:{params.get('tradedate') or params.get('recentdays', 'latest')}",
        )
        return _rows(payload)

    async def finance_indicator(
        self,
        symbols: list[str] | None = None,
        *,
        period: str | None = None,
        as_of: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "version": "latest",
            "limit": min(max(int(limit), 1), 2000),
        }
        if symbols:
            params["symbols"] = ",".join(_symbol(item) for item in symbols if _symbol(item))
        if period:
            params["period"] = str(period).replace("-", "")[:8]
        if as_of:
            params["as_of"] = str(as_of)
        payload = await numcat_gateway.query(
            "finance_indicator",
            fields=FINANCE_INDICATOR_FIELDS,
            params=params,
            cache_ttl=3600,
            affinity_key=f"finance-indicator:{params.get('symbols', 'all')}:{period or as_of or 'latest'}",
        )
        output = []
        for row in _rows(payload):
            code = _symbol(row.get("symbol"))
            if code:
                output.append({
                    "code": code,
                    "report_date": _date_text(row.get("report_date")),
                    "announce_date": _date_text(row.get("announce_date")),
                    "revision_no": row.get("revision_no"),
                    "version_ambiguous": row.get("version_ambiguous"),
                    "eps": row.get("eps"),
                    "roe": row.get("roe"),
                    "roa": row.get("roa"),
                    "debt_to_assets": row.get("debt_to_assets"),
                    "update_flag": row.get("update_flag"),
                    "source": "numcat_finance_indicator",
                })
        return output

    async def theme_daily(
        self,
        *,
        level: str = "parent",
        symbols: list[str] | None = None,
        recentdays: int = 5,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "level": level,
            "recentdays": min(max(int(recentdays), 1), 800),
            "limit": 6000,
        }
        if symbols:
            params["theme_symbols"] = ",".join(str(item) for item in symbols if item)
        payload = await numcat_gateway.query(
            "themedaily_jx",
            fields=THEME_DAILY_JX_FIELDS,
            params=params,
            cache_ttl=300,
            affinity_key=f"themedaily:{level}:{params.get('theme_symbols', 'all')}:{recentdays}",
        )
        return _rows(payload)

    async def theme_fund_flow(
        self,
        *,
        symbols: list[str] | None = None,
        start_minute: str | None = None,
        end_minute: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "level": "all",
            "order_by": "main_net_amount",
            "order_dir": "desc",
            "limit": 6000,
        }
        if symbols:
            params["theme_symbols"] = ",".join(str(item) for item in symbols if item)
        if start_minute:
            params["start_trademin"] = str(start_minute)
        if end_minute:
            params["end_trademin"] = str(end_minute)
        payload = await numcat_gateway.query(
            "themefundflow_jx",
            fields=THEME_FUND_FLOW_FIELDS,
            params=params,
            cache_ttl=30,
            affinity_key=f"themefundflow:{params.get('theme_symbols', 'all')}:{start_minute or ''}:{end_minute or ''}",
        )
        return _rows(payload)

    async def theme_members(
        self,
        *,
        theme_symbols: list[str] | None = None,
        symbols: list[str] | None = None,
        level: str = "parent",
        tag: str | None = None,
        tradedate: date | None = None,
    ) -> list[dict[str, Any]]:
        """Return the official selected-theme constituent groups.

        ``thememembers_jx`` is reused by the vendor for the selected pool,
        authentic stocks and the close-time dragon ranking.  The tag is kept
        explicit at this boundary so callers cannot confuse the three lists.
        """
        if level not in {"parent", "child"}:
            raise ValueError("theme member level must be parent or child")
        if tag not in {None, "authentic", "long"}:
            raise ValueError("theme member tag must be authentic or long")
        params: dict[str, Any] = {"level": level}
        if theme_symbols:
            params["theme_symbols"] = ",".join(str(item) for item in theme_symbols if item)
        if symbols:
            params["symbols"] = ",".join(_symbol(item) for item in symbols if _symbol(item))
        if tag:
            params["tag"] = tag
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        payload = await numcat_gateway.query(
            "thememembers_jx",
            fields=THEME_MEMBERS_FIELDS,
            params=params,
            cache_ttl=900 if tag == "long" or tradedate else 300,
            affinity_key=f"theme-members:{tag or 'selected'}:{level}:{params.get('theme_symbols', 'all')}:{params.get('tradedate', 'latest')}",
        )
        output = []
        for row in _rows(payload):
            theme_symbol = str(row.get("theme_symbol") or "").strip()
            raw_symbols = row.get("symbols")
            if isinstance(raw_symbols, str):
                raw_symbols = raw_symbols.split(",")
            if not isinstance(raw_symbols, list):
                raw_symbols = []
            member_codes = []
            for item in raw_symbols:
                try:
                    code = _symbol(item)
                    if len(code) == 6 and code.isdigit():
                        member_codes.append(code)
                except (TypeError, ValueError):
                    continue
            if theme_symbol:
                output.append({
                    "theme_symbol": theme_symbol,
                    "symbols": list(dict.fromkeys(member_codes)),
                    "tag": tag or "selected",
                    "level": level,
                    "trade_date": _date_text(row.get("tradedate")) if row.get("tradedate") else None,
                    "updated_at": _data(payload).get("updated_at"),
                    "source": "numcat_thememembers_jx",
                })
        return output

    async def theme_auction(
        self,
        *,
        groups: list[str] | None = None,
        theme_symbols: list[str] | None = None,
        tradedate: date | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if groups:
            params["groups"] = ",".join(str(item) for item in groups if item)
        if theme_symbols:
            params["theme_symbols"] = ",".join(str(item) for item in theme_symbols if item)
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        payload = await numcat_gateway.query(
            "theme_auc_kp",
            fields=THEME_AUCTION_FIELDS,
            params=params,
            cache_ttl=30 if tradedate is None else 900,
            affinity_key=f"theme-auction:{params.get('theme_symbols', 'all')}:{params.get('tradedate', 'latest')}:{params.get('groups', 'all')}",
        )
        return [
            {
                **row,
                "source_day": _date_text(row.get("source_day") or row.get("tradedate")),
                "theme_symbol": str(row.get("theme_symbol") or ""),
                "theme_name": str(row.get("theme_name") or row.get("name") or ""),
                "source": "numcat_theme_auc_kp",
            }
            for row in _rows(payload)
            if row.get("theme_symbol") or row.get("theme_name")
        ]

    async def strongest_fengkou(
        self,
        *,
        symbols: list[str] | None = None,
        tradedate: date | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": min(max(int(limit), 1), 100)}
        if symbols:
            params["symbols"] = ",".join(_symbol(item) for item in symbols if _symbol(item))
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        payload = await numcat_gateway.query(
            "fengk_kp",
            fields=FENGK_FIELDS,
            params=params,
            cache_ttl=60 if tradedate is None else 900,
            affinity_key=f"fengkou:{params.get('symbols', 'all')}:{params.get('tradedate', 'latest')}:{limit}",
        )
        output = []
        for row in _rows(payload):
            code = _symbol(row.get("symbol"))
            if not code:
                continue
            output.append({
                "trade_date": _date_text(row.get("tradedate")),
                "code": code,
                "name": str(row.get("name") or ""),
                "rank": _int_or_none(row.get("rank")),
                "strength": row.get("strength"),
                "change_pct": row.get("pct_chg"),
                "main_net_amount": row.get("main_net_amount"),
                "main_buy_amount": row.get("main_buy_amount"),
                "main_sell_amount": row.get("main_sell_amount"),
                "signal_time": row.get("signal_time"),
                "selected_themes": row.get("selected_themes"),
                "source": "numcat_fengk_kp",
            })
        return output

    async def hot_stock(
        self,
        *,
        ranking_type: str = "xq&resou",
        tradedate: date | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"type": ranking_type}
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        fields = HOT_STOCK_HISTORY_FIELDS if tradedate else HOT_STOCK_LIVE_FIELDS
        payload = await numcat_gateway.query(
            "hotstock",
            fields=fields,
            params=params,
            cache_ttl=60 if tradedate is None else 900,
            affinity_key=f"hotstock:{ranking_type}:{params.get('tradedate', 'latest')}:{limit}",
        )
        output = []
        for index, row in enumerate(_rows(payload), start=1):
            code = _symbol(row.get("s") or row.get("symbol"))
            if not code:
                continue
            output.append({
                "code": code,
                "name": str(row.get("n") or row.get("name") or ""),
                "rank": _int_or_none(row.get("rank")) or index,
                "change_pct": row.get("pc") if row.get("pc") is not None else row.get("pct_chg"),
                "trade_date": _date_text(row.get("tradedate")),
                "type": row.get("type") or ranking_type,
                "source": "numcat_hotstock",
            })
        return output[:min(max(int(limit), 1), 100)]

    async def theme_library(self, *, theme_ids: list[str] | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if theme_ids:
            params["theme_ids"] = ",".join(str(item) for item in theme_ids if item)
        payload = await numcat_gateway.query(
            "theme_lib_kp",
            fields=THEME_LIBRARY_FIELDS,
            params=params,
            cache_ttl=3600,
            affinity_key=f"theme-library:{params.get('theme_ids', 'all')}",
        )
        return [
            {
                "theme_id": str(row.get("theme_id") or row.get("id") or ""),
                "name": str(row.get("name") or ""),
                "source": "numcat_theme_lib_kp",
            }
            for row in _rows(payload)
            if row.get("theme_id") is not None or row.get("id") is not None
        ]

    async def theme_library_detail(self, theme_id: str) -> dict[str, Any]:
        identifier = str(theme_id or "").strip()
        if not identifier:
            raise ValueError("theme_id is required")
        payload = await numcat_gateway.query(
            "theme_lib_detail_kp",
            params={"theme_id": identifier},
            cache_ttl=3600,
            affinity_key=f"theme-library-detail:{identifier}",
        )
        data = _data(payload)
        return {**data, "theme_id": str(data.get("theme_id") or identifier), "source": "numcat_theme_lib_detail_kp"}

    async def theme_reason(
        self,
        *,
        source: str = "xgb",
        symbols: list[str] | None = None,
        tradedate: date | None = None,
        recentdays: int | None = 5,
    ) -> list[dict[str, Any]]:
        if source not in {"xgb", "jygs"}:
            raise ValueError("theme reason source must be xgb or jygs")
        params: dict[str, Any] = {"source": source}
        if symbols:
            params["symbols"] = ",".join(str(item) for item in symbols if item)
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        elif recentdays:
            params["recentdays"] = min(max(int(recentdays), 1), 30)
        payload = await numcat_gateway.query(
            "theme_reason",
            fields=THEME_REASON_FIELDS,
            params=params,
            cache_ttl=300 if tradedate is None else 900,
            affinity_key=f"theme-reason:{source}:{params.get('symbols', 'all')}:{params.get('tradedate') or params.get('recentdays', 'latest')}",
        )
        return [
            {
                **row,
                "trade_date": _date_text(row.get("tradedate")),
                "theme_symbol": str(row.get("symbol") or ""),
                "reason_source": row.get("source") or source,
                "source": "numcat_theme_reason",
            }
            for row in _rows(payload)
        ]

    async def theme_style_daily(
        self,
        *,
        recentdays: int = 5,
        tradedate: date | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"type": "fg"}
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        else:
            params["startdate"] = (date.today() - timedelta(days=max(int(recentdays), 1) * 2 + 8)).strftime("%Y%m%d")
        payload = await numcat_gateway.query(
            "theme_daily",
            fields=THEME_STYLE_FIELDS,
            params=params,
            cache_ttl=300 if tradedate is None else 900,
            affinity_key=f"theme-style:{params.get('tradedate') or params.get('startdate')}",
        )
        return [{**row, "trade_date": _date_text(row.get("tradedate")), "source": "numcat_theme_style_daily"} for row in _rows(payload)]

    async def theme_stat_daily(
        self,
        *,
        recentdays: int = 5,
        tradedate: date | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"type": "tj"}
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        else:
            params["startdate"] = (date.today() - timedelta(days=max(int(recentdays), 1) * 2 + 8)).strftime("%Y%m%d")
        payload = await numcat_gateway.query(
            "theme_daily",
            fields=THEME_STYLE_FIELDS,
            params=params,
            cache_ttl=300 if tradedate is None else 900,
            affinity_key=f"theme-stat:{params.get('tradedate') or params.get('startdate')}",
        )
        return [{**row, "trade_date": _date_text(row.get("tradedate")), "source": "numcat_theme_stat_daily"} for row in _rows(payload)]

    async def news(self, *, keyword: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "view": "time",
            "limit": min(max(int(limit), 1), 500),
        }
        if keyword:
            params["keyword"] = str(keyword)
        payload = await numcat_gateway.query(
            "news",
            fields=NEWS_FIELDS,
            params=params,
            cache_ttl=300,
            affinity_key=f"news:{keyword or 'all'}:{limit}",
        )
        return _rows(payload)

    async def announcements(
        self,
        symbols: list[str],
        *,
        startdate: date | None = None,
        enddate: date | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        codes = [_symbol(item) for item in symbols if _symbol(item)]
        if not codes:
            return []
        params: dict[str, Any] = {
            "symbols": ",".join(codes),
            "limit": min(max(int(limit), 1), 2000),
            "version": "latest",
        }
        if startdate:
            params["startdate"] = startdate.strftime("%Y%m%d")
        if enddate:
            params["enddate"] = enddate.strftime("%Y%m%d")
        payload = await numcat_gateway.query(
            "finance_announcement",
            fields=ANNOUNCEMENT_FIELDS,
            params=params,
            cache_ttl=900,
            affinity_key=f"announcements:{','.join(codes)}:{params.get('startdate', '')}:{params.get('enddate', '')}",
        )
        return _rows(payload)

    async def screening(
        self,
        *,
        symbols: list[str] | None = None,
        tradedate: date | None = None,
        enrichment_limit: int = 500,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbols:
            params["symbols"] = ",".join(_symbol(item) for item in symbols if _symbol(item))
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        else:
            params["recentdays"] = 1
        payload = await numcat_gateway.query(
            "screening", fields=SCREENING_FIELDS, params=params,
            market=None, cache_ttl=4,
            affinity_key=f"screening:{params.get('symbols', 'all')}:{params.get('tradedate') or 'latest'}",
        )
        output = [
            normalized
            for row in _rows(payload)
            if (normalized := self._screening_row(row)) is not None
        ]
        # The full-market screening payload already owns quote, turnover and
        # volume-ratio fields. Only enrich the candidates that can reach the
        # picker, avoiding another full-market master and valuation download.
        ranked = sorted(
            output,
            key=lambda item: (
                _number(item.get("volume_ratio")),
                _number(item.get("change_pct")),
                _number(item.get("turnover")),
            ),
            reverse=True,
        )
        bounded_limit = min(max(int(enrichment_limit), 0), 1000)
        enrich_codes = (
            [_symbol(item) for item in symbols if _symbol(item)]
            if symbols
            else [str(item["code"]) for item in ranked[:bounded_limit]]
        )
        if not enrich_codes:
            return output
        basic_result, valuation_result = await asyncio.gather(
            self.stock_basic(enrich_codes),
            self.valuation(enrich_codes, tradedate=tradedate),
            return_exceptions=True,
        )
        basic_by_code = {
            str(item.get("code") or ""): item
            for item in ([] if isinstance(basic_result, Exception) else basic_result)
        }
        valuation_by_code = {
            str(item.get("code") or ""): item
            for item in ([] if isinstance(valuation_result, Exception) else valuation_result)
        }
        for normalized in output:
            code = str(normalized["code"])
            basic = basic_by_code.get(code) or {}
            valuation = valuation_by_code.get(code) or {}
            normalized.update({
                "sector": str(basic.get("industry") or "").strip(),
                "market": basic.get("market"),
                "exchange": basic.get("exchange"),
                "list_date": basic.get("list_date"),
                "pe": valuation.get("pe_ttm") if valuation.get("pe_ttm") is not None else valuation.get("pe"),
                "pb": valuation.get("pb"),
                "volume_ratio": normalized.get("volume_ratio") if normalized.get("volume_ratio") is not None else valuation.get("volume_ratio"),
                "market_cap": normalized.get("market_cap") if normalized.get("market_cap") is not None else valuation.get("total_market_cap"),
                "circulating_market_cap": normalized.get("circulating_market_cap") if normalized.get("circulating_market_cap") is not None else valuation.get("circulating_market_cap"),
                "data_sources": {
                    "quote": "numcat_screening",
                    "security_master": basic.get("source"),
                    "valuation": valuation.get("source"),
                },
            })
        return output

    @staticmethod
    def _screening_row(row: dict[str, Any]) -> dict[str, Any] | None:
        code = _symbol(row.get("symbol"))
        try:
            price = float(row.get("close"))
        except (TypeError, ValueError):
            return None
        if not code or len(code) != 6 or not code.isdigit() or price <= 0:
            return None
        return {
            "code": code,
            "name": str(row.get("name") or ""),
            "price": price,
            "change_pct": row.get("pct_chg"),
            "change_amount": row.get("change"),
            "volume": _multiply(row.get("vol"), 100),
            "amount": row.get("amount"),
            "turnover": row.get("turnover_rate_f"),
            "pe": None,
            "pb": None,
            "roe": None,
            "volume_ratio": row.get("volume_ratio"),
            "market_cap": row.get("total_mv"),
            "circulating_market_cap": row.get("circ_mv"),
            "free_float_market_cap": row.get("free_float_mv"),
            "sector": "",
            "main_net_inflow": None,
            "main_net_inflow_pct": None,
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "previous_close": row.get("pre_close"),
            "quote_timestamp": row.get("updated_at") or row.get("quote_time"),
            "is_st": row.get("is_st"),
            "is_break": row.get("is_break"),
            "limit_times": row.get("limit_times"),
            "fd_amount": row.get("fd_amount"),
            "auc_pct_chg": row.get("auc_pct_chg"),
            "auc_amt": row.get("auc_amt"),
            "auc_vol": row.get("auc_vol"),
            "hot_rank": row.get("hot_ths_normal_rank") or row.get("hot_dfcf_biaos_rank"),
            "themes": row.get("theme_names_kpl") or row.get("theme_names_xgb") or row.get("theme_names_jygs"),
            "limit_reason": row.get("reason_main_kpl") or row.get("reason_main_jygs") or row.get("reason_kpl") or row.get("reason_xgb"),
            "trade_date": _date_text(row.get("tradedate")),
            "quote_source": "numcat",
        }

    async def auction(self, symbols: list[str], *, tradedate: date | None = None) -> list[dict[str, Any]]:
        codes = [_symbol(item) for item in symbols if _symbol(item)]
        if not codes:
            return []
        params: dict[str, Any] = {"symbols": ",".join(codes)}
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        payload = await numcat_gateway.query(
            "daily_auc", fields=AUCTION_FIELDS, params=params,
            market=None, cache_ttl=4, affinity_key=f"auction:{','.join(codes)}:{params.get('tradedate', 'latest')}",
        )
        return [
            {
                **row,
                "tradedate": _date_text(row.get("tradedate")),
                "auc_vol": _multiply(row.get("auc_vol"), 100),
                "um_vol": _multiply(row.get("um_vol"), 100),
            }
            for row in _rows(payload)
        ]

    async def minute(self, symbol: str, *, tradedate: date | None = None, period: str = "1m") -> list[dict[str, Any]]:
        code = _symbol(symbol)
        normalized_period = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h"}.get(period, "1m")
        params: dict[str, Any] = {"symbols": code, "period": normalized_period}
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        payload = await numcat_gateway.query(
            "minute", fields=MINUTE_FIELDS, params=params,
            market=_market(code), cache_ttl=30, affinity_key=f"minute:{code}:{period}:{params.get('tradedate', 'latest')}",
        )
        return [
            {**row, "tradedate": _date_text(row.get("tradedate"))}
            for row in _rows(payload)
        ]

    async def stock_fund_flow(self, symbols: list[str], *, tradedate: date | None = None, days: int = 260) -> list[dict[str, Any]]:
        codes = [_symbol(item) for item in symbols if _symbol(item)]
        if not codes:
            return []
        params: dict[str, Any] = {"symbols": ",".join(codes)}
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        else:
            params["recentdays"] = min(max(int(days), 1), 800)
        payload = await numcat_gateway.query(
            "fundflow_kp", fields=FUND_FLOW_FIELDS, params=params,
            market=None, cache_ttl=30, affinity_key=f"fundflow:{','.join(codes)}:{params.get('tradedate', 'latest')}",
        )
        return [
            {**row, "tradedate": _date_text(row.get("tradedate"))}
            for row in _rows(payload)
        ]

    async def market_emotion(
        self,
        *,
        tradedate: date | None = None,
        recentdays: int = 30,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        else:
            params["recentdays"] = min(max(int(recentdays), 1), 800)
        payload = await numcat_gateway.query(
            "emoindic_daily",
            fields=MARKET_EMOTION_FIELDS,
            params=params,
            cache_ttl=60 if tradedate is None else 900,
            affinity_key=f"market-emotion:{params.get('tradedate') or params.get('recentdays')}",
        )
        output = []
        for row in _rows(payload):
            up = _int_or_none(row.get("s2"))
            down = _int_or_none(row.get("s6"))
            flat = _int_or_none(row.get("s10"))
            stock_count = up + down + flat if None not in (up, down, flat) else None
            output.append({
                "trade_date": _date_text(row.get("tradedate")),
                "up_count": up,
                "down_count": down,
                "flat_count": flat,
                "stock_count": stock_count,
                "up_7pct_count": row.get("s3"),
                "up_3_to_7pct_count": row.get("s4"),
                "up_0_to_3pct_count": row.get("s5"),
                "down_0_to_3pct_count": row.get("s7"),
                "down_3_to_7pct_count": row.get("s8"),
                "down_7pct_count": row.get("s9"),
                "market_amount": row.get("am"),
                "market_amount_change": row.get("am_diff"),
                "market_amount_forecast": row.get("am_pred"),
                "market_amount_forecast_change_pct": row.get("am_pred_pct"),
                "market_amount_forecast_change": row.get("am_pred_diff"),
                "main_net_inflow": row.get("mf_main"),
                "auction_main_net_inflow": row.get("mf_auction_main"),
                "touched_limit_up_count": row.get("u"),
                "limit_up_count": row.get("u5"),
                "yesterday_limit_up_count": row.get("yes_u5"),
                "promotion_candidate_count": row.get("promotion_candidate_count"),
                "one_price_limit_up_count": row.get("u6"),
                "failed_limit_count": row.get("u12"),
                "failed_limit_rate": row.get("fp108"),
                "limit_down_count": row.get("d3"),
                "first_board_amount": row.get("l1_amount"),
                "second_board_amount": row.get("l2_amount"),
                "third_board_amount": row.get("l3_amount"),
                "second_board_or_higher_count": row.get("l2up"),
                "second_board_or_higher_amount": row.get("l2up_amount"),
                "yesterday_second_board_or_higher_amount": row.get("yes_l2up_amount"),
                "third_board_or_higher_count": row.get("l3up"),
                "third_board_or_higher_amount": row.get("l3up_amount"),
                "yesterday_third_board_or_higher_amount": row.get("yes_l3up_amount"),
                "fourth_board_or_higher_count": row.get("l4up"),
                "fourth_board_or_higher_amount": row.get("l4up_amount"),
                "yesterday_fourth_board_or_higher_amount": row.get("yes_l4up_amount"),
                "max_streak_height": row.get("l17"),
                "first_board_count": row.get("l1"),
                "second_board_count": row.get("l2"),
                "third_board_count": row.get("l3"),
                "promotion_rate_1_to_2": row.get("l21"),
                "promotion_rate": row.get("l22"),
                "promotion_rate_2_plus": row.get("l2up_rate"),
                "limit_up_order_amount": row.get("ztwme"),
                "limit_up_order_amount_after_0920": row.get("ztwme20"),
                "limit_up_order_count": row.get("ztwme_count"),
                "overnight_order_amount": row.get("owfd_overnight"),
                "order_amount_0920": row.get("owfd_0920"),
                "order_amount_0925": row.get("owfd_0925"),
                "order_count_0925": row.get("owfd_0925_count"),
                "deep_retrace_count": row.get("deep_retrace_count"),
                "source": "numcat_emoindic_daily",
            })
        return [item for item in output if item.get("trade_date")]

    async def limit_pool(
        self,
        pool_type: str,
        *,
        tradedate: date | None = None,
        recentdays: int | None = None,
    ) -> dict[str, Any]:
        normalized_type = str(pool_type or "").strip().lower()
        if normalized_type not in {"u", "d", "ub", "db", "bu", "bd"}:
            raise ValueError("unsupported NumCat limit-pool type")
        params: dict[str, Any] = {"type": normalized_type}
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        elif recentdays:
            params["recentdays"] = min(max(int(recentdays), 1), 800)
        payload = await numcat_gateway.query(
            "limit_pool",
            fields=LIMIT_POOL_FIELDS,
            params=params,
            cache_ttl=30 if tradedate is None else 900,
            affinity_key=f"limit-pool:{normalized_type}:{params.get('tradedate') or params.get('recentdays', 'latest')}",
        )
        rows = []
        for row in _rows(payload):
            code = _symbol(row.get("symbol"))
            if not code:
                continue
            rows.append({
                "code": code,
                "name": str(row.get("name") or ""),
                "trade_date": _date_text(row.get("tradedate")),
                "price": row.get("close"),
                "change_pct": row.get("pct_chg"),
                "amount": row.get("amount"),
                "volume": None,
                "turnover": None,
                "pe": None,
                "market_cap": None,
                "continuous_days": row.get("limit_times"),
                "previous_continuous_days": row.get("pre_limit_times"),
                "failed_attempts": row.get("open_times"),
                "seal_amount": row.get("fd_amount"),
                "first_limit_time": row.get("first_time"),
                "last_limit_time": row.get("last_time"),
                "limit_detail": row.get("limit_detail"),
                "limit_type": row.get("limit_type"),
                "is_break": row.get("is_break"),
                "limit_direction": {"u": "up", "d": "down", "ub": "failed", "db": "down_failed"}.get(normalized_type, normalized_type),
                "source": "numcat_limit_pool",
            })
        dates = [str(item.get("trade_date")) for item in rows if item.get("trade_date")]
        return {
            "stocks": rows,
            "total": len(rows),
            "trade_date": max(dates) if dates else None,
            "source": "numcat_limit_pool",
        }

    async def dragon_board(self, *, tradedate: date | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        stock_payload = await numcat_gateway.query(
            "longhubang_stock",
            fields=DRAGON_STOCK_FIELDS,
            params=params,
            cache_ttl=900,
            affinity_key=f"dragon-stock:{params.get('tradedate', 'latest')}",
        )
        stock_rows = _rows(stock_payload)
        if not stock_rows:
            return []
        resolved_date = max(
            (str(row.get("tradedate") or "") for row in stock_rows),
            default="",
        )
        seat_params = {"tradedate": resolved_date} if len(resolved_date) == 8 else params
        try:
            seat_payload = await numcat_gateway.query(
                "longhubang_seat",
                fields=DRAGON_SEAT_FIELDS,
                params=seat_params,
                cache_ttl=900,
                affinity_key=f"dragon-seat:{seat_params.get('tradedate', 'latest')}",
            )
            seat_rows = _rows(seat_payload)
        except NumCatGatewayError:
            seat_rows = []
        institutions: dict[tuple[str, str], dict[str, Any]] = {}
        for seat in seat_rows:
            if "机构专用" not in str(seat.get("seat_name") or ""):
                continue
            key = (_symbol(seat.get("symbol")), str(seat.get("reason") or ""))
            aggregate = institutions.setdefault(key, {"names": set(), "buy": 0, "sell": 0, "net": 0})
            aggregate["names"].add(str(seat.get("seat_name") or "机构专用"))
            aggregate["buy"] += _int_or_zero(seat.get("buy"))
            aggregate["sell"] += _int_or_zero(seat.get("sell"))
            aggregate["net"] += _int_or_zero(seat.get("net_buy"))
        output = []
        for row in stock_rows:
            code = _symbol(row.get("symbol"))
            if not code:
                continue
            reason = str(row.get("reason") or "")
            institution = institutions.get((code, reason)) or {"names": set(), "buy": 0, "sell": 0, "net": 0}
            output.append({
                "code": code,
                "name": str(row.get("name") or ""),
                "date": _date_text(row.get("tradedate")),
                "price": row.get("close"),
                "change_pct": row.get("pct_chg"),
                "turnover": row.get("turnover_rate"),
                "amount": row.get("lhb_amount") if row.get("lhb_amount") is not None else row.get("amount"),
                "buy_amount": row.get("lhb_buy"),
                "sell_amount": row.get("lhb_sell"),
                "net_amount": row.get("net_amount"),
                "main_net_inflow": row.get("net_amount"),
                "market_cap": row.get("float_value"),
                "institution_count": len(institution["names"]),
                "institution_buy_amount": institution["buy"],
                "institution_sell_amount": institution["sell"],
                "institution_net_amount": institution["net"],
                "reason": reason,
                "source": "numcat_longhubang",
            })
        return output

    async def margin_summary(
        self,
        *,
        tradedate: date | None = None,
        recentdays: int = 250,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        else:
            params["recentdays"] = min(max(int(recentdays), 1), 800)
        payload = await numcat_gateway.query(
            "margin_summary",
            fields=MARGIN_SUMMARY_FIELDS,
            params=params,
            cache_ttl=3600,
            affinity_key=f"margin-summary:{params.get('tradedate') or params.get('recentdays')}",
        )
        return [
            {**row, "tradedate": _date_text(row.get("tradedate")), "source": "numcat_margin_summary"}
            for row in _rows(payload)
            if row.get("tradedate")
        ]

    async def margin_detail(
        self,
        symbols: list[str] | None = None,
        *,
        tradedate: date | None = None,
        recentdays: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbols:
            params["symbols"] = ",".join(_symbol(item) for item in symbols if _symbol(item))
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        elif recentdays:
            params["recentdays"] = min(max(int(recentdays), 1), 800)
        payload = await numcat_gateway.query(
            "margin_detail",
            fields=MARGIN_DETAIL_FIELDS,
            params=params,
            cache_ttl=3600,
            affinity_key=f"margin-detail:{params.get('symbols', 'all')}:{params.get('tradedate') or params.get('recentdays', 'latest')}",
        )
        return [
            {**row, "tradedate": _date_text(row.get("tradedate")), "source": "numcat_margin_detail"}
            for row in _rows(payload)
            if row.get("tradedate")
        ]

    async def margin_securities(
        self,
        symbols: list[str] | None = None,
        *,
        tradedate: date | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbols:
            params["symbols"] = ",".join(_symbol(item) for item in symbols if _symbol(item))
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        payload = await numcat_gateway.query(
            "margin_securities",
            fields=MARGIN_SECURITIES_FIELDS,
            params=params,
            cache_ttl=3600,
            affinity_key=f"margin-securities:{params.get('symbols', 'all')}:{params.get('tradedate', 'latest')}",
        )
        return [
            {**row, "tradedate": _date_text(row.get("tradedate")), "source": "numcat_margin_securities"}
            for row in _rows(payload)
            if row.get("tradedate")
        ]

    async def auction_detail_snapshot(
        self,
        symbols: list[str] | None = None,
        *,
        tradedate: date | None = None,
        minute: str = "0925",
        side: str = "before",
    ) -> list[dict[str, Any]]:
        normalized_minute = str(minute or "0925").replace(":", "")[:4]
        if len(normalized_minute) != 4 or not normalized_minute.isdigit():
            raise ValueError("auction minute must use HHMM")
        if side not in {"before", "after"}:
            raise ValueError("auction snapshot side must be before or after")
        params: dict[str, Any] = {
            "trademin": normalized_minute,
            "side": side,
            "limit": 6000,
        }
        if symbols:
            params["symbols"] = ",".join(_symbol(item) for item in symbols if _symbol(item))
        if tradedate:
            params["tradedate"] = tradedate.strftime("%Y%m%d")
        payload = await numcat_gateway.query(
            "daily_auc_detail",
            fields=AUCTION_DETAIL_FIELDS,
            params=params,
            cache_ttl=4 if tradedate is None else 900,
            affinity_key=f"auction-detail:{params.get('symbols', 'all')}:{params.get('tradedate', 'latest')}:{normalized_minute}:{side}",
        )
        return [
            {
                **row,
                "tradedate": _date_text(row.get("tradedate")),
                # The contract documents auction detail volume in shares.
                "auc_vol": _int_or_none(row.get("auc_vol")),
                "um_vol": _int_or_none(row.get("um_vol")),
            }
            for row in _rows(payload)
        ]

    def status(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": self.configured, "gateway": numcat_gateway.status()}


def _multiply(value: Any, factor: int) -> int | None:
    try:
        return int(float(value) * factor)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(float(value)) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: Any) -> int:
    return _int_or_none(value) or 0


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


def _market(code: str) -> str | None:
    if code.startswith(("000", "001", "002", "003", "300", "301", "302")):
        return "sz"
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh"
    return None


numcat_market_provider = NumCatMarketProvider()

__all__ = ["NumCatMarketProvider", "NumCatGatewayError", "numcat_market_provider"]
