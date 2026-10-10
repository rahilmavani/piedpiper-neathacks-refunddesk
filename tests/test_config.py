import pytest

from refunddesk.config import ConfigError, load_settings


def test_defaults_to_anthropic(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("VICTIM_MODEL", "some-model")
    settings = load_settings()
    assert settings.provider == "anthropic" and settings.temperature == 0.0


def test_rejects_unknown_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    monkeypatch.setenv("VICTIM_MODEL", "m")
    with pytest.raises(ConfigError):
        load_settings()


def test_requires_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("VICTIM_MODEL", "")
    with pytest.raises(ConfigError):
        load_settings()
