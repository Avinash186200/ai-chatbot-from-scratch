"""LLM providers behind one small interface.

``build_provider`` turns settings into a ``Provider``:

* ``scripted`` (default): ``ScriptedProvider`` replays a fixed list of turns
  and, when it runs out, falls back to the offline keyword router in
  ``offline_llm``. It never touches the network; tests, the offline demo and
  the evals use it.
* ``openai_compatible`` or ``anthropic``: ``llm_adapter.HTTPChatProvider``,
  one httpx client that speaks either wire format. With a fallback provider
  configured (``AA_LLM_FALLBACK_*``), the two are wrapped in a
  ``FailoverProvider``.

The internal types (``ToolCall``, ``Completion``, ``LLMError``,
``Provider``) live in ``llm_types`` and are re-exported here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from analytics_assistant.config import Settings
from analytics_assistant.llm_adapter import (
    FailoverProvider,
    HTTPChatProvider,
    OpenAICompatibleProvider,
    ProviderConfig,
)
from analytics_assistant.llm_types import Completion, LLMError, Provider, ToolCall, Usage

__all__ = ["Completion", "LLMError", "Provider", "ToolCall", "Usage", "ScriptedProvider",
           "OpenAICompatibleProvider", "HTTPChatProvider", "FailoverProvider",
           "ProviderConfig", "build_provider"]

HTTP_KINDS = ("openai_compatible", "anthropic")


class ScriptedProvider:
    """Replays scripted turns. A turn is a Completion or a callable of the messages."""

    def __init__(self, turns: list[Completion | Callable[[list[dict[str, Any]]], Completion]]
                 | None = None) -> None:
        self.turns = list(turns) if turns is not None else []
        self.calls: list[list[dict[str, Any]]] = []

    def complete(self, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]]) -> Completion:
        self.calls.append(list(messages))
        if not self.turns:
            from analytics_assistant.offline_llm import offline_turn

            return offline_turn(messages)
        turn = self.turns.pop(0)
        return turn(messages) if callable(turn) else turn


def _http_provider(kind: str, base_url: str, model: str, api_key: str,
                   settings: Settings, name: str) -> HTTPChatProvider:
    if kind not in HTTP_KINDS:
        raise LLMError(f"unknown provider kind {kind!r}")
    return HTTPChatProvider(ProviderConfig(kind, base_url, model, api_key,
                                           settings.llm_timeout_s, settings.llm_max_retries,
                                           name=name))


def build_provider(settings: Settings) -> Provider:
    if settings.llm_provider == "scripted":
        return ScriptedProvider()
    if settings.llm_provider not in HTTP_KINDS:
        raise LLMError(f"unknown AA_LLM_PROVIDER {settings.llm_provider!r}")
    primary = _http_provider(settings.llm_provider, settings.llm_base_url, settings.llm_model,
                             settings.llm_api_key, settings, "primary")
    if not settings.llm_fallback_provider:
        return primary
    fallback = _http_provider(settings.llm_fallback_provider, settings.llm_fallback_base_url,
                              settings.llm_fallback_model, settings.llm_fallback_api_key,
                              settings, "fallback")
    return FailoverProvider([primary, fallback])
