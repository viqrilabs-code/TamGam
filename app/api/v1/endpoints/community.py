# app/api/v1/endpoints/community.py
from fastapi import APIRouter

router = APIRouter()

# app/api/v1/endpoints/community.py
# Community endpoints: channels, posts, replies, reactions
#
# Access rules:
#   Read (GET): anonymous allowed
#   Write (POST/DELETE): must be logged in
#   Identity marks resolved live on every author field

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

import app.db.base  # noqa: F401
from app.core.dependencies import get_optional_user, require_login, resolve_user_marks
from app.db.session import get_db
from app.models.community import Channel, Post, Reaction, Reply
from app.models.user import User
from app.schemas.community import (
    AuthorInfo,
    ChannelResponse,
    MessageResponse,
    PostCreateRequest,
    PostDetail,
    PostSummary,
    ReactionRequest,
    ReactionResponse,
    ReactionSummary,
    ReplyCreateRequest,
    ReplyResponse,
)

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_author(user: User, db) -> AuthorInfo:
    marks = resolve_user_marks(user, db)
    return AuthorInfo(
        id=user.id,
        full_name=user.full_name,
        avatar_url=user.avatar_url,
        role=user.role,
        is_subscribed=marks["is_subscribed"],
        is_verified_teacher=marks["is_verified_teacher"],
    )


def _build_reaction_summaries(
    post_id: Optional[UUID],
    reply_id: Optional[UUID],
    viewer_id: Optional[UUID],
    db,
) -> List[ReactionSummary]:
    if post_id:
        reactions = db.query(Reaction).filter(
            and_(Reaction.post_id == post_id, Reaction.reply_id == None)
        ).all()
    else:
        reactions = db.query(Reaction).filter(Reaction.reply_id == reply_id).all()

    counts: dict = {}
    my_reactions: set = set()
    for r in reactions:
        counts[r.emoji] = counts.get(r.emoji, 0) + 1
        if viewer_id and r.user_id == viewer_id:
            my_reactions.add(r.emoji)

    return [
        ReactionSummary(emoji=emoji, count=count, reacted_by_me=emoji in my_reactions)
        for emoji, count in counts.items()
    ]


def _build_reply_response(reply: Reply, author: User, viewer_id, db) -> ReplyResponse:
    return ReplyResponse(
        id=reply.id,
        post_id=reply.post_id,
        parent_reply_id=reply.parent_reply_id,
        body=reply.body,
        author=_build_author(author, db),
        reactions=_build_reaction_summaries(None, reply.id, viewer_id, db),
        created_at=reply.created_at,
    )


# ── Channel Endpoints ─────────────────────────────────────────────────────────

@router.get(
    "/channels",
    response_model=List[ChannelResponse],
    summary="List community channels (public)",
)
def list_channels(db: Session = Depends(get_db)):
    """Public -- list all active community channels."""
    channels = db.query(Channel).filter(Channel.is_active == True).all()
    return [
        ChannelResponse(
            id=c.id,
            name=c.name,
            description=c.description,
            subject=None,
            post_count=db.query(Post).filter(Post.channel_id == c.id).count(),
            is_active=c.is_active,
        )
        for c in channels
    ]


# ── Post Endpoints ────────────────────────────────────────────────────────────

