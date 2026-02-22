"""add teacher feedback fields to student assessments

Revision ID: 20260222_0012
Revises: 20260222_0011
Create Date: 2026-02-22 14:15:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260222_0012"
down_revision = "20260222_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("student_assessments", sa.Column("teacher_feedback_text", sa.Text(), nullable=True))
    op.add_column("student_assessments", sa.Column("teacher_feedback_score", sa.Integer(), nullable=True))
    op.add_column("student_assessments", sa.Column("teacher_feedback_given_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("student_assessments", "teacher_feedback_given_at")
    op.drop_column("student_assessments", "teacher_feedback_score")
    op.drop_column("student_assessments", "teacher_feedback_text")
