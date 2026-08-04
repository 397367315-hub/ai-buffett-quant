"""API for the single-user personal investment workspace."""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from services.admin_auth import require_admin
from services.personal_portfolio import personal_portfolio_service


router = APIRouter(
    prefix="/api/v1/personal",
    tags=["personal"],
    dependencies=[Depends(require_admin)],
)


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/overview")
async def get_personal_overview():
    return {"code": 0, "data": await personal_portfolio_service.overview()}


@router.get("/health")
async def get_personal_health():
    overview = await personal_portfolio_service.overview()
    return {"code": 0, "data": overview["health"]}


@router.get("/logs")
async def get_personal_logs(limit: int = Query(30, ge=1, le=100)):
    return {"code": 0, "data": {"logs": await personal_portfolio_service.list_logs(limit)}}


@router.post("/items", status_code=status.HTTP_201_CREATED)
async def create_personal_item(request: dict):
    try:
        result = await personal_portfolio_service.create_item(request or {})
    except (ValueError, TypeError) as exc:
        raise _bad_request(exc) from exc
    # Idempotent additions from analysis pages are successful even when the
    # item already exists; the response tells the caller whether it was new.
    return {"code": 0, "data": result, "message": "已加入个人股票池" if result["created"] else "该标的已在个人股票池"}


@router.put("/items/{item_id}")
async def update_personal_item(item_id: int, request: dict):
    try:
        item = await personal_portfolio_service.update_item(item_id, request or {})
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise _bad_request(exc) from exc
    return {"code": 0, "data": {"item": item}, "message": "个人池条目已更新"}


@router.delete("/items/{item_id}")
async def delete_personal_item(item_id: int):
    try:
        await personal_portfolio_service.delete_item(item_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"code": 0, "data": {"deleted": True}, "message": "已移出个人股票池"}


@router.post("/items/{item_id}/move-to-watchlist")
async def move_personal_item_to_watchlist(item_id: int):
    try:
        item = await personal_portfolio_service.move_to_watchlist(item_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise _bad_request(exc) from exc
    return {"code": 0, "data": {"item": item}, "message": "已移入长期观察池"}


@router.post("/logs", status_code=status.HTTP_201_CREATED)
async def create_personal_log(request: dict):
    try:
        log = await personal_portfolio_service.create_log(request or {})
    except (ValueError, TypeError) as exc:
        raise _bad_request(exc) from exc
    return {"code": 0, "data": {"log": log}, "message": "投资日志已记录"}
