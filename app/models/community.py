# app/models/community.py
# Community: Channels, Posts, Replies, Reactions
# Access model: read = anyone | post/reply/react = logged in
# Identity marks (⭐ 🟡T) resolved at query time via resolve_user_marks()

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class Channel(Base):
    """
    Subject or batch-specific community channel.
    Created by admin only.
    Examples: "Mathematics", "Science", "General", "Batch A"
    """
    __tablename__ = "channels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    name = Column(String(100), unique=True, nullable=False)
    slug = Column(String(100), unique=True, nullable=False, index=True)  # url-safe name
    description = Column(Text, nullable=True)
    icon = Column(String(10), nullable=True)                  # Emoji icon e.g. "📐"

    channel_type = Column(
        Enum("subject", "batch", "general", name="channel_type_enum"),
        nullable=False,
        default="subject",
    )
    is_active = Column(Boolean, nullable=False, default=True)

    # ── Access ────────────────────────────────────────────────────────────────
    # Channels can optionally be restricted to a specific batch
    batch_id = Column(
        UUID(as_uuid=True),
        ForeignKey("batches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    posts = relationship("Post", back_populates="channel", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Channel name={self.name} type={self.channel_type}>"


class Post(Base):
    """
    A community post in a channel.
    Anyone can read. Logged-in users can post.
    ⭐ and 🟡T marks shown based on live subscription/verification check.
    """
    __tablename__ = "posts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id = Column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Content ───────────────────────────────────────────────────────────────
    title = Column(String(500), nullable=True)                # Optional title for questions
    body = Column(Text, nullable=False)
    post_type = Column(
        Enum("question", "achievement", "update", name="post_type_enum"),
        nullable=False,
        default="question",
    )

    # ── Attachments ───────────────────────────────────────────────────────────
    image_url = Column(Text, nullable=True)                   # Optional attached image (GCS)

    # ── Moderation ────────────────────────────────────────────────────────────
    is_pinned = Column(Boolean, nullable=False, default=False)
    is_resolved = Column(Boolean, nullable=False, default=False)  # For question posts
    is_deleted = Column(Boolean, nullable=False, default=False)   # Soft delete
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # ── Stats (denormalised) ──────────────────────────────────────────────────
    reply_count = Column(Integer, nullable=False, default=0)
    reaction_count = Column(Integer, nullable=False, default=0)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    channel = relationship("Channel", back_populates="posts")
    author = relationship("User", back_populates="posts")
    replies = relationship(
        "Reply", back_populates="post", cascade="all, delete-orphan",
        order_by="Reply.created_at",
    )
    reactions = relationship("Reaction", back_populates="post")

    def __repr__(self) -> str:
        return f"<Post id={self.id} type={self.post_type} channel={self.channel_id}>"


class Reply(Base):
    """
    A reply to a community post.
    Supports one level of threading (reply to a reply via parent_reply_id).
    """
    __tablename__ = "replies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(
        UUID(as_uuid=True),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_reply_id = Column(
        UUID(as_uuid=True),
        ForeignKey("replies.id", ondelete="SET NULL"),
        nullable=True,
    )

    body = Column(Text, nullable=False)
    image_url = Column(Text, nullable=True)

    is_accepted = Column(Boolean, nullable=False, default=False)  # Accepted answer
    is_deleted = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    reaction_count = Column(Integer, nullable=False, default=0)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    post = relationship("Post", back_populates="replies")
    author = relationship("User", back_populates="replies")
    reactions = relationship("Reaction", back_populates="reply")
    child_replies = relationship(
        "Reply",
        foreign_keys=[parent_reply_id],
        primaryjoin="Reply.parent_reply_id == remote(Reply.id)",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<Reply id={self.id} post={self.post_id} author={self.author_id}>"


class Reaction(Base):
    """
    Emoji reaction on a post or reply.
    One reaction per user per target per emoji type.
    """
    __tablename__ = "reactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Target ────────────────────────────────────────────────────────────────
    # Exactly one of post_id or reply_id must be set
    post_id = Column(
        UUID(as_uuid=True),
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    reply_id = Column(
        UUID(as_uuid=True),
        ForeignKey("replies.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    emoji = Column(String(10), nullable=False)                # "👍" | "❤️" | "🔥" etc.

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Unique constraint: one emoji per user per target ──────────────────────
    __table_args__ = (
        UniqueConstraint("user_id", "post_id", "emoji", name="uq_reaction_user_post_emoji"),
        UniqueConstraint("user_id", "reply_id", "emoji", name="uq_reaction_user_reply_emoji"),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    post = relationship("Post", back_populates="reactions")
    reply = relationship("Reply", back_populates="reactions")

    def __repr__(self) -> str:
        return f"<Reaction user={self.user_id} emoji={self.emoji}>"