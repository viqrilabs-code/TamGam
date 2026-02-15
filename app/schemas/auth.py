# app/schemas/auth.py
# Pydantic request/response models for authentication endpoints

import re
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator


# ── Signup ────────────────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: str = "student"  # student | teacher

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("role")
    @classmethod
    def valid_role(cls, v: str) -> str:
        if v not in ("student", "teacher"):
            raise ValueError("Role must be 'student' or 'teacher'")
        return v

    @field_validator("full_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Full name cannot be empty")
        return v


# ── Login ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ── Token Responses ───────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until access token expiry

    # User info embedded so frontend doesn't need a second request
    user_id: UUID
    role: str
    full_name: str
    is_subscribed: bool
    is_verified_teacher: bool


class AccessTokenResponse(BaseModel):
    """Returned by /refresh -- only a new access token, not a new refresh token."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int


# ── Refresh ───────────────────────────────────────────────────────────────────

class RefreshRequest(BaseModel):
    refresh_token: str


# ── Logout ────────────────────────────────────────────────────────────────────

class LogoutRequest(BaseModel):
    refresh_token: str


# ── Google OAuth ──────────────────────────────────────────────────────────────

class GoogleCallbackResponse(BaseModel):
    """Returned after successful Google OAuth -- same shape as TokenResponse."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: UUID
    role: str
    full_name: str
    is_new_user: bool  # True if account was just created via Google
    is_subscribed: bool
    is_verified_teacher: bool


# ── Generic Message ───────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str