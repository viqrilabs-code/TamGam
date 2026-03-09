from types import SimpleNamespace

import pytest

from app.services import gemini_key_manager as gkm


def test_key_state_recovery(monkeypatch):
    t = {"v": 100.0}
    monkeypatch.setattr(gkm.time, "monotonic", lambda: t["v"])
    ks = gkm._KeyState("k", 1)
    assert ks.is_available() is True
    ks.mark_exhausted()
    assert ks.is_available() is False
    t["v"] = 100.0 + gkm.RECOVERY_SECONDS + 1
    assert ks.is_available() is True


def test_key_state_get_client(monkeypatch):
    called = {}

    class FakeClient:
        def __init__(self, api_key):
            called["key"] = api_key

    monkeypatch.setattr(gkm.genai, "Client", FakeClient)
    ks = gkm._KeyState("abc123", 1)
    ks.get_client()
    assert called["key"] == "abc123"


def test_manager_load_keys_and_status(monkeypatch):
    mgr = gkm.GeminiKeyManager()
    monkeypatch.setattr(gkm.settings, "gemini_api_key_1", "k1")
    monkeypatch.setattr(gkm.settings, "gemini_api_key_2", "k2")
    monkeypatch.setattr(gkm.settings, "gemini_api_key_3", "")
    monkeypatch.setattr(gkm.settings, "gemini_api_key_4", "")
    monkeypatch.setattr(gkm.settings, "gemini_api_key_5", "")
    mgr._load_keys()
    status = mgr.status()
    assert len(status) == 2
    assert status[0]["available"] is True


def test_manager_raises_when_no_keys(monkeypatch):
    mgr = gkm.GeminiKeyManager()
    monkeypatch.setattr(gkm.settings, "gemini_api_key_1", "")
    monkeypatch.setattr(gkm.settings, "gemini_api_key_2", "")
    monkeypatch.setattr(gkm.settings, "gemini_api_key_3", "")
    monkeypatch.setattr(gkm.settings, "gemini_api_key_4", "")
    monkeypatch.setattr(gkm.settings, "gemini_api_key_5", "")
    with pytest.raises(RuntimeError):
        mgr._load_keys()


def test_generate_with_fallback_rotates_on_quota(monkeypatch):
    class K:
        def __init__(self, idx, client):
            self.index = idx
            self._client = client

        def get_client(self):
            return self._client

    class C1:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                raise Exception("429 quota exceeded")

    class C2:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                return SimpleNamespace(text="ok")

    keys = [K(1, C1()), K(2, C2())]
    exhausted = []

    class Mgr:
        def get_available_key(self):
            if not keys:
                raise gkm.GeminiQuotaExhausted("none")
            return keys.pop(0)

        def mark_key_exhausted(self, key_state):
            exhausted.append(key_state.index)

    monkeypatch.setattr(gkm, "manager", Mgr())
    out = gkm.generate_with_fallback("hello")
    assert out == "ok"
    assert exhausted == [1]


def test_generate_embedding_with_fallback_returns_none_when_all_exhausted(monkeypatch):
    class Mgr:
        def get_available_key(self):
            raise gkm.GeminiQuotaExhausted("all exhausted")

    monkeypatch.setattr(gkm, "manager", Mgr())
    assert gkm.generate_embedding_with_fallback("x") is None


def test_generate_with_uploaded_file_fallback_success(monkeypatch):
    class Uploaded:
        name = "file-id"

    deleted = []

    class Files:
        @staticmethod
        def upload(file):
            return Uploaded()

        @staticmethod
        def delete(name):
            deleted.append(name)

    class Models:
        @staticmethod
        def generate_content(**kwargs):
            return SimpleNamespace(text="from-file")

    class Client:
        files = Files()
        models = Models()

    class KeyState:
        index = 1

        @staticmethod
        def get_client():
            return Client()

    class Mgr:
        @staticmethod
        def get_available_key():
            return KeyState()

        @staticmethod
        def mark_key_exhausted(_):
            raise AssertionError("Should not exhaust")

    monkeypatch.setattr(gkm, "manager", Mgr())
    out = gkm.generate_with_uploaded_file_fallback(
        prompt="p",
        file_bytes=b"abc",
        file_name="a.txt",
    )
    assert out == "from-file"
    assert deleted == ["file-id"]


def test_generate_with_api_key_quota_raises(monkeypatch):
    class FakeClient:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                raise Exception("Resource exhausted 429")

        def __init__(self, api_key):
            pass

    monkeypatch.setattr(gkm.genai, "Client", FakeClient)
    with pytest.raises(gkm.GeminiQuotaExhausted):
        gkm.generate_with_api_key(prompt="p", api_key="a" * 30)


def test_generate_embedding_with_api_key_non_quota_returns_none(monkeypatch):
    class FakeClient:
        class models:
            @staticmethod
            def embed_content(**kwargs):
                raise Exception("network boom")

        def __init__(self, api_key):
            pass

    monkeypatch.setattr(gkm.genai, "Client", FakeClient)
    assert gkm.generate_embedding_with_api_key(text="x", api_key="a" * 30) is None

