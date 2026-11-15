import json

import pytest
from starlette.testclient import TestClient

from analytics_assistant.config import Settings
from analytics_assistant.llm import Completion, ScriptedProvider, ToolCall
from analytics_assistant.web import create_app

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(registry):
    app = create_app(Settings(gateway_token=TOKEN, db_path=":memory:"), registry=registry,
                     provider=ScriptedProvider())
    return TestClient(app)


def test_healthz_is_open(client):
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"},
                                     {"Authorization": f"Basic {TOKEN}"}])
def test_gateway_requires_token(client, headers):
    r = client.post("/gateway/health", headers=headers)
    assert r.status_code == 401
    assert "wrong" not in r.text


def test_no_configured_token_refuses_everything(registry):
    app = create_app(Settings(gateway_token="", db_path=":memory:"), registry=registry,
                     provider=ScriptedProvider())
    r = TestClient(app).post("/gateway/health", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


def test_gateway_call(client):
    r = client.post("/gateway/forecast_series", headers=AUTH,
                    json={"horizon": 2, "as_of": "2023-01-31"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_gateway_unknown_tool_and_bad_body(client):
    assert client.post("/gateway/nope", headers=AUTH, json={}).status_code == 404
    assert client.post("/gateway/health", headers=AUTH, content=b"[1]").status_code == 422
    assert client.post("/gateway/health", headers=AUTH, content=b"{bad").status_code == 422


def test_domain_refusal_is_http_200(client):
    r = client.post("/gateway/forecast_series", headers=AUTH, json={"horizon": 0})
    assert r.status_code == 200
    assert r.json()["payload"]["error"]["code"] == "UNSUPPORTED_HORIZON"


def test_openapi_lists_every_tool(client):
    spec = client.get("/gateway/openapi.json", headers=AUTH).json()
    assert "/forecast_series" in spec["paths"]
    schema = spec["paths"]["/forecast_series"]["post"]["requestBody"]["content"][
        "application/json"]["schema"]
    assert "as_of" in schema["properties"]


def parse_sse(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_chat_streams_tool_call_and_answer(client):
    r = client.post("/api/chat", headers=AUTH, json={"message": "forecast please"})
    events = parse_sse(r.text)
    types = [e["type"] for e in events]
    assert types == ["run.started", "tool.called", "tool.result", "message.completed",
                     "run.finished"]
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5]
    assert "snap-" in events[3]["data"]["content"]


def test_chat_validation(client):
    assert client.post("/api/chat", json={"message": "x"}).status_code == 401
    assert client.post("/api/chat", headers=AUTH, json={}).status_code == 422
    assert client.post("/api/chat", headers=AUTH,
                       json={"message": "x" * 5000}).status_code == 413


def test_memory_rejects_market_levels(client):
    ok = client.post("/api/memory", headers=AUTH, json={"note": "Prefer short answers"})
    assert ok.status_code == 200
    bad = client.post("/api/memory", headers=AUTH, json={"note": "Rate is 4.2% today"})
    assert bad.status_code == 422


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "Analytics Assistant" in r.text


def test_scripted_provider_used_in_app(registry):
    provider = ScriptedProvider([
        Completion(tool_calls=[ToolCall("1", "detect_regime", {"as_of": "2023-06-30"})]),
        Completion("done"),
    ])
    app = create_app(Settings(gateway_token=TOKEN, db_path=":memory:"), registry=registry,
                     provider=provider)
    events = parse_sse(TestClient(app).post("/api/chat", headers=AUTH,
                                            json={"message": "regime?"}).text)
    assert events[2]["data"]["ok"] is True
    assert events[-2]["data"]["content"] == "done"


# The Excel client (excel-client/src/functions/functions.ts) sends exactly these
# bodies; if the server renames an argument, this fails before Excel does.
@pytest.mark.parametrize("tool,body", [
    ("forecast_series", {"series": "RATE_LONG", "horizon": 5, "as_of": "2023-06-30"}),
    ("forecast_series", {"series": "RATE_LONG", "horizon": 5}),
    ("detect_regime", {"series": "POLICY_RATE", "as_of": "2023-06-30"}),
    ("detect_regime", {"series": "POLICY_RATE"}),
])
def test_excel_client_contract(client, tool, body):
    r = client.post(f"/gateway/{tool}", headers=AUTH, json=body)
    env = r.json()
    assert r.status_code == 200 and env["ok"] is True, env
    for field in ("model_id", "model_version", "as_of", "snapshot_id", "computed_at"):
        assert env["provenance"][field], field


def test_excel_client_tool_list_contract(client):
    spec = client.get("/gateway/openapi.json", headers=AUTH).json()
    for path, item in spec["paths"].items():
        assert item["post"]["operationId"] == path.lstrip("/")
        assert isinstance(item["post"]["description"], str)
