# app/services/plan_limits.py
# Plan entitlements and monthly usage tracking helpers.

from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.dependencies import get_effective_active_subscription
from app.models.feature_usage import FeatureUsage
from app.models.subscription import Plan
from app.models.user import User

# -1 means unlimited
PLAN_LIMITS = {
    "core": {
        "student_notes_monthly": 8,
        "diya_questions_monthly": 120,
        "class_assessment_submissions_monthly": 10,
        "profile_assessment_attempts_monthly": 1,
        "community_interactions_monthly": 20,
    },
    "plus": {
        "student_notes_monthly": 30,
        "diya_questions_monthly": 500,
        "class_assessment_submissions_monthly": -1,
        "profile_assessment_attempts_monthly": 2,
        "community_interactions_monthly": 100,
    },
    # Backward-compat fallback for older seeded slugs.
    "basic": {
        "student_notes_monthly": 8,
        "diya_questions_monthly": 120,
        "class_assessment_submissions_monthly": 10,
        "profile_assessment_attempts_monthly": 1,
        "community_interactions_monthly": 20,
    },
    "standard": {
        "student_notes_monthly": 30,
        "diya_questions_monthly": 500,
        "class_assessment_submissions_monthly": -1,
        "profile_assessment_attempts_monthly": 2,
        "community_interactions_monthly": 100,
    },
    "pro": {
        "student_notes_monthly": 30,
        "diya_questions_monthly": 500,
        "class_assessment_submissions_monthly": -1,
        "profile_assessment_attempts_monthly": 2,
        "community_interactions_monthly": 100,
    },
}


def month_period_start(now: Optional[datetime] = None) -> date:
    current = now or datetime.now(timezone.utc)
    return date(current.year, current.month, 1)


def get_active_plan(user_id: UUID, db: Session) -> Optional[Plan]:
    sub = get_effective_active_subscription(user_id, db)
    if not sub:
        return None
    return db.query(Plan).filter(Plan.id == sub.plan_id).first()


def get_limit_for_user(user_id: UUID, feature_key: str, db: Session) -> Optional[int]:
    plan = get_active_plan(user_id, db)
    if not plan:
        if db is None:
            return None
        user = db.query(User).filter(User.id == user_id).first()
        if user and user.role == "student":
            return None
        return None
    slug = (plan.slug or "").strip().lower()
    return PLAN_LIMITS.get(slug, {}).get(feature_key)


def get_current_usage(user_id: UUID, feature_key: str, db: Session) -> int:
    row = db.query(FeatureUsage).filter(
        and_(
            FeatureUsage.user_id == user_id,
            FeatureUsage.feature_key == feature_key,
            FeatureUsage.period_start == month_period_start(),
        )
    ).first()
    return int(row.usage_count or 0) if row else 0


def assert_feature_available(user_id: UUID, feature_key: str, db: Session, units: int = 1) -> None:
    limit = get_limit_for_user(user_id, feature_key, db)
    if limit is None:
        raise HTTPException(
            status_code=403,
            detail={"message": "Active teacher billing plan required.", "redirect": "/plans.html"},
        )
    if limit < 0:
        return
    used = get_current_usage(user_id, feature_key, db)
    if used + units > limit:
        raise HTTPException(
            status_code=403,
            detail={
                "message": f"Monthly limit reached for {feature_key.replace('_', ' ')} ({used}/{limit}).",
                "redirect": "/plans.html",
            },
        )


def consume_feature(user_id: UUID, feature_key: str, db: Session, units: int = 1) -> int:
    period = month_period_start()
    row = db.query(FeatureUsage).filter(
        and_(
            FeatureUsage.user_id == user_id,
            FeatureUsage.feature_key == feature_key,
            FeatureUsage.period_start == period,
        )
    ).with_for_update(read=False).first()
    if not row:
        row = FeatureUsage(
            user_id=user_id,
            feature_key=feature_key,
            period_start=period,
            usage_count=0,
        )
        db.add(row)
        db.flush()
    row.usage_count = int(row.usage_count or 0) + int(units or 0)
    return row.usage_count

