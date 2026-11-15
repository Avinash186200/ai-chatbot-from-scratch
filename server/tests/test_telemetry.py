"""Tracing spans, token and cost counters, JSON logs with request ids and redaction."""

import io
import json
import logging

import httpx
import pytest

from analytics_assistant import telemetry
from analytics_assistant.agent import Agent
from analytics_assistant.llm import Completion, ScriptedProvider, ToolCall
from analytics_assistant.llm_adapter import HTTPChatProvider, ProviderConfig

sdk = pytest.importorskip("opentelemetry.sdk.trace")
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

_EXPORTER = InMemorySpanExporter()
_INSTALLED = {"done": False}


@pytest.fixture
def spans():
    # The global tracer provider can be set once per process.
    if not _INSTALLED["done"]:
        assert telemetry.setup_tracing(span_exporter=_EXPORTER)
        _INSTALLED["done"] = True
    _EXPORTER.clear()
    yield _EXPORTER
    _EXPORTER.clear()


def test_agent_run_tool_and_llm_spans_nest(spans, registry):
    llm = HTTPChatProvider(
        ProviderConfig("openai_compatible", "https://llm.example.test", "m", "sk-secret-123"),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
            "usage": {"prompt_tokens": 7, "completion_tokens": 2},
            "choices": [{"message": {"content": "plain answer"}}]})))
    provider = ScriptedProvider([
        Completion(tool_calls=[ToolCall("1", "query_series", {"series": "POLICY_RATE",
                                                              "as_of": "2021-01-15"})]),
        lambda messages: llm.complete(messages, []),
    ])
    list(Agent(registry, provider).run("show it"))
    finished = {s.name: s for s in spans.get_finished_spans()}
    assert {"agent.run", "tool.call", "llm.complete"} <= set(finished)
    root = finished["agent.run"]
    assert finished["tool.call"].parent.span_id == root.context.span_id
    assert finished["llm.complete"].parent.span_id == root.context.span_id
    assert finished["tool.call"].attributes["tool.name"] == "query_series"
    assert finished["llm.complete"].attributes["llm.input_tokens"] == 7
    # No message text, arguments or keys on any span.
    blob = json.dumps([dict(s.attributes) for s in spans.get_finished_spans()])
    assert "sk-secret-123" not in blob and "show it" not in blob and "POLICY_RATE" not in blob


def test_usage_meter_tokens_and_cost():
    meter = telemetry.UsageMeter(price_in_per_mtok=2.0, price_out_per_mtok=10.0)
    cost = meter.add("primary", "m", 1_000_000, 100_000)
    assert cost == pytest.approx(3.0)
    snap = meter.snapshot()
    assert snap["input_tokens"] == 1_000_000 and snap["by_model"]["primary/m"]["calls"] == 1


def test_record_usage_updates_global_meter():
    before = telemetry.meter.snapshot()["output_tokens"]
    telemetry.record_usage("p", "m", 3, 4)
    assert telemetry.meter.snapshot()["output_tokens"] == before + 4


def test_json_logs_carry_request_id_and_redact_secrets():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(telemetry.JsonFormatter())
    handler.addFilter(telemetry.RedactingFilter(["configured-gateway-token"]))
    log = logging.getLogger("aa.test.redaction")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    token = telemetry.request_id_var.set("req-1234")
    try:
        log.info("calling with Authorization: Bearer abc.def-ghi and api_key=sk-zzz "
                 "and configured-gateway-token %s", "x", extra={"event": "test"})
    finally:
        telemetry.request_id_var.reset(token)
        log.removeHandler(handler)
    entry = json.loads(stream.getvalue())
    assert entry["request_id"] == "req-1234" and entry["event"] == "test"
    assert "abc.def-ghi" not in entry["msg"] and "sk-zzz" not in entry["msg"]
    assert "configured-gateway-token" not in entry["msg"]
    assert entry["msg"].count("[REDACTED]") >= 3


def test_setup_logging_replaces_its_own_handler():
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    try:
        telemetry.setup_logging("json", "WARNING", secrets=["s3cret-value"])
        telemetry.setup_logging("text", "WARNING")
        ours = [h for h in root.handlers if getattr(h, "_aa_handler", False)]
        assert len(ours) == 1
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
        root.setLevel(level)


def test_tracing_off_is_a_noop():
    assert telemetry.setup_tracing("none") is False
    with telemetry.span("x") as s:
        s.set_attribute("k", 1)
