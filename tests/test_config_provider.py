import pytest

from refunddesk.config import ConfigError, load_settings

VARS = ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "LLM_PROVIDER", "VICTIM_MODEL")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in VARS:
        monkeypatch.delenv(var, raising=False)


def test_anthropic_key_selects_sonnet(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    s = load_settings()
    assert (s.provider, s.model) == ("anthropic", "claude-sonnet-5-5")


def test_openrouter_key_alone_selects_glm(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    s = load_settings()
    assert (s.provider, s.model) == ("openrouter", "z-ai/glm-5.3")


def test_explicit_settings_win(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("VICTIM_MODEL", "z-ai/glm-5.3-flash")
    s = load_settings()
    assert (s.provider, s.model) == ("openrouter", "z-ai/glm-5.3-flash")


def test_no_key_is_a_config_error():
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        load_settings()
