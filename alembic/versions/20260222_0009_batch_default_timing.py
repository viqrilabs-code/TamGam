"""add default timing to batches

Revision ID: 20260222_0009
Revises: 20260222_0008
Create Date: 2026-02-22
"""

from alembic import op
import sqlalchemy as sa


revision = "20260222_0009"
down_revision = "20260222_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("batches", sa.Column("default_timing", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("batches", "default_timing")
