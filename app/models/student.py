# app/models/student.py
# Student-specific data: profile, teacher enrollments, batch groupings

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
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class StudentProfile(Base):
    """
    Extended profile for users with role='student'.
    Two-view privacy model:
      - Public:  name, avatar, performance_score, badges
      - Private: + phone, address, subscription details, payment history
    The API layer enforces this — the model stores everything.
    """
    __tablename__ = "student_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # ── Public Fields ─────────────────────────────────────────────────────────
    grade = Column(Integer, nullable=True)                    # 5–10 (school grade)
    school_name = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)

    # ── Performance (public) ──────────────────────────────────────────────────
    # Aggregate score used for teacher's top-performers list
    performance_score = Column(Float, nullable=False, default=0.0)
    badges = Column(ARRAY(String), nullable=True)             # e.g. ["top_scorer", "streak_7"]
    streak_days = Column(Integer, nullable=False, default=0)  # Consecutive days active

    # ── Private Fields ────────────────────────────────────────────────────────
    # These are NEVER returned to non-admin, non-self requesters
    date_of_birth = Column(DateTime(timezone=True), nullable=True)
    parent_name = Column(String(255), nullable=True)
    parent_phone = Column(String(20), nullable=True)
    address_line1 = Column(String(255), nullable=True)
    address_line2 = Column(String(255), nullable=True)
    pincode = Column(String(10), nullable=True)

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
    tuition_requests = relationship("TuitionRequest", back_populates="student", cascade="all, delete-orphan")
    user = relationship("User", back_populates="student_profile")
    enrollments = relationship(
        "Enrollment", back_populates="student", cascade="all, delete-orphan"
    )
    batch_memberships = relationship("BatchMember", back_populates="student")
    understanding_profiles = relationship(
        "StudentUnderstandingProfile", back_populates="student", cascade="all, delete-orphan"
    )
    assessments = relationship("StudentAssessment", back_populates="student")
    attendances = relationship("Attendance", back_populates="student")
    top_performer_entries = relationship("TopPerformer", back_populates="student")

    def __repr__(self) -> str:
        return f"<StudentProfile user={self.user_id} score={self.performance_score}>"


class Enrollment(Base):
    """
    Many-to-many: Student ↔ Teacher.
    A student subscribes to a teacher's classes.
    Created when a student with an active subscription accesses a teacher's content.
    """
    __tablename__ = "enrollments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(
        UUID(as_uuid=True),
        ForeignKey("student_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    teacher_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teacher_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    subject = Column(String(100), nullable=True)              # Which subject they enrolled for
    is_active = Column(Boolean, nullable=False, default=True)

    enrolled_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    unenrolled_at = Column(DateTime(timezone=True), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    student = relationship("StudentProfile", back_populates="enrollments")

    def __repr__(self) -> str:
        return f"<Enrollment student={self.student_id} teacher={self.teacher_id}>"


class Batch(Base):
    """
    A named group of students assigned by a teacher (e.g. "Batch A — Maths 2025").
    Used for targeted announcements and class scheduling.
    """
    __tablename__ = "batches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    teacher_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teacher_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name = Column(String(255), nullable=False)                # "Batch A — Maths 2025"
    subject = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    members = relationship(
        "BatchMember", back_populates="batch", cascade="all, delete-orphan"
    )
    classes = relationship("Class", back_populates="batch")

    def __repr__(self) -> str:
        return f"<Batch name={self.name} teacher={self.teacher_id}>"


class BatchMember(Base):
    """
    Join table: Student ↔ Batch.
    """
    __tablename__ = "batch_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id = Column(
        UUID(as_uuid=True),
        ForeignKey("batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_id = Column(
        UUID(as_uuid=True),
        ForeignKey("student_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    added_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    batch = relationship("Batch", back_populates="members")
    student = relationship("StudentProfile", back_populates="batch_memberships")

    def __repr__(self) -> str:
        return f"<BatchMember batch={self.batch_id} student={self.student_id}>"