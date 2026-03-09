from app.api.v1.endpoints import subscriptions
import pytest
from fastapi import HTTPException
from types import SimpleNamespace
from uuid import uuid4


def test_teacher_commission_rate_brackets():
    assert subscriptions._teacher_commission_rate(100_00) == 25.0
    assert subscriptions._teacher_commission_rate(30_000_00) == 20.0
    assert subscriptions._teacher_commission_rate(80_000_00) == 15.0


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
