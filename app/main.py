from __future__ import annotations
import sentry_sdk
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import structlog

from app.config import get_settings
from app.database import get_db_pool, close_db_pool
from app.routers import auth, communities, missions, competitions, user, offerwall, fee_routing

settings = get_settings()
logger   = structlog.get_logger()

if settings.sentry_dsn:
    sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.app_env, traces_sample_rate=0.2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("UTOPIA API starting", env=settings.app_env)
    yield
    await close_db_pool()
    logger.info("UTOPIA API shut down")


app = FastAPI(
    title       = "UTOPIA API",
    description = "Gamified community engagement platform for token communities",
    version     = "1.0.0",
    docs_url    = "/docs" if not settings.is_production else None,
    redoc_url   = "/redoc" if not settings.is_production else None,
    lifespan    = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = settings.cors_origins,
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception", path=request.url.path, error=str(exc), exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal server error", "detail": str(exc) if settings.debug else None},
    )


app.include_router(auth.router)
app.include_router(communities.router)
app.include_router(missions.router)
app.include_router(competitions.router)
app.include_router(user.router)
app.include_router(offerwall.router)
app.include_router(fee_routing.router)


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "version": "1.0.0", "env": settings.app_env}


@app.get("/", tags=["system"])
async def root():
    return {"message": "UTOPIA API", "docs": "/docs"}
