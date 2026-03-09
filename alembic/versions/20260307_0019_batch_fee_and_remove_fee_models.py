"""add batch fee and remove teacher fee models

Revision ID: 20260307_0019
Revises: 20260307_0018
Create Date: 2026-03-07 22:15:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260307_0019"
down_revision = "20260307_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "batches" in table_names:
        cols = {col["name"] for col in inspector.get_columns("batches")}
        if "fee_paise" not in cols:
            op.add_column(
                "batches",
                sa.Column("fee_paise", sa.Integer(), nullable=False, server_default="0"),
            )

    if "teacher_fee_models" in table_names:
        indexes = {idx["name"] for idx in inspector.get_indexes("teacher_fee_models")}
        if "ix_teacher_fee_models_is_active" in indexes:
            op.drop_index("ix_teacher_fee_models_is_active", table_name="teacher_fee_models")
        if "ix_teacher_fee_models_teacher_id" in indexes:
            op.drop_index("ix_teacher_fee_models_teacher_id", table_name="teacher_fee_models")
        op.drop_table("teacher_fee_models")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "teacher_fee_models" not in table_names:
        op.create_table(
            "teacher_fee_models",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("teacher_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("title", sa.String(length=120), nullable=False),
            sa.Column("subjects", postgresql.ARRAY(sa.String()), nullable=False),
            sa.Column("tuition_frequency_per_week", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("monthly_fee_paise", sa.Integer(), nullable=False),
            sa.Column("assessment_discount_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("assessment_min_score", sa.Integer(), nullable=True),
            sa.Column("discount_percent", sa.Integer(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["teacher_id"], ["teacher_profiles.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_teacher_fee_models_teacher_id", "teacher_fee_models", ["teacher_id"], unique=False)
        op.create_index("ix_teacher_fee_models_is_active", "teacher_fee_models", ["is_active"], unique=False)

    if "batches" in table_names:
        cols = {col["name"] for col in inspector.get_columns("batches")}
        if "fee_paise" in cols:
            op.drop_column("batches", "fee_paise")
