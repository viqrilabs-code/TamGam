# app/schemas/class_.py
# Pydantic request/response models for class and attendance endpoints

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


# ── Class ─────────────────────────────────────────────────────────────────────

class ClassCreate(BaseModel):
    title: str
    subject: str
    description: Optional[str] = None
    scheduled_at: datetime
    duration_minutes: int = 60
    batch_id: Optional[UUID] = None   # Optional -- can be for all enrolled students

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Title cannot be empty")
        return v

    @field_validator("duration_minutes")
    @classmethod
    def valid_duration(cls, v: int) -> int:
        if v < 15 or v > 480:
            raise ValueError("Duration must be between 15 and 480 minutes")
        return v


class ClassUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    meet_link: Optional[str] = None
    status: Optional[str] = None   # scheduled | live | completed | cancelled


class ClassResponse(BaseModel):
    id: UUID
    title: str
    subject: str
    description: Optional[str] = None
    teacher_id: UUID
    teacher_name: str
    teacher_avatar_url: Optional[str] = None
    teacher_is_verified: bool
    batch_id: Optional[UUID] = None
    scheduled_at: datetime
    duration_minutes: int
    status: str
    # Meet link -- only included if student has active subscription
    # None for unsubscribed students, actual link for subscribed
    meet_link: Optional[str] = None
    meet_link_gated: bool   # True means link exists but was hidden due to no subscription
    transcript_status: Optional[str] = None
    notes_status: Optional[str] = None
    created_at: datetime


class ClassListResponse(BaseModel):
    classes: List[ClassResponse]
    total: int


# ── Attendance ────────────────────────────────────────────────────────────────

class AttendanceMarkRequest(BaseModel):
    """Student marks their own attendance for a class."""
    pass   # No body needed -- just hitting the endpoint is enough


class AttendanceResponse(BaseModel):
    id: UUID
    class_id: UUID
    student_id: UUID
    student_name: str
    student_avatar_url: Optional[str] = None
    joined_at: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    marked_by: str   # student | teacher | system


class AttendanceListResponse(BaseModel):
    class_id: UUID
    total_enrolled: int
    total_present: int
    attendance: List[AttendanceResponse]


# ── Generic ───────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str