"""teacher student verification requests for T badge

Revision ID: 20260222_0011
Revises: 20260222_0010
Create Date: 2026-02-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260222_0011"
down_revision = "20260222_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teacher_student_verification_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("teacher_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["teacher_id"], ["teacher_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["student_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_teacher_student_verification_requests_teacher_id",
        "teacher_student_verification_requests",
        ["teacher_id"],
        unique=False,
    )
    op.create_index(
        "ix_teacher_student_verification_requests_student_id",
        "teacher_student_verification_requests",
        ["student_id"],
        unique=False,
    )
    op.create_index(
        "ix_teacher_student_verification_requests_status",
        "teacher_student_verification_requests",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_teacher_student_verification_requests_status",
        table_name="teacher_student_verification_requests",
    )
    op.drop_index(
        "ix_teacher_student_verification_requests_student_id",
        table_name="teacher_student_verification_requests",
    )
    op.drop_index(
        "ix_teacher_student_verification_requests_teacher_id",
        table_name="teacher_student_verification_requests",
    )
    op.drop_table("teacher_student_verification_requests")
