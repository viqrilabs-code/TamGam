"""add subscription cancel flag and past_due enum value

Revision ID: 20260221_0007
Revises: 20260221_0006
Create Date: 2026-02-21
"""

from alembic import op
import sqlalchemy as sa


revision = "20260221_0007"
down_revision = "20260221_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE subscription_status_enum ADD VALUE IF NOT EXISTS 'past_due'")
    op.add_column(
        "subscriptions",
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.alter_column("subscriptions", "cancel_at_period_end", server_default=None)


def downgrade() -> None:
    op.drop_column("subscriptions", "cancel_at_period_end")
