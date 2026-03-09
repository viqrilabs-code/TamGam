# app/api/v1/endpoints/subscriptions.py
# Subscription and payment endpoints
#
# Flow:
#   1. Teacher picks a billing plan -> POST /create -> gets payment_link
#   2. Teacher pays on Razorpay hosted page
#   3. Razorpay sends webhook -> POST /webhook -> we update DB status
#   4. Teacher now has active billing subscription

import json
import logging
from datetime import datetime, timezone
from html import escape
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import and_
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.config import settings
from app.core.dependencies import (
    get_effective_active_subscription,
    get_effective_active_subscriptions,
    require_login,
)
from app.db.session import get_db
from app.models.notification import Notification
from app.models.student import Batch, BatchMember, Enrollment, StudentProfile
from app.models.subscription import Payment, Plan, Subscription
from app.models.teacher import TeacherProfile
from app.models.tuition_request import TuitionRequest
from app.models.user import User
from app.schemas.subscription import (
    CancelSubscriptionResponse,
    CreateSubscriptionRequest,
    CreateSubscriptionResponse,
    MessageResponse,
    PaymentResponse,
    PlanResponse,
    SubscriptionStatusResponse,
    WebhookResponse,
)
from app.services import razorpay_service
from app.services.transactional_email import (
    TransactionalEmailConfigError,
    TransactionalEmailError,
    send_transactional_email,
)

router = APIRouter()
logger = logging.getLogger("tamgam.subscriptions")

def _format_razorpay_error(exc: Exception) -> str:
    """Return a non-empty readable Razorpay error string."""
    parts = []
    message = str(exc).strip()
    if message:
        parts.append(message)
    for attr in ("status_code", "code", "reason", "description", "field"):
        value = getattr(exc, attr, None)
        if value not in (None, "", []):
            parts.append(f"{attr}={value}")
    args = getattr(exc, "args", None)
    if args:
        parts.append(f"args={args!r}")
    details = getattr(exc, "__dict__", None)
    if details:
        parts.append(f"details={details!r}")
    if parts:
        return " | ".join(parts)
    return f"{exc.__class__.__name__}: {repr(exc)}"


def _teacher_commission_rate(total_revenue_paise: int) -> float:
    """
    Commission tiers for teacher income:
      up to Rs 25,000      -> 25%
      Rs 25,001-75,000     -> 20%
      above Rs 75,000      -> 15%
    """
    total_rupees = (total_revenue_paise or 0) / 100
    if total_rupees <= 25000:
        return 25.0
    if total_rupees <= 75000:
        return 20.0
    return 15.0


def _epoch_to_utc(value) -> datetime | None:
    try:
        if value is None:
            return None
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except Exception:
        return None


def _format_amount_inr(amount_paise: int) -> str:
    return f"{(int(amount_paise or 0) / 100):,.2f}"


