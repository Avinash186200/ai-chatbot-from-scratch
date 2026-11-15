"""Vendor-neutral LLM adapter over plain ``httpx``.

One class, ``HTTPChatProvider``, speaks two wire formats:

* ``openai_compatible``: ``POST <base>/chat/completions`` (hosted routers,
  vLLM, Ollama, LM Studio and most gateways);
* ``anthropic``: ``POST <base>/v1/messages`` (the Anthropic Messages API).

Both are normalised to the internal shape in ``llm_types``: a ``Completion``
with text, a list of ``ToolCall(id, name, arguments: dict)`` and token usage.
The agent never sees a vendor format and this is the only module that knows
one. No vendor SDK is imported anywhere (``scripts/check_vendor_imports.py``
enforces it), which keeps the dependency tree small and the transport
mockable with ``httpx.MockTransport``.

Reliability:

* every attempt has a timeout (connect + read);
* 408, 409, 425, 429, 500, 502, 503, 504, 529, network errors and timeouts
  are retried with exponential backoff and full jitter, honouring
  ``Retry-After`` (capped), within ``max_retries``;
* other 4xx are not retried (a bad request stays bad);
* ``FailoverProvider`` tries a second configured provider when the first
  one is exhausted.

Error messages carry the status code and provider name only, never the
response body (some proxies echo request headers, including the key).
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from analytics_assistant.llm_types import Completion, LLMError, ToolCall, Usage

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
ANTHROPIC_VERSION = "2023-06-01"
MAX_RETRY_AFTER_S = 30.0


@dataclass(frozen=True)
class ProviderConfig:
    kind: str                      # "openai_compatible" | "anthropic"
    base_url: str
    model: str
    api_key: str = field(default="", repr=False)
    timeout_s: float = 60.0
    max_retries: int = 3
    max_tokens: int = 1024
    name: str = ""                 # label used in logs, spans and errors

    @property
    def label(self) -> str:
        return self.name or self.kind


# ---------------------------------------------------------------- wire formats


def _openai_request(cfg: ProviderConfig, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]]) -> tuple[str, dict[str, str], dict[str, Any]]:
    body: dict[str, Any] = {"model": cfg.model, "messages": messages, "temperature": 0}
    if tools:
        body["tools"] = tools
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    return "/chat/completions", headers, body


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {"__invalid_json__": raw}
    return value if isinstance(value, dict) else {"__invalid_json__": raw}


def _openai_response(data: dict[str, Any]) -> Completion:
    message = data["choices"][0]["message"]
    calls = [ToolCall(raw.get("id", ""), (raw.get("function") or {}).get("name", ""),
                      _parse_args((raw.get("function") or {}).get("arguments")))
             for raw in message.get("tool_calls") or []]
    usage = data.get("usage") or {}
    return Completion(message.get("content") or "", calls,
                      Usage(int(usage.get("prompt_tokens") or 0),
                            int(usage.get("completion_tokens") or 0)),
                      model=str(data.get("model") or ""))


def to_anthropic(messages: list[dict[str, Any]], tools: list[dict[str, Any]]
                 ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Internal (OpenAI-shaped) messages and tools -> (system, messages, tools) for Messages."""
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []

    def push(role: str, block: dict[str, Any]) -> None:
        # Messages wants alternating roles; consecutive same-role blocks merge.
        if out and out[-1]["role"] == role:
            out[-1]["content"].append(block)
        else:
            out.append({"role": role, "content": [block]})

    for m in messages:
        role = m.get("role")
        if role == "system":
            system_parts.append(str(m.get("content") or ""))
        elif role == "user":
            push("user", {"type": "text", "text": str(m.get("content") or "")})
        elif role == "assistant":
            if m.get("content"):
                push("assistant", {"type": "text", "text": str(m["content"])})
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                push("assistant", {"type": "tool_use", "id": call.get("id", ""),
                                   "name": fn.get("name", ""),
                                   "input": _parse_args(fn.get("arguments"))})
        elif role == "tool":
            push("user", {"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""),
                          "content": str(m.get("content") or "")})
    anth_tools = [{"name": t["function"]["name"],
                   "description": t["function"].get("description", ""),
                   "input_schema": t["function"].get("parameters")
                   or {"type": "object", "properties": {}}}
                  for t in tools if t.get("type") == "function"]
    return "\n\n".join(p for p in system_parts if p), out, anth_tools


def _anthropic_request(cfg: ProviderConfig, messages: list[dict[str, Any]],
                       tools: list[dict[str, Any]]) -> tuple[str, dict[str, str], dict[str, Any]]:
    system, msgs, anth_tools = to_anthropic(messages, tools)
    body: dict[str, Any] = {"model": cfg.model, "max_tokens": cfg.max_tokens,
                            "messages": msgs, "temperature": 0}
    if system:
        body["system"] = system
    if anth_tools:
        body["tools"] = anth_tools
    headers = {"anthropic-version": ANTHROPIC_VERSION}
    if cfg.api_key:
        headers["x-api-key"] = cfg.api_key
    return "/v1/messages", headers, body


