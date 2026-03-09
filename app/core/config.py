# app/core/config.py
# All application settings loaded from environment variables / .env file
# In production (Cloud Run): values come from GCP Secret Manager via env injection
# In development: loaded from .env file via python-dotenv

from functools import lru_cache
from typing import List

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Single source of truth for all tamgam configuration.
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
    app_name: str = "tamgam"
    app_version: str = "1.0.0"
    debug: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    allowed_origins: str = "http://localhost:3000,http://localhost:8000"

    # Database
    database_url: str
    auto_migrate_on_startup: bool = True
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle_seconds: int = 1800

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_required: bool = False

    # JWT
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    # Google OAuth
    google_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("google_client_id", "GOOGLE_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_ID"),
    )
    google_client_secret: str = Field(
        default="",
        validation_alias=AliasChoices("google_client_secret", "GOOGLE_CLIENT_SECRET", "GOOGLE_OAUTH_CLIENT_SECRET"),
    )
    google_redirect_uri: str = Field(
        default="http://localhost:8000/api/v1/auth/google/callback",
        validation_alias=AliasChoices("google_redirect_uri", "GOOGLE_REDIRECT_URI", "GOOGLE_OAUTH_REDIRECT_URI"),
    )
    firebase_project_id: str = Field(
        default="",
        validation_alias=AliasChoices("firebase_project_id", "FIREBASE_PROJECT_ID"),
    )
    firebase_credentials_json: str = Field(
        default="",
        validation_alias=AliasChoices("firebase_credentials_json", "FIREBASE_CREDENTIALS_JSON"),
    )
    firebase_credentials_path: str = Field(
        default="",
        validation_alias=AliasChoices("firebase_credentials_path", "FIREBASE_CREDENTIALS_PATH"),
    )

    # GCP
    gcp_project_id: str = "tamgam-prod"
    gcp_region: str = "asia-south1"

    # GCS
    gcs_public_bucket: str = "tamgam-public"
    gcs_private_bucket: str = "tamgam-docs-private"

    # Cloud Tasks
    cloud_tasks_enabled: bool = False
    cloud_tasks_queue: str = "tamgam-jobs"
    cloud_tasks_location: str = "asia-south1"
    cloud_tasks_project_id: str = ""
    cloud_tasks_target_url: str = ""
    cloud_tasks_auth_secret: str = ""

    # Google Drive
    google_drive_folder_id: str = ""
    google_service_account_key_path: str = "./service-account.json"

    # Vertex AI / Gemini
    vertex_ai_location: str = "asia-south1"
    gemini_model: str = "gemini-2.5-flash"
    embedding_model: str = "text-embedding-001"
    embedding_dimensions: int = 768
    gemini_api_key_1: str = ""
    gemini_api_key_2: str = ""
    gemini_api_key_3: str = ""
    gemini_api_key_4: str = ""
    gemini_api_key_5: str = ""

    # Razorpay
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    razorpayx_account_number: str = ""

    # SendGrid
    sendgrid_api_key: str = ""
    email_from: str = "noreply@tamgam.in"
    email_from_name: str = "tamgam"
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_smtp_username: str = ""
    email_smtp_password: str = ""
    email_smtp_use_tls: bool = True
    email_login_code_ttl_minutes: int = 10
    email_login_code_resend_cooldown_seconds: int = 45
    email_login_code_max_attempts: int = 5

    # AI Tutor RAG
    rag_chunk_size: int = 500
    rag_chunk_overlap: int = 50
    rag_top_k: int = 5

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, v):
        if isinstance(v, bool):
            return v
        text = str(v or "").strip().lower()
        if text in {"1", "true", "yes", "y", "on", "debug"}:
            return True
        if text in {"0", "false", "no", "n", "off", "release", "prod", "production", ""}:
            return False
        raise ValueError("DEBUG must be a boolean-like value (true/false).")

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