@router.get(
    "/channels/{channel_id}/posts",
    response_model=List[PostSummary],
    summary="List posts in a channel (public)",
)
def list_posts(
    channel_id: UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    viewer: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Public -- list posts in a channel, newest first."""
    channel = db.query(Channel).filter(Channel.id == channel_id).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found.")

    posts = db.query(Post).filter(
        Post.channel_id == channel_id
    ).order_by(Post.created_at.desc()).offset(skip).limit(limit).all()

    result = []
    viewer_id = viewer.id if viewer else None
    for post in posts:
        author_user = db.query(User).filter(User.id == post.user_id).first()
        if not author_user:
            continue
        result.append(PostSummary(
            id=post.id,
            channel_id=post.channel_id,
            title=post.title or "",
            body_preview=post.body[:200] + ("..." if len(post.body) > 200 else ""),
            author=_build_author(author_user, db),
            reply_count=post.reply_count,
            reactions=_build_reaction_summaries(post.id, None, viewer_id, db),
            created_at=post.created_at,
            updated_at=post.updated_at,
        ))
    return result


@router.post(
    "/channels/{channel_id}/posts",
    response_model=PostSummary,
    status_code=201,
    summary="Create a post (auth required)",
)
def create_post(
    channel_id: UUID,
    payload: PostCreateRequest,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Create a post in a channel. Must be logged in."""
    channel = db.query(Channel).filter(
        and_(Channel.id == channel_id, Channel.is_active == True)
    ).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found.")

    post = Post(
        channel_id=channel_id,
        user_id=current_user.id,
        title=payload.title,
        body=payload.body,
        reply_count=0,
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    return PostSummary(
        id=post.id,
        channel_id=post.channel_id,
        title=post.title or "",
        body_preview=post.body[:200],
        author=_build_author(current_user, db),
        reply_count=0,
        reactions=[],
        created_at=post.created_at,
        updated_at=post.updated_at,
    )


@router.get(
    "/posts/{post_id}",
    response_model=PostDetail,
    summary="Get post with replies (public)",
)
def get_post(
    post_id: UUID,
    viewer: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Public -- get full post with all replies and reactions."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found.")

    author_user = db.query(User).filter(User.id == post.user_id).first()
    viewer_id = viewer.id if viewer else None

    # Load top-level replies + nested replies
    all_replies = db.query(Reply).filter(
        Reply.post_id == post_id
    ).order_by(Reply.created_at.asc()).all()

    reply_responses = []
    for reply in all_replies:
        reply_author = db.query(User).filter(User.id == reply.user_id).first()
        if not reply_author:
            continue
        reply_responses.append(_build_reply_response(reply, reply_author, viewer_id, db))

    return PostDetail(
        id=post.id,
        channel_id=post.channel_id,
        title=post.title or "",
        body=post.body,
        author=_build_author(author_user, db),
        replies=reply_responses,
        reply_count=post.reply_count,
        reactions=_build_reaction_summaries(post.id, None, viewer_id, db),
        created_at=post.created_at,
        updated_at=post.updated_at,
    )


@router.delete(
    "/posts/{post_id}",
    response_model=MessageResponse,
    summary="Delete own post",
)
def delete_post(
    post_id: UUID,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Delete own post. Admins can delete any post."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found.")
    if post.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Cannot delete another user's post.")

    db.delete(post)
    db.commit()
    return MessageResponse(message="Post deleted.")


# ── Reply Endpoints ───────────────────────────────────────────────────────────

@router.post(
    "/posts/{post_id}/replies",
    response_model=ReplyResponse,
    status_code=201,
    summary="Reply to a post (auth required)",
)
def create_reply(
    post_id: UUID,
    payload: ReplyCreateRequest,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Reply to a post or to another reply (nested). Must be logged in."""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found.")

    if payload.parent_reply_id:
        parent = db.query(Reply).filter(
            and_(Reply.id == payload.parent_reply_id, Reply.post_id == post_id)
        ).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent reply not found.")

    reply = Reply(
        post_id=post_id,
        user_id=current_user.id,
        body=payload.body,
        parent_reply_id=payload.parent_reply_id,
    )
    db.add(reply)
    post.reply_count = (post.reply_count or 0) + 1
    db.commit()
    db.refresh(reply)

    return _build_reply_response(reply, current_user, current_user.id, db)


# ── Reaction Endpoints ────────────────────────────────────────────────────────

@router.post(
    "/posts/{post_id}/reactions",
    response_model=ReactionResponse,
    status_code=201,
    summary="React to a post or reply (auth required)",
)
def add_reaction(
    post_id: UUID,
    payload: ReactionRequest,
    current_user: User = Depends(require_login),
    db: Session = Depends(get_db),
):
    """
    Add a reaction emoji to a post or reply.
    Reacting with the same emoji again removes the reaction (toggle).
    """
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found.")

    if payload.target_type == "reply":
        reply = db.query(Reply).filter(
            and_(Reply.id == payload.target_id, Reply.post_id == post_id)
        ).first()
        if not reply:
            raise HTTPException(status_code=404, detail="Reply not found.")

    # Check for existing reaction (toggle)
    existing = db.query(Reaction).filter(
        and_(
            Reaction.user_id == current_user.id,
            Reaction.emoji == payload.emoji,
            Reaction.post_id == post_id if payload.target_type == "post" else True,
            Reaction.reply_id == payload.target_id if payload.target_type == "reply" else True,
        )
    ).first()

    if existing:
        db.delete(existing)
        db.commit()
        raise HTTPException(status_code=200, detail="Reaction removed.")

    reaction = Reaction(
        user_id=current_user.id,
        emoji=payload.emoji,
        post_id=post_id if payload.target_type == "post" else None,
        reply_id=payload.target_id if payload.target_type == "reply" else None,
    )
    db.add(reaction)
    db.commit()
    db.refresh(reaction)

    return ReactionResponse(
        id=reaction.id,
        emoji=reaction.emoji,
        target_type=payload.target_type,
        target_id=payload.target_id,
        user_id=current_user.id,
        created_at=reaction.created_at,
    )