def _send_batch_purchase_receipt_email(
    *,
    student_email: str,
    student_name: str,
    teacher_name: str,
    batch_name: str,
    subject: str,
    amount_paise: int,
    payment_id: str,
    paid_at: datetime | None,
) -> None:
    paid_label = (paid_at or datetime.now(timezone.utc)).strftime("%d %b %Y, %I:%M %p UTC")
    subject_line = f"tamgam receipt: {batch_name}"
    plain_text = (
        f"Hi {student_name},\n\n"
        "Your batch purchase is confirmed.\n\n"
        f"Batch: {batch_name}\n"
        f"Teacher: {teacher_name}\n"
        f"Subject: {subject}\n"
        f"Amount paid: INR {_format_amount_inr(amount_paise)}\n"
        f"Payment ID: {payment_id}\n"
        f"Paid at: {paid_label}\n\n"
        "Please keep this email as your receipt."
    )
    html = f"""
<!doctype html>
<html>
  <body style="font-family:Arial,sans-serif;background:#fdf6ec;padding:24px;">
    <div style="max-width:560px;margin:0 auto;background:#fff;border:1px solid #f0e4cc;border-radius:12px;overflow:hidden;">
      <div style="background:#e8640c;color:#fff;padding:16px 20px;">
        <h2 style="margin:0;font-size:20px;">tamgam</h2>
      </div>
      <div style="padding:20px;color:#1a1a2e;">
        <p style="margin:0 0 12px 0;">Hi {escape(student_name)},</p>
        <p style="margin:0 0 12px 0;">Your batch purchase is confirmed.</p>
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
          <tr><td style="padding:6px 0;color:#4b5563;">Batch</td><td style="padding:6px 0;"><strong>{escape(batch_name)}</strong></td></tr>
          <tr><td style="padding:6px 0;color:#4b5563;">Teacher</td><td style="padding:6px 0;"><strong>{escape(teacher_name)}</strong></td></tr>
          <tr><td style="padding:6px 0;color:#4b5563;">Subject</td><td style="padding:6px 0;"><strong>{escape(subject)}</strong></td></tr>
          <tr><td style="padding:6px 0;color:#4b5563;">Amount paid</td><td style="padding:6px 0;"><strong>INR {_format_amount_inr(amount_paise)}</strong></td></tr>
          <tr><td style="padding:6px 0;color:#4b5563;">Payment ID</td><td style="padding:6px 0;"><strong>{escape(payment_id)}</strong></td></tr>
          <tr><td style="padding:6px 0;color:#4b5563;">Paid at</td><td style="padding:6px 0;"><strong>{escape(paid_label)}</strong></td></tr>
        </table>
        <p style="margin:14px 0 0;color:#6b7280;font-size:13px;">Please keep this email as your receipt.</p>
      </div>
    </div>
  </body>
</html>
""".strip()
    send_transactional_email(
        to_email=student_email,
        subject=subject_line,
        plain_text=plain_text,
        html=html,
    )


