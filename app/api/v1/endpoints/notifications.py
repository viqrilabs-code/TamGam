# app/api/v1/endpoints/notifications.py
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import require_login
from app.db.session import get_db
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notification import (
    MessageResponse,
    NotificationListResponse,
    NotificationResponse,
)

router = APIRouter()


def _to_response(n: Notification) -> NotificationResponse:
    return NotificationResponse(
        id=n.id,
        type=n.notification_type,
        title=n.title,
        body=n.body or "",
        extra_data=n.extra_data,
        is_read=n.is_read,
        created_at=n.created_at,
    )


@router.get(
    "/",
    response_model=NotificationListResponse,
    summary="List own notifications",
)
def list_notifications(
    unread_only: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    query = db.query(Notification).filter(Notification.user_id == current_user.id)
    if unread_only:
        query = query.filter(Notification.is_read == False)

    total = query.count()
    unread_count = db.query(Notification).filter(
        and_(Notification.user_id == current_user.id, Notification.is_read == False)
    ).count()

    notifications = query.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()

    return NotificationListResponse(
        notifications=[_to_response(n) for n in notifications],
        unread_count=unread_count,
        total=total,
    )


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark notification as read",
)
def mark_read(
    notification_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    n = db.query(Notification).filter(
        and_(Notification.id == notification_id, Notification.user_id == current_user.id)
    ).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found.")
    n.is_read = True
    db.commit()
    return _to_response(n)


@router.patch(
    "/read-all",
    response_model=MessageResponse,
    summary="Mark all notifications as read",
)
def mark_all_read(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    db.query(Notification).filter(
        and_(Notification.user_id == current_user.id, Notification.is_read == False)
    ).update({"is_read": True})
    db.commit()
    return MessageResponse(message="All notifications marked as read.")


@router.delete(
    "/{notification_id}",
    response_model=MessageResponse,
    summary="Delete a notification",
)
def delete_notification(
    notification_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    n = db.query(Notification).filter(
        and_(Notification.id == notification_id, Notification.user_id == current_user.id)
    ).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found.")
    db.delete(n)
    db.commit()
    return MessageResponse(message="Notification deleted.")

@router.get(
    "/unread-count",
    summary="Get unread notification count",
)
def get_unread_count(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    count = db.query(Notification).filter(
        and_(Notification.user_id == current_user.id, Notification.is_read == False)
    ).count()
    return {"count": count}