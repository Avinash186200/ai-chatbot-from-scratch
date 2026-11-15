"""The vendor-neutral adapter: both wire formats, retries, failover, normalisation.

Every test mocks the transport with httpx.MockTransport; nothing touches the
network and no sleep is real.
"""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from analytics_assistant.config import Settings
from analytics_assistant.llm import build_provider
from analytics_assistant.llm_adapter import (
    FailoverProvider,
    HTTPChatProvider,
    ProviderConfig,
    to_anthropic,
)
from analytics_assistant.llm_types import Completion, LLMError, ToolCall, Usage

ROOT = Path(__file__).resolve().parents[2]
TOOLS = [{"type": "function", "function": {
    "name": "forecast_series", "description": "Forecast a series.",
    "parameters": {"type": "object", "properties": {"horizon": {"type": "integer"}}}}}]
HISTORY = [
    {"role": "system", "content": "rules"},
    {"role": "user", "content": "forecast"},
    {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": "forecast_series", "arguments": "{\"horizon\": 2}"}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "{\"ok\": true}"},
]


def provider(kind, handler, retries=3, sleeps=None, name=""):
    cfg = ProviderConfig(kind, "https://llm.example.test", "m-1", "sk-test-secret-key",
                         timeout_s=5, max_retries=retries, name=name)
    record = sleeps.append if sleeps is not None else (lambda _s: None)
    return HTTPChatProvider(cfg, transport=httpx.MockTransport(handler), sleep=record)


def test_openai_wire_format_and_normalised_tool_call():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "m-1", "usage": {"prompt_tokens": 11, "completion_tokens": 3},
            "choices": [{"message": {"content": None, "tool_calls": [
                {"id": "t9", "type": "function",
                 "function": {"name": "forecast_series", "arguments": "{\"horizon\": 5}"}}]}}]})

    out = provider("openai_compatible", handler).complete(HISTORY, TOOLS)
    assert seen["path"] == "/chat/completions"
    assert seen["auth"] == "Bearer sk-test-secret-key"
    assert seen["body"]["tools"] == TOOLS and seen["body"]["messages"] == HISTORY
    assert out.tool_calls == [ToolCall("t9", "forecast_series", {"horizon": 5})]
    assert out.usage == Usage(11, 3) and out.provider == "openai_compatible"


def test_anthropic_wire_format_and_normalised_tool_call():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "m-1", "usage": {"input_tokens": 20, "output_tokens": 4},
            "content": [{"type": "text", "text": "Let me check. "},
                        {"type": "tool_use", "id": "tu1", "name": "forecast_series",
                         "input": {"horizon": 5}}]})

    out = provider("anthropic", handler).complete(HISTORY, TOOLS)
    assert seen["path"] == "/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-test-secret-key"
    assert "authorization" not in seen["headers"]
    assert seen["headers"]["anthropic-version"]
    body = seen["body"]
    assert body["system"] == "rules"
    assert body["tools"][0]["input_schema"] == TOOLS[0]["function"]["parameters"]
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["messages"][1]["content"][0] == {
        "type": "tool_use", "id": "c1", "name": "forecast_series", "input": {"horizon": 2}}
    assert body["messages"][2]["content"][0]["type"] == "tool_result"
    # Same internal shape as the OpenAI-compatible path.
    assert out == Completion("Let me check. ", [ToolCall("tu1", "forecast_series",
                                                         {"horizon": 5})],
                             Usage(20, 4), model="m-1", provider="anthropic")


def test_to_anthropic_merges_consecutive_tool_results():
    msgs = [{"role": "user", "content": "q"},
            {"role": "assistant", "tool_calls": [
                {"id": "a", "function": {"name": "x", "arguments": "{}"}},
                {"id": "b", "function": {"name": "y", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "a", "content": "1"},
            {"role": "tool", "tool_call_id": "b", "content": "2"}]
    _, out, _ = to_anthropic(msgs, [])
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert [b["tool_use_id"] for b in out[2]["content"]] == ["a", "b"]


def test_invalid_tool_arguments_are_kept_not_raised():
    def handler(_r):
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [
            {"id": "x", "function": {"name": "f", "arguments": "{not json"}}]}}]})

    out = provider("openai_compatible", handler).complete([], [])
    assert out.tool_calls[0].arguments == {"__invalid_json__": "{not json"}


