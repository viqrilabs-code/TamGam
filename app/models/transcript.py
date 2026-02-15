# app/models/transcript.py
# Raw transcript pulled from Google Drive after a class ends

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class Transcript(Base):
    """
    Raw transcript text extracted from the Google Drive .docx file.
    One transcript per class — created by the process_transcript Cloud Run Job.

    Flow:
        Class ends
        → Cloud Task enqueued
        → Job downloads .docx from Drive
        → Extracts text via python-docx
        → Saves here (status=raw)
        → Enqueues generate_notes job
    """
    __tablename__ = "transcripts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    class_id = Column(
        UUID(as_uuid=True),
        ForeignKey("classes.id", ondelete="CASCADE"),
        unique=True,                  # One transcript per class
        nullable=False,
        index=True,
    )

    # ── Content ───────────────────────────────────────────────────────────────
    raw_text = Column(Text, nullable=True)                    # Full extracted text
    word_count = Column(Integer, nullable=True)
    duration_seconds = Column(Integer, nullable=True)         # Class duration from transcript

    # ── Source ────────────────────────────────────────────────────────────────
    google_drive_file_id = Column(String(255), nullable=True)
    original_filename = Column(String(255), nullable=True)

    # ── Pipeline Status ───────────────────────────────────────────────────────
    status = Column(
        Enum(
            "pending",    # Job enqueued, not yet processed
            "raw",        # Text extracted successfully
            "failed",     # Extraction failed (Drive file not found, parse error)
            name="transcript_status_enum",
        ),
        nullable=False,
        default="pending",
        index=True,
    )
    error_message = Column(Text, nullable=True)               # If status=failed

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    processed_at = Column(DateTime(timezone=True), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    class_ = relationship("Class", back_populates="transcript")
    embeddings = relationship(
        "ContentEmbedding",
        back_populates="transcript",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Transcript class={self.class_id} status={self.status} words={self.word_count}>"