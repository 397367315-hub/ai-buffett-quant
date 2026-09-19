"""Private account contract used by Wildman detail views and its panel.

The parent application deliberately includes this router separately from the
public Wildman dashboard/scan router.  No account fields are ever read by a
scan endpoint.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response

from services.admin_auth import require_admin
from services.wildman_account_service import wildman_account_service


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/wildman",
    tags=["野人哥个人账户"],
)


@router.get("/account")
async def get_wildman_account(
    response: Response,
    username: str = Depends(require_admin),
    symbol: str | None = Query(None, min_length=1, max_length=32),
):
    """Return only the authenticated user's account, optionally scoped to a symbol."""
    response.headers["Cache-Control"] = "private, no-store"
    try:
        account = await wildman_account_service.get_account(username, symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Wildman account read failed")
        raise HTTPException(status_code=503, detail="个人账户暂时不可用，请稍后重试") from exc
    return {"code": 0, "data": account}


@router.put("/account")
async def put_wildman_account(
    response: Response,
    payload: dict[str, Any] = Body(default_factory=dict),
    username: str = Depends(require_admin),
):
    """Replace the bounded manual snapshot owned by the authenticated user."""
    response.headers["Cache-Control"] = "private, no-store"
    try:
        account = await wildman_account_service.update_account(username, payload or {})
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Wildman account write failed")
        raise HTTPException(status_code=503, detail="个人账户暂时不可用，请稍后重试") from exc
    return {"code": 0, "data": account, "message": "个人账户快照已保存，请手工与券商核对"}


__all__ = ["router"]
