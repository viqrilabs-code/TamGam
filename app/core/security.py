# app/core/security.py
# JWT token creation/decoding and password hashing
# Used by: auth endpoints, dependencies.py

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# Password hashing context -- bcrypt with auto-upgrade
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── Password Hashing ──────────────────────────────────────────────────────────

def hash_password(plain_password: str) -> str:
    """Hash a plain-text password using bcrypt."""
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain-text password against a bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


# ── JWT Tokens ────────────────────────────────────────────────────────────────

def create_access_token(user_id: UUID, role: str) -> str:
    """
    Create a short-lived JWT access token.
    Expires in ACCESS_TOKEN_EXPIRE_MINUTES (default: 60 min).

    Payload:
        sub  -- user UUID as string
        role -- student | teacher | admin
        type -- "access"
        exp  -- expiry timestamp
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: UUID) -> str:
    """
    Create a long-lived refresh token JWT.
    Expires in REFRESH_TOKEN_EXPIRE_DAYS (default: 30 days).
    Stored (as hash) in refresh_tokens table -- rotated on each use.

    Payload:
        sub  -- user UUID as string
        type -- "refresh"
        exp  -- expiry timestamp
    """
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.refresh_token_expire_days
    )
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Optional[dict]:
    """
    Decode and validate a JWT token.
    Returns the payload dict if valid, None if expired or invalid.
    Does NOT check the database -- use dependencies.py for full validation.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except JWTError:
        return None


def get_token_expiry(days: int = 0, minutes: int = 0) -> datetime:
    """Helper to compute an expiry datetime from now."""
    return datetime.now(timezone.utc) + timedelta(days=days, minutes=minutes)


# ── Refresh Token Hashing ─────────────────────────────────────────────────────

def hash_token(token: str) -> str:
    """
    SHA-256 hash of a refresh token for safe DB storage.
    We store the hash, not the raw token, so a DB breach
    doesn't expose valid refresh tokens.
    """
    return hashlib.sha256(token.encode()).hexdigest()