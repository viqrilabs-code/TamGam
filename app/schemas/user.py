# app/schemas/user.py
# Pydantic request/response models for user profile endpoints.
#
# Three distinct response shapes:
#   OwnProfileResponse   — full private view (only returned to the user themselves / admin)
#   PublicProfileResponse — sanitised view (returned to anyone for GET /users/{user_id})
#   UpdateProfileRequest  — PATCH /users/me body
#
# resolve_user_marks() is called in the endpoint; the marks are injected into
# every response that includes author identity (is_subscribed, is_verified_teacher).

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, HttpUrl, field_validator


# ── Embedded sub-schemas ──────────────────────────────────────────────────────

class TeacherPublicInfo(BaseModel):
    """Shown on public teacher profiles."""
    subjects: list[str]
    bio: Optional[str] = None
    is_verified: bool
    total_students: int

    model_config = {"from_attributes": True}


class StudentPublicInfo(BaseModel):
    """Minimal student info shown publicly."""
    grade: Optional[int] = None
    performance_score: float = 0.0
    badges: list[str] = []

    model_config = {"from_attributes": True}


# ── Own (private) profile ─────────────────────────────────────────────────────

class OwnProfileResponse(BaseModel):
    """
    Full profile — only returned when requester == this user, or requester is admin.
    Contains all fields including phone, address, subscription status.
    """
    id: UUID
    email: EmailStr
    full_name: str
    phone: Optional[str] = None
    avatar_url: Optional[str] = None
    role: str                         # student | teacher | admin
    is_active: bool
    is_subscribed: bool               # resolved live via resolve_user_marks()
    is_verified_teacher: bool         # resolved live via resolve_user_marks()

    # Role-specific nested info
    teacher_info: Optional[TeacherPublicInfo] = None
    student_info: Optional[StudentPublicInfo] = None

    model_config = {"from_attributes": True}


# ── Public profile ────────────────────────────────────────────────────────────

class PublicProfileResponse(BaseModel):
    """
    Sanitised view for GET /users/{user_id}.
    Never includes phone, address, or payment info.
    Teacher: includes subjects, bio, is_verified, total_students.
    Student: includes grade, performance_score, badges only.
    """
    id: UUID
    full_name: str
    avatar_url: Optional[str] = None
    role: str
    is_subscribed: bool               # resolved live — shown as ⭐ mark in UI
    is_verified_teacher: bool         # resolved live — shown as 🟡T mark in UI

    # Only populated for teachers
    teacher_info: Optional[TeacherPublicInfo] = None
    # Only populated for students
    student_info: Optional[StudentPublicInfo] = None

    model_config = {"from_attributes": True}


# ── Update request ────────────────────────────────────────────────────────────

class UpdateProfileRequest(BaseModel):
    """
    PATCH /users/me — only these three fields are user-editable here.
    Avatar is updated via POST /users/me/avatar (GCS upload), not this endpoint.
    """
    full_name: Optional[str] = None
    phone: Optional[str] = None
    avatar_url: Optional[str] = None  # direct URL update (e.g. after GCS upload)

    @field_validator("full_name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("Full name cannot be empty")
        return v

    @field_validator("phone")
    @classmethod
    def phone_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            # Accept Indian mobile: optional +91, then 10 digits
            import re
            v = v.strip()
            if not re.match(r"^(\+91)?[6-9]\d{9}$", v):
                raise ValueError("Enter a valid 10-digit Indian mobile number")
        return v


# ── Avatar upload response ────────────────────────────────────────────────────

class AvatarUploadResponse(BaseModel):
    avatar_url: str
    message: str = "Avatar updated successfully"