"""migrate plan catalog to teacher-platform pricing

Revision ID: 20260312_0024
Revises: 20260312_0023
Create Date: 2026-03-12 15:30:00
"""

from alembic import op
import sqlalchemy as sa
import json
import uuid


revision = "20260312_0024"
down_revision = "20260312_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    teacher_features = json.dumps(
        [
            "Students are free on tamgam",
            "Platform fee: Rs 99/month",
            "Flat commission on teacher income: 5%",
            "Tuition requests and enrollments",
            "AI notes, Diya tutor, and assessments included",
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
                is_active = TRUE,
                razorpay_plan_id_monthly = CASE
                    WHEN plans.price_monthly_paise <> EXCLUDED.price_monthly_paise THEN NULL
                    ELSE plans.razorpay_plan_id_monthly
                END,
                razorpay_plan_id_annual = CASE
                    WHEN plans.price_annual_paise <> EXCLUDED.price_annual_paise THEN NULL
                    ELSE plans.razorpay_plan_id_annual
                END
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "name": "Teacher Platform",
            "slug": "teacher-platform",
            "monthly": 9900,
            "annual": 99000,
            "subjects_allowed": -1,
            "description": "Teacher billing plan: monthly platform fee + flat 5% commission.",
            "features": teacher_features,
        },
    )

    conn.execute(
        sa.text(
            """
            UPDATE plans
            SET is_active = FALSE
            WHERE slug IN ('core', 'plus', 'free', 'basic', 'standard', 'pro')
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE plans SET is_active = TRUE WHERE slug IN ('core', 'plus')"))
    conn.execute(sa.text("UPDATE plans SET is_active = FALSE WHERE slug = 'teacher-platform'"))

