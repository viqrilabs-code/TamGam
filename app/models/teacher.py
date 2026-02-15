# app/models/teacher.py
# Teacher-specific data: public profile, verification workflow, top performers

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class TeacherProfile(Base):
    """
    Extended profile for users with role='teacher'.
    Public fields are visible to all users.
    Private fields (bank_account_*) only to the teacher + admin.
    """
    __tablename__ = "teacher_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # ── Public Profile ────────────────────────────────────────────────────────
    bio = Column(Text, nullable=True)
    subjects = Column(ARRAY(String), nullable=True)           # ["Mathematics", "Physics"]
    qualifications = Column(Text, nullable=True)
    experience_years = Column(Integer, nullable=True)
    school_or_institution = Column(String(255), nullable=True)

    # ── Verification Status ───────────────────────────────────────────────────
    # is_verified = True → 🟡T mark shown on all posts/profile
    # Set by admin after document review — NEVER set directly by teacher
    is_verified = Column(Boolean, nullable=False, default=False, index=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)

    # ── Stats (denormalised for performance) ──────────────────────────────────
    total_students = Column(Integer, nullable=False, default=0)
    total_classes = Column(Integer, nullable=False, default=0)
    average_rating = Column(Float, nullable=True)

    # ── Earnings (private — admin + teacher only) ─────────────────────────────
    total_revenue_paise = Column(Integer, nullable=False, default=0)
    platform_commission_paise = Column(Integer, nullable=False, default=0)
    # Commission tiers:
    #   0 – 50,000  → 20%
    #   50,001 – 2,00,000 → 15%
    #   2,00,001+ → 10%

    # ── Bank Account (private — admin + teacher only) ─────────────────────────
    bank_account_name = Column(String(255), nullable=True)
    bank_account_number = Column(String(50), nullable=True)    # Store encrypted in prod
    bank_ifsc_code = Column(String(20), nullable=True)
    bank_upi_id = Column(String(100), nullable=True)

    # ── Razorpay ──────────────────────────────────────────────────────────────
    razorpay_contact_id = Column(String(255), nullable=True)   # For payouts
    razorpay_fund_account_id = Column(String(255), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user = relationship("User", back_populates="teacher_profile")
    verifications = relationship(
        "TeacherVerification", back_populates="teacher", cascade="all, delete-orphan"
    )
    classes = relationship("Class", back_populates="teacher")
    top_performers = relationship(
        "TopPerformer", back_populates="teacher", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<TeacherProfile user={self.user_id} verified={self.is_verified}>"


class TeacherVerification(Base):
    """
    One verification request per teacher.
    Teacher can resubmit after rejection (new record).
    """
    __tablename__ = "teacher_verifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    teacher_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teacher_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status = Column(
        Enum(
            "pending",   # Awaiting admin review
            "approved",  # Admin approved → is_verified = True on profile
            "rejected",  # Admin rejected with reason
            name="verification_status_enum",
        ),
        nullable=False,
        default="pending",
        index=True,
    )

    # ── Admin Decision ────────────────────────────────────────────────────────
    reviewed_by_admin_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    admin_notes = Column(Text, nullable=True)

    submitted_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    teacher = relationship("TeacherProfile", back_populates="verifications")
    documents = relationship(
        "VerificationDocument", back_populates="verification", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<TeacherVerification id={self.id} status={self.status}>"


class VerificationDocument(Base):
    """
    Individual document uploaded as part of a verification request.
    Stored in GCS private bucket — accessed via time-limited signed URLs.
    """
    __tablename__ = "verification_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    verification_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teacher_verifications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    document_type = Column(
        Enum(
            "certificate",
            "id_proof",
            "degree",
            "linkedin",
            "other",
            name="document_type_enum",
        ),
        nullable=False,
    )

    # ── GCS Location ──────────────────────────────────────────────────────────
    gcs_path = Column(String(512), nullable=False)   # gs://tamgam-docs-private/...
    original_filename = Column(String(255), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    mime_type = Column(String(100), nullable=True)

    uploaded_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    verification = relationship("TeacherVerification", back_populates="documents")

    def __repr__(self) -> str:
        return f"<VerificationDocument type={self.document_type} verification={self.verification_id}>"


class TopPerformer(Base):
    """
    Cached top-performing students per teacher.
    Recomputed periodically by recompute_rankings Cloud Run Job.
    Displayed on teacher's public profile — public fields only.
    """
    __tablename__ = "top_performers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    teacher_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teacher_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_id = Column(
        UUID(as_uuid=True),
        ForeignKey("student_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )

    rank = Column(Integer, nullable=False)                   # 1 = top student
    performance_score = Column(Float, nullable=False)
    computed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    teacher = relationship("TeacherProfile", back_populates="top_performers")
    student = relationship("StudentProfile", back_populates="top_performer_entries")

    def __repr__(self) -> str:
        return f"<TopPerformer teacher={self.teacher_id} student={self.student_id} rank={self.rank}>"