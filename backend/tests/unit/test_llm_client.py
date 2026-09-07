"""LLM client header tests: provider proxies (e.g. Console Go) that route
via extra headers like x-opencode-session get them on every call."""
import sys
import types

from app.config import settings
from app.llm.client import OpenAICompatibleClient


def test_extra_headers_parsing(monkeypatch):
    monkeypatch.setattr(settings, "llm_extra_headers",
                        '{"x-opencode-session":"abc123"}')
    assert settings.llm_extra_headers_dict == {"x-opencode-session": "abc123"}
    monkeypatch.setattr(settings, "llm_extra_headers", "")
    assert settings.llm_extra_headers_dict == {}
    monkeypatch.setattr(settings, "llm_extra_headers", "not-json{")
    assert settings.llm_extra_headers_dict == {}
    monkeypatch.setattr(settings, "llm_extra_headers", '["list"]')
    assert settings.llm_extra_headers_dict == {}


def test_client_passes_default_headers(monkeypatch):
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_mod = types.ModuleType("openai")
    fake_mod.AsyncOpenAI = FakeAsyncOpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_mod)
    monkeypatch.setattr(settings, "llm_extra_headers",
                        '{"x-opencode-session":"sess-1"}')
    OpenAICompatibleClient()
    assert captured.get("default_headers") == {"x-opencode-session": "sess-1"}


def test_client_no_headers_when_unset(monkeypatch):
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_mod = types.ModuleType("openai")
    fake_mod.AsyncOpenAI = FakeAsyncOpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_mod)
    monkeypatch.setattr(settings, "llm_extra_headers", "{}")
    OpenAICompatibleClient()
    assert captured.get("default_headers") is None
