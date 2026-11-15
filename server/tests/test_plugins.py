"""Model plugins: discovery through entry points, versioning and failure isolation."""

from importlib.metadata import version

import pytest

from analytics_assistant import tools as tools_module
from analytics_assistant.envelopes import Provenance
from analytics_assistant.plugin_api import ModelPlugin, ToolOutput, ToolSpec
from analytics_assistant.tools import ENTRY_POINT_GROUP, ToolRegistry, discover_plugins

EMPTY = {"type": "object", "properties": {}}


def echo_plugin(name="p", tool="echo", ver="9.9.9"):
    return ModelPlugin(name, ver, [ToolSpec(
        tool, "d", {"type": "object", "properties": {"x": {"type": "integer"}}},
        lambda ctx, args: ToolOutput({"x": args.get("x")}, Provenance(method="echo")))],
        model_id=f"{name}-model")


def test_model_packages_are_discovered_through_the_entry_point_group():
    assert ENTRY_POINT_GROUP == "analytics_assistant.models"
    names = {name for name, _ in discover_plugins()}
    assert {"ar1", "regime"} <= names, (
        "install the model packages: pip install -e packages/aa-model-ar1 "
        "-e packages/aa-model-regime")


def test_each_model_package_has_its_own_version(registry):
    loaded = {p["plugin"]: p["version"] for p in registry.plugins}
    assert loaded == {"aa-model-ar1": version("aa-model-ar1"),
                      "aa-model-regime": version("aa-model-regime")}
    assert loaded["aa-model-ar1"] != loaded["aa-model-regime"]


def test_registry_stamps_the_package_version_over_the_tool_provenance(seeded_store):
    reg = ToolRegistry(seeded_store, plugin_factories=[("e", lambda: echo_plugin())])
    prov = reg.call("echo", {"x": 1})["provenance"]
    assert prov["model_id"] == "p-model" and prov["model_version"] == "9.9.9"


@pytest.mark.parametrize("bad_factory,error", [
    (lambda: (_ for _ in ()).throw(ImportError("missing dependency")), "ImportError"),
    (lambda: "not a plugin", "TypeError"),
    (lambda: ModelPlugin("empty", "1", []), "TypeError"),
    (lambda: ModelPlugin("dup-inside", "1", [ToolSpec("t", "d", EMPTY, None),
                                             ToolSpec("t", "d", EMPTY, None)]), "ValueError"),
    (lambda: ModelPlugin("clash", "1", [ToolSpec("health", "d", EMPTY, None)]), "ValueError"),
])
def test_broken_plugin_is_isolated(seeded_store, bad_factory, error):
    reg = ToolRegistry(seeded_store, plugin_factories=[("good", lambda: echo_plugin()),
                                                       ("bad", bad_factory)])
    assert reg.call("echo", {"x": 4})["payload"]["result"] == {"x": 4}
    assert reg.failed_plugins == [{"plugin": "bad", "error": error}]
    health = reg.call("health", {})["payload"]["result"]
    assert [p["plugin"] for p in health["plugins"]] == ["p"]
    assert health["failed_plugins"] == [{"plugin": "bad", "error": error}]


def test_broken_entry_point_next_to_the_real_packages(seeded_store, monkeypatch):
    real = list(discover_plugins())

    def broken_load():
        raise ModuleNotFoundError("no module named heavy_dependency")

    monkeypatch.setattr(tools_module, "discover_plugins",
                        lambda: [*real, ("broken", broken_load)])
    reg = ToolRegistry(seeded_store)
    assert reg.call("forecast_series", {"as_of": "2023-06-30"})["ok"] is True
    assert reg.call("detect_regime", {"as_of": "2023-06-30"})["ok"] is True
    assert reg.failed_plugins == [{"plugin": "broken", "error": "ModuleNotFoundError"}]


def test_duplicate_tool_names_across_plugins_rejected(seeded_store):
    reg = ToolRegistry(seeded_store, plugin_factories=[("a", lambda: echo_plugin("a")),
                                                       ("b", lambda: echo_plugin("b"))])
    assert [p["plugin"] for p in reg.failed_plugins] == ["b"]
    assert reg.call("echo", {"x": 1})["provenance"]["model_id"] == "a-model"


def test_model_exception_becomes_model_failure_for_that_tool_only(seeded_store):
    def boom(ctx, args):
        raise ZeroDivisionError

    plugin = ModelPlugin("p", "1", [ToolSpec("explode", "d", EMPTY, boom)])
    reg = ToolRegistry(seeded_store, plugin_factories=[("p", lambda: plugin),
                                                       ("e", lambda: echo_plugin("e"))])
    env = reg.call("explode", {})
    assert env["payload"]["error"] == {"code": "MODEL_FAILURE",
                                       "message": "the tool failed; see the server log"}
    assert reg.call("echo", {"x": 2})["ok"] is True


def test_server_runs_with_no_models_installed(seeded_store):
    reg = ToolRegistry(seeded_store, plugin_factories=[])
    assert set(reg.tools) == {"health", "model_info", "query_series"}
    assert reg.call("health", {})["payload"]["result"]["plugins"] == []
