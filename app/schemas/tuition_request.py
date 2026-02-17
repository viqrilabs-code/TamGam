# app/schemas/tuition_request.py
# Pydantic request/response models for tuition request endpoints

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


# ── Requests (input) ──────────────────────────────────────────────────────────

class TuitionRequestCreate(BaseModel):
    """Student sends this to request tuition from a teacher."""
    teacher_id: UUID
    subject: str
    message: Optional[str] = None    # Optional intro from student

    @field_validator("subject")
    @classmethod
    def subject_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Subject cannot be empty.")
        return v.strip()


class TuitionRequestDecline(BaseModel):
    """Teacher provides a reason when declining."""
    decline_reason: Optional[str] = None


# ── Responses (output) ────────────────────────────────────────────────────────

class TuitionRequestResponse(BaseModel):
    """Full tuition request detail."""
    id: UUID
    status: str                           # pending | accepted | declined | cancelled

    # Student info
    student_id: UUID
    student_name: str
    student_avatar_url: Optional[str] = None
    student_grade: Optional[int] = None

    # Teacher info
    teacher_id: UUID
    teacher_name: str
    teacher_avatar_url: Optional[str] = None
    teacher_is_verified: bool

    # Request details
    subject: str
    message: Optional[str] = None
    decline_reason: Optional[str] = None
    enrollment_id: Optional[UUID] = None

    # Timestamps
    created_at: datetime
    responded_at: Optional[datetime] = None


class TuitionRequestListItem(BaseModel):
    """Compact item for list views."""
    id: UUID
    status: str
    subject: str
    message: Optional[str] = None
    decline_reason: Optional[str] = None
    enrollment_id: Optional[UUID] = None
    created_at: datetime
    responded_at: Optional[datetime] = None

    # Counterparty (who you're seeing the request with)
    counterparty_id: UUID
    counterparty_name: str
    counterparty_avatar_url: Optional[str] = None
    counterparty_is_verified: Optional[bool] = None   # only for teachers
    counterparty_grade: Optional[int] = None          # only for students


# ── Student Search (for teachers) ────────────────────────────────────────────

class StudentSearchItem(BaseModel):
    """Student card shown to teachers when searching for students."""
    student_id: UUID
    user_id: UUID
    full_name: str
    avatar_url: Optional[str] = None
    grade: Optional[int] = None
    city: Optional[str] = None
    state: Optional[str] = None
    performance_score: float
    badges: Optional[List[str]] = None
    streak_days: int
    is_subscribed: bool                   # Only subscribed students can be enrolled
    already_enrolled: bool                # Already enrolled with this teacher


class MessageResponse(BaseModel):
    message: str