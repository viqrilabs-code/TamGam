import pytest

from app.schemas.auth import (
    EmailCodeLoginRequest,
    EmailLoginCodeSendRequest,
    FirebasePhoneLoginRequest,
    ForgotPasswordResetRequest,
    SignupRequest,
)


def test_teacher_signup_schema_accepts_missing_declaration_flag():
    req = SignupRequest(
        email="teacher@example.com",
        password="strongpass123",
        full_name="Teacher One",
        verification_code="123456",
        role="teacher",
    )
    assert req.teacher_declaration_accepted is False


def test_teacher_signup_with_declaration_is_valid():
    req = SignupRequest(
        email="teacher@example.com",
        password="strongpass123",
        full_name="Teacher One",
        verification_code="123456",
        role="teacher",
        teacher_declaration_accepted=True,
        teacher_declaration_version="teacher-payout-v2026-02-25",
    )
    assert req.teacher_declaration_accepted is True
    assert req.teacher_declaration_version == "teacher-payout-v2026-02-25"


def test_student_signup_does_not_require_teacher_declaration():
    req = SignupRequest(
        email="student@example.com",
        password="strongpass123",
        full_name="Student One",
        verification_code="123456",
        role="student",
    )
    assert req.teacher_declaration_accepted is False


def test_firebase_phone_login_schema_trims_fields():
    req = FirebasePhoneLoginRequest(
        id_token="  token-123  ",
        full_name="  Student One  ",
    )
    assert req.id_token == "token-123"
    assert req.full_name == "Student One"


def test_firebase_phone_login_schema_rejects_empty_token():
    with pytest.raises(ValueError):
        FirebasePhoneLoginRequest(id_token="   ")


def test_email_login_code_send_request_accepts_valid_payload():
    req = EmailLoginCodeSendRequest(email="student@example.com", password="strongpass123")
    assert str(req.email) == "student@example.com"


def test_email_code_login_request_rejects_non_numeric_code():
    with pytest.raises(ValueError):
        EmailCodeLoginRequest(
            email="student@example.com",
            password="strongpass123",
            verification_code="12ab56",
        )


def test_email_code_login_request_rejects_short_code():
    with pytest.raises(ValueError):
        EmailCodeLoginRequest(
            email="student@example.com",
            password="strongpass123",
            verification_code="12345",
        )


def test_forgot_password_reset_schema_rejects_weak_password():
    with pytest.raises(ValueError):
        ForgotPasswordResetRequest(
            email="student@example.com",
            verification_code="123456",
            new_password="123",
        )
