# app/models/book.py
# Books uploaded by admin for RAG knowledge base.
# Chunks embedded into content_embeddings with source_type = 'book_chunk'.

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class Book(Base):
    __tablename__ = "books"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Metadata ──────────────────────────────────────────────────────────────
    title       = Column(String(500), nullable=False)
    author      = Column(String(255), nullable=True)
    subject     = Column(String(100), nullable=True, index=True)  # e.g. "Mathematics"
    description = Column(Text, nullable=True)
    filename    = Column(String(500), nullable=False)             # Original upload filename
    gcs_path    = Column(String(1000), nullable=True)             # GCS object path
    file_size   = Column(Integer, nullable=True)                  # Bytes
    page_count  = Column(Integer, nullable=True)

    # ── Embedding Status ──────────────────────────────────────────────────────
    embed_status = Column(
        Enum("pending", "processing", "completed", "failed", name="book_embed_status_enum"),
        nullable=False,
        default="pending",
        index=True,
    )
    chunk_count  = Column(Integer, nullable=True)    # How many chunks were embedded
    embed_error  = Column(Text, nullable=True)       # Error message if failed
    embedded_at  = Column(DateTime(timezone=True), nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
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