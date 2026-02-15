# app/core/dependencies.py
# FastAPI dependency functions for authentication and authorization
#
# Key design rules from product spec:
#   1. resolve_user_marks() -- ALWAYS live query, never cached
#   2. Community GET endpoints use get_optional_user() -- returns None for anonymous
#   3. 401 responses for community actions include CTA redirect
#   4. Meet links gated -- require_subscription() used in class endpoints

from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User
from app.models.subscription import Subscription

# Bearer token extractor -- auto_error=False so we can handle 401 ourselves
bearer_scheme = HTTPBearer(auto_error=False)


# ── Token Extraction ──────────────────────────────────────────────────────────

def _extract_user_from_token(
    credentials: Optional[HTTPAuthorizationCredentials],
    db: Session,
) -> Optional[User]:
    """
    Internal helper: decode Bearer token and load user from DB.
    Returns None if no token, invalid token, or user not found/inactive.
    """
    if not credentials:
        return None

    payload = decode_token(credentials.credentials)
    if not payload:
        return None

    if payload.get("type") != "access":
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    user = db.query(User).filter(
        and_(User.id == user_id, User.is_active == True)
    ).first()

    return user


# ── Identity Marks ────────────────────────────────────────────────────────────

def resolve_user_marks(user: User, db: Session) -> dict:
    """
    Compute identity marks for a user at query time -- NEVER cached.

    Returns:
        {
            "id": str,
            "full_name": str,
            "avatar_url": str | None,
            "role": str,
            "is_subscribed": bool,       # True -> show pink star mark
            "is_verified_teacher": bool, # True -> show golden T mark
        }

    Called every time author info is serialised in API responses.
    Checks subscription and verification tables live on every call.
    """
    is_subscribed = False
    is_verified_teacher = False

    if user.role == "student":
        active_sub = db.query(Subscription).filter(
            and_(
                Subscription.user_id == user.id,
                Subscription.status == "active",
            )
        ).first()
        is_subscribed = active_sub is not None

    elif user.role == "teacher":
        # Import here to avoid circular imports
        from app.models.teacher import TeacherProfile
        profile = db.query(TeacherProfile).filter(
            TeacherProfile.user_id == user.id
        ).first()
        is_verified_teacher = profile.is_verified if profile else False

    return {
        "id": str(user.id),
        "full_name": user.full_name,
        "avatar_url": user.avatar_url,
        "role": user.role,
        "is_subscribed": is_subscribed,
        "is_verified_teacher": is_verified_teacher,
    }


# ── Auth Dependencies ─────────────────────────────────────────────────────────

def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    Returns the authenticated user if a valid token is present.
    Returns None for anonymous requests -- does NOT raise 401.

    Use for: Community GET endpoints (open to all, marks shown if logged in)
    """
    return _extract_user_from_token(credentials, db)


def require_login(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Requires a valid JWT token. Raises 401 if not authenticated.

    Use for: Any endpoint requiring login but not a specific role.
    """
    user = _extract_user_from_token(credentials, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_login_for_community(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Requires login for community write actions (post/reply/react).
    Returns a special 401 with CTA redirect for anonymous users.

    Per product spec: anonymous 401 must always include signup CTA.
    """
    user = _extract_user_from_token(credentials, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "message": "Sign up free to join the conversation",
                "cta": "Sign up free",
                "redirect": "/signup",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_subscription(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Requires an active subscription.
    Teachers always pass (they see their own content).
    Admins always pass.
    Students must have status='active' subscription.

    Use for: Notes, AI Tutor, Assessments, Meet links.
    """
    user = _extract_user_from_token(credentials, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Teachers and admins always have access
    if user.role in ("teacher", "admin"):
        return user

    # Students need an active subscription
    active_sub = db.query(Subscription).filter(
        and_(
            Subscription.user_id == user.id,
            Subscription.status == "active",
        )
    ).first()

    if not active_sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "This feature requires an active subscription.",
                "cta": "View plans",
                "redirect": "/pricing",
            },
        )

    return user


def require_teacher(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Requires role='teacher'. Raises 403 for other roles.
    Use for: Teacher dashboard, class management, batch management.
    """
    user = _extract_user_from_token(credentials, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.role != "teacher":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Teacher access required.",
        )
    return user


def require_admin(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Requires role='admin'. Raises 403 for all other roles.
    Use for: Admin portal endpoints only.
    """
    user = _extract_user_from_token(credentials, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return user