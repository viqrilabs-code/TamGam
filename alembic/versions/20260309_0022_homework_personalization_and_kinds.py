"""add homework personalization and kind fields

Revision ID: 20260309_0022
Revises: 20260308_0021
Create Date: 2026-03-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260309_0022"
down_revision = "20260308_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("homeworks")}
    indexes = {idx["name"] for idx in inspector.get_indexes("homeworks")}

    if "target_student_id" not in columns:
        op.add_column("homeworks", sa.Column("target_student_id", sa.UUID(), nullable=True))
        op.create_foreign_key(
            "fk_homeworks_target_student_id_student_profiles",
            "homeworks",
            "student_profiles",
            ["target_student_id"],
            ["id"],
            ondelete="CASCADE",
        )
    if "kind" not in columns:
        op.add_column(
            "homeworks",
            sa.Column("kind", sa.String(length=32), nullable=False, server_default="assignment"),
        )
    if "generated_by_diya" not in columns:
        op.add_column(
            "homeworks",
            sa.Column("generated_by_diya", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    if "ix_homeworks_target_student_id" not in indexes:
        op.create_index("ix_homeworks_target_student_id", "homeworks", ["target_student_id"], unique=False)
    if "ix_homeworks_kind" not in indexes:
        op.create_index("ix_homeworks_kind", "homeworks", ["kind"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("homeworks")}
    indexes = {idx["name"] for idx in inspector.get_indexes("homeworks")}
    fks = {fk["name"] for fk in inspector.get_foreign_keys("homeworks")}

    if "ix_homeworks_kind" in indexes:
        op.drop_index("ix_homeworks_kind", table_name="homeworks")
    if "ix_homeworks_target_student_id" in indexes:
        op.drop_index("ix_homeworks_target_student_id", table_name="homeworks")

    if "fk_homeworks_target_student_id_student_profiles" in fks:
        op.drop_constraint("fk_homeworks_target_student_id_student_profiles", "homeworks", type_="foreignkey")

    if "generated_by_diya" in columns:
        op.drop_column("homeworks", "generated_by_diya")
    if "kind" in columns:
        op.drop_column("homeworks", "kind")
    if "target_student_id" in columns:
        op.drop_column("homeworks", "target_student_id")
