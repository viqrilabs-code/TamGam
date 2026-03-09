"""create email login codes table

Revision ID: 20260308_0020
Revises: 20260307_0019
Create Date: 2026-03-08 15:10:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260308_0020"
down_revision = "20260307_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "email_login_codes" not in table_names:
        op.create_table(
            "email_login_codes",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("code_hash", sa.String(length=255), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_email_login_codes_email", "email_login_codes", ["email"], unique=False)
        op.create_index("ix_email_login_codes_expires_at", "email_login_codes", ["expires_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "email_login_codes" in table_names:
        indexes = {idx["name"] for idx in inspector.get_indexes("email_login_codes")}
        if "ix_email_login_codes_email" in indexes:
            op.drop_index("ix_email_login_codes_email", table_name="email_login_codes")
        if "ix_email_login_codes_expires_at" in indexes:
            op.drop_index("ix_email_login_codes_expires_at", table_name="email_login_codes")
        op.drop_table("email_login_codes")
