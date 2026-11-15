"""Placeholder AR(1) forecaster, packaged as its own installable model plugin.

The server finds it through the ``analytics_assistant.models`` entry-point
group (see pyproject.toml). The version reported in provenance and in
``health`` is this distribution's version, so a model release is visible in
every answer it produces.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from aa_model_ar1.model import MODEL_ID, ar1_forecast_tool, fit_ar1, forecast_path
from analytics_assistant.plugin_api import ModelPlugin

DISTRIBUTION = "aa-model-ar1"

try:
    __version__ = version(DISTRIBUTION)
except PackageNotFoundError:  # running from a source tree without installing
    __version__ = "0+unknown"


def plugin() -> ModelPlugin:
    return ModelPlugin(name=DISTRIBUTION, version=__version__, tools=[ar1_forecast_tool()],
                       model_id=MODEL_ID)


__all__ = ["DISTRIBUTION", "MODEL_ID", "ar1_forecast_tool", "fit_ar1", "forecast_path", "plugin"]
