# Dockerfile
# TamGam FastAPI application
# Python 3.13 slim — matches your local dev version

FROM python:3.13-slim

# ── System dependencies ───────────────────────────────────────────────────────
# libpq-dev: required by psycopg2 to compile C extension
# gcc: build tool for native extensions
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR /app

# ── Install Python dependencies ───────────────────────────────────────────────
# Copy requirements first for Docker layer caching
# (re-install only when requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Copy application code ─────────────────────────────────────────────────────
COPY . .

# ── Non-root user (security best practice for Cloud Run) ─────────────────────
RUN useradd --create-home --shell /bin/bash tamgam
USER tamgam

# ── Port ──────────────────────────────────────────────────────────────────────
EXPOSE 8000

# ── Health check ──────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ── Start command ─────────────────────────────────────────────────────────────
# Cloud Run sets PORT env var — default to 8000 for local
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]