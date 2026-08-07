"""API for the single-user personal investment workspace."""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from services.admin_auth import require_admin
from services.ai_robot import ai_robot_service
from services.macro_dashboard import macro_dashboard_service
from services.personal_analytics import personal_analytics_service
from services.personal_portfolio import personal_portfolio_service
from services.report_calendar import report_calendar_service
from services.research_notes import research_notes_service


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


@router.get("/robot")
async def get_robot_dashboard():
    return {"code": 0, "data": await ai_robot_service.dashboard()}


@router.post("/robot/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_robot_run(request: dict):
    pool_type = str((request or {}).get("pool_type") or "all").strip().lower()
    try:
        if pool_type == "all":
            runs = [
                await ai_robot_service.refresh("short", trigger="manual", background=True),
                await ai_robot_service.refresh("long", trigger="manual", background=True),
            ]
        else:
            runs = [await ai_robot_service.refresh(pool_type, trigger="manual", background=True)]
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return {"code": 0, "data": {"runs": runs}, "message": "机器人刷新任务已提交"}


@router.get("/robot/runs/{run_id}")
async def get_robot_run(run_id: int):
    try:
        run = await ai_robot_service.get_run(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"code": 0, "data": {"run": run}}


@router.get("/robot/history")
async def get_robot_history(
    pool_type: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    try:
        runs = await ai_robot_service.history(pool_type, limit)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return {"code": 0, "data": {"runs": runs}}


@router.get("/robot/journals")
async def get_robot_journals(
    pool_type: str | None = Query(None),
    limit: int = Query(30, ge=1, le=180),
):
    try:
        journals = await ai_robot_service.journal_history(pool_type, limit)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return {"code": 0, "data": {"journals": journals}}


@router.get("/allocation")
async def get_personal_allocation():
    return {"code": 0, "data": await personal_analytics_service.allocation()}


@router.put("/allocation/config")
async def update_personal_allocation_config(request: dict):
    try:
        config = await personal_analytics_service.update_account_config(request or {})
    except (ValueError, TypeError) as exc:
        raise _bad_request(exc) from exc
    return {"code": 0, "data": {"config": config}, "message": "仓位规则已更新"}


@router.get("/attribution")
async def get_personal_attribution(period: str = Query("year")):
    return {"code": 0, "data": await personal_analytics_service.attribution(period)}


@router.get("/macro")
async def get_personal_macro_dashboard():
    return {"code": 0, "data": await macro_dashboard_service.dashboard()}


@router.get("/reports")
async def get_personal_report_calendar():
    return {"code": 0, "data": await report_calendar_service.dashboard()}


@router.post("/reports/refresh")
async def refresh_personal_report_calendar():
    return {
        "code": 0,
        "data": await report_calendar_service.refresh_snapshot(),
        "message": "财报日历已刷新",
    }


@router.get("/notes")
async def get_personal_notes():
    return {"code": 0, "data": {"notes": await research_notes_service.list_notes()}}


@router.get("/notes/{note_id}")
async def get_personal_note(note_id: int):
    try:
        note = await research_notes_service.get_note(note_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"code": 0, "data": {"note": note}}


@router.post("/notes", status_code=status.HTTP_201_CREATED)
async def create_personal_note(request: dict):
    try:
        note = await research_notes_service.upsert_note(request or {})
    except (ValueError, TypeError) as exc:
        raise _bad_request(exc) from exc
    return {"code": 0, "data": {"note": note}, "message": "研究笔记已保存"}


@router.put("/notes/{note_id}")
async def update_personal_note(note_id: int, request: dict):
    try:
        note = await research_notes_service.upsert_note(request or {}, note_id=note_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise _bad_request(exc) from exc
    return {"code": 0, "data": {"note": note}, "message": "研究笔记已更新"}


@router.delete("/notes/{note_id}")
async def delete_personal_note(note_id: int):
    try:
        await research_notes_service.delete_note(note_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"code": 0, "data": {"deleted": True}, "message": "研究笔记已删除"}


@router.get("/errors/warnings")
async def get_personal_error_warnings(code: str | None = Query(None)):
    try:
        warnings = await research_notes_service.warnings(code)
    except ValueError as exc:
        raise _bad_request(exc) from exc
    return {"code": 0, "data": {"warnings": warnings}}


@router.get("/errors")
async def get_personal_errors():
    return {"code": 0, "data": await research_notes_service.list_errors()}


@router.post("/errors", status_code=status.HTTP_201_CREATED)
async def create_personal_error(request: dict):
    try:
        error = await research_notes_service.create_error(request or {})
    except (ValueError, TypeError) as exc:
        raise _bad_request(exc) from exc
    return {"code": 0, "data": {"error": error}, "message": "错误模式已记录"}


@router.delete("/errors/{error_id}")
async def delete_personal_error(error_id: int):
    try:
        await research_notes_service.delete_error(error_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"code": 0, "data": {"deleted": True}, "message": "错误记录已删除"}
