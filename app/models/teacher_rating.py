# app/models/teacher_rating.py
# Student-to-teacher ratings (post-enrollment trust signal).

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.db.base_class import Base


class TeacherRating(Base):
    """
    One rating per (student, teacher).
    Student can update their rating later; latest value is used in average rating.
    """

    __tablename__ = "teacher_ratings"
    __table_args__ = (
        UniqueConstraint("teacher_id", "student_id", name="uq_teacher_ratings_teacher_student"),
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_teacher_ratings_rating_range"),
    )

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
        index=True,
    )
    rating = Column(Integer, nullable=False)
    comment = Column(Text, nullable=True)
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

    def __repr__(self) -> str:
        return f"<TeacherRating teacher={self.teacher_id} student={self.student_id} rating={self.rating}>"

