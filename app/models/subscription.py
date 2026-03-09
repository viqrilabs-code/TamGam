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
    Seeded via init_db.py â€” not user-created.
    """
    __tablename__ = "plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    name = Column(String(50), unique=True, nullable=False)   # Basic | Standard | Pro
    slug = Column(String(50), unique=True, nullable=False)   # basic | standard | pro

    # â”€â”€ Pricing (paise â€” smallest INR unit, like cents) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    price_monthly_paise = Column(Integer, nullable=False)    # 49900 = â‚¹499
    price_annual_paise = Column(Integer, nullable=False)     # 10 months price for annual

    # â”€â”€ Features â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    subjects_allowed = Column(Integer, nullable=False)       # 1 | 3 | -1 (unlimited)
    description = Column(Text, nullable=True)
    features = Column(JSONB, nullable=True)                  # JSON list of feature strings

    # â”€â”€ Razorpay Plan IDs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    razorpay_plan_id_monthly = Column(String(255), nullable=True)
    razorpay_plan_id_annual = Column(String(255), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # â”€â”€ Relationships â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    subscriptions = relationship("Subscription", back_populates="plan")

    def __repr__(self) -> str:
        return f"<Plan name={self.name} monthly=â‚¹{self.price_monthly_paise // 100}>"


class Subscription(Base):
    """
    Billing subscription record (primarily used for teacher billing).
    Status is mirrored from Razorpay webhooks â€” never set manually.
    """
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id = Column(
        UUID(as_uuid=True), ForeignKey("plans.id"), nullable=False
    )

    # â”€â”€ Status â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Mirrors Razorpay subscription states
    status = Column(
        Enum(
            "pending",    # Created, payment not yet completed
            "active",     # Paid and active (â­ mark shown)
            "past_due",   # Payment retry / failed states
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

    # â”€â”€ Razorpay IDs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    razorpay_subscription_id = Column(String(255), unique=True, nullable=True, index=True)
    razorpay_customer_id = Column(String(255), nullable=True)

    # â”€â”€ Dates â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    current_period_start = Column(DateTime(timezone=True), nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end = Column(Boolean, nullable=False, default=False)
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

    # â”€â”€ Relationships â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    user = relationship("User", back_populates="subscriptions")
    plan = relationship("Plan", back_populates="subscriptions")
    payments = relationship("Payment", back_populates="subscription")

    def __repr__(self) -> str:
        return f"<Subscription user={self.user_id} plan={self.plan_id} status={self.status}>"


class Payment(Base):
    """
    Immutable audit trail for every payment event.
    Written by Razorpay webhook handler â€” never modified after creation.
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

    # â”€â”€ Amount â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    amount_paise = Column(Integer, nullable=False)           # Amount in paise
    gst_paise = Column(Integer, nullable=False, default=0)   # 18% GST
    currency = Column(String(3), nullable=False, default="INR")

    # â”€â”€ Status â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    status = Column(
        Enum("captured", "failed", "refunded", name="payment_status_enum"),
        nullable=False,
    )

    # â”€â”€ Razorpay â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    razorpay_payment_id = Column(String(255), unique=True, nullable=True, index=True)
    razorpay_order_id = Column(String(255), nullable=True)
    razorpay_signature = Column(String(512), nullable=True)  # HMAC for verification
    payment_method = Column(String(50), nullable=True)       # upi | card | netbanking | wallet

    # â”€â”€ Raw event (for debugging / disputes) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    webhook_payload = Column(JSONB, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # â”€â”€ Relationships â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    subscription = relationship("Subscription", back_populates="payments")

    def __repr__(self) -> str:
        return f"<Payment id={self.id} amount=â‚¹{self.amount_paise // 100} status={self.status}>"

