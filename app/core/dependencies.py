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

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db.session import get_db
from app.models.subscription import Subscription
from app.models.user import User
from app.services.subscription_access import (
    get_effective_active_subscriptions as _get_effective_active_subscriptions,
)

# Bearer token extractor -- auto_error=False so we can handle 401 ourselves
bearer_scheme = HTTPBearer(auto_error=False)


# â”€â”€ Token Extraction â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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


# â”€â”€ Identity Marks â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
        # Students are now free by default; subscription marks remain true for UX compatibility.
        is_subscribed = True

    elif user.role == "teacher":
        # Import here to avoid circular imports
        from app.models.teacher import TeacherProfile
        profile = db.query(TeacherProfile).filter(
            TeacherProfile.user_id == user.id
        ).first()
        is_verified_teacher = profile.is_verified if profile else False
        is_subscribed = bool(_get_effective_active_subscriptions(user.id, db))

    return {
        "id": str(user.id),
        "full_name": user.full_name,
        "avatar_url": user.avatar_url,
        "role": user.role,
        "is_subscribed": is_subscribed,
        "is_verified_teacher": is_verified_teacher,
    }


def get_effective_active_subscription(user_id: UUID, db: Session) -> Optional[Subscription]:
    """
    Return an active subscription only if it is still effective right now.
    This ensures benefits stop immediately after period end even if webhook lag exists.
    """
    active = _get_effective_active_subscriptions(user_id, db)
    return active[0] if active else None


def get_effective_active_subscriptions(user_id: UUID, db: Session) -> list[Subscription]:
    return _get_effective_active_subscriptions(user_id, db)


# â”€â”€ Auth Dependencies â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    Legacy dependency retained for backward-compatible endpoint wiring.
    All authenticated users now pass because student access is free and
    teacher billing is handled separately.

    Use for: Notes, AI Tutor, Assessments, Meet links.
    """
    user = _extract_user_from_token(credentials, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def require_teacher(
    request: Request = None,
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
    method = (request.method if request else "").upper()
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        ensure_teacher_billing_active(user, db)
    return user


def ensure_teacher_billing_active(user: User, db: Session) -> None:
    """
    Enforce active teacher billing for premium actions.
    Teachers without active billing can still browse GET endpoints/UI,
    but cannot execute mutating premium operations.
    """
    if user.role != "teacher":
        return

    active = _get_effective_active_subscriptions(user.id, db)
    if active:
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "message": "Active teacher billing plan required before using premium features.",
            "redirect": "/plans.html?onboarding=1",
            "cta": "Pay now",
        },
    )


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

