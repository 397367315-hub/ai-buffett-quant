"""Evidence-bound individual-stock decision profile (V2.3).

The service joins public company disclosures, verified market observations and
locally derived metrics into one versioned snapshot.  Facts, calculations and
forward-looking scenarios stay separate.  Missing public disclosure is never
replaced with invented data.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any, Awaitable

from sqlalchemy import desc, select

from database import async_session
from models import (
    IndustryFundFlowDaily,
    MarketBoard,
    StockDecisionProfile,
    StockFundFlowDaily,
)
from services.a_stock_data import calculate_indicators
from services.cyclical_valuation import build_cyclical_valuation, cycle_guard_from_stock
from services.data_collector import (
    EASTMONEY_UT,
    collector,
    is_a_share_market_session,
    normalize_stock_code,
    shanghai_now,
    stock_secid,
)
from services.fqe_reference_data import fqe_reference_data
from services.ftshare_mcp import ftshare_mcp_client
from services.macro_policy_news import macro_policy_news_collector
from services.market_decision_workbench import market_decision_workbench_service
from quant.reflexivity_skill import build_reflexivity_diagnosis


CONTRACT_VERSION = "stock-essence-decision-v2.6.0"
F10_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
QUOTE_DETAIL_URL = "https://push2.eastmoney.com/api/qt/stock/get"
DECISION_STATES = ("EXECUTE", "CAUTION", "OBSERVE", "AVOID", "NO_TRADE")
HORIZONS = (1, 3, 5, 10, 20)


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _pct_field_to_decimal(value: Any) -> float | None:
    """Convert quote/board ``change_pct`` percentage points to a return."""
    parsed = _number(value)
    return parsed / 100 if parsed is not None else None


def _integer(value: Any) -> int | None:
    parsed = _number(value)
    return int(parsed) if parsed is not None else None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return min(upper, max(lower, value))


def _average(values: list[float | None]) -> float | None:
    observed = [value for value in values if value is not None]
    return sum(observed) / len(observed) if observed else None


def _percentile_rank(value: float | None, values: list[float]) -> float | None:
    clean = sorted(item for item in values if math.isfinite(item))
    if value is None or not clean:
        return None
    below = sum(item < value for item in clean)
    equal = sum(math.isclose(item, value, rel_tol=1e-9, abs_tol=1e-9) for item in clean)
    return _round((below + equal * 0.5) / len(clean) * 100, 1)


def _moving_average(values: list[float], window: int) -> float | None:
    return sum(values[-window:]) / window if len(values) >= window else None


def _cumulative_return(values: list[float], horizon: int) -> float | None:
    if len(values) <= horizon or values[-horizon - 1] == 0:
        return None
    return (values[-1] / values[-horizon - 1] - 1) * 100


def _daily_returns(rows: list[dict], date_key: str, close_key: str) -> dict[date, float]:
    dated: dict[date, float] = {}
    for row in rows:
        day = _date(row.get(date_key))
        close = _number(row.get(close_key))
        if day and close is not None and close > 0:
            dated[day] = close
    output: dict[date, float] = {}
    previous = None
    for day, close in sorted(dated.items()):
        if previous not in (None, 0):
            output[day] = (close / previous - 1) * 100
        previous = close
    return output


def _solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Small Gaussian-elimination solver used by the attribution regression."""
    size = len(vector)
    augmented = [list(matrix[index]) + [vector[index]] for index in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-10:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                augmented[row][item] - factor * augmented[column][item]
                for item in range(size + 1)
            ]
    return [augmented[index][-1] for index in range(size)]


def _ols(rows: list[tuple[list[float], float]]) -> list[float] | None:
    if not rows:
        return None
    columns = len(rows[0][0]) + 1
    matrix = [[0.0 for _ in range(columns)] for _ in range(columns)]
    vector = [0.0 for _ in range(columns)]
    for features, target in rows:
        design = [1.0, *features]
        for left in range(columns):
            vector[left] += design[left] * target
            for right in range(columns):
                matrix[left][right] += design[left] * design[right]
    # A very small ridge term stabilises collinear market/sector observations.
    for index in range(1, columns):
        matrix[index][index] += 1e-8
    return _solve_linear_system(matrix, vector)


def _ttm(rows: dict[date, dict], period: date, field: str) -> float | None:
    current = _number((rows.get(period) or {}).get(field))
    if current is None:
        return None
    if period.month == 12:
        return current
    prior_full = _number((rows.get(date(period.year - 1, 12, 31)) or {}).get(field))
    prior_same = _number((rows.get(date(period.year - 1, period.month, period.day)) or {}).get(field))
    if prior_full is None or prior_same is None:
        return None
    return current + prior_full - prior_same


def _normalise_sector_name(value: Any) -> str:
    name = str(value or "").strip()
    for token in ("行业", "板块", "概念", "指数", "Ⅰ", "Ⅱ", "Ⅲ", "I", "II", "III", "（申万）", "(申万)"):
        name = name.replace(token, "")
    aliases = {
        "白酒": "酿酒",
        "饮料": "食品饮料",
        "半导体": "电子",
        "证券": "券商",
    }
    return aliases.get(name, name)


def _sector_match(targets: list[str], rows: list[dict]) -> dict | None:
    wanted = [_normalise_sector_name(item) for item in targets if str(item or "").strip()]
    best: tuple[float, dict] | None = None
    for row in rows:
        raw_name = str(row.get("name") or "").strip()
        candidate = _normalise_sector_name(raw_name)
        if not candidate:
            continue
        score = 0.0
        for target in wanted:
            if target == candidate:
                score = max(score, 100.0)
            elif target and (target in candidate or candidate in target):
                score = max(score, 80.0)
            else:
                overlap = len(set(target) & set(candidate))
                score = max(score, overlap / max(len(set(target) | set(candidate)), 1) * 50)
        if best is None or score > best[0]:
            best = (score, row)
    return dict(best[1]) if best and best[0] >= 35 else None


def _sector_exact_match(targets: list[str], rows: list[dict]) -> dict | None:
    wanted = {_normalise_sector_name(item) for item in targets if str(item or "").strip()}
    wanted.discard("")
    for row in rows:
        if _normalise_sector_name(row.get("name")) in wanted:
            return dict(row)
    return None


def _source_item(
    key: str,
    label: str,
    *,
    status: str,
    source: str,
    data_date: str | None,
    detail: str,
) -> dict:
    return {
        "key": key,
        "label": label,
        "status": status,
        "source": source,
        "data_date": data_date,
        "detail": detail,
    }


