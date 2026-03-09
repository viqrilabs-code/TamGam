# app/services/gemini_key_manager.py
# Gemini API Key Rotation Manager
#
# Manages up to 5 Gemini API keys with smart quota rotation.
# Strategy:
#   1. Try keys in order (GEMINI_API_KEY_1 → GEMINI_API_KEY_5)
#   2. On 429 (quota exhausted) or ResourceExhausted → mark key as exhausted, try next
#   3. Exhausted keys recover after RECOVERY_SECONDS (default 60s)
#   4. If ALL keys are exhausted → raise GeminiQuotaExhausted

import time
import threading
import logging
import os
import tempfile
from typing import Optional

from google import genai

from app.core.config import settings

RECOVERY_SECONDS = 60
logger = logging.getLogger("tamgam.gemini_keys")


class GeminiQuotaExhausted(Exception):
    """Raised when all configured Gemini API keys have hit their quota."""
    pass


class _KeyState:
    def __init__(self, key: str, index: int):
        self.key = key
        self.index = index
        self.exhausted_at: Optional[float] = None

    def is_available(self) -> bool:
        if self.exhausted_at is None:
            return True
        if time.monotonic() - self.exhausted_at > RECOVERY_SECONDS:
            self.exhausted_at = None
            return True
        return False

    def mark_exhausted(self):
        self.exhausted_at = time.monotonic()
        logger.warning("Gemini key #%s quota exhausted; retrying after %ss", self.index, RECOVERY_SECONDS)

    def remaining_recovery(self) -> float:
        if self.exhausted_at is None:
            return 0
        return max(0, RECOVERY_SECONDS - (time.monotonic() - self.exhausted_at))

    def get_client(self) -> genai.Client:
        return genai.Client(api_key=self.key)


class GeminiKeyManager:
    """Thread-safe Gemini API key rotation manager. Singleton."""

    def __init__(self):
        self._lock = threading.Lock()
        self._keys: list[_KeyState] = []
        self._initialized = False

    def _load_keys(self):
        raw_keys = [
            settings.gemini_api_key_1,
            settings.gemini_api_key_2,
            settings.gemini_api_key_3,
            settings.gemini_api_key_4,
            settings.gemini_api_key_5,
        ]
        self._keys = [
            _KeyState(k.strip(), i + 1)
            for i, k in enumerate(raw_keys)
            if k and k.strip()
        ]
        if not self._keys:
            raise RuntimeError(
                "No Gemini API keys configured. Set GEMINI_API_KEY_1 (through _5) in your .env"
            )
        logger.info("Loaded %s Gemini API key(s)", len(self._keys))
        self._initialized = True

    def get_available_key(self) -> _KeyState:
        with self._lock:
            if not self._initialized:
                self._load_keys()
            for key_state in self._keys:
                if key_state.is_available():
                    return key_state
            soonest = min(self._keys, key=lambda k: k.remaining_recovery())
            raise GeminiQuotaExhausted(
                f"All {len(self._keys)} Gemini API keys are quota-exhausted. "
                f"Soonest recovery: key #{soonest.index} in {soonest.remaining_recovery():.0f}s"
            )

    def mark_key_exhausted(self, key_state: _KeyState):
        with self._lock:
            key_state.mark_exhausted()

    def status(self) -> list[dict]:
        with self._lock:
            if not self._initialized:
                self._load_keys()
            return [
                {
                    "index": k.index,
                    "available": k.is_available(),
                    "recovery_in": round(k.remaining_recovery()),
                }
                for k in self._keys
            ]


# Module-level singleton
manager = GeminiKeyManager()


# ── Public API ────────────────────────────────────────────────────────────────

def generate_with_fallback(prompt: str, model_name: str = "gemini-1.5-flash") -> str:
    """
    Generate content with automatic key rotation on quota errors.
    Returns the response text. Raises GeminiQuotaExhausted if all keys fail.
    """
    tried = set()

    while True:
        key_state = manager.get_available_key()
        if key_state.index in tried:
            raise GeminiQuotaExhausted("All available keys already tried.")
        tried.add(key_state.index)

        try:
            client = key_state.get_client()
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            return response.text

        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "quota" in err_str or "resource exhausted" in err_str:
                manager.mark_key_exhausted(key_state)
                continue
            raise


