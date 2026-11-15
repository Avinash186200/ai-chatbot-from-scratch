"""Tracing, token/cost counters and structured JSON logs.

Tracing uses the OpenTelemetry API. Without the SDK installed (or before
``setup_tracing`` runs) every span is a no-op, so library code can always
call ``span(...)``. ``setup_tracing`` installs a console exporter by default
or an OTLP exporter (``AA_OTEL_EXPORTER=otlp``, needs the ``otel`` extra).

Spans: ``agent.run`` (one per chat turn), ``tool.call`` (one per registry
call), ``retrieval.search`` and ``llm.complete`` (one per provider call,
with attempts and token counts). Attributes are an allowlist of ids, names,
counts and status codes; message text, tool arguments, tokens and keys are
never put on a span.

Usage: ``record_usage`` adds to OpenTelemetry counters
(``aa.llm.tokens``, ``aa.llm.cost``) and to an in-process ``UsageMeter``
that ``/api/usage`` reports. Cost is computed from configured per-million
token prices; with no prices configured it stays 0.

Logs: ``setup_logging`` installs a JSON formatter on the root logger. Each
record carries the request id of the HTTP request it belongs to (from a
context variable set by the request-id middleware) and passes a redaction
filter that removes bearer tokens and any configured secret.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import re
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("aa_request_id", default="-")

try:  # the API is a light dependency; the SDK is optional
    from opentelemetry import metrics as _otel_metrics
    from opentelemetry import trace as _otel_trace
except ImportError:  # pragma: no cover - exercised only without the otel API
    _otel_trace = None
    _otel_metrics = None

TRACER_NAME = "analytics_assistant"


# ---------------------------------------------------------------- tracing


class _NoopSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status_error(self, description: str) -> None:
        pass


class _Span:
    def __init__(self, span: Any) -> None:
        self._span = span

    def set_attribute(self, key: str, value: Any) -> None:
        self._span.set_attribute(key, value)

    def set_status_error(self, description: str) -> None:
        from opentelemetry.trace import Status, StatusCode

        self._span.set_status(Status(StatusCode.ERROR, description))


@contextlib.contextmanager
def span(name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[Any]:
    if _otel_trace is None:
        yield _NoopSpan()
        return
    tracer = _otel_trace.get_tracer(TRACER_NAME)
    attrs = {"aa.request_id": request_id_var.get(), **(attributes or {})}
    with tracer.start_as_current_span(name, attributes=attrs) as s:
        yield _Span(s)


class RunSpan:
    """A span that outlives one stack frame, for generators that yield events.

    A generator can resume in another thread, so a span made "current" across
    a ``yield`` would be detached in the wrong context. Instead the span is
    started detached, and each synchronous step activates it with
    ``with run.active():`` so child spans (tool calls, LLM calls) nest under it.
    """

    def __init__(self, name: str, attributes: Mapping[str, Any] | None = None) -> None:
        self._span = None
        if _otel_trace is not None:
            attrs = {"aa.request_id": request_id_var.get(), **(attributes or {})}
            self._span = _otel_trace.get_tracer(TRACER_NAME).start_span(name, attributes=attrs)

    @contextlib.contextmanager
    def active(self) -> Iterator[None]:
        if self._span is None:
            yield
            return
        with _otel_trace.use_span(self._span, end_on_exit=False):
            yield

    def set_attribute(self, key: str, value: Any) -> None:
        if self._span is not None:
            self._span.set_attribute(key, value)

    def end(self, error: str | None = None) -> None:
        if self._span is None:
            return
        if error:
            from opentelemetry.trace import Status, StatusCode

            self._span.set_status(Status(StatusCode.ERROR, error))
        self._span.end()
        self._span = None


def setup_tracing(exporter: str = "console", endpoint: str = "",
                  span_exporter: Any = None) -> bool:
    """Install a tracer provider. Returns False when the SDK is not installed.

    ``span_exporter`` overrides the choice (tests pass an in-memory exporter).
    """
    if exporter == "none" and span_exporter is None:
        return False
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )
    except ImportError:
        logging.getLogger(__name__).warning("opentelemetry-sdk not installed; tracing off")
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": TRACER_NAME}))
    if span_exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    elif exporter == "otlp":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint) if endpoint else OTLPSpanExporter()))
    else:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    _otel_trace.set_tracer_provider(provider)
    return True


# ---------------------------------------------------------------- usage and cost


@dataclass
class UsageMeter:
    price_in_per_mtok: float = 0.0
    price_out_per_mtok: float = 0.0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, provider: str, model: str, tokens_in: int, tokens_out: int) -> float:
        cost = (tokens_in * self.price_in_per_mtok + tokens_out * self.price_out_per_mtok) / 1e6
        with self._lock:
            self.calls += 1
            self.input_tokens += tokens_in
            self.output_tokens += tokens_out
            self.cost += cost
            row = self.by_model.setdefault(f"{provider}/{model}",
                                           {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                            "cost": 0.0})
            row["calls"] += 1
            row["input_tokens"] += tokens_in
            row["output_tokens"] += tokens_out
            row["cost"] += cost
        return cost

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"calls": self.calls, "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens, "cost": round(self.cost, 6),
                    "by_model": {k: dict(v) for k, v in self.by_model.items()}}


meter = UsageMeter()
_instruments: dict[str, Any] = {}


def configure_prices(price_in_per_mtok: float, price_out_per_mtok: float) -> None:
    meter.price_in_per_mtok = price_in_per_mtok
    meter.price_out_per_mtok = price_out_per_mtok


def _counter(name: str, unit: str, description: str) -> Any:
    if _otel_metrics is None:
        return None
    if name not in _instruments:
        _instruments[name] = _otel_metrics.get_meter(TRACER_NAME).create_counter(
            name, unit=unit, description=description)
    return _instruments[name]


def record_usage(provider: str, model: str, tokens_in: int, tokens_out: int) -> None:
    cost = meter.add(provider, model, tokens_in, tokens_out)
    tokens = _counter("aa.llm.tokens", "{token}", "LLM tokens by direction")
    if tokens is not None:
        tokens.add(tokens_in, {"provider": provider, "model": model, "direction": "input"})
        tokens.add(tokens_out, {"provider": provider, "model": model, "direction": "output"})
    if cost:
        cost_counter = _counter("aa.llm.cost", "1", "LLM cost in configured price units")
        if cost_counter is not None:
            cost_counter.add(cost, {"provider": provider, "model": model})


# ---------------------------------------------------------------- logging

_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+")
_KEYISH = re.compile(r"(?i)\b(api[_-]?key|x-api-key|authorization|token|password)"
                     r"(\s*[=:]\s*)(\"?)[^\s\",]+")


class RedactingFilter(logging.Filter):
    """Removes bearer tokens, key=value secrets and any configured secret values."""

    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        self.secrets = [s for s in (secrets or []) if s and len(s) >= 4]

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        text = _BEARER.sub(r"\1 [REDACTED]", text)
        return _KEYISH.sub(r"\1\2\3[REDACTED]", text)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = self.redact(message)
        record.args = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key in ("event", "tool", "status", "duration_ms", "run_id", "thread_id", "path",
                    "method", "agent"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(entry, default=str)


def setup_logging(fmt: str = "json", level: str = "INFO",
                  secrets: list[str] | None = None) -> logging.Handler:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactingFilter(secrets))
    root = logging.getLogger()
    for old in list(root.handlers):
        if getattr(old, "_aa_handler", False):
            root.removeHandler(old)
    handler._aa_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level.upper())
    return handler
