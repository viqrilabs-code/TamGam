# alembic/env.py
# Alembic migration environment
#
# Key responsibilities:
#   1. Build database URL dynamically (same logic as app/db/session.py)
#   2. Import all models via app/db/base.py so Alembic detects schema changes
#   3. Support both offline (SQL script) and online (live connection) modes

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# ── Make app importable from alembic/ directory ───────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Load .env for local development ──────────────────────────────────────────
# In production (Cloud Run), env vars come from Secret Manager — no .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not required in production

# ── Alembic Config ────────────────────────────────────────────────────────────
config = context.config

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Import all models so Alembic can detect them ─────────────────────────────
# app/db/base.py imports every model — this single import covers all tables
import app.db.base  # noqa: F401 — registers all models as side effect
from app.db.base_class import Base  # noqa: F401
target_metadata = Base.metadata


# ── Build Database URL ────────────────────────────────────────────────────────
def get_url() -> str:
    """
    Build database URL from environment — same dual-mode logic as session.py.
    Called by both offline and online migration modes.
    """
    app_env = os.getenv("APP_ENV", "development")

    if app_env == "production":
        db_user = os.environ["DB_USER"]
        db_pass = os.environ["DB_PASS"]
        db_name = os.environ["DB_NAME"]
        db_connection_name = os.environ["DB_CONNECTION_NAME"]
        socket_path = f"/cloudsql/{db_connection_name}/.s.PGSQL.5432"
        return (
            f"postgresql+pg8000://{db_user}:{db_pass}@/{db_name}"
            f"?unix_sock={socket_path}"
        )

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL not set. Run: cp .env.example .env and fill in credentials."
        )
    return database_url


# ── Offline Mode ──────────────────────────────────────────────────────────────
# Generates SQL migration script without connecting to DB
# Usage: alembic upgrade head --sql > migration.sql
def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,          # Detect column type changes
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online Mode ───────────────────────────────────────────────────────────────
# Connects to DB and runs migrations directly
# Usage: alembic upgrade head
def run_migrations_online() -> None:
    # Override the URL in config (alembic.ini has it blank)
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,    # No connection pooling for migrations
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


# ── Entry Point ───────────────────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()