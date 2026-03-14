# app/services/password_reset_link.py
# One-time password reset link issuance and verification.

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_token
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.services.transactional_email import (
    TransactionalEmailConfigError,
    TransactionalEmailError,
    send_transactional_email,
)


class PasswordResetLinkConfigError(RuntimeError):
    """Raised when password reset link config is invalid."""


class PasswordResetLinkDeliveryError(RuntimeError):
    """Raised when password reset link delivery fails."""


class PasswordResetLinkInvalidError(RuntimeError):
    """Raised when password reset link token is invalid or expired."""


def _build_reset_url(raw_token: str) -> str:
    base = (settings.password_reset_frontend_url or "").strip()
    if not base:
        raise PasswordResetLinkConfigError(
            "Password reset URL is missing. Set PASSWORD_RESET_FRONTEND_URL."
        )

    parsed = urlparse(base)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["token"] = raw_token
    return urlunparse(parsed._replace(query=urlencode(query)))


def _build_email_content(reset_url: str, ttl_minutes: int) -> tuple[str, str]:
    plain_text = (
        "We received a request to reset your tamgam password.\n\n"
        f"Open this link to set a new password (valid for {ttl_minutes} minutes):\n"
        f"{reset_url}\n\n"
        "If you did not request this, you can safely ignore this email."
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;padding:20px;border:1px solid #e5e7eb;border-radius:12px;">
      <h2 style="margin:0 0 8px;color:#1f2937;">Reset your tamgam password</h2>
      <p style="margin:0 0 16px;color:#4b5563;">Use the button below to set a new password. This link is valid for <strong>{ttl_minutes} minutes</strong>.</p>
      <p style="margin:0 0 20px;">
        <a href="{reset_url}" style="display:inline-block;padding:12px 18px;border-radius:8px;background:#e8640c;color:#fff;text-decoration:none;font-weight:600;">Reset password</a>
      </p>
      <p style="margin:0 0 6px;color:#6b7280;font-size:13px;">If the button does not work, copy and paste this URL into your browser:</p>
      <p style="margin:0 0 16px;color:#0f172a;font-size:13px;word-break:break-all;">{reset_url}</p>
      <p style="margin:0;color:#6b7280;font-size:13px;">If you did not request this, you can safely ignore this email.</p>
    </div>
    """
    return plain_text, html


def issue_password_reset_link(db: Session, user: User) -> None:
    ttl_minutes = max(1, int(settings.password_reset_link_ttl_minutes))
    now = datetime.now(timezone.utc)

    # Invalidate existing unused links for this user before creating a new one.
    db.query(PasswordResetToken).filter(
        and_(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
    ).update({"used_at": now}, synchronize_session=False)

    raw_token = token_urlsafe(32)
    token = PasswordResetToken(
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=now + timedelta(minutes=ttl_minutes),
    )
    db.add(token)
    db.flush()

    reset_url = _build_reset_url(raw_token)
    plain_text, html = _build_email_content(reset_url, ttl_minutes)

    try:
        send_transactional_email(
            to_email=(user.email or "").strip(),
            subject="Reset your tamgam password",
            plain_text=plain_text,
            html=html,
        )
    except TransactionalEmailConfigError as exc:
        raise PasswordResetLinkConfigError(str(exc)) from exc
    except TransactionalEmailError as exc:
        raise PasswordResetLinkDeliveryError(str(exc)) from exc


def consume_password_reset_link(db: Session, raw_token: str) -> User:
    submitted = (raw_token or "").strip()
    if not submitted:
        raise PasswordResetLinkInvalidError("Password reset link is invalid or expired.")

    now = datetime.now(timezone.utc)
    record = (
        db.query(PasswordResetToken)
        .filter(
            and_(
                PasswordResetToken.token_hash == hash_token(submitted),
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > now,
            )
        )
        .first()
    )
    if not record:
        raise PasswordResetLinkInvalidError("Password reset link is invalid or expired.")

    user = db.query(User).filter(and_(User.id == record.user_id, User.is_active == True)).first()
    if not user:
        raise PasswordResetLinkInvalidError("Password reset link is invalid or expired.")

    record.used_at = now
    return user
