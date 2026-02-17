# app/models/tuition_request.py
# Tuition request flow: Student → Teacher → Accept/Decline → Enrollment
#
# Flow:
#   1. Student searches teachers (existing GET /teachers/)
#   2. Student sends request → POST /tuition-requests/
#   3. Teacher sees incoming requests → GET /tuition-requests/incoming
#   4. Teacher accepts → enrollment auto-created, student notified
#   5. Teacher declines with reason → student notified
#
# Teacher search for students:
#   GET /students/search?grade=8&subject=Mathematics&city=Delhi

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


class TuitionRequest(Base):
    """
    A student's request to a teacher for tuition.
    Status lifecycle: pending → accepted | declined | cancelled
    On accept: Enrollment is auto-created by the endpoint.
    On decline: decline_reason is stored and student is notified.
    """
    __tablename__ = "tuition_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Parties ───────────────────────────────────────────────────────────────
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

    # ── Request Details ───────────────────────────────────────────────────────
    subject = Column(String(100), nullable=False)          # Subject requested
    message = Column(Text, nullable=True)                  # Optional intro message from student
    grade = Column(Integer, nullable=True)                 # Student's grade (denormalised for teacher)

    # ── Status ────────────────────────────────────────────────────────────────
    status = Column(
        Enum(
            "pending",    # Awaiting teacher response
            "accepted",   # Teacher accepted → enrollment created
            "declined",   # Teacher declined
            "cancelled",  # Student cancelled before response
            name="tuition_request_status_enum",
        ),
        nullable=False,
        default="pending",
        index=True,
    )

    # ── Teacher Response ──────────────────────────────────────────────────────
    decline_reason = Column(Text, nullable=True)           # Filled on decline
    responded_at = Column(DateTime(timezone=True), nullable=True)

    # ── Enrollment Link ───────────────────────────────────────────────────────
    # Set when teacher accepts — points to the created enrollment
    enrollment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("enrollments.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    student  = relationship("StudentProfile",  back_populates="tuition_requests")
    teacher  = relationship("TeacherProfile",  back_populates="tuition_requests")
    enrollment = relationship("Enrollment")

    def __repr__(self) -> str:
        return (
            f"<TuitionRequest student={self.student_id} "
            f"teacher={self.teacher_id} status={self.status}>"
        )