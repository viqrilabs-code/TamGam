"""add grade_level to batches

Revision ID: 20260222_0008
Revises: 20260221_0007
Create Date: 2026-02-22
"""

from alembic import op
import sqlalchemy as sa


revision = "20260222_0008"
down_revision = "20260221_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("batches", sa.Column("grade_level", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("batches", "grade_level")
