"""Bounded point-in-time announcement and financial risk facts.

NumCat is the primary source.  The existing announcement collector is used
only when the NumCat announcement request fails; a successful empty response
is retained as an explicit coverage state. Empty, failed, and partial sources
remain visible in every result.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import desc, select

from database import async_session
from models import FinancialPITSnapshot
from services.macro_policy_news import macro_policy_news_collector
from wildman.risks import (
    ANNOUNCEMENT_LOOKBACK_DAYS,
    classify_announcement,
    financial_indicator_reasons,
    financial_indicator_warnings,
    normalize_code,
    parse_date,
    pit_financial_reasons,
    pit_financial_warnings,
    review_window,
)

try:
    from market_data.numcat.market_provider import numcat_market_provider
except ImportError:  # pragma: no cover - stripped-down test tooling
    numcat_market_provider = None


RISK_BATCH_SIZE = 32
RISK_CONCURRENCY = 2
FALLBACK_CONCURRENCY = 8
RISK_BATCH_TIMEOUT_SECONDS = 12
RISK_BATCH_TOTAL_TIMEOUT_SECONDS = 20
RISK_ADAPTIVE_MAX_REQUESTS = 16
FALLBACK_TIMEOUT_SECONDS = 8
MAX_RISK_SYMBOLS = 160
RISK_EVIDENCE_LIMIT = 24
RISK_CACHE_MAX_ENTRIES = 1024
RISK_CACHE_REALTIME_TTL = 15 * 60
RISK_CACHE_HISTORICAL_TTL = 60 * 60


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _source_status(status: str, source: str, checked_from: date, target: date, *, error: str | None = None, truncated: bool = False) -> dict[str, Any]:
    item = {
        "status": status,
        "source": source,
        "checked_from": checked_from.isoformat(),
        "checked_to": target.isoformat(),
    }
    if error:
        item["error"] = error
    if truncated:
        item["truncated"] = True
    return item


def _evidence_summary(evidence: list[dict[str, Any]], limit: int = RISK_EVIDENCE_LIMIT) -> tuple[list[dict[str, Any]], dict[str, int | bool]]:
    """Keep calculations full-size while returning a bounded audit summary."""
    risk_hits = [item for item in evidence if item.get("status") == "risk_hit"]
    warnings = [item for item in evidence if item.get("status") == "warning"]
    source_states = [item for item in evidence if not item.get("kind")]
    observed = [item for item in evidence if item.get("status") == "observed"]

    def recent(item: dict[str, Any]) -> str:
        return str(item.get("published_at") or item.get("checked_to") or "")

    risk_hits.sort(key=recent, reverse=True)
    warnings.sort(key=recent, reverse=True)
    observed.sort(key=recent, reverse=True)
    source_states = source_states[:4]
    risk_budget = max(0, limit - len(source_states))
    selected = risk_hits[:risk_budget]
    selected.extend(source_states)
    remaining = limit - len(selected)
    selected.extend(warnings[:remaining])
    remaining = limit - len(selected)
    selected.extend(observed[:remaining])
    return selected[:limit], {
        "evidence_total": len(evidence),
        "risk_hit_count": len(risk_hits),
        "warning_count": len(warnings),
        "evidence_returned": min(len(selected), limit),
        "evidence_truncated": len(evidence) > limit,
    }


class WildmanRiskService:
    """Fetch and classify bounded batches of PIT risk facts."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, date], tuple[float, dict[str, Any]]] = {}
        self._cache_lock = asyncio.Lock()

    @staticmethod
    def _cache_ttl(target: date) -> int:
        try:
            from services.data_collector import shanghai_now
            current = shanghai_now().date()
        except Exception:
            current = date.today()
        return RISK_CACHE_REALTIME_TTL if target >= current else RISK_CACHE_HISTORICAL_TTL

    @staticmethod
    def _bounded_unknown(code: str, target: date) -> dict[str, Any]:
        return {
            "fundamentals_clear": None,
            "major_risk": None,
            "financial_safe": None,
            "financial_screen": {},
            "risk_evidence": [{
                "kind": "coverage",
                "status": "bounded_not_checked",
                "source": "wildman_risk_service",
                "reason": f"单次风险核查上限{MAX_RISK_SYMBOLS}只，未发起该标的请求",
            }],
            "coverage": {
                "status": "bounded_not_checked",
                "checked_from": None,
                "checked_to": target.isoformat(),
                "sources": [],
                "attempted_sources": [],
                "numcat_errors": [],
                "cache_hit": False,
            },
        }

    @staticmethod
    async def _pit_financials(codes: list[str], target: date) -> dict[str, dict[str, Any]]:
        if not codes:
            return {}
        async with async_session() as session:
            statement = select(FinancialPITSnapshot).where(
                FinancialPITSnapshot.stock_code.in_(codes),
                FinancialPITSnapshot.disclosed_at <= target,
            ).order_by(
                FinancialPITSnapshot.stock_code,
                desc(FinancialPITSnapshot.report_date),
                desc(FinancialPITSnapshot.disclosed_at),
            )
            rows = list((await session.execute(statement)).scalars().all())
        output: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = normalize_code(row.stock_code)
            if code in output:
                continue
            output[code] = {
                "net_profit": row.net_profit,
                "operating_cf": row.operating_cf,
                "report_date": row.report_date.isoformat() if row.report_date else None,
                "disclosed_at": row.disclosed_at.isoformat() if row.disclosed_at else None,
                "source": row.source or "financial_pit_snapshots",
            }
        return output

    @staticmethod
    async def _fallback_one(code: str, target: date) -> tuple[str, list[dict[str, Any]], str | None]:
        try:
            rows = await asyncio.wait_for(
                macro_policy_news_collector._get_stock_announcements(code),
                timeout=FALLBACK_TIMEOUT_SECONDS,
            )
            return code, rows, None
        except Exception as exc:  # pragma: no cover - exercised by integration
            return code, [], type(exc).__name__

    async def _fallback_announcements(self, codes: list[str], target: date) -> dict[str, tuple[list[dict[str, Any]], str | None]]:
        semaphore = asyncio.Semaphore(FALLBACK_CONCURRENCY)

        async def fetch(code: str):
            async with semaphore:
                return await self._fallback_one(code, target)

        values = await asyncio.gather(*(fetch(code) for code in codes), return_exceptions=True)
        output: dict[str, tuple[list[dict[str, Any]], str | None]] = {}
        for code, value in zip(codes, values):
            if isinstance(value, Exception):
                output[code] = ([], type(value).__name__)
            else:
                output[value[0]] = (value[1], value[2])
        return output

    @staticmethod
    async def _numcat_batch(
        codes: list[str],
        target: date,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], dict[str, str], dict[str, bool]]:
        """Return rows and independent errors for the two NumCat endpoints."""
        if numcat_market_provider is None or not getattr(numcat_market_provider, "configured", False):
            return {}, {}, {"announcements": "NumCatNotConfigured", "finance_indicator": "NumCatNotConfigured"}, {}
        start_date = start_date or target.fromordinal(target.toordinal() - ANNOUNCEMENT_LOOKBACK_DAYS)
        end_date = end_date or target
        announcement_call = numcat_market_provider.announcements(
            codes, startdate=start_date, enddate=end_date, limit=2000,
        )
        finance_call = numcat_market_provider.finance_indicator(
            codes, as_of=target.isoformat(), limit=2000,
        )
        announcements_result, finance_result = await asyncio.gather(
            announcement_call, finance_call, return_exceptions=True,
        )
        errors: dict[str, str] = {}
        truncated: dict[str, bool] = {}
        if isinstance(announcements_result, Exception):
            errors["announcements"] = type(announcements_result).__name__
            announcements_by_code: dict[str, list[dict[str, Any]]] = {}
        else:
            if len(announcements_result or []) >= 2000:
                truncated["announcements"] = True
            announcements_by_code = defaultdict(list)
            for row in announcements_result or []:
                code = normalize_code(row.get("symbol") or row.get("code"))
                if code in codes:
                    announcements_by_code[code].append(row)
        if isinstance(finance_result, Exception):
            errors["finance_indicator"] = type(finance_result).__name__
            finance_by_code: dict[str, list[dict[str, Any]]] = {}
        else:
            if len(finance_result or []) >= 2000:
                truncated["finance_indicator"] = True
            finance_by_code = defaultdict(list)
            for row in finance_result or []:
                code = normalize_code(row.get("code") or row.get("symbol"))
                if code in codes:
                    finance_by_code[code].append(row)
        return dict(announcements_by_code), dict(finance_by_code), errors, truncated

    @staticmethod
    def _merge_numcat_parts(parts: list[tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], dict[str, str], dict[str, bool]]]):
        announcements: dict[str, list[dict[str, Any]]] = defaultdict(list)
        finance: dict[str, list[dict[str, Any]]] = defaultdict(list)
        errors: dict[str, str] = {}
        truncated: dict[str, bool] = {}

        def append_unique(target: dict[str, list[dict[str, Any]]], code: str, rows: list[dict[str, Any]]) -> None:
            seen = {
                (str(item.get("event_date") or item.get("announce_date") or ""), str(item.get("title") or item.get("report_date") or ""), str(item.get("content_url") or ""))
                for item in target[code]
            }
            for row in rows:
                key = (str(row.get("event_date") or row.get("announce_date") or ""), str(row.get("title") or row.get("report_date") or ""), str(row.get("content_url") or ""))
                if key not in seen:
                    target[code].append(row)
                    seen.add(key)

        for ann, fin, part_errors, part_truncated in parts:
            for code, rows in ann.items():
                append_unique(announcements, code, rows)
            for code, rows in fin.items():
                append_unique(finance, code, rows)
            errors.update(part_errors)
            truncated.update(part_truncated)
        return dict(announcements), dict(finance), errors, truncated

    async def _numcat_adaptive(
        self,
        codes: list[str],
        target: date,
        *,
        start_date: date,
        end_date: date,
        budget: list[int],
        deadline: float,
    ):
        remaining = deadline - asyncio.get_running_loop().time()
        if budget[0] <= 0 or remaining <= 0:
            return {}, {}, {"announcements": "AdaptiveDeadline"}, {}
        budget[0] -= 1
        try:
            part = await asyncio.wait_for(
                self._numcat_batch(codes, target, start_date=start_date, end_date=end_date),
                timeout=min(RISK_BATCH_TIMEOUT_SECONDS, remaining),
            )
        except Exception as exc:
            return {}, {}, {"announcements": type(exc).__name__, "finance_indicator": type(exc).__name__}, {}
        _, _, _, truncated = part
        if not truncated.get("announcements"):
            return part
        if len(codes) > 1:
            middle = len(codes) // 2
            left = await self._numcat_adaptive(
                codes[:middle], target, start_date=start_date, end_date=end_date,
                budget=budget, deadline=deadline,
            )
            right = await self._numcat_adaptive(
                codes[middle:], target, start_date=start_date, end_date=end_date,
                budget=budget, deadline=deadline,
            )
            return self._merge_numcat_parts([left, right])
        if start_date < end_date:
            middle = start_date + (end_date - start_date) // 2
            left = await self._numcat_adaptive(
                codes, target, start_date=start_date, end_date=middle,
                budget=budget, deadline=deadline,
            )
            right = await self._numcat_adaptive(
                codes, target, start_date=middle + timedelta(days=1), end_date=end_date,
                budget=budget, deadline=deadline,
            )
            return self._merge_numcat_parts([left, right])
        return part

    @staticmethod
    def _announcement_row(row: dict[str, Any], source: str, target: date, checked_from: date) -> tuple[dict[str, Any] | None, bool]:
        published = parse_date(row.get("event_date") or row.get("published_at") or row.get("notice_date"))
        if published is None:
            return None, True
        if published > target or published < checked_from:
            return None, False
        title = str(row.get("title") or row.get("title_ch") or row.get("announcement_title") or "").strip()
        summary = str(row.get("summary") or "").strip()
        category = str(row.get("announcement_type") or row.get("category") or row.get("column_type") or "").strip()
        level, reasons = classify_announcement(title, summary, category)
        return {
            "kind": "announcement",
            "status": "risk_hit" if level == "major" else "warning" if level == "warning" else "observed",
            "source": source,
            "published_at": published.isoformat(),
            "title": title,
            "url": row.get("content_url") or row.get("url"),
            "category": category,
            "reason": "、".join(reasons) if reasons else "公告已在PIT范围内核查，未命中当前负面关键词代理",
            "checked_from": checked_from.isoformat(),
            "checked_to": target.isoformat(),
        }, False

    async def _risk_facts_uncached(self, symbols: list[str], target: date) -> dict[str, dict[str, Any]]:
        codes = list(dict.fromkeys(normalize_code(value) for value in symbols if normalize_code(value)))
        if not codes:
            return {}
        checked_from, checked_to = review_window(target)
        pit_error: str | None = None
        try:
            pit = await self._pit_financials(codes, target)
        except Exception as exc:  # Keep source failure explicit and conclusions unknown.
            pit = {}
            pit_error = type(exc).__name__
        batches = [codes[index:index + RISK_BATCH_SIZE] for index in range(0, len(codes), RISK_BATCH_SIZE)]
        semaphore = asyncio.Semaphore(RISK_CONCURRENCY)

        async def fetch(batch: list[str]):
            async with semaphore:
                return await self._numcat_adaptive(
                    batch,
                    target,
                    start_date=checked_from,
                    end_date=target,
                    budget=[RISK_ADAPTIVE_MAX_REQUESTS],
                    deadline=asyncio.get_running_loop().time() + RISK_BATCH_TOTAL_TIMEOUT_SECONDS,
                )

        batch_results = await asyncio.gather(*(fetch(batch) for batch in batches), return_exceptions=True)
        numcat_announcements: dict[str, list[dict[str, Any]]] = defaultdict(list)
        numcat_finance: dict[str, list[dict[str, Any]]] = defaultdict(list)
        errors_by_batch: dict[str, str] = {}
        announcement_errors: dict[str, str] = {}
        finance_errors: dict[str, str] = {}
        announcement_truncated: set[str] = set()
        finance_truncated: set[str] = set()
        for batch, value in zip(batches, batch_results):
            if isinstance(value, Exception):
                error = type(value).__name__
                for code in batch:
                    errors_by_batch[code] = error
                    announcement_errors[code] = error
                    finance_errors[code] = error
                continue
            announcements, finance, errors, truncated = value
            for code, rows in announcements.items():
                numcat_announcements[code].extend(rows)
            for code, rows in finance.items():
                numcat_finance[code].extend(rows)
            if errors.get("announcements"):
                for code in batch:
                    announcement_errors[code] = errors["announcements"]
            if errors.get("finance_indicator"):
                for code in batch:
                    finance_errors[code] = errors["finance_indicator"]
            if errors:
                for code in batch:
                    errors_by_batch[code] = ";".join(f"{key}:{value}" for key, value in errors.items())
            if truncated.get("announcements"):
                announcement_truncated.update(batch)
            if truncated.get("finance_indicator"):
                finance_truncated.update(batch)

        fallback_codes = [
            code for code in codes
            if announcement_errors.get(code)
        ]
        if fallback_codes:
            try:
                fallback = await self._fallback_announcements(fallback_codes, target)
            except Exception as exc:  # pragma: no cover - defensive integration guard
                fallback = {code: ([], type(exc).__name__) for code in fallback_codes}
        else:
            fallback = {}
        output: dict[str, dict[str, Any]] = {}
        for code in codes:
            evidence: list[dict[str, Any]] = []
            sources: list[str] = []
            numcat_rows = numcat_announcements.get(code, [])
            numcat_ann_status = "failed" if announcement_errors.get(code) and not numcat_rows else "success_empty" if not numcat_rows else "available"
            evidence.append(_source_status(
                "truncated" if code in announcement_truncated else numcat_ann_status,
                "numcat_finance_announcement",
                checked_from,
                target,
                error=announcement_errors.get(code),
                truncated=code in announcement_truncated,
            ))
            if numcat_rows:
                sources.append("numcat_finance_announcement")
            valid_announcements = 0
            announcement_risk = False
            for row in numcat_rows:
                item, invalid_date = self._announcement_row(row, "numcat_finance_announcement", target, checked_from)
                if invalid_date:
                    continue
                if item:
                    evidence.append(item)
                    valid_announcements += 1
                    announcement_risk |= item["status"] == "risk_hit"

            fallback_rows, fallback_error = fallback.get(code, ([], None))
            fallback_used = code in fallback
            if fallback_used:
                fallback_status = "failed" if fallback_error else "success_empty" if not fallback_rows else "available"
                evidence.append(_source_status(fallback_status, "existing_announcement_fallback", checked_from, target, error=fallback_error))
                if fallback_rows:
                    sources.append("existing_announcement_fallback")
                for row in fallback_rows:
                    item, invalid_date = self._announcement_row(row, "existing_announcement_fallback", target, checked_from)
                    if invalid_date:
                        continue
                    if item:
                        evidence.append(item)
                        valid_announcements += 1
                        announcement_risk |= item["status"] == "risk_hit"

            finance_rows = numcat_finance.get(code, [])
            latest_finance = None
            finance_invalid = False
            for row in finance_rows:
                announced = parse_date(row.get("announce_date"))
                report_period = parse_date(row.get("report_date"))
                if announced is None:
                    finance_invalid = True
                    continue
                if report_period is None:
                    finance_invalid = True
                    continue
                current_period = parse_date(latest_finance.get("report_date")) if latest_finance else None
                current_announced = parse_date(latest_finance.get("announce_date")) if latest_finance else None
                if announced <= target and (
                    latest_finance is None
                    or report_period > current_period
                    or (report_period == current_period and announced > current_announced)
                ):
                    latest_finance = row
            finance_in_window = bool(
                latest_finance
                and checked_from <= parse_date(latest_finance.get("announce_date")) <= target
            )
            finance_status = "failed" if finance_errors.get(code) and not finance_rows else "limited_history" if finance_invalid or (latest_finance and not finance_in_window) else "success_empty" if not latest_finance else "available"
            evidence.append(_source_status(
                "truncated" if code in finance_truncated else finance_status,
                "numcat_finance_indicator",
                checked_from,
                target,
                error=finance_errors.get(code),
                truncated=code in finance_truncated,
            ))
            financial_values = dict(latest_finance or {})
            financial_risk_reasons = financial_indicator_reasons(financial_values) if finance_in_window else []
            financial_warnings = financial_indicator_warnings(financial_values) if finance_in_window else []
            if latest_finance:
                evidence.append({
                    "kind": "financial_indicator",
                    "status": "risk_hit" if financial_risk_reasons else "warning" if financial_warnings else "observed",
                    "source": "numcat_finance_indicator",
                    "published_at": parse_date(latest_finance.get("announce_date")).isoformat(),
                    "url": None,
                    "reason": "、".join([*financial_risk_reasons, *financial_warnings]) if (financial_risk_reasons or financial_warnings) else "已取得PIT财务指标；正指标不等于全面基本面安全",
                    "values": {key: latest_finance.get(key) for key in ("eps", "roe", "debt_to_assets", "report_date", "announce_date")},
                    "checked_from": checked_from.isoformat(),
                    "checked_to": target.isoformat(),
                })

            pit_row = pit.get(code)
            pit_reasons = pit_financial_reasons(pit_row or {})
            pit_warnings = pit_financial_warnings(pit_row or {})
            pit_in_window = bool(
                pit_row
                and pit_row.get("disclosed_at")
                and checked_from <= parse_date(pit_row.get("disclosed_at")) <= target
            )
            current_pit_reasons = pit_reasons if pit_in_window else []
            evidence.append(_source_status(
                "failed" if pit_error else "available" if pit_in_window else "limited_history" if pit_row else "success_empty",
                "financial_pit_snapshots",
                checked_from,
                target,
                error=pit_error,
            ))
            if pit_row:
                evidence.append({
                    "kind": "financial_pit",
                    "status": "risk_hit" if pit_reasons else "warning" if pit_warnings else "observed",
                    "source": pit_row.get("source") or "financial_pit_snapshots",
                    "published_at": pit_row.get("disclosed_at"),
                    "url": None,
                    "reason": "、".join([*pit_reasons, *pit_warnings]) if (pit_reasons or pit_warnings) else "已取得披露日PIT财务字段；非负不代表全面基本面安全",
                    "values": {key: pit_row.get(key) for key in ("net_profit", "operating_cf", "report_date", "disclosed_at")},
                    "checked_from": checked_from.isoformat(),
                    "checked_to": target.isoformat(),
                })

            complete_pit_financials = bool(
                pit_row
                and pit_in_window
                and pit_row.get("net_profit") is not None
                and pit_row.get("operating_cf") is not None
            )
            window_clear = bool(valid_announcements and not announcement_risk and code not in announcement_truncated)
            proxy_clear = bool(
                complete_pit_financials
                and not current_pit_reasons
                and not (pit_warnings if pit_in_window else [])
                and not financial_risk_reasons
                and code not in finance_truncated
            )
            fundamentals_clear = True if window_clear and proxy_clear else False if current_pit_reasons else None
            all_risk_reasons = [*current_pit_reasons, *financial_risk_reasons]
            major_risk = True if (announcement_risk or all_risk_reasons) else None
            if major_risk is None and window_clear and code not in finance_truncated and (latest_finance or pit_row):
                major_risk = False
            source_failed = bool(errors_by_batch.get(code) or pit_error or fallback_error)
            source_available = bool(valid_announcements or latest_finance or pit_row)
            source_limited = code in announcement_truncated or code in finance_truncated
            announcement_empty = not numcat_rows and not fallback_rows and not announcement_errors.get(code)
            evidence_for_output, evidence_counts = _evidence_summary(evidence)
            coverage_status = "failed" if source_failed and not source_available else (
                "limited_history" if source_limited or source_failed or finance_status == "limited_history" or (valid_announcements == 0 and (numcat_rows or fallback_rows)) else (
                    "success_empty" if announcement_empty or (not valid_announcements and not latest_finance) else "covered"
                )
            )
            output[code] = {
                "fundamentals_clear": fundamentals_clear,
                "major_risk": major_risk,
                "financial_safe": False if current_pit_reasons else None,
                "financial_screen": {
                    "net_profit": pit_row.get("net_profit") if pit_row else None,
                    "operating_cf": pit_row.get("operating_cf") if pit_row else None,
                    "eps": financial_values.get("eps"),
                    "roe": financial_values.get("roe"),
                    "debt_to_assets": financial_values.get("debt_to_assets"),
                    "proxy_rules": "核查通过仅表示最新已披露利润与经营现金流非负、窗口内公告可核查且未命中硬风险，不认证完整基本面安全。已披露亏损触发风险；经营现金流为负和高负债率受行业影响，仅作进一步核查提醒。",
                },
                "risk_evidence": evidence_for_output,
                "risk_evidence_summary": evidence_counts,
                "coverage": {
                    "status": coverage_status,
                    "checked_from": checked_from.isoformat(),
                    "checked_to": checked_to.isoformat(),
                    "sources": list(dict.fromkeys(sources + (["numcat_finance_indicator"] if latest_finance else []) + (["financial_pit_snapshots"] if pit_row else []))),
                    "attempted_sources": [
                        "numcat_finance_announcement",
                        "numcat_finance_indicator",
                        "financial_pit_snapshots",
                        *(["existing_announcement_fallback"] if fallback_used else []),
                    ],
                    "numcat_errors": [errors_by_batch[code]] if code in errors_by_batch else [],
                    "truncated": [
                        source for source, matched in (
                            ("numcat_finance_announcement", code in announcement_truncated),
                            ("numcat_finance_indicator", code in finance_truncated),
                        ) if matched
                    ],
                    "announcement_records": valid_announcements,
                    "announcements_checked": valid_announcements,
                    "risk_hits": evidence_counts["risk_hit_count"],
                    "financial_indicator": bool(latest_finance),
                    **evidence_counts,
                },
            }
        return output

    async def risk_facts(self, symbols: list[str], target: date, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
        """Return per-symbol PIT facts with bounded per-symbol TTL caching.

        Historical targets use a 60-minute TTL; the current target uses 15
        minutes. ``refresh=True`` bypasses cached entries and replaces them.
        The 160-symbol bound prevents a dashboard call from turning into an
        unbounded fallback crawl.
        """
        codes = list(dict.fromkeys(normalize_code(value) for value in symbols if normalize_code(value)))
        if not codes:
            return {}
        bounded = codes[:MAX_RISK_SYMBOLS]
        overflow = codes[MAX_RISK_SYMBOLS:]
        now = time.monotonic()
        ttl = self._cache_ttl(target)
        result: dict[str, dict[str, Any]] = {}
        uncached: list[str] = []
        if not refresh:
            async with self._cache_lock:
                for code in bounded:
                    item = self._cache.get((code, target))
                    if item and now - item[0] < ttl:
                        cached = dict(item[1])
                        cached["coverage"] = {
                            **(cached.get("coverage") or {}),
                            "cache_hit": True,
                            "cached_at": cached.get("coverage", {}).get("cached_at"),
                        }
                        result[code] = cached
                    else:
                        uncached.append(code)
        else:
            uncached = bounded
        if uncached:
            fresh = await self._risk_facts_uncached(uncached, target)
            for code in uncached:
                if code not in fresh:
                    fresh[code] = self._bounded_unknown(code, target)
            cached_at = time.time()
            stored_at = time.monotonic()
            async with self._cache_lock:
                for code, fact in fresh.items():
                    fact["coverage"] = {**(fact.get("coverage") or {}), "cache_hit": False, "cached_at": cached_at}
                    self._cache[(code, target)] = (stored_at, fact)
                    result[code] = fact
                if len(self._cache) > RISK_CACHE_MAX_ENTRIES:
                    oldest = sorted(self._cache.items(), key=lambda item: item[1][0])
                    for key, _ in oldest[:len(self._cache) - RISK_CACHE_MAX_ENTRIES]:
                        self._cache.pop(key, None)
        for code in overflow:
            result[code] = self._bounded_unknown(code, target)
        return {code: result[code] for code in codes if code in result}


wildman_risk_service = WildmanRiskService()


async def risk_facts(symbols: list[str], target: date, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
    """Return bounded PIT facts: ``risk_facts(symbols, target, refresh=False)``."""
    return await wildman_risk_service.risk_facts(symbols, target, refresh=refresh)


__all__ = ["WildmanRiskService", "risk_facts", "wildman_risk_service"]
