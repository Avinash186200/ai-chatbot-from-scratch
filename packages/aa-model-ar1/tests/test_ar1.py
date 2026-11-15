import random
from datetime import UTC, datetime

import pytest

from aa_model_ar1 import (
    DISTRIBUTION,
    MODEL_ID,
    __version__,
    fit_ar1,
    forecast_path,
    plugin,
)
from analytics_assistant.asof_store import AsOfStore
from analytics_assistant.envelopes import ToolError
from analytics_assistant.synthetic import seed_store
from analytics_assistant.tools import ToolRegistry, discover_plugins

NOW = datetime(2025, 3, 3, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def registry():
    store = AsOfStore(":memory:")
    seed_store(store, seed=7)
    return ToolRegistry(store, plugin_factories=[("ar1", lambda: plugin)], clock=lambda: NOW)


def test_fit_recovers_coefficient():
    rng, x, xs = random.Random(1), 0.0, []
    for _ in range(3000):
        x = 0.5 * x + rng.gauss(0, 1)
        xs.append(x)
    _, phi, _ = fit_ar1(xs)
    assert abs(phi - 0.5) < 0.05


def test_fit_needs_history():
    with pytest.raises(ToolError) as err:
        fit_ar1([0.1] * 5)
    assert err.value.code == "SNAPSHOT_UNAVAILABLE"


def test_interval_widens_with_horizon():
    path = forecast_path(3.0, 0.0, 0.0, 0.2, 0.01, 5)
    widths = [p["upper_90"] - p["lower_90"] for p in path]
    assert widths == sorted(widths) and widths[0] > 0


def test_plugin_metadata_comes_from_the_installed_distribution():
    p = plugin()
    assert p.name == DISTRIBUTION == "aa-model-ar1"
    assert p.version == __version__ != "0+unknown"
    assert p.model_id == MODEL_ID
    assert [t.name for t in p.tools] == ["forecast_series"]


def test_registered_under_the_entry_point_group():
    assert "ar1" in {name for name, _ in discover_plugins()}


def test_forecast_envelope_carries_package_version(registry):
    env = registry.call("forecast_series", {"horizon": 3, "as_of": "2023-06-30"})
    assert env["ok"] is True
    assert env["provenance"]["model_id"] == MODEL_ID
    assert env["provenance"]["model_version"] == __version__
    assert len(env["payload"]["result"]["path"]) == 3


def test_unknown_series_and_horizon(registry):
    assert registry.call("forecast_series", {"series": "NOPE"})["payload"]["error"]["code"] \
        == "SCHEMA_VIOLATION"
    assert registry.call("forecast_series", {"horizon": 21})["payload"]["error"]["code"] \
        == "UNSUPPORTED_HORIZON"
