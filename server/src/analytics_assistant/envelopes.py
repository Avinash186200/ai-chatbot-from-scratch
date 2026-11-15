"""The response envelope every tool returns, on MCP and REST alike.

    {tool, capability_id, ok, payload: {result} | {error: {code, message}},
     notes, provenance}

Domain refusals are ``ok: false`` with a stable error code, not exceptions or
HTTP errors, so a client (the agent, the Excel add-in) can tell "your
question has no answer" from "the server is broken". ``provenance`` sits next
to ``payload`` and records what produced the number: model, version, the
data snapshot it saw and the as-of date it was computed for.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Stable codes. Clients map these, never the message text.
SNAPSHOT_UNAVAILABLE = "SNAPSHOT_UNAVAILABLE"  # no data published by the requested as-of
SCHEMA_VIOLATION = "SCHEMA_VIOLATION"  # a bad argument
UNSUPPORTED_HORIZON = "UNSUPPORTED_HORIZON"  # horizon outside what the model serves
MODEL_FAILURE = "MODEL_FAILURE"  # the model raised
INTERNAL = "INTERNAL"

ERROR_CODES = frozenset(
    {SNAPSHOT_UNAVAILABLE, SCHEMA_VIOLATION, UNSUPPORTED_HORIZON, MODEL_FAILURE, INTERNAL}
)


class ToolError(Exception):
    """Raised inside a tool to produce an ``ok: false`` envelope."""

    def __init__(self, code: str, message: str) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Provenance:
    method: str
    model_id: str | None = None
    model_version: str | None = None
    snapshot_id: str | None = None
    as_of: str | None = None
    note: str | None = None
    trained_at: str | None = None
    feature_set_version: str | None = None
    computed_at: str | None = None
    deterministic: bool | None = None


def ok(
    tool: str,
    result: Any,
    provenance: Provenance | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    return {
        "tool": tool,
        "capability_id": f"tool.{tool}",
        "ok": True,
        "payload": {"result": result},
        "notes": notes,
        "provenance": asdict(provenance) if provenance else None,
    }


def refusal(tool: str, code: str, message: str) -> dict[str, Any]:
    return {
        "tool": tool,
        "capability_id": f"tool.{tool}",
        "ok": False,
        "payload": {"error": {"code": code, "message": message}},
        "notes": None,
        "provenance": None,
    }
