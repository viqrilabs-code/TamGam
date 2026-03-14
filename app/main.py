# app/main.py
# tamgam FastAPI application entry point

import logging
import time
import uuid
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import redis as redis_lib
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging_config import configure_logging
from app.db.session import check_db_connection, engine

configure_logging(debug=settings.debug)
logger = logging.getLogger("tamgam.main")
STARTUP_DB_CHECK_TIMEOUT_SECONDS = 2.0


async def _check_db_connection_with_timeout(timeout_seconds: float) -> bool | None:
    """
    Run DB availability probe without blocking startup for long connector timeouts.
    Returns:
      True  -> DB reachable
      False -> DB probe completed and failed
      None  -> Probe timed out
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(check_db_connection),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        return None
    except Exception:
        return False


def _redis_check_enabled() -> bool:
    return bool(settings.redis_url.strip())


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

    db_ok = await _check_db_connection_with_timeout(STARTUP_DB_CHECK_TIMEOUT_SECONDS)
    if db_ok is True:
        logger.info("Database connection: OK")
    elif db_ok is False:
        logger.warning("Database connection failed -- check DATABASE_URL")
    else:
        logger.warning(
            "Database connection check timed out after %.1fs -- continuing startup",
            STARTUP_DB_CHECK_TIMEOUT_SECONDS,
        )

    if _redis_check_enabled():
        try:
            r = redis_lib.from_url(settings.redis_url, socket_connect_timeout=2)
            r.ping()
            r.close()
            logger.info("Redis connection: OK")
        except Exception:
            level = logger.error if settings.redis_required else logger.warning
            level("Redis connection failed -- check REDIS_URL")
    else:
        logger.info("Redis disabled (REDIS_URL not set)")

    yield

    logger.info("Shutting down -- disposing DB connection pool")
    engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "tamgam by Viqri Labs -- EdTech platform for Indian children in standards 5-10. "
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


@app.get("/auth/google", include_in_schema=False)
def legacy_google_login_redirect(request: Request):
    """
    Backward-compatible alias for older OAuth redirect URIs.
    """
    qs = request.url.query
    target = "/api/v1/auth/google"
    if qs:
        target = f"{target}?{qs}"
    return RedirectResponse(url=target, status_code=307)


@app.get("/auth/google/callback", include_in_schema=False)
def legacy_google_callback_redirect(request: Request):
    """
    Backward-compatible alias for older OAuth callback URIs.
    """
    qs = request.url.query
    target = "/api/v1/auth/google/callback"
    if qs:
        target = f"{target}?{qs}"
    return RedirectResponse(url=target, status_code=307)


@app.get("/health", tags=["Health"], include_in_schema=False)
def health_check():
    db_ok = check_db_connection()
    redis_state = "disabled"
    if _redis_check_enabled():
        try:
            r = redis_lib.from_url(settings.redis_url, socket_connect_timeout=1)
            r.ping()
            r.close()
            redis_state = "ok"
        except Exception:
            redis_state = "unavailable"
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
                "redis": redis_state,
            },
        },
    )


@app.get("/", include_in_schema=False)
def root():
    if _FRONTEND_DIR.exists():
        return RedirectResponse(url="/index.html", status_code=307)
    return JSONResponse(
        content={
            "message": "tamgam API -- From darkness to light",
            "docs": "/api/docs",
            "health": "/health",
        }
    )


# Local convenience: serve frontend static pages from the same host/port as API.
# Keep this mount after explicit API/health routes so they are not shadowed.
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "tamgam-frontend"
if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
