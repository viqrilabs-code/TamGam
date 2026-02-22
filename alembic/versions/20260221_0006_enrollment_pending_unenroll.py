"""add pending_unenroll_at to enrollments

Revision ID: 20260221_0006
Revises: 20260221_0005
Create Date: 2026-02-21
"""

from alembic import op
import sqlalchemy as sa


revision = "20260221_0006"
down_revision = "20260221_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "enrollments",
        sa.Column("pending_unenroll_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("enrollments", "pending_unenroll_at")
