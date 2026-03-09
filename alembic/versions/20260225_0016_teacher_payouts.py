"""add teacher payouts table

Revision ID: 20260225_0016
Revises: 20260224_0015
Create Date: 2026-02-25 18:10:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260225_0016"
down_revision = "20260224_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    payout_status_enum = sa.Enum(
        "pending",
        "processing",
        "paid",
        "failed",
        name="teacher_payout_status_enum",
    )
    payout_status_enum.create(op.get_bind(), checkfirst=True)

    payout_status_enum_for_column = postgresql.ENUM(
        "pending",
        "processing",
        "paid",
        "failed",
        name="teacher_payout_status_enum",
        create_type=False,
    )

    op.create_table(
        "teacher_payouts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("teacher_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gross_revenue_paise", sa.Integer(), nullable=True),
        sa.Column("platform_commission_paise", sa.Integer(), nullable=True),
        sa.Column("net_amount_paise", sa.Integer(), nullable=False),
        sa.Column("status", payout_status_enum_for_column, nullable=False, server_default="pending"),
        sa.Column("razorpay_payout_id", sa.String(length=255), nullable=True),
        sa.Column("razorpay_status", sa.String(length=100), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["teacher_id"], ["teacher_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_teacher_payouts_teacher_id"), "teacher_payouts", ["teacher_id"], unique=False)
    op.create_index(op.f("ix_teacher_payouts_status"), "teacher_payouts", ["status"], unique=False)
    op.create_index(
        op.f("ix_teacher_payouts_razorpay_payout_id"),
        "teacher_payouts",
        ["razorpay_payout_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_teacher_payouts_razorpay_payout_id"), table_name="teacher_payouts")
    op.drop_index(op.f("ix_teacher_payouts_status"), table_name="teacher_payouts")
    op.drop_index(op.f("ix_teacher_payouts_teacher_id"), table_name="teacher_payouts")
    op.drop_table("teacher_payouts")

    payout_status_enum = sa.Enum(
        "pending",
        "processing",
        "paid",
        "failed",
        name="teacher_payout_status_enum",
    )
    payout_status_enum.drop(op.get_bind(), checkfirst=True)
