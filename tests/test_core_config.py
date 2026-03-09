from app.core.config import Settings, get_settings


def test_settings_helpers():
    s = Settings(jwt_secret_key="x", app_env="production", allowed_origins=" http://a.com, http://b.com ")
    assert s.is_production is True
    assert s.cors_origins == ["http://a.com", "http://b.com"]


def test_get_settings_is_cached():
    get_settings.cache_clear()
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_google_oauth_aliases_are_supported():
    s = Settings(
        jwt_secret_key="x",
        GOOGLE_OAUTH_CLIENT_ID="cid",
        GOOGLE_OAUTH_CLIENT_SECRET="secret",
        GOOGLE_OAUTH_REDIRECT_URI="https://example.com/callback",
    )
    assert s.google_client_id == "cid"
    assert s.google_client_secret == "secret"
    assert s.google_redirect_uri == "https://example.com/callback"


def test_firebase_aliases_are_supported():
    s = Settings(
        jwt_secret_key="x",
        FIREBASE_PROJECT_ID="my-firebase-project",
        FIREBASE_CREDENTIALS_JSON='{"type":"service_account"}',
        FIREBASE_CREDENTIALS_PATH="/tmp/firebase-sa.json",
    )
    assert s.firebase_project_id == "my-firebase-project"
    assert s.firebase_credentials_json == '{"type":"service_account"}'
    assert s.firebase_credentials_path == "/tmp/firebase-sa.json"
