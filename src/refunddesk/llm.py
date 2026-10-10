"""The single place where RefundDesk talks to a model provider.

The agent works with a provider-neutral message list:

* ``{"role": "user", "content": str}``
* ``{"role": "assistant", "content": str, "tool_calls": [ToolCall...], "native": ...}``
* ``{"role": "tool_results", "results": [{"tool_call_id", "name", "content", "is_error"}]}``

This module converts that list to the provider's wire format, sends it, and
normalises the reply. Provider clients are wrapped with ``neatlogs.wrap`` so each
model call is captured as one LLM span.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import neatlogs

from .config import OPENROUTER_BASE_URL, PROJECT_ROOT, ConfigError, Settings

# USD per million tokens (input, output). Used only to report run cost; an
# unknown model reports cost as null rather than a guess.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

logger = logging.getLogger(__name__)

_SAMPLING_CACHE = PROJECT_ROOT / ".cache" / "refunddesk" / "sampling.json"
_TEMPERATURE_REJECTED = "`temperature` is deprecated for this model"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text exactly as produced by the model


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str | None
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None
    temperature_sent: bool = True
    native: Any = field(default=None, repr=False)


def _price(model: str) -> tuple[float, float] | None:
    env_in, env_out = os.environ.get("VICTIM_PRICE_IN"), os.environ.get("VICTIM_PRICE_OUT")
    if env_in and env_out:
        return float(env_in), float(env_out)
    return PRICES_PER_MTOK.get(model)


def _load_sampling_cache() -> dict[str, bool]:
    try:
        return json.loads(_SAMPLING_CACHE.read_text())
    except (OSError, ValueError):
        return {}


def _remember_temperature_unsupported(provider: str, model: str) -> None:
    cache = _load_sampling_cache()
    cache[f"{provider}:{model}"] = False
    try:
        _SAMPLING_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _SAMPLING_CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True))
    except OSError:
        pass


class LLMClient:
    """Provider-neutral chat client with tool calling."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = settings.provider
        self.model = settings.model
        self._temperature_ok = _load_sampling_cache().get(f"{self.provider}:{self.model}", True)
        self.requests_sent = 0  # every provider request, including rejected ones
        if self.provider == "anthropic":
            import anthropic

            self._anthropic = anthropic
            self._client = neatlogs.wrap(anthropic.Anthropic(max_retries=3))
        elif self.provider == "openrouter":
            import openai

            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                raise ConfigError("OPENROUTER_API_KEY must be set when LLM_PROVIDER=openrouter")
            self._client = neatlogs.wrap(
                openai.OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key, max_retries=3)
            )
        else:  # pragma: no cover - guarded by load_settings
            raise ConfigError(f"unsupported provider {self.provider!r}")

    # ------------------------------------------------------------------ public

    def chat(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        if self.provider == "anthropic":
            return self._chat_anthropic(system, messages, tools)
        return self._chat_openrouter(system, messages, tools)

    # --------------------------------------------------------------- anthropic

    def _anthropic_messages(self, messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for msg in messages:
            role = msg["role"]
            if role == "user":
                out.append({"role": "user", "content": msg["content"]})
            elif role == "assistant":
                native = msg.get("native")
                if native is not None and msg.get("native_provider") == "anthropic":
                    out.append({"role": "assistant", "content": native})
                else:
                    blocks: list[dict] = []
                    if msg.get("content"):
                        blocks.append({"type": "text", "text": msg["content"]})
                    for call in msg.get("tool_calls", []):
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": call.id,
                                "name": call.name,
                                "input": json.loads(call.arguments),
                            }
                        )
                    out.append({"role": "assistant", "content": blocks})
            elif role == "tool_results":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": r["tool_call_id"],
                                "content": r["content"],
                                "is_error": bool(r.get("is_error")),
                            }
                            for r in msg["results"]
                        ],
                    }
                )
        return out

    def _chat_anthropic(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.settings.max_output_tokens,
            "system": system,
            "messages": self._anthropic_messages(messages),
            "tools": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["parameters"],
                    "strict": True,
                }
                for t in tools
            ],
        }
        # The 1.x SDK has no typed `temperature` argument, so it goes in the body.
        temperature_sent = self._temperature_ok
        if temperature_sent:
            params["extra_body"] = {"temperature": self.settings.temperature}
        try:
            self.requests_sent += 1
            resp = self._client.messages.create(**params)
        except self._anthropic.BadRequestError as exc:
            if not (temperature_sent and _TEMPERATURE_REJECTED in str(exc)):
                raise
            # The model rejects sampling parameters; remember that and resend without.
            logger.info("%s rejects temperature; resending without it", self.model)
            self._temperature_ok = False
            _remember_temperature_unsupported(self.provider, self.model)
            params.pop("extra_body", None)
            temperature_sent = False
            self.requests_sent += 1
            resp = self._client.messages.create(**params)

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=json.dumps(block.input)))
        usage = resp.usage
        cache_read = getattr(usage, "cache_read_input_tokens", None) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", None) or 0
        cost = None
        price = _price(self.model)
        if price is not None:
            p_in, p_out = price
            cost = (
                usage.input_tokens * p_in
                + usage.output_tokens * p_out
                + cache_read * p_in * 0.1
                + cache_write * p_in * 1.25
            ) / 1_000_000
        return LLMResponse(
            text="\n".join(text_parts).strip(),
            tool_calls=calls,
            stop_reason=resp.stop_reason,
            model=resp.model,
            input_tokens=usage.input_tokens + cache_read + cache_write,
            output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=cost,
            temperature_sent=temperature_sent,
            native=[block.to_dict() for block in resp.content],
        )

    # -------------------------------------------------------------- openrouter

    @staticmethod
    def _openai_messages(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for msg in messages:
            role = msg["role"]
            if role == "user":
                out.append({"role": "user", "content": msg["content"]})
            elif role == "assistant":
                entry: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or None}
                if msg.get("tool_calls"):
                    entry["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": c.arguments},
                        }
                        for c in msg["tool_calls"]
                    ]
                out.append(entry)
            elif role == "tool_results":
                for r in msg["results"]:
                    out.append(
                        {"role": "tool", "tool_call_id": r["tool_call_id"], "content": r["content"]}
                    )
        return out

    def _chat_openrouter(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        self.requests_sent += 1
        resp = self._client.chat.completions.create(
            model=self.model,
            temperature=self.settings.temperature,
            max_tokens=self.settings.max_output_tokens,
            messages=self._openai_messages(system, messages),
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    },
                }
                for t in tools
            ],
        )
        choice = resp.choices[0]
        calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "")
            for c in (choice.message.tool_calls or [])
        ]
        usage = resp.usage
        in_tok = getattr(usage, "prompt_tokens", None) if usage else None
        out_tok = getattr(usage, "completion_tokens", None) if usage else None
        cost = getattr(usage, "cost", None) if usage else None
        if cost is None and in_tok is not None and out_tok is not None:
            price = _price(self.model)
            if price is not None:
                cost = (in_tok * price[0] + out_tok * price[1]) / 1_000_000
        return LLMResponse(
            text=(choice.message.content or "").strip(),
            tool_calls=calls,
            stop_reason=choice.finish_reason,
            model=resp.model or self.model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            temperature_sent=True,
        )
