from datetime import UTC, datetime

import pytest

from aa_model_regime import DISTRIBUTION, MODEL_ID, __version__, classify, plugin
from analytics_assistant.asof_store import AsOfStore
from analytics_assistant.synthetic import seed_store
from analytics_assistant.tools import ToolRegistry, discover_plugins

NOW = datetime(2025, 3, 3, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def registry():
    store = AsOfStore(":memory:")
    seed_store(store, seed=7)
    return ToolRegistry(store, plugin_factories=[("regime", lambda: plugin)], clock=lambda: NOW)


def test_classify():
    assert classify(0.5, 0.25) == "tightening"
    assert classify(-0.25, 0.25) == "easing"
    assert classify(0.1, 0.25) == "on_hold"


def test_plugin_metadata_comes_from_the_installed_distribution():
    p = plugin()
    assert p.name == DISTRIBUTION == "aa-model-regime"
    assert p.version == __version__ != "0+unknown"
    assert [t.name for t in p.tools] == ["detect_regime"]


def test_registered_under_the_entry_point_group():
    assert "regime" in {name for name, _ in discover_plugins()}


def test_regime_envelope_and_series_argument(registry):
    env = registry.call("detect_regime", {"series": "RATE_LONG", "as_of": "2023-06-30"})
    assert env["ok"] is True
    res = env["payload"]["result"]
    assert res["series"] == "RATE_LONG"
    assert res["regime"] in {"tightening", "easing", "on_hold"}
    assert env["provenance"]["model_id"] == MODEL_ID
    assert env["provenance"]["model_version"] == __version__


def test_default_series_is_policy_rate(registry):
    env = registry.call("detect_regime", {"as_of": "2023-06-30"})
    assert env["payload"]["result"]["series"] == "POLICY_RATE"


def test_supported_but_absent_series_is_snapshot_unavailable(registry):
    # RATE_SHORT only exists once the vendor drop feed has been ingested.
    env = registry.call("detect_regime", {"series": "RATE_SHORT", "as_of": "2023-06-30"})
    assert env["payload"]["error"]["code"] == "SNAPSHOT_UNAVAILABLE"


def test_bad_arguments(registry):
    for args in ({"series": "VOL_INDEX"}, {"lookback_days": 5}, {"threshold": -1}):
        env = registry.call("detect_regime", args)
        assert env["payload"]["error"]["code"] == "SCHEMA_VIOLATION", args
