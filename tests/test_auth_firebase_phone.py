from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import auth
from app.api.v1.endpoints.auth import (
    _build_phone_email,
    _ensure_teacher_profile_exists,
    _normalize_phone_number,
)
from app.models.teacher import TeacherProfile
from app.models.user import User


class _PendingTeacherProfileDB:
    def __init__(self, pending_profile):
        self.new = [pending_profile]
        self.added = []
        self.flush_count = 0

    def query(self, _model):
        raise AssertionError("Pending teacher profiles should short-circuit before querying.")

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        self.flush_count += 1


def test_normalize_phone_number_strips_non_digits():
    assert _normalize_phone_number("+91 98765-43210") == "+919876543210"


def test_normalize_phone_number_rejects_short_numbers():
    with pytest.raises(HTTPException) as exc:
        _normalize_phone_number("+12")
    assert exc.value.status_code == 400


def test_build_phone_email_from_e164():
    assert _build_phone_email("+919876543210") == "phone_919876543210@phone.tamgam.local"


def test_ensure_teacher_profile_exists_skips_pending_profile():
    user = User(
        id=uuid4(),
        email="teacher@example.com",
        hashed_password="hashed",
        full_name="Teacher One",
        role="teacher",
        auth_provider="email",
        is_active=True,
        is_email_verified=True,
    )
    pending_profile = TeacherProfile(user_id=user.id)
    db = _PendingTeacherProfileDB(pending_profile)

    _ensure_teacher_profile_exists(user, db)

    assert db.added == []
    assert db.flush_count == 0


def test_create_role_profile_for_teacher_uses_idempotent_helper(monkeypatch):
    user = User(
        id=uuid4(),
        email="teacher2@example.com",
        hashed_password="hashed",
        full_name="Teacher Two",
        role="teacher",
        auth_provider="email",
        is_active=True,
        is_email_verified=True,
    )
    calls = []

    def fake_ensure_teacher_profile_exists(arg_user, arg_db):
        calls.append((arg_user, arg_db))

    monkeypatch.setattr(auth, "_ensure_teacher_profile_exists", fake_ensure_teacher_profile_exists)
    db = object()

    auth._create_role_profile(user, db)

    assert calls == [(user, db)]
