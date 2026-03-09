# app/db/init_db.py
# Seed initial data into the database
# Run once after migrations: python -m app.db.init_db
#
# Creates:
#   1. Admin user (from env vars or defaults)
#   2. Default teacher billing plan

import os
import sys

# Load .env for local dev
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import app.db.base
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.subscription import Plan
from app.models.user import User


def seed_admin(db) -> None:
    """Create the admin user if it doesn't exist."""
    admin_email = os.getenv("ADMIN_EMAIL", "admin@tamgam.in")
    admin_password = os.getenv("ADMIN_PASSWORD", "tamgam@Admin123")
    admin_name = os.getenv("ADMIN_NAME", "tamgam Admin")

    existing = db.query(User).filter(User.email == admin_email).first()
    if existing:
        print(f"  Admin already exists: {admin_email}")
        return

    admin = User(
        email=admin_email,
        hashed_password=hash_password(admin_password),
        full_name=admin_name,
        role="admin",
        auth_provider="email",
        is_active=True,
        is_email_verified=True,
    )
    db.add(admin)
    db.flush()
    print(f"  Admin created: {admin_email}")


def seed_plans(db) -> None:
    """Create/update default billing plans (teacher-only)."""
    # Platform fee X is set to Rs 250/month.
    # Break-even assumption used:
    #   - 10 teachers, 5 students each (50 students total)
    #   - Hosting estimate near 50 students: ~Rs 9,500/month (README baseline)
    #   - Expected commission at low-income tier from current baseline pricing contributes ~Rs 6,990
    #   - Required fixed fee per teacher ~= (9,500 - 6,990) / 10 = Rs 251 -> rounded to Rs 250
    plans = [
        {
            "name": "Teacher Platform",
            "slug": "teacher-platform",
            "price_monthly_paise": 25000,
            "price_annual_paise": 250000,
            "subjects_allowed": -1,
            "description": "Teacher billing plan: monthly platform fee + dynamic commission (20% / 15% / 10%).",
            "features": [
                "Students are free on tamgam",
                "Platform fee: Rs 250/month",
                "Dynamic commission on teacher income: 20% / 15% / 10%",
                "Tuition requests and enrollments",
                "AI notes, Diya tutor, and assessments included",
            ],
        },
    ]

    active_slugs = {p["slug"] for p in plans}
    for plan_data in plans:
        existing = db.query(Plan).filter(Plan.slug == plan_data["slug"]).first()
        if existing:
            for k, v in plan_data.items():
                setattr(existing, k, v)
            existing.is_active = True
            print(f"  Plan updated: {plan_data['name']}")
            continue
        plan = Plan(**plan_data)
        db.add(plan)
        print(f"  Plan created: {plan_data['name']} (Rs.{plan_data['price_monthly_paise'] // 100}/month)")

    # Disable legacy plans from old catalog.
    legacy = db.query(Plan).filter(~Plan.slug.in_(list(active_slugs))).all()
    for old in legacy:
        old.is_active = False
        print(f"  Plan disabled: {old.name}")


def init_db() -> None:
    print("Seeding database...")
    db = SessionLocal()
    try:
        print("\n[1/2] Admin user")
        seed_admin(db)

        print("\n[2/2] Teacher billing plans")
        seed_plans(db)

        db.commit()
        print("\nDone. Database seeded successfully.")
    except Exception as e:
        db.rollback()
        print(f"\nERROR: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    init_db()

