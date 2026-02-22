# app/api/v1/endpoints/subscriptions.py
# Subscription and payment endpoints
#
# Flow:
#   1. Student picks a plan -> POST /create -> gets payment_link
#   2. Student pays on Razorpay hosted page
#   3. Razorpay sends webhook -> POST /webhook -> we update DB status
#   4. Student now has active subscription -> features unlocked

import json
import logging
from datetime import datetime, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import and_
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.config import settings
from app.core.dependencies import get_effective_active_subscription, require_login
from app.db.session import get_db
from app.models.subscription import Payment, Plan, Subscription
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


# ── Plans ─────────────────────────────────────────────────────────────────────

@router.get(
    "/plans",
    response_model=List[PlanResponse],
    summary="List all subscription plans (public)",
)
def list_plans(db: Session = Depends(get_db)):
    """Public endpoint -- returns all active plans with pricing."""
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
            subjects_allowed=p.subjects_allowed,
            description=p.description,
            features=p.features if isinstance(p.features, list) else json.loads(p.features or "[]"),
            is_active=p.is_active,
        )
        for p in plans
    ]


# ── Current Subscription ──────────────────────────────────────────────────────

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
    subscription = db.query(Subscription, Plan).join(
        Plan, Plan.id == Subscription.plan_id
    ).filter(
        and_(
            Subscription.user_id == current_user.id,
            Subscription.status.in_(["active", "past_due", "pending"]),
        )
    ).order_by(Subscription.created_at.desc()).first()

    if not subscription:
        return SubscriptionStatusResponse(is_subscribed=False)

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
    )


# ── Create Subscription ───────────────────────────────────────────────────────

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

    Note: Requires RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env.
    In development without Razorpay keys, returns a mock response.
    """
    logger.info(
        "create_subscription user_id=%s plan_id=%s billing_cycle=%s",
        current_user.id,
        payload.plan_id,
        payload.billing_cycle,
    )
    # Validate plan
    plan = db.query(Plan).filter(
        and_(Plan.id == payload.plan_id, Plan.is_active == True)
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")

    if plan.slug == "free":
        raise HTTPException(status_code=400, detail="Free plan does not require a subscription.")

    # Check no active subscription already
    existing = get_effective_active_subscription(current_user.id, db)
    if existing:
        raise HTTPException(
            status_code=409,
            detail="You already have an active subscription. Cancel it first to change plans.",
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

    # Development mode -- no Razorpay keys configured
    if not razorpay_plan_id or not razorpay_service.settings.razorpay_key_id:
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


# ── Cancel Subscription ───────────────────────────────────────────────────────

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


# ── Payment History ───────────────────────────────────────────────────────────

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
            paid_at=p.paid_at,
            created_at=p.created_at,
        )
        for p in payments
    ]


# ── Webhook ───────────────────────────────────────────────────────────────────

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

    # Verify webhook signature
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
        if not rz_sub_id:
            return WebhookResponse(received=True)

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
            razorpay_subscription_id=rz_sub_id,
            webhook_payload=rz_payment,
            paid_at=datetime.fromtimestamp(
                rz_payment.get("created_at", 0), tz=timezone.utc
            ) if rz_payment.get("created_at") else None,
        )
        db.add(payment)
        db.commit()
        logger.info(
            "razorpay_webhook payment_recorded razorpay_payment_id=%s subscription_id=%s amount_paise=%s",
            payment.razorpay_payment_id,
            payment.subscription_id,
            payment.amount_paise,
        )

    return WebhookResponse(received=True)
