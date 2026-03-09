from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import plan_limits


def test_assert_feature_available_requires_active_subscription(monkeypatch):
    monkeypatch.setattr(plan_limits, "get_limit_for_user", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as exc:
        plan_limits.assert_feature_available(uuid4(), "diya_questions_monthly", db=object())

    assert exc.value.status_code == 403
    assert exc.value.detail["redirect"] == "/plans.html"


def test_assert_feature_available_blocks_after_limit(monkeypatch):
    monkeypatch.setattr(plan_limits, "get_limit_for_user", lambda *args, **kwargs: 3)
    monkeypatch.setattr(plan_limits, "get_current_usage", lambda *args, **kwargs: 3)

    with pytest.raises(HTTPException) as exc:
        plan_limits.assert_feature_available(uuid4(), "student_notes_monthly", db=object())

    assert exc.value.status_code == 403
    assert "Monthly limit reached" in exc.value.detail["message"]


def test_assert_feature_available_allows_within_limit(monkeypatch):
    monkeypatch.setattr(plan_limits, "get_limit_for_user", lambda *args, **kwargs: 10)
    monkeypatch.setattr(plan_limits, "get_current_usage", lambda *args, **kwargs: 8)

    # Should not raise
    plan_limits.assert_feature_available(uuid4(), "community_interactions_monthly", db=object(), units=2)


def test_assert_feature_available_allows_unlimited(monkeypatch):
    monkeypatch.setattr(plan_limits, "get_limit_for_user", lambda *args, **kwargs: -1)

    # Should not raise
    plan_limits.assert_feature_available(uuid4(), "class_assessment_submissions_monthly", db=object(), units=999)

