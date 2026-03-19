import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class GroupStudy(Base):
    __tablename__ = "group_studies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    creator_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    creator_role = Column(String(20), nullable=False)
    teacher_id = Column(
        UUID(as_uuid=True),
        ForeignKey("teacher_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    batch_id = Column(
        UUID(as_uuid=True),
        ForeignKey("batches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    winner_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    title = Column(String(255), nullable=False)
    subject = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    document_name = Column(String(255), nullable=True)
    document_text = Column(Text, nullable=True)
    sections_payload = Column(JSONB, nullable=False, default=list)
    group_discussion_enabled = Column(Boolean, nullable=False, default=False)

    scheduled_at = Column(DateTime(timezone=True), nullable=False, index=True)
    duration_minutes = Column(Integer, nullable=False, default=60)
    status = Column(String(20), nullable=False, default="scheduled", index=True)
    stop_reason = Column(String(255), nullable=True)
    stop_request_reason = Column(String(255), nullable=True)
    stop_requester_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    stop_requested_at = Column(DateTime(timezone=True), nullable=True)
    stop_approvals_payload = Column(JSONB, nullable=False, default=list)
    report_payload = Column(JSONB, nullable=True)

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
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    batch = relationship("Batch")
    participant_rows = relationship(
        "GroupStudyParticipant",
        back_populates="group_study",
        cascade="all, delete-orphan",
        order_by="GroupStudyParticipant.created_at.asc()",
    )
    turns = relationship(
        "GroupStudyTurn",
        back_populates="group_study",
        cascade="all, delete-orphan",
        order_by="GroupStudyTurn.turn_index.asc()",
    )


class GroupStudyParticipant(Base):
    __tablename__ = "group_study_participants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_study_id = Column(
        UUID(as_uuid=True),
        ForeignKey("group_studies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    student_id = Column(
        UUID(as_uuid=True),
        ForeignKey("student_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    invited_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    role = Column(String(20), nullable=False, default="participant")
    invite_source = Column(String(20), nullable=False, default="search")
    status = Column(String(20), nullable=False, default="invited", index=True)
    gemini_api_key_encrypted = Column(Text, nullable=True)
    gemini_key_submitted_at = Column(DateTime(timezone=True), nullable=True)
    joined_at = Column(DateTime(timezone=True), nullable=True)

    total_score = Column(Float, nullable=False, default=0.0)
    total_questions = Column(Integer, nullable=False, default=0)
    correct_answers = Column(Integer, nullable=False, default=0)
    participation_count = Column(Integer, nullable=False, default=0)
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

    group_study = relationship("GroupStudy", back_populates="participant_rows")
    user = relationship("User", foreign_keys=[user_id])
    student = relationship("StudentProfile")


class GroupStudyTurn(Base):
    __tablename__ = "group_study_turns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_study_id = Column(
        UUID(as_uuid=True),
        ForeignKey("group_studies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    turn_index = Column(Integer, nullable=False)
    section_index = Column(Integer, nullable=False, default=0)
    turn_type = Column(String(30), nullable=False)
    section_title = Column(String(255), nullable=True)
    target_name = Column(String(255), nullable=True)
    prompt_text = Column(Text, nullable=False)
    question_text = Column(Text, nullable=True)
    source_excerpt = Column(Text, nullable=True)
    prompt_payload = Column(JSONB, nullable=True)
    correct_answer = Column(String(20), nullable=True)

    answer_text = Column(Text, nullable=True)
    answer_choice = Column(String(20), nullable=True)
    evaluation_data = Column(JSONB, nullable=True)
    score_awarded = Column(Float, nullable=True)
    is_correct = Column(Boolean, nullable=True)
    status = Column(String(20), nullable=False, default="pending", index=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    answered_at = Column(DateTime(timezone=True), nullable=True)

    group_study = relationship("GroupStudy", back_populates="turns")
