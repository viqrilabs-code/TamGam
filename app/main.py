# app/main.py
# TamGam FastAPI application entry point
#
# Startup:  DB connection check, Redis ping
# Shutdown: Clean connection pool disposal
# Routes:   /health, /api/v1/* (all endpoints via master router)

from contextlib import asynccontextmanager

import redis as redis_lib
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import check_db_connection, engine


def run_startup_migrations() -> bool:
    """Run `alembic upgrade head` using the project alembic.ini."""
    try:
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
        print("Database migrations: OK")
        return True
    except Exception as exc:
        print(f"WARNING: Database migrations failed -- {exc}")
        return False


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup and shutdown logic.
    FastAPI's modern replacement for @app.on_event("startup").
    """
    # Startup
    print(f"Starting {settings.app_name} v{settings.app_version} [{settings.app_env}]")

    # Apply DB migrations (development default)
    if settings.auto_migrate_on_startup:
        run_startup_migrations()

    # Verify DB connection
    if check_db_connection():
        print("Database connection: OK")
    else:
        print("WARNING: Database connection failed -- check DATABASE_URL")

    # Verify Redis connection
    try:
        r = redis_lib.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        r.close()
        print("Redis connection: OK")
    except Exception:
        print("WARNING: Redis connection failed -- check REDIS_URL")

    yield  # App runs here

    # Shutdown
    print("Shutting down -- disposing DB connection pool")
    engine.dispose()


# ── App Instance ──────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "TamGam by Viqri Labs -- EdTech platform for Indian children aged 10-14. "
        "From darkness to light."
    ),
    docs_url="/api/docs",       # Swagger UI
    redoc_url="/api/redoc",     # ReDoc
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
    debug=settings.debug,
)


# ── CORS ──────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],#settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────

# All API routes under /api/v1
app.include_router(api_router, prefix="/api/v1")


# ── Health Check ──────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"], include_in_schema=False)
def health_check():
    """
    Health check endpoint for Cloud Run and load balancers.
    Returns 200 OK if the app is running.
    DB and Redis status included for observability.
    """
    db_ok = check_db_connection()

    redis_ok = False
    try:
        r = redis_lib.from_url(settings.redis_url, socket_connect_timeout=1)
        r.ping()
        r.close()
        redis_ok = True
    except Exception:
        pass

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


# ── Root Redirect ─────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    return JSONResponse(
        content={
            "message": "TamGam API -- From darkness to light",
            "docs": "/api/docs",
            "health": "/health",
        }
    )