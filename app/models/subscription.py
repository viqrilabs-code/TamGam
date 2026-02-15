# app/models/subscription.py
# Subscription plans, active subscriptions, and payment audit trail
# Razorpay manages the recurring billing; we mirror state via webhooks

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class Plan(Base):
    """
    Subscription plan catalogue.
    Seeded via init_db.py — not user-created.
    """
    __tablename__ = "plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    name = Column(String(50), unique=True, nullable=False)   # Basic | Standard | Pro
    slug = Column(String(50), unique=True, nullable=False)   # basic | standard | pro

    # ── Pricing (paise — smallest INR unit, like cents) ───────────────────────
    price_monthly_paise = Column(Integer, nullable=False)    # 49900 = ₹499
    price_annual_paise = Column(Integer, nullable=False)     # 10 months price for annual

    # ── Features ──────────────────────────────────────────────────────────────
    subjects_allowed = Column(Integer, nullable=False)       # 1 | 3 | -1 (unlimited)
    description = Column(Text, nullable=True)
    features = Column(JSONB, nullable=True)                  # JSON list of feature strings

    # ── Razorpay Plan IDs ──────────────────────────────────────────────────────
    razorpay_plan_id_monthly = Column(String(255), nullable=True)
    razorpay_plan_id_annual = Column(String(255), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    subscriptions = relationship("Subscription", back_populates="plan")

    def __repr__(self) -> str:
        return f"<Plan name={self.name} monthly=₹{self.price_monthly_paise // 100}>"


class Subscription(Base):
    """
    One active subscription per student.
    Status is mirrored from Razorpay webhooks — never set manually.
    """
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id = Column(
        UUID(as_uuid=True), ForeignKey("plans.id"), nullable=False
    )

    # ── Status ────────────────────────────────────────────────────────────────
    # Mirrors Razorpay subscription states
    status = Column(
        Enum(
            "pending",    # Created, payment not yet completed
            "active",     # Paid and active (⭐ mark shown)
            "paused",     # Razorpay halted (payment failed, retrying)
            "cancelled",  # Student cancelled
            "expired",    # End of subscription period
            name="subscription_status_enum",
        ),
        nullable=False,
        default="pending",
        index=True,
    )

    billing_cycle = Column(
        Enum("monthly", "annual", name="billing_cycle_enum"),
        nullable=False,
        default="monthly",
    )

    # ── Razorpay IDs ──────────────────────────────────────────────────────────
    razorpay_subscription_id = Column(String(255), unique=True, nullable=True, index=True)
    razorpay_customer_id = Column(String(255), nullable=True)

    # ── Dates ─────────────────────────────────────────────────────────────────
    current_period_start = Column(DateTime(timezone=True), nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)
    trial_end = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user = relationship("User", back_populates="subscriptions")
    plan = relationship("Plan", back_populates="subscriptions")
    payments = relationship("Payment", back_populates="subscription")

    def __repr__(self) -> str:
        return f"<Subscription user={self.user_id} plan={self.plan_id} status={self.status}>"


class Payment(Base):
    """
    Immutable audit trail for every payment event.
    Written by Razorpay webhook handler — never modified after creation.
    """
    __tablename__ = "payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_id = Column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # ── Amount ────────────────────────────────────────────────────────────────
    amount_paise = Column(Integer, nullable=False)           # Amount in paise
    gst_paise = Column(Integer, nullable=False, default=0)   # 18% GST
    currency = Column(String(3), nullable=False, default="INR")

    # ── Status ────────────────────────────────────────────────────────────────
    status = Column(
        Enum("captured", "failed", "refunded", name="payment_status_enum"),
        nullable=False,
    )

    # ── Razorpay ──────────────────────────────────────────────────────────────
    razorpay_payment_id = Column(String(255), unique=True, nullable=True, index=True)
    razorpay_order_id = Column(String(255), nullable=True)
    razorpay_signature = Column(String(512), nullable=True)  # HMAC for verification
    payment_method = Column(String(50), nullable=True)       # upi | card | netbanking | wallet

    # ── Raw event (for debugging / disputes) ──────────────────────────────────
    webhook_payload = Column(JSONB, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    subscription = relationship("Subscription", back_populates="payments")

    def __repr__(self) -> str:
        return f"<Payment id={self.id} amount=₹{self.amount_paise // 100} status={self.status}>"