"""create teacher ratings and rating count snapshot

Revision ID: 20260308_0021
Revises: 20260308_0020
Create Date: 2026-03-08 17:25:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260308_0021"
down_revision = "20260308_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "teacher_profiles" in table_names:
        columns = {col["name"] for col in inspector.get_columns("teacher_profiles")}
        if "rating_count" not in columns:
            op.add_column(
                "teacher_profiles",
                sa.Column("rating_count", sa.Integer(), nullable=False, server_default="0"),
            )

    if "teacher_ratings" not in table_names:
        op.create_table(
            "teacher_ratings",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("teacher_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("student_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("rating", sa.Integer(), nullable=False),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_teacher_ratings_rating_range"),
            sa.ForeignKeyConstraint(["teacher_id"], ["teacher_profiles.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["student_id"], ["student_profiles.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("teacher_id", "student_id", name="uq_teacher_ratings_teacher_student"),
        )
        op.create_index("ix_teacher_ratings_teacher_id", "teacher_ratings", ["teacher_id"], unique=False)
        op.create_index("ix_teacher_ratings_student_id", "teacher_ratings", ["student_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "teacher_ratings" in table_names:
        indexes = {idx["name"] for idx in inspector.get_indexes("teacher_ratings")}
        if "ix_teacher_ratings_teacher_id" in indexes:
            op.drop_index("ix_teacher_ratings_teacher_id", table_name="teacher_ratings")
        if "ix_teacher_ratings_student_id" in indexes:
            op.drop_index("ix_teacher_ratings_student_id", table_name="teacher_ratings")
        op.drop_table("teacher_ratings")

    if "teacher_profiles" in table_names:
        columns = {col["name"] for col in inspector.get_columns("teacher_profiles")}
        if "rating_count" in columns:
            op.drop_column("teacher_profiles", "rating_count")

