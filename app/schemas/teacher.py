# app/schemas/teacher.py
# Pydantic request/response models for teacher profile endpoints

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


# ── Public Author Mark ────────────────────────────────────────────────────────

class UserMark(BaseModel):
    """Minimal user identity with marks -- used wherever author info is shown."""
    id: UUID
    full_name: str
    avatar_url: Optional[str] = None
    role: str
    is_subscribed: bool
    is_verified_teacher: bool


# ── Teacher Profile ───────────────────────────────────────────────────────────

class TeacherProfilePublic(BaseModel):
    """Public-facing teacher profile -- shown to students and anonymous visitors."""
    id: UUID
    user_id: UUID
    full_name: str           # From User table
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    subjects: Optional[List[str]] = None
    qualifications: Optional[str] = None
    experience_years: Optional[int] = None
    school_or_institution: Optional[str] = None
    is_verified: bool
    total_students: int
    total_classes: int
    average_rating: Optional[float] = None
    is_verified_teacher: bool  # resolved mark


class TeacherProfilePrivate(TeacherProfilePublic):
    """Full teacher profile -- only shown to the teacher themselves and admin."""
    bank_account_name: Optional[str] = None
    bank_account_number: Optional[str] = None   # Masked in response: ****1234
    bank_ifsc_code: Optional[str] = None
    bank_upi_id: Optional[str] = None
    razorpay_contact_id: Optional[str] = None
    razorpay_fund_account_id: Optional[str] = None
    total_revenue_paise: int
    platform_commission_paise: int
    verified_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class TeacherProfileUpdate(BaseModel):
    """Fields a teacher can update on their own profile."""
    bio: Optional[str] = None
    subjects: Optional[List[str]] = None
    qualifications: Optional[str] = None
    experience_years: Optional[int] = None
    school_or_institution: Optional[str] = None
    bank_account_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_ifsc_code: Optional[str] = None
    bank_upi_id: Optional[str] = None

    @field_validator("experience_years")
    @classmethod
    def valid_experience(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 0 or v > 60):
            raise ValueError("Experience years must be between 0 and 60")
        return v

    @field_validator("subjects")
    @classmethod
    def subjects_not_empty(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            v = [s.strip() for s in v if s.strip()]
        return v


class TeacherListItem(BaseModel):
    """Compact teacher card for listing/discovery."""
    id: UUID
    user_id: UUID
    full_name: str
    avatar_url: Optional[str] = None
    subjects: Optional[List[str]] = None
    experience_years: Optional[int] = None
    is_verified: bool
    total_students: int
    average_rating: Optional[float] = None


# ── Verification ──────────────────────────────────────────────────────────────

class VerificationDocumentResponse(BaseModel):
    id: UUID
    document_type: str
    original_filename: Optional[str] = None
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    uploaded_at: datetime
    # No gcs_path in response -- never expose raw GCS paths to client


class VerificationStatusResponse(BaseModel):
    """Current verification status for the teacher."""
    has_submitted: bool
    status: Optional[str] = None           # pending | approved | rejected | None
    submitted_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    documents: List[VerificationDocumentResponse] = []


# ── Earnings ──────────────────────────────────────────────────────────────────

class EarningsResponse(BaseModel):
    """Teacher earnings breakdown."""
    total_revenue_paise: int
    platform_commission_paise: int
    net_earnings_paise: int
    current_commission_rate_percent: float  # 20 | 15 | 10
    total_revenue_rupees: float             # Convenience field
    net_earnings_rupees: float


# ── Top Performers ────────────────────────────────────────────────────────────

class TopPerformerItem(BaseModel):
    rank: int
    student_id: UUID
    full_name: str
    avatar_url: Optional[str] = None
    performance_score: float
    computed_at: datetime


class TopPerformersResponse(BaseModel):
    teacher_id: UUID
    performers: List[TopPerformerItem] = []
    computed_at: Optional[datetime] = None


# ── Generic ───────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str