import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class StudentNoteRequest(Base):
    __tablename__ = "student_note_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(
        UUID(as_uuid=True),
        ForeignKey("student_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    standard = Column(Integer, nullable=False)
    subject = Column(String(100), nullable=False, index=True)
    chapter = Column(String(255), nullable=False, index=True)

    chapter_uploaded = Column(Integer, nullable=False, default=0)
    understanding_level = Column(Integer, nullable=True)
    weak_sections = Column(ARRAY(String), nullable=True)

    exam_file_name = Column(String(255), nullable=True)
    exam_file_mime = Column(String(120), nullable=True)
    exam_file_size_bytes = Column(Integer, nullable=True)
    exam_file_bytes = Column(LargeBinary, nullable=True)

    generation_status = Column(String(30), nullable=False, default="completed")
    generation_error = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    next_allowed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc) + timedelta(days=7),
        index=True,
    )

    student = relationship("StudentProfile")
