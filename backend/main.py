from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from api.routes import router
from api.quant_routes import router as quant_router
from api.personal_routes import router as personal_router
from api.openclaw_routes import router as openclaw_router
from api.research_routes import router as research_router
from api.forecast_routes import router as forecast_router
from api.trading_skill_routes import router as trading_skill_router
from database import init_db
from config import settings
from services.data_collector import collector
import sim_models


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    from seed_data import seed
    from services.history_cache import history_cache
    from services.ai_robot import ai_robot_service
    from services.fqe_reference_data import fqe_reference_data
    from services.scheduler import scheduler, start_scheduler
    from services.midday_research import midday_research_service
    from services.weekend_research import weekend_research_service
    from services.trading_skill_registry import ensure_trading_skill_registry
    await seed()
    await ensure_trading_skill_registry()
    from quant.persistence import hydrate_strategy_store
    await hydrate_strategy_store()
    await history_cache.resume_incomplete_runs()
    await ai_robot_service.resume_incomplete_runs()
    await fqe_reference_data.resume_incomplete_runs()
    await fqe_reference_data.ensure_initialized()
    await weekend_research_service.resume_incomplete_runs()
    await midday_research_service.resume_incomplete_runs()
    await start_scheduler(collector)
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# Safari rejects credentialed cross-origin requests when the preflight response
# combines wildcard origins/headers. Keep one authoritative CORS layer and
# return the requesting trusted origin plus an explicit header allow-list.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ai-buffett-quant.netlify.app",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:3100",
        "http://127.0.0.1:3100",
    ],
    allow_origin_regex=r"^https://[a-z0-9-]+--ai-buffett-quant\.netlify\.app$",
    allow_credentials=True,
    allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Cache-Control",
        "Pragma",
        "X-Requested-With",
    ],
)


app.include_router(router)
app.include_router(quant_router)
app.include_router(personal_router)
app.include_router(openclaw_router)
app.include_router(research_router)
app.include_router(forecast_router)
app.include_router(trading_skill_router)


@app.get("/")
async def root():
    return {"name": settings.app_name, "version": settings.app_version, "status": "running"}


@app.head("/")
async def root_head():
    """Keep legacy Render probes healthy while the configured path migrates."""
    return Response(status_code=200)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.head("/health")
async def health_head():
    return Response(status_code=200)


@app.get("/health/data-source")
async def data_source_health():
    try:
        return await collector.check_data_source()
    except Exception as exc:
        source = "proxy" if settings.data_proxy_base_url else "direct"
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "source": source,
                "error": type(exc).__name__,
            },
        )
