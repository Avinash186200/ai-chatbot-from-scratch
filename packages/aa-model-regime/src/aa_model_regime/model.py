"""Placeholder regime classifier for rate series.

Labels the as-of date "tightening" (the rate rose), "easing" (it fell) or
"on_hold" from the net change in a rate series over a trailing lookback.
Lookback and threshold are illustrative defaults, exposed as arguments so the
rule is transparent.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from analytics_assistant.envelopes import (
    SCHEMA_VIOLATION,
    SNAPSHOT_UNAVAILABLE,
    Provenance,
    ToolError,
)
from analytics_assistant.plugin_api import ToolContext, ToolOutput, ToolSpec

MODEL_ID = "placeholder-rate-regime"
DEFAULT_SERIES = "POLICY_RATE"
# Rate series the rule is meant for. RATE_SHORT arrives through the vendor
# drop feed (see analytics_assistant.ingest), so it may be absent from a store.
SUPPORTED_SERIES = ("POLICY_RATE", "RATE_LONG", "RATE_SHORT")


def classify(change: float, threshold: float) -> str:
    if change >= threshold:
        return "tightening"
    if change <= -threshold:
        return "easing"
    return "on_hold"


def _handler(ctx: ToolContext, args: dict[str, Any]) -> ToolOutput:
    series = args.get("series") or DEFAULT_SERIES
    lookback = int(args.get("lookback_days", 180))
    threshold = float(args.get("threshold", 0.25))
    if lookback < 20 or lookback > 1000:
        raise ToolError(SCHEMA_VIOLATION, "lookback_days must be between 20 and 1000")
    if threshold <= 0:
        raise ToolError(SCHEMA_VIOLATION, "threshold must be positive")
    history = ctx.store.as_of(series, ctx.as_of, start=ctx.as_of - timedelta(days=lookback))
    if len(history) < 2:
        raise ToolError(SNAPSHOT_UNAVAILABLE, f"no {series} history published by {ctx.as_of}")
    change = history[-1][1] - history[0][1]
    return ToolOutput(
        result={
            "series": series,
            "regime": classify(change, threshold),
            "net_change": round(change, 4),
            "window_start": history[0][0].isoformat(),
            "window_end": history[-1][0].isoformat(),
            "lookback_days": lookback,
            "threshold": threshold,
        },
        provenance=Provenance(
            method="trailing_net_change_rule",
            model_id=MODEL_ID,
            snapshot_id=ctx.store.snapshot_id(series, ctx.as_of),
            as_of=ctx.as_of.isoformat(),
            computed_at=ctx.now().isoformat(),
            deterministic=True,
        ),
        notes="Placeholder rule on synthetic data.",
    )


def regime_tool() -> ToolSpec:
    return ToolSpec(
        name="detect_regime",
        description=(
            "Classify the regime of a rate series (tightening / easing / on_hold) on the "
            "as-of date from its net change over a trailing window."
        ),
        parameters={
            "type": "object",
            "properties": {
                "series": {"type": "string", "enum": list(SUPPORTED_SERIES),
                           "default": DEFAULT_SERIES, "description": "Rate series to classify."},
                "lookback_days": {"type": "integer", "default": 180,
                                  "description": "Trailing window in calendar days."},
                "threshold": {"type": "number", "default": 0.25,
                              "description": "Net change (percentage points) that counts as a move."},
            },
            "required": [],
        },
        handler=_handler,
    )