def test_retries_transient_errors_with_backoff_then_succeeds():
    statuses = iter([503, 429, 200])
    sleeps = []

    def handler(_r):
        status = next(statuses)
        if status == 200:
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        headers = {"retry-after": "2"} if status == 429 else {}
        return httpx.Response(status, headers=headers)

    out = provider("openai_compatible", handler, sleeps=sleeps).complete([], [])
    assert out.content == "ok"
    assert len(sleeps) == 2 and sleeps[1] == 2.0   # Retry-After honoured
    assert 0 <= sleeps[0] <= 0.5                   # full jitter, first attempt


def test_timeouts_and_network_errors_are_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("slow", request=request)
        if calls["n"] == 2:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    assert provider("openai_compatible", handler).complete([], []).content == "ok"
    assert calls["n"] == 3


def test_non_retryable_4xx_fails_at_once_without_leaking_body():
    calls = {"n": 0}

    def handler(_r):
        calls["n"] += 1
        return httpx.Response(400, text="echo: sk-test-secret-key")

    with pytest.raises(LLMError) as err:
        provider("anthropic", handler).complete([], [])
    assert calls["n"] == 1
    assert "sk-test-secret-key" not in str(err.value) and "400" in str(err.value)


def test_retry_budget_exhausted():
    with pytest.raises(LLMError, match="after 3 attempts"):
        provider("openai_compatible", lambda _r: httpx.Response(502), retries=2).complete([], [])


def test_unexpected_body_is_an_llm_error():
    with pytest.raises(LLMError, match="unexpected response body"):
        provider("openai_compatible", lambda _r: httpx.Response(200, json={"x": 1})).complete(
            [], [])


def test_failover_uses_second_provider_only_after_first_fails():
    down = provider("openai_compatible", lambda _r: httpx.Response(503), retries=1,
                    name="primary")
    up = provider("anthropic", lambda _r: httpx.Response(200, json={
        "content": [{"type": "text", "text": "from fallback"}]}), name="fallback")
    out = FailoverProvider([down, up]).complete([], [])
    assert out.content == "from fallback" and out.provider == "fallback"
    with pytest.raises(LLMError, match="all providers failed: primary"):
        FailoverProvider([down]).complete([], [])


def test_build_provider_from_settings():
    assert type(build_provider(Settings())).__name__ == "ScriptedProvider"
    one = build_provider(Settings(llm_provider="anthropic", llm_base_url="https://x.test",
                                  llm_model="m"))
    assert isinstance(one, HTTPChatProvider) and one.cfg.kind == "anthropic"
    two = build_provider(Settings(llm_provider="openai_compatible", llm_model="m",
                                  llm_fallback_provider="anthropic",
                                  llm_fallback_base_url="https://y.test",
                                  llm_fallback_model="m2"))
    assert isinstance(two, FailoverProvider) and len(two.providers) == 2
    with pytest.raises(LLMError):
        build_provider(Settings(llm_provider="nope"))
    with pytest.raises(LLMError):
        build_provider(Settings(llm_provider="anthropic", llm_model=""))


def test_settings_repr_hides_secrets():
    text = repr(Settings(gateway_token="tok-abc", llm_api_key="sk-xyz",
                         llm_fallback_api_key="sk-2", database_url="postgresql://u:pw@h/db"))
    assert "tok-abc" not in text and "sk-xyz" not in text and "pw@" not in text


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_vendor_imports", ROOT / "scripts" / "check_vendor_imports.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_vendor_sdk_imported_outside_the_adapter():
    checker = _load_checker()
    assert checker.scan(ROOT) == []


def test_vendor_import_checker_catches_every_form():
    checker = _load_checker()
    src = ("import openai\nfrom anthropic import Anthropic\nimport anthropic.types as t\n"
           "from openai.types import X\nx = 'import openai'  # import anthropic\n"
           "import openai_compat_helper\n")
    assert [n for _, n in checker.vendor_imports(src)] == [
        "openai", "anthropic", "anthropic.types", "openai.types"]
