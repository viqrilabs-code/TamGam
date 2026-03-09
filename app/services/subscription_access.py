# app/services/subscription_access.py
# Shared helpers for subscription effectiveness.

from datetime import datetime, timezone
from typing import Iterable, List
from uuid import UUID

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.subscription import Subscription


def is_effective_active_subscription(sub: Subscription, *, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    if sub.status != "active":
        return False
    if sub.cancel_at_period_end and sub.current_period_end and current >= sub.current_period_end:
        return False
    return True


def get_effective_active_subscriptions(user_id: UUID, db: Session) -> List[Subscription]:
    current = datetime.now(timezone.utc)
    query = db.query(Subscription).filter(
        and_(
            Subscription.user_id == user_id,
            Subscription.status == "active",
        )
    ).order_by(Subscription.created_at.desc())
    if hasattr(query, "all"):
        subs = query.all()
    elif hasattr(query, "first"):
        one = query.first()
        subs = [one] if one else []
    else:
        subs = []
    return [sub for sub in subs if is_effective_active_subscription(sub, now=current)]


def can_allocate_teacher_seat(
    *,
    seat_count: int,
    active_teacher_ids: Iterable[UUID] | set[UUID],
    target_teacher_id: UUID,
) -> bool:
    """
    Backward-compatible helper kept for tests/legacy callers.
    If teacher is already part of the active set, allocation is allowed.
    Otherwise enforce max seat capacity.
    """
    active_set = set(active_teacher_ids or [])
    if target_teacher_id in active_set:
        return True
    capacity = max(0, int(seat_count or 0))
    return len(active_set) < capacity
