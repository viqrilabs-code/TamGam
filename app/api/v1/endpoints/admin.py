# app/api/v1/endpoints/admin.py
# Admin portal endpoints -- all require role=admin

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import require_admin
from app.db.session import get_db
from app.models.class_ import Class
from app.models.note import Note
from app.models.student import StudentProfile
from app.models.subscription import Payment, Plan, Subscription
from app.models.teacher import TeacherProfile, TeacherVerification, VerificationDocument
from app.models.transcript import Transcript
from app.models.user import User
from app.schemas.admin import (
    AdminSubscriptionItem,
    AdminUserItem,
    MessageResponse,
    PendingVerificationItem,
    PlatformStats,
    UserStatusUpdate,
    VerifyTeacherRequest,
    VerifyTeacherResponse,
)
from app.services.notification_service import notify

router = APIRouter()


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get(
    "/stats",
    response_model=PlatformStats,
    summary="Platform statistics",
)
def get_stats(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    total_users = db.query(User).count()
    total_students = db.query(User).filter(User.role == "student").count()
    total_teachers = db.query(User).filter(User.role == "teacher").count()
    verified_teachers = db.query(TeacherProfile).filter(TeacherProfile.is_verified == True).count()
    pending = db.query(TeacherVerification).filter(TeacherVerification.status == "pending").count()
    active_subs = db.query(Subscription).filter(Subscription.status == "active").count()
    total_classes = db.query(Class).count()
    total_transcripts = db.query(Transcript).count()
    total_notes = db.query(Note).filter(Note.status == "completed").count()

    revenue = db.query(func.sum(Payment.amount_paise)).filter(
        Payment.status == "captured"
    ).scalar() or 0

    return PlatformStats(
        total_users=total_users,
        total_students=total_students,
        total_teachers=total_teachers,
        verified_teachers=verified_teachers,
        pending_verifications=pending,
        active_subscriptions=active_subs,
        total_revenue_paise=revenue,
        total_revenue_rupees=revenue / 100,
        total_classes=total_classes,
        total_transcripts=total_transcripts,
        total_notes=total_notes,
    )


# ── Teacher Verification ──────────────────────────────────────────────────────

@router.get(
    "/teachers/pending",
    response_model=List[PendingVerificationItem],
    summary="List teachers pending verification",
)
def list_pending_verifications(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    results = db.query(TeacherVerification, TeacherProfile, User).join(
        TeacherProfile, TeacherProfile.id == TeacherVerification.teacher_id
    ).join(
        User, User.id == TeacherProfile.user_id
    ).filter(
        TeacherVerification.status == "pending"
    ).order_by(TeacherVerification.submitted_at.asc()).all()

    items = []
    for verification, profile, user in results:
        doc_count = db.query(VerificationDocument).filter(
            VerificationDocument.verification_id == verification.id
        ).count()
        items.append(PendingVerificationItem(
            teacher_id=profile.id,
            user_id=user.id,
            full_name=user.full_name,
            email=user.email,
            avatar_url=user.avatar_url,
            bio=profile.bio,
            subjects=profile.subjects,
            qualifications=profile.qualifications,
            experience_years=profile.experience_years,
            verification_id=verification.id,
            submitted_at=verification.submitted_at,
            document_count=doc_count,
        ))
    return items


@router.post(
    "/teachers/{teacher_id}/verify",
    response_model=VerifyTeacherResponse,
    summary="Approve or reject teacher verification",
)
def verify_teacher(
    teacher_id: UUID,
    payload: VerifyTeacherRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Approve or reject a teacher verification request.
    On approval: sets is_verified=True on profile, notifies teacher.
    On rejection: stores reason, notifies teacher to resubmit.
    """
    profile = db.query(TeacherProfile).filter(TeacherProfile.id == teacher_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher not found.")

    verification = db.query(TeacherVerification).filter(
        and_(
            TeacherVerification.teacher_id == teacher_id,
            TeacherVerification.status == "pending",
        )
    ).order_by(TeacherVerification.submitted_at.desc()).first()

    if not verification:
        raise HTTPException(status_code=404, detail="No pending verification request found.")

    if not payload.approved and not payload.rejection_reason:
        raise HTTPException(
            status_code=422,
            detail="rejection_reason is required when rejecting.",
        )

    verification.reviewed_by_admin_id = current_user.id
    verification.reviewed_at = datetime.now(timezone.utc)
    verification.admin_notes = payload.admin_notes

    teacher_user = db.query(User).filter(User.id == profile.user_id).first()

    if payload.approved:
        verification.status = "approved"
        profile.is_verified = True
        profile.verified_at = datetime.now(timezone.utc)
        message = "Verification approved. 🟡T mark activated on your profile."

        notify(
            db,
            user_id=profile.user_id,
            notification_type="verification_approved",
            title="Verification Approved!",
            body=f"Congratulations {teacher_user.full_name}! Your teacher verification has been approved. Your 🟡T mark is now visible on your profile.",
        )
    else:
        verification.status = "rejected"
        verification.rejection_reason = payload.rejection_reason
        message = f"Verification rejected: {payload.rejection_reason}"

        notify(
            db,
            user_id=profile.user_id,
            notification_type="verification_rejected",
            title="Verification Update",
            body=f"Hi {teacher_user.full_name}, your verification was not approved. Reason: {payload.rejection_reason}. Please resubmit with the required documents.",
        )

    db.commit()

    return VerifyTeacherResponse(
        teacher_id=teacher_id,
        approved=payload.approved,
        message=message,
    )


# ── User Management ───────────────────────────────────────────────────────────

@router.get(
    "/users",
    response_model=List[AdminUserItem],
    summary="List all users",
)
def list_users(
    role: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(User)
    if role:
        query = query.filter(User.role == role)
    if is_active is not None:
        query = query.filter(User.is_active == is_active)

    users = query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()

    result = []
    for user in users:
        is_subscribed = db.query(Subscription).filter(
            and_(Subscription.user_id == user.id, Subscription.status == "active")
        ).first() is not None
        result.append(AdminUserItem(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
            is_email_verified=user.is_email_verified,
            auth_provider=user.auth_provider,
            is_subscribed=is_subscribed,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        ))
    return result


@router.patch(
    "/users/{user_id}/status",
    response_model=MessageResponse,
    summary="Activate or deactivate a user",
)
def update_user_status(
    user_id: UUID,
    payload: UserStatusUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.role == "admin":
        raise HTTPException(status_code=403, detail="Cannot deactivate admin accounts.")

    user.is_active = payload.is_active
    db.commit()

    status = "activated" if payload.is_active else "deactivated"
    return MessageResponse(message=f"User {user.email} {status}.")


# ── Subscriptions ─────────────────────────────────────────────────────────────

@router.get(
    "/subscriptions",
    response_model=List[AdminSubscriptionItem],
    summary="List all subscriptions",
)
def list_subscriptions(
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(Subscription, Plan, User).join(
        Plan, Plan.id == Subscription.plan_id
    ).join(
        User, User.id == Subscription.user_id
    )
    if status:
        query = query.filter(Subscription.status == status)

    results = query.order_by(Subscription.created_at.desc()).offset(skip).limit(limit).all()

    return [
        AdminSubscriptionItem(
            id=sub.id,
            user_id=user.id,
            user_email=user.email,
            user_name=user.full_name,
            plan_name=plan.name,
            billing_cycle=sub.billing_cycle,
            status=sub.status,
            current_period_end=sub.current_period_end,
            cancel_at_period_end=sub.cancel_at_period_end,
            created_at=sub.created_at,
        )
        for sub, plan, user in results
    ]