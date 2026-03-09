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
import logging
from typing import Optional

import razorpay

from app.core.config import settings
logger = logging.getLogger("tamgam.razorpay")


def get_razorpay_client() -> razorpay.Client:
    """Return authenticated Razorpay client."""
    logger.debug("Initializing Razorpay client configured=%s", bool(settings.razorpay_key_id and settings.razorpay_key_secret))
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
    logger.info("Razorpay create_subscription plan_id=%s total_count=%s", razorpay_plan_id, total_count)
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


def create_plan(
    *,
    name: str,
    amount_paise: int,
    period: str,
    interval: int = 1,
    description: Optional[str] = None,
) -> dict:
    """
    Create a Razorpay recurring plan.
    period must be one of: daily, weekly, monthly, yearly.
    """
    if period not in {"daily", "weekly", "monthly", "yearly"}:
        raise ValueError("period must be daily|weekly|monthly|yearly")
    client = get_razorpay_client()
    payload = {
        "period": period,
        "interval": interval,
        "item": {
            "name": name,
            "amount": int(amount_paise),
            "currency": "INR",
            "description": description or name,
        },
    }
    logger.info("Razorpay create_plan name=%s period=%s amount_paise=%s", name, period, amount_paise)
    return client.plan.create(payload)


def create_payment_link(
    *,
    amount_paise: int,
    description: str,
    customer_name: Optional[str] = None,
    customer_email: Optional[str] = None,
    callback_url: Optional[str] = None,
    notes: Optional[dict] = None,
) -> dict:
    client = get_razorpay_client()
    payload = {
        "amount": int(amount_paise),
        "currency": "INR",
        "description": description,
        "accept_partial": False,
        "notify": {"sms": False, "email": bool(customer_email)},
    }
    if customer_name or customer_email:
        payload["customer"] = {}
        if customer_name:
            payload["customer"]["name"] = customer_name
        if customer_email:
            payload["customer"]["email"] = customer_email
    if callback_url:
        payload["callback_url"] = callback_url
        payload["callback_method"] = "get"
    if notes:
        payload["notes"] = {str(k): str(v) for k, v in notes.items()}
    logger.info("Razorpay create_payment_link amount_paise=%s", amount_paise)
    return client.payment_link.create(payload)


def fetch_subscription(razorpay_subscription_id: str) -> dict:
    """Fetch current subscription state from Razorpay."""
    client = get_razorpay_client()
    logger.info("Razorpay fetch_subscription id=%s", razorpay_subscription_id)
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
    logger.info(
        "Razorpay cancel_subscription id=%s cancel_at_cycle_end=%s",
        razorpay_subscription_id,
        cancel_at_cycle_end,
    )
    return client.subscription.cancel(
        razorpay_subscription_id,
        {"cancel_at_cycle_end": 1 if cancel_at_cycle_end else 0},
    )


def create_contact(
    *,
    name: str,
    email: Optional[str] = None,
    contact: Optional[str] = None,
    reference_id: Optional[str] = None,
) -> dict:
    client = get_razorpay_client()
    payload = {"name": name, "type": "employee"}
    if email:
        payload["email"] = email
    if contact:
        payload["contact"] = contact
    if reference_id:
        payload["reference_id"] = reference_id
    logger.info("Razorpay create_contact reference_id=%s", reference_id)
    return client.contact.create(payload)


def create_fund_account_upi(
    *,
    contact_id: str,
    name: str,
    vpa: str,
) -> dict:
    client = get_razorpay_client()
    payload = {
        "contact_id": contact_id,
        "account_type": "vpa",
        "vpa": {
            "address": vpa,
        },
    }
    if name:
        payload["vpa"]["name"] = name
    logger.info("Razorpay create_fund_account_upi contact_id=%s", contact_id)
    return client.fund_account.create(payload)


def create_fund_account_bank(
    *,
    contact_id: str,
    name: str,
    ifsc: str,
    account_number: str,
) -> dict:
    client = get_razorpay_client()
    payload = {
        "contact_id": contact_id,
        "account_type": "bank_account",
        "bank_account": {
            "name": name,
            "ifsc": ifsc,
            "account_number": account_number,
        },
    }
    logger.info("Razorpay create_fund_account_bank contact_id=%s", contact_id)
    return client.fund_account.create(payload)


def create_payout(
    *,
    source_account_number: str,
    fund_account_id: str,
    amount_paise: int,
    reference_id: str,
    narration: str,
) -> dict:
    client = get_razorpay_client()
    payload = {
        "account_number": source_account_number,
        "fund_account_id": fund_account_id,
        "amount": int(amount_paise),
        "currency": "INR",
        "mode": "IMPS",
        "purpose": "payout",
        "queue_if_low_balance": True,
        "reference_id": reference_id,
        "narration": narration[:30] if narration else "tamgam payout",
    }
    logger.info("Razorpay create_payout reference_id=%s amount_paise=%s", reference_id, amount_paise)
    return client.payout.create(payload)


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
