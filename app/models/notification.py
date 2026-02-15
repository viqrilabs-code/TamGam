# app/models/notification.py
# In-app notification queue for all platform events

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class Notification(Base):
    """
    In-app notification for a user.
    Created by notification_service.py for all key platform events.
    Delivered via GET /api/v1/notifications (polled or WebSocket push).
    Email delivery handled asynchronously via SendGrid + Cloud Tasks.
    """
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Type ──────────────────────────────────────────────────────────────────
    notification_type = Column(
        Enum(
            "reply",          # Someone replied to your post
            "mention",        # @mention in post/reply
            "reaction",       # Someone reacted to your content
            "announcement",   # Teacher posted an update
            "post_resolved",  # Your question was marked resolved
            "notes_ready",    # AI notes published for a class
            "verification",   # Teacher verification status changed
            "subscription",   # Subscription status change
            "assessment",     # New assessment available
            name="notification_type_enum",
        ),
        nullable=False,
        index=True,
    )

    # ── Content ───────────────────────────────────────────────────────────────
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=True)

    # ── Deep Link ─────────────────────────────────────────────────────────────
    # Frontend uses this to navigate on click
    action_url = Column(String(512), nullable=True)           # e.g. "/community/posts/abc123"

    # ── Related Entity IDs (for context / deduplication) ──────────────────────
    # Store relevant IDs so we can avoid duplicate notifications
    # Named extra_data (not metadata — reserved by SQLAlchemy)
    extra_data = Column(JSONB, nullable=True)
    # Examples:
    #   reply notification:  {"post_id": "...", "reply_id": "...", "actor_id": "..."}
    #   notes_ready:         {"class_id": "...", "note_id": "..."}
    #   verification:        {"status": "approved"}

    # ── Read Status ───────────────────────────────────────────────────────────
    is_read = Column(Boolean, nullable=False, default=False, index=True)
    read_at = Column(DateTime(timezone=True), nullable=True)

    # ── Email Delivery ────────────────────────────────────────────────────────
    email_sent = Column(Boolean, nullable=False, default=False)
    email_sent_at = Column(DateTime(timezone=True), nullable=True)
    email_error = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user = relationship("User", back_populates="notifications")

    def __repr__(self) -> str:
        return (
            f"<Notification user={self.user_id} "
            f"type={self.notification_type} read={self.is_read}>"
        )