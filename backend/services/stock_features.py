"""Source-backed stock features shared by quant scans and selection agents.

The quote snapshot is fast enough for the whole A-share market, while
financial statements, shareholder counts and lock-up schedules live in
separate EastMoney reports.  This service joins those reports by stock code,
keeps their disclosure dates, and never substitutes zero for missing data.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from database import async_session
from models import MarketDataCache
from services.data_collector import collector, normalize_stock_code, shanghai_now


FINANCIAL_FIELDS = frozenset({
    "gross_margin",
    "revenue_growth",
    "deducted_profit_growth",
    "ocf_to_profit",
    "revenue_ttm",
    "net_profit_ttm",
    "deducted_profit_ttm",
    "operating_cf_ttm",
    "ocf_to_profit_ttm",
    "debt_ratio",
    "receivable_to_revenue",
    "net_profit",
    "is_profitable_non_st",
})
SHAREHOLDER_FIELDS = frozenset({"holder_change_pct", "holder_decline_streak"})
LOCKUP_FIELDS = frozenset({"lockup_days", "lockup_ratio_pct"})
MARKET_FIELDS = frozenset({"market_breadth", "sector_rank", "sector_strength_score"})

ADVANCED_RULE_FIELDS: dict[str, frozenset[str]] = {
    "gross_margin": frozenset({"gross_margin"}),
    "revenue_growth": frozenset({"revenue_growth"}),
    "deducted_profit_growth": frozenset({"deducted_profit_growth"}),
    "ocf_to_profit": frozenset({"ocf_to_profit"}),
    "debt_ratio": frozenset({"debt_ratio"}),
    "receivable_to_revenue": frozenset({"receivable_to_revenue"}),
    "is_profitable": frozenset({"net_profit", "is_profitable_non_st"}),
    "holder_concentration": frozenset({"holder_change_pct"}),
    "holder_decline_streak": frozenset({"holder_decline_streak"}),
    "no_lockup_expiry": frozenset({"lockup_days"}),
    "market_breadth": frozenset({"market_breadth"}),
    "sector_strength": frozenset({"sector_rank"}),
}


def required_feature_fields(strategies: Iterable[dict]) -> set[str]:
    fields: set[str] = set()
    for strategy in strategies:
        rules = [
            *((strategy.get("filter") or {}).get("rules") or []),
            *((strategy.get("entry") or {}).get("rules") or []),
            *((strategy.get("exit") or {}).get("rules") or []),
        ]
        for rule in rules:
            fields.update(ADVANCED_RULE_FIELDS.get(str(rule.get("type") or ""), ()))
    return fields


def _number(value: Any) -> float | None:
    if value in (None, "", "-") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _recent_quarter_ends(as_of: date, count: int = 4) -> list[date]:
    candidates = [
        date(year, month, day)
        for year in range(as_of.year - 2, as_of.year + 1)
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31))
        if date(year, month, day) <= as_of
    ]
    return sorted(candidates, reverse=True)[:count]


def _a_share_code(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw.isdigit() or len(raw) != 6:
        return None
    try:
        return normalize_stock_code(raw)
    except ValueError:
        return None


TTM_VALUE_FIELDS = ("revenue", "net_profit", "deducted_profit", "operating_cf")


def _ttm_value(
    rows_by_period: dict[date, dict],
    current_period: date,
    field: str,
) -> float | None:
    """Convert cumulative quarterly disclosures into a point-in-time TTM value."""
    current = _number((rows_by_period.get(current_period) or {}).get(field))
    if current is None:
        return None
    if current_period.month == 12:
        return current

    try:
        prior_year_total_period = date(current_period.year - 1, 12, 31)
        prior_year_same_period = date(current_period.year - 1, current_period.month, current_period.day)
    except ValueError:
        return None
    prior_year_total = _number((rows_by_period.get(prior_year_total_period) or {}).get(field))
    prior_year_same = _number((rows_by_period.get(prior_year_same_period) or {}).get(field))
    if prior_year_total is None or prior_year_same is None:
        return None
    return current + prior_year_total - prior_year_same


class StockFeatureService:
    """Fetch, cache and merge slow-moving feature datasets."""

    _CACHE_KEY = "stock_feature_snapshot_v2"
    _CACHE_SECONDS = 4 * 60 * 60
    _PAGE_SIZE = 500
    _PAGE_CONCURRENCY = 8
    _LOCKUP_COVERAGE_DAYS = 365

    def __init__(self) -> None:
        self._memory_cache: dict[str, dict] = {}
        self._cache_lock = asyncio.Lock()
        self._sector_cache: tuple[float, list[dict]] | None = None

    @classmethod
    def _cache_key(cls, as_of: date) -> str:
        return f"{cls._CACHE_KEY}:{as_of.isoformat()}"

    @staticmethod
    def _cache_fresh(payload: dict, as_of: date) -> bool:
        if payload.get("as_of_date") != as_of.isoformat():
            return False
        try:
            fetched_at = datetime.fromisoformat(str(payload.get("fetched_at") or ""))
        except ValueError:
            return False
        now = shanghai_now()
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=now.tzinfo)
        return 0 <= (now - fetched_at).total_seconds() <= StockFeatureService._CACHE_SECONDS

    async def _read_cache(self, as_of: date) -> dict:
        cache_key = self._cache_key(as_of)
        cached = self._memory_cache.get(cache_key)
        if cached and self._cache_fresh(cached, as_of):
            return dict(cached)
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, cache_key)
                # Read the old singleton key only for backward compatibility;
                # a snapshot for another research date is never a valid fallback.
                if row is None:
                    row = await session.get(MarketDataCache, self._CACHE_KEY)
            payload = row.payload if row and isinstance(row.payload, dict) else {}
            if payload and payload.get("as_of_date") == as_of.isoformat():
                self._memory_cache[cache_key] = payload
                return dict(payload)
        except Exception as exc:
            print(f"Stock feature cache load failed: {type(exc).__name__}")
        return {}

    async def _write_cache(self, payload: dict) -> None:
        as_of = _date(payload.get("as_of_date"))
        if as_of is None:
            return
        cache_key = self._cache_key(as_of)
        self._memory_cache[cache_key] = payload
        try:
            async with async_session() as session:
                row = await session.get(MarketDataCache, cache_key)
                if row:
                    row.payload = payload
                    row.updated_at = datetime.utcnow()
                else:
                    session.add(MarketDataCache(key=cache_key, payload=payload))
                # Keep the legacy key as a latest-snapshot compatibility view.
                legacy = await session.get(MarketDataCache, self._CACHE_KEY)
                if legacy:
                    legacy.payload = payload
                    legacy.updated_at = datetime.utcnow()
                else:
                    session.add(MarketDataCache(key=self._CACHE_KEY, payload=payload))
                await session.commit()
        except Exception as exc:
            print(f"Stock feature cache save failed: {type(exc).__name__}")

    async def _fetch_report(
        self,
        *,
        report_name: str,
        columns: str,
        filter_value: str,
        sort_columns: str,
        sort_types: str,
    ) -> list[dict]:
        base = {
            "reportName": report_name,
            "columns": columns,
            "filter": filter_value,
            "pageNumber": "1",
            "pageSize": str(self._PAGE_SIZE),
            "sortTypes": sort_types,
            "sortColumns": sort_columns,
            "source": "WEB",
            "client": "WEB",
        }
        first = await collector.fetch_json(collector.DATACENTER_URL, base)
        result = first.get("result") or {}
        first_rows = result.get("data") or []
        pages = int(result.get("pages") or 0)
        if not first.get("success"):
            message = str(first.get("message") or f"{report_name} returned no result")
            raise RuntimeError(message)
        if pages < 1:
            return []
        rows = [item for item in first_rows if isinstance(item, dict)]
        if pages == 1:
            return rows

        async def fetch_page(page: int) -> list[dict]:
            payload = await collector.fetch_json(
                collector.DATACENTER_URL,
                {**base, "pageNumber": str(page)},
            )
            page_result = payload.get("result") or {}
            page_rows = page_result.get("data") or []
            if not payload.get("success") or not page_rows:
                raise RuntimeError(f"{report_name} page {page} is incomplete")
            return [item for item in page_rows if isinstance(item, dict)]

        for start in range(2, pages + 1, self._PAGE_CONCURRENCY):
            numbers = range(start, min(start + self._PAGE_CONCURRENCY, pages + 1))
            for page_rows in await asyncio.gather(*(fetch_page(page) for page in numbers)):
                rows.extend(page_rows)
        return rows

    async def _financial_snapshot(self, codes: set[str], as_of: date) -> dict[str, dict]:
        history: dict[str, dict[date, dict]] = defaultdict(dict)
        columns = (
            "SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_DATE,NOTICE_DATE,"
            "TOTALOPERATEREVE,TOTALOPERATEREVETZ,KCFJCXSYJLR,KCFJCXSYJLRTZ,"
            "ROEJQ,XSMLL,ZCFZL,YSZKYYSR,PARENTNETPROFIT,NETCASH_OPERATE_PK,NCO_NETPROFIT"
        )
        for report_date in _recent_quarter_ends(as_of, count=6):
            rows = await self._fetch_report(
                report_name="RPT_F10_FINANCE_MAINFINADATA",
                columns=columns,
                filter_value=(
                    f"(REPORT_DATE='{report_date.isoformat()}')"
                    f"(NOTICE_DATE<='{as_of.isoformat()}')"
                ),
                sort_columns="NOTICE_DATE",
                sort_types="-1",
            )
            for row in rows:
                code = _a_share_code(row.get("SECURITY_CODE"))
                if not code:
                    continue
                report_day = _date(row.get("REPORT_DATE"))
                disclosed_at = _date(row.get("NOTICE_DATE"))
                if not report_day or not disclosed_at or disclosed_at > as_of:
                    continue
                net_profit = _number(row.get("PARENTNETPROFIT"))
                operating_cf = _number(row.get("NETCASH_OPERATE_PK"))
                ocf_to_profit = _number(row.get("NCO_NETPROFIT"))
                if ocf_to_profit is None and net_profit not in (None, 0) and operating_cf is not None:
                    ocf_to_profit = operating_cf / net_profit
                record = {
                    "roe": _number(row.get("ROEJQ")),
                    "gross_margin": _number(row.get("XSMLL")),
                    "revenue_growth": _number(row.get("TOTALOPERATEREVETZ")),
                    "deducted_profit_growth": _number(row.get("KCFJCXSYJLRTZ")),
                    "ocf_to_profit": ocf_to_profit,
                    "debt_ratio": _number(row.get("ZCFZL")),
                    "receivable_to_revenue": _number(row.get("YSZKYYSR")),
                    "revenue": _number(row.get("TOTALOPERATEREVE")),
                    "deducted_profit": _number(row.get("KCFJCXSYJLR")),
                    "net_profit": net_profit,
                    "operating_cf": operating_cf,
                    "report_date": report_day,
                    "disclosed_at": disclosed_at,
                }
                previous = history[code].get(report_day)
                if previous is None or disclosed_at >= previous["disclosed_at"]:
                    history[code][report_day] = record
            if len(history) >= 5000:
                break

        output: dict[str, dict] = {}
        for code, rows_by_period in history.items():
            if not rows_by_period:
                continue
            current_period = max(rows_by_period)
            latest = rows_by_period[current_period]
            ttm_values = {
                f"{field}_ttm": _ttm_value(rows_by_period, current_period, field)
                for field in TTM_VALUE_FIELDS
            }
            available_ttm = [
                field for field in TTM_VALUE_FIELDS
                if ttm_values.get(f"{field}_ttm") is not None
            ]
            ttm_profit = ttm_values.get("net_profit_ttm")
            ttm_cashflow = ttm_values.get("operating_cf_ttm")
            ttm_ratio = (
                ttm_cashflow / ttm_profit
                if ttm_cashflow is not None and ttm_profit not in (None, 0)
                else None
            )
            output[code] = {
                **{key: value for key, value in latest.items() if key not in {"report_date", "disclosed_at"}},
                **ttm_values,
                "ocf_to_profit_ttm": ttm_ratio,
                "ttm_available": bool(available_ttm),
                "ttm_available_fields": available_ttm,
                "financial_period_count": len(rows_by_period),
                "financial_report_date": current_period.isoformat(),
                "financial_disclosed_at": latest["disclosed_at"].isoformat(),
                "ttm_formula": "current_period + prior_year_full_year - prior_year_same_period",
            }
        return output

    async def _shareholder_snapshot(self, codes: set[str], as_of: date) -> dict[str, dict]:
        history: dict[str, list[dict]] = defaultdict(list)
        columns = (
            "SECURITY_CODE,SECURITY_NAME_ABBR,HOLDER_NUM,PRE_HOLDER_NUM,"
            "HOLDER_NUM_RATIO,END_DATE,HOLD_NOTICE_DATE"
        )
        for period in _recent_quarter_ends(as_of, count=3):
            rows = await self._fetch_report(
                report_name="RPT_HOLDERNUM_DET",
                columns=columns,
                filter_value=(
                    f"(END_DATE='{period.isoformat()}')"
                    f"(HOLD_NOTICE_DATE<='{as_of.isoformat()}')"
                ),
                sort_columns="HOLD_NOTICE_DATE",
                sort_types="-1",
            )
            for row in rows:
                code = _a_share_code(row.get("SECURITY_CODE"))
                if not code:
                    continue
                period_day = _date(row.get("END_DATE"))
                disclosed_at = _date(row.get("HOLD_NOTICE_DATE"))
                if not period_day or not disclosed_at or disclosed_at > as_of:
                    continue
                history[code].append({
                    "holder_count": _number(row.get("HOLDER_NUM")),
                    "previous_holder_count": _number(row.get("PRE_HOLDER_NUM")),
                    "holder_change_pct": _number(row.get("HOLDER_NUM_RATIO")),
                    "holder_period": period_day.isoformat(),
                    "holder_disclosed_at": disclosed_at.isoformat(),
                })
        output: dict[str, dict] = {}
        for code, rows in history.items():
            rows.sort(key=lambda item: item["holder_period"], reverse=True)
            latest = rows[0]
            streak = 0
            for row in rows:
                change = row.get("holder_change_pct")
                if change is None or change >= 0:
                    break
                streak += 1
            output[code] = {**latest, "holder_decline_streak": streak}
        return output

    async def _lockup_snapshot(self, codes: set[str], as_of: date) -> dict[str, dict]:
        coverage_end = as_of + timedelta(days=self._LOCKUP_COVERAGE_DAYS)
        rows = await self._fetch_report(
            report_name="RPT_LIFT_STAGE",
            columns=(
                "SECURITY_CODE,SECURITY_NAME_ABBR,FREE_DATE,CURRENT_FREE_SHARES,"
                "ABLE_FREE_SHARES,LIFT_MARKET_CAP,FREE_RATIO,TOTAL_RATIO,FREE_SHARES_TYPE"
            ),
            filter_value=(
                f"(FREE_DATE>='{as_of.isoformat()}')"
                f"(FREE_DATE<='{coverage_end.isoformat()}')"
            ),
            sort_columns="FREE_DATE,CURRENT_FREE_SHARES",
            sort_types="1,-1",
        )
        output: dict[str, dict] = {}
        for row in rows:
            code = _a_share_code(row.get("SECURITY_CODE"))
            free_date = _date(row.get("FREE_DATE"))
            if not code or not free_date:
                continue
            days = (free_date - as_of).days
            if days < 0 or (code in output and output[code]["lockup_days"] < days):
                continue
            ratio = _number(row.get("TOTAL_RATIO"))
            ratio_pct = ratio * 100 if ratio is not None and abs(ratio) <= 1 else ratio
            output[code] = {
                "lockup_days": days,
                "lockup_date": free_date.isoformat(),
                "lockup_ratio_pct": ratio_pct,
                "lockup_market_cap": _number(row.get("LIFT_MARKET_CAP")),
                "lockup_share_type": str(row.get("FREE_SHARES_TYPE") or ""),
                "lockup_coverage_end": coverage_end.isoformat(),
            }
        return output

    async def _datasets(self, codes: set[str], fields: set[str], as_of: date) -> tuple[dict, list[str]]:
        needs = {
            "financial": bool(fields & FINANCIAL_FIELDS),
            "shareholders": bool(fields & SHAREHOLDER_FIELDS),
            "lockups": bool(fields & LOCKUP_FIELDS),
        }
        if not any(needs.values()):
            return {}, []
        async with self._cache_lock:
            cached = await self._read_cache(as_of)
            same_observation_date = cached.get("as_of_date") == as_of.isoformat()
            if not same_observation_date:
                # A snapshot for another research date may contain disclosures
                # that were not yet public at ``as_of``. Never use it as a
                # fallback for point-in-time research.
                cached = {}
            fresh = self._cache_fresh(cached, as_of)
            warnings: list[str] = []
            payload = {
                "version": 2,
                "as_of_date": as_of.isoformat(),
                "fetched_at": cached.get("fetched_at") or shanghai_now().isoformat(),
                "financial": cached.get("financial") or {},
                "shareholders": cached.get("shareholders") or {},
                "lockups": cached.get("lockups") or {},
                "dataset_status": cached.get("dataset_status") or {},
            }
            refreshed = False
            for dataset, needed in needs.items():
                if not needed or (
                    fresh and (payload.get("dataset_status") or {}).get(dataset) == "available"
                ):
                    continue
                try:
                    if dataset == "financial":
                        values = await self._financial_snapshot(codes, as_of)
                    elif dataset == "shareholders":
                        values = await self._shareholder_snapshot(codes, as_of)
                    else:
                        values = await self._lockup_snapshot(codes, as_of)
                    payload[dataset] = values
                    payload["dataset_status"][dataset] = "available"
                    refreshed = True
                except Exception as exc:
                    label = {"financial": "财务报告", "shareholders": "股东户数", "lockups": "限售解禁"}[dataset]
                    if payload.get(dataset):
                        warnings.append(
                            f"{label}数据源暂不可用，使用同一研究日缓存"
                            f"（{payload.get('fetched_at') or '时间未知'}；{type(exc).__name__}）"
                        )
                    else:
                        warnings.append(f"{label}数据源暂不可用，相关规则按数据不足处理（{type(exc).__name__}）")
                        payload["dataset_status"][dataset] = "unavailable"
            if refreshed:
                payload["fetched_at"] = shanghai_now().isoformat()
                await self._write_cache(payload)
            return payload, warnings

    @staticmethod
    def _derived_market_context(stocks: list[dict]) -> tuple[dict, dict[str, dict]]:
        valid = [
            stock for stock in stocks
            if _number(stock.get("change_pct")) is not None and str(stock.get("sector") or "").strip()
        ]
        changes = [_number(stock.get("change_pct")) for stock in stocks]
        changes = [value for value in changes if value is not None]
        up = sum(value > 0 for value in changes)
        down = sum(value < 0 for value in changes)
        flat = len(changes) - up - down
        breadth = up / down if down else None

        groups: dict[str, list[dict]] = defaultdict(list)
        for stock in valid:
            groups[str(stock.get("sector") or "").strip()].append(stock)
        ranked: list[tuple[str, dict]] = []
        for sector, members in groups.items():
            sector_changes = [_number(item.get("change_pct")) for item in members]
            sector_changes = [value for value in sector_changes if value is not None]
            if not sector_changes:
                continue
            inflows = [_number(item.get("main_inflow")) for item in members]
            inflows = [value for value in inflows if value is not None]
            member_up = sum(value > 0 for value in sector_changes)
            strength = (
                sum(sector_changes) / len(sector_changes) * 10
                + member_up / len(sector_changes) * 30
                + max(-10.0, min(10.0, (sum(inflows) if inflows else 0) / 100_000))
            )
            ranked.append((sector, {
                "sector_strength_score": round(strength, 3),
                "sector_member_count": len(members),
                "sector_up_ratio": round(member_up / len(sector_changes), 4),
                "sector_avg_change_pct": round(sum(sector_changes) / len(sector_changes), 4),
            }))
        ranked.sort(key=lambda item: item[1]["sector_strength_score"], reverse=True)
        by_sector = {
            sector: {**metrics, "sector_rank": rank}
            for rank, (sector, metrics) in enumerate(ranked, start=1)
        }
        return {
            "market_breadth": breadth,
            "market_up_count": up,
            "market_down_count": down,
            "market_flat_count": flat,
            "market_observation_count": len(changes),
        }, by_sector

    async def _live_sector_ranks(self) -> dict[str, dict]:
        now = time.monotonic()
        if self._sector_cache and now - self._sector_cache[0] <= 300:
            rows = self._sector_cache[1]
        else:
            try:
                rows = await collector.fetch_all_industry_flow()
            except Exception:
                rows = []
            if rows:
                self._sector_cache = (now, rows)
        def sort_value(row: dict) -> tuple[float, float]:
            change = _number(row.get("change_pct"))
            inflow = _number(row.get("main_net_inflow"))
            return (
                change if change is not None else -math.inf,
                inflow if inflow is not None else -math.inf,
            )

        ranked = sorted(
            (row for row in rows if str(row.get("name") or "").strip()),
            key=sort_value,
            reverse=True,
        )
        return {
            str(row.get("name") or "").strip(): {
                "sector_rank": rank,
                "sector_strength_score": _number(row.get("change_pct")),
                "sector_avg_change_pct": _number(row.get("change_pct")),
                "sector_main_inflow": _number(row.get("main_net_inflow")),
            }
            for rank, row in enumerate(ranked, start=1)
        }

    async def enrich(
        self,
        stocks: list[dict],
        fields: set[str] | None = None,
        *,
        full_market: bool = False,
        as_of: date | None = None,
    ) -> dict:
        """Return copied stock contexts plus source and coverage metadata."""
        requested = set(fields or (FINANCIAL_FIELDS | SHAREHOLDER_FIELDS | LOCKUP_FIELDS | MARKET_FIELDS))
        observation_date = as_of or shanghai_now().date()
        contexts = [dict(stock) for stock in stocks]
        codes = {str(stock.get("code") or "") for stock in contexts if str(stock.get("code") or "")}
        datasets, warnings = await self._datasets(codes, requested, observation_date)

        market_context: dict = {}
        sector_context: dict[str, dict] = {}
        if requested & MARKET_FIELDS:
            if full_market:
                market_context, sector_context = self._derived_market_context(contexts)
            else:
                sector_context = await self._live_sector_ranks()
                if "market_breadth" in requested:
                    warnings.append("候选池不是全市场快照，大盘涨跌比未据此推算")

        coverage = {
            "financial": 0,
            "shareholders": 0,
            "lockups": 0,
            "sector_strength": 0,
            "market_breadth": bool(market_context.get("market_breadth") is not None),
            "total": len(contexts),
        }
        for stock in contexts:
            code = str(stock.get("code") or "")
            meta = dict(stock.get("_feature_meta") or {})
            financial = (datasets.get("financial") or {}).get(code)
            if financial:
                stock.update(financial)
                stock["is_profitable_non_st"] = bool(
                    _number(financial.get("net_profit")) is not None
                    and float(financial["net_profit"]) > 0
                    and "ST" not in str(stock.get("name") or "").upper()
                    and "退" not in str(stock.get("name") or "")
                )
                meta["financial"] = {
                    "status": "available",
                    "source": "东方财富 RPT_F10_FINANCE_MAINFINADATA",
                    "report_date": financial.get("financial_report_date"),
                    "disclosed_at": financial.get("financial_disclosed_at"),
                }
                coverage["financial"] += 1
            elif requested & FINANCIAL_FIELDS:
                meta["financial"] = {"status": "unavailable", "source": "数据源未覆盖"}

            shareholders = (datasets.get("shareholders") or {}).get(code)
            if shareholders:
                stock.update(shareholders)
                meta["shareholders"] = {
                    "status": "available",
                    "source": "东方财富 RPT_HOLDERNUM_DET",
                    "report_date": shareholders.get("holder_period"),
                    "disclosed_at": shareholders.get("holder_disclosed_at"),
                }
                coverage["shareholders"] += 1
            elif requested & SHAREHOLDER_FIELDS:
                meta["shareholders"] = {"status": "unavailable", "source": "数据源未覆盖"}

            lockup = (datasets.get("lockups") or {}).get(code)
            if (
                not lockup
                and requested & LOCKUP_FIELDS
                and (datasets.get("dataset_status") or {}).get("lockups") == "available"
            ):
                lockup = {
                    "lockup_days": self._LOCKUP_COVERAGE_DAYS + 1,
                    "lockup_date": None,
                    "lockup_ratio_pct": None,
                    "lockup_market_cap": None,
                    "lockup_share_type": "",
                    "lockup_coverage_end": (
                        observation_date + timedelta(days=self._LOCKUP_COVERAGE_DAYS)
                    ).isoformat(),
                }
            if lockup:
                stock.update(lockup)
                meta["lockups"] = {
                    "status": "available",
                    "source": "东方财富 RPT_LIFT_STAGE",
                    "as_of": observation_date.isoformat(),
                    "coverage_end": lockup.get("lockup_coverage_end"),
                    "next_event_date": lockup.get("lockup_date"),
                }
                coverage["lockups"] += 1
            elif requested & LOCKUP_FIELDS:
                meta["lockups"] = {"status": "unavailable", "source": "数据源未覆盖"}

            if market_context:
                stock.update(market_context)
            sector = str(stock.get("sector") or "").strip()
            sector_values = sector_context.get(sector)
            if sector_values:
                stock.update(sector_values)
                coverage["sector_strength"] += 1
            if requested & MARKET_FIELDS:
                meta["market"] = {
                    "status": "available" if market_context or sector_values else "unavailable",
                    "source": "完整行情快照横截面推导" if full_market else "东方财富行业资金榜",
                    "as_of": observation_date.isoformat(),
                    "observation_count": market_context.get("market_observation_count"),
                }
            stock["_feature_meta"] = meta

        return {
            "stocks": contexts,
            "coverage": coverage,
            "warnings": warnings,
            "as_of_date": observation_date.isoformat(),
            "source_updated_at": datasets.get("fetched_at"),
        }


stock_feature_service = StockFeatureService()
