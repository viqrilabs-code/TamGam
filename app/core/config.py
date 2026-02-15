# app/core/config.py
# All application settings loaded from environment variables / .env file
# In production (Cloud Run): values come from GCP Secret Manager via env injection
# In development: loaded from .env file via python-dotenv

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Single source of truth for all TamGam configuration.
    pydantic-settings automatically reads from environment variables.
    Variable names are case-insensitive.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore unknown env vars (safe for Cloud Run)
    )

    # App
    app_env: str = "development"
    app_name: str = "TamGam"
    app_version: str = "1.0.0"
    debug: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    allowed_origins: str = "http://localhost:3000,http://localhost:8000"

    # Database
    database_url: str

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # JWT
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"

    # GCP
    gcp_project_id: str = "tamgam-prod"
    gcp_region: str = "asia-south1"

    # GCS
    gcs_public_bucket: str = "tamgam-public"
    gcs_private_bucket: str = "tamgam-docs-private"

    # Cloud Tasks
    cloud_tasks_queue: str = "tamgam-jobs"
    cloud_tasks_location: str = "asia-south1"

    # Google Drive
    google_drive_folder_id: str = ""
    google_service_account_key_path: str = "./service-account.json"

    # Vertex AI / Gemini
    vertex_ai_location: str = "asia-south1"
    gemini_model: str = "gemini-2.5-flash"
    embedding_model: str = "text-embedding-004"
    embedding_dimensions: int = 768

    # Razorpay
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # SendGrid
    sendgrid_api_key: str = ""
    email_from: str = "noreply@tamgam.in"
    email_from_name: str = "TamGam"

    # AI Tutor RAG
    rag_chunk_size: int = 500
    rag_chunk_overlap: int = 50
    rag_top_k: int = 5

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origins(self) -> List[str]:
        """Parse comma-separated ALLOWED_ORIGINS into a list."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """
    Returns a cached Settings instance.
    Use as a FastAPI dependency: settings = Depends(get_settings)
    Or import directly:         from app.core.config import settings
    """
    return Settings()


# Module-level singleton -- import this directly in most places
settings = get_settings()