import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.auth import _build_phone_email, _normalize_phone_number


def test_normalize_phone_number_strips_non_digits():
    assert _normalize_phone_number("+91 98765-43210") == "+919876543210"


def test_normalize_phone_number_rejects_short_numbers():
    with pytest.raises(HTTPException) as exc:
        _normalize_phone_number("+12")
    assert exc.value.status_code == 400


def test_build_phone_email_from_e164():
    assert _build_phone_email("+919876543210") == "phone_919876543210@phone.tamgam.local"
