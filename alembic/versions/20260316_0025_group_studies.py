"""add group study workflow tables

Revision ID: 20260316_0025
Revises: 20260312_0024
Create Date: 2026-03-16 18:30:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260316_0025"
down_revision = "20260312_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "group_studies" not in table_names:
        op.create_table(
            "group_studies",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("creator_user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("creator_role", sa.String(length=20), nullable=False),
            sa.Column("teacher_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("winner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("subject", sa.String(length=100), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("document_name", sa.String(length=255), nullable=True),
            sa.Column("document_text", sa.Text(), nullable=True),
            sa.Column("sections_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
            sa.Column("group_discussion_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="scheduled"),
            sa.Column("stop_reason", sa.String(length=255), nullable=True),
            sa.Column("report_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["batch_id"], ["batches.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["creator_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["teacher_id"], ["teacher_profiles.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["winner_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_group_studies_creator_user_id", "group_studies", ["creator_user_id"], unique=False)
        op.create_index("ix_group_studies_teacher_id", "group_studies", ["teacher_id"], unique=False)
        op.create_index("ix_group_studies_batch_id", "group_studies", ["batch_id"], unique=False)
        op.create_index("ix_group_studies_winner_user_id", "group_studies", ["winner_user_id"], unique=False)
        op.create_index("ix_group_studies_scheduled_at", "group_studies", ["scheduled_at"], unique=False)
        op.create_index("ix_group_studies_status", "group_studies", ["status"], unique=False)

    table_names = set(inspector.get_table_names())
    if "group_study_participants" not in table_names:
        op.create_table(
            "group_study_participants",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("group_study_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("invited_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("role", sa.String(length=20), nullable=False, server_default="participant"),
            sa.Column("invite_source", sa.String(length=20), nullable=False, server_default="search"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="invited"),
            sa.Column("gemini_api_key_encrypted", sa.Text(), nullable=True),
            sa.Column("gemini_key_submitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("total_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("total_questions", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("correct_answers", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("participation_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["group_study_id"], ["group_studies.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["student_id"], ["student_profiles.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_group_study_participants_group_study_id", "group_study_participants", ["group_study_id"], unique=False)
        op.create_index("ix_group_study_participants_user_id", "group_study_participants", ["user_id"], unique=False)
        op.create_index("ix_group_study_participants_student_id", "group_study_participants", ["student_id"], unique=False)
        op.create_index("ix_group_study_participants_status", "group_study_participants", ["status"], unique=False)

    table_names = set(inspector.get_table_names())
    if "group_study_turns" not in table_names:
        op.create_table(
            "group_study_turns",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("group_study_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("turn_index", sa.Integer(), nullable=False),
            sa.Column("section_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("turn_type", sa.String(length=30), nullable=False),
            sa.Column("section_title", sa.String(length=255), nullable=True),
            sa.Column("target_name", sa.String(length=255), nullable=True),
            sa.Column("prompt_text", sa.Text(), nullable=False),
            sa.Column("question_text", sa.Text(), nullable=True),
            sa.Column("source_excerpt", sa.Text(), nullable=True),
            sa.Column("prompt_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("correct_answer", sa.String(length=20), nullable=True),
            sa.Column("answer_text", sa.Text(), nullable=True),
            sa.Column("answer_choice", sa.String(length=20), nullable=True),
            sa.Column("evaluation_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("score_awarded", sa.Float(), nullable=True),
            sa.Column("is_correct", sa.Boolean(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["group_study_id"], ["group_studies.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_group_study_turns_group_study_id", "group_study_turns", ["group_study_id"], unique=False)
        op.create_index("ix_group_study_turns_target_user_id", "group_study_turns", ["target_user_id"], unique=False)
        op.create_index("ix_group_study_turns_status", "group_study_turns", ["status"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "group_study_turns" in table_names:
        indexes = {idx["name"] for idx in inspector.get_indexes("group_study_turns")}
        for name in ("ix_group_study_turns_group_study_id", "ix_group_study_turns_target_user_id", "ix_group_study_turns_status"):
            if name in indexes:
                op.drop_index(name, table_name="group_study_turns")
        op.drop_table("group_study_turns")

    if "group_study_participants" in table_names:
        indexes = {idx["name"] for idx in inspector.get_indexes("group_study_participants")}
        for name in (
            "ix_group_study_participants_group_study_id",
            "ix_group_study_participants_user_id",
            "ix_group_study_participants_student_id",
            "ix_group_study_participants_status",
        ):
            if name in indexes:
                op.drop_index(name, table_name="group_study_participants")
        op.drop_table("group_study_participants")

    if "group_studies" in table_names:
        indexes = {idx["name"] for idx in inspector.get_indexes("group_studies")}
        for name in (
            "ix_group_studies_creator_user_id",
            "ix_group_studies_teacher_id",
            "ix_group_studies_batch_id",
            "ix_group_studies_winner_user_id",
            "ix_group_studies_scheduled_at",
            "ix_group_studies_status",
        ):
            if name in indexes:
                op.drop_index(name, table_name="group_studies")
        op.drop_table("group_studies")
