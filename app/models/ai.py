# app/models/ai.py
# RAG vector store and AI Tutor conversation history
# ContentEmbedding uses pgvector for cosine similarity search

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base
from pgvector.sqlalchemy import Vector


class ContentEmbedding(Base):
    """
    Chunked and embedded content for RAG (Retrieval Augmented Generation).
    Used by the AI Tutor (Diya) to ground answers in actual class content.

    Sources:
        - Transcript chunks (500 tokens, 50 token overlap)
        - Note sections (each key point + detailed section)
        - Community posts (title + body, for similar question matching)
        - NCERT textbook chapters (Class 8, 9, 10 Mathematics)

    Vector dimensions: 768 (Gemini text-embedding-004)
    Search: cosine similarity via pgvector `<=>` operator
    """
    __tablename__ = "content_embeddings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Source Reference ──────────────────────────────────────────────────────
    # At most one source FK is set per embedding.
    # ncert_book rows have all three FKs as NULL — metadata is in ncert_* columns.
    transcript_id = Column(
        UUID(as_uuid=True),
        ForeignKey("transcripts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    note_id = Column(
        UUID(as_uuid=True),
        ForeignKey("notes.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    post_id = Column(
        UUID(as_uuid=True),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # ── Class Context ─────────────────────────────────────────────────────────
    # Denormalised for fast filtering by class without joins.
    # NULL for ncert_book rows (not tied to any specific TamGam class).
    class_id = Column(
        UUID(as_uuid=True),
        ForeignKey("classes.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    book_id = Column(
    UUID(as_uuid=True),
    ForeignKey("books.id", ondelete="CASCADE"),
    nullable=True,
    index=True,
    )
    subject = Column(String(100), nullable=True, index=True)

    # ── Content ───────────────────────────────────────────────────────────────
    content_type = Column(
        Enum(
            "transcript_chunk",
            "note_section",
            "community_post",
            "ncert_book",
            name="embedding_content_type_enum",
        ),
        nullable=False,
        index=True,
    )
    chunk_text = Column(Text, nullable=False)                 # The text that was embedded
    chunk_index = Column(Integer, nullable=True)              # Position in source document
    token_count = Column(Integer, nullable=True)

    # ── NCERT Metadata ────────────────────────────────────────────────────────
    # Only populated when content_type = 'ncert_book'.
    # Enables grade-scoped RAG: search only Class 9 material for a Grade 9 student.
    ncert_grade = Column(Integer, nullable=True, index=True)          # 8 | 9 | 10
    ncert_chapter = Column(String(200), nullable=True)                # e.g. "Polynomials"
    ncert_chapter_num = Column(Integer, nullable=True)                # 1-based chapter number

    # ── Vector ────────────────────────────────────────────────────────────────
    # 768-dimensional embedding from Gemini text-embedding-004
    embedding = Column(Vector(768), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    transcript = relationship("Transcript", back_populates="embeddings")
    note = relationship("Note", back_populates="embeddings")

    def __repr__(self) -> str:
        if self.content_type == "ncert_book":
            return (
                f"<ContentEmbedding type=ncert_book "
                f"grade={self.ncert_grade} ch={self.ncert_chapter_num} "
                f"chunk={self.chunk_index}>"
            )
        return (
            f"<ContentEmbedding type={self.content_type} "
            f"class={self.class_id} chunk={self.chunk_index}>"
        )


class TutorSession(Base):
    """
    AI Tutor (Diya) conversation history per student.
    Each session is a sequence of turns stored as JSONB.
    Context passed to Gemini on each new question (stateless API + stored history).

    Turn structure in `turns` array:
    [
        {
            "role": "user",
            "content": "Can you explain integration by parts?",
            "timestamp": "2025-08-01T10:00:00Z"
        },
        {
            "role": "assistant",
            "content": "Sure! Integration by parts is...",
            "sources": [{"class_id": "...", "title": "...", "chunk": "..."}],
            "timestamp": "2025-08-01T10:00:02Z"
        }
    ]
    """
    __tablename__ = "tutor_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Context ───────────────────────────────────────────────────────────────
    # Optional: session scoped to a specific class for focused Q&A
    class_id = Column(
        UUID(as_uuid=True),
        ForeignKey("classes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    subject = Column(String(100), nullable=True)

    # ── Conversation ──────────────────────────────────────────────────────────
    turns = Column(JSONB, nullable=False, default=list)       # Array of turn objects (see above)
    turn_count = Column(Integer, nullable=False, default=0)

    # ── Student Level (at session start — for Gemini system prompt) ───────────
    student_level_at_start = Column(Integer, nullable=True)   # 1–5

    # ── Session Metadata ──────────────────────────────────────────────────────
    is_active = Column(Boolean, nullable=True, default=True)  # Can be resumed
    last_question_at = Column(DateTime(timezone=True), nullable=True)

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
    user = relationship("User", back_populates="tutor_sessions")

    def __repr__(self) -> str:
        return (
            f"<TutorSession user={self.user_id} "
            f"turns={self.turn_count} subject={self.subject}>"
        )