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


def load_settings() -> Settings:
    provider = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower() or "anthropic"
    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigError(
            f"LLM_PROVIDER must be one of {SUPPORTED_PROVIDERS}, got {provider!r}"
        )
    model = os.environ.get("VICTIM_MODEL", "").strip()
    if not model:
        raise ConfigError("VICTIM_MODEL must be set to the model id the agent should use")
    return Settings(
        provider=provider,
        model=model,
        history_window=int(os.environ.get("REFUNDDESK_HISTORY_WINDOW", "24")),
    )
