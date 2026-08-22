"""HTTP surface for the user-configurable quantitative strategy workspace."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, status

from quant.backtest import quant_backtest_service
from quant.persistence import (
    StrategyPersistenceError,
    create_strategy_persisted,
    delete_strategy_persisted,
    get_strategy_persisted,
    list_strategies_persisted,
    update_strategy_persisted,
)
from quant.portfolio import paper_portfolio
from quant.market_cache import load_quant_market_snapshot, save_quant_market_snapshot
from quant.rules import public_rule_catalog
from quant.schemas import (
    BacktestRequest,
    CompareRequest,
    FQEDataSyncRequest,
    FQERequest,
    PaperBuyRequest,
    PaperResetRequest,
    PaperSellRequest,
    PreviewRequest,
    ResearchDslValidateRequest,
    ResearchRunRequest,
    ScanRequest,
    StrategyCreate,
    StrategyUpdate,
    ZhabanBacktestRequest,
    ZhabanScanRequest,
)
from quant.signals import quant_signal_service
from quant.storage import quant_store
from quant.templates import list_templates
from services.data_collector import collector
from database import async_session
from services.quote_cache import quote_snapshot_service
from services.overnight_strategy import overnight_strategy_service
from services.fqe_engine import fqe_compare_service
from services.fqe_reference_data import fqe_reference_data
from services.pit_market_data import pit_market_data_service
from services.quant_research_workspace import quant_research_workspace
from services.zhaban_strategy import zhaban_strategy_service


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


@router.get("/zhaban/config")
async def get_zhaban_strategy_config():
    return {"code": 0, "data": zhaban_strategy_service.config()}


@router.get("/zhaban/bootstrap")
async def get_zhaban_bootstrap():
    """Return controls immediately even when a cached-result database read is slow."""
    warnings: list[str] = []

    async def cached(label: str, awaitable):
        try:
            return await asyncio.wait_for(awaitable, timeout=3.0)
        except Exception as exc:
            warnings.append(f"{label}缓存暂不可用：{type(exc).__name__}")
            return None

    research, backtest = await asyncio.gather(
        cached("扫描", zhaban_strategy_service.latest()),
        cached("回测", zhaban_strategy_service.latest_backtest()),
    )
    return {
        "code": 0,
        "data": {
            "config": zhaban_strategy_service.config(),
            "research": research,
            "backtest": backtest,
            "scan_job": zhaban_strategy_service.running_scan_job(),
            "backtest_job": zhaban_strategy_service.running_backtest_job(),
            "warnings": warnings,
        },
    }


@router.get("/zhaban/latest")
async def get_latest_zhaban_research():
    return {"code": 0, "data": await zhaban_strategy_service.latest()}


@router.get("/zhaban/backtest/latest")
async def get_latest_zhaban_backtest():
    return {"code": 0, "data": await zhaban_strategy_service.latest_backtest()}


@router.post("/zhaban/scan", status_code=status.HTTP_202_ACCEPTED)
async def start_zhaban_research(payload: ZhabanScanRequest):
    try:
        job = await zhaban_strategy_service.start_scan(payload.model_dump(mode="json"))
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return {"code": 0, "data": job, "message": "炸板事件研究任务已提交"}


@router.get("/zhaban/scan/status/{job_id}")
async def get_zhaban_research_status(job_id: str):
    job = zhaban_strategy_service.job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="炸板研究任务不存在")
    if job.get("status") == "completed":
        job = {**job, "research": await zhaban_strategy_service.latest()}
    return {"code": 0, "data": job}


@router.post("/zhaban/backtest", status_code=status.HTTP_202_ACCEPTED)
async def start_zhaban_backtest(payload: ZhabanBacktestRequest):
    try:
        job = await zhaban_strategy_service.start_backtest(payload.model_dump(mode="json"))
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return {"code": 0, "data": job, "message": "炸板策略回测任务已提交"}


@router.get("/zhaban/backtest/status/{job_id}")
async def get_zhaban_backtest_status(job_id: str):
    job = zhaban_strategy_service.backtest_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="炸板回测任务不存在")
    if job.get("status") == "completed":
        job = {**job, "backtest": await zhaban_strategy_service.latest_backtest()}
    return {"code": 0, "data": job}


@router.get("/sectors")
async def get_sectors(limit: int = Query(300, ge=20, le=1000)):
    snapshot = quant_store.read("market_snapshot")
    if not snapshot.get("stocks"):
        try:
            snapshot = await collector.fetch_quant_market_snapshot()
            if not snapshot.get("stocks"):
                raise RuntimeError("全市场行情返回空列表")
            await save_quant_market_snapshot(snapshot)
            quant_store.write("market_snapshot", {"version": 1, **snapshot})
        except Exception:
            snapshot = await load_quant_market_snapshot()
            if snapshot.get("stocks"):
                quant_store.write("market_snapshot", {"version": 1, **snapshot})
            else:
                snapshot = await pit_market_data_service.latest_universe_snapshot()
                if not snapshot.get("stocks"):
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
        return {"code": 0, "data": await create_strategy_persisted(payload)}
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    except StrategyPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/strategies")
async def list_strategies_endpoint():
    try:
        return {"code": 0, "data": await list_strategies_persisted()}
    except StrategyPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/strategy/{strategy_id}")
async def get_strategy_endpoint(strategy_id: str):
    try:
        strategy = await get_strategy_persisted(strategy_id)
    except StrategyPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if strategy is None:
        raise HTTPException(status_code=404, detail="策略不存在")
    return {"code": 0, "data": strategy}


@router.put("/strategy/{strategy_id}")
async def update_strategy_endpoint(strategy_id: str, payload: StrategyUpdate):
    try:
        strategy = await update_strategy_persisted(strategy_id, payload)
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    except StrategyPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if strategy is None:
        raise HTTPException(status_code=404, detail="策略不存在")
    return {"code": 0, "data": strategy}


@router.delete("/strategy/{strategy_id}")
async def delete_strategy_endpoint(strategy_id: str):
    try:
        deleted = await delete_strategy_persisted(strategy_id)
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    except StrategyPersistenceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not deleted:
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
            "feature_coverage": result.get("feature_coverage"),
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


@router.post("/fqe/compare", status_code=status.HTTP_202_ACCEPTED)
async def start_fqe_compare(payload: FQERequest):
    """Start the auditable fundamental dual-engine comparison."""
    try:
        job = await fqe_compare_service.start(
            top_n=payload.top_n,
            candidate_pool=payload.candidate_pool,
            mode=payload.mode,
            force=payload.force,
        )
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return {"code": 0, "data": job}


@router.get("/fqe/status/{job_id}")
async def get_fqe_status(job_id: str):
    job = fqe_compare_service.get_status(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="FQE任务不存在")
    return {"code": 0, "data": job}


@router.get("/fqe/latest")
async def get_latest_fqe():
    return {"code": 0, "data": await fqe_compare_service.get_latest()}


@router.get("/fqe/bootstrap")
async def get_fqe_bootstrap():
    """Load the cached result and any active jobs for refresh-safe pages."""
    latest, sync = await asyncio.gather(
        fqe_compare_service.get_latest(),
        fqe_reference_data.latest_status(),
    )
    return {
        "code": 0,
        "data": {
            "result": latest,
            "job": fqe_compare_service.running_job(),
            "sync": sync,
        },
    }


@router.post("/fqe/data/sync", status_code=status.HTTP_202_ACCEPTED)
async def start_fqe_data_sync(payload: FQEDataSyncRequest):
    """Backfill the dated security master, PE history and strategic evidence."""
    result = await fqe_reference_data.queue_sync(
        full=payload.full,
        years=payload.years,
        force=payload.force,
    )
    return {"code": 0, "data": result}


@router.get("/fqe/data/status")
async def get_fqe_data_status():
    return {"code": 0, "data": await fqe_reference_data.latest_status()}


@router.get("/fqe/data/valuation/{stock_code}")
async def get_fqe_valuation_history(
    stock_code: str,
    days: int = Query(1095, ge=1, le=1825),
):
    try:
        result = await fqe_reference_data.get_history(stock_code, days=days)
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="该股票尚无PE历史缓存")
    return {"code": 0, "data": result}


@router.get("/signals")
async def get_signals(strategy_id: str | None = None):
    return {"code": 0, "data": quant_signal_service.get_signals(strategy_id)}


@router.get("/signals/history")
async def get_signal_history(limit: int = Query(20, ge=1, le=100)):
    return {"code": 0, "data": quant_signal_service.get_history(limit)}


@router.get("/research/workspace")
async def get_quant_research_workspace(refresh: bool = Query(False)):
    return {
        "code": 0,
        "data": await quant_research_workspace.workspace(force_refresh=refresh),
    }


@router.post("/research/run", status_code=status.HTTP_202_ACCEPTED)
async def run_quant_research(payload: ResearchRunRequest):
    try:
        job = await quant_research_workspace.start_experiment(payload.model_dump(mode="json"))
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"研究任务暂时不可用：{type(exc).__name__}") from exc
    return {"code": 0, "data": job, "message": "量化研究任务已提交"}


@router.get("/research/run/status/{job_id}")
async def get_quant_research_status(job_id: str):
    job = quant_research_workspace.job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="研究任务不存在或状态已过期")
    return {"code": 0, "data": job}


@router.post("/research/dsl/validate")
async def validate_quant_research_dsl(payload: ResearchDslValidateRequest):
    return {"code": 0, "data": quant_research_workspace.validate_dsl(payload.definition)}


@router.get("/overnight")
async def get_overnight_strategy_dashboard():
    return {"code": 0, "data": await overnight_strategy_service.dashboard()}


@router.get("/overnight/strategies")
async def get_overnight_strategies():
    return {"code": 0, "data": await overnight_strategy_service.list_strategies()}


@router.post("/overnight/strategies", status_code=status.HTTP_201_CREATED)
async def create_overnight_strategy(request: dict):
    try:
        strategy = await overnight_strategy_service.save_strategy(request or {})
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return {"code": 0, "data": strategy, "message": "一夜持股策略已另存"}


@router.put("/overnight/strategies/{strategy_id}")
async def update_overnight_strategy(strategy_id: str, request: dict):
    try:
        strategy = await overnight_strategy_service.save_strategy(request or {}, strategy_id)
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return {"code": 0, "data": strategy, "message": "一夜持股策略已更新"}


@router.delete("/overnight/strategies/{strategy_id}")
async def delete_overnight_strategy(strategy_id: str):
    try:
        await overnight_strategy_service.delete_strategy(strategy_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return {"code": 0, "data": {"deleted": True}, "message": "一夜持股策略已删除"}


@router.post("/overnight/strategies/{strategy_id}/activate")
async def activate_overnight_strategy(strategy_id: str):
    try:
        strategy = await overnight_strategy_service.activate_strategy(strategy_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"code": 0, "data": strategy, "message": "已切换一夜持股策略"}


@router.post("/overnight/compare")
async def compare_overnight_strategies(request: dict):
    try:
        result = await overnight_strategy_service.compare_strategies(
            list((request or {}).get("strategy_ids") or [])
        )
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return {"code": 0, "data": result}


@router.post("/overnight/runs", status_code=status.HTTP_202_ACCEPTED)
async def start_overnight_strategy_run(request: dict):
    stage = str((request or {}).get("stage") or "preliminary").strip().lower()
    requested_trigger = str((request or {}).get("trigger") or "manual").strip().lower()
    trigger = "github_schedule" if requested_trigger == "github_schedule" else "manual"
    strategy_id = str((request or {}).get("strategy_id") or "").strip() or None
    research_only = bool((request or {}).get("research_only"))
    try:
        result = await overnight_strategy_service.start(
            stage,
            trigger=trigger,
            background=True,
            strategy_id=strategy_id,
            research_only=research_only,
        )
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return {"code": 0, "data": result, "message": "一夜持股策略任务已提交"}


@router.get("/overnight/runs/{run_id}")
async def get_overnight_strategy_run(run_id: int):
    try:
        result = await overnight_strategy_service.get_run(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"code": 0, "data": {"run": result}}


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
    if await get_strategy_persisted(strategy_id) is None:
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
        snapshot = await quote_snapshot_service.fetch(codes, async_session)
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