def _handle_batch_join_payment_captured(rz_payment: dict, db: Session) -> None:
    payment_id = str(rz_payment.get("id") or "").strip()
    if not payment_id:
        return

    existing_payment = db.query(Payment).filter(Payment.razorpay_payment_id == payment_id).first()
    if existing_payment:
        return

    notes = rz_payment.get("notes") or {}
    if str(notes.get("kind") or "").strip().lower() != "batch_join":
        return

    try:
        batch_id = UUID(str(notes.get("batch_id")))
        teacher_id = UUID(str(notes.get("teacher_id")))
        student_user_id = UUID(str(notes.get("student_user_id")))
    except Exception:
        logger.warning("batch_join payment ignored due to invalid note IDs payment_id=%s", payment_id)
        return

    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    teacher_profile = db.query(TeacherProfile).filter(TeacherProfile.id == teacher_id).first()
    student_user = db.query(User).filter(User.id == student_user_id).first()
    student_profile = db.query(StudentProfile).filter(StudentProfile.user_id == student_user_id).first()
    teacher_user = db.query(User).filter(User.id == (teacher_profile.user_id if teacher_profile else None)).first()
    if not batch or not teacher_profile or not student_user or not student_profile or not teacher_user:
        logger.warning("batch_join payment ignored due to missing entities payment_id=%s", payment_id)
        return
    if not student_user.is_active:
        logger.warning("batch_join payment ignored for inactive student payment_id=%s", payment_id)
        return

    subject_text = str(notes.get("subject") or batch.subject or "General").strip()
    amount_paise = int(rz_payment.get("amount") or batch.fee_paise or 0)
    payment_time = _epoch_to_utc(rz_payment.get("captured_at")) or _epoch_to_utc(rz_payment.get("created_at"))
    gst_paise = int(amount_paise * 18 / 118) if amount_paise > 0 else 0

    payment = Payment(
        user_id=student_user.id,
        subscription_id=None,
        amount_paise=amount_paise,
        gst_paise=gst_paise,
        status="captured",
        razorpay_payment_id=payment_id,
        razorpay_order_id=rz_payment.get("order_id"),
        payment_method=rz_payment.get("method"),
        webhook_payload=rz_payment,
    )
    db.add(payment)

    enrollment = db.query(Enrollment).filter(
        and_(
            Enrollment.student_id == student_profile.id,
            Enrollment.teacher_id == teacher_profile.id,
            Enrollment.subject == subject_text,
            Enrollment.is_active == True,
        )
    ).first()
    if not enrollment:
        enrollment = Enrollment(
            student_id=student_profile.id,
            teacher_id=teacher_profile.id,
            subject=subject_text,
            is_active=True,
        )
        db.add(enrollment)
        db.flush()

    existing_member = db.query(BatchMember).filter(
        and_(
            BatchMember.batch_id == batch.id,
            BatchMember.student_id == student_profile.id,
        )
    ).first()
    if not existing_member:
        db.add(BatchMember(batch_id=batch.id, student_id=student_profile.id))

    req = db.query(TuitionRequest).filter(
        and_(
            TuitionRequest.student_id == student_profile.id,
            TuitionRequest.teacher_id == teacher_profile.id,
            TuitionRequest.batch_id == batch.id,
            TuitionRequest.subject == subject_text,
            TuitionRequest.status.in_(("pending", "accepted")),
        )
    ).order_by(TuitionRequest.created_at.desc()).first()
    if not req:
        req = TuitionRequest(
            student_id=student_profile.id,
            teacher_id=teacher_profile.id,
            batch_id=batch.id,
            subject=subject_text,
            message="Auto-created from successful batch purchase",
            grade=student_profile.grade,
            status="accepted",
            enrollment_id=enrollment.id,
            responded_at=datetime.now(timezone.utc),
        )
        db.add(req)
    else:
        req.status = "accepted"
        req.enrollment_id = enrollment.id
        req.responded_at = datetime.now(timezone.utc)
        req.updated_at = datetime.now(timezone.utc)

    if amount_paise > 0:
        teacher_profile.total_revenue_paise = int(teacher_profile.total_revenue_paise or 0) + amount_paise
        rate = _teacher_commission_rate(int(teacher_profile.total_revenue_paise or 0))
        commission = int(round(amount_paise * rate / 100.0))
        teacher_profile.platform_commission_paise = int(teacher_profile.platform_commission_paise or 0) + commission

    teacher_profile.total_students = db.query(Enrollment).filter(
        and_(
            Enrollment.teacher_id == teacher_profile.id,
            Enrollment.is_active == True,
        )
    ).count()

    db.add(Notification(
        user_id=student_user.id,
        notification_type="announcement",
        title="Batch purchase confirmed",
        body=(
            f"Your payment for {batch.name} is confirmed. "
            f"You are enrolled in {subject_text} with {teacher_user.full_name}."
        ),
        action_url="/dashboard.html",
        extra_data={
            "kind": "batch_purchase_confirmed",
            "batch_id": str(batch.id),
            "teacher_id": str(teacher_profile.id),
            "subject": subject_text,
            "payment_id": payment_id,
            "amount_paise": amount_paise,
        },
        is_read=False,
    ))
    db.add(Notification(
        user_id=teacher_user.id,
        notification_type="announcement",
        title="New batch purchase",
        body=(
            f"{student_user.full_name} completed payment for {batch.name} "
            f"({subject_text})."
        ),
        action_url="/teacher-dashboard.html#tuition-requests",
        extra_data={
            "kind": "batch_purchase_received",
            "batch_id": str(batch.id),
            "student_id": str(student_profile.id),
            "payment_id": payment_id,
            "subject": subject_text,
        },
        is_read=False,
    ))

    if student_user.email:
        try:
            _send_batch_purchase_receipt_email(
                student_email=student_user.email,
                student_name=student_user.full_name,
                teacher_name=teacher_user.full_name,
                batch_name=batch.name,
                subject=subject_text,
                amount_paise=amount_paise,
                payment_id=payment_id,
                paid_at=payment_time,
            )
        except (TransactionalEmailConfigError, TransactionalEmailError) as exc:
            logger.warning("batch_join receipt email failed payment_id=%s error=%s", payment_id, exc)

def _enforce_single_plan_topups(requested_plan_id: UUID, ongoing_subscriptions: list) -> UUID | None:
    """
    Compatibility helper retained for plan validation logic.
    Ensures all ongoing subscriptions (if any) point to one plan slug/id.
    Returns the locked ongoing plan_id when present.
    """
    if not ongoing_subscriptions:
        return None

    plan_ids = {getattr(sub, "plan_id", None) for sub in ongoing_subscriptions if getattr(sub, "plan_id", None)}
    if not plan_ids:
        return None
    if len(plan_ids) > 1:
        raise HTTPException(status_code=409, detail="Mixed ongoing billing plans are not supported.")

    locked_plan_id = next(iter(plan_ids))
    if requested_plan_id != locked_plan_id:
        raise HTTPException(
            status_code=409,
            detail="You already have an ongoing billing plan with a different configuration.",
        )
    return locked_plan_id


