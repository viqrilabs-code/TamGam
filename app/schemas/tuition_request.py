# app/schemas/tuition_request.py
# Pydantic request/response models for tuition request endpoints

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# Requests (input)
class TuitionRequestCreate(BaseModel):
    """Student sends this to request tuition from a teacher."""

    teacher_id: UUID
    batch_id: Optional[UUID] = None
    subject: str
    message: Optional[str] = None

    @field_validator("subject")
    @classmethod
    def subject_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Subject cannot be empty.")
        return v.strip()


class TuitionRequestDecline(BaseModel):
    """Teacher provides a reason when declining."""

    decline_reason: Optional[str] = None


class BatchPaymentInitRequest(BaseModel):
    subject: Optional[str] = None
    message: Optional[str] = None
    callback_url: Optional[str] = None


# Responses (output)
class TuitionRequestResponse(BaseModel):
    """Full tuition request detail."""

    id: UUID
    status: str

    student_id: UUID
    student_name: str
    student_avatar_url: Optional[str] = None
    student_grade: Optional[int] = None

    teacher_id: UUID
    teacher_name: str
    teacher_avatar_url: Optional[str] = None
    teacher_is_verified: bool
    batch_id: Optional[UUID] = None
    batch_name: Optional[str] = None

    subject: str
    message: Optional[str] = None
    decline_reason: Optional[str] = None
    enrollment_id: Optional[UUID] = None

    created_at: datetime
    responded_at: Optional[datetime] = None


class TuitionRequestListItem(BaseModel):
    """Compact item for list views."""

    id: UUID
    status: str
    subject: str
    batch_id: Optional[UUID] = None
    batch_name: Optional[str] = None
    message: Optional[str] = None
    decline_reason: Optional[str] = None
    enrollment_id: Optional[UUID] = None
    created_at: datetime
    responded_at: Optional[datetime] = None

    counterparty_id: UUID
    counterparty_name: str
    counterparty_avatar_url: Optional[str] = None
    counterparty_is_verified: Optional[bool] = None
    counterparty_grade: Optional[int] = None
    counterparty_city: Optional[str] = None
    counterparty_state: Optional[str] = None
    counterparty_learning_goals: Optional[str] = None


class BatchCheckoutResponse(BaseModel):
    batch_id: UUID
    batch_name: str
    subject: Optional[str] = None
    description: Optional[str] = None
    grade_level: Optional[int] = None
    class_timing: Optional[str] = None
    class_days: List[str] = Field(default_factory=list)
    max_students: Optional[int] = None
    seats_left: Optional[int] = None
    fee_paise: int
    fee_rupees: float
    teacher_id: UUID
    teacher_name: str
    teacher_avatar_url: Optional[str] = None
    teacher_is_verified: bool
    teacher_profile_url: str


class BatchPaymentInitResponse(BaseModel):
    payment_link: str
    amount_paise: int
    amount_rupees: float
    currency: str = "INR"
    mode: str  # free | mock | razorpay
    message: str


# Student Search (for teachers)
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
    is_subscribed: bool
    already_enrolled: bool


class TeacherStudentItem(BaseModel):
    """Student list item shown to teachers for active enrollments."""

    student_id: UUID
    user_id: UUID
    full_name: str
    avatar_url: Optional[str] = None
    grade: Optional[int] = None
    city: Optional[str] = None
    state: Optional[str] = None
    is_subscribed: bool
    enrolled_subjects: List[str]
    latest_enrolled_at: Optional[datetime] = None


class MessageResponse(BaseModel):
    message: str
