# app/schemas/student.py
# Pydantic request/response models for student profile endpoints

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


# ── Student Profile ───────────────────────────────────────────────────────────

class StudentProfilePublic(BaseModel):
    """Public-facing student profile -- minimal info only."""
    id: UUID
    user_id: UUID
    full_name: str
    avatar_url: Optional[str] = None
    grade: Optional[int] = None
    performance_score: Optional[float] = None
    badges: Optional[List[str]] = None
    streak_days: int


class StudentProfilePrivate(StudentProfilePublic):
    """Full student profile -- only shown to the student themselves and admin."""
    date_of_birth: Optional[date] = None
    parent_name: Optional[str] = None
    parent_phone: Optional[str] = None
    parent_email: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_pincode: Optional[str] = None
    is_subscribed: bool
    created_at: datetime
    updated_at: datetime


class StudentProfileUpdate(BaseModel):
    """Fields a student can update on their own profile."""
    grade: Optional[int] = None
    date_of_birth: Optional[date] = None
    parent_name: Optional[str] = None
    parent_phone: Optional[str] = None
    parent_email: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_pincode: Optional[str] = None

    @field_validator("grade")
    @classmethod
    def valid_grade(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v not in range(1, 13):
            raise ValueError("Grade must be between 1 and 12")
        return v

    @field_validator("address_pincode")
    @classmethod
    def valid_pincode(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.isdigit():
            raise ValueError("Pincode must contain only digits")
        return v


# ── Enrollment ────────────────────────────────────────────────────────────────

class EnrollRequest(BaseModel):
    teacher_id: UUID
    subject: str

    @field_validator("subject")
    @classmethod
    def subject_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Subject cannot be empty")
        return v


class EnrollmentResponse(BaseModel):
    id: UUID
    teacher_id: UUID
    teacher_name: str
    teacher_avatar_url: Optional[str] = None
    teacher_is_verified: bool
    subject: str
    is_active: bool
    enrolled_at: datetime


# ── Batches ───────────────────────────────────────────────────────────────────

class BatchResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    teacher_id: UUID
    teacher_name: str
    subject: Optional[str] = None
    is_active: bool
    joined_at: datetime


# ── Generic ───────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str