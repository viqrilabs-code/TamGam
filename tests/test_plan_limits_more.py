from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.services import plan_limits


class _Q:
    def __init__(self, result):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class _DB:
    def __init__(self, result=None):
        self._result = result
        self.added = []
        self.flushed = False

    def query(self, _model):
        return _Q(self._result)

    def add(self, obj):
        self.added.append(obj)
        self._result = obj

    def flush(self):
        self.flushed = True


def test_month_period_start():
    now = datetime(2026, 2, 24, tzinfo=timezone.utc)
    assert plan_limits.month_period_start(now).isoformat() == "2026-02-01"


def test_get_limit_for_user(monkeypatch):
    user_id = uuid4()
    monkeypatch.setattr(plan_limits, "get_active_plan", lambda *_: SimpleNamespace(slug="core"))
    assert plan_limits.get_limit_for_user(user_id, "diya_questions_monthly", db=None) == 120

    monkeypatch.setattr(plan_limits, "get_active_plan", lambda *_: None)
    assert plan_limits.get_limit_for_user(user_id, "diya_questions_monthly", db=None) is None


def test_get_current_usage():
    row = SimpleNamespace(usage_count=7)
    db = _DB(result=row)
    assert plan_limits.get_current_usage(uuid4(), "x", db) == 7
    db2 = _DB(result=None)
    assert plan_limits.get_current_usage(uuid4(), "x", db2) == 0


def test_consume_feature_existing_row_increments():
    row = SimpleNamespace(usage_count=2)
    db = _DB(result=row)
    out = plan_limits.consume_feature(uuid4(), "student_notes_monthly", db, units=3)
    assert out == 5
    assert row.usage_count == 5


def test_consume_feature_creates_new_row():
    db = _DB(result=None)
    out = plan_limits.consume_feature(uuid4(), "community_interactions_monthly", db, units=1)
    assert out == 1
    assert len(db.added) == 1
    assert db.flushed is True

