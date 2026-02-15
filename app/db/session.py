# app/db/session.py
# Database session management
#
# Two connection modes:
#   Local dev  → TCP via psycopg2  (DATABASE_URL in .env)
#   Cloud Run  → Unix socket via pg8000 (auto-detected by APP_ENV=production)
#
# FastAPI endpoints get a session via: Depends(get_db)

import os
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session


def _build_database_url() -> str:
    """
    Build the correct DATABASE_URL for the current environment.

    - Development: reads DATABASE_URL directly from env (psycopg2 TCP)
    - Production:  constructs a pg8000 Unix socket URL from individual
                   DB_USER, DB_PASS, DB_NAME, DB_CONNECTION_NAME env vars
                   (these are injected by Cloud Run from Secret Manager)
    """
    app_env = os.getenv("APP_ENV", "development")

    if app_env == "production":
        db_user = os.environ["DB_USER"]
        db_pass = os.environ["DB_PASS"]
        db_name = os.environ["DB_NAME"]
        db_connection_name = os.environ["DB_CONNECTION_NAME"]
        # pg8000 Unix socket format for Cloud SQL Auth Proxy
        socket_path = f"/cloudsql/{db_connection_name}/.s.PGSQL.5432"
        return (
            f"postgresql+pg8000://{db_user}:{db_pass}@/{db_name}"
            f"?unix_sock={socket_path}"
        )

    # Local development — use DATABASE_URL from .env directly
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Copy .env.example to .env and fill in your local DB credentials."
        )
    return database_url


# ── Engine ────────────────────────────────────────────────────────────────────
# pool_pre_ping=True  → test connection before each use (handles Cloud SQL
#                        idle disconnects gracefully)
# pool_size / max_overflow → tuned for Cloud Run's concurrency model
#   Cloud Run scales horizontally — keep per-instance pool small
engine = create_engine(
    _build_database_url(),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,  # Recycle connections every 30 min
    echo=os.getenv("APP_ENV") != "production",  # SQL logging in dev only
)

# ── Session Factory ───────────────────────────────────────────────────────────
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # Prevents lazy load errors after commit
)


# ── FastAPI Dependency ────────────────────────────────────────────────────────
def get_db() -> Generator[Session, None, None]:
    """
    Dependency injected into every FastAPI endpoint that needs DB access.

    Usage:
        @router.get("/example")
        def example(db: Session = Depends(get_db)):
            ...

    Guarantees the session is always closed, even on exceptions.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── Health Check Helper ───────────────────────────────────────────────────────
def check_db_connection() -> bool:
    """
    Used by /health endpoint to verify DB connectivity.
    Returns True if connected, False otherwise.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False