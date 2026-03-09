# app/services/email_login_code.py
# Email-based one-time code generation, delivery, and verification for login.

from __future__ import annotations

import hashlib
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.email_login_code import EmailLoginCode


class EmailLoginCodeConfigError(RuntimeError):
    """Raised when no email provider is configured."""


class EmailLoginCodeDeliveryError(RuntimeError):
    """Raised when email sending fails."""


class EmailLoginCodeCooldownError(ValueError):
    """Raised when resend is blocked by cooldown."""

    def __init__(self, retry_after_seconds: int):
        super().__init__("Verification code was requested too recently.")
        self.retry_after_seconds = max(1, int(retry_after_seconds))


class EmailLoginCodeInvalidError(ValueError):
    """Raised when code is missing/expired/invalid."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _hash_login_code(email: str, code: str) -> str:
    payload = f"{_normalize_email(email)}:{code}:{settings.jwt_secret_key}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _generate_code(length: int = 6) -> str:
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(length))


def _code_context(purpose: str) -> tuple[str, str]:
    normalized = (purpose or "").strip().lower()
    if normalized == "signup":
        return "email verification", "verify your tamgam signup"
    if normalized in {"reset", "forgot_password", "forgot-password"}:
        return "password reset", "reset your tamgam password"
    return "login verification", "sign in to tamgam"


def _build_subject(purpose: str) -> str:
    label, _ = _code_context(purpose)
    return f"tamgam {label} code"


def _build_plaintext(code: str, ttl_minutes: int, purpose: str) -> str:
    label, action = _code_context(purpose)
    return (
        f"Your tamgam {label} code is: {code}\n\n"
        f"Use this code to {action}.\n"
        f"This code expires in {ttl_minutes} minutes.\n"
        "If you did not request this, please ignore this email."
    )


def _build_html(code: str, ttl_minutes: int, purpose: str) -> str:
    label, action = _code_context(purpose)
    return f"""
<!doctype html>
<html>
  <body style="font-family:Arial,sans-serif;background:#fdf6ec;padding:24px;">
    <div style="max-width:560px;margin:0 auto;background:#fff;border:1px solid #f0e4cc;border-radius:12px;overflow:hidden;">
      <div style="background:#e8640c;color:#fff;padding:16px 20px;">
        <h2 style="margin:0;font-size:20px;">tamgam</h2>
      </div>
      <div style="padding:20px;color:#1a1a2e;">
        <p style="margin:0 0 12px 0;">Use this {label} code to {action}:</p>
        <p style="margin:0 0 14px 0;font-size:30px;font-weight:700;letter-spacing:4px;">{code}</p>
        <p style="margin:0 0 12px 0;color:#3d3d5c;">Code expires in {ttl_minutes} minutes.</p>
        <p style="margin:0;color:#6b7280;font-size:13px;">If you did not request this, you can safely ignore this email.</p>
      </div>
    </div>
  </body>
