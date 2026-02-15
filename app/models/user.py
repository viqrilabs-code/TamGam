# app/models/user.py
# Base user model for all roles: student | teacher | admin
# Role-specific data lives in StudentProfile / TeacherProfile (separate tables)

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # ── Identity ──────────────────────────────────────────────────────────────
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=True)  # Null for OAuth users
    full_name = Column(String(255), nullable=False)
    avatar_url = Column(Text, nullable=True)
    phone = Column(String(20), nullable=True)

    # ── Role ──────────────────────────────────────────────────────────────────
    role = Column(
        Enum("student", "teacher", "admin", name="user_role_enum"),
        nullable=False,
        default="student",
    )

    # ── OAuth ─────────────────────────────────────────────────────────────────
    google_id = Column(String(255), unique=True, nullable=True, index=True)
    auth_provider = Column(
        Enum("email", "google", name="auth_provider_enum"),
        nullable=False,
        default="email",
    )

    # ── Account Status ────────────────────────────────────────────────────────
    is_active = Column(Boolean, nullable=False, default=True)
    is_email_verified = Column(Boolean, nullable=False, default=False)
    email_verification_token = Column(String(255), nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    refresh_tokens = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )
    student_profile = relationship(
        "StudentProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    teacher_profile = relationship(
        "TeacherProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    subscriptions = relationship("Subscription", back_populates="user")
    notifications = relationship(
        "Notification", back_populates="user", cascade="all, delete-orphan"
    )
    posts = relationship("Post", back_populates="author")
    replies = relationship("Reply", back_populates="author")
    tutor_sessions = relationship(
        "TutorSession", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.role}>"


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ── Token ─────────────────────────────────────────────────────────────────
    token_hash = Column(String(255), unique=True, nullable=False)  # SHA-256 of the token
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_revoked = Column(Boolean, nullable=False, default=False)

    # ── Device / Session Info (optional, useful for "active sessions" page) ───
    device_info = Column(String(255), nullable=True)   # e.g. "Chrome on Windows"
    ip_address = Column(String(50), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user = relationship("User", back_populates="refresh_tokens")

    def __repr__(self) -> str:
        return f"<RefreshToken id={self.id} user_id={self.user_id} revoked={self.is_revoked}>"