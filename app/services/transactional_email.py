# app/services/transactional_email.py
# Lightweight transactional email sender shared across auth and billing events.

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.core.config import settings


class TransactionalEmailConfigError(RuntimeError):
    """Raised when no email provider is configured."""


class TransactionalEmailError(RuntimeError):
    """Raised when sending transactional email fails."""


def _send_via_smtp(to_email: str, subject: str, plain_text: str, html: str | None = None) -> None:
    host = (settings.email_smtp_host or "").strip()
    username = (settings.email_smtp_username or "").strip()
    password = settings.email_smtp_password
    if not host or not username or not password:
        raise TransactionalEmailConfigError("SMTP credentials are incomplete.")

    message = EmailMessage()
    from_name = (settings.email_from_name or "tamgam").strip()
    from_email = (settings.email_from or "noreply@tamgam.in").strip()
    message["From"] = f"{from_name} <{from_email}>"
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(plain_text)
    if html:
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
        raise TransactionalEmailError(f"SMTP send failed: {exc}") from exc


def _send_via_sendgrid(to_email: str, subject: str, plain_text: str, html: str | None = None) -> None:
    if not settings.sendgrid_api_key:
        raise TransactionalEmailConfigError("SendGrid API key is missing.")
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail

        sg = sendgrid.SendGridAPIClient(api_key=settings.sendgrid_api_key)
        message = Mail(
            from_email=(settings.email_from or "noreply@tamgam.in").strip(),
            to_emails=to_email,
            subject=subject,
            plain_text_content=plain_text,
            html_content=html or plain_text,
        )
        sg.send(message)
    except Exception as exc:
        raise TransactionalEmailError(f"SendGrid send failed: {exc}") from exc


def send_transactional_email(
    *,
    to_email: str,
    subject: str,
    plain_text: str,
    html: str | None = None,
) -> None:
    target = (to_email or "").strip()
    if not target:
        return
    if (settings.email_smtp_host or "").strip():
        _send_via_smtp(target, subject, plain_text, html)
        return
    if settings.sendgrid_api_key:
        _send_via_sendgrid(target, subject, plain_text, html)
        return
    raise TransactionalEmailConfigError(
        "No email provider configured. Set SMTP settings or SENDGRID_API_KEY."
    )
