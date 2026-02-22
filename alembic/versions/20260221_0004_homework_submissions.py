"""add homework_submissions table

Revision ID: 20260221_0004
Revises: 20260221_0003
Create Date: 2026-02-21
"""

from alembic import op
import sqlalchemy as sa


revision = "20260221_0004"
down_revision = "20260221_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "homework_submissions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("homework_id", sa.UUID(), nullable=False),
        sa.Column("student_id", sa.UUID(), nullable=False),
        sa.Column("submission_text", sa.Text(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("file_mime", sa.String(length=100), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("file_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("feedback_text", sa.Text(), nullable=True),
        sa.Column("feedback_score", sa.Integer(), nullable=True),
        sa.Column("feedback_given_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["homework_id"], ["homeworks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["student_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("homework_id", "student_id", name="uq_homework_submission_student"),
    )
    op.create_index(op.f("ix_homework_submissions_homework_id"), "homework_submissions", ["homework_id"], unique=False)
    op.create_index(op.f("ix_homework_submissions_student_id"), "homework_submissions", ["student_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_homework_submissions_student_id"), table_name="homework_submissions")
    op.drop_index(op.f("ix_homework_submissions_homework_id"), table_name="homework_submissions")
    op.drop_table("homework_submissions")
