from datetime import date
from importlib.metadata import version


def test_forecast_envelope_and_provenance(registry):
    env = registry.call("forecast_series", {"horizon": 3, "as_of": "2023-06-30"})
    assert env["ok"] is True
    res = env["payload"]["result"]
    assert len(res["path"]) == 3
    assert all(p["lower_90"] <= p["point"] <= p["upper_90"] for p in res["path"])
    prov = env["provenance"]
    assert prov["as_of"] == "2023-06-30"
    assert prov["snapshot_id"].startswith("snap-")
    assert prov["model_id"] == "placeholder-ar1"
    # The version comes from the installed model package, not from the model code.
    assert prov["model_version"] == version("aa-model-ar1")
    # The last observation used is before the as-of date (publication lag).
    assert res["last_observation"] < "2023-06-30"


def test_forecast_is_deterministic(registry):
    a = registry.call("forecast_series", {"as_of": "2022-03-15"})
    b = registry.call("forecast_series", {"as_of": "2022-03-15"})
    assert a == b


def test_forecast_never_sees_later_data(registry, seeded_store):
    as_of = date(2021, 5, 14)
    env = registry.call("forecast_series", {"as_of": as_of.isoformat()})
    known = seeded_store.as_of("RATE_LONG", as_of)
    assert env["payload"]["result"]["last_value"] == known[-1][1]


def test_unsupported_horizon(registry):
    env = registry.call("forecast_series", {"horizon": 99})
    assert env["ok"] is False
    assert env["payload"]["error"]["code"] == "UNSUPPORTED_HORIZON"


def test_schema_violations(registry):
    for args in ({"series": "NOPE"}, {"horizon": "five"}, {"horizon": True},
                 {"unexpected": 1}, {"as_of": "30/06/2023"}, {"as_of": "2999-01-01"}):
        env = registry.call("forecast_series", args)
        assert env["payload"]["error"]["code"] == "SCHEMA_VIOLATION", args


def test_no_data_before_history_starts(registry):
    env = registry.call("forecast_series", {"as_of": "2019-06-01"})
    assert env["payload"]["error"]["code"] == "SNAPSHOT_UNAVAILABLE"


def test_regime(registry):
    env = registry.call("detect_regime", {"as_of": "2023-06-30"})
    assert env["ok"]
    assert env["payload"]["result"]["regime"] in {"tightening", "easing", "on_hold"}
    assert env["provenance"]["model_version"] == version("aa-model-regime")


def test_query_series_point_in_time(registry):
    env = registry.call("query_series", {"series": "POLICY_RATE", "start_date": "2022-01-03",
                                         "end_date": "2022-01-07", "as_of": "2022-01-05"})
    dates = [o["date"] for o in env["payload"]["result"]["observations"]]
    assert dates and max(dates) <= "2022-01-04"


def test_default_as_of_is_latest_publication(registry, seeded_store):
    env = registry.call("query_series", {"series": "VOL_INDEX"})
    assert env["provenance"]["as_of"] == seeded_store.latest_publication().isoformat()


def test_health_and_catalog(registry):
    health = registry.call("health", {})["payload"]["result"]
    loaded = {p["plugin"]: p for p in health["plugins"]}
    assert set(loaded) == {"aa-model-ar1", "aa-model-regime"}
    assert loaded["aa-model-ar1"]["version"] == version("aa-model-ar1")
    assert loaded["aa-model-regime"]["version"] == version("aa-model-regime")
    assert loaded["aa-model-ar1"]["tools"] == ["forecast_series"]
    assert loaded["aa-model-regime"]["model_id"] == "placeholder-rate-regime"
    assert health["failed_plugins"] == []
    tools = {t["name"]: t for t in registry.call("model_info", {})["payload"]["result"]["tools"]}
    assert {"forecast_series", "detect_regime", "query_series"} <= set(tools)
    assert tools["detect_regime"]["plugin"] == "aa-model-regime"
    assert tools["query_series"]["plugin"] is None


def test_core_tools_have_no_model_version(registry):
    env = registry.call("query_series", {"series": "VOL_INDEX", "as_of": "2022-01-05"})
    assert env["provenance"]["model_version"] is None


