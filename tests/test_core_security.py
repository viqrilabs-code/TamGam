from datetime import datetime, timezone
from uuid import uuid4

from app.core import security


def test_hash_and_verify_password():
    hashed = security.hash_password("StrongPass123!")
    assert hashed != "StrongPass123!"
    assert security.verify_password("StrongPass123!", hashed) is True
    assert security.verify_password("wrong", hashed) is False


def test_access_token_roundtrip():
    user_id = uuid4()
    token = security.create_access_token(user_id=user_id, role="student")
    payload = security.decode_token(token)
    assert payload is not None
    assert payload["sub"] == str(user_id)
    assert payload["role"] == "student"
    assert payload["type"] == "access"


def test_refresh_token_has_unique_jti():
    user_id = uuid4()
    t1 = security.create_refresh_token(user_id=user_id)
    t2 = security.create_refresh_token(user_id=user_id)
    p1 = security.decode_token(t1)
    p2 = security.decode_token(t2)
    assert p1 is not None and p2 is not None
    assert p1["type"] == "refresh"
    assert p2["type"] == "refresh"
    assert p1["jti"] != p2["jti"]


def test_decode_token_invalid_returns_none():
    assert security.decode_token("not-a-jwt") is None


def test_get_token_expiry_future():
    now = datetime.now(timezone.utc)
    expiry = security.get_token_expiry(days=1, minutes=15)
    assert expiry > now


def test_hash_token_is_stable_sha256():
    digest1 = security.hash_token("abc")
    digest2 = security.hash_token("abc")
    digest3 = security.hash_token("xyz")
    assert digest1 == digest2
    assert digest1 != digest3
    assert len(digest1) == 64

