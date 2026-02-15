# app/schemas/subscription.py
# Pydantic request/response models for subscription and payment endpoints

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel


# ── Plans ─────────────────────────────────────────────────────────────────────

class PlanResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    price_monthly_paise: int
    price_annual_paise: int
    price_monthly_rupees: float
    price_annual_rupees: float
    subjects_allowed: int        # -1 = unlimited
    description: Optional[str] = None
    features: Optional[List[str]] = None
    is_active: bool


# ── Subscription ──────────────────────────────────────────────────────────────

class SubscriptionStatusResponse(BaseModel):
    """Current subscription state for the student."""
    is_subscribed: bool
    status: Optional[str] = None      # active | past_due | cancelled | None
    plan_name: Optional[str] = None
    plan_slug: Optional[str] = None
    billing_cycle: Optional[str] = None   # monthly | annual
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False
    razorpay_subscription_id: Optional[str] = None


class CreateSubscriptionRequest(BaseModel):
    plan_id: UUID
    billing_cycle: str = "monthly"   # monthly | annual

    def model_post_init(self, __context) -> None:
        if self.billing_cycle not in ("monthly", "annual"):
            raise ValueError("billing_cycle must be 'monthly' or 'annual'")


class CreateSubscriptionResponse(BaseModel):
    """Returned after creating a subscription -- frontend opens the payment_link."""
    subscription_id: UUID              # Our DB record ID
    razorpay_subscription_id: str
    payment_link: str                  # Razorpay hosted checkout URL
    plan_name: str
    amount_paise: int
    billing_cycle: str


class CancelSubscriptionResponse(BaseModel):
    message: str
    cancel_at_period_end: bool
    current_period_end: Optional[datetime] = None


# ── Payments ──────────────────────────────────────────────────────────────────

class PaymentResponse(BaseModel):
    id: UUID
    amount_paise: int
    amount_rupees: float
    gst_paise: int
    status: str
    razorpay_payment_id: Optional[str] = None
    razorpay_order_id: Optional[str] = None
    paid_at: Optional[datetime] = None
    created_at: datetime


# ── Webhook ───────────────────────────────────────────────────────────────────

class WebhookResponse(BaseModel):
    received: bool = True


# ── Generic ───────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str