"""Placeholder forecaster: AR(1) on daily changes, fitted on the as-of window.

    dy_t = c + phi * dy_{t-1} + e_t

Fitted by ordinary least squares on the last ``window`` changes known on the
as-of date, then iterated forward ``horizon`` business days. The interval is
the normal approximation from the residual variance accumulated over the
horizon. It is a textbook baseline, chosen because it is easy to verify, not
because it forecasts well.
"""

from __future__ import annotations

import math
from itertools import pairwise
from typing import Any

from analytics_assistant.envelopes import (
    SCHEMA_VIOLATION,
    SNAPSHOT_UNAVAILABLE,
    UNSUPPORTED_HORIZON,
    Provenance,
    ToolError,
)
from analytics_assistant.plugin_api import ToolContext, ToolOutput, ToolSpec

MODEL_ID = "placeholder-ar1"
WINDOW = 250
MAX_HORIZON = 20
DEFAULT_SERIES = "RATE_LONG"
Z_90 = 1.6449


def fit_ar1(changes: list[float]) -> tuple[float, float, float]:
    """OLS of x[t] on x[t-1]; returns (c, phi, residual variance)."""
    x, y = changes[:-1], changes[1:]
    n = len(x)
    if n < 10:
        raise ToolError(SNAPSHOT_UNAVAILABLE, "not enough history on this as-of date to fit")
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    phi = sxy / sxx if sxx > 0 else 0.0
    phi = max(-0.99, min(0.99, phi))  # keep the recursion stable
    c = my - phi * mx
    resid = [b - (c + phi * a) for a, b in zip(x, y)]
    var = sum(r * r for r in resid) / max(1, n - 2)
    return c, phi, var


def forecast_path(level: float, last_change: float, c: float, phi: float, var: float,
                  horizon: int) -> list[dict[str, float]]:
    path, dy, cum_var, psi_sum = [], last_change, 0.0, 0.0
    for h in range(1, horizon + 1):
        dy = c + phi * dy
        level += dy
        # Variance of the level after h steps: sum over k of (sum_{j<=k} phi^j)^2 * var.
        psi_sum = psi_sum * phi + 1.0
        cum_var += psi_sum * psi_sum * var
        half = Z_90 * math.sqrt(cum_var)
        path.append({"step": h, "point": round(level, 4),
                     "lower_90": round(level - half, 4), "upper_90": round(level + half, 4)})
    return path


def _handler(ctx: ToolContext, args: dict[str, Any]) -> ToolOutput:
    series = args.get("series") or DEFAULT_SERIES
    if series not in ctx.store.series_catalog():
        raise ToolError(SCHEMA_VIOLATION, f"unknown series {series!r}; see model_info")
    horizon = int(args.get("horizon", 5))
    if not 1 <= horizon <= MAX_HORIZON:
        raise ToolError(UNSUPPORTED_HORIZON, f"horizon must be between 1 and {MAX_HORIZON}")
    history = ctx.store.as_of(series, ctx.as_of)[-(WINDOW + 1):]
    if len(history) < 12:
        raise ToolError(SNAPSHOT_UNAVAILABLE, f"no {series} history published by {ctx.as_of}")
    levels = [v for _, v in history]
    changes = [b - a for a, b in pairwise(levels)]
    c, phi, var = fit_ar1(changes)
    result = {
        "series": series,
        "last_observation": history[-1][0].isoformat(),
        "last_value": levels[-1],
        "horizon": horizon,
        "phi": round(phi, 4),
        "path": forecast_path(levels[-1], changes[-1], c, phi, var, horizon),
    }
    return ToolOutput(
        result=result,
        provenance=Provenance(
            method="ar1_ols_daily_changes",
            model_id=MODEL_ID,
            snapshot_id=ctx.store.snapshot_id(series, ctx.as_of),
            as_of=ctx.as_of.isoformat(),
            # Fitted on demand from the as-of window, so "trained at" is the as-of date.
            trained_at=ctx.as_of.isoformat(),
            feature_set_version="levels-v1",
            computed_at=ctx.now().isoformat(),
            deterministic=True,
        ),
        notes="Placeholder model on synthetic data; not a production forecast.",
    )


def ar1_forecast_tool() -> ToolSpec:
    return ToolSpec(
        name="forecast_series",
        description=(
            "Forecast a series a few business days ahead with a placeholder AR(1) model "
            "fitted only on data published by the as-of date. Returns a point path with "
            "90% intervals."
        ),
        parameters={
            "type": "object",
            "properties": {
                "series": {"type": "string", "default": DEFAULT_SERIES,
                           "description": "Series to forecast; see model_info for names."},
                "horizon": {"type": "integer", "default": 5,
                            "description": f"Business days ahead, 1 to {MAX_HORIZON}."},
            },
            "required": [],
        },
        handler=_handler,
    )
