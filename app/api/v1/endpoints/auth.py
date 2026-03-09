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
import json
import re
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
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
    EmailCodeLoginRequest,
    EmailLoginCodeSendRequest,
    EmailLoginCodeSendResponse,
    FirebasePhoneLoginRequest,
    ForgotPasswordCodeSendRequest,
    ForgotPasswordResetRequest,
    GoogleCallbackResponse,
    LoginRequest,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    SignupEmailCodeSendRequest,
    SignupRequest,
    TokenResponse,
)
from app.services.firebase_auth import (
    FirebaseAuthConfigError,
    FirebaseTokenVerificationError,
    verify_phone_id_token,
)
from app.services.email_login_code import (
    EmailLoginCodeConfigError,
    EmailLoginCodeCooldownError,
    EmailLoginCodeDeliveryError,
    EmailLoginCodeInvalidError,
    issue_login_code,
    verify_login_code,
)

router = APIRouter()
TEACHER_PAYOUT_DECLARATION_VERSION = "teacher-payout-v2026-03-07"
PHONE_LOGIN_EMAIL_DOMAIN = "phone.tamgam.local"


# Helper
def _google_oauth_missing_fields() -> list[str]:
    missing = []
    if not (settings.google_client_id or "").strip():
        missing.append("GOOGLE_CLIENT_ID")
    if not (settings.google_client_secret or "").strip():
        missing.append("GOOGLE_CLIENT_SECRET")
    if not (settings.google_redirect_uri or "").strip():
        missing.append("GOOGLE_REDIRECT_URI")
    return missing


def _ensure_google_oauth_configured():
    missing = _google_oauth_missing_fields()
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"Google OAuth is not configured. Missing: {', '.join(missing)}",
        )


