# app/schemas/auth.py
# Pydantic request/response models for authentication endpoints.

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    verification_code: str
    role: str = "student"  # student | teacher
    teacher_declaration_accepted: bool = False
    teacher_declaration_version: Optional[str] = None

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
        value = v.strip()
        if not value:
            raise ValueError("Full name cannot be empty")
        return value

    @field_validator("teacher_declaration_version")
    @classmethod
    def declaration_version_trim(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return v.strip() or None

    @field_validator("verification_code")
    @classmethod
    def verification_code_six_digits(cls, v: str) -> str:
        value = (v or "").strip()
        if len(value) != 6 or not value.isdigit():
            raise ValueError("verification_code must be a 6-digit number")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class EmailLoginCodeSendRequest(BaseModel):
    email: EmailStr
    password: str


class SignupEmailCodeSendRequest(BaseModel):
    email: EmailStr


class ForgotPasswordCodeSendRequest(BaseModel):
    email: EmailStr


class ForgotPasswordLinkSendRequest(BaseModel):
    email: EmailStr


class EmailLoginCodeSendResponse(BaseModel):
    message: str
    resend_after_seconds: int


class EmailCodeLoginRequest(BaseModel):
    email: EmailStr
    password: str
    verification_code: str

    @field_validator("verification_code")
    @classmethod
    def verification_code_six_digits(cls, v: str) -> str:
        value = (v or "").strip()
        if len(value) != 6 or not value.isdigit():
            raise ValueError("verification_code must be a 6-digit number")
        return value


class ForgotPasswordResetRequest(BaseModel):
    email: EmailStr
    verification_code: str
    new_password: str

    @field_validator("verification_code")
    @classmethod
    def forgot_password_code_six_digits(cls, v: str) -> str:
        value = (v or "").strip()
        if len(value) != 6 or not value.isdigit():
            raise ValueError("verification_code must be a 6-digit number")
        return value

    @field_validator("new_password")
    @classmethod
    def forgot_password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("new_password must be at least 8 characters")
        return v


class ForgotPasswordTokenResetRequest(BaseModel):
    reset_token: str
    new_password: str

    @field_validator("reset_token")
    @classmethod
    def reset_token_not_empty(cls, v: str) -> str:
        value = (v or "").strip()
        if not value:
            raise ValueError("reset_token is required")
        return value

    @field_validator("new_password")
    @classmethod
    def forgot_password_link_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("new_password must be at least 8 characters")
        return v


class FirebasePhoneLoginRequest(BaseModel):
    id_token: str
    full_name: Optional[str] = None
    role: str = "student"  # student | teacher
    teacher_declaration_accepted: bool = False
    teacher_declaration_version: Optional[str] = None

    @field_validator("id_token")
    @classmethod
    def id_token_not_empty(cls, v: str) -> str:
        token = v.strip()
        if not token:
            raise ValueError("id_token cannot be empty")
        return token

    @field_validator("full_name")
    @classmethod
    def trim_full_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        trimmed = v.strip()
        return trimmed or None

    @field_validator("role")
    @classmethod
    def firebase_role_valid(cls, v: str) -> str:
        role = (v or "").strip().lower()
        if role not in ("student", "teacher"):
            raise ValueError("Role must be 'student' or 'teacher'")
        return role

    @field_validator("teacher_declaration_version")
    @classmethod
    def firebase_teacher_declaration_version_trim(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        value = v.strip()
        return value or None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: UUID
    role: str
    full_name: str
    is_subscribed: bool
    is_verified_teacher: bool


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class GoogleCallbackResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: UUID
    role: str
    full_name: str
    is_new_user: bool
    is_subscribed: bool
    is_verified_teacher: bool


class MessageResponse(BaseModel):
    message: str
