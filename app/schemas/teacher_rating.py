# app/schemas/teacher_rating.py
# Pydantic models for student -> teacher ratings.

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class TeacherRatingUpsertRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = None


class TeacherRatingResponse(BaseModel):
    id: UUID
    teacher_id: UUID
    student_id: UUID
    rating: int
    comment: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class TeacherRatingEligibilityResponse(BaseModel):
    teacher_id: UUID
    is_enrolled: bool
    can_rate: bool
    eligible_from: Optional[datetime] = None
    days_remaining: int = 0
    reason: Optional[str] = None
    existing_rating: Optional[TeacherRatingResponse] = None


class TeacherRatingPublicItem(BaseModel):
    rating: int
    comment: Optional[str] = None
    student_name: str
    created_at: datetime
    updated_at: datetime


class TeacherRatingSummaryResponse(BaseModel):
    teacher_id: UUID
    average_rating: Optional[float] = None
    rating_count: int = 0
    ratings: List[TeacherRatingPublicItem] = Field(default_factory=list)

