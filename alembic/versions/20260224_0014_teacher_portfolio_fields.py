"""add teacher education and portfolio experience fields

Revision ID: 20260224_0014
Revises: 20260223_0013
Create Date: 2026-02-24 18:40:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260224_0014"
down_revision = "20260223_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("teacher_profiles", sa.Column("education", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("teacher_profiles", sa.Column("past_job_experiences", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("teacher_profiles", "past_job_experiences")
    op.drop_column("teacher_profiles", "education")
