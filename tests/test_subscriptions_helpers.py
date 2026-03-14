from app.api.v1.endpoints import subscriptions
import pytest
from fastapi import HTTPException
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4


def test_teacher_commission_rate_brackets():
    assert subscriptions._teacher_commission_rate(100_00) == 5.0
    assert subscriptions._teacher_commission_rate(30_000_00) == 5.0
    assert subscriptions._teacher_commission_rate(80_000_00) == 5.0


def test_format_razorpay_error():
    class E(Exception):
        status_code = 400
        code = "BAD"
        reason = "invalid"

    msg = subscriptions._format_razorpay_error(E("oops"))
    assert "oops" in msg
    assert "status_code=400" in msg
    assert "code=BAD" in msg


def test_enforce_single_plan_topups_allows_empty_and_same_plan():
    plan_id = uuid4()
    assert subscriptions._enforce_single_plan_topups(
        requested_plan_id=plan_id,
        ongoing_subscriptions=[],
    ) is None

    locked = subscriptions._enforce_single_plan_topups(
        requested_plan_id=plan_id,
        ongoing_subscriptions=[SimpleNamespace(plan_id=plan_id)],
    )
    assert locked == plan_id


def test_enforce_single_plan_topups_rejects_mixed_or_different():
    p1 = uuid4()
    p2 = uuid4()
    with pytest.raises(HTTPException):
        subscriptions._enforce_single_plan_topups(
            requested_plan_id=p1,
            ongoing_subscriptions=[
                SimpleNamespace(plan_id=p1),
                SimpleNamespace(plan_id=p2),
            ],
        )

    with pytest.raises(HTTPException):
        subscriptions._enforce_single_plan_topups(
            requested_plan_id=p2,
            ongoing_subscriptions=[SimpleNamespace(plan_id=p1)],
        )


class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.refreshes = 0

    def commit(self):
        self.commits += 1

    def refresh(self, _obj):
        self.refreshes += 1


def test_sync_subscription_from_razorpay_updates_local_status_and_periods(monkeypatch):
    sub = SimpleNamespace(
        razorpay_subscription_id="sub_live_123",
        status="pending",
        current_period_start=None,
        current_period_end=None,
        cancel_at_period_end=True,
    )
    db = _FakeDB()
    start_epoch = 1_710_000_000
    end_epoch = 1_712_592_000

    monkeypatch.setattr(subscriptions.settings, "razorpay_key_id", "rzp_live_test")
    monkeypatch.setattr(subscriptions.settings, "razorpay_key_secret", "secret_test")
    monkeypatch.setattr(
        subscriptions.razorpay_service,
        "fetch_subscription",
        lambda _id: {
            "id": _id,
            "status": "active",
            "current_start": start_epoch,
            "current_end": end_epoch,
        },
    )

    changed = subscriptions._sync_subscription_from_razorpay(sub, db)

    assert changed is True
    assert sub.status == "active"
    assert sub.current_period_start == datetime.fromtimestamp(start_epoch, tz=timezone.utc)
    assert sub.current_period_end == datetime.fromtimestamp(end_epoch, tz=timezone.utc)
    assert db.commits == 1
    assert db.refreshes == 1


def test_sync_subscription_from_razorpay_ignores_mock_ids(monkeypatch):
    sub = SimpleNamespace(
        razorpay_subscription_id="sub_mock_abc",
        status="pending",
        current_period_start=None,
        current_period_end=None,
        cancel_at_period_end=False,
    )
    db = _FakeDB()

    called = {"fetch": 0}

    def _fetch(_id):
        called["fetch"] += 1
        return {"id": _id, "status": "active"}

    monkeypatch.setattr(subscriptions.settings, "razorpay_key_id", "rzp_live_test")
    monkeypatch.setattr(subscriptions.settings, "razorpay_key_secret", "secret_test")
    monkeypatch.setattr(subscriptions.razorpay_service, "fetch_subscription", _fetch)

    changed = subscriptions._sync_subscription_from_razorpay(sub, db)

    assert changed is False
    assert called["fetch"] == 0
    assert db.commits == 0
    assert db.refreshes == 0
