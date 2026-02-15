# app/api/v1/endpoints/auth.py
# Authentication endpoints
#
# POST /auth/signup          -- email + password signup
# POST /auth/login           -- returns access + refresh token
# POST /auth/refresh         -- new access token from refresh token
# POST /auth/logout          -- revoke refresh token
# GET  /auth/google          -- redirect to Google OAuth
# GET  /auth/google/callback -- exchange code for tokens

from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import resolve_user_marks
from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_token_expiry,
    hash_password,
    hash_token,
    decode_token,
    verify_password,
)
from app.db.session import get_db
import app.db.base  # noqa: F401 -- registers all models so relationships resolve
from app.models.student import StudentProfile
from app.models.teacher import TeacherProfile
from app.models.user import RefreshToken, User
from app.schemas.auth import (
    AccessTokenResponse,
    GoogleCallbackResponse,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    SignupRequest,
    TokenResponse,
)

router = APIRouter()


# Helper
def _build_token_response(user: User, db: Session) -> TokenResponse:
    access_token = create_access_token(user.id, user.role)
    refresh_token = create_refresh_token(user.id)
    db_token = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh_token),
        expires_at=get_token_expiry(days=settings.refresh_token_expire_days),
    )
    db.add(db_token)
    db.flush()
    marks = resolve_user_marks(user, db)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
        user_id=user.id,
        role=user.role,
        full_name=user.full_name,
        is_subscribed=marks["is_subscribed"],
        is_verified_teacher=marks["is_verified_teacher"],
    )


def _create_role_profile(user: User, db: Session) -> None:
    if user.role == "student":
        db.add(StudentProfile(user_id=user.id))
    elif user.role == "teacher":
        db.add(TeacherProfile(user_id=user.id))


# Signup
@router.post("/signup", response_model=TokenResponse, status_code=201, summary="Sign up with email and password")
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        auth_provider="email",
        is_active=True,
        is_email_verified=False,
    )
    db.add(user)
    db.flush()
    _create_role_profile(user, db)
    response = _build_token_response(user, db)
    db.commit()
    return response


# Login
@router.post("/login", response_model=TokenResponse, summary="Log in with email and password")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(and_(User.email == payload.email, User.is_active == True)).first()
    if not user or not user.hashed_password:
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    user.last_login_at = datetime.now(timezone.utc)
    response = _build_token_response(user, db)
    db.commit()
    return response


# Refresh
@router.post("/refresh", response_model=AccessTokenResponse, summary="Get a new access token")
def refresh_token(payload: RefreshRequest, db: Session = Depends(get_db)):
    token_payload = decode_token(payload.refresh_token)
    if not token_payload or token_payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token.")
    user_id = token_payload.get("sub")
    token_hash = hash_token(payload.refresh_token)
    db_token = db.query(RefreshToken).filter(
        and_(
            RefreshToken.user_id == user_id,
            RefreshToken.token_hash == token_hash,
            RefreshToken.is_revoked == False,
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
    ).first()
    if not db_token:
        raise HTTPException(status_code=401, detail="Refresh token has been revoked or expired.")
    db_token.is_revoked = True
    user = db.query(User).filter(and_(User.id == user_id, User.is_active == True)).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")
    new_access_token = create_access_token(user.id, user.role)
    db.commit()
    return AccessTokenResponse(access_token=new_access_token, expires_in=settings.access_token_expire_minutes * 60)


# Logout
@router.post("/logout", response_model=MessageResponse, summary="Log out and revoke refresh token")
def logout(payload: LogoutRequest, db: Session = Depends(get_db)):
    db_token = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_token(payload.refresh_token)).first()
    if db_token:
        db_token.is_revoked = True
        db.commit()
    return MessageResponse(message="Logged out successfully.")


# Google OAuth
@router.get("/google", summary="Redirect to Google OAuth")
def google_login():
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured.")
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(url=f"https://accounts.google.com/o/oauth2/v2/auth?{qs}")


@router.get("/google/callback", response_model=GoogleCallbackResponse, summary="Google OAuth callback")
def google_callback(code: str, db: Session = Depends(get_db)):
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured.")
    try:
        with httpx.Client() as client:
            token_resp = client.post("https://oauth2.googleapis.com/token", data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            })
            token_resp.raise_for_status()
            google_tokens = token_resp.json()
            userinfo = client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {google_tokens['access_token']}"},
            ).json()
    except httpx.HTTPError:
        raise HTTPException(status_code=400, detail="Failed to authenticate with Google.")

    email = userinfo.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Google account did not provide an email address.")

    google_id = userinfo.get("sub")
    full_name = userinfo.get("name", email)
    avatar_url = userinfo.get("picture")

    user = db.query(User).filter((User.google_id == google_id) | (User.email == email)).first()
    is_new_user = False

    if user:
        if not user.google_id:
            user.google_id = google_id
        if not user.avatar_url and avatar_url:
            user.avatar_url = avatar_url
        user.last_login_at = datetime.now(timezone.utc)
    else:
        is_new_user = True
        user = User(
            email=email, full_name=full_name, avatar_url=avatar_url,
            google_id=google_id, role="student", auth_provider="google",
            is_active=True, is_email_verified=True,
        )
        db.add(user)
        db.flush()
        _create_role_profile(user, db)

    access_token = create_access_token(user.id, user.role)
    refresh_token_str = create_refresh_token(user.id)
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh_token_str),
        expires_at=get_token_expiry(days=settings.refresh_token_expire_days),
    ))
    marks = resolve_user_marks(user, db)
    db.commit()

    return GoogleCallbackResponse(
        access_token=access_token,
        refresh_token=refresh_token_str,
        expires_in=settings.access_token_expire_minutes * 60,
        user_id=user.id,
        role=user.role,
        full_name=user.full_name,
        is_new_user=is_new_user,
        is_subscribed=marks["is_subscribed"],
        is_verified_teacher=marks["is_verified_teacher"],
    )