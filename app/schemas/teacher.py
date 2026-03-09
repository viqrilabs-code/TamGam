# app/schemas/teacher.py
# Pydantic request/response models for teacher profile endpoints

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# â”€â”€ Public Author Mark â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class UserMark(BaseModel):
    """Minimal user identity with marks -- used wherever author info is shown."""
    id: UUID
    full_name: str
    avatar_url: Optional[str] = None
    role: str
    is_subscribed: bool
    is_verified_teacher: bool


class TeacherEducationItem(BaseModel):
    level: str  # 10th | 12th | bachelors | masters | other
    institution: Optional[str] = None
    board_or_university: Optional[str] = None
    specialization: Optional[str] = None
    year_of_completion: Optional[int] = None
    marks_obtained: Optional[float] = None
    total_marks: Optional[float] = None
    score_percent: Optional[float] = None
    grade: Optional[str] = None
    achievements: List[str] = Field(default_factory=list)

    @field_validator("level")
    @classmethod
    def valid_level(cls, v: str) -> str:
        value = (v or "").strip().lower()
        if value not in {"10th", "12th", "bachelors", "masters", "other"}:
            raise ValueError("Education level must be one of: 10th, 12th, bachelors, masters, other")
        return value

    @field_validator("score_percent")
    @classmethod
    def valid_score_percent(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and (v < 0 or v > 100):
            raise ValueError("score_percent must be between 0 and 100")
        return v

    @field_validator("achievements")
    @classmethod
    def clean_achievements(cls, v: List[str]) -> List[str]:
        return [a.strip() for a in v if isinstance(a, str) and a.strip()]


class TeacherPastJobExperienceItem(BaseModel):
    organization: str
    role_title: str
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    currently_working: bool = False
    description: Optional[str] = None
    achievements: List[str] = Field(default_factory=list)

    @field_validator("organization", "role_title")
    @classmethod
    def required_non_empty(cls, v: str) -> str:
        value = (v or "").strip()
        if not value:
            raise ValueError("This field is required")
        return value

    @field_validator("achievements")
    @classmethod
    def clean_job_achievements(cls, v: List[str]) -> List[str]:
        return [a.strip() for a in v if isinstance(a, str) and a.strip()]


class TeacherPortfolioHighlights(BaseModel):
    most_promising_aspect: Optional[str] = None
    marketable_achievements: List[str] = Field(default_factory=list)
    education_achievements: List[str] = Field(default_factory=list)
    experience_achievements: List[str] = Field(default_factory=list)

# â”€â”€ Teacher Profile â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    school_name: Optional[str] = None
    preferred_language: Optional[str] = None
    teaching_style: Optional[str] = None
    focus_grades: Optional[List[str]] = None
    focus_boards: Optional[List[str]] = None
    class_note_tone: Optional[str] = None
    class_note_preferences: Optional[str] = None
    education: List[TeacherEducationItem] = Field(default_factory=list)
    past_job_experiences: List[TeacherPastJobExperienceItem] = Field(default_factory=list)
    portfolio_highlights: TeacherPortfolioHighlights = Field(default_factory=TeacherPortfolioHighlights)
    is_verified: bool
    total_students: int
    total_classes: int
    average_rating: Optional[float] = None
    rating_count: int = 0
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
    school_name: Optional[str] = None
    preferred_language: Optional[str] = None
    teaching_style: Optional[str] = None
    focus_grades: Optional[List[str]] = None
    focus_boards: Optional[List[str]] = None
    class_note_tone: Optional[str] = None
    class_note_preferences: Optional[str] = None
    education: Optional[List[TeacherEducationItem]] = None
    past_job_experiences: Optional[List[TeacherPastJobExperienceItem]] = None
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

    @field_validator("teaching_style")
    @classmethod
    def valid_teaching_style(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in {"lecture", "interactive", "practical", "mixed"}:
            raise ValueError("Teaching style must be one of: lecture, interactive, practical, mixed")
        return v

    @field_validator("class_note_tone")
    @classmethod
    def valid_class_note_tone(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in {"concise", "detailed", "exam_focused", "conceptual"}:
            raise ValueError("Class note tone must be one of: concise, detailed, exam_focused, conceptual")
        return v

    @field_validator("subjects")
    @classmethod
    def subjects_not_empty(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            v = [s.strip() for s in v if s.strip()]
        return v


class TeacherBatchListItem(BaseModel):
    id: UUID
    name: str
    subject: Optional[str] = None
    description: Optional[str] = None
    grade_level: Optional[int] = None
    class_timing: Optional[str] = None
    class_days: List[str] = Field(default_factory=list)
    max_students: Optional[int] = None
    member_count: int = 0
    seats_left: Optional[int] = None
    fee_paise: int
    fee_rupees: float


class TeacherListItem(BaseModel):
    """Compact teacher card for listing/discovery."""
    id: UUID
    user_id: UUID
    full_name: str
    school_or_institution: Optional[str] = None
    school_name: Optional[str] = None
    avatar_url: Optional[str] = None
    subjects: Optional[List[str]] = None
    experience_years: Optional[int] = None
    is_verified: bool
    total_students: int
    average_rating: Optional[float] = None
    rating_count: int = 0
    upcoming_class_times: Optional[List[datetime]] = None
    available_batches: Optional[List[TeacherBatchListItem]] = None


# â”€â”€ Verification â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class VerificationDocumentResponse(BaseModel):
    id: UUID
    document_type: str
    original_filename: Optional[str] = None
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    uploaded_at: datetime
    # No gcs_path in response -- never expose raw GCS paths to client


class StudentVerificationRequestItem(BaseModel):
    id: UUID
    student_id: UUID
    student_name: str
    student_grade: Optional[int] = None
    status: str
    requested_at: datetime
    responded_at: Optional[datetime] = None


class VerificationStatusResponse(BaseModel):
    """Current verification status for the teacher."""
    has_submitted: bool
    status: Optional[str] = None           # pending | approved | rejected | None
    submitted_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    documents: List[VerificationDocumentResponse] = Field(default_factory=list)
    verification_mode: str = "student"
    required_verifications: int = 3
    verified_count: int = 0
    pending_count: int = 0
    can_request_more: bool = True
    requests: List[StudentVerificationRequestItem] = Field(default_factory=list)


class VerificationRequestCreate(BaseModel):
    student_ids: List[UUID] = Field(default_factory=list, min_length=1, max_length=3)


class VerificationStudentCandidate(BaseModel):
    student_id: UUID
    full_name: str
    email: str
    grade: Optional[int] = None


# â”€â”€ Earnings â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class EarningsResponse(BaseModel):
    """Teacher earnings breakdown."""
    total_revenue_paise: int
    platform_commission_paise: int
    net_earnings_paise: int
    current_commission_rate_percent: float  # 20 | 15 | 10
    total_revenue_rupees: float             # Convenience field
    net_earnings_rupees: float
    # UI compatibility fields (legacy cards expect these keys)
    this_month_paise: int = 0
    last_month_paise: int = 0
    total_paise: int = 0
    platform_monthly_fee_paise: int = 0


class TeacherPayoutItem(BaseModel):
    id: UUID
    period_start: datetime
    period_end: datetime
    net_amount_paise: int
    net_amount_rupees: float
    status: str
    razorpay_payout_id: Optional[str] = None
    razorpay_status: Optional[str] = None
    failure_reason: Optional[str] = None
    created_at: datetime
    processed_at: Optional[datetime] = None


# â”€â”€ Top Performers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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


# â”€â”€ Generic â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class MessageResponse(BaseModel):
    message: str