def generate_embedding_with_fallback(
    text: str,
    model_name: str = "gemini-embedding-001",
) -> Optional[list]:
    """
    Generate embedding with automatic key rotation on quota errors.
    Returns list of floats (768 dimensions) or None on failure.
    """
    tried = set()

    while True:
        try:
            key_state = manager.get_available_key()
        except GeminiQuotaExhausted:
            return None

        if key_state.index in tried:
            return None
        tried.add(key_state.index)

        try:
            client = key_state.get_client()
            result = client.models.embed_content(
                model=model_name,
                contents=text,
            )
            return result.embeddings[0].values

        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "quota" in err_str or "resource exhausted" in err_str:
                manager.mark_key_exhausted(key_state)
                continue
            logger.exception("Embedding error: %s", e)
            return None


def generate_with_uploaded_file_fallback(
    *,
    prompt: str,
    file_bytes: bytes,
    file_name: str,
    model_name: str = "gemini-2.0-flash",
) -> str:
    """
    Generate content using an uploaded file + prompt with automatic key rotation.
    Returns response text. Raises GeminiQuotaExhausted if all keys fail.
    """
    tried = set()
    suffix = ""
    if "." in (file_name or ""):
        suffix = "." + file_name.rsplit(".", 1)[1]

    while True:
        key_state = manager.get_available_key()
        if key_state.index in tried:
            raise GeminiQuotaExhausted("All available keys already tried.")
        tried.add(key_state.index)

        temp_path = None
        uploaded = None
        try:
            client = key_state.get_client()

            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(file_bytes)
                temp_path = tmp.name

            uploaded = client.files.upload(file=temp_path)
            response = client.models.generate_content(
                model=model_name,
                contents=[uploaded, prompt],
            )
            return response.text or ""

        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "quota" in err_str or "resource exhausted" in err_str:
                manager.mark_key_exhausted(key_state)
                continue
            raise
        finally:
            if uploaded is not None:
                try:
                    client.files.delete(name=uploaded.name)
                except Exception:
                    pass
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass


def generate_with_api_key(
    *,
    prompt: str,
    api_key: str,
    model_name: str = "gemini-2.0-flash",
) -> str:
    """
    Generate content using a user-provided Gemini API key.
    Raises GeminiQuotaExhausted when the key hits quota/rate limits.
    """
    try:
        client = genai.Client(api_key=(api_key or "").strip())
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
        )
        return response.text or ""
    except Exception as e:
        err_str = str(e).lower()
        if "429" in err_str or "quota" in err_str or "resource exhausted" in err_str:
            raise GeminiQuotaExhausted("User Gemini API key quota exhausted.") from e
        raise


def generate_with_uploaded_file_api_key(
    *,
    prompt: str,
    file_bytes: bytes,
    file_name: str,
    api_key: str,
    model_name: str = "gemini-2.0-flash",
) -> str:
    """
    Generate content with a user-provided Gemini API key using uploaded file context.
    Raises GeminiQuotaExhausted when the key hits quota/rate limits.
    """
    suffix = ""
    if "." in (file_name or ""):
        suffix = "." + file_name.rsplit(".", 1)[1]

    temp_path = None
    uploaded = None
    client = None
    try:
        client = genai.Client(api_key=(api_key or "").strip())
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            temp_path = tmp.name

        uploaded = client.files.upload(file=temp_path)
        response = client.models.generate_content(
            model=model_name,
            contents=[uploaded, prompt],
        )
        return response.text or ""
    except Exception as e:
        err_str = str(e).lower()
        if "429" in err_str or "quota" in err_str or "resource exhausted" in err_str:
            raise GeminiQuotaExhausted("User Gemini API key quota exhausted.") from e
        raise
    finally:
        if uploaded is not None and client is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def generate_embedding_with_api_key(
    *,
    text: str,
    api_key: str,
    model_name: str = "gemini-embedding-001",
) -> Optional[list]:
    """
    Generate embedding using a user-provided Gemini API key.
    Raises GeminiQuotaExhausted when the key hits quota/rate limits.
    """
    try:
        client = genai.Client(api_key=(api_key or "").strip())
        result = client.models.embed_content(
            model=model_name,
            contents=text,
        )
        return result.embeddings[0].values
    except Exception as e:
        err_str = str(e).lower()
        if "429" in err_str or "quota" in err_str or "resource exhausted" in err_str:
            raise GeminiQuotaExhausted("User Gemini API key quota exhausted.") from e
        logger.exception("Embedding error (user key): %s", e)
        return None
