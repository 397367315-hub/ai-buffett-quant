"""HTTP surface for the user-configurable quantitative strategy workspace."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from quant.backtest import quant_backtest_service
from quant.engine import create_strategy, delete_strategy, get_strategy, list_strategies, update_strategy
from quant.portfolio import paper_portfolio
from quant.rules import public_rule_catalog
from quant.schemas import (
    BacktestRequest,
    CompareRequest,
    PaperBuyRequest,
    PaperResetRequest,
    PaperSellRequest,
    PreviewRequest,
    ScanRequest,
    StrategyCreate,
    StrategyUpdate,
)
from quant.signals import quant_signal_service
from quant.storage import quant_store
from quant.templates import list_templates
from services.data_collector import collector


router = APIRouter(prefix="/api/v1/quant", tags=["量化策略"])


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.get("/rules")
async def get_rules():
    """Rule metadata is backend-owned so the builder cannot emit unsupported rules."""
    return {"code": 0, "data": {"rules": public_rule_catalog()}}


@router.get("/templates")
async def get_templates():
    return {"code": 0, "data": list_templates()}


@router.get("/sectors")
async def get_sectors(limit: int = Query(300, ge=20, le=1000)):
    snapshot = quant_store.read("market_snapshot")
    if not snapshot.get("stocks"):
        try:
            snapshot = await collector.fetch_quant_market_snapshot()
            quant_store.write("market_snapshot", {"version": 1, **snapshot})
        except Exception:
            snapshot = {"stocks": []}
    names = sorted({
        str(item.get("sector") or "").strip()
        for item in snapshot.get("stocks") or []
        if str(item.get("sector") or "").strip()
    })
    return {
        "code": 0,
        "data": {
            "sectors": [{"code": name, "name": name, "type": "industry"} for name in names[:limit]],
            "source": snapshot.get("source", "cache"),
            "data_date": snapshot.get("data_date"),
        },
    }


@router.post("/strategy", status_code=status.HTTP_201_CREATED)
async def create_strategy_endpoint(payload: StrategyCreate):
    try:
        return {"code": 0, "data": create_strategy(payload)}
    except ValueError as exc:
        raise _unprocessable(exc) from exc


@router.get("/strategies")
async def list_strategies_endpoint():
    return {"code": 0, "data": list_strategies()}


@router.get("/strategy/{strategy_id}")
async def get_strategy_endpoint(strategy_id: str):
    strategy = get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="策略不存在")
    return {"code": 0, "data": strategy}


@router.put("/strategy/{strategy_id}")
async def update_strategy_endpoint(strategy_id: str, payload: StrategyUpdate):
    try:
        strategy = update_strategy(strategy_id, payload)
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    if strategy is None:
        raise HTTPException(status_code=404, detail="策略不存在")
    return {"code": 0, "data": strategy}


@router.delete("/strategy/{strategy_id}")
async def delete_strategy_endpoint(strategy_id: str):
    if not delete_strategy(strategy_id):
        raise HTTPException(status_code=404, detail="策略不存在")
    return {"code": 0, "data": {"deleted": True}}


@router.post("/preview")
async def preview_strategy(payload: PreviewRequest):
    strategy = payload.strategy.model_dump(mode="json")
    strategy.update({"id": "preview", "created_at": "", "updated_at": ""})
    try:
        result = await quant_signal_service.scan(
            force=False, persist=False, strategies_override=[strategy],
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    signals = result.get("signals", [])
    return {
        "code": 0,
        "data": {
            "count": len(signals), "signals": signals[:payload.limit],
            "scanned_stocks": result.get("scanned_stocks"), "warning": result.get("warning"),
            "technical_history_coverage": result.get("technical_history_coverage"),
        },
    }


@router.post("/scan", status_code=status.HTTP_202_ACCEPTED)
async def start_scan(payload: ScanRequest):
    try:
        job = await quant_signal_service.start_scan(payload.strategy_id, payload.force)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return {"code": 0, "data": job}


@router.get("/scan/status/{job_id}")
async def get_scan_status(job_id: str):
    job = quant_signal_service.get_status(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="扫描任务不存在")
    return {"code": 0, "data": job}


@router.get("/signals")
async def get_signals(strategy_id: str | None = None):
    return {"code": 0, "data": quant_signal_service.get_signals(strategy_id)}


@router.get("/signals/history")
async def get_signal_history(limit: int = Query(20, ge=1, le=100)):
    return {"code": 0, "data": quant_signal_service.get_history(limit)}


@router.post("/backtest/{strategy_id}", status_code=status.HTTP_202_ACCEPTED)
async def start_backtest(strategy_id: str, payload: BacktestRequest):
    try:
        job = await quant_backtest_service.start_backtest(strategy_id, payload.model_dump(mode="json"))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"code": 0, "data": job}


@router.get("/backtest/{job_id}/status")
async def get_backtest_status(job_id: str):
    job = quant_backtest_service.get_status(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="回测任务不存在")
    if job.get("status") == "completed":
        job["result"] = quant_store.read_backtest_result(job_id)
    return {"code": 0, "data": job}


@router.get("/performance/{strategy_id}")
async def get_strategy_performance(strategy_id: str):
    if get_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail="策略不存在")
    return {"code": 0, "data": quant_backtest_service.get_results(strategy_id)}


@router.post("/compare")
async def compare_strategies(payload: CompareRequest):
    return {"code": 0, "data": quant_backtest_service.compare(payload.strategy_ids)}


@router.get("/paper/portfolio")
async def get_paper_portfolio(refresh: bool = False):
    portfolio = paper_portfolio.get_portfolio()
    if not refresh or not portfolio.get("holdings"):
        return {"code": 0, "data": portfolio}
    codes = [item["stock_code"] for item in portfolio["holdings"]]
    warning = None
    try:
        snapshot = await collector.fetch_stock_quotes(codes)
        if not snapshot.get("complete"):
            warning = "部分持仓最新行情不可用，未更新的股票保留上次价格"
    except Exception:
        snapshot = quant_store.read("market_snapshot")
        warning = "最新持仓行情不可用，已使用最近一次全市场缓存价格"
    return {"code": 0, "data": paper_portfolio.refresh_prices(snapshot, warning=warning)}


@router.get("/paper/history")
async def get_paper_history(limit: int = Query(100, ge=1, le=500)):
    portfolio = paper_portfolio.get_portfolio()
    return {"code": 0, "data": list(reversed(portfolio.get("history", [])[-limit:]))}


@router.post("/paper/buy")
async def paper_buy(payload: PaperBuyRequest):
    try:
        return {"code": 0, "data": paper_portfolio.buy(payload), "message": "模拟买入成功"}
    except ValueError as exc:
        raise _unprocessable(exc) from exc


@router.post("/paper/sell")
async def paper_sell(payload: PaperSellRequest):
    try:
        return {"code": 0, "data": paper_portfolio.sell(payload), "message": "模拟卖出成功"}
    except ValueError as exc:
        raise _unprocessable(exc) from exc


@router.post("/paper/reset")
async def paper_reset(payload: PaperResetRequest):
    return {"code": 0, "data": paper_portfolio.reset(payload), "message": "模拟盘已重置"}
