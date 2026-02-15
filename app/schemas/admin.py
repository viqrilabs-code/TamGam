# app/schemas/admin.py

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel


# ── Stats ─────────────────────────────────────────────────────────────────────

class PlatformStats(BaseModel):
    total_users: int
    total_students: int
    total_teachers: int
    verified_teachers: int
    pending_verifications: int
    active_subscriptions: int
    total_revenue_paise: int
    total_revenue_rupees: float
    total_classes: int
    total_transcripts: int
    total_notes: int


# ── Teacher Verification ──────────────────────────────────────────────────────

class PendingVerificationItem(BaseModel):
    teacher_id: UUID
    user_id: UUID
    full_name: str
    email: str
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    subjects: Optional[List[str]] = None
    qualifications: Optional[str] = None
    experience_years: Optional[int] = None
    verification_id: UUID
    submitted_at: datetime
    document_count: int


class VerifyTeacherRequest(BaseModel):
    approved: bool
    rejection_reason: Optional[str] = None
    admin_notes: Optional[str] = None


class VerifyTeacherResponse(BaseModel):
    teacher_id: UUID
    approved: bool
    message: str


# ── User Management ───────────────────────────────────────────────────────────

class AdminUserItem(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    is_email_verified: bool
    auth_provider: str
    is_subscribed: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None


class UserStatusUpdate(BaseModel):
    is_active: bool


# ── Subscriptions ─────────────────────────────────────────────────────────────

class AdminSubscriptionItem(BaseModel):
    id: UUID
    user_id: UUID
    user_email: str
    user_name: str
    plan_name: str
    billing_cycle: str
    status: str
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool
    created_at: datetime


# ── Generic ───────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str