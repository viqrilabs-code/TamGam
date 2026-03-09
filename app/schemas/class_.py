# app/schemas/class_.py
# Pydantic request/response models for class and attendance endpoints

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# Class
class ClassCreate(BaseModel):
    title: str
    subject: str
    description: Optional[str] = None
    meet_link: Optional[str] = None
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

    @field_validator("meet_link")
    @classmethod
    def normalize_meet_link(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        link = v.strip()
        if not link:
            return None
        if not (link.startswith("http://") or link.startswith("https://")):
            raise ValueError("Live class link must start with http:// or https://")
        return link


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
    meet_link: Optional[str] = None
    meet_link_gated: bool
    transcript_status: Optional[str] = None
    notes_status: Optional[str] = None
    created_at: datetime


class ClassListResponse(BaseModel):
    classes: List[ClassResponse]
    total: int


# Batch management
class BatchStudentResponse(BaseModel):
    student_id: UUID
    user_id: UUID
    full_name: str
    avatar_url: Optional[str] = None
    grade: Optional[int] = None
    enrolled_subjects: List[str] = Field(default_factory=list)


class BatchSummaryResponse(BaseModel):
    id: UUID
    name: str
    subject: Optional[str] = None
    class_timing: Optional[str] = None
    fee_paise: int
    fee_rupees: float
    description: Optional[str] = None
    grade_level: Optional[int] = None
    student_selection_enabled: bool = True
    max_students: Optional[int] = None
    class_days: List[str] = Field(default_factory=list)
    cancelled_days: List[str] = Field(default_factory=list)
    is_active: bool
    member_count: int
    created_at: datetime
    members: List[BatchStudentResponse]


class BatchListResponse(BaseModel):
    batches: List[BatchSummaryResponse]
    total: int


class BatchCreateRequest(BaseModel):
    name: str
    subject: Optional[str] = None
    class_timing: Optional[str] = None
    fee_paise: int = Field(gt=0)
    description: Optional[str] = None
    grade_level: Optional[int] = None
    max_students: Optional[int] = None
    class_days: List[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def batch_name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Batch name cannot be empty")
        return v

    @field_validator("grade_level")
    @classmethod
    def valid_grade_level(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v not in (5, 6, 7, 8, 9, 10):
            raise ValueError("Grade level must be one of: 5, 6, 7, 8, 9, 10")
        return v

    @field_validator("max_students")
    @classmethod
    def create_max_students_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1:
            raise ValueError("max_students must be at least 1")
        return v


class BatchAddStudentsRequest(BaseModel):
    student_ids: List[UUID]

    @field_validator("student_ids")
    @classmethod
    def student_ids_required(cls, v: List[UUID]) -> List[UUID]:
        if not v:
            raise ValueError("At least one student_id is required")
        return v


class BatchUpdateRequest(BaseModel):
    subject: Optional[str] = None
    class_timing: Optional[str] = None
    fee_paise: Optional[int] = Field(default=None, gt=0)
    description: Optional[str] = None
    student_selection_enabled: Optional[bool] = None
    max_students: Optional[int] = None
    class_days: Optional[List[str]] = None

    @field_validator("max_students")
    @classmethod
    def max_students_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1:
            raise ValueError("max_students must be at least 1")
        return v


class BatchCancelDayRequest(BaseModel):
    day: str
    note: Optional[str] = None

    @field_validator("day")
    @classmethod
    def valid_day(cls, v: str) -> str:
        day = v.strip().lower()
        valid = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
        if day not in valid:
            raise ValueError("day must be a valid weekday name")
        return day


class BatchEnrolledStudentListResponse(BaseModel):
    students: List[BatchStudentResponse]
    total: int


# Attendance
class AttendanceMarkRequest(BaseModel):
    """Student marks their own attendance for a class."""
    pass


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


class MessageResponse(BaseModel):
    message: str
