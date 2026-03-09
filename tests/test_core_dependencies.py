from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core import dependencies


class _Q:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _DB:
    def __init__(self, mapping):
        self.mapping = mapping

    def query(self, model):
        return _Q(self.mapping.get(model))


@dataclass
class _Cred:
    credentials: str


def test_extract_user_from_token_happy_path(monkeypatch):
    user = SimpleNamespace(id=str(uuid4()), is_active=True, role="student")
    db = _DB({dependencies.User: user})
    monkeypatch.setattr(dependencies, "decode_token", lambda _t: {"type": "access", "sub": user.id})
    assert dependencies._extract_user_from_token(_Cred("abc"), db) is user


def test_extract_user_from_token_invalid_paths(monkeypatch):
    db = _DB({dependencies.User: None})
    assert dependencies._extract_user_from_token(None, db) is None
    monkeypatch.setattr(dependencies, "decode_token", lambda _t: None)
    assert dependencies._extract_user_from_token(_Cred("abc"), db) is None
    monkeypatch.setattr(dependencies, "decode_token", lambda _t: {"type": "refresh", "sub": "x"})
    assert dependencies._extract_user_from_token(_Cred("abc"), db) is None
    monkeypatch.setattr(dependencies, "decode_token", lambda _t: {"type": "access"})
    assert dependencies._extract_user_from_token(_Cred("abc"), db) is None


def test_require_login_and_community(monkeypatch):
    user = SimpleNamespace(id=uuid4(), role="student")
    monkeypatch.setattr(dependencies, "_extract_user_from_token", lambda *_: user)
    assert dependencies.require_login(credentials=None, db=None) is user
    assert dependencies.require_login_for_community(credentials=None, db=None) is user

    monkeypatch.setattr(dependencies, "_extract_user_from_token", lambda *_: None)
    with pytest.raises(HTTPException) as e1:
        dependencies.require_login(credentials=None, db=None)
    assert e1.value.status_code == 401

    with pytest.raises(HTTPException) as e2:
        dependencies.require_login_for_community(credentials=None, db=None)
    assert e2.value.status_code == 401
    assert e2.value.detail["redirect"] == "/signup"


def test_require_subscription(monkeypatch):
    teacher = SimpleNamespace(id=uuid4(), role="teacher")
    monkeypatch.setattr(dependencies, "_extract_user_from_token", lambda *_: teacher)
    assert dependencies.require_subscription(credentials=None, db=None) is teacher

    student = SimpleNamespace(id=uuid4(), role="student")
    monkeypatch.setattr(dependencies, "_extract_user_from_token", lambda *_: student)
    monkeypatch.setattr(dependencies, "get_effective_active_subscription", lambda *_: None)
    with pytest.raises(HTTPException) as e:
        dependencies.require_subscription(credentials=None, db=None)
    assert e.value.status_code == 403

    monkeypatch.setattr(dependencies, "get_effective_active_subscription", lambda *_: object())
    assert dependencies.require_subscription(credentials=None, db=None) is student


def test_require_teacher_and_admin(monkeypatch):
    teacher = SimpleNamespace(id=uuid4(), role="teacher")
    admin = SimpleNamespace(id=uuid4(), role="admin")
    student = SimpleNamespace(id=uuid4(), role="student")

    monkeypatch.setattr(dependencies, "_extract_user_from_token", lambda *_: teacher)
    assert dependencies.require_teacher(credentials=None, db=None) is teacher
    with pytest.raises(HTTPException):
        dependencies.require_admin(credentials=None, db=None)

    monkeypatch.setattr(dependencies, "_extract_user_from_token", lambda *_: admin)
    assert dependencies.require_admin(credentials=None, db=None) is admin

    monkeypatch.setattr(dependencies, "_extract_user_from_token", lambda *_: student)
    with pytest.raises(HTTPException):
        dependencies.require_teacher(credentials=None, db=None)


def test_resolve_user_marks_student_and_teacher(monkeypatch):
    student = SimpleNamespace(id=uuid4(), full_name="S", avatar_url=None, role="student")
    monkeypatch.setattr(dependencies, "get_effective_active_subscription", lambda *_: object())
    marks = dependencies.resolve_user_marks(student, db=None)
    assert marks["is_subscribed"] is True
    assert marks["is_verified_teacher"] is False

    from app.models.teacher import TeacherProfile

    teacher = SimpleNamespace(id=uuid4(), full_name="T", avatar_url=None, role="teacher")
    profile = SimpleNamespace(is_verified=True)
    db = _DB({TeacherProfile: profile})
    marks2 = dependencies.resolve_user_marks(teacher, db=db)
    assert marks2["is_verified_teacher"] is True
    assert marks2["is_subscribed"] is False

