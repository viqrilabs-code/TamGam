"""add student and teacher personalization profile fields

Revision ID: 20260220_0002
Revises: 20260218_0001
Create Date: 2026-02-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260220_0002"
down_revision = "20260218_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("student_profiles", sa.Column("preferred_language", sa.String(length=50), nullable=True))
    op.add_column("student_profiles", sa.Column("learning_style", sa.String(length=50), nullable=True))
    op.add_column("student_profiles", sa.Column("target_exam", sa.String(length=100), nullable=True))
    op.add_column("student_profiles", sa.Column("strengths", postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column("student_profiles", sa.Column("improvement_areas", postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column("student_profiles", sa.Column("learning_goals", sa.Text(), nullable=True))
    op.add_column("student_profiles", sa.Column("weekly_study_hours", sa.Integer(), nullable=True))

    op.add_column("teacher_profiles", sa.Column("school_name", sa.String(length=255), nullable=True))
    op.add_column("teacher_profiles", sa.Column("preferred_language", sa.String(length=50), nullable=True))
    op.add_column("teacher_profiles", sa.Column("teaching_style", sa.String(length=50), nullable=True))
    op.add_column("teacher_profiles", sa.Column("focus_grades", postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column("teacher_profiles", sa.Column("focus_boards", postgresql.ARRAY(sa.String()), nullable=True))
    op.add_column("teacher_profiles", sa.Column("class_note_tone", sa.String(length=50), nullable=True))
    op.add_column("teacher_profiles", sa.Column("class_note_preferences", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("teacher_profiles", "class_note_preferences")
    op.drop_column("teacher_profiles", "class_note_tone")
    op.drop_column("teacher_profiles", "focus_boards")
    op.drop_column("teacher_profiles", "focus_grades")
    op.drop_column("teacher_profiles", "teaching_style")
    op.drop_column("teacher_profiles", "preferred_language")
    op.drop_column("teacher_profiles", "school_name")

    op.drop_column("student_profiles", "weekly_study_hours")
    op.drop_column("student_profiles", "learning_goals")
    op.drop_column("student_profiles", "improvement_areas")
    op.drop_column("student_profiles", "strengths")
    op.drop_column("student_profiles", "target_exam")
    op.drop_column("student_profiles", "learning_style")
    op.drop_column("student_profiles", "preferred_language")
