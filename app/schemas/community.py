# app/schemas/community.py
# Pydantic request/response models for community endpoints

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


# ── Author ────────────────────────────────────────────────────────────────────

class AuthorInfo(BaseModel):
    """Author shown on posts and replies with identity marks."""
    id: UUID
    full_name: str
    avatar_url: Optional[str] = None
    role: str
    is_subscribed: bool       # ⭐ mark
    is_verified_teacher: bool # 🟡T mark


# ── Channel ───────────────────────────────────────────────────────────────────

class ChannelResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    subject: Optional[str] = None
    post_count: int
    is_active: bool


# ── Post ──────────────────────────────────────────────────────────────────────

class PostCreateRequest(BaseModel):
    title: str
    body: str

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Title cannot be empty")
        if len(v) > 300:
            raise ValueError("Title too long (max 300 characters)")
        return v

    @field_validator("body")
    @classmethod
    def body_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Body cannot be empty")
        return v


class ReactionSummary(BaseModel):
    emoji: str
    count: int
    reacted_by_me: bool


class PostSummary(BaseModel):
    """Post card for listing -- no full body."""
    id: UUID
    channel_id: UUID
    title: str
    body_preview: str        # First 200 chars of body
    author: AuthorInfo
    reply_count: int
    reactions: List[ReactionSummary]
    created_at: datetime
    updated_at: datetime


class PostDetail(BaseModel):
    """Full post with replies."""
    id: UUID
    channel_id: UUID
    title: str
    body: str
    author: AuthorInfo
    replies: List["ReplyResponse"]
    reply_count: int
    reactions: List[ReactionSummary]
    created_at: datetime
    updated_at: datetime


# ── Reply ─────────────────────────────────────────────────────────────────────

class ReplyCreateRequest(BaseModel):
    body: str
    parent_reply_id: Optional[UUID] = None   # For nested replies

    @field_validator("body")
    @classmethod
    def body_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Reply body cannot be empty")
        return v


class ReplyResponse(BaseModel):
    id: UUID
    post_id: UUID
    parent_reply_id: Optional[UUID] = None
    body: str
    author: AuthorInfo
    reactions: List[ReactionSummary]
    created_at: datetime


# ── Reaction ──────────────────────────────────────────────────────────────────

class ReactionRequest(BaseModel):
    emoji: str
    target_type: str    # post | reply
    target_id: UUID

    @field_validator("emoji")
    @classmethod
    def valid_emoji(cls, v: str) -> str:
        allowed = {"👍", "❤️", "🔥", "💡", "😮", "👏"}
        if v not in allowed:
            raise ValueError(f"Emoji must be one of: {', '.join(allowed)}")
        return v

    @field_validator("target_type")
    @classmethod
    def valid_target(cls, v: str) -> str:
        if v not in ("post", "reply"):
            raise ValueError("target_type must be 'post' or 'reply'")
        return v


class ReactionResponse(BaseModel):
    id: UUID
    emoji: str
    target_type: str
    target_id: UUID
    user_id: UUID
    created_at: datetime


# ── Generic ───────────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str


# Resolve forward reference
PostDetail.model_rebuild()