def _oauth_success_html(payload: dict) -> HTMLResponse:
    data_json = json.dumps(payload)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Signing you in...</title>
  <style>
    body {{ font-family: sans-serif; background: #fdf6ec; color: #1a1a2e; display: grid; place-items: center; min-height: 100vh; margin: 0; }}
    .card {{ background: white; border: 1px solid #f0e4cc; border-radius: 12px; padding: 20px; width: min(92vw, 420px); }}
    .muted {{ color: #3d3d5c; font-size: 14px; }}
  </style>
</head>
<body>
  <div class="card">
    <h3 style="margin:0 0 8px;">Signing you in...</h3>
    <p class="muted">Please wait while we complete Google login.</p>
  </div>
  <script>
    (function() {{
      const data = {data_json};
      const user = {{
        user_id: data.user_id,
        role: data.role,
        full_name: data.full_name,
        is_subscribed: !!data.is_subscribed,
        is_verified_teacher: !!data.is_verified_teacher
      }};
      try {{
        localStorage.setItem('tg_access', data.access_token);
        localStorage.setItem('tg_refresh', data.refresh_token);
        localStorage.setItem('tg_user', JSON.stringify(user));
      }} catch (_e) {{}}

      let next = '/dashboard.html';
      if (data.role === 'teacher') {{
        next = data.is_subscribed ? '/teacher-dashboard.html' : '/plans.html?onboarding=1';
      }}
      else if (data.role === 'admin') next = '/admin.html';
      window.location.replace(next);
    }})();
  </script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


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


def _normalize_phone_number(raw_phone: str) -> str:
    text = (raw_phone or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Phone number is missing in Firebase token.")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 8:
        raise HTTPException(status_code=400, detail="Firebase phone number is invalid.")
    return f"+{digits}"


def _build_phone_email(phone_e164: str) -> str:
    digits = re.sub(r"\D+", "", phone_e164)
    return f"phone_{digits}@{PHONE_LOGIN_EMAIL_DOMAIN}"


def _get_active_user_by_email_password(db: Session, email: str, password: str) -> User:
    user = db.query(User).filter(and_(User.email.ilike(email), User.is_active == True)).first()
    if not user or not user.hashed_password:
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    if not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    return user


def _get_active_user_by_email(db: Session, email: str) -> User | None:
    normalized = (email or "").strip()
    if not normalized:
        return None
    return db.query(User).filter(and_(User.email.ilike(normalized), User.is_active == True)).first()


# Signup
@router.post("/signup", response_model=TokenResponse, status_code=201, summary="Sign up with email and password")
def signup(payload: SignupRequest, request: Request, db: Session = Depends(get_db)):
    if payload.role == "teacher" and not payload.teacher_declaration_accepted:
        raise HTTPException(
            status_code=400,
            detail="Teacher payout declaration consent is required.",
        )
    if db.query(User).filter(User.email.ilike(str(payload.email))).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    try:
        verify_login_code(db, str(payload.email), payload.verification_code)
    except EmailLoginCodeInvalidError as exc:
        db.commit()
        raise HTTPException(status_code=401, detail=str(exc))
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        auth_provider="email",
        is_active=True,
        is_email_verified=True,
    )
    if payload.role == "teacher":
        ip_addr = request.client.host if request and request.client else None
        user.teacher_payout_declaration_accepted = True
        user.teacher_payout_declaration_accepted_at = datetime.now(timezone.utc)
        user.teacher_payout_declaration_version = (
            payload.teacher_declaration_version or TEACHER_PAYOUT_DECLARATION_VERSION
        )[:64]
        user.teacher_payout_declaration_ip = (ip_addr or "")[:64] or None
    db.add(user)
    db.flush()
    _create_role_profile(user, db)
    response = _build_token_response(user, db)
    db.commit()
    return response


@router.post(
    "/signup/email-code/send",
    response_model=EmailLoginCodeSendResponse,
    summary="Send email verification code for signup",
)
def send_signup_email_code(payload: SignupEmailCodeSendRequest, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.email.ilike(str(payload.email))).first()
    if existing_user:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    try:
        cooldown_seconds = issue_login_code(db, str(payload.email), purpose="signup")
    except EmailLoginCodeCooldownError as exc:
        db.rollback()
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {exc.retry_after_seconds}s before requesting another code.",
        )
    except EmailLoginCodeConfigError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc))
    except EmailLoginCodeDeliveryError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc))

    db.commit()
    return EmailLoginCodeSendResponse(
        message="Signup verification code sent to your email.",
        resend_after_seconds=cooldown_seconds,
    )


# Login
@router.post("/login", response_model=TokenResponse, summary="Log in with email and password")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = _get_active_user_by_email_password(db, str(payload.email), payload.password)
    user.last_login_at = datetime.now(timezone.utc)
    response = _build_token_response(user, db)
    db.commit()
    return response


@router.post(
    "/login/email-code/send",
    response_model=EmailLoginCodeSendResponse,
    summary="Send email verification code for login",
)
def send_login_email_code(payload: EmailLoginCodeSendRequest, db: Session = Depends(get_db)):
    user = _get_active_user_by_email_password(db, str(payload.email), payload.password)
    try:
        cooldown_seconds = issue_login_code(db, user.email, purpose="login")
    except EmailLoginCodeCooldownError as exc:
        db.rollback()
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {exc.retry_after_seconds}s before requesting another code.",
        )
    except EmailLoginCodeConfigError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc))
    except EmailLoginCodeDeliveryError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc))

    db.commit()
    return EmailLoginCodeSendResponse(
        message="Verification code sent to your email.",
        resend_after_seconds=cooldown_seconds,
    )


@router.post(
    "/login/email-code/verify",
    response_model=TokenResponse,
    summary="Log in with email, password, and verification code",
)
def verify_login_email_code(payload: EmailCodeLoginRequest, db: Session = Depends(get_db)):
    user = _get_active_user_by_email_password(db, str(payload.email), payload.password)
    try:
        verify_login_code(db, user.email, payload.verification_code)
    except EmailLoginCodeInvalidError as exc:
        db.commit()
        raise HTTPException(status_code=401, detail=str(exc))

    user.last_login_at = datetime.now(timezone.utc)
    response = _build_token_response(user, db)
    db.commit()
    return response


