"""Runtime configuration read from the environment (and `.env` when present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Values already in the process environment win over `.env`.
load_dotenv(PROJECT_ROOT / ".env", override=False)

SUPPORTED_PROVIDERS = ("anthropic", "openrouter")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    temperature: float = 0.0
    max_output_tokens: int = 1024
    history_window: int = 24


DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "openrouter": "z-ai/glm-5.3"}


def _default_provider() -> str:
    """Anthropic when its key is set, otherwise OpenRouter when its key is set."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    raise ConfigError("set ANTHROPIC_API_KEY (Claude Sonnet 5.5) or OPENROUTER_API_KEY (GLM 5.3)")


def load_settings() -> Settings:
    """`LLM_PROVIDER` and `VICTIM_MODEL` win when set; otherwise both follow the available key."""
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower() or _default_provider()
    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigError(
            f"LLM_PROVIDER must be one of {SUPPORTED_PROVIDERS}, got {provider!r}"
        )
    model = os.environ.get("VICTIM_MODEL", "").strip() or DEFAULT_MODELS[provider]
    return Settings(
        provider=provider,
        model=model,
        history_window=int(os.environ.get("REFUNDDESK_HISTORY_WINDOW", "24")),
    )
