# app/models/homework.py
# Teacher-created homework attached to classes, with optional small file upload.

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class Homework(Base):
    __tablename__ = "homeworks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    class_id = Column(
        UUID(as_uuid=True),
        ForeignKey("classes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    teacher_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teacher_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_student_id = Column(
        UUID(as_uuid=True),
        ForeignKey("student_profiles.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    kind = Column(String(32), nullable=False, default="assignment", index=True)
    generated_by_diya = Column(Boolean, nullable=False, default=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    due_at = Column(DateTime(timezone=True), nullable=True)

    file_name = Column(String(255), nullable=True)
    file_mime = Column(String(100), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    file_bytes = Column(LargeBinary, nullable=True)

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

    class_ = relationship("Class")
    teacher = relationship("TeacherProfile")
    target_student = relationship("StudentProfile")
    submissions = relationship("HomeworkSubmission", back_populates="homework", cascade="all, delete-orphan")


class HomeworkSubmission(Base):
    __tablename__ = "homework_submissions"
    __table_args__ = (
        UniqueConstraint("homework_id", "student_id", name="uq_homework_submission_student"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    homework_id = Column(
        UUID(as_uuid=True),
        ForeignKey("homeworks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_id = Column(
        UUID(as_uuid=True),
        ForeignKey("student_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    submission_text = Column(Text, nullable=True)
    file_name = Column(String(255), nullable=True)
    file_mime = Column(String(100), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    file_bytes = Column(LargeBinary, nullable=True)

    feedback_text = Column(Text, nullable=True)
    feedback_score = Column(Integer, nullable=True)
    feedback_given_at = Column(DateTime(timezone=True), nullable=True)

    submitted_at = Column(
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

    homework = relationship("Homework", back_populates="submissions")
    student = relationship("StudentProfile")
