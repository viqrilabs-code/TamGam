"""add student_note_requests table

Revision ID: 20260221_0005
Revises: 20260221_0004
Create Date: 2026-02-21
"""

from alembic import op
import sqlalchemy as sa


revision = "20260221_0005"
down_revision = "20260221_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_note_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("standard", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=100), nullable=False),
        sa.Column("chapter", sa.String(length=255), nullable=False),
        sa.Column("chapter_uploaded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("understanding_level", sa.Integer(), nullable=True),
        sa.Column("weak_sections", sa.ARRAY(sa.String()), nullable=True),
        sa.Column("exam_file_name", sa.String(length=255), nullable=True),
        sa.Column("exam_file_mime", sa.String(length=120), nullable=True),
        sa.Column("exam_file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("exam_file_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("generation_status", sa.String(length=30), nullable=False, server_default="completed"),
        sa.Column("generation_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_allowed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["student_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_student_note_requests_student_id"), "student_note_requests", ["student_id"], unique=False)
    op.create_index(op.f("ix_student_note_requests_subject"), "student_note_requests", ["subject"], unique=False)
    op.create_index(op.f("ix_student_note_requests_chapter"), "student_note_requests", ["chapter"], unique=False)
    op.create_index(op.f("ix_student_note_requests_created_at"), "student_note_requests", ["created_at"], unique=False)
    op.create_index(op.f("ix_student_note_requests_next_allowed_at"), "student_note_requests", ["next_allowed_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_student_note_requests_next_allowed_at"), table_name="student_note_requests")
    op.drop_index(op.f("ix_student_note_requests_created_at"), table_name="student_note_requests")
    op.drop_index(op.f("ix_student_note_requests_chapter"), table_name="student_note_requests")
    op.drop_index(op.f("ix_student_note_requests_subject"), table_name="student_note_requests")
    op.drop_index(op.f("ix_student_note_requests_student_id"), table_name="student_note_requests")
    op.drop_table("student_note_requests")
