# app/db/init_db.py
# Seed initial data into the database
# Run once after migrations: python -m app.db.init_db
#
# Creates:
#   1. Admin user (from env vars or defaults)
#   2. Default subscription plans (Basic, Standard, Pro)

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
    admin_password = os.getenv("ADMIN_PASSWORD", "TamGam@Admin123")
    admin_name = os.getenv("ADMIN_NAME", "TamGam Admin")

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
    """Create default subscription plans if they don't exist."""
    plans = [
        {
            "name": "Free",
            "slug": "free",
            "price_monthly_paise": 0,
            "price_annual_paise": 0,
            "subjects_allowed": 0,
            "description": "Community access only. No classes, notes, or AI features.",
            "features": ["Community read/write", "Public teacher profiles"],
        },
        {
            "name": "Basic",
            "slug": "basic",
            "price_monthly_paise": 49900,       # 499 rupees
            "price_annual_paise": 399200,        # 3992 rupees (10 months price)
            "subjects_allowed": 1,
            "description": "Access to 1 subject. Live classes, AI notes, assessments, and Diya tutor.",
            "features": [
                "1 subject",
                "Live class access",
                "AI-generated notes",
                "Adaptive assessments",
                "Diya AI Tutor",
            ],
        },
        {
            "name": "Standard",
            "slug": "standard",
            "price_monthly_paise": 99900,        # 999 rupees
            "price_annual_paise": 799200,         # 7992 rupees (10 months price)
            "subjects_allowed": 3,
            "description": "Access to 3 subjects. All Basic features included.",
            "features": [
                "3 subjects",
                "Live class access",
                "AI-generated notes",
                "Adaptive assessments",
                "Diya AI Tutor",
                "Priority support",
            ],
        },
        {
            "name": "Pro",
            "slug": "pro",
            "price_monthly_paise": 149900,       # 1499 rupees
            "price_annual_paise": 1199200,        # 11992 rupees (10 months price)
            "subjects_allowed": -1,              # -1 = unlimited
            "description": "Unlimited subjects. All features. Best value for serious students.",
            "features": [
                "All subjects",
                "Live class access",
                "AI-generated notes",
                "Adaptive assessments",
                "Diya AI Tutor",
                "Priority support",
                "Early access to new features",
            ],
        },
    ]

    for plan_data in plans:
        existing = db.query(Plan).filter(Plan.slug == plan_data["slug"]).first()
        if existing:
            print(f"  Plan already exists: {plan_data['name']}")
            continue
        plan = Plan(**plan_data)
        db.add(plan)
        print(f"  Plan created: {plan_data['name']} (Rs.{plan_data['price_monthly_paise'] // 100}/month)")


def init_db() -> None:
    print("Seeding database...")
    db = SessionLocal()
    try:
        print("\n[1/2] Admin user")
        seed_admin(db)

        print("\n[2/2] Subscription plans")
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