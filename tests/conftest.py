import os


# Ensure module imports that build config/engine do not fail during test collection.
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://ci:ci@localhost:5432/ci")
os.environ.setdefault("REDIS_URL", "")

