from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from api.routes import router
from api.quant_routes import router as quant_router
from api.personal_routes import router as personal_router
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
    from services.scheduler import scheduler, start_scheduler
    await seed()
    from quant.persistence import hydrate_strategy_store
    await hydrate_strategy_store()
    await history_cache.resume_incomplete_runs()
    await ai_robot_service.resume_incomplete_runs()
    await start_scheduler(collector)
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# CORS - both middleware + manual header injection
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.middleware("http")
async def add_cors_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"
    return response


app.include_router(router)
app.include_router(quant_router)
app.include_router(personal_router)


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


@app.options("/{full_path:path}")
async def options_handler(full_path: str):
    return Response(status_code=200)
