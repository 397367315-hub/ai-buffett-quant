"""Financial-report appointments and published-report comparisons for personal-pool stocks."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from database import async_session
from models import PersonalPoolItem, PersonalSystemConfig
from services.data_collector import collector, shanghai_now


DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _number(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_text(value: object) -> str | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _codes_filter(codes: list[str]) -> str:
    quoted = ",".join(f'"{code}"' for code in codes)
    return f"(SECURITY_CODE in ({quoted}))"


def _pool_priority(item: PersonalPoolItem) -> int:
    if item.status in {"holding", "reduce"}:
        return 0
    return {"core": 1, "watchlist": 2, "leaders": 3, "etf": 4, "blacklist": 5}.get(item.pool_key, 9)


class ReportCalendarService:
    async def _personal_universe(self) -> tuple[list[str], dict[str, dict]]:
        async with async_session() as session:
            rows = (await session.execute(select(PersonalPoolItem))).scalars().all()
        by_code: dict[str, list[PersonalPoolItem]] = defaultdict(list)
        for row in rows:
            if row.asset_type == "stock":
                by_code[row.code].append(row)
        relation = {}
        for code, items in by_code.items():
            item = sorted(items, key=_pool_priority)[0]
            relation[code] = {
                "name": item.name,
                "pool": item.pool_key,
                "holding": item.status in {"holding", "reduce"},
                "relation": "持仓股" if item.status in {"holding", "reduce"} else {
                    "core": "核心池", "watchlist": "观察池", "leaders": "龙头池",
                }.get(item.pool_key, "个人池"),
            }
        return sorted(relation), relation

    @staticmethod
    async def _fetch_appointments(codes: list[str], today: date) -> list[dict]:
        if not codes:
            return []
        end = today + timedelta(days=14)
        payload = await collector.fetch_json(DATACENTER_URL, {
            "reportName": "RPT_PUBLIC_BS_APPOIN",
            "columns": "ALL",
            "filter": (
                _codes_filter(codes)
                + f"(APPOINT_PUBLISH_DATE>='{today.isoformat()}')"
                + f"(APPOINT_PUBLISH_DATE<='{end.isoformat()}')"
            ),
            "pageNumber": "1",
            "pageSize": "500",
            "sortTypes": "1",
            "sortColumns": "APPOINT_PUBLISH_DATE",
            "source": "WEB",
            "client": "WEB",
        })
        return list(((payload.get("result") or {}).get("data") or []))

    @staticmethod
    async def _fetch_published(codes: list[str], today: date) -> list[dict]:
        if not codes:
            return []
        earliest = today - timedelta(days=400)
        payload = await collector.fetch_json(DATACENTER_URL, {
            "reportName": "RPT_LICO_FN_CPD",
            "columns": "ALL",
            "filter": _codes_filter(codes) + f"(NOTICE_DATE>='{earliest.isoformat()}')",
            "pageNumber": "1",
            "pageSize": "500",
            "sortTypes": "-1",
            "sortColumns": "NOTICE_DATE",
            "source": "WEB",
            "client": "WEB",
        })
        return list(((payload.get("result") or {}).get("data") or []))

    @staticmethod
    def _map_published(rows: list[dict], relation: dict[str, dict]) -> list[dict]:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            code = str(row.get("SECURITY_CODE") or "")
            if code in relation:
                grouped[code].append(row)
        result = []
        for code, items in grouped.items():
            items.sort(key=lambda item: str(item.get("NOTICE_DATE") or ""), reverse=True)
            latest = items[0]
            report_kind = str(latest.get("DATEMMDD") or "")
            prior = next(
                (item for item in items[1:] if str(item.get("DATEMMDD") or "") == report_kind),
                items[1] if len(items) > 1 else None,
            )
            revenue_growth = _number(latest.get("YSTZ"))
            profit_growth = _number(latest.get("SJLTZ"))
            previous_revenue_growth = _number(prior.get("YSTZ")) if prior else None
            previous_profit_growth = _number(prior.get("SJLTZ")) if prior else None
            anomalies = []
            if revenue_growth is not None and revenue_growth < 0:
                anomalies.append(f"营收同比 {revenue_growth:.1f}%")
            if profit_growth is not None and profit_growth < 0:
                anomalies.append(f"归母净利润同比 {profit_growth:.1f}%")
            cashflow_per_share = _number(latest.get("MGJYXJJE"))
            if cashflow_per_share is not None and cashflow_per_share < 0:
                anomalies.append("每股经营现金流为负")
            revenue_acceleration = (
                revenue_growth - previous_revenue_growth
                if revenue_growth is not None and previous_revenue_growth is not None else None
            )
            profit_acceleration = (
                profit_growth - previous_profit_growth
                if profit_growth is not None and previous_profit_growth is not None else None
            )
            result.append({
                "code": code,
                "name": str(latest.get("SECURITY_NAME_ABBR") or relation[code]["name"]),
                **relation[code],
                "report_type": str(latest.get("DATATYPE") or "财务报告"),
                "notice_date": _date_text(latest.get("NOTICE_DATE")),
                "report_date": _date_text(latest.get("REPORTDATE")),
                "metrics": {
                    "revenue": _number(latest.get("TOTAL_OPERATE_INCOME")),
                    "net_profit": _number(latest.get("PARENT_NETPROFIT")),
                    "revenue_growth_pct": revenue_growth,
                    "profit_growth_pct": profit_growth,
                    "gross_margin_pct": _number(latest.get("XSMLL")),
                    "roe_pct": _number(latest.get("WEIGHTAVG_ROE")),
                    "cashflow_per_share": cashflow_per_share,
                    "debt_ratio_pct": None,
                },
                "comparison": {
                    "previous_report_type": str(prior.get("DATATYPE") or "") if prior else None,
                    "previous_revenue_growth_pct": previous_revenue_growth,
                    "previous_profit_growth_pct": previous_profit_growth,
                    "revenue_acceleration_pct": revenue_acceleration,
                    "profit_acceleration_pct": profit_acceleration,
                },
                "anomalies": anomalies,
                "source": "东方财富财务数据中心",
            })
        return sorted(result, key=lambda item: item["notice_date"] or "", reverse=True)

    async def dashboard(self) -> dict[str, Any]:
        codes, relation = await self._personal_universe()
        today = shanghai_now().date()
        appointments_result, published_result = await asyncio.gather(
            self._fetch_appointments(codes, today),
            self._fetch_published(codes, today),
            return_exceptions=True,
        )
        appointments_failed = isinstance(appointments_result, Exception)
        published_failed = isinstance(published_result, Exception)
        cached: dict[str, Any] = {}
        appointments = [] if appointments_failed else appointments_result
        published_rows = [] if published_failed else published_result
        upcoming = []
        for item in appointments:
            code = str(item.get("SECURITY_CODE") or "")
            if code not in relation:
                continue
            publish_date = _date_text(item.get("APPOINT_PUBLISH_DATE"))
            upcoming.append({
                "code": code,
                "name": str(item.get("SECURITY_NAME_ABBR") or relation[code]["name"]),
                **relation[code],
                "report_type": str(item.get("REPORT_TYPE_NAME") or "财务报告"),
                "publish_date": publish_date,
                "actual_publish_date": _date_text(item.get("ACTUAL_PUBLISH_DATE")),
                "days_until": (date.fromisoformat(publish_date) - today).days if publish_date else None,
                "changed": bool(item.get("APPOINT_CHANGE")),
                "source": "东方财富预约披露时间表",
            })
        upcoming.sort(key=lambda item: item["publish_date"] or "")
        published = self._map_published(published_rows, relation)
        cache_used = False
        if appointments_failed or published_failed:
            async with async_session() as session:
                cached_row = await session.get(PersonalSystemConfig, "report_snapshot")
            cached = cached_row.payload if cached_row and isinstance(cached_row.payload, dict) else {}
            if appointments_failed and cached.get("upcoming"):
                upcoming = [
                    item for item in cached["upcoming"]
                    if not item.get("publish_date") or item["publish_date"] >= today.isoformat()
                ]
                cache_used = True
            if published_failed and cached.get("published"):
                published = list(cached["published"])
                cache_used = True
        updated_at = shanghai_now().isoformat()
        return {
            "updated_at": updated_at,
            "snapshot_updated_at": (
                cached.get("snapshot_updated_at") or cached.get("updated_at")
                if cache_used else updated_at
            ),
            "universe_count": len(codes),
            "upcoming": upcoming,
            "published": published,
            "source_status": {
                "appointments": "cache" if appointments_failed and upcoming else "available" if not appointments_failed else "unavailable",
                "financials": "cache" if published_failed and published else "available" if not published_failed else "unavailable",
            },
            "cache_used": cache_used,
            "automation": {
                "extract_metrics": True,
                "compare_previous": True,
                "flag_anomalies": True,
                "update_selection_features": True,
                "push_configured": False,
                "message": "财报数据会进入研究与选股特征；微信/飞书推送尚未配置凭据。",
            },
            "disclaimer": "预约披露日期可能变更；以交易所最终公告为准。缺失财务字段保持为空。",
        }

    async def refresh_snapshot(self) -> dict[str, Any]:
        dashboard = await self.dashboard()
        snapshot = {
            "updated_at": dashboard["updated_at"],
            "snapshot_updated_at": dashboard["snapshot_updated_at"],
            "upcoming": dashboard["upcoming"],
            "published": dashboard["published"],
            "source_status": dashboard["source_status"],
        }
        async with async_session() as session:
            row = await session.get(PersonalSystemConfig, "report_snapshot")
            if row is None:
                session.add(PersonalSystemConfig(key="report_snapshot", payload=snapshot))
            else:
                row.payload = snapshot
                row.updated_at = datetime.utcnow()
            await session.commit()
        return dashboard


report_calendar_service = ReportCalendarService()
