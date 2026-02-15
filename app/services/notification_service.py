# app/services/notification_service.py
# Creates in-app notifications and sends emails via SendGrid
#
# Usage (from any endpoint or background task):
#   from app.services.notification_service import notify
#   notify(db, user_id=student_id, type="class_reminder",
#          title="Class starting soon", body="Your Maths class starts in 15 minutes")

from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.notification import Notification


# ── Notification Types ────────────────────────────────────────────────────────
# Used to filter notifications on frontend and trigger emails selectively

NOTIFICATION_TYPES = {
    "class_scheduled",      # Teacher scheduled a new class
    "class_reminder",       # 15 min before class starts
    "class_cancelled",      # Teacher cancelled a class
    "notes_ready",          # AI notes published for a class
    "assessment_ready",     # Assessment available for a class
    "subscription_active",  # Payment confirmed, subscription active
    "subscription_expiring",# Subscription expiring in 3 days
    "subscription_expired", # Subscription expired
    "verification_approved",# Teacher verification approved
    "verification_rejected",# Teacher verification rejected
    "community_reply",      # Someone replied to your post
    "community_reaction",   # Someone reacted to your post
    "new_student",          # Student enrolled with teacher
    "top_performer",        # Student ranked in top performers
}

# Which types also send an email
EMAIL_TYPES = {
    "class_scheduled",
    "subscription_active",
    "subscription_expiring",
    "subscription_expired",
    "verification_approved",
    "verification_rejected",
}


def notify(
    db: Session,
    user_id: UUID,
    notification_type: str,
    title: str,
    body: str,
    extra_data: Optional[Dict[str, Any]] = None,
    send_email: bool = True,
) -> Notification:
    """
    Create an in-app notification and optionally send an email.

    Args:
        db: Database session
        user_id: Recipient user ID
        notification_type: One of NOTIFICATION_TYPES
        title: Short notification title
        body: Full notification body
        extra_data: Optional dict stored as JSONB (e.g. class_id, post_id)
        send_email: Override email sending (default: based on type)

    Returns:
        Created Notification instance
    """
    notification = Notification(
        user_id=user_id,
        type=notification_type,
        title=title,
        body=body,
        extra_data=extra_data or {},
        is_read=False,
    )
    db.add(notification)
    db.flush()

    # Send email for important notification types
    if send_email and notification_type in EMAIL_TYPES:
        try:
            _send_email_notification(user_id, title, body, db)
        except Exception as e:
            # Email failure should never block the main flow
            print(f"Email notification failed for user {user_id}: {e}")

    return notification


def _send_email_notification(
    user_id: UUID,
    subject: str,
    body: str,
    db: Session,
) -> None:
    """
    Send email via SendGrid.
    No-op in dev mode if SENDGRID_API_KEY is not configured.
    """
    if not settings.sendgrid_api_key:
        print(f"[DEV] Email skipped (no SendGrid key): {subject}")
        return

    from app.models.user import User
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.email:
        return

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail

        sg = sendgrid.SendGridAPIClient(api_key=settings.sendgrid_api_key)
        message = Mail(
            from_email=settings.sendgrid_from_email or "noreply@tamgam.in",
            to_emails=user.email,
            subject=f"TamGam: {subject}",
            plain_text_content=body,
            html_content=_build_email_html(user.full_name, subject, body),
        )
        sg.send(message)
        print(f"Email sent to {user.email}: {subject}")
    except Exception as e:
        raise RuntimeError(f"SendGrid error: {e}")


def _build_email_html(full_name: str, subject: str, body: str) -> str:
    """Simple HTML email template."""
    return f"""
<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px;">
    <div style="background: #f97316; padding: 20px; border-radius: 8px 8px 0 0;">
        <h1 style="color: white; margin: 0;">TamGam</h1>
        <p style="color: #fed7aa; margin: 4px 0 0 0;">From darkness to light 🪔</p>
    </div>
    <div style="background: #fff; padding: 24px; border: 1px solid #e5e7eb; border-radius: 0 0 8px 8px;">
        <p>Hi {full_name},</p>
        <h2 style="color: #1f2937;">{subject}</h2>
        <p style="color: #4b5563; line-height: 1.6;">{body}</p>
        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 24px 0;">
        <p style="color: #9ca3af; font-size: 12px;">
            You received this email from TamGam. To manage notifications, visit your account settings.
        </p>
    </div>
</body>
</html>
"""