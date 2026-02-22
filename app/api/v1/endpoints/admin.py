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
from app.models.subscription import Payment, Plan, Subscription
from app.models.teacher import TeacherProfile, TeacherVerification, VerificationDocument
from app.models.transcript import Transcript
from app.models.user import User
from app.schemas.admin import (
    AdminPaymentItem,
    AdminPaymentStatusUpdateRequest,
    AdminSubscriptionControlRequest,
    AdminSubscriptionItem,
    AdminSubscriptionUpdateRequest,
    AdminTeacherItem,
    AdminTeacherVerifiedUpdate,
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


@router.get("/stats", response_model=PlatformStats, summary="Platform statistics")
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
    total_notes = db.query(Note).filter(Note.status == "published").count()

    revenue = db.query(func.sum(Payment.amount_paise)).filter(Payment.status == "captured").scalar() or 0

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


@router.get(
    "/teachers",
    response_model=List[AdminTeacherItem],
    summary="List all teachers",
)
def list_teachers(
    verified: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(TeacherProfile, User).join(User, User.id == TeacherProfile.user_id)
    if verified is not None:
        query = query.filter(TeacherProfile.is_verified == verified)

    rows = query.order_by(User.full_name.asc()).offset(skip).limit(limit).all()
    items = []
    for profile, user in rows:
        latest = (
            db.query(TeacherVerification)
            .filter(TeacherVerification.teacher_id == profile.id)
            .order_by(TeacherVerification.submitted_at.desc())
            .first()
        )
        items.append(
            AdminTeacherItem(
                teacher_id=profile.id,
                user_id=user.id,
                full_name=user.full_name,
                email=user.email,
                subjects=profile.subjects,
                experience_years=profile.experience_years,
                is_verified=profile.is_verified,
                verified_at=profile.verified_at,
                latest_verification_status=latest.status if latest else None,
            )
        )
    return items


@router.patch(
    "/teachers/{teacher_id}/verified",
    response_model=MessageResponse,
    summary="Set teacher verified tag",
)
def set_teacher_verified(
    teacher_id: UUID,
    payload: AdminTeacherVerifiedUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    profile = db.query(TeacherProfile).filter(TeacherProfile.id == teacher_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher not found.")

    profile.is_verified = payload.is_verified
    profile.verified_at = datetime.now(timezone.utc) if payload.is_verified else None
    db.commit()

    teacher_user = db.query(User).filter(User.id == profile.user_id).first()
    if teacher_user:
        if payload.is_verified:
            notify(
                db,
                user_id=teacher_user.id,
                notification_type="verification_approved",
                title="Verified Teacher Tag Enabled",
                body="Your verified teacher tag is now active.",
            )
        else:
            notify(
                db,
                user_id=teacher_user.id,
                notification_type="verification_rejected",
                title="Verified Teacher Tag Removed",
                body="Your verified teacher tag was removed by admin.",
            )
        db.commit()

    state = "enabled" if payload.is_verified else "disabled"
    return MessageResponse(message=f"Teacher verified tag {state}.")


@router.get(
    "/teachers/pending",
    response_model=List[PendingVerificationItem],
    summary="List teachers pending verification",
)
def list_pending_verifications(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    results = (
        db.query(TeacherVerification, TeacherProfile, User)
        .join(TeacherProfile, TeacherProfile.id == TeacherVerification.teacher_id)
        .join(User, User.id == TeacherProfile.user_id)
        .filter(TeacherVerification.status == "pending")
        .order_by(TeacherVerification.submitted_at.asc())
        .all()
    )

    items = []
    for verification, profile, user in results:
        doc_count = (
            db.query(VerificationDocument)
            .filter(VerificationDocument.verification_id == verification.id)
            .count()
        )
        items.append(
            PendingVerificationItem(
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
            )
        )
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
    profile = db.query(TeacherProfile).filter(TeacherProfile.id == teacher_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher not found.")

    verification = (
        db.query(TeacherVerification)
        .filter(
            and_(
                TeacherVerification.teacher_id == teacher_id,
                TeacherVerification.status == "pending",
            )
        )
        .order_by(TeacherVerification.submitted_at.desc())
        .first()
    )

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
        message = "Verification approved. T mark activated on the teacher profile."

        notify(
            db,
            user_id=profile.user_id,
            notification_type="verification_approved",
            title="Verification Approved",
            body=(
                f"Congratulations {teacher_user.full_name}! Your teacher verification "
                "has been approved."
            ),
        )
    else:
        verification.status = "rejected"
        verification.rejection_reason = payload.rejection_reason
        profile.is_verified = False
        profile.verified_at = None
        message = f"Verification rejected: {payload.rejection_reason}"

        notify(
            db,
            user_id=profile.user_id,
            notification_type="verification_rejected",
            title="Verification Update",
            body=(
                f"Hi {teacher_user.full_name}, your verification was not approved. "
                f"Reason: {payload.rejection_reason}. Please resubmit."
            ),
        )

    db.commit()

    return VerifyTeacherResponse(
        teacher_id=teacher_id,
        approved=payload.approved,
        message=message,
    )


@router.get("/users", response_model=List[AdminUserItem], summary="List all users")
def list_users(
    role: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
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
        is_subscribed = (
            db.query(Subscription)
            .filter(and_(Subscription.user_id == user.id, Subscription.status == "active"))
            .first()
            is not None
        )
        result.append(
            AdminUserItem(
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
            )
        )
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


@router.get(
    "/subscriptions",
    response_model=List[AdminSubscriptionItem],
    summary="List all subscriptions",
)
def list_subscriptions(
    status: Optional[str] = Query(None),
    user_id: Optional[UUID] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(Subscription, Plan, User).join(Plan, Plan.id == Subscription.plan_id).join(
        User, User.id == Subscription.user_id
    )
    if status:
        query = query.filter(Subscription.status == status)
    if user_id:
        query = query.filter(Subscription.user_id == user_id)

    results = query.order_by(Subscription.created_at.desc()).offset(skip).limit(limit).all()

    return [
        AdminSubscriptionItem(
            id=sub.id,
            user_id=user.id,
            user_email=user.email,
            user_name=user.full_name,
            plan_id=plan.id,
            plan_name=plan.name,
            billing_cycle=sub.billing_cycle,
            status=sub.status,
            current_period_end=sub.current_period_end,
            cancel_at_period_end=sub.cancel_at_period_end,
            created_at=sub.created_at,
        )
        for sub, plan, user in results
    ]


@router.patch(
    "/subscriptions/{subscription_id}/control",
    response_model=MessageResponse,
    summary="Control subscription cancellation state",
)
def control_subscription(
    subscription_id: UUID,
    payload: AdminSubscriptionControlRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found.")

    subscription.cancel_at_period_end = payload.cancel_at_period_end
    db.commit()

    if payload.cancel_at_period_end:
        return MessageResponse(message="Subscription marked to cancel at period end.")
    return MessageResponse(message="Subscription cancellation mark removed.")


@router.patch(
    "/subscriptions/{subscription_id}",
    response_model=MessageResponse,
    summary="Update subscription details",
)
def update_subscription(
    subscription_id: UUID,
    payload: AdminSubscriptionUpdateRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    subscription = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found.")

    if payload.plan_id is None and payload.status is None and payload.billing_cycle is None and payload.cancel_at_period_end is None:
        raise HTTPException(status_code=422, detail="No fields provided to update.")

    if payload.plan_id is not None:
        plan = db.query(Plan).filter(Plan.id == payload.plan_id).first()
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found.")
        subscription.plan_id = payload.plan_id

    if payload.status is not None:
        allowed = {"pending", "active", "past_due", "paused", "cancelled", "expired"}
        if payload.status not in allowed:
            raise HTTPException(status_code=422, detail="Invalid subscription status.")
        subscription.status = payload.status

    if payload.billing_cycle is not None:
        if payload.billing_cycle not in {"monthly", "annual"}:
            raise HTTPException(status_code=422, detail="Invalid billing cycle.")
        subscription.billing_cycle = payload.billing_cycle

    if payload.cancel_at_period_end is not None:
        subscription.cancel_at_period_end = payload.cancel_at_period_end

    db.commit()
    return MessageResponse(message="Subscription updated.")


@router.get("/payments", response_model=List[AdminPaymentItem], summary="List payments")
def list_payments(
    status: Optional[str] = Query(None),
    user_id: Optional[UUID] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(Payment, User).outerjoin(User, User.id == Payment.user_id)
    if status:
        query = query.filter(Payment.status == status)
    if user_id:
        query = query.filter(Payment.user_id == user_id)

    rows = query.order_by(Payment.created_at.desc()).offset(skip).limit(limit).all()

    return [
        AdminPaymentItem(
            id=payment.id,
            user_id=payment.user_id,
            user_email=user.email if user else None,
            user_name=user.full_name if user else None,
            subscription_id=payment.subscription_id,
            amount_paise=payment.amount_paise,
            amount_rupees=payment.amount_paise / 100,
            gst_paise=payment.gst_paise,
            status=payment.status,
            razorpay_payment_id=payment.razorpay_payment_id,
            created_at=payment.created_at,
        )
        for payment, user in rows
    ]


@router.patch(
    "/payments/{payment_id}/status",
    response_model=MessageResponse,
    summary="Update payment status",
)
def update_payment_status(
    payment_id: UUID,
    payload: AdminPaymentStatusUpdateRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found.")

    allowed = {"captured", "failed", "refunded"}
    if payload.status not in allowed:
        raise HTTPException(status_code=422, detail="Invalid payment status.")

    payment.status = payload.status
    db.commit()
    return MessageResponse(message="Payment status updated.")
