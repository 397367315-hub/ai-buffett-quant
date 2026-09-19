"""HTTP contract for the independent Wildman decision module."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Response

from services.admin_auth import require_admin_for_mutation, verify_admin_token
from services.wildman_account_service import wildman_account_service
from services.wildman_classic_service import wildman_classic_service
from services.wildman_service import dashboard_summary, wildman_service


logger = logging.getLogger(__name__)


async def _personal_account(symbol: str, authorization: str | None) -> dict | None:
    scheme, _, token = str(authorization or "").partition(" ")
    username = verify_admin_token(token) if scheme.lower() == "bearer" else None
    if username is None:
        return None
    return await wildman_account_service.get_account(username, symbol)

router = APIRouter(
    prefix="/api/v1/wildman",
    tags=["野人哥交易决策"],
    dependencies=[Depends(require_admin_for_mutation)],
)


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="date必须使用YYYY-MM-DD格式") from exc


async def _call(factory, *args, **kwargs):
    try:
        return {"code": 0, "data": await factory(*args, **kwargs)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Wildman endpoint failed")
        raise HTTPException(status_code=503, detail="野人哥交易决策模块暂时不可用，请稍后重试") from exc


@router.get("/registry")
async def registry():
    return await _call(wildman_service.rule_registry)


@router.get("/dashboard")
async def dashboard(
    date_value: str | None = Query(None, alias="date"),
    refresh: bool = Query(False),
    exclude_star_market: bool = Query(True),
    exclude_gem: bool = Query(True),
):
    result = await _call(wildman_service.dashboard, _date(date_value), refresh=refresh, exclude_star_market=exclude_star_market, exclude_gem=exclude_gem)
    result["data"] = dashboard_summary(result["data"])
    return result


@router.get("/cycle")
async def cycle(date_value: str | None = Query(None, alias="date"), refresh: bool = Query(False)):
    payload = await wildman_service.dashboard(_date(date_value), refresh=refresh)
    return {"code": 0, "data": {"trade_date": payload["trade_date"], "cycle": payload["cycle"], "market_facts": payload["market_facts"], "rule_version": payload["rule_version"]}}


@router.get("/mainlines")
async def mainlines(date_value: str | None = Query(None, alias="date"), refresh: bool = Query(False)):
    payload = await wildman_service.dashboard(_date(date_value), refresh=refresh)
    return {"code": 0, "data": {"trade_date": payload["trade_date"], "rows": payload["mainlines"]}}


@router.get("/mainlines/{theme_id}")
async def mainline_detail(theme_id: str, date_value: str | None = Query(None, alias="date")):
    payload = await wildman_service.dashboard(_date(date_value))
    row = next((item for item in payload["mainlines"] if item["theme_id"] == theme_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到该题材的野人哥主线快照")
    return {"code": 0, "data": row}


@router.get("/candidates")
async def candidates(
    date_value: str | None = Query(None, alias="date"),
    refresh: bool = Query(False),
    status: str | None = Query(None),
    role: str | None = Query(None),
    exclude_star_market: bool = Query(True),
    exclude_gem: bool = Query(True),
):
    payload = await wildman_service.dashboard(_date(date_value), refresh=refresh, exclude_star_market=exclude_star_market, exclude_gem=exclude_gem)
    rows = payload["candidates"]
    if status:
        rows = [row for row in rows if row["candidate_status"] == status]
    if role:
        rows = [row for row in rows if row["role"]["role"] == role]
    return {"code": 0, "data": {"trade_date": payload["trade_date"], "rows": rows, "count": len(rows), "filters": payload["filters"]}}


@router.get("/candidates/{symbol}")
async def candidate_detail(symbol: str, response: Response, date_value: str | None = Query(None, alias="date"), refresh: bool = Query(False), authorization: str | None = Header(None)):
    response.headers["Cache-Control"] = "private, no-store"
    account = await _personal_account(symbol, authorization)
    return await _call(wildman_service.candidate, symbol, _date(date_value), refresh=refresh, account=account)


@router.get("/roles/{symbol}")
async def role_detail(symbol: str, date_value: str | None = Query(None, alias="date")):
    row = await wildman_service.candidate(symbol, _date(date_value))
    return {"code": 0, "data": {"symbol": row["symbol"], "trade_date": row["trade_date"], "theme_name": row["theme_name"], "role": row["role"]}}


@router.get("/setups")
async def setups(date_value: str | None = Query(None, alias="date")):
    payload = await wildman_service.dashboard(_date(date_value))
    return {"code": 0, "data": {"trade_date": payload["trade_date"], "rows": [{"symbol": row["symbol"], "name": row["name"], "theme_name": row["theme_name"], "setup": row["setup"], "status": row["candidate_status"]} for row in payload["candidates"] if row["setup"]["type"] != "NONE"]}}


@router.get("/risk/{symbol}")
async def risk(symbol: str, date_value: str | None = Query(None, alias="date")):
    row = await wildman_service.candidate(symbol, _date(date_value))
    return {"code": 0, "data": {"symbol": row["symbol"], "risk": row["risk"], "risk_reward": row["risk_reward"], "exit_plan": row["exit_plan"]}}


@router.get("/expectation/{symbol}")
async def expectation(symbol: str, date_value: str | None = Query(None, alias="date")):
    row = await wildman_service.candidate(symbol, _date(date_value))
    return {"code": 0, "data": {"symbol": row["symbol"], "expectation": row["expectation"]}}


@router.get("/intraday/{symbol}")
async def intraday(symbol: str, date_value: str | None = Query(None, alias="date"), refresh: bool = Query(False)):
    row = await wildman_service.candidate(symbol, _date(date_value), refresh=refresh)
    return {"code": 0, "data": {"symbol": row["symbol"], "trade_date": row["trade_date"], "support": row["support"], "level2": row["level2"]}}


@router.get("/review/{period}")
async def review(period: str, date_value: str | None = Query(None, alias="date")):
    if period not in {"daily", "weekly", "monthly"}:
        raise HTTPException(status_code=422, detail="period只能是daily、weekly或monthly")
    return await _call(wildman_service.review, period, _date(date_value))


@router.post("/manual-review")
async def manual_review(payload: dict[str, Any] = Body(default_factory=dict)):
    return await _call(wildman_service.save_review, payload)


@router.post("/plan")
async def plan(payload: dict[str, Any] = Body(default_factory=dict)):
    dashboard_payload = await wildman_service.dashboard(_date(payload.get("date")), refresh=bool(payload.get("refresh")))
    focus = [row for row in dashboard_payload["candidates"] if row["candidate_status"] in {"模式条件成立", "等待确认"}][:8]
    return {"code": 0, "data": {"trade_date": dashboard_payload["trade_date"], "cycle": dashboard_payload["cycle"], "mainlines": dashboard_payload["mainlines"][:5], "focus": focus, "scripts": {"above": "超预期：持有或等待模式内盘口确认", "match": "符合预期：观察锚点和承接", "below": "不及预期：竞价或开盘优先处理，绝不补仓"}}}


@router.get("/toolbox/{strategy_id}/scan")
async def classic_scan(
    strategy_id: str,
    date_value: str | None = Query(None, alias="date"),
    refresh: bool = Query(False),
    exclude_star_market: bool = Query(True),
    exclude_gem: bool = Query(True),
):
    return await _call(
        wildman_classic_service.scan,
        strategy_id,
        _date(date_value),
        refresh=refresh,
        exclude_star_market=exclude_star_market,
        exclude_gem=exclude_gem,
    )


@router.get("/toolbox/{strategy_id}/stocks/{symbol}")
async def classic_stock_detail(
    strategy_id: str,
    symbol: str,
    response: Response,
    date_value: str | None = Query(None, alias="date"),
    detail: bool = Query(True),
    refresh: bool = Query(False),
    authorization: str | None = Header(None),
):
    del detail
    response.headers["Cache-Control"] = "private, no-store"
    account = await _personal_account(symbol, authorization) if strategy_id.upper() == "WM_CLASSIC_T" else None
    return await _call(wildman_classic_service.detail, strategy_id, symbol, _date(date_value), refresh=refresh, account=account)