# â”€â”€ Plans â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/plans",
    response_model=List[PlanResponse],
    summary="List all active billing plans (public)",
)
def list_plans(db: Session = Depends(get_db)):
    """Public endpoint -- returns all active teacher billing plans with pricing."""
    plans = db.query(Plan).filter(Plan.is_active == True).all()
    return [
        PlanResponse(
            id=p.id,
            name=p.name,
            slug=p.slug,
            price_monthly_paise=p.price_monthly_paise,
            price_annual_paise=p.price_annual_paise,
            price_monthly_rupees=p.price_monthly_paise / 100,
            price_annual_rupees=p.price_annual_paise / 100,
            description=p.description,
            features=p.features if isinstance(p.features, list) else json.loads(p.features or "[]"),
            is_active=p.is_active,
        )
        for p in plans
    ]


# â”€â”€ Current Subscription â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/me",
    response_model=SubscriptionStatusResponse,
    summary="Get own subscription status",
)
def get_my_subscription(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Returns the current subscription status for the logged-in user."""
    if current_user.role == "student":
        return SubscriptionStatusResponse(
            is_subscribed=True,
            status="free",
            plan_name="Student Free Access",
            plan_slug="student-free",
            billing_cycle=None,
            active_subscription_count=0,
        )

    subscription = db.query(Subscription, Plan).join(
        Plan, Plan.id == Subscription.plan_id
    ).filter(
        and_(
            Subscription.user_id == current_user.id,
            Subscription.status.in_(["active", "past_due", "pending"]),
        )
    ).order_by(Subscription.created_at.desc()).first()

    active_effective_subs = get_effective_active_subscriptions(current_user.id, db)

    if not subscription:
        return SubscriptionStatusResponse(
            is_subscribed=False,
            active_subscription_count=len(active_effective_subs),
        )

    sub, plan = subscription
    effective_active = get_effective_active_subscription(current_user.id, db)
    return SubscriptionStatusResponse(
        is_subscribed=effective_active is not None,
        status=sub.status,
        plan_name=plan.name,
        plan_slug=plan.slug,
        billing_cycle=sub.billing_cycle,
        current_period_start=sub.current_period_start,
        current_period_end=sub.current_period_end,
        cancel_at_period_end=sub.cancel_at_period_end,
        razorpay_subscription_id=sub.razorpay_subscription_id,
        active_subscription_count=len(active_effective_subs),
    )


# â”€â”€ Create Subscription â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post(
    "/create",
    response_model=CreateSubscriptionResponse,
    status_code=201,
    summary="Create a subscription and get payment link",
)
def create_subscription(
    payload: CreateSubscriptionRequest,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    Create a Razorpay subscription for the selected plan.
    Returns a payment_link -- frontend redirects the user to this URL.
    Subscription is marked 'pending' until webhook confirms payment.
    This billing flow is available only for teacher accounts.

    Note: Requires RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env.
    In development without Razorpay keys, returns a mock response.
    """
    logger.info(
        "create_subscription user_id=%s plan_id=%s billing_cycle=%s",
        current_user.id,
        payload.plan_id,
        payload.billing_cycle,
    )
    if current_user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teacher accounts can purchase billing plans.")

    # Validate plan
    plan = db.query(Plan).filter(
        and_(Plan.id == payload.plan_id, Plan.is_active == True)
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")
    if plan.slug != "teacher-platform":
        raise HTTPException(status_code=400, detail="Only teacher platform billing plan is supported.")

    ongoing_subs = db.query(Subscription).filter(
        and_(
            Subscription.user_id == current_user.id,
            Subscription.status.in_(("active", "pending", "past_due")),
        )
    ).order_by(Subscription.created_at.desc()).all()

    if ongoing_subs:
        raise HTTPException(
            status_code=409,
            detail="You already have an active or pending billing subscription.",
        )

    # Pick correct Razorpay plan ID and amount
    if payload.billing_cycle == "annual":
        razorpay_plan_id = plan.razorpay_plan_id_annual
        amount_paise = plan.price_annual_paise
        total_count = 1  # Annual = 1 payment
    else:
        razorpay_plan_id = plan.razorpay_plan_id_monthly
        amount_paise = plan.price_monthly_paise
        total_count = 12  # Monthly = up to 12 cycles

    has_razorpay_keys = bool(razorpay_service.settings.razorpay_key_id and razorpay_service.settings.razorpay_key_secret)
    if has_razorpay_keys and not razorpay_plan_id:
        try:
            period = "yearly" if payload.billing_cycle == "annual" else "monthly"
            created_plan = razorpay_service.create_plan(
                name=f"tamgam {plan.name} ({payload.billing_cycle})",
                amount_paise=amount_paise,
                period=period,
                interval=1,
                description=plan.description or f"{plan.name} {payload.billing_cycle} subscription",
            )
            razorpay_plan_id = created_plan.get("id")
            if payload.billing_cycle == "annual":
                plan.razorpay_plan_id_annual = razorpay_plan_id
            else:
                plan.razorpay_plan_id_monthly = razorpay_plan_id
            db.flush()
            logger.info(
                "create_subscription razorpay_plan_bootstrapped plan_slug=%s billing_cycle=%s razorpay_plan_id=%s",
                plan.slug,
                payload.billing_cycle,
                razorpay_plan_id,
            )
        except Exception as e:
            logger.exception(
                "create_subscription razorpay_plan_bootstrap_failed plan_slug=%s billing_cycle=%s error=%s",
                plan.slug,
                payload.billing_cycle,
                _format_razorpay_error(e),
            )
            if settings.app_env == "production":
                raise HTTPException(
                    status_code=502,
                    detail=f"Failed to bootstrap Razorpay plan: {_format_razorpay_error(e)}",
                )

    if settings.app_env == "production":
        if not has_razorpay_keys:
            raise HTTPException(status_code=503, detail="Razorpay keys are not configured on the server.")
        if not razorpay_plan_id:
            raise HTTPException(
                status_code=503,
                detail=f"Razorpay plan mapping missing for {plan.slug} ({payload.billing_cycle}).",
            )

    # Development mode fallback -- no Razorpay keys/plan mapping configured
    if not razorpay_plan_id or not has_razorpay_keys:
        # Create a pending subscription record with a mock ID
        mock_razorpay_id = f"sub_mock_{current_user.id!s:.8}"
        subscription = Subscription(
            user_id=current_user.id,
            plan_id=plan.id,
            status="pending",
            billing_cycle=payload.billing_cycle,
            razorpay_subscription_id=mock_razorpay_id,
        )
        db.add(subscription)
        db.commit()
        logger.info(
            "create_subscription mock_created user_id=%s subscription_id=%s",
            current_user.id,
            subscription.id,
        )
        return CreateSubscriptionResponse(
            subscription_id=subscription.id,
            razorpay_subscription_id=mock_razorpay_id,
            payment_link="https://rzp.io/mock-checkout (configure Razorpay keys to use real checkout)",
            plan_name=plan.name,
            amount_paise=amount_paise,
            billing_cycle=payload.billing_cycle,
        )

    # Production -- create real Razorpay subscription
    try:
        rz_sub = razorpay_service.create_subscription(
            razorpay_plan_id=razorpay_plan_id,
            total_count=total_count,
        )
    except Exception as e:
        logger.exception(
            "create_subscription razorpay_failed user_id=%s plan_id=%s error=%s",
            current_user.id,
            plan.id,
            _format_razorpay_error(e),
        )
        raise HTTPException(
            status_code=502,
            detail=f"Failed to create Razorpay subscription: {_format_razorpay_error(e)}",
        )

    subscription = Subscription(
        user_id=current_user.id,
        plan_id=plan.id,
        status="pending",
        billing_cycle=payload.billing_cycle,
        razorpay_subscription_id=rz_sub["id"],
    )
    db.add(subscription)
    db.commit()
    logger.info(
        "create_subscription created user_id=%s subscription_id=%s razorpay_subscription_id=%s",
        current_user.id,
        subscription.id,
        rz_sub["id"],
    )

    return CreateSubscriptionResponse(
        subscription_id=subscription.id,
        razorpay_subscription_id=rz_sub["id"],
        payment_link=rz_sub.get("short_url", ""),
        plan_name=plan.name,
        amount_paise=amount_paise,
        billing_cycle=payload.billing_cycle,
    )


# â”€â”€ Cancel Subscription â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post(
    "/cancel",
    response_model=CancelSubscriptionResponse,
    summary="Cancel subscription at period end",
)
def cancel_subscription(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    Cancel the active subscription.
    Access remains until current_period_end.
    Sets cancel_at_period_end=True -- Razorpay webhook will set status to cancelled.
    """
    logger.info("cancel_subscription user_id=%s", current_user.id)
    if current_user.role != "teacher":
        raise HTTPException(status_code=403, detail="Only teacher accounts can cancel billing plans.")
    subscription = get_effective_active_subscription(current_user.id, db)

    if not subscription:
        raise HTTPException(status_code=404, detail="No active subscription found.")

    if subscription.cancel_at_period_end:
        raise HTTPException(status_code=409, detail="Subscription is already set to cancel.")

    # Cancel in Razorpay if real subscription
    cancelled_via_razorpay = False
    local_warning = ""
    if subscription.razorpay_subscription_id and not subscription.razorpay_subscription_id.startswith("sub_mock_"):
        has_keys = bool(settings.razorpay_key_id and settings.razorpay_key_secret)
        if not has_keys and settings.app_env == "production":
            raise HTTPException(
                status_code=503,
                detail="Razorpay is not configured on server (missing key id/secret).",
            )

        if has_keys:
            try:
                logger.info(
                    "cancel_subscription razorpay_attempt user_id=%s razorpay_subscription_id=%s",
                    current_user.id,
                    subscription.razorpay_subscription_id,
                )
                razorpay_service.cancel_subscription(
                    subscription.razorpay_subscription_id,
                    cancel_at_cycle_end=True,
                )
                cancelled_via_razorpay = True
            except Exception as e:
                err = _format_razorpay_error(e)
                logger.exception(
                    "cancel_subscription razorpay_failed user_id=%s razorpay_subscription_id=%s error=%s",
                    current_user.id,
                    subscription.razorpay_subscription_id,
                    err,
                )
                if settings.app_env == "production":
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            "Failed to cancel with Razorpay: "
                            f"{err}. subscription_id={subscription.razorpay_subscription_id}"
                        ),
                    )
                local_warning = err

    subscription.cancel_at_period_end = True
    db.commit()
    logger.info(
        "cancel_subscription scheduled user_id=%s subscription_id=%s cancel_at_period_end=%s current_period_end=%s",
        current_user.id,
        subscription.id,
        subscription.cancel_at_period_end,
        subscription.current_period_end,
    )

    msg = "Subscription will be cancelled at the end of the current billing period."
    if subscription.razorpay_subscription_id and not subscription.razorpay_subscription_id.startswith("sub_mock_"):
        if cancelled_via_razorpay:
            msg = "Cancellation confirmed with Razorpay. Subscription remains active until period end."
        elif settings.app_env != "production":
            msg = (
                "Cancellation scheduled locally (dev mode). Configure Razorpay keys to sync cancellation upstream."
            )
            if local_warning:
                msg += f" Razorpay warning: {local_warning}"

    return CancelSubscriptionResponse(
        message=msg,
        cancel_at_period_end=True,
        current_period_end=subscription.current_period_end,
    )


# â”€â”€ Payment History â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get(
    "/payments",
    response_model=List[PaymentResponse],
    summary="Get payment history",
)
def list_payments(
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Returns all payment records for the current user, newest first."""
    payments = db.query(Payment).filter(
        Payment.user_id == current_user.id
    ).order_by(Payment.created_at.desc()).all()

    return [
        PaymentResponse(
            id=p.id,
            amount_paise=p.amount_paise,
            amount_rupees=p.amount_paise / 100,
            gst_paise=p.gst_paise,
            status=p.status,
            razorpay_payment_id=p.razorpay_payment_id,
            razorpay_order_id=p.razorpay_order_id,
            paid_at=getattr(p, "paid_at", None),
            created_at=p.created_at,
        )
        for p in payments
    ]


# â”€â”€ Webhook â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post(
    "/webhook",
    response_model=WebhookResponse,
    summary="Razorpay webhook handler",
    include_in_schema=False,  # Hide from public docs -- internal endpoint
)
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(None),
    db: Session = Depends(get_db),
):
    """
    Handles Razorpay webhook events.
    Must be registered in Razorpay dashboard:
      URL: https://your-domain/api/v1/subscriptions/webhook
      Events: subscription.*, payment.captured

    CRITICAL: Verify signature before processing any event.
    Status is NEVER set manually -- only updated via this webhook.
    """
    payload_body = await request.body()
    logger.info("razorpay_webhook received content_length=%s", len(payload_body))

    # Verify webhook signature (mandatory in production and when webhook secret is configured).
    require_signature = settings.app_env == "production" or bool(settings.razorpay_webhook_secret)
    if require_signature and not x_razorpay_signature:
        raise HTTPException(status_code=400, detail="Missing webhook signature.")
    if x_razorpay_signature:
        if not razorpay_service.verify_webhook_signature(payload_body, x_razorpay_signature):
            raise HTTPException(status_code=400, detail="Invalid webhook signature.")

    try:
        event = json.loads(payload_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    event_type = event.get("event")
    logger.info("razorpay_webhook event=%s", event_type)
    if event_type not in razorpay_service.HANDLED_EVENTS:
        return WebhookResponse(received=True)

    # Extract subscription ID from event payload
    payload = event.get("payload", {})

    # Handle subscription events
    if event_type.startswith("subscription."):
        rz_sub = payload.get("subscription", {}).get("entity", {})
        rz_sub_id = rz_sub.get("id")
        if not rz_sub_id:
            return WebhookResponse(received=True)

        subscription = db.query(Subscription).filter(
            Subscription.razorpay_subscription_id == rz_sub_id
        ).first()
        if not subscription:
            return WebhookResponse(received=True)

        # Map and update status
        new_status = razorpay_service.map_status(rz_sub.get("status", ""))
        subscription.status = new_status

        # Update billing period dates
        if rz_sub.get("current_start"):
            subscription.current_period_start = datetime.fromtimestamp(
                rz_sub["current_start"], tz=timezone.utc
            )
        if rz_sub.get("current_end"):
            subscription.current_period_end = datetime.fromtimestamp(
                rz_sub["current_end"], tz=timezone.utc
            )

        # Mark cancelled if completed/cancelled
        if new_status == "cancelled":
            subscription.cancel_at_period_end = False

        db.commit()
        logger.info(
            "razorpay_webhook subscription_updated razorpay_subscription_id=%s status=%s",
            rz_sub_id,
            subscription.status,
        )

    # Handle payment.captured -- record payment
    elif event_type == "payment.captured":
        rz_payment = payload.get("payment", {}).get("entity", {})
        rz_sub_id = rz_payment.get("subscription_id")
        if rz_sub_id:
            subscription = db.query(Subscription).filter(
                Subscription.razorpay_subscription_id == rz_sub_id
            ).first()
            if not subscription:
                return WebhookResponse(received=True)

            # Check if we already recorded this payment
            existing_payment = db.query(Payment).filter(
                Payment.razorpay_payment_id == rz_payment.get("id")
            ).first()
            if existing_payment:
                return WebhookResponse(received=True)

            amount_paise = rz_payment.get("amount", 0)
            # GST: 18% of base amount
            gst_paise = int(amount_paise * 18 / 118)  # GST is already included in amount

            payment = Payment(
                user_id=subscription.user_id,
                subscription_id=subscription.id,
                amount_paise=amount_paise,
                gst_paise=gst_paise,
                status="captured",
                razorpay_payment_id=rz_payment.get("id"),
                razorpay_order_id=rz_payment.get("order_id"),
                webhook_payload=rz_payment,
            )
            db.add(payment)
            db.commit()
            logger.info(
                "razorpay_webhook payment_recorded razorpay_payment_id=%s subscription_id=%s amount_paise=%s",
                payment.razorpay_payment_id,
                payment.subscription_id,
                payment.amount_paise,
            )
        else:
            _handle_batch_join_payment_captured(rz_payment, db)
            db.commit()
            logger.info(
                "razorpay_webhook batch_payment_processed razorpay_payment_id=%s",
                rz_payment.get("id"),
            )

    return WebhookResponse(received=True)

