"""add contact complaints table

Revision ID: 20260224_0015
Revises: 20260224_0014
Create Date: 2026-02-24 21:05:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260224_0015"
down_revision = "20260224_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    complaint_status_enum = sa.Enum(
        "open",
        "in_progress",
        "resolved",
        "closed",
        name="complaint_status_enum",
    )
    complaint_status_enum.create(op.get_bind(), checkfirst=True)

    complaint_status_enum_for_column = postgresql.ENUM(
        "open",
        "in_progress",
        "resolved",
        "closed",
        name="complaint_status_enum",
        create_type=False,
    )

    op.create_table(
        "contact_complaints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source_page", sa.String(length=255), nullable=True),
        sa.Column("status", complaint_status_enum_for_column, nullable=False, server_default="open"),
        sa.Column("admin_notes", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_contact_complaints_user_id"), "contact_complaints", ["user_id"], unique=False)
    op.create_index(op.f("ix_contact_complaints_email"), "contact_complaints", ["email"], unique=False)
    op.create_index(op.f("ix_contact_complaints_status"), "contact_complaints", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_contact_complaints_status"), table_name="contact_complaints")
    op.drop_index(op.f("ix_contact_complaints_email"), table_name="contact_complaints")
    op.drop_index(op.f("ix_contact_complaints_user_id"), table_name="contact_complaints")
    op.drop_table("contact_complaints")

    complaint_status_enum = sa.Enum(
        "open",
        "in_progress",
        "resolved",
        "closed",
        name="complaint_status_enum",
    )
    complaint_status_enum.drop(op.get_bind(), checkfirst=True)
