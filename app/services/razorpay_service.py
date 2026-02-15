# app/services/razorpay_service.py
# Razorpay API wrapper
# Handles subscription creation, status sync, and webhook verification
#
# Razorpay subscription flow:
#   1. Create subscription via API -> get razorpay_subscription_id
#   2. Frontend opens hosted checkout with that ID
#   3. Razorpay sends webhook events as payment status changes
#   4. We update our DB subscription status from webhook events

import hashlib
import hmac
import json
from typing import Optional

import razorpay

from app.core.config import settings


def get_razorpay_client() -> razorpay.Client:
    """Return authenticated Razorpay client."""
    return razorpay.Client(
        auth=(settings.razorpay_key_id, settings.razorpay_key_secret)
    )


def create_subscription(
    razorpay_plan_id: str,
    total_count: int = 12,  # Number of billing cycles (12 = 1 year for monthly)
    customer_notify: int = 1,
) -> dict:
    """
    Create a Razorpay subscription.

    Args:
        razorpay_plan_id: The Razorpay plan ID (from plans table)
        total_count: How many billing cycles before auto-cancel
        customer_notify: 1 = Razorpay sends email to customer

    Returns:
        Razorpay subscription object with 'id', 'status', 'short_url' etc.
    """
    client = get_razorpay_client()
    subscription = client.subscription.create({
        "plan_id": razorpay_plan_id,
        "total_count": total_count,
        "customer_notify": customer_notify,
        "notify_info": {
            "notify_phone": 0,
            "notify_email": 1,
        },
    })
    return subscription


def fetch_subscription(razorpay_subscription_id: str) -> dict:
    """Fetch current subscription state from Razorpay."""
    client = get_razorpay_client()
    return client.subscription.fetch(razorpay_subscription_id)


def cancel_subscription(
    razorpay_subscription_id: str,
    cancel_at_cycle_end: bool = True,
) -> dict:
    """
    Cancel a Razorpay subscription.
    cancel_at_cycle_end=True means it stays active until period end.
    """
    client = get_razorpay_client()
    return client.subscription.cancel(
        razorpay_subscription_id,
        {"cancel_at_cycle_end": 1 if cancel_at_cycle_end else 0},
    )


def verify_webhook_signature(
    payload_body: bytes,
    razorpay_signature: str,
) -> bool:
    """
    Verify Razorpay webhook signature using HMAC-SHA256.
    Must be called before processing any webhook event.

    Args:
        payload_body: Raw request body bytes
        razorpay_signature: Value of X-Razorpay-Signature header

    Returns:
        True if signature is valid, False otherwise
    """
    if not settings.razorpay_webhook_secret:
        # In development without webhook secret, skip verification
        return True

    expected = hmac.new(
        settings.razorpay_webhook_secret.encode("utf-8"),
        payload_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, razorpay_signature)


# ── Razorpay Status -> Our Status Mapping ────────────────────────────────────
# Razorpay subscription statuses:
#   created      -> pending payment
#   authenticated -> payment authenticated
#   active       -> subscription active, payments succeeding
#   pending      -> payment pending/failed, will retry
#   halted       -> too many failed retries
#   cancelled    -> cancelled by us or customer
#   completed    -> all billing cycles done
#   expired      -> plan expired

RAZORPAY_TO_OUR_STATUS = {
    "created": "pending",
    "authenticated": "pending",
    "active": "active",
    "pending": "past_due",
    "halted": "past_due",
    "cancelled": "cancelled",
    "completed": "cancelled",
    "expired": "cancelled",
}


def map_status(razorpay_status: str) -> str:
    """Map Razorpay subscription status to our internal status."""
    return RAZORPAY_TO_OUR_STATUS.get(razorpay_status, "pending")


# ── Webhook Event Types We Handle ────────────────────────────────────────────
HANDLED_EVENTS = {
    "subscription.activated",    # First payment succeeded -- mark active
    "subscription.charged",      # Recurring payment succeeded
    "subscription.pending",      # Payment failed, retrying
    "subscription.halted",       # Too many retries -- access revoked
    "subscription.cancelled",    # Cancelled
    "subscription.completed",    # All cycles done
    "payment.captured",          # Individual payment captured
}