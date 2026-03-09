"""add feature usage tracking and migrate to 2-plan catalog

Revision ID: 20260223_0013
Revises: 20260222_0012
Create Date: 2026-02-23 10:20:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import json
import uuid


# revision identifiers, used by Alembic.
revision = "20260223_0013"
down_revision = "20260222_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feature_usages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feature_key", sa.String(length=100), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("usage_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "feature_key", "period_start", name="uq_feature_usage_user_key_period"),
    )
    op.create_index(op.f("ix_feature_usages_feature_key"), "feature_usages", ["feature_key"], unique=False)
    op.create_index(op.f("ix_feature_usages_user_id"), "feature_usages", ["user_id"], unique=False)

    conn = op.get_bind()

    # Ensure new plans exist.
    core_id = uuid.uuid4()
    plus_id = uuid.uuid4()
    core_features = json.dumps(
        [
            "Unlimited tuition bookings",
            "Join all enrolled live classes",
            "Student Notes generation: 8/month",
            "Diya questions: 120/month",
            "Class assessment submissions: 10/month",
            "Profile assessment: 1/month",
            "Community interactions: 20/month",
        ]
    )
    plus_features = json.dumps(
        [
            "Everything in Learn Core",
            "Student Notes generation: 30/month",
            "Diya questions: 500/month",
            "Class assessment submissions: unlimited (fair use)",
            "Profile assessment: 2/month",
            "Community interactions: 100/month",
            "Priority support",
        ]
    )

    conn.execute(
        sa.text(
            """
            INSERT INTO plans (
                id, name, slug, price_monthly_paise, price_annual_paise, subjects_allowed,
                description, features, razorpay_plan_id_monthly, razorpay_plan_id_annual, is_active, created_at
            )
            VALUES (
                :id, :name, :slug, :monthly, :annual, :subjects_allowed,
                :description, CAST(:features AS jsonb), NULL, NULL, TRUE, now()
            )
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name,
                price_monthly_paise = EXCLUDED.price_monthly_paise,
                price_annual_paise = EXCLUDED.price_annual_paise,
                subjects_allowed = EXCLUDED.subjects_allowed,
                description = EXCLUDED.description,
                features = EXCLUDED.features,
                is_active = TRUE
            """
        ),
        {
            "id": str(core_id),
            "name": "Learn Core",
            "slug": "core",
            "monthly": 69900,
            "annual": 699900,
            "subjects_allowed": -1,
            "description": "Best for regular learners with essential AI support.",
            "features": core_features,
        },
    )

    conn.execute(
        sa.text(
            """
            INSERT INTO plans (
                id, name, slug, price_monthly_paise, price_annual_paise, subjects_allowed,
                description, features, razorpay_plan_id_monthly, razorpay_plan_id_annual, is_active, created_at
            )
            VALUES (
                :id, :name, :slug, :monthly, :annual, :subjects_allowed,
                :description, CAST(:features AS jsonb), NULL, NULL, TRUE, now()
            )
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name,
                price_monthly_paise = EXCLUDED.price_monthly_paise,
                price_annual_paise = EXCLUDED.price_annual_paise,
                subjects_allowed = EXCLUDED.subjects_allowed,
                description = EXCLUDED.description,
                features = EXCLUDED.features,
                is_active = TRUE
            """
        ),
        {
            "id": str(plus_id),
            "name": "Learn Plus",
            "slug": "plus",
            "monthly": 129900,
            "annual": 1299900,
            "subjects_allowed": -1,
            "description": "For power users who need deeper AI support and higher limits.",
            "features": plus_features,
        },
    )

    # Map active legacy subscriptions to new plans.
    conn.execute(
        sa.text(
            """
            UPDATE subscriptions s
            SET plan_id = p_core.id
            FROM plans p_old, plans p_core
            WHERE p_old.slug IN ('basic', 'free')
              AND p_core.slug = 'core'
              AND s.plan_id = p_old.id
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE subscriptions s
            SET plan_id = p_plus.id
            FROM plans p_old, plans p_plus
            WHERE p_old.slug IN ('standard', 'pro')
              AND p_plus.slug = 'plus'
              AND s.plan_id = p_old.id
            """
        )
    )

    # Deactivate legacy plans.
    conn.execute(
        sa.text("UPDATE plans SET is_active = FALSE WHERE slug IN ('free', 'basic', 'standard', 'pro')")
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE plans SET is_active = TRUE WHERE slug IN ('free', 'basic', 'standard', 'pro')"))
    conn.execute(sa.text("DELETE FROM plans WHERE slug IN ('core', 'plus')"))

    op.drop_index(op.f("ix_feature_usages_user_id"), table_name="feature_usages")
    op.drop_index(op.f("ix_feature_usages_feature_key"), table_name="feature_usages")
    op.drop_table("feature_usages")