def _anthropic_response(data: dict[str, Any]) -> Completion:
    text: list[str] = []
    calls: list[ToolCall] = []
    for block in data["content"]:
        if block.get("type") == "text":
            text.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            calls.append(ToolCall(block.get("id", ""), block.get("name", ""),
                                  _parse_args(block.get("input"))))
    usage = data.get("usage") or {}
    return Completion("".join(text), calls,
                      Usage(int(usage.get("input_tokens") or 0),
                            int(usage.get("output_tokens") or 0)),
                      model=str(data.get("model") or ""))


WIRE_FORMATS: dict[str, tuple[Callable[..., tuple[str, dict[str, str], dict[str, Any]]],
                              Callable[[dict[str, Any]], Completion]]] = {
    "openai_compatible": (_openai_request, _openai_response),
    "anthropic": (_anthropic_request, _anthropic_response),
}


# ---------------------------------------------------------------- provider


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return min(MAX_RETRY_AFTER_S, max(0.0, float(raw)))
    except ValueError:
        return None


class HTTPChatProvider:
    """One configured endpoint. ``complete`` returns a normalised ``Completion``."""

    def __init__(self, cfg: ProviderConfig, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 rng: random.Random | None = None,
                 base_delay_s: float = 0.5, max_delay_s: float = 8.0) -> None:
        if cfg.kind not in WIRE_FORMATS:
            raise LLMError(f"unknown provider kind {cfg.kind!r}")
        if not cfg.model:
            raise LLMError(f"no model configured for provider {cfg.label}")
        self.cfg = cfg
        self._build, self._parse = WIRE_FORMATS[cfg.kind]
        self._client = httpx.Client(base_url=cfg.base_url.rstrip("/"),
                                    timeout=httpx.Timeout(cfg.timeout_s, connect=10.0),
                                    transport=transport, follow_redirects=False)
        self._sleep = sleep
        self._rng = rng or random.Random()
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s

    def _backoff(self, attempt: int, hint: float | None) -> float:
        if hint is not None:
            return hint
        return self._rng.uniform(0, min(self.max_delay_s, self.base_delay_s * 2 ** attempt))

    def complete(self, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]]) -> Completion:
        from analytics_assistant import telemetry

        path, headers, body = self._build(self.cfg, messages, tools)
        label = self.cfg.label
        with telemetry.span("llm.complete", {"llm.provider": label, "llm.model": self.cfg.model,
                                             "llm.kind": self.cfg.kind}) as sp:
            last_error = "no attempt made"
            for attempt in range(self.cfg.max_retries + 1):
                hint: float | None = None
                try:
                    resp = self._client.post(path, json=body, headers=headers)
                except httpx.TimeoutException:
                    last_error = f"{label}: request timed out"
                except httpx.HTTPError as exc:
                    last_error = f"{label}: request failed ({type(exc).__name__})"
                else:
                    if resp.status_code == 200:
                        try:
                            out = self._parse(resp.json())
                        except (ValueError, KeyError, IndexError, TypeError):
                            raise LLMError(f"{label}: unexpected response body") from None
                        out.provider = label
                        out.model = out.model or self.cfg.model
                        telemetry.record_usage(label, out.model, out.usage.input_tokens,
                                               out.usage.output_tokens)
                        sp.set_attribute("llm.attempts", attempt + 1)
                        sp.set_attribute("llm.input_tokens", out.usage.input_tokens)
                        sp.set_attribute("llm.output_tokens", out.usage.output_tokens)
                        return out
                    last_error = f"{label}: HTTP {resp.status_code}"
                    if resp.status_code not in RETRYABLE_STATUS:
                        sp.set_attribute("llm.attempts", attempt + 1)
                        raise LLMError(last_error)
                    hint = _retry_after(resp)
                if attempt < self.cfg.max_retries:
                    self._sleep(self._backoff(attempt, hint))
            sp.set_attribute("llm.attempts", self.cfg.max_retries + 1)
            raise LLMError(f"{last_error} (after {self.cfg.max_retries + 1} attempts)")


class FailoverProvider:
    """Try providers in order; the next one is used only when the previous one fails."""

    def __init__(self, providers: Sequence[Any]) -> None:
        if not providers:
            raise LLMError("FailoverProvider needs at least one provider")
        self.providers = list(providers)

    def complete(self, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]]) -> Completion:
        errors = []
        for provider in self.providers:
            try:
                return provider.complete(messages, tools)
            except LLMError as exc:
                errors.append(str(exc))
        raise LLMError("all providers failed: " + "; ".join(errors))


def OpenAICompatibleProvider(base_url: str, model: str, api_key: str, timeout_s: float = 60.0,
                             transport: httpx.BaseTransport | None = None,
                             **kwargs: Any) -> HTTPChatProvider:
    """Phase-1 name kept for callers: an ``HTTPChatProvider`` for /chat/completions."""
    if not model:
        raise LLMError("AA_LLM_MODEL is not set")
    return HTTPChatProvider(ProviderConfig("openai_compatible", base_url, model, api_key,
                                           timeout_s, name="openai_compatible"),
                            transport=transport, **kwargs)