class StockEssenceDecisionService:
    _CACHE_SECONDS = 90
    _SOURCE_TIMEOUT = 20

    def __init__(self) -> None:
        self._memory: dict[tuple[str, str], tuple[float, dict]] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def _safe(self, key: str, awaitable: Awaitable[Any], timeout: float | None = None) -> dict:
        try:
            value = await asyncio.wait_for(awaitable, timeout=timeout or self._SOURCE_TIMEOUT)
            return {"key": key, "value": value, "error": None}
        except Exception as exc:
            return {"key": key, "value": None, "error": type(exc).__name__}

    async def _f10(
        self,
        report_name: str,
        filter_value: str,
        *,
        columns: str = "ALL",
        sort_columns: str | None = None,
        sort_types: str | None = None,
        page_size: int = 50,
    ) -> list[dict]:
        params = {
            "reportName": report_name,
            "columns": columns,
            "filter": filter_value,
            "pageNumber": "1",
            "pageSize": str(page_size),
            "source": "HSF10",
            "client": "PC",
        }
        if sort_columns:
            params["sortColumns"] = sort_columns
        if sort_types:
            params["sortTypes"] = sort_types
        payload = await collector.fetch_json(F10_URL, params)
        if not payload.get("success"):
            raise RuntimeError(str(payload.get("message") or report_name))
        return [row for row in ((payload.get("result") or {}).get("data") or []) if isinstance(row, dict)]

    async def _company_profile(self, code: str) -> dict:
        rows = await self._f10("RPT_F10_BASIC_ORGINFO", f'(SECURITY_CODE="{code}")', page_size=1)
        if not rows:
            raise RuntimeError("CompanyProfileEmpty")
        return rows[0]

    async def _financial_rows(self, code: str) -> list[dict]:
        columns = (
            "SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_DATE,NOTICE_DATE,"
            "TOTALOPERATEREVE,TOTALOPERATEREVETZ,PARENTNETPROFIT,PARENTNETPROFITTZ,"
            "KCFJCXSYJLR,KCFJCXSYJLRTZ,NETCASH_OPERATE_PK,NCO_NETPROFIT,"
            "ROEJQ,XSMLL,ZCFZL,YSZKYYSR"
        )
        return await self._f10(
            "RPT_F10_FINANCE_MAINFINADATA",
            f'(SECURITY_CODE="{code}")',
            columns=columns,
            sort_columns="REPORT_DATE,NOTICE_DATE",
            sort_types="-1,-1",
            page_size=24,
        )

    async def _balance_rows(self, code: str) -> list[dict]:
        columns = (
            "SECURITY_CODE,REPORT_DATE,NOTICE_DATE,INVENTORY,INVENTORY_YOY,"
            "ACCOUNTS_RECE,ACCOUNTS_RECE_YOY,NOTE_ACCOUNTS_RECE,NOTE_ACCOUNTS_RECE_YOY,"
            "TOTAL_ASSETS,TOTAL_LIABILITIES"
        )
        return await self._f10(
            "RPT_F10_FINANCE_GBALANCE",
            f'(SECURITY_CODE="{code}")',
            columns=columns,
            sort_columns="REPORT_DATE,NOTICE_DATE",
            sort_types="-1,-1",
            page_size=16,
        )

    async def _main_operations(self, code: str) -> list[dict]:
        return await self._f10(
            "RPT_F10_FN_MAINOP",
            f'(SECURITY_CODE="{code}")',
            sort_columns="REPORT_DATE,MAINOP_TYPE,MBI_RATIO",
            sort_types="-1,1,-1",
            page_size=100,
        )

    async def _equity_rows(self, code: str) -> list[dict]:
        suffix = "SH" if code.startswith(("5", "6", "9")) else "BJ" if code.startswith(("4", "8")) else "SZ"
        return await self._f10(
            "RPT_F10_EH_EQUITY",
            f'(SECUCODE="{code}.{suffix}")',
            sort_columns="END_DATE",
            sort_types="-1",
            page_size=30,
        )

    async def _consensus(self, code: str) -> dict:
        payload = await collector.fetch_json(
            collector.DATACENTER_URL,
            {
                "reportName": "RPT_WEB_RESPREDICT",
                "columns": "WEB_RESPREDICT",
                "filter": f'(SECURITY_CODE="{code}")',
                "pageNumber": "1",
                "pageSize": "5",
                "source": "WEB",
                "client": "WEB",
            },
        )
        rows = ((payload.get("result") or {}).get("data") or [])
        if rows:
            return {
                **rows[0],
                "_coverage_status": "covered",
                "_source_record_count": len(rows),
            }
        # An empty successful response means no institution currently covers
        # the stock.  It is an observed zero, not a failed data source.
        return {
            "_coverage_status": "no_analyst_coverage",
            "_source_record_count": 0,
            "RATING_ORG_NUM": 0,
        }

    async def _quote_detail(self, code: str) -> dict:
        fields = (
            "f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f84,f85,f86,"
            "f116,f117,f127,f128,f129,f162,f167,f168,f169,f170"
        )
        payload = await collector.fetch_json(
            QUOTE_DETAIL_URL,
            {"secid": stock_secid(code), "fields": fields, "fltt": "2", "ut": EASTMONEY_UT},
        )
        row = payload.get("data") or {}
        price = _number(row.get("f43"))
        if price is None or price <= 0:
            raise RuntimeError("QuoteDetailEmpty")
        return {
            "code": code,
            "name": str(row.get("f58") or ""),
            "price": price,
            "high": _number(row.get("f44")),
            "low": _number(row.get("f45")),
            "open": _number(row.get("f46")),
            "volume": _integer(row.get("f47")),
            "amount": _integer(row.get("f48")),
            "volume_ratio": _number(row.get("f50")),
            "previous_close": _number(row.get("f60")),
            "total_shares": _number(row.get("f84")),
            "circulating_shares": _number(row.get("f85")),
            "quote_timestamp": _integer(row.get("f86")),
            "market_cap": _number(row.get("f116")),
            "circulating_market_cap": _number(row.get("f117")),
            "sector": str(row.get("f127") or ""),
            "region": str(row.get("f128") or ""),
            "concepts": [item for item in str(row.get("f129") or "").split(",") if item],
            "pe": _number(row.get("f162")),
            "pb": _number(row.get("f167")),
            "turnover": _number(row.get("f168")),
            "change_amount": _number(row.get("f169")),
            "change_pct": _number(row.get("f170")),
            "source": "eastmoney_quote_detail",
        }

    async def _quote(self, code: str) -> dict:
        detail, batch = await asyncio.gather(
            self._safe("detail", self._quote_detail(code)),
            self._safe("batch", collector.fetch_stock_quotes([code])),
        )
        merged: dict[str, Any] = {}
        sources: list[str] = []
        if detail["value"]:
            merged.update(detail["value"])
            sources.append("eastmoney")
        batch_payload = batch["value"] or {}
        if batch_payload.get("stocks"):
            live = batch_payload["stocks"][0]
            for key, value in live.items():
                if value not in (None, "", "-") and (key not in merged or merged.get(key) in (None, "", "-")):
                    merged[key] = value
            sources.append(str(batch_payload.get("source") or "quote_fallback"))
        if not merged:
            raise RuntimeError(detail["error"] or batch["error"] or "QuoteUnavailable")
        merged["source"] = "+".join(dict.fromkeys(sources))
        merged["is_realtime"] = bool(batch_payload.get("is_realtime"))
        merged["source_updated_at"] = batch_payload.get("source_updated_at")
        return merged

    async def _fund_flow(self, code: str, as_of: date) -> list[dict]:
        end = min(as_of, shanghai_now().date())
        start = end - timedelta(days=370)
        by_date: dict[str, dict] = {}
        live_rows = await collector.fetch_stock_fund_flow(code)
        for row in live_rows:
            trade_date = str(row.get("date") or "")[:10]
            if trade_date and (_date(trade_date) or date.max) <= end:
                by_date[trade_date] = {**row, "source": row.get("source") or "eastmoney"}

        cached_dates: set[str] = set()
        try:
            async with async_session() as session:
                cached = list((await session.execute(
                    select(StockFundFlowDaily)
                    .where(
                        StockFundFlowDaily.stock_code == code,
                        StockFundFlowDaily.trade_date >= start,
                        StockFundFlowDaily.trade_date <= end,
                    )
                    .order_by(StockFundFlowDaily.trade_date.asc())
                )).scalars().all())
            for row in cached:
                trade_date = row.trade_date.isoformat()
                cached_dates.add(trade_date)
                by_date.setdefault(trade_date, {
                    "date": trade_date,
                    "main_net_inflow": row.main_net_inflow,
                    "small_net_inflow": row.small_net_inflow,
                    "medium_net_inflow": row.medium_net_inflow,
                    "large_net_inflow": row.large_net_inflow,
                    "super_large_net_inflow": row.super_large_net_inflow,
                    "close_price": row.close_price,
                    "change_pct": row.change_pct,
                    "source": "database_cache",
                })
        except Exception:
            cached_dates = set()

        if len(by_date) < 80 and ftshare_mcp_client._enabled():
            try:
                ftshare_rows = await ftshare_mcp_client.get_stock_flow_history(
                    code, start.isoformat(), end.isoformat(),
                )
                for row in ftshare_rows:
                    trade_date = str(row.get("date") or "")[:10]
                    if trade_date:
                        by_date.setdefault(trade_date, row)
            except Exception as exc:
                print(f"FTShare stock flow interval failed for {code}: {type(exc).__name__}")

        uncached = [row for day, row in by_date.items() if day not in cached_dates]
        if uncached:
            try:
                async with async_session() as session:
                    session.add_all([
                        StockFundFlowDaily(
                            stock_code=code,
                            trade_date=_date(row.get("date")),
                            close_price=_number(row.get("close_price")),
                            change_pct=_number(row.get("change_pct")),
                            main_net_inflow=_integer(row.get("main_net_inflow")),
                            super_large_net_inflow=_integer(row.get("super_large_net_inflow")),
                            large_net_inflow=_integer(row.get("large_net_inflow")),
                            medium_net_inflow=_integer(row.get("medium_net_inflow")),
                            small_net_inflow=_integer(row.get("small_net_inflow")),
                        )
                        for row in uncached if _date(row.get("date"))
                    ])
                    await session.commit()
            except Exception:
                pass
        return [by_date[key] for key in sorted(by_date)][-260:]

    async def _industry_directory(self) -> list[dict]:
        try:
            rows = await collector.fetch_all_industry_flow()
            if rows:
                return rows
        except Exception:
            pass
        try:
            async with async_session() as session:
                rows = list((await session.execute(
                    select(MarketBoard).where(MarketBoard.board_type == "industry")
                )).scalars().all())
            return [{"code": row.code, "name": row.name, "source": "database_cache"} for row in rows]
        except Exception:
            return []

    async def _legacy_industry_directory(self) -> list[dict]:
        if not ftshare_mcp_client._enabled():
            return []
        try:
            return await ftshare_mcp_client.get_eastmoney_board_directory()
        except Exception:
            return []

    async def _cached_industry_history(self, board_codes: list[str], as_of: date) -> dict[str, list[dict]]:
        output: dict[str, list[dict]] = {code: [] for code in board_codes}
        if not board_codes:
            return output
        try:
            start = as_of - timedelta(days=500)
            async with async_session() as session:
                rows = list((await session.execute(
                    select(IndustryFundFlowDaily)
                    .where(
                        IndustryFundFlowDaily.board_code.in_(board_codes),
                        IndustryFundFlowDaily.trade_date >= start,
                        IndustryFundFlowDaily.trade_date <= as_of,
                    )
                    .order_by(IndustryFundFlowDaily.trade_date.asc())
                )).scalars().all())
            for row in rows:
                output.setdefault(row.board_code, []).append({
                    "trade_date": row.trade_date.isoformat(),
                    "close_price": row.close_price,
                    "change_pct": row.change_pct,
                    "main_net_inflow": row.main_net_inflow,
                    "main_net_inflow_pct": row.main_net_inflow_pct,
                    "super_large_net_inflow": row.super_large_net_inflow,
                    "large_net_inflow": row.large_net_inflow,
                    "medium_net_inflow": row.medium_net_inflow,
                    "small_net_inflow": row.small_net_inflow,
                    "source": "database_cache",
                })
        except Exception:
            pass
        return output

    async def _sector_history(
        self,
        role_board_code: str,
        benchmark_board_code: str,
        as_of: date,
    ) -> dict:
        codes = list(dict.fromkeys(code for code in (role_board_code, benchmark_board_code) if code))
        cached = await self._cached_industry_history(codes, as_of)
        role_by_date = {
            str(row.get("trade_date"))[:10]: row
            for row in cached.get(role_board_code, [])
            if row.get("trade_date")
        }
        try:
            live = await collector.fetch_board_flow_history(role_board_code, 420)
            for row in live.get("history") or []:
                trade_date = str(row.get("trade_date") or "")[:10]
                if trade_date and (_date(trade_date) or date.max) <= as_of:
                    role_by_date[trade_date] = {**row, "source": "eastmoney"}
        except Exception:
            pass

        role_history = [role_by_date[key] for key in sorted(role_by_date)]
        role_price_points = sum(_number(row.get("change_pct")) is not None for row in role_history)
        if role_price_points >= 60 or not benchmark_board_code:
            return {
                "code": role_board_code,
                "benchmark_code": role_board_code,
                "history": role_history[-420:],
                "source": "+".join(sorted({str(row.get("source") or "eastmoney") for row in role_history})) or "eastmoney",
            }

        benchmark_by_date = {
            str(row.get("trade_date"))[:10]: row
            for row in cached.get(benchmark_board_code, [])
            if row.get("trade_date")
        }
        if ftshare_mcp_client._enabled():
            try:
                ftshare_rows = await ftshare_mcp_client.get_eastmoney_board_price_history(
                    benchmark_board_code, limit=180,
                )
                for row in ftshare_rows:
                    trade_date = str(row.get("trade_date") or "")[:10]
                    if trade_date and (_date(trade_date) or date.max) <= as_of:
                        benchmark_by_date[trade_date] = row
            except Exception as exc:
                print(f"FTShare board OHLC failed for {benchmark_board_code}: {type(exc).__name__}")
        benchmark_history = [benchmark_by_date[key] for key in sorted(benchmark_by_date)]
        if sum(_number(row.get("change_pct")) is not None for row in benchmark_history) >= 15:
            return {
                "code": role_board_code,
                "benchmark_code": benchmark_board_code,
                "history": benchmark_history[-420:],
                "source": "+".join(sorted({str(row.get("source") or "ftshare_mcp") for row in benchmark_history})),
            }
        return {
            "code": role_board_code,
            "benchmark_code": role_board_code,
            "history": role_history[-420:],
            "source": "+".join(sorted({str(row.get("source") or "eastmoney") for row in role_history})) or "eastmoney",
        }

    async def _constituent_sector_history(
        self,
        members: list[dict],
        sector_code: str,
        sector_name: str,
        as_of: date,
    ) -> dict:
        eligible = [row for row in members if str(row.get("code") or "").isdigit()]
        eligible.sort(
            key=lambda row: _number(row.get("market_cap") or row.get("total_market_cap")) or 0,
            reverse=True,
        )
        selected = eligible[:10]
        results = await asyncio.gather(*(
            collector.fetch_stock_price_history(str(row["code"]), 150)
            for row in selected
        ), return_exceptions=True)
        daily: dict[date, list[float]] = defaultdict(list)
        observed_members = 0
        for result in results:
            if isinstance(result, Exception) or not isinstance(result, dict):
                continue
            returns = _daily_returns(result.get("history") or [], "trade_date", "close")
            if not returns:
                continue
            observed_members += 1
            for trade_date, value in returns.items():
                if trade_date <= as_of:
                    daily[trade_date].append(value)
        minimum_members = max(2, math.ceil(observed_members * 0.5))
        history = [{
            "trade_date": trade_date.isoformat(),
            "change_pct": _round(sum(values) / len(values), 6),
            "member_observations": len(values),
            "source": "tencent_constituent_equal_weight_proxy",
        } for trade_date, values in sorted(daily.items()) if len(values) >= minimum_members]
        return {
            "code": sector_code,
            "benchmark_code": sector_code,
            "benchmark_name": f"{sector_name}成分等权基准",
            "history": history[-120:],
            "source": "腾讯成分股日线等权计算",
            "constituent_sample": observed_members,
            "formula": "行业基准日收益=可核验主要成分股当日收益等权平均",
        }

    async def _valuation_history(self, code: str, name: str, as_of: date) -> dict:
        start = as_of - timedelta(days=365 * 3 + 20)
        try:
            cached = await fqe_reference_data.get_history(code, days=1200)
        except Exception:
            cached = None
        if cached:
            cached_rows = [
                row for row in (cached.get("history") or [])
                if start <= (_date(row.get("date")) or date.min) <= as_of
            ]
            cached_end = _date(cached_rows[-1].get("date")) if cached_rows else None
            if cached_end and cached_end >= as_of - timedelta(days=10):
                return {
                    **cached,
                    "history": cached_rows,
                    "sample_count": len(cached_rows),
                    "history_start": cached_rows[0].get("date"),
                    "history_end": cached_rows[-1].get("date"),
                }
        rows, source = await fqe_reference_data._fetch_valuation(code, start, as_of)
        record = fqe_reference_data._valuation_record(code, name, rows, start, source)
        try:
            from models import StockValuationHistory

            await fqe_reference_data._upsert(StockValuationHistory, [record], ["stock_code"])
        except Exception:
            pass
        history = record.get("history") or []
        return {
            "stock_code": code,
            "stock_name": record.get("stock_name"),
            "history": [{"date": item[0], "pe_ttm": item[1]} for item in history],
            "sample_count": len(history),
            "pe_percentile_3y": record.get("pe_percentile_3y"),
            "history_start": history[0][0] if history else None,
            "history_end": history[-1][0] if history else None,
            "source": source,
            "updated_at": shanghai_now().isoformat(),
        }

    @staticmethod
    def _select_pit_rows(rows: list[dict], as_of: date) -> dict[date, dict]:
        selected: dict[date, dict] = {}
        for row in rows:
            report_day = _date(row.get("REPORT_DATE"))
            disclosed = _date(row.get("NOTICE_DATE"))
            if not report_day or not disclosed or disclosed > as_of:
                continue
            previous = selected.get(report_day)
            if previous is None or disclosed >= (_date(previous.get("NOTICE_DATE")) or date.min):
                selected[report_day] = row
        return selected

    def _build_financials(self, financial_rows: list[dict], balance_rows: list[dict], as_of: date) -> dict:
        selected = self._select_pit_rows(financial_rows, as_of)
        if not selected:
            return {"available": False, "resolution": "公开财报源本次未返回可用披露"}
        period = max(selected)
        latest = selected[period]
        series = {
            day: {
                "revenue": _number(row.get("TOTALOPERATEREVE")),
                "net_profit": _number(row.get("PARENTNETPROFIT")),
                "deducted_profit": _number(row.get("KCFJCXSYJLR")),
                "operating_cashflow": _number(row.get("NETCASH_OPERATE_PK")),
            }
            for day, row in selected.items()
        }
        revenue_ttm = _ttm(series, period, "revenue")
        net_profit_ttm = _ttm(series, period, "net_profit")
        deducted_ttm = _ttm(series, period, "deducted_profit")
        cashflow_ttm = _ttm(series, period, "operating_cashflow")
        net_margin = net_profit_ttm / revenue_ttm * 100 if revenue_ttm and net_profit_ttm is not None else None
        cash_to_profit = cashflow_ttm / net_profit_ttm if net_profit_ttm not in (None, 0) and cashflow_ttm is not None else None
        deducted_ratio = deducted_ttm / net_profit_ttm if net_profit_ttm not in (None, 0) and deducted_ttm is not None else None
        non_recurring_ratio = 1 - deducted_ratio if deducted_ratio is not None else None

        balances = self._select_pit_rows(balance_rows, as_of)
        balance = balances.get(period) or (balances[max(balances)] if balances else {})
        receivable = _number(balance.get("NOTE_ACCOUNTS_RECE"))
        if receivable is None:
            receivable = _number(balance.get("ACCOUNTS_RECE"))
        receivable_yoy = _number(balance.get("NOTE_ACCOUNTS_RECE_YOY"))
        if receivable_yoy is None:
            receivable_yoy = _number(balance.get("ACCOUNTS_RECE_YOY"))
        assets = _number(balance.get("TOTAL_ASSETS"))
        liabilities = _number(balance.get("TOTAL_LIABILITIES"))
        debt_ratio = _number(latest.get("ZCFZL"))
        if debt_ratio is None and assets not in (None, 0) and liabilities is not None:
            debt_ratio = liabilities / assets * 100
        revenue_growth = _number(latest.get("TOTALOPERATEREVETZ"))
        profit_growth = _number(latest.get("PARENTNETPROFITTZ"))
        deducted_growth = _number(latest.get("KCFJCXSYJLRTZ"))
        inventory_growth = _number(balance.get("INVENTORY_YOY"))

        growth_values = [revenue_growth, profit_growth, deducted_growth]
        growth_mean = _average(growth_values)
        earnings_state = "改善" if growth_mean is not None and growth_mean >= 5 else "恶化" if growth_mean is not None and growth_mean < 0 else "稳定"
        quality_checks = [
            cash_to_profit is not None and cash_to_profit >= 0.8,
            deducted_ratio is not None and deducted_ratio >= 0.85,
            debt_ratio is not None and debt_ratio <= 60,
            _number(latest.get("ROEJQ")) is not None and float(latest["ROEJQ"]) >= 8,
            net_margin is not None and net_margin > 0,
        ]
        quality_score = sum(quality_checks) / len(quality_checks) * 100
        quality = "高" if quality_score >= 80 else "中" if quality_score >= 50 else "低"
        adverse_working_capital = sum(
            value is not None and revenue_growth is not None and value > revenue_growth + 20
            for value in (receivable_yoy, inventory_growth)
        )
        sustainability = (
            "高" if earnings_state == "改善" and quality == "高" and not adverse_working_capital
            else "低" if earnings_state == "恶化" or quality == "低" or adverse_working_capital >= 2
            else "中"
        )
        cycle_history = []
        for history_period in sorted(selected)[-20:]:
            history_row = selected[history_period]
            cycle_history.append({
                "period": history_period.isoformat(),
                "net_profit": _round(_number(history_row.get("PARENTNETPROFIT")), 0),
                "net_profit_ttm": _round(_ttm(series, history_period, "net_profit"), 0),
                "operating_cashflow_ttm": _round(_ttm(series, history_period, "operating_cashflow"), 0),
                "gross_margin_pct": _round(_number(history_row.get("XSMLL"))),
                "roe_pct": _round(_number(history_row.get("ROEJQ"))),
                "net_profit_growth_pct": _round(_number(history_row.get("PARENTNETPROFITTZ"))),
            })
        return {
            "available": True,
            "report_date": period.isoformat(),
            "disclosed_at": str(latest.get("NOTICE_DATE") or "")[:10],
            "metrics": {
                "revenue_growth_pct": _round(revenue_growth),
                "net_profit_growth_pct": _round(profit_growth),
                "deducted_profit_growth_pct": _round(deducted_growth),
                "operating_cashflow_ttm": _round(cashflow_ttm, 0),
                "operating_cashflow_to_profit": _round(cash_to_profit, 3),
                "roe_pct": _round(_number(latest.get("ROEJQ"))),
                "gross_margin_pct": _round(_number(latest.get("XSMLL"))),
                "net_margin_ttm_pct": _round(net_margin),
                "debt_ratio_pct": _round(debt_ratio),
                "accounts_receivable": _round(receivable, 0),
                "accounts_receivable_yoy_pct": _round(receivable_yoy),
                "inventory": _round(_number(balance.get("INVENTORY")), 0),
                "inventory_yoy_pct": _round(inventory_growth),
                "revenue_ttm": _round(revenue_ttm, 0),
                "net_profit_ttm": _round(net_profit_ttm, 0),
                "deducted_profit_ttm": _round(deducted_ttm, 0),
                "deducted_profit_ratio": _round(deducted_ratio, 3),
                "non_recurring_profit_ratio": _round(non_recurring_ratio, 3),
            },
            "earnings_state": earnings_state,
            "earnings_quality": quality,
            "earnings_quality_score": _round(quality_score, 1),
            "earnings_sustainability": sustainability,
            "cycle_history": cycle_history,
            "operating_vs_non_recurring": (
                "利润主要来自扣非经营成果" if deducted_ratio is not None and deducted_ratio >= 0.85
                else "非经常性损益占比较高，需核对利润来源"
            ),
            "formula": "TTM=本期累计+上年全年-上年同期累计；只使用公告日不晚于决策日的财报",
            "source": "东方财富 F10 主营财务指标+资产负债表",
        }

    @staticmethod
    def _build_operations(rows: list[dict], as_of: date, latest_report: date | None) -> dict:
        valid = [
            row for row in rows
            if (day := _date(row.get("REPORT_DATE"))) and day <= as_of and (latest_report is None or day <= latest_report)
        ]
        if not valid:
            return {"report_date": None, "products": [], "segments": [], "regions": []}
        report_date = max(_date(row.get("REPORT_DATE")) for row in valid if _date(row.get("REPORT_DATE")))
        selected = [row for row in valid if _date(row.get("REPORT_DATE")) == report_date]

        def map_rows(kind: str) -> list[dict]:
            return [{
                "name": str(row.get("ITEM_NAME") or ""),
                "revenue": _round(_number(row.get("MAIN_BUSINESS_INCOME")), 0),
                "revenue_ratio_pct": _round(
                    (_number(row.get("MBI_RATIO")) or 0) * 100
                    if abs(_number(row.get("MBI_RATIO")) or 0) <= 1 else _number(row.get("MBI_RATIO"))
                ),
                "gross_margin_pct": _round(
                    (_number(row.get("GROSS_RPOFIT_RATIO")) or 0) * 100
                    if _number(row.get("GROSS_RPOFIT_RATIO")) is not None and abs(_number(row.get("GROSS_RPOFIT_RATIO")) or 0) <= 1
                    else _number(row.get("GROSS_RPOFIT_RATIO"))
                ),
            } for row in selected if str(row.get("MAINOP_TYPE") or "") == kind and str(row.get("ITEM_NAME") or "").strip()]

        return {
            "report_date": report_date.isoformat(),
            "segments": map_rows("1"),
            "products": map_rows("2"),
            "regions": map_rows("3"),
            "source": "东方财富 RPT_F10_FN_MAINOP",
        }

    @staticmethod
    def _select_equity(rows: list[dict], as_of: date) -> dict:
        valid = [row for row in rows if (_date(row.get("END_DATE")) or date.max) <= as_of]
        if not valid:
            return {}
        return max(valid, key=lambda row: _date(row.get("END_DATE")) or date.min)

    @staticmethod
    def _build_capital_impact(flow_rows: list[dict], free_float_cap: float | None) -> dict:
        dated = sorted(
            (row for row in flow_rows if _date(row.get("date") or row.get("trade_date"))),
            key=lambda row: _date(row.get("date") or row.get("trade_date")) or date.min,
        )
        windows = []
        daily_impacts = []
        if free_float_cap and free_float_cap > 0:
            daily_impacts = [
                (_number(row.get("main_net_inflow")) or 0) / free_float_cap * 100
                for row in dated
                if _number(row.get("main_net_inflow")) is not None
            ]
        for horizon in (1, 3, 5, 20):
            observed = dated[-horizon:]
            net = sum(_number(row.get("main_net_inflow")) or 0 for row in observed) if observed else None
            ratio = net / free_float_cap * 100 if net is not None and free_float_cap else None
            windows.append({
                "days": horizon,
                "observations": len(observed),
                "main_net_inflow": _round(net, 0),
                "impact_ratio_pct": _round(ratio, 4),
                "impact_percentile": _percentile_rank(ratio, daily_impacts) if horizon == 1 else None,
            })
        positive_5 = sum((_number(row.get("main_net_inflow")) or 0) > 0 for row in dated[-5:])
        return {
            "free_float_market_cap": _round(free_float_cap, 0),
            "windows": windows,
            "positive_days_5d": positive_5,
            "persistence": "持续流入" if positive_5 >= 4 else "持续流出" if positive_5 <= 1 else "方向反复",
            "latest_data_date": str((dated[-1].get("date") or dated[-1].get("trade_date")))[:10] if dated else None,
            "formula": "资金冲击率=窗口主力净流入/自由流通市值",
            "source": "东方财富个股资金流+F10自由流通股本",
        }

    @staticmethod
    def _build_relative(
        stock_rows: list[dict],
        market_rows: list[dict],
        sector_rows: list[dict],
        flow_rows: list[dict],
        free_float_cap: float | None,
    ) -> dict:
        stock_returns = _daily_returns(stock_rows, "trade_date", "close")
        market_returns = _daily_returns(market_rows, "date", "close")
        sector_returns = {
            _date(row.get("trade_date")): _number(row.get("change_pct"))
            for row in sector_rows
            if _date(row.get("trade_date")) and _number(row.get("change_pct")) is not None
        }
        flow_impacts = {
            _date(row.get("date")): (_number(row.get("main_net_inflow")) or 0) / free_float_cap * 100
            for row in flow_rows
            if free_float_cap and _date(row.get("date")) and _number(row.get("main_net_inflow")) is not None
        }
        common = sorted(set(stock_returns) & set(market_returns) & set(sector_returns))[-80:]
        regression_rows = []
        for day in common:
            market_return = market_returns[day]
            sector_excess = sector_returns[day] - market_return
            flow_impact = flow_impacts.get(day, 0.0)
            regression_rows.append(([market_return, sector_excess, flow_impact], stock_returns[day]))
        coefficients = _ols(regression_rows) if len(regression_rows) >= 15 else None
        intercept, market_beta, sector_beta, fund_beta = coefficients if coefficients else (None, None, None, None)

        stock_closes = [_number(row.get("close")) for row in stock_rows]
        stock_closes = [value for value in stock_closes if value is not None and value > 0]
        market_closes = [_number(row.get("close")) for row in market_rows]
        market_closes = [value for value in market_closes if value is not None and value > 0]
        sector_changes = [
            _number(row.get("change_pct")) for row in sector_rows if _number(row.get("change_pct")) is not None
        ]
        results = []
        for horizon in HORIZONS:
            stock_return = _cumulative_return(stock_closes, horizon)
            market_return = _cumulative_return(market_closes, horizon)
            sector_window = sector_changes[-horizon:]
            sector_return = (
                (math.prod(1 + value / 100 for value in sector_window) - 1) * 100
                if len(sector_window) == horizon else None
            )
            flow_window = sorted(flow_impacts.items())[-horizon:]
            flow_sum = sum(value for _, value in flow_window) if len(flow_window) == horizon else None
            market_contribution = market_beta * market_return if market_beta is not None and market_return is not None else None
            sector_contribution = (
                sector_beta * (sector_return - market_return)
                if sector_beta is not None and sector_return is not None and market_return is not None else None
            )
            fund_contribution = fund_beta * flow_sum if fund_beta is not None and flow_sum is not None else None
            alpha = (
                stock_return - sum(value for value in (market_contribution, sector_contribution, fund_contribution) if value is not None)
                if stock_return is not None and market_contribution is not None else None
            )
            results.append({
                "days": horizon,
                "stock_return_pct": _round(stock_return),
                "market_return_pct": _round(market_return),
                "sector_return_pct": _round(sector_return),
                "market_contribution_pct": _round(market_contribution),
                "sector_contribution_pct": _round(sector_contribution),
                "fund_contribution_pct": _round(fund_contribution),
                "individual_alpha_pct": _round(alpha),
            })
        alpha_20 = next((row.get("individual_alpha_pct") for row in results if row["days"] == 20), None)
        alpha_score = _clamp(50 + (alpha_20 or 0) * 4) if alpha_20 is not None else None
        return {
            "sample_count": len(regression_rows),
            "market_beta": _round(market_beta, 4),
            "sector_beta": _round(sector_beta, 4),
            "fund_flow_beta": _round(fund_beta, 4),
            "daily_alpha_intercept": _round(intercept, 4),
            "windows": results,
            "individual_alpha_score": _round(alpha_score, 1),
            "formula": "日收益OLS：个股=截距+市场Beta×市场+板块Beta×板块超额+资金Beta×资金冲击；窗口Alpha为残差",
        }

    @staticmethod
    def _build_sector_role(
        code: str,
        quote: dict,
        members: list[dict],
        relative: dict,
        sector_name: str,
        sector_change: float | None,
    ) -> dict:
        valid = [row for row in members if row.get("code")]
        change_rank = None
        flow_rank = None
        cap_rank = None
        if valid:
            change_sorted = sorted(valid, key=lambda row: _number(row.get("change_pct")) or -math.inf, reverse=True)
            flow_sorted = sorted(valid, key=lambda row: _number(row.get("main_net_inflow")) or -math.inf, reverse=True)
            cap_sorted = sorted(valid, key=lambda row: _number(row.get("market_cap") or row.get("total_market_cap")) or -math.inf, reverse=True)
            change_rank = next((index for index, row in enumerate(change_sorted, 1) if row.get("code") == code), None)
            flow_rank = next((index for index, row in enumerate(flow_sorted, 1) if row.get("code") == code), None)
            cap_rank = next((index for index, row in enumerate(cap_sorted, 1) if row.get("code") == code), None)
        total = len(valid)
        alpha_5 = next((row.get("individual_alpha_pct") for row in relative.get("windows", []) if row["days"] == 5), None)
        alpha_20 = next((row.get("individual_alpha_pct") for row in relative.get("windows", []) if row["days"] == 20), None)
        if total and change_rank and flow_rank and change_rank <= max(2, total * 0.1) and flow_rank <= max(3, total * 0.15):
            role = "核心龙头"
        elif total and cap_rank and cap_rank <= max(2, total * 0.1):
            role = "中军"
        elif alpha_20 is not None and alpha_20 >= 5:
            role = "核心趋势股"
        elif alpha_5 is not None and alpha_5 >= 3:
            role = "补涨"
        elif change_rank and total and change_rank <= total * 0.5:
            role = "跟风"
        else:
            role = "后排"
        migration = "强化" if alpha_5 is not None and alpha_20 is not None and alpha_5 > alpha_20 else "弱化" if alpha_5 is not None and alpha_20 is not None and alpha_5 < alpha_20 - 2 else "稳定"
        return {
            "sector": sector_name,
            "sector_change_pct": _round(sector_change),
            "role": role,
            "role_migration": migration,
            "member_count": total,
            "change_rank": change_rank,
            "fund_flow_rank": flow_rank,
            "market_cap_rank": cap_rank,
            "evidence": f"板块内涨幅排名 {change_rank or '--'}/{total or '--'}，资金排名 {flow_rank or '--'}/{total or '--'}，20日Alpha {alpha_20 if alpha_20 is not None else '--'}%",
        }

    @staticmethod
    def _build_sector_recommendations(
        code: str,
        quote: dict,
        members: list[dict],
        sector_name: str,
        sector_code: str,
        decision_date: date,
        *,
        historical_mode: bool = False,
    ) -> dict:
        """Rank a bounded sector snapshot into research groups.

        The constituent endpoint is a current quote snapshot.  It is therefore
        deliberately excluded from historical profiles to avoid putting today's
        constituents or prices into a past decision.  Scores use only observed
        fields and expose their coverage and evidence alongside the result.
        """

        empty = {
            "available": False,
            "sector": {
                "code": sector_code or None,
                "name": sector_name,
                "member_count": 0,
            },
            "leader": [],
            "excellent": [],
            "potential": [],
            "data_date": decision_date.isoformat(),
            "source": "东方财富行业成分快照",
            "is_realtime": False,
            "warnings": [],
            "method": "板块内市值/资金/相对强度/盈利代理/活跃度分组；缺失因子不填默认分",
        }
        if historical_mode:
            empty["warnings"] = ["历史查询不使用当前板块成分快照，避免前视偏差；请查询最近交易日实时/缓存画像"]
            return empty

        def number(value: Any) -> float | None:
            return _number(value)

        def scale(value: float | None, low: float, high: float) -> float | None:
            if value is None or high <= low:
                return None
            return min(100.0, max(0.0, (value - low) / (high - low) * 100))

        def percentile(value: float | None, values: list[float], *, high_is_better: bool = True) -> float | None:
            clean = sorted(item for item in values if math.isfinite(item))
            if value is None or not clean:
                return None
            below = sum(item < value for item in clean)
            equal = sum(math.isclose(item, value, rel_tol=1e-9, abs_tol=1e-9) for item in clean)
            rank = (below + equal * 0.5) / len(clean) * 100
            return round(rank if high_is_better else 100 - rank, 1)

        def average(parts: list[tuple[float | None, float]]) -> tuple[float | None, float]:
            observed = [(value, weight) for value, weight in parts if value is not None]
            if not observed:
                return None, 0.0
            total_weight = sum(weight for _, weight in observed)
            return round(sum(value * weight for value, weight in observed) / total_weight, 1), total_weight

        valid: list[dict] = []
        seen: set[str] = set()
        for raw in members or []:
            try:
                member_code = normalize_stock_code(str(raw.get("code") or ""))
            except ValueError:
                continue
            name = str(raw.get("name") or member_code).strip()
            if member_code in seen or member_code == code or "ST" in name.upper() or "退" in name:
                continue
            price = number(raw.get("price"))
            if price is None or price <= 0:
                continue
            seen.add(member_code)
            valid.append({
                **raw,
                "code": member_code,
                "name": name,
                "price": price,
                "change_pct": number(raw.get("change_pct")),
                "main_net_inflow": number(raw.get("main_net_inflow")),
                "market_cap": number(raw.get("market_cap") or raw.get("total_market_cap")),
                "turnover": number(raw.get("turnover")),
                "volume_ratio": number(raw.get("volume_ratio")),
                "pe": number(raw.get("pe")),
                "pb": number(raw.get("pb")),
                "roe": number(raw.get("roe")),
            })

        empty["sector"]["member_count"] = len(valid)
        if not valid:
            empty["warnings"] = ["板块成分快照为空或没有可核验的正常交易股票，不生成伪推荐"]
            return empty

        changes = [row["change_pct"] for row in valid if row["change_pct"] is not None]
        inflows = [row["main_net_inflow"] for row in valid if row["main_net_inflow"] is not None]
        caps = [row["market_cap"] for row in valid if row["market_cap"] is not None]
        cyclical = cycle_guard_from_stock({"industry": sector_name, "sector": sector_name}).get("is_cyclical")
        scored: list[dict] = []
        for row in valid:
            change_score = percentile(row["change_pct"], changes)
            flow_score = percentile(row["main_net_inflow"], inflows)
            cap_score = percentile(row["market_cap"], caps)
            volume_score = scale(row["volume_ratio"], 1.2, 5.0)
            turnover_score = scale(row["turnover"], 1.0, 15.0)
            activity_score = average([(volume_score, 0.6), (turnover_score, 0.4)])[0]

            roe_score = scale(row["roe"], 0.0, 25.0)
            pe_score = None
            if row["pe"] is not None and not cyclical:
                pe_score = 85.0 if 0 < row["pe"] <= 20 else 68.0 if row["pe"] <= 40 else 42.0 if row["pe"] <= 80 else 18.0 if row["pe"] > 80 else 12.0
            quality_score, quality_weight = average([(roe_score, 0.7), (pe_score, 0.3)])

            risk_penalty = 0.0
            risk_reasons: list[str] = []
            if row["change_pct"] is not None and row["change_pct"] > 8:
                risk_penalty += 6
                risk_reasons.append("当日涨幅偏高，短线追高风险增加")
            if row["turnover"] is not None and row["turnover"] > 25:
                risk_penalty += 7
                risk_reasons.append("换手率过高，波动风险增加")
            if row["volume_ratio"] is not None and row["volume_ratio"] > 6:
                risk_penalty += 5
                risk_reasons.append("量比过高，成交可能过热")
            if row["pe"] is not None and row["pe"] <= 0:
                risk_penalty += 8
                risk_reasons.append("PE为负或亏损期，盈利质量需核验")
            if row["roe"] is not None and row["roe"] < 0:
                risk_penalty += 7
                risk_reasons.append("ROE为负")
            if cyclical:
                risk_reasons.append("周期板块：低PE不直接视为低估，需结合盈利周期")

            leader_score, leader_weight = average([
                (cap_score, 0.32), (flow_score, 0.25), (change_score, 0.18),
                (activity_score, 0.15), (quality_score, 0.10),
            ])
            excellent_score, excellent_weight = average([
                (quality_score, 0.30), (change_score, 0.25), (flow_score, 0.20),
                (activity_score, 0.15), (cap_score, 0.10),
            ])
            potential_score, potential_weight = average([
                (change_score, 0.30), (flow_score, 0.30), (activity_score, 0.20),
                (quality_score, 0.10), (cap_score, 0.10),
            ])
            row.update({
                "scores": {
                    "leader": round(max(0.0, (leader_score or 0) - risk_penalty), 1),
                    "excellent": round(max(0.0, (excellent_score or 0) - risk_penalty), 1),
                    "potential": round(max(0.0, (potential_score or 0) - risk_penalty), 1),
                },
                "coverage": {
                    "leader": leader_weight,
                    "excellent": excellent_weight,
                    "potential": potential_weight,
                },
                "factor_scores": {
                    "change": change_score,
                    "capital": flow_score,
                    "market_cap": cap_score,
                    "activity": activity_score,
                    "quality": quality_score,
                    "risk_penalty": round(risk_penalty, 1),
                },
                "quality_weight": quality_weight,
                "risk_reasons": risk_reasons,
                "ranks": {
                    "change": next((index for index, item in enumerate(sorted(valid, key=lambda item: item["change_pct"] if item["change_pct"] is not None else -math.inf, reverse=True), 1) if item["code"] == row["code"]), None),
                    "capital": next((index for index, item in enumerate(sorted(valid, key=lambda item: item["main_net_inflow"] if item["main_net_inflow"] is not None else -math.inf, reverse=True), 1) if item["code"] == row["code"]), None),
                    "market_cap": next((index for index, item in enumerate(sorted(valid, key=lambda item: item["market_cap"] if item["market_cap"] is not None else -math.inf, reverse=True), 1) if item["code"] == row["code"]), None),
                },
            })
            scored.append(row)

        def present(value: float | None, suffix: str = "") -> str:
            return f"{value:.1f}{suffix}" if value is not None else "待补"

        def render(row: dict, group: str, rank: int) -> dict:
            factors = row["factor_scores"]
            reasons = []
            if factors.get("change") is not None:
                reasons.append(f"板块内涨幅强度 {present(factors['change'])} 分（排名 {row['ranks']['change'] or '--'}）")
            if factors.get("capital") is not None:
                reasons.append(f"主力资金强度 {present(factors['capital'])} 分（排名 {row['ranks']['capital'] or '--'}）")
            if group == "leader" and factors.get("market_cap") is not None:
                reasons.append(f"板块市值排名 {row['ranks']['market_cap'] or '--'}")
            elif factors.get("quality") is not None:
                reasons.append(f"盈利代理分 {present(factors['quality'])}；ROE {present(row['roe'], '%')}")
            if row["volume_ratio"] is not None:
                reasons.append(f"量比 {row['volume_ratio']:.2f}")
            if not reasons:
                reasons.append("板块成分存在可核验行情字段")
            quality_note = (
                "周期板块，PE仅作辅助" if cyclical
                else "ROE/PE行情字段代理，未覆盖完整财务PIT" if row["quality_weight"] < 0.7
                else "ROE与PE字段可用"
            )
            risk = row["risk_reasons"][:3] or ["未触发已观测的明显短线风险阈值"]
            return {
                "code": row["code"],
                "name": row["name"],
                "sector": sector_name,
                "price": round(row["price"], 2),
                "change_pct": round(row["change_pct"], 2) if row["change_pct"] is not None else None,
                "market_cap": round(row["market_cap"], 0) if row["market_cap"] is not None else None,
                "main_net_inflow": round(row["main_net_inflow"], 0) if row["main_net_inflow"] is not None else None,
                "volume_ratio": round(row["volume_ratio"], 2) if row["volume_ratio"] is not None else None,
                "turnover": round(row["turnover"], 2) if row["turnover"] is not None else None,
                "roe": round(row["roe"], 2) if row["roe"] is not None else None,
                "pe": round(row["pe"], 2) if row["pe"] is not None else None,
                "score": row["scores"][group],
                "confidence_pct": round(row["coverage"][group] / 1.0 * 100, 1),
                "rank": rank,
                "reasons": reasons[:3],
                "risk": "；".join(risk),
                "quality_note": quality_note,
                "data_date": decision_date.isoformat(),
                "source": "东方财富行业成分快照",
                "is_realtime": bool(quote.get("is_realtime") and decision_date == shanghai_now().date() and is_a_share_market_session(shanghai_now())),
            }

        warnings: list[str] = []
        if len(valid) < 5:
            warnings.append(f"板块仅核验到 {len(valid)} 只可用成分，排名稳定性有限")
        if sum(row["quality_weight"] > 0 for row in scored) < max(1, len(scored) // 2):
            warnings.append("多数成分缺少ROE/PE盈利代理，优秀分组仅作行情研究参考")
        if cyclical:
            warnings.append("该板块具有周期属性；推荐排序不把低PE直接解释为安全边际")

        used: set[str] = set()

        def select_group(group: str, *, require_quality: bool = False) -> list[dict]:
            ordered = sorted(
                scored,
                key=lambda item: (item["scores"][group], item["coverage"][group], item["code"]),
                reverse=True,
            )
            selected = []
            for row in ordered:
                if row["code"] in used or row["coverage"][group] < 0.45:
                    continue
                if require_quality and row["factor_scores"].get("quality") is None:
                    continue
                used.add(row["code"])
                selected.append(render(row, group, len(selected) + 1))
                if len(selected) >= 3:
                    break
            return selected

        leader = select_group("leader")
        excellent = select_group("excellent", require_quality=True)
        potential = select_group("potential")
        if not any((leader, excellent, potential)):
            warnings.append("可用因子覆盖不足，未生成分组推荐")
        return {
            **empty,
            "available": bool(leader or excellent or potential),
            "sector": {**empty["sector"], "member_count": len(valid)},
            "leader": leader,
            "excellent": excellent,
            "potential": potential,
            "is_realtime": bool(quote.get("is_realtime") and decision_date == shanghai_now().date() and is_a_share_market_session(shanghai_now())),
            "warnings": warnings,
            "coverage": {
                "members": len(valid),
                "change_pct": sum(row["change_pct"] is not None for row in valid),
                "capital": sum(row["main_net_inflow"] is not None for row in valid),
                "market_cap": sum(row["market_cap"] is not None for row in valid),
                "volume_ratio": sum(row["volume_ratio"] is not None for row in valid),
                "roe_or_pe": sum(row["roe"] is not None or row["pe"] is not None for row in valid),
            },
        }

    @staticmethod
    def _build_dependency(stock_rows: list[dict], sector_rows: list[dict], relative: dict) -> dict:
        stock_returns = _daily_returns(stock_rows, "trade_date", "close")
        sector_returns = {
            _date(row.get("trade_date")): _number(row.get("change_pct"))
            for row in sector_rows
            if _date(row.get("trade_date")) and _number(row.get("change_pct")) is not None
        }
        common = sorted(set(stock_returns) & set(sector_returns))[-60:]
        correlation = None
        if len(common) >= 10:
            left = [stock_returns[day] for day in common]
            right = [sector_returns[day] for day in common]
            left_mean = sum(left) / len(left)
            right_mean = sum(right) / len(right)
            numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
            denominator = math.sqrt(sum((a - left_mean) ** 2 for a in left) * sum((b - right_mean) ** 2 for b in right))
            correlation = numerator / denominator if denominator else 0.0
        dependency_score = _clamp(abs(correlation) * 100) if correlation is not None else None
        level = "高" if dependency_score is not None and dependency_score >= 65 else "中" if dependency_score is not None and dependency_score >= 35 else "低" if dependency_score is not None else "以Alpha证据观察"
        alpha_20 = next((row.get("individual_alpha_pct") for row in relative.get("windows", []) if row["days"] == 20), None)
        independence = "高" if alpha_20 is not None and alpha_20 >= 5 and (dependency_score or 0) < 65 else "低" if dependency_score is not None and dependency_score >= 70 else "中"
        return {
            "sample_count": len(common),
            "correlation_60d": _round(correlation, 4),
            "dependency_score": _round(dependency_score, 1),
            "dependency_level": level,
            "independence_level": independence,
            "sector_retreat_resilience": "较强" if independence == "高" else "较弱" if independence == "低" else "需要继续验证",
            "formula": "依赖度=近60个共同交易日个股与板块日收益相关系数绝对值×100",
        }

    @staticmethod
    def _build_emotion(stock_rows: list[dict], flow_rows: list[dict], quote: dict, relative: dict) -> dict:
        closes = [_number(row.get("close")) for row in stock_rows]
        closes = [value for value in closes if value is not None]
        volumes = [_number(row.get("volume")) for row in stock_rows]
        volumes = [value for value in volumes if value is not None]
        current = _number(quote.get("price")) or (closes[-1] if closes else None)
        ma5, ma10, ma20 = (_moving_average(closes, window) for window in (5, 10, 20))
        volume_ma5 = _moving_average(volumes, 5)
        volume_ratio = _number(quote.get("volume_ratio"))
        if volume_ratio is None and volumes and volume_ma5:
            volume_ratio = volumes[-1] / volume_ma5
        alpha_5 = next((row.get("individual_alpha_pct") for row in relative.get("windows", []) if row["days"] == 5), None)
        flow_5 = [(_number(row.get("main_net_inflow")) or 0) for row in flow_rows[-5:]]
        flow_positive = sum(value > 0 for value in flow_5) / len(flow_5) * 100 if flow_5 else None
        high20 = max(closes[-20:]) if closes else None
        low20 = min(closes[-20:]) if closes else None
        drawdown = (current / high20 - 1) * 100 if current and high20 else None
        recovery = (current / low20 - 1) * 100 if current and low20 else None
        dimensions = {
            "capital": flow_positive,
            "relative_strength": _clamp(50 + (alpha_5 or 0) * 5) if alpha_5 is not None else None,
            "participation": _clamp((volume_ratio or 0) / 2 * 100) if volume_ratio is not None else None,
            "trend": 85.0 if current and ma5 and ma10 and ma20 and current > ma5 > ma10 > ma20 else 60.0 if current and ma20 and current > ma20 else 30.0,
            "drawdown_repair": _clamp((recovery or 0) * 5) if recovery is not None else None,
            "downside_safety": _clamp(100 + (drawdown or 0) * 5) if drawdown is not None else None,
        }
        score = _average(list(dimensions.values()))
        level = "过热" if score is not None and score >= 82 else "偏热" if score is not None and score >= 68 else "正常" if score is not None and score >= 42 else "偏冷"

        daily_scores = []
        for index in range(max(20, len(closes) - 3), len(closes)):
            partial = closes[:index + 1]
            if len(partial) < 20:
                continue
            p_ma5 = _moving_average(partial, 5)
            p_ma20 = _moving_average(partial, 20)
            ret5 = (partial[-1] / partial[-6] - 1) * 100 if len(partial) >= 6 else 0
            trend_score = 80 if p_ma5 and p_ma20 and partial[-1] > p_ma5 > p_ma20 else 55 if p_ma20 and partial[-1] > p_ma20 else 30
            daily_scores.append(_clamp(trend_score * 0.6 + _clamp(50 + ret5 * 5) * 0.4))
        velocity = daily_scores[-1] - daily_scores[-2] if len(daily_scores) >= 2 else 0.0
        acceleration = (daily_scores[-1] - daily_scores[-2]) - (daily_scores[-2] - daily_scores[-3]) if len(daily_scores) >= 3 else 0.0
        return {
            "score": _round(score, 1),
            "level": level,
            "dimensions": {key: _round(value, 1) for key, value in dimensions.items()},
            "velocity": _round(velocity, 1),
            "acceleration": _round(acceleration, 1),
            "trend": "加速升温" if velocity > 3 and acceleration > 0 else "快速退潮" if velocity < -3 else "温度平稳",
            "observations": {"volume_ratio": _round(volume_ratio), "drawdown_from_20d_high_pct": _round(drawdown), "recovery_from_20d_low_pct": _round(recovery)},
        }

    @staticmethod
    def _build_catalysts(announcements: list[dict], policy_items: list[dict], decision_date: date) -> dict:
        items = []
        grade_a = ("业绩", "中标", "合同", "回购", "增持", "分红", "获批", "订单", "扭亏")
        negative = ("减持", "处罚", "立案", "亏损", "诉讼", "终止", "风险提示", "退市")
        for row in [*announcements, *policy_items]:
            published = _date(row.get("published_at"))
            if published and published > decision_date:
                continue
            title = str(row.get("title") or "")
            if any(word in title for word in negative):
                grade, direction, credibility = "A", "negative", "高"
            elif any(word in title for word in grade_a):
                grade, direction, credibility = "A", "positive", "高"
            elif row.get("scope") == "domestic_policy":
                grade, direction, credibility = "B", str(row.get("impact") or "neutral"), "高"
            elif row.get("scope") == "company_announcement":
                grade, direction, credibility = "B", "neutral", "高"
            else:
                grade, direction, credibility = "C", str(row.get("impact") or "neutral"), "中"
            age = (decision_date - published).days if published else None
            items.append({
                "title": title,
                "grade": grade,
                "direction": direction,
                "credibility": credibility,
                "published_at": published.isoformat() if published else None,
                "age_days": age,
                "realisation": "已公告事实" if row.get("scope") == "company_announcement" else "政策影响仍需价格与业绩验证",
                "source": row.get("source"),
                "url": row.get("url"),
            })
        grade_order = {"A": 0, "B": 1, "C": 2, "D": 3}
        items.sort(key=lambda item: (grade_order.get(item["grade"], 9), item.get("published_at") or ""), reverse=False)
        positive_count = sum(item["direction"] == "positive" for item in items)
        negative_count = sum(item["direction"] == "negative" for item in items)
        return {
            "items": items[:12],
            "highest_grade": items[0]["grade"] if items else "无新增事件",
            "net_direction": "positive" if positive_count > negative_count else "negative" if negative_count > positive_count else "neutral",
            "resolution": "最近公开窗口无新增公司/政策催化" if not items else "已按公告和官方政策事实分级",
        }

    @staticmethod
    def _build_expectation(consensus: dict, fundamentals: dict, fetched_at: str) -> dict:
        coverage_status = str(consensus.get("_coverage_status") or "covered")
        estimates = []
        actual_eps = None
        for index in range(1, 5):
            year = _integer(consensus.get(f"YEAR{index}"))
            eps = _number(consensus.get(f"EPS{index}"))
            mark = str(consensus.get(f"YEAR_MARK{index}") or "")
            if year is None or eps is None:
                continue
            estimates.append({"year": year, "eps": _round(eps, 4), "type": "actual" if mark == "A" else "estimate"})
            if mark == "A":
                actual_eps = eps
        future = next((item for item in estimates if item["type"] == "estimate" and item["eps"] is not None), None)
        expected_growth = (future["eps"] / actual_eps - 1) * 100 if future and actual_eps not in (None, 0) else None
        actual_growth = _number((fundamentals.get("metrics") or {}).get("net_profit_growth_pct"))
        gap = actual_growth - expected_growth if actual_growth is not None and expected_growth is not None else None
        if coverage_status == "historical_excluded":
            state = "历史快照不引入当前一致预期"
            warning = "当前一致预期没有可验证的历史发布时间，已按PIT规则从历史决策中排除"
        elif coverage_status == "no_analyst_coverage":
            state = "无机构一致预期覆盖"
            warning = "公开一致预期源已核验，当前覆盖机构数为0；系统保留实际财报增速，不虚构预测值"
        else:
            state = "正预期差" if gap is not None and gap >= 5 else "负预期差" if gap is not None and gap <= -5 else "基本匹配"
            warning = "一致预期接口不提供历史发布时间，仅用于当前研究快照，不进入历史回测"
        analyst_count = _integer(consensus.get("RATING_ORG_NUM"))
        if coverage_status == "no_analyst_coverage":
            analyst_count = 0
        return {
            "availability": coverage_status,
            "analyst_count": analyst_count,
            "rating_counts": {
                "buy": _integer(consensus.get("RATING_BUY_NUM")),
                "add": _integer(consensus.get("RATING_ADD_NUM")),
                "neutral": _integer(consensus.get("RATING_NEUTRAL_NUM")),
                "reduce": _integer(consensus.get("RATING_REDUCE_NUM")),
                "sell": _integer(consensus.get("RATING_SALE_NUM")),
            },
            "eps_path": estimates,
            "expected_eps_growth_pct": _round(expected_growth),
            "latest_actual_profit_growth_pct": _round(actual_growth),
            "expectation_gap_proxy_pct": _round(gap),
            "state": state,
            "target_price_range": [_number(consensus.get("DEC_AIMPRICEMIN")), _number(consensus.get("DEC_AIMPRICEMAX"))],
            "snapshot_at": fetched_at,
            "historical_backtest_eligible": False,
            "formula": "预期增速=首个预测EPS/最近实际EPS-1；预期差代理=最新利润同比-预期EPS增速",
            "warning": warning,
        }

    @staticmethod
    def _build_valuation(
        quote: dict,
        history: dict,
        members: list[dict],
        fundamentals: dict,
        consensus: dict,
        sector_names: tuple[Any, ...] = (),
    ) -> dict:
        history_rows = history.get("history") or []
        history_values = [_number(row.get("pe_ttm")) for row in history_rows]
        positive_history = [value for value in history_values if value is not None and value > 0]
        current_pe = next((value for value in reversed(history_values) if value is not None), None)
        if current_pe is None:
            current_pe = _number(quote.get("pe"))
        pe_applicable = current_pe is not None and current_pe > 0
        industry_pes = [_number(row.get("pe")) for row in members]
        industry_pes = [value for value in industry_pes if value is not None and value > 0]
        historical_percentile = _percentile_rank(current_pe, positive_history) if pe_applicable else None
        industry_percentile = _percentile_rank(current_pe, industry_pes) if pe_applicable else None
        expected_growth = None
        for index in range(1, 4):
            if str(consensus.get(f"YEAR_MARK{index}") or "") == "E":
                previous = _number(consensus.get(f"EPS{index - 1}")) if index > 1 else None
                current = _number(consensus.get(f"EPS{index}"))
                if previous not in (None, 0) and current is not None:
                    expected_growth = (current / previous - 1) * 100
                    break
        growth = expected_growth or _number((fundamentals.get("metrics") or {}).get("net_profit_growth_pct"))
        peg = current_pe / growth if pe_applicable and growth is not None and growth > 0 else None
        change_20 = (current_pe / positive_history[-21] - 1) * 100 if pe_applicable and len(positive_history) >= 21 and positive_history[-21] else None
        earnings_state = fundamentals.get("earnings_state")
        current_pb = _number(quote.get("pb"))
        cycle = build_cyclical_valuation(
            sector_names=sector_names,
            current_pe=current_pe,
            current_pb=current_pb,
            market_cap=_number(quote.get("market_cap")),
            fundamentals=fundamentals,
        )
        if cycle.get("is_cyclical"):
            state = str(cycle.get("cycle_state"))
        elif not pe_applicable:
            state = "亏损期：PE不作为估值依据"
        elif historical_percentile is not None and historical_percentile <= 35 and earnings_state == "改善":
            state = "低估 + 盈利改善"
        elif historical_percentile is not None and historical_percentile >= 70 and earnings_state == "恶化":
            state = "高估 + 盈利恶化"
        elif historical_percentile is not None and historical_percentile >= 70:
            state = "高估 + 盈利稳定/增长"
        elif earnings_state == "恶化":
            state = "低估/合理 + 盈利恶化"
        else:
            state = "合理 + 盈利改善/稳定"
        return {
            "current_pe_ttm": _round(current_pe),
            "current_pb": _round(current_pb),
            "pe_applicable": pe_applicable,
            "pe_resolution": (
                "周期行业不把低TTM PE直接视为低估，使用标准化盈利、PB/ROE、现金流与阶段复核"
                if cycle.get("is_cyclical")
                else "使用正PE历史与行业样本" if pe_applicable
                else "当前净利润为负，保留原始PE并改看PB、现金流与资产质量"
            ),
            "pe_percentile_3y": historical_percentile,
            "industry_pe_percentile": industry_percentile,
            "industry_positive_pe_samples": len(industry_pes),
            "pe_history_samples": len(positive_history),
            "earnings_growth_pct": _round(growth),
            "peg_proxy": _round(peg, 3),
            "pe_change_20d_pct": _round(change_20),
            "state": state,
            "source": f"{history.get('source') or 'eastmoney'}+行业成分股实时估值",
            "data_date": history.get("history_end"),
            **cycle,
        }

    @staticmethod
    def _build_risk_reward(stock_rows: list[dict], quote: dict, valuation: dict, emotion: dict) -> dict:
        recent = stock_rows[-60:]
        current = _number(quote.get("price")) or (_number(recent[-1].get("close")) if recent else None)
        highs = [_number(row.get("high")) for row in recent[-20:]]
        lows = [_number(row.get("low")) for row in recent[-20:]]
        closes = [_number(row.get("close")) for row in recent]
        highs = [value for value in highs if value is not None]
        lows = [value for value in lows if value is not None]
        closes = [value for value in closes if value is not None]
        support = min(lows) if lows else None
        resistance = max(highs) if highs else None
        true_ranges = []
        previous = None
        for row in recent[-15:]:
            high, low, close = (_number(row.get(key)) for key in ("high", "low", "close"))
            if high is None or low is None:
                previous = close
                continue
            true_ranges.append(max(high - low, abs(high - previous) if previous else 0, abs(low - previous) if previous else 0))
            previous = close
        atr14 = sum(true_ranges[-14:]) / len(true_ranges[-14:]) if true_ranges else None
        potential_up = max((resistance / current - 1) * 100 if current and resistance else 0, (atr14 / current * 2 * 100) if current and atr14 else 0)
        potential_down = max((1 - support / current) * 100 if current and support else 0, (atr14 / current * 1.5 * 100) if current and atr14 else 0)
        ratio = potential_up / potential_down if potential_down > 0 else None
        max_drawdown = None
        if closes:
            peak = closes[0]
            drawdowns = []
            for close in closes:
                peak = max(peak, close)
                drawdowns.append((close / peak - 1) * 100 if peak else 0)
            max_drawdown = min(drawdowns)
        valuation_percentile = _number(valuation.get("pe_percentile_3y")) or 0
        if valuation.get("pe_inversion_risk"):
            valuation_risk = "高"
            valuation_risk_reason = "周期盈利高位触发PE反向风险"
        elif valuation.get("is_cyclical") and valuation.get("cycle_phase") in {"peak", "contraction"}:
            valuation_risk = "中"
            valuation_risk_reason = f"周期阶段为{valuation.get('cycle_phase_label')}，低PE不作为安全边际"
        else:
            valuation_risk = "高" if valuation_percentile >= 80 else "中" if valuation_percentile >= 55 else "低"
            valuation_risk_reason = "按三年正PE历史分位"
        return {
            "current_price": _round(current),
            "support": _round(support),
            "resistance": _round(resistance),
            "atr14": _round(atr14),
            "potential_upside_pct": _round(potential_up),
            "potential_downside_pct": _round(potential_down),
            "risk_reward_ratio": _round(ratio, 2),
            "max_drawdown_60d_pct": _round(max_drawdown),
            "valuation_risk": valuation_risk,
            "valuation_risk_reason": valuation_risk_reason,
            "crowding_risk": "高" if emotion.get("level") == "过热" else "中" if emotion.get("level") == "偏热" else "低",
            "scenarios": {
                "bull": _round(current * (1 + potential_up / 100)) if current else None,
                "base": _round(_moving_average(closes, 20)) if closes else None,
                "bear": _round(current * (1 - potential_down / 100)) if current else None,
            },
            "formula": "上行取20日阻力与2ATR较大者；下行取20日支撑与1.5ATR较大者；RR=上行空间/下行空间",
        }

    @staticmethod
    def _build_strategy_fit(
        code: str,
        name: str,
        quote: dict,
        indicators: dict,
        stock_rows: list[dict],
        fundamentals: dict,
        valuation: dict,
        relative: dict,
        risk_reward: dict,
        market_context: dict,
        now: datetime,
    ) -> dict:
        price = _number(quote.get("price"))
        cap_yi = (_number(quote.get("market_cap")) or 0) / 1e8
        change = _number(quote.get("change_pct"))
        volume_ratio = _number(quote.get("volume_ratio"))
        turnover = _number(quote.get("turnover"))
        ma5 = _number(indicators.get("ma5"))
        ma10 = _number(indicators.get("ma10"))
        ma20 = _number(indicators.get("ma20"))
        closes = [_number(row.get("close")) for row in stock_rows]
        closes = [value for value in closes if value is not None]
        ma30 = _moving_average(closes, 30)
        volumes = [_number(row.get("volume")) for row in stock_rows[-3:]]
        stepped_volume = len(volumes) == 3 and all(value is not None for value in volumes) and volumes[0] < volumes[1] < volumes[2]
        excluded = "ST" in name.upper() or "退" in name or code.startswith(("688", "689", "300", "301", "302")) or (price is not None and price < 2)
        index_metrics = ((market_context.get("market_state") or {}).get("dimensions") or [])
        trend_dimension = next((item for item in index_metrics if item.get("id") == "trend"), {})
        market_above_ma20 = _number((trend_dimension.get("metrics") or {}).get("distance_ma20_pct"))
        market_ok = market_above_ma20 is not None and market_above_ma20 > 0
        tail_conditions = [
            {"key": "market", "label": "上证 > MA20", "passed": market_ok, "value": market_above_ma20},
            {"key": "market_cap", "label": "市值50-200亿", "passed": 50 <= cap_yi <= 200, "value": _round(cap_yi)},
            {"key": "change", "label": "涨幅3%-5%", "passed": change is not None and 3 <= change <= 5, "value": change},
            {"key": "volume_ratio", "label": "量比 > 1.2", "passed": volume_ratio is not None and volume_ratio > 1.2, "value": volume_ratio},
            {"key": "turnover", "label": "换手率5%-10%", "passed": turnover is not None and 5 <= turnover <= 10, "value": turnover},
            {"key": "moving_averages", "label": "MA10>MA20>MA30且价格>MA5", "passed": all(value is not None for value in (price, ma5, ma10, ma20, ma30)) and ma10 > ma20 > ma30 and price > ma5, "value": {"ma5": ma5, "ma10": ma10, "ma20": ma20, "ma30": _round(ma30)}},
            {"key": "volume_steps", "label": "近3日量阶梯放大", "passed": stepped_volume, "value": volumes},
            {"key": "exclusion", "label": "排除ST/科创/创业/低价", "passed": not excluded, "value": not excluded},
        ]
        passed = sum(item["passed"] for item in tail_conditions)
        tail_score = passed / len(tail_conditions) * 100
        valuation_score = _number(valuation.get("long_term_value_score"))
        if valuation_score is None and _number(valuation.get("pe_percentile_3y")) is not None:
            valuation_score = 100 - _number(valuation.get("pe_percentile_3y"))
        long_score = _average([
            fundamentals.get("earnings_quality_score"),
            valuation_score,
            _clamp((risk_reward.get("risk_reward_ratio") or 0) / 3 * 100),
        ])
        alpha_score = _number(relative.get("individual_alpha_score"))
        trend_score = _average([alpha_score, 85 if ma5 and ma10 and ma20 and ma5 > ma10 > ma20 else 45, 100 - (risk_reward.get("crowding_risk") == "高") * 60])
        phase = "14:55执行窗口" if now.weekday() < 5 and now.hour == 14 and 50 <= now.minute <= 59 else "研究/观察窗口"
        return {
            "long_term": {"score": _round(long_score, 1), "fit": "高度适配" if (long_score or 0) >= 75 else "适配" if (long_score or 0) >= 60 else "谨慎" if (long_score or 0) >= 45 else "不适配"},
            "trend": {"score": _round(trend_score, 1), "fit": "高度适配" if (trend_score or 0) >= 75 else "适配" if (trend_score or 0) >= 60 else "谨慎" if (trend_score or 0) >= 45 else "不适配"},
            "tail_1455": {
                "score": _round(tail_score, 1),
                "fit": "高度适配" if passed == len(tail_conditions) else "谨慎" if passed >= 6 else "不适配",
                "phase": phase,
                "conditions": tail_conditions,
                "all_conditions_passed": passed == len(tail_conditions),
                "volume_ratio_threshold": 1.2,
            },
            "auction_confirmation": {
                "fit": "等待次日09:25真实竞价窗口" if phase == "14:55执行窗口" else "当前不在竞价确认窗口",
                "required_conditions": ["竞价量比 > 3", "高开幅度 2%-5%"],
                "execution_ready": False,
            },
        }

    @staticmethod
    def _build_decision(
        market_context: dict,
        fundamentals: dict,
        valuation: dict,
        relative: dict,
        emotion: dict,
        catalysts: dict,
        expectation: dict,
        risk_reward: dict,
        strategy_fit: dict,
        is_realtime: bool,
    ) -> dict:
        market_action = str(((market_context.get("market_cognition") or {}).get("final_action") or "observe"))
        quality = fundamentals.get("earnings_quality")
        rr = _number(risk_reward.get("risk_reward_ratio"))
        alpha = _number(relative.get("individual_alpha_score"))
        tail_ready = bool((strategy_fit.get("tail_1455") or {}).get("all_conditions_passed"))
        auction_ready = bool((strategy_fit.get("auction_confirmation") or {}).get("execution_ready"))
        reasons = []
        if market_action in {"no_trade", "observe"}:
            reasons.append(f"市场工作台当前行动为 {market_action}")
        if quality == "低":
            reasons.append("盈利质量处于低档")
        if valuation.get("state") == "高估 + 盈利恶化":
            reasons.append("估值处于高位且盈利恶化")
        if valuation.get("pe_inversion_risk"):
            reasons.append("周期盈利处于高位，低TTM PE存在反向陷阱")
        if emotion.get("level") == "过热":
            reasons.append("个股情绪过热，拥挤风险较高")
        if rr is not None and rr < 1.2:
            reasons.append(f"风险收益比仅 {rr:.2f}")

        if quality == "低" or valuation.get("state") == "高估 + 盈利恶化" or valuation.get("pe_inversion_risk"):
            state = "AVOID"
        elif market_action == "no_trade":
            state = "NO_TRADE"
        elif is_realtime and market_action == "execute" and tail_ready and auction_ready and (rr or 0) >= 1.5:
            state = "EXECUTE"
        elif market_action in {"execute", "caution"} and quality in {"高", "中"} and (rr or 0) >= 1.2 and (alpha or 0) >= 50:
            state = "CAUTION"
        else:
            state = "OBSERVE"
        if state == "EXECUTE" and not auction_ready:
            state = "OBSERVE"
        if not reasons:
            reasons = ["公司、估值、Alpha与风险收益条件需继续按策略窗口确认"]
        invalidation = [
            f"跌破20日支撑 {risk_reward.get('support')}" if risk_reward.get("support") else "跌破最近确认支撑",
            "盈利、扣非利润或经营现金流转为同步恶化",
            "板块退潮且个股Alpha连续转负",
            "重大负面公告、减持、处罚或监管风险出现",
        ]
        return {
            "state": state,
            "label": {"EXECUTE": "执行", "CAUTION": "谨慎", "OBSERVE": "观察", "AVOID": "放弃", "NO_TRADE": "不交易"}[state],
            "reasons": reasons,
            "invalidation_conditions": invalidation,
            "market_action": market_action,
            "execution_guard": "结构化规则决定状态；AI只能解释，不能修改状态或交易数据",
        }

    @classmethod
    def _best_previous_payload(cls, payloads: list[dict]) -> dict | None:
        candidates = [payload for payload in payloads if isinstance(payload, dict)]
        if not candidates:
            return None

        def score(payload: dict) -> tuple[int, int, date]:
            data_date = _date((payload.get("meta") or {}).get("data_date")) or date.min
            return (
                int(bool(payload.get("available"))),
                len(cls._verified_source_keys(payload)),
                data_date,
            )

        return max(candidates, key=score)

    async def _load_latest(self, code: str, as_of: date | None = None) -> dict | None:
        try:
            async with async_session() as session:
                statement = select(StockDecisionProfile).where(
                    StockDecisionProfile.stock_code == code,
                    StockDecisionProfile.contract_version == CONTRACT_VERSION,
                )
                if as_of:
                    statement = statement.where(StockDecisionProfile.decision_date <= as_of)
                rows = list((await session.execute(
                    statement.order_by(desc(StockDecisionProfile.decision_date), desc(StockDecisionProfile.updated_at)).limit(30)
                )).scalars().all())
            return self._best_previous_payload([
                dict(row.payload) for row in rows if isinstance(row.payload, dict)
            ])
        except Exception:
            return None

    async def _persist(self, payload: dict) -> None:
        decision_date = _date((payload.get("meta") or {}).get("decision_date"))
        data_date = _date((payload.get("meta") or {}).get("data_date"))
        if decision_date is None:
            return
        try:
            async with async_session() as session:
                row = (await session.execute(select(StockDecisionProfile).where(
                    StockDecisionProfile.stock_code == payload["company"]["stock_code"],
                    StockDecisionProfile.decision_date == decision_date,
                    StockDecisionProfile.contract_version == CONTRACT_VERSION,
                ))).scalar_one_or_none()
                values = {
                    "stock_name": payload["company"].get("stock_name"),
                    "data_date": data_date,
                    "decision_state": payload["decision"]["state"],
                    "source": str((payload.get("meta") or {}).get("source") or "public_sources"),
                    "is_realtime": bool((payload.get("meta") or {}).get("is_realtime")),
                    "payload": payload,
                    "evidence": payload.get("evidence") or [],
                    "updated_at": datetime.utcnow(),
                }
                if row:
                    for key, value in values.items():
                        setattr(row, key, value)
                else:
                    session.add(StockDecisionProfile(
                        stock_code=payload["company"]["stock_code"],
                        decision_date=decision_date,
                        contract_version=CONTRACT_VERSION,
                        **values,
                    ))
                await session.commit()
        except Exception as exc:
            print(f"Stock decision profile persistence failed: {type(exc).__name__}")

    @staticmethod
    def _verified_source_keys(payload: dict | None) -> set[str]:
        sources = ((payload or {}).get("data_audit") or {}).get("sources") or []
        return {
            str(item.get("key"))
            for item in sources
            if item.get("key") and item.get("status") in {"observed", "cached_fallback"}
        }

    def _prefer_verified_snapshot(
        self,
        previous: dict | None,
        candidate: dict,
        attempted_at: datetime,
    ) -> dict | None:
        """Keep an audited snapshot when a refresh loses a verified source."""
        if not previous or not previous.get("available"):
            return None
        previous_keys = self._verified_source_keys(previous)
        candidate_keys = self._verified_source_keys(candidate)
        lost_keys = sorted(previous_keys - candidate_keys)
        if not lost_keys:
            return None

        fallback = deepcopy(previous)
        fallback_meta = dict(fallback.get("meta") or {})
        fallback_meta.update({
            "cache_used": True,
            "refresh_attempted_at": attempted_at.isoformat(),
            "refresh_warning": f"本次刷新有 {len(lost_keys)} 个公开源异常，继续使用最近已核验快照",
        })
        fallback["meta"] = fallback_meta

        audit = dict(fallback.get("data_audit") or {})
        retained_sources = []
        for source in audit.get("sources") or []:
            item = dict(source)
            if item.get("status") in {"observed", "cached_fallback"}:
                item["status"] = "cached_fallback"
            retained_sources.append(item)
        audit.update({
            "sources": retained_sources,
            "refresh_warning": fallback_meta["refresh_warning"],
            "refresh_failed_sources": lost_keys,
        })
        fallback["data_audit"] = audit
        return fallback

    async def history(self, stock_code: str, limit: int = 30) -> list[dict]:
        code = normalize_stock_code(stock_code)
        async with async_session() as session:
            rows = list((await session.execute(
                select(StockDecisionProfile)
                .where(StockDecisionProfile.stock_code == code)
                .order_by(desc(StockDecisionProfile.decision_date), desc(StockDecisionProfile.updated_at))
                .limit(min(max(limit, 1), 120))
            )).scalars().all())
        return [{
            "id": row.id,
            "stock_code": row.stock_code,
            "stock_name": row.stock_name,
            "decision_date": row.decision_date.isoformat(),
            "data_date": row.data_date.isoformat() if row.data_date else None,
            "decision_state": row.decision_state,
            "is_realtime": bool(row.is_realtime),
            "contract_version": row.contract_version,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "summary": ((row.payload or {}).get("state_matrix") or {}).get("label"),
        } for row in rows]

    async def get(
        self,
        stock_code: str,
        *,
        as_of: date | None = None,
        force: bool = False,
    ) -> dict:
        code = normalize_stock_code(stock_code)
        requested_date = as_of or shanghai_now().date()
        cache_key = (code, requested_date.isoformat())
        cached = self._memory.get(cache_key)
        if cached and not force and time.monotonic() - cached[0] <= self._CACHE_SECONDS:
            payload = dict(cached[1])
            payload["meta"] = {**payload["meta"], "cache_used": True}
            return payload

        async with self._locks[code]:
            cached = self._memory.get(cache_key)
            if cached and not force and time.monotonic() - cached[0] <= self._CACHE_SECONDS:
                return dict(cached[1])

            now = shanghai_now()
            historical_mode = as_of is not None and requested_date < now.date()
            previous = await self._load_latest(code, requested_date)
            initial = await asyncio.gather(
                self._safe("company", self._company_profile(code)),
                self._safe("financial", self._financial_rows(code)),
                self._safe("balance", self._balance_rows(code)),
                self._safe("operations", self._main_operations(code)),
                self._safe("equity", self._equity_rows(code)),
                self._safe("consensus", self._consensus(code)),
                self._safe("quote", self._quote(code)),
                self._safe("stock_history", collector.fetch_stock_price_history(code, 420)),
                self._safe("market_history", collector.fetch_shanghai_index_history(420)),
                self._safe("fund_flow", self._fund_flow(code, requested_date)),
                self._safe("announcements", macro_policy_news_collector.get_stock_announcements_audit([code], 1)),
                self._safe("macro", macro_policy_news_collector.get_context()),
                self._safe("market_context", market_decision_workbench_service.get(force=force), timeout=24),
                self._safe("industry_directory", self._industry_directory()),
                self._safe("legacy_industry_directory", self._legacy_industry_directory()),
            )
            raw = {item["key"]: item for item in initial}
            company_raw = raw["company"]["value"] or {}
            quote = raw["quote"]["value"] or {}
            stock_history_payload = raw["stock_history"]["value"] or {}
            stock_rows = [
                row for row in (stock_history_payload.get("history") or [])
                if (_date(row.get("trade_date")) or date.max) <= requested_date
            ]
            data_date = _date(stock_rows[-1].get("trade_date")) if stock_rows else None
            if data_date is None and previous:
                data_date = _date((previous.get("meta") or {}).get("data_date"))
            decision_date = data_date or requested_date

            if historical_mode and stock_rows:
                latest_bar = stock_rows[-1]
                previous_bar = stock_rows[-2] if len(stock_rows) >= 2 else {}
                quote = {
                    **quote,
                    "price": _number(latest_bar.get("close")),
                    "previous_close": _number(previous_bar.get("close")),
                    "open": _number(latest_bar.get("open")),
                    "high": _number(latest_bar.get("high")),
                    "low": _number(latest_bar.get("low")),
                    "volume": _number(latest_bar.get("volume")),
                    "amount": _number(latest_bar.get("amount")),
                    "change_amount": _number(latest_bar.get("change_amount")),
                    "change_pct": _number(latest_bar.get("change_pct")),
                    "turnover": _number(latest_bar.get("turnover")),
                    "volume_ratio": None,
                    "pe": None,
                    "pb": None,
                    "market_cap": None,
                    "circulating_market_cap": None,
                    "is_realtime": False,
                    "source": f"{stock_history_payload.get('source') or 'tencent'}_historical_daily",
                    "source_updated_at": f"{decision_date.isoformat()}T15:00:00+08:00",
                }

            financials = self._build_financials(raw["financial"]["value"] or [], raw["balance"]["value"] or [], decision_date)
            financial_report = _date(financials.get("report_date"))
            operations = self._build_operations(raw["operations"]["value"] or [], decision_date, financial_report)
            equity = self._select_equity(raw["equity"]["value"] or [], decision_date)
            price = _number(quote.get("price")) or (_number(stock_rows[-1].get("close")) if stock_rows else None)
            total_shares = _number(equity.get("TOTAL_SHARES")) or _number(quote.get("total_shares"))
            circulating_shares = _number(equity.get("UNLIMITED_SHARES")) or _number(quote.get("circulating_shares"))
            disclosed_free_shares = _number(equity.get("FREELIQCI_SHARES")) or _number(equity.get("FREE_SHARES"))
            free_shares = disclosed_free_shares or circulating_shares
            free_float_method = "F10自由流通股本" if disclosed_free_shares else "F10无限售流通股本代理"
            free_float_cap = free_shares * price if free_shares and price else None
            total_cap = _number(quote.get("market_cap")) or (total_shares * price if total_shares and price else None)
            circulating_cap = _number(quote.get("circulating_market_cap")) or (circulating_shares * price if circulating_shares and price else None)
            if historical_mode and _number(quote.get("turnover")) is None and circulating_shares:
                historical_volume = _number(quote.get("volume"))
                if historical_volume is not None:
                    quote["turnover"] = historical_volume / circulating_shares * 100
            stock_name = str(quote.get("name") or company_raw.get("SECURITY_NAME_ABBR") or stock_history_payload.get("name") or "")

            sector_targets = [
                quote.get("sector"),
                (raw["consensus"]["value"] or {}).get("INDUSTRY_BOARD"),
                str(company_raw.get("BOARD_NAME_LEVEL") or "").split("-")[-1],
                company_raw.get("EM2016"),
            ]
            directory = raw["industry_directory"]["value"] or []
            sector_row = _sector_match([str(item or "") for item in sector_targets], directory)
            sector_name = str((sector_row or {}).get("name") or quote.get("sector") or company_raw.get("BOARD_NAME_LEVEL") or company_raw.get("EM2016") or "未归类")
            sector_code = str((sector_row or {}).get("code") or "")
            legacy_directory = raw["legacy_industry_directory"]["value"] or []
            benchmark_row = _sector_exact_match(
                [str(item or "") for item in [*sector_targets, sector_name]],
                legacy_directory,
            )
            benchmark_code = str((benchmark_row or {}).get("code") or sector_code)
            benchmark_name = str((benchmark_row or {}).get("name") or sector_name)
            if sector_code:
                second = await asyncio.gather(
                    self._safe(
                        "sector_history",
                        self._sector_history(sector_code, benchmark_code, decision_date),
                        timeout=40,
                    ),
                    self._safe("sector_members", collector.fetch_all_board_stocks(sector_code, sector_name)),
                )
                raw.update({item["key"]: item for item in second})
            else:
                raw["sector_history"] = {"value": {}, "error": "SectorBoardNotResolved"}
                raw["sector_members"] = {"value": {}, "error": "SectorBoardNotResolved"}
            sector_history_payload = raw["sector_history"]["value"] or {}
            sector_history = sector_history_payload.get("history") or []
            members = (raw["sector_members"]["value"] or {}).get("stocks") or []
            if (
                sector_code
                and members
                and sum(_number(row.get("change_pct")) is not None for row in sector_history) < 15
            ):
                proxy_result = await self._safe(
                    "sector_constituent_proxy",
                    self._constituent_sector_history(members, sector_code, sector_name, decision_date),
                    timeout=22,
                )
                proxy_payload = proxy_result.get("value") or {}
                if len(proxy_payload.get("history") or []) >= 15:
                    raw["sector_history"] = proxy_result
                    sector_history_payload = proxy_payload
                    sector_history = proxy_payload["history"]
                    benchmark_code = sector_code
                    benchmark_name = str(proxy_payload.get("benchmark_name") or f"{sector_name}成分等权基准")
            valuation_result = await self._safe("valuation_history", self._valuation_history(code, stock_name, decision_date), timeout=35)
            raw["valuation_history"] = valuation_result
            valuation_history = valuation_result["value"] or {}

            fund_rows = [row for row in (raw["fund_flow"]["value"] or []) if (_date(row.get("date")) or date.max) <= decision_date]
            market_rows = [row for row in (raw["market_history"]["value"] or []) if (_date(row.get("date")) or date.max) <= decision_date]
            capital_impact = self._build_capital_impact(fund_rows, free_float_cap)
            fund_sources = sorted({str(row.get("source") or "eastmoney") for row in fund_rows})
            if fund_sources:
                capital_impact["source"] = "+".join(fund_sources) + "+F10自由流通股本"
            relative = self._build_relative(stock_rows, market_rows, sector_history, fund_rows, free_float_cap)
            relative["sector_benchmark"] = {
                "code": sector_history_payload.get("benchmark_code") or benchmark_code or None,
                "name": benchmark_name,
                "source": sector_history_payload.get("source"),
            }
            indicators = calculate_indicators(stock_rows)
            sector_change = _number((sector_row or {}).get("change_pct"))
            sector_role = self._build_sector_role(code, quote, members, relative, sector_name, sector_change)
            sector_recommendations = self._build_sector_recommendations(
                code,
                quote,
                members,
                sector_name,
                sector_code,
                decision_date,
                historical_mode=historical_mode,
            )
            sector_dependency = self._build_dependency(stock_rows, sector_history, relative)
            sector_dependency["benchmark"] = relative["sector_benchmark"]
            emotion = self._build_emotion(stock_rows, fund_rows, quote, relative)
            market_context_for_reflexivity = raw["market_context"]["value"] or {}
            # Skill 10 consumes the same PIT daily bars already loaded for the
            # individual profile.  Current constituent breadth is deliberately
            # omitted in historical mode because the live constituent list is
            # not a point-in-time universe.
            if historical_mode:
                historical_sector_return = (
                    _number(sector_history[-1].get("change_pct"))
                    if sector_history else None
                )
                reflexivity_context = {
                    "market_state": "历史模式（仅使用截止日观察）",
                    "sector_state": "历史板块状态按日线核验",
                    "sector_return_1d": _pct_field_to_decimal(historical_sector_return),
                    "stock_alpha_score": relative.get("individual_alpha_score"),
                }
            else:
                member_changes = [_number(item.get("change_pct")) for item in members]
                member_changes = [value for value in member_changes if value is not None]
                member_average = sum(member_changes) / len(member_changes) if member_changes else None
                member_breadth = sum(value > 0 for value in member_changes) / len(member_changes) * 100 if member_changes else None
                member_flows = [_number(item.get("main_net_inflow")) for item in members]
                member_flows = [value for value in member_flows if value is not None]
                sector_strength = _clamp(50 + (sector_change or 0) * 8 + ((member_breadth or 50) - 50) * 0.35 + _clamp((sum(member_flows) if member_flows else 0) / 1e8, -15, 15))
                sector_state = "强化" if sector_strength >= 72 else "启势" if sector_strength >= 58 else "退潮" if sector_strength <= 34 else "分歧" if sector_strength <= 46 else "震荡"
                latest_market_return = _number(market_rows[-1].get("change_pct")) if market_rows else None
                reflexivity_context = {
                    "market_state": (market_context_for_reflexivity.get("market_state") or {}).get("state_label") if isinstance(market_context_for_reflexivity, dict) else None,
                    "sector_state": sector_state,
                    "sector_return_1d": _pct_field_to_decimal(sector_change),
                    "sector_breadth": member_breadth,
                    "sector_strength": sector_strength,
                    "alpha_density": sum(value > (member_average or 0) for value in member_changes) / len(member_changes) * 100 if member_changes else None,
                    "stock_alpha_score": relative.get("individual_alpha_score"),
                    # Emotion score is a local participation/crowding proxy,
                    # not an assertion about a specific participant.
                    "crowding_score": emotion.get("score"),
                    "fomo_score": emotion.get("score") if emotion.get("level") in {"偏热", "过热"} else None,
                    "panic_score": _clamp(60 - (emotion.get("score") or 60)) if emotion.get("score") is not None else None,
                    "market_return_1d": _pct_field_to_decimal(latest_market_return),
                }
            behavior_reflexivity = build_reflexivity_diagnosis(
                stock_rows,
                as_of=decision_date,
                context=reflexivity_context,
                symbol=code,
                name=stock_name,
            )
            consensus = raw["consensus"]["value"] or {}
            consensus_for_analysis = (
                {"_coverage_status": "historical_excluded"}
                if historical_mode else consensus
            )
            valuation = self._build_valuation(
                {**quote, "market_cap": total_cap},
                valuation_history,
                members,
                financials,
                consensus_for_analysis,
                (
                    sector_name,
                    quote.get("sector"),
                    company_raw.get("BOARD_NAME_LEVEL"),
                    company_raw.get("EM2016"),
                ),
            )
            announcement_payload = raw["announcements"]["value"] or {}
            announcements = (announcement_payload.get("announcements") or {}).get(code, [])
            macro = raw["macro"]["value"] or {}
            sector_terms = macro_policy_news_collector.sector_terms(sector_name)
            policy_items = [
                item for item in macro.get("policy_items") or []
                if any(term and term in str(item.get("title") or "") for term in sector_terms)
            ]
            catalysts = self._build_catalysts(announcements, policy_items, decision_date)
            expectation = self._build_expectation(consensus_for_analysis, financials, now.isoformat())
            risk_reward = self._build_risk_reward(stock_rows, quote, valuation, emotion)
            market_context = raw["market_context"]["value"] or {}
            strategy_fit = self._build_strategy_fit(
                code, stock_name, quote, indicators, stock_rows, financials, valuation,
                relative, risk_reward, market_context, now,
            )
            reflexivity_gate = behavior_reflexivity.get("gate") or {}
            strategy_fit["behavior_reflexivity_gate"] = reflexivity_gate
            tail_1455 = strategy_fit.get("tail_1455") or {}
            tail_conditions = list(tail_1455.get("conditions") or [])
            tail_conditions.append({
                "key": "behavior_reflexivity",
                "label": "行为反身性不处于高位衰减/负向加速",
                "passed": reflexivity_gate.get("status") != "BLOCK",
                "value": behavior_reflexivity.get("candidate_type"),
            })
            tail_1455["conditions"] = tail_conditions
            tail_1455["all_conditions_passed"] = all(bool(item.get("passed")) for item in tail_conditions)
            tail_1455["score"] = _round(sum(bool(item.get("passed")) for item in tail_conditions) / len(tail_conditions) * 100, 1) if tail_conditions else None
            if reflexivity_gate.get("status") == "BLOCK":
                tail_1455["fit"] = "不适配"
            strategy_fit["tail_1455"] = tail_1455
            is_realtime = bool(
                not historical_mode
                and quote.get("is_realtime")
                and decision_date == now.date()
                and is_a_share_market_session(now)
            )
            decision = self._build_decision(
                market_context, financials, valuation, relative, emotion, catalysts,
                expectation, risk_reward, strategy_fit, is_realtime,
            )

            profile_industry = str(company_raw.get("BOARD_NAME_LEVEL") or company_raw.get("EM2016") or sector_name)
            company = {
                "stock_code": code,
                "stock_name": stock_name,
                "legal_name": company_raw.get("ORG_NAME"),
                "security_type": company_raw.get("SECURITY_TYPE"),
                "exchange": company_raw.get("TRADE_MARKET"),
                "listing_date": str(company_raw.get("LISTING_DATE") or "")[:10] or None,
                "founded_date": str(company_raw.get("FOUND_DATE") or "")[:10] or None,
                "industry": profile_industry.split("-")[0] if profile_industry else sector_name,
                "sub_industry": profile_industry,
                "industry_board": {"code": sector_code or None, "name": sector_name},
                "sector_benchmark": {
                    "code": sector_history_payload.get("benchmark_code") or benchmark_code or None,
                    "name": benchmark_name,
                },
                "main_business": company_raw.get("MAIN_BUSINESS"),
                "business_scope": company_raw.get("BUSINESS_SCOPE"),
                "actual_controller": company_raw.get("ACTUAL_HOLDER"),
                "company_profile": company_raw.get("ORG_PROFILE"),
                "current_price": _round(price),
                "total_market_cap": _round(total_cap, 0),
                "circulating_market_cap": _round(circulating_cap, 0),
                "free_float_market_cap": _round(free_float_cap, 0),
                "total_shares": _round(total_shares, 0),
                "circulating_shares": _round(circulating_shares, 0),
                "free_float_shares": _round(free_shares, 0),
                "free_float_method": free_float_method,
                "equity_data_date": str(equity.get("END_DATE") or "")[:10] or None,
                "revenue_structure": operations,
                "core_products": [item["name"] for item in operations.get("products", [])[:5]],
                "core_customers": {
                    "names": [],
                    "disclosure_status": "公司未在当前结构化公开披露中列示客户名称",
                    "integrity_rule": "不根据渠道、行业或传闻虚构客户名称",
                },
                "source": "东方财富公司资料、主营构成、股本结构和核验行情",
            }

            state_matrix = {
                "label": (
                    "高质量趋势型" if financials.get("earnings_quality") == "高" and (relative.get("individual_alpha_score") or 0) >= 60 and emotion.get("level") != "过热"
                    else "高度板块依赖型" if sector_dependency.get("dependency_level") == "高"
                    else "早期观察型" if (
                        financials.get("earnings_state") == "改善"
                        and not valuation.get("pe_inversion_risk")
                        and (valuation.get("pe_percentile_3y") or 100) <= 40
                    )
                    else "条件验证型"
                ),
                "dimensions": {
                    "company": "已核验" if company_raw else "使用最近审计快照",
                    "earnings": financials.get("earnings_state"),
                    "valuation": valuation.get("state"),
                    "capital": capital_impact.get("persistence"),
                    "sector": sector_role.get("role"),
                    "alpha": relative.get("individual_alpha_score"),
                    "emotion": emotion.get("level"),
                    "reflexivity": behavior_reflexivity.get("candidate_label"),
                    "expectation": expectation.get("state"),
                },
            }

            consensus_coverage = str(consensus.get("_coverage_status") or "source_unavailable")
            consensus_detail = (
                "历史模式按PIT规则排除当前一致预期"
                if historical_mode
                else "公开源已核验：当前覆盖机构数为0"
                if consensus_coverage == "no_analyst_coverage"
                else f"当前覆盖机构数{_integer(consensus.get('RATING_ORG_NUM')) or 0}，仅用于当前研究快照"
            )
            source_specs = [
                ("company", "公司本体", bool(company_raw), "东方财富F10", str(company_raw.get("LISTING_DATE") or "")[:10] or None, "公司资料与主营业务"),
                ("operations", "主营构成", bool(operations.get("report_date")), "RPT_F10_FN_MAINOP", operations.get("report_date"), "按最新已披露报告期"),
                ("equity", "股本/自由流通", bool(equity), "RPT_F10_EH_EQUITY", company.get("equity_data_date"), "自由流通市值使用FREELIQCI_SHARES优先"),
                ("financial", "盈利质量", bool(financials.get("available")), "F10财务+资产负债表", financials.get("report_date"), "严格按公告日PIT"),
                ("quote", "行情/市值", bool(price), quote.get("source") or "核验行情", decision_date.isoformat(), "交易时段实时，其他时段最近收盘"),
                ("stock_history", "个股历史", len(stock_rows) >= 30, stock_history_payload.get("source") or "腾讯", decision_date.isoformat(), f"{len(stock_rows)}条前复权日线"),
                ("market_history", "大盘历史", len(market_rows) >= 30, "腾讯上证指数", decision_date.isoformat(), f"{len(market_rows)}条日线"),
                ("fund_flow", "资金流", len(fund_rows) >= 5, capital_impact.get("source") or "东方财富/FTShare/数据库缓存", capital_impact.get("latest_data_date"), f"{len(fund_rows)}条核验记录"),
                ("sector_history", "板块历史", len(sector_history) >= 15, sector_history_payload.get("source") or "东方财富行业板块/FTShare", str(sector_history[-1].get("trade_date"))[:10] if sector_history else None, f"{len(sector_history)}条，基准{benchmark_name}({sector_history_payload.get('benchmark_code') or benchmark_code or '--'})"),
                ("sector_members", "板块成分", bool(members), "东方财富行业成分", decision_date.isoformat(), f"{len(members)}只"),
                ("sector_recommendations", "板块推荐", bool(sector_recommendations.get("available")), "板块成分多因子排序", sector_recommendations.get("data_date"), f"龙头{len(sector_recommendations.get('leader') or [])}只、优秀{len(sector_recommendations.get('excellent') or [])}只、潜力{len(sector_recommendations.get('potential') or [])}只"),
                ("behavior_reflexivity", "行为反身性", bool(behavior_reflexivity.get("available")), "Skill 10 PIT日线诊断", behavior_reflexivity.get("data_date"), f"{behavior_reflexivity.get('candidate_label') or '暂无明确候选'} · {behavior_reflexivity.get('diagnosis_level') or 'S0'}"),
                ("valuation_history", "三年估值", bool(valuation_history.get("history")), valuation_history.get("source") or "东方财富", valuation_history.get("history_end"), f"{valuation.get('pe_history_samples')}条正PE样本"),
                ("consensus", "一致预期", raw["consensus"].get("error") is None, "东方财富分析师一致预期/PIT约束", now.date().isoformat(), consensus_detail),
                ("announcements", "公司公告", bool((announcement_payload.get("status") or {}).get(code, {}).get("available")), "东方财富公告/FTShare", decision_date.isoformat(), f"{len(announcements)}条最新公告"),
                ("market_context", "市场决策", bool(market_context.get("available")), "AI市场决策工作台", str((market_context.get("meta") or {}).get("decision_date") or "") or None, "市场状态与主要矛盾"),
            ]
            source_audit = []
            for key, label, observed, source, source_date, detail in source_specs:
                error = (raw.get(key) or {}).get("error")
                source_audit.append(_source_item(
                    key, label,
                    status="observed" if observed else "source_retry_required",
                    source=str(source),
                    data_date=source_date,
                    detail=detail if observed else f"公开源本次响应异常：{error or '无有效记录'}",
                ))
            resolved = sum(item["status"] in {"observed", "cached_fallback"} for item in source_audit)
            data_audit = {
                "public_source_coverage_pct": _round(resolved / len(source_audit) * 100, 1),
                "resolved_sources": resolved,
                "required_sources": len(source_audit),
                "sources": source_audit,
                "legally_not_disclosed": ["核心客户名称：公司当前结构化公开披露未列示，系统不虚构"],
                "not_applicable_now": [
                    "09:25竞价确认：仅在下一交易日09:24-09:27真实观察窗口生效",
                    "14:55执行确认：仅在交易日14:50-14:59真实观察窗口生效",
                ],
                "missing_policy": "公开可得字段优先实时源，其次同日/最近交易日审计缓存；未披露事实不以估算冒充",
            }
            evidence = [
                {"nature": "fact", "category": "company", "statement": f"{stock_name}主营：{company.get('main_business') or '公司公开简介'}", "source": company["source"], "data_date": company.get("equity_data_date")},
                {"nature": "fact", "category": "earnings", "statement": f"盈利状态{financials.get('earnings_state')}，质量{financials.get('earnings_quality')}，持续性{financials.get('earnings_sustainability')}", "source": financials.get("source"), "data_date": financials.get("report_date")},
                {
                    "nature": "calculation",
                    "category": "valuation",
                    "statement": (
                        f"周期阶段{valuation.get('cycle_phase_label')}，标准化PE {valuation.get('normalized_pe')}，"
                        f"PE反向风险{'是' if valuation.get('pe_inversion_risk') else '否'}"
                        if valuation.get("is_cyclical")
                        else f"PE三年分位{valuation.get('pe_percentile_3y')}%，行业分位{valuation.get('industry_pe_percentile')}%"
                    ),
                    "source": valuation.get("source"),
                    "data_date": valuation.get("data_date"),
                },
                {"nature": "calculation", "category": "capital", "statement": f"5日资金状态：{capital_impact.get('persistence')}", "source": capital_impact.get("source"), "data_date": capital_impact.get("latest_data_date")},
                {"nature": "calculation", "category": "alpha", "statement": f"个股Alpha评分{relative.get('individual_alpha_score')}，板块依赖{sector_dependency.get('dependency_level')}", "source": "个股/板块/上证日收益OLS", "data_date": decision_date.isoformat()},
                {"nature": "calculation", "category": "behavior_reflexivity", "statement": f"{behavior_reflexivity.get('candidate_label') or '暂无明确候选'}；反身性{(behavior_reflexivity.get('reflexivity') or {}).get('reflexivity_label') or '未形成'}；评分{behavior_reflexivity.get('selection_score')}", "source": "Skill 10六维PIT日线诊断", "data_date": behavior_reflexivity.get("data_date")},
                {"nature": "scenario", "category": "risk", "statement": f"潜在上行{risk_reward.get('potential_upside_pct')}%，下行{risk_reward.get('potential_downside_pct')}%，RR={risk_reward.get('risk_reward_ratio')}", "source": "20日高低点+ATR情景", "data_date": decision_date.isoformat()},
            ]
            payload = {
                "available": bool(price and stock_rows and company_raw),
                "meta": {
                    "contract_version": CONTRACT_VERSION,
                    "requested_as_of": requested_date.isoformat(),
                    "decision_date": decision_date.isoformat(),
                    "data_date": decision_date.isoformat(),
                    "calculated_at": now.isoformat(),
                    "source_updated_at": quote.get("source_updated_at") or now.isoformat(),
                    "is_realtime": is_realtime,
                    "cache_used": False,
                    "source": "eastmoney+tencent+ftshare_optional+database_cache",
                    "scope": "交易时段实时决策" if is_realtime else "最近完整交易日复盘/下一交易日准备",
                },
                "company": company,
                "fundamentals": financials,
                "valuation": valuation,
                "capital_impact": capital_impact,
                "attribution": relative,
                "alpha": {"score": relative.get("individual_alpha_score"), "windows": [{"days": item["days"], "alpha_pct": item["individual_alpha_pct"]} for item in relative.get("windows", [])], "model": relative.get("formula")},
                "sector_role": sector_role,
                "sector_recommendations": sector_recommendations,
                "sector_dependency": sector_dependency,
                "behavior_reflexivity": behavior_reflexivity,
                "emotion": emotion,
                "catalysts": catalysts,
                "expectation_gap": expectation,
                "risk_reward": risk_reward,
                "strategy_fit": strategy_fit,
                "technical": {"indicators": indicators, "kline_points": len(stock_rows), "source": stock_history_payload.get("source")},
                "market_context": {
                    "state": (market_context.get("market_state") or {}).get("state_label"),
                    "score": (market_context.get("market_state") or {}).get("score"),
                    "principal_contradiction": ((market_context.get("market_cognition") or {}).get("principal_contradiction") or {}).get("statement"),
                    "dominant_aspect": ((market_context.get("market_cognition") or {}).get("dominant_aspect") or {}).get("statement"),
                    "stage": (market_context.get("market_cognition") or {}).get("stage"),
                    "final_action": (market_context.get("market_cognition") or {}).get("final_action") or "observe",
                    "source_date": (market_context.get("meta") or {}).get("decision_date"),
                },
                "state_matrix": state_matrix,
                "decision": decision,
                "data_audit": data_audit,
                "evidence": evidence,
            }

            verified_fallback = self._prefer_verified_snapshot(previous, payload, now)
            if verified_fallback:
                self._memory[cache_key] = (time.monotonic(), verified_fallback)
                return verified_fallback
            await self._persist(payload)
            self._memory[cache_key] = (time.monotonic(), payload)
            return payload


stock_essence_decision_service = StockEssenceDecisionService()