@router.post(
    "/forgot-password/send-code",
    response_model=EmailLoginCodeSendResponse,
    summary="Send forgot-password verification code",
)
def send_forgot_password_code(
    payload: ForgotPasswordCodeSendRequest,
    db: Session = Depends(get_db),
):
    cooldown_seconds = max(1, int(settings.email_login_code_resend_cooldown_seconds))
    user = _get_active_user_by_email(db, str(payload.email))
    if user and user.hashed_password:
        try:
            cooldown_seconds = issue_login_code(db, user.email, purpose="reset")
        except EmailLoginCodeCooldownError as exc:
            db.rollback()
            raise HTTPException(
                status_code=429,
                detail=f"Please wait {exc.retry_after_seconds}s before requesting another code.",
            )
        except EmailLoginCodeConfigError as exc:
            db.rollback()
            raise HTTPException(status_code=503, detail=str(exc))
        except EmailLoginCodeDeliveryError as exc:
            db.rollback()
            raise HTTPException(status_code=502, detail=str(exc))
    db.commit()
    return EmailLoginCodeSendResponse(
        message="If this email is registered, a reset code has been sent.",
        resend_after_seconds=cooldown_seconds,
    )


@router.post(
    "/forgot-password/reset",
    response_model=MessageResponse,
    summary="Reset password with email verification code",
)
def reset_forgot_password(
    payload: ForgotPasswordResetRequest,
    db: Session = Depends(get_db),
):
    user = _get_active_user_by_email(db, str(payload.email))
    if not user or not user.hashed_password:
        raise HTTPException(status_code=404, detail="No account found for this email.")
    try:
        verify_login_code(db, user.email, payload.verification_code)
    except EmailLoginCodeInvalidError as exc:
        db.commit()
        raise HTTPException(status_code=401, detail=str(exc))

    user.hashed_password = hash_password(payload.new_password)
    db.query(RefreshToken).filter(
        and_(RefreshToken.user_id == user.id, RefreshToken.is_revoked == False)
    ).update({"is_revoked": True}, synchronize_session=False)
    db.commit()
    return MessageResponse(message="Password has been reset. Please log in again.")


@router.post(
    "/firebase-phone",
    response_model=TokenResponse,
    summary="Log in with Firebase phone verification",
)
def firebase_phone_login(payload: FirebasePhoneLoginRequest, db: Session = Depends(get_db)):
    try:
        firebase_claims = verify_phone_id_token(payload.id_token)
    except FirebaseAuthConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except FirebaseTokenVerificationError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    phone_e164 = _normalize_phone_number(str(firebase_claims.get("phone_number") or ""))
    generated_email = _build_phone_email(phone_e164)

    inactive_phone_user = db.query(User).filter(and_(User.phone == phone_e164, User.is_active == False)).first()
    if inactive_phone_user:
        raise HTTPException(status_code=403, detail="This account is deactivated.")

    user = db.query(User).filter(and_(User.phone == phone_e164, User.is_active == True)).first()
    if not user:
        inactive_email_user = db.query(User).filter(and_(User.email == generated_email, User.is_active == False)).first()
        if inactive_email_user:
            raise HTTPException(status_code=403, detail="This account is deactivated.")
        user = db.query(User).filter(and_(User.email == generated_email, User.is_active == True)).first()

    if not user:
        fallback_name = (payload.full_name or "").strip() or str(firebase_claims.get("name") or "").strip()
        user = User(
            email=generated_email,
            hashed_password=None,
            full_name=fallback_name or "tamgam Student",
            role="student",
            auth_provider="email",
            is_active=True,
            is_email_verified=True,
            phone=phone_e164,
        )
        db.add(user)
        db.flush()
        _create_role_profile(user, db)
    else:
        if not user.is_active:
            raise HTTPException(status_code=403, detail="This account is deactivated.")
        if user.phone != phone_e164:
            user.phone = phone_e164
        if payload.full_name and not user.full_name:
            user.full_name = payload.full_name
        if not user.is_email_verified:
            user.is_email_verified = True

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
def google_login(mode: str = "web"):
    _ensure_google_oauth_configured()
    state = "json" if (mode or "").strip().lower() == "json" else "web"
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
        "state": state,
    }
    qs = urlencode(params)
    return RedirectResponse(url=f"https://accounts.google.com/o/oauth2/v2/auth?{qs}")


@router.get("/google/callback", response_model=GoogleCallbackResponse, summary="Google OAuth callback")
def google_callback(code: str, state: str | None = None, db: Session = Depends(get_db)):
    _ensure_google_oauth_configured()
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

    response_payload = GoogleCallbackResponse(
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
    if (state or "web").strip().lower() == "json":
        return response_payload
    return _oauth_success_html(response_payload.model_dump(mode="json"))
