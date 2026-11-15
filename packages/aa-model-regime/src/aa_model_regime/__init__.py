"""Placeholder regime rule, packaged as its own installable model plugin.

Discovered through the ``analytics_assistant.models`` entry-point group. It
is versioned and released separately from the forecaster: the two packages
share nothing but the server's plugin API.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from aa_model_regime.model import MODEL_ID, SUPPORTED_SERIES, classify, regime_tool
from analytics_assistant.plugin_api import ModelPlugin

DISTRIBUTION = "aa-model-regime"

try:
    __version__ = version(DISTRIBUTION)
except PackageNotFoundError:  # running from a source tree without installing
    __version__ = "0+unknown"


def plugin() -> ModelPlugin:
    return ModelPlugin(name=DISTRIBUTION, version=__version__, tools=[regime_tool()],
                       model_id=MODEL_ID)


__all__ = ["DISTRIBUTION", "MODEL_ID", "SUPPORTED_SERIES", "classify", "plugin", "regime_tool"]
