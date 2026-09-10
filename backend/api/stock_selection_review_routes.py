from fastapi import APIRouter, HTTPException

from services.stock_selection_review import stock_selection_review_service


router = APIRouter(prefix="/api/v1")


@router.get("/stock-selection/runs/{run_id}/review")
async def review_stock_selection_run(run_id: int):
    """Review a saved selection against its prior compatible snapshot."""
    data = await stock_selection_review_service.review(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="未找到该次智能选股记录")
    return {"code": 0, "data": data}
