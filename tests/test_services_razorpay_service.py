import hashlib
import hmac

import pytest

from app.services import razorpay_service as rs


def test_map_status_known_and_unknown():
    assert rs.map_status("active") == "active"
    assert rs.map_status("halted") == "past_due"
    assert rs.map_status("unknown_status") == "pending"


def test_verify_webhook_signature_without_secret(monkeypatch):
    monkeypatch.setattr(rs.settings, "razorpay_webhook_secret", "")
    assert rs.verify_webhook_signature(b"{}", "anything") is True


def test_verify_webhook_signature_with_secret(monkeypatch):
    secret = "abc123"
    body = b'{"event":"x"}'
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    monkeypatch.setattr(rs.settings, "razorpay_webhook_secret", secret)
    assert rs.verify_webhook_signature(body, sig) is True
    assert rs.verify_webhook_signature(body, "bad") is False


def test_create_plan_validates_period():
    with pytest.raises(ValueError):
        rs.create_plan(name="n", amount_paise=100, period="invalid")


def test_create_plan_calls_client(monkeypatch):
    captured = {}

    class PlanAPI:
        @staticmethod
        def create(payload):
            captured["payload"] = payload
            return {"id": "plan_1"}

    class Client:
        plan = PlanAPI()

    monkeypatch.setattr(rs, "get_razorpay_client", lambda: Client())
    out = rs.create_plan(name="Core", amount_paise=69900, period="monthly", description="d")
    assert out["id"] == "plan_1"
    assert captured["payload"]["item"]["amount"] == 69900
    assert captured["payload"]["period"] == "monthly"


def test_create_fetch_cancel_subscription(monkeypatch):
    calls = {"create": None, "fetch": None, "cancel": None}

    class SubscriptionAPI:
        @staticmethod
        def create(payload):
            calls["create"] = payload
            return {"id": "sub_123"}

        @staticmethod
        def fetch(subscription_id):
            calls["fetch"] = subscription_id
            return {"id": subscription_id}

        @staticmethod
        def cancel(subscription_id, payload):
            calls["cancel"] = (subscription_id, payload)
            return {"id": subscription_id, "status": "cancelled"}

    class Client:
        subscription = SubscriptionAPI()

    monkeypatch.setattr(rs, "get_razorpay_client", lambda: Client())

    created = rs.create_subscription("plan_1", total_count=6)
    fetched = rs.fetch_subscription("sub_123")
    cancelled = rs.cancel_subscription("sub_123", cancel_at_cycle_end=True)

    assert created["id"] == "sub_123"
    assert fetched["id"] == "sub_123"
    assert cancelled["status"] == "cancelled"
    assert calls["create"]["plan_id"] == "plan_1"
    assert calls["fetch"] == "sub_123"
    assert calls["cancel"][1]["cancel_at_cycle_end"] == 1


def test_create_contact_fund_account_and_payout(monkeypatch):
    calls = {"contact": None, "fund": None, "payout": None}

    class ContactAPI:
        @staticmethod
        def create(payload):
            calls["contact"] = payload
            return {"id": "cont_1"}

    class FundAPI:
        @staticmethod
        def create(payload):
            calls["fund"] = payload
            return {"id": "fa_1"}

    class PayoutAPI:
        @staticmethod
        def create(payload):
            calls["payout"] = payload
            return {"id": "pout_1", "status": "queued"}

    class Client:
        contact = ContactAPI()
        fund_account = FundAPI()
        payout = PayoutAPI()

    monkeypatch.setattr(rs, "get_razorpay_client", lambda: Client())

    contact = rs.create_contact(name="Teacher", email="t@example.com", reference_id="t1")
    fund_upi = rs.create_fund_account_upi(contact_id="cont_1", name="Teacher", vpa="abc@upi")
    fund_bank = rs.create_fund_account_bank(
        contact_id="cont_1",
        name="Teacher",
        ifsc="HDFC0001",
        account_number="1234567890",
    )
    payout = rs.create_payout(
        source_account_number="123412341234",
        fund_account_id="fa_1",
        amount_paise=120000,
        reference_id="ref_1",
        narration="Monthly payout",
    )

    assert contact["id"] == "cont_1"
    assert fund_upi["id"] == "fa_1"
    assert fund_bank["id"] == "fa_1"
    assert payout["id"] == "pout_1"
    assert calls["contact"]["reference_id"] == "t1"
    assert calls["fund"]["account_type"] in {"vpa", "bank_account"}
    assert calls["payout"]["amount"] == 120000
    assert calls["payout"]["fund_account_id"] == "fa_1"
