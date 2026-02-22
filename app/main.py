# app/main.py
# TamGam FastAPI application entry point

import logging
import time
import uuid
from contextlib import asynccontextmanager

import redis as redis_lib
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging_config import configure_logging
from app.db.session import check_db_connection, engine

configure_logging(debug=settings.debug)
logger = logging.getLogger("tamgam.main")


def run_startup_migrations() -> bool:
    try:
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations: OK")
        return True
    except Exception as exc:
        logger.exception("Database migrations failed: %s", exc)
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s [%s]", settings.app_name, settings.app_version, settings.app_env)

    if settings.auto_migrate_on_startup:
        run_startup_migrations()

    if check_db_connection():
        logger.info("Database connection: OK")
    else:
        logger.warning("Database connection failed -- check DATABASE_URL")

    try:
        r = redis_lib.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        r.close()
        logger.info("Redis connection: OK")
    except Exception:
        logger.warning("Redis connection failed -- check REDIS_URL")

    yield

    logger.info("Shutting down -- disposing DB connection pool")
    engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "TamGam by Viqri Labs -- EdTech platform for Indian children aged 10-14. "
        "From darkness to light."
    ),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
    debug=settings.debug,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[*settings.cors_origins, "null"],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging_middleware(request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    request.state.request_id = request_id
    start = time.perf_counter()
    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        response.headers["x-request-id"] = request_id
        logger.info(
            "request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
    except Exception:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.exception(
            "request_id=%s method=%s path=%s status=500 duration_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            duration_ms,
        )
        raise


app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["Health"], include_in_schema=False)
def health_check():
    db_ok = check_db_connection()
    redis_ok = False
    try:
        r = redis_lib.from_url(settings.redis_url, socket_connect_timeout=1)
        r.ping()
        r.close()
        redis_ok = True
    except Exception:
        logger.debug("Redis health check failed")

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "app": settings.app_name,
            "version": settings.app_version,
            "environment": settings.app_env,
            "services": {
                "database": "ok" if db_ok else "unavailable",
                "redis": "ok" if redis_ok else "unavailable",
            },
        },
    )


@app.get("/", include_in_schema=False)
def root():
    return JSONResponse(
        content={
            "message": "TamGam API -- From darkness to light",
            "docs": "/api/docs",
            "health": "/health",
        }
    )
