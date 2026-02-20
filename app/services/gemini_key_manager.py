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
from typing import Optional

from google import genai

from app.core.config import settings

RECOVERY_SECONDS = 60


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
        print(f"[GeminiKeyManager] Key #{self.index} quota exhausted — will retry after {RECOVERY_SECONDS}s")

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
        print(f"[GeminiKeyManager] Loaded {len(self._keys)} Gemini API key(s)")
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
            print(f"[GeminiKeyManager] Embedding error: {e}")
            return None