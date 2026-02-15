# app/models/class_.py
# Scheduled Google Meet sessions and per-student attendance records

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class Class(Base):
    """
    A scheduled Google Meet live class session.

    CRITICAL — Meet link gating:
        The meet_link column stores the raw link.
        The API serialiser returns meet_link=None for unsubscribed users.
        This model does NOT enforce access — the endpoint layer does.
    """
    __tablename__ = "classes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    teacher_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teacher_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    batch_id = Column(
        UUID(as_uuid=True),
        ForeignKey("batches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Class Details ──────────────────────────────────────────────────────────
    title = Column(String(255), nullable=False)
    subject = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    grade_level = Column(Integer, nullable=True)              # Target grade (5–10)

    # ── Schedule ──────────────────────────────────────────────────────────────
    scheduled_at = Column(DateTime(timezone=True), nullable=False, index=True)
    duration_minutes = Column(Integer, nullable=False, default=60)

    # ── Google Meet ───────────────────────────────────────────────────────────
    # Raw link — NEVER returned to unsubscribed users in API responses
    meet_link = Column(String(512), nullable=True)
    meet_event_id = Column(String(255), nullable=True)        # Google Calendar event ID

    # ── Status ────────────────────────────────────────────────────────────────
    status = Column(
        Enum(
            "scheduled",   # Not yet started
            "live",        # Teacher marked as started
            "completed",   # Teacher marked as ended → triggers transcript pipeline
            "cancelled",   # Teacher cancelled
            name="class_status_enum",
        ),
        nullable=False,
        default="scheduled",
        index=True,
    )

    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    # ── Content Pipeline Flags ─────────────────────────────────────────────────
    # Track whether async jobs have been triggered / completed
    transcript_processed = Column(Boolean, nullable=False, default=False)
    notes_generated = Column(Boolean, nullable=False, default=False)
    assessment_generated = Column(Boolean, nullable=False, default=False)

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
    teacher = relationship("TeacherProfile", back_populates="classes")
    batch = relationship("Batch", back_populates="classes")
    attendances = relationship(
        "Attendance", back_populates="class_", cascade="all, delete-orphan"
    )
    transcript = relationship(
        "Transcript", back_populates="class_", uselist=False, cascade="all, delete-orphan"
    )
    note = relationship(
        "Note", back_populates="class_", uselist=False, cascade="all, delete-orphan"
    )
    assessments = relationship("StudentAssessment", back_populates="class_")

    def __repr__(self) -> str:
        return f"<Class title={self.title!r} subject={self.subject} status={self.status}>"


class Attendance(Base):
    """
    Per-student attendance record for each class.
    Created when a subscribed student joins the Google Meet link.
    Can also be manually marked by the teacher.
    """
    __tablename__ = "attendances"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    class_id = Column(
        UUID(as_uuid=True),
        ForeignKey("classes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_id = Column(
        UUID(as_uuid=True),
        ForeignKey("student_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    attended = Column(Boolean, nullable=False, default=False)
    joined_at = Column(DateTime(timezone=True), nullable=True)
    left_at = Column(DateTime(timezone=True), nullable=True)
    duration_minutes = Column(Integer, nullable=True)         # Time in class

    # ── Source ────────────────────────────────────────────────────────────────
    marked_by = Column(
        Enum("system", "teacher", name="attendance_source_enum"),
        nullable=False,
        default="system",
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    class_ = relationship("Class", back_populates="attendances")
    student = relationship("StudentProfile", back_populates="attendances")

    def __repr__(self) -> str:
        return f"<Attendance class={self.class_id} student={self.student_id} attended={self.attended}>"