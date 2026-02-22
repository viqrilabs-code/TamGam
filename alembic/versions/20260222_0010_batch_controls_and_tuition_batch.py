"""batch visibility controls and tuition request batch selection

Revision ID: 20260222_0010
Revises: 20260222_0009
Create Date: 2026-02-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260222_0010"
down_revision = "20260222_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("batches", sa.Column("student_selection_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column("batches", sa.Column("max_students", sa.Integer(), nullable=True))
    op.add_column("batches", sa.Column("class_days", postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column("batches", sa.Column("cancelled_days", postgresql.ARRAY(sa.String()), nullable=True))
    op.alter_column("batches", "student_selection_enabled", server_default=None)

    op.add_column("tuition_requests", sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_tuition_requests_batch_id_batches",
        "tuition_requests",
        "batches",
        ["batch_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_tuition_requests_batch_id", "tuition_requests", ["batch_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tuition_requests_batch_id", table_name="tuition_requests")
    op.drop_constraint("fk_tuition_requests_batch_id_batches", "tuition_requests", type_="foreignkey")
    op.drop_column("tuition_requests", "batch_id")

    op.drop_column("batches", "cancelled_days")
    op.drop_column("batches", "class_days")
    op.drop_column("batches", "max_students")
    op.drop_column("batches", "student_selection_enabled")
