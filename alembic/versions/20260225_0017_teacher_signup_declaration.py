"""add teacher signup payout declaration fields

Revision ID: 20260225_0017
Revises: 20260225_0016
Create Date: 2026-02-25 22:05:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260225_0017"
down_revision = "20260225_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "teacher_payout_declaration_accepted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "users",
        sa.Column("teacher_payout_declaration_accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("teacher_payout_declaration_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("teacher_payout_declaration_ip", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "teacher_payout_declaration_ip")
    op.drop_column("users", "teacher_payout_declaration_version")
    op.drop_column("users", "teacher_payout_declaration_accepted_at")
    op.drop_column("users", "teacher_payout_declaration_accepted")
