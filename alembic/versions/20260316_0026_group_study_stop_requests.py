"""add group study stop request state

Revision ID: 20260316_0026
Revises: 20260316_0025
Create Date: 2026-03-16 23:40:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260316_0026"
down_revision = "20260316_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    if "group_studies" not in table_names:
        return

    columns = {col["name"] for col in inspector.get_columns("group_studies")}
    indexes = {idx["name"] for idx in inspector.get_indexes("group_studies")}

    if "stop_request_reason" not in columns:
        op.add_column("group_studies", sa.Column("stop_request_reason", sa.String(length=255), nullable=True))
    if "stop_requester_user_id" not in columns:
        op.add_column("group_studies", sa.Column("stop_requester_user_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            "fk_group_studies_stop_requester_user_id_users",
            "group_studies",
            "users",
            ["stop_requester_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if "stop_requested_at" not in columns:
        op.add_column("group_studies", sa.Column("stop_requested_at", sa.DateTime(timezone=True), nullable=True))
    if "stop_approvals_payload" not in columns:
        op.add_column(
            "group_studies",
            sa.Column(
                "stop_approvals_payload",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )

    if "ix_group_studies_stop_requester_user_id" not in indexes:
        op.create_index(
            "ix_group_studies_stop_requester_user_id",
            "group_studies",
            ["stop_requester_user_id"],
            unique=False,
        )

    op.alter_column("group_studies", "stop_approvals_payload", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    if "group_studies" not in table_names:
        return

    columns = {col["name"] for col in inspector.get_columns("group_studies")}
    indexes = {idx["name"] for idx in inspector.get_indexes("group_studies")}
    foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys("group_studies")}

    if "ix_group_studies_stop_requester_user_id" in indexes:
        op.drop_index("ix_group_studies_stop_requester_user_id", table_name="group_studies")
    if "fk_group_studies_stop_requester_user_id_users" in foreign_keys:
        op.drop_constraint("fk_group_studies_stop_requester_user_id_users", "group_studies", type_="foreignkey")
    if "stop_approvals_payload" in columns:
        op.drop_column("group_studies", "stop_approvals_payload")
    if "stop_requested_at" in columns:
        op.drop_column("group_studies", "stop_requested_at")
    if "stop_requester_user_id" in columns:
        op.drop_column("group_studies", "stop_requester_user_id")
    if "stop_request_reason" in columns:
        op.drop_column("group_studies", "stop_request_reason")
