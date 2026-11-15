"""The contract between the server and a model plugin.

A plugin is any installed package that exposes a callable under the
``analytics_assistant.models`` entry-point group (one package per model; see
``packages/`` in the repository). Calling it returns a
``ModelPlugin``: a name, a version and the tools it serves. The server knows
nothing about model math; it discovers plugins, validates arguments against
each tool's JSON schema, resolves the as-of date and wraps results in the
standard envelope.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from analytics_assistant.asof_store import AsOfStore
from analytics_assistant.envelopes import Provenance


@dataclass(frozen=True)
class ToolContext:
    store: AsOfStore
    as_of: date
    now: Callable[[], datetime]


@dataclass(frozen=True)
class ToolOutput:
    result: Any
    provenance: Provenance
    notes: str | None = None


Handler = Callable[[ToolContext, dict[str, Any]], ToolOutput]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    # JSON schema (object) for the arguments. "as_of" is added by the server.
    parameters: dict[str, Any]
    handler: Handler


@dataclass(frozen=True)
class ModelPlugin:
    # ``name`` is the distribution name and ``version`` its release; the
    # registry stamps both into the provenance of every result the plugin's
    # tools return, so a model release is visible in each answer.
    name: str
    version: str
    tools: list[ToolSpec] = field(default_factory=list)
    model_id: str | None = None
