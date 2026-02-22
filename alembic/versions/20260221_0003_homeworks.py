"""add homeworks table

Revision ID: 20260221_0003
Revises: 20260220_0002
Create Date: 2026-02-21
"""

from alembic import op
import sqlalchemy as sa


revision = "20260221_0003"
down_revision = "20260220_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "homeworks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("class_id", sa.UUID(), nullable=False),
        sa.Column("teacher_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("file_mime", sa.String(length=100), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("file_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["teacher_id"], ["teacher_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_homeworks_class_id"), "homeworks", ["class_id"], unique=False)
    op.create_index(op.f("ix_homeworks_teacher_id"), "homeworks", ["teacher_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_homeworks_teacher_id"), table_name="homeworks")
    op.drop_index(op.f("ix_homeworks_class_id"), table_name="homeworks")
    op.drop_table("homeworks")
