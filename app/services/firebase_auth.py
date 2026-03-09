# app/services/firebase_auth.py
# Firebase Admin SDK helpers for verifying Firebase phone-auth ID tokens.

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.core.config import settings


class FirebaseAuthConfigError(RuntimeError):
    """Raised when Firebase Admin SDK cannot be initialized from current settings."""


class FirebaseTokenVerificationError(ValueError):
    """Raised when a Firebase ID token is invalid or missing required claims."""


@lru_cache(maxsize=1)
def _firebase_modules():
    try:
        import firebase_admin
        from firebase_admin import auth as firebase_auth
        from firebase_admin import credentials
    except Exception as exc:  # pragma: no cover - dependency/import environment specific
        raise FirebaseAuthConfigError(
            "Firebase Admin SDK is unavailable. Install firebase-admin and configure credentials."
        ) from exc
    return firebase_admin, firebase_auth, credentials


@lru_cache(maxsize=1)
def _firebase_app():
    firebase_admin, _firebase_auth, credentials = _firebase_modules()
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    credentials_json = (settings.firebase_credentials_json or "").strip()
    credentials_path = (settings.firebase_credentials_path or "").strip()
    project_id = (settings.firebase_project_id or "").strip()
    options = {"projectId": project_id} if project_id else None

    try:
        if credentials_json:
            cert = credentials.Certificate(json.loads(credentials_json))
            return firebase_admin.initialize_app(cert, options=options)
        if credentials_path:
            cert = credentials.Certificate(credentials_path)
            return firebase_admin.initialize_app(cert, options=options)
        return firebase_admin.initialize_app(options=options)
    except json.JSONDecodeError as exc:
        raise FirebaseAuthConfigError("FIREBASE_CREDENTIALS_JSON is not valid JSON.") from exc
    except Exception as exc:
        raise FirebaseAuthConfigError(
            "Failed to initialize Firebase Admin SDK. Check Firebase credentials configuration."
        ) from exc


def verify_phone_id_token(id_token: str) -> dict[str, Any]:
    token = (id_token or "").strip()
    if not token:
        raise FirebaseTokenVerificationError("id_token is required.")

    _firebase_admin, firebase_auth, _credentials = _firebase_modules()
    app = _firebase_app()

    try:
        decoded = firebase_auth.verify_id_token(token, app=app)
    except Exception as exc:
        raise FirebaseTokenVerificationError("Invalid or expired Firebase ID token.") from exc

    phone_number = (decoded.get("phone_number") or "").strip()
    if not phone_number:
        raise FirebaseTokenVerificationError(
            "Firebase token does not include a verified phone number."
        )
    return decoded
