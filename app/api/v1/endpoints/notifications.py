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
from app.models.student import StudentProfile
from app.models.teacher import TeacherProfile, TeacherStudentVerificationRequest
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


@router.post(
    "/{notification_id}/teacher-verification-response",
    response_model=MessageResponse,
    summary="Student responds to teacher verification request",
)
def respond_teacher_verification(
    notification_id: UUID,
    decision: str = Query(..., description="verify | dont_know"),
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    if current_user.role != "student":
        raise HTTPException(status_code=403, detail="Student access only.")
    if decision not in {"verify", "dont_know"}:
        raise HTTPException(status_code=422, detail="decision must be verify or dont_know")

    student_profile = db.query(StudentProfile).filter(StudentProfile.user_id == current_user.id).first()
    if not student_profile:
        raise HTTPException(status_code=404, detail="Student profile not found.")

    notification = db.query(Notification).filter(
        and_(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
    ).first()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found.")

    data = notification.extra_data or {}
    if data.get("kind") != "teacher_verification_request":
        raise HTTPException(status_code=409, detail="This notification is not a verification request.")

    request_id = data.get("verification_request_id")
    if not request_id:
        raise HTTPException(status_code=409, detail="Invalid verification request payload.")

    try:
        request_uuid = UUID(str(request_id))
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Invalid verification request id.") from exc

    verification_request = db.query(TeacherStudentVerificationRequest).filter(
        TeacherStudentVerificationRequest.id == request_uuid
    ).first()
    if not verification_request:
        raise HTTPException(status_code=404, detail="Verification request not found.")
    if verification_request.student_id != student_profile.id:
        raise HTTPException(status_code=403, detail="You cannot respond to this request.")
    if verification_request.status != "pending":
        raise HTTPException(status_code=409, detail="This verification request was already answered.")

    teacher_profile = db.query(TeacherProfile).filter(
        TeacherProfile.id == verification_request.teacher_id
    ).first()
    if not teacher_profile:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")

    verification_request.status = "verified" if decision == "verify" else "dont_know"
    verification_request.responded_at = datetime.now(timezone.utc)
    notification.is_read = True
    notification.read_at = datetime.now(timezone.utc)
    notification.extra_data = {
        **(notification.extra_data or {}),
        "status": decision,
    }

    teacher_user = db.query(User).filter(User.id == teacher_profile.user_id).first()
    response_text = "verified you" if decision == "verify" else "clicked Don't know"
    db.add(Notification(
        user_id=teacher_profile.user_id,
        notification_type="announcement",
        title="Student verification update",
        body=f"{current_user.full_name} {response_text} on your T badge request.",
        action_url="/teacher-dashboard.html#notifications-panel",
        extra_data={
            "kind": "teacher_verification_response",
            "verification_request_id": str(verification_request.id),
            "teacher_id": str(teacher_profile.id),
            "student_id": str(student_profile.id),
            "student_name": current_user.full_name,
            "decision": decision,
        },
        is_read=False,
    ))

    verified_count = db.query(TeacherStudentVerificationRequest).filter(
        and_(
            TeacherStudentVerificationRequest.teacher_id == teacher_profile.id,
            TeacherStudentVerificationRequest.status == "verified",
        )
    ).count()
    if verified_count >= 3 and not teacher_profile.is_verified:
        teacher_profile.is_verified = True
        teacher_profile.verified_at = datetime.now(timezone.utc)
        if teacher_user:
            db.add(Notification(
                user_id=teacher_user.id,
                notification_type="announcement",
                title="You earned the T badge",
                body="Three students verified you. Your teacher profile now shows the T badge.",
                action_url="/teacher-dashboard.html",
                extra_data={
                    "kind": "teacher_verified_by_students",
                    "teacher_id": str(teacher_profile.id),
                },
                is_read=False,
            ))

    db.commit()
    return MessageResponse(message="Your response has been submitted.")