</html>
""".strip()


def _send_via_smtp(to_email: str, subject: str, plain_text: str, html: str) -> None:
    host = settings.email_smtp_host.strip()
    username = settings.email_smtp_username.strip()
    password = settings.email_smtp_password
    if not host or not username or not password:
        raise EmailLoginCodeConfigError("SMTP credentials are incomplete.")

    message = EmailMessage()
    from_name = (settings.email_from_name or "tamgam").strip()
    from_email = (settings.email_from or "noreply@tamgam.in").strip()
    message["From"] = f"{from_name} <{from_email}>"
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(plain_text)
    message.add_alternative(html, subtype="html")

    try:
        server = smtplib.SMTP(host, settings.email_smtp_port, timeout=20)
        with server:
            server.ehlo()
            if settings.email_smtp_use_tls:
                server.starttls()
                server.ehlo()
            server.login(username, password)
            server.send_message(message)
    except Exception as exc:
        raise EmailLoginCodeDeliveryError(f"SMTP send failed: {exc}") from exc


def _send_via_sendgrid(to_email: str, subject: str, plain_text: str, html: str) -> None:
    if not settings.sendgrid_api_key:
        raise EmailLoginCodeConfigError("SendGrid API key is missing.")
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail

        sg = sendgrid.SendGridAPIClient(api_key=settings.sendgrid_api_key)
        message = Mail(
            from_email=(settings.email_from or "noreply@tamgam.in").strip(),
            to_emails=to_email,
            subject=subject,
            plain_text_content=plain_text,
            html_content=html,
        )
        sg.send(message)
    except Exception as exc:
        raise EmailLoginCodeDeliveryError(f"SendGrid send failed: {exc}") from exc


def _send_email_code(to_email: str, code: str, ttl_minutes: int, purpose: str) -> None:
    subject = _build_subject(purpose)
    plain_text = _build_plaintext(code, ttl_minutes, purpose)
    html = _build_html(code, ttl_minutes, purpose)

    if settings.email_smtp_host.strip():
        _send_via_smtp(to_email=to_email, subject=subject, plain_text=plain_text, html=html)
        return
    if settings.sendgrid_api_key:
        _send_via_sendgrid(to_email=to_email, subject=subject, plain_text=plain_text, html=html)
        return
    raise EmailLoginCodeConfigError(
        "No email provider configured. Set SMTP settings or SENDGRID_API_KEY."
    )


def issue_login_code(db: Session, email: str, purpose: str = "login") -> int:
    """
    Generates and sends a one-time login code.
    Returns resend cooldown seconds to help client UX.
    """
    normalized_email = _normalize_email(email)
    now = _utcnow()
    ttl_minutes = max(1, int(settings.email_login_code_ttl_minutes))
    cooldown_seconds = max(1, int(settings.email_login_code_resend_cooldown_seconds))

    latest_active: Optional[EmailLoginCode] = (
        db.query(EmailLoginCode)
        .filter(
            EmailLoginCode.email == normalized_email,
            EmailLoginCode.used_at.is_(None),
            EmailLoginCode.expires_at > now,
        )
        .order_by(EmailLoginCode.created_at.desc())
        .first()
    )
    if latest_active is not None:
        elapsed = (now - latest_active.created_at).total_seconds()
        if elapsed < cooldown_seconds:
            raise EmailLoginCodeCooldownError(retry_after_seconds=(cooldown_seconds - elapsed))

    code = _generate_code()
    record = EmailLoginCode(
        email=normalized_email,
        code_hash=_hash_login_code(normalized_email, code),
        expires_at=now + timedelta(minutes=ttl_minutes),
        attempts=0,
        max_attempts=max(1, int(settings.email_login_code_max_attempts)),
    )
    db.add(record)
    db.flush()

    try:
        _send_email_code(
            to_email=normalized_email,
            code=code,
            ttl_minutes=ttl_minutes,
            purpose=purpose,
        )
    except Exception:
        db.delete(record)
        db.flush()
        raise

    return cooldown_seconds


def verify_login_code(db: Session, email: str, code: str) -> None:
    normalized_email = _normalize_email(email)
    now = _utcnow()
    submitted = (code or "").strip()
    if len(submitted) != 6 or not submitted.isdigit():
        raise EmailLoginCodeInvalidError("Enter a valid 6-digit verification code.")

    record: Optional[EmailLoginCode] = (
        db.query(EmailLoginCode)
        .filter(
            EmailLoginCode.email == normalized_email,
            EmailLoginCode.used_at.is_(None),
        )
        .order_by(EmailLoginCode.created_at.desc())
        .first()
    )
    if record is None:
        raise EmailLoginCodeInvalidError("Request a verification code first.")
    if record.expires_at <= now:
        raise EmailLoginCodeInvalidError("Verification code expired. Request a new code.")
    if record.attempts >= record.max_attempts:
        raise EmailLoginCodeInvalidError("Too many attempts. Request a new verification code.")

    record.attempts = int(record.attempts or 0) + 1
    if record.code_hash != _hash_login_code(normalized_email, submitted):
        db.flush()
        raise EmailLoginCodeInvalidError("Incorrect verification code.")

    record.used_at = now
    db.flush()
