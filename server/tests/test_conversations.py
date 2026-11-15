"""Conversations over HTTP (create, list, resume) with both agents, and durability."""

import json

import pytest
from starlette.testclient import TestClient

from analytics_assistant.config import Settings
from analytics_assistant.conversations import ConversationStore, make_title
from analytics_assistant.llm import ScriptedProvider
from analytics_assistant.web import create_app

TOKEN = "test-token-123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def parse_sse(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def make_client(registry, agent="loop", state_db=""):
    settings = Settings(gateway_token=TOKEN, db_path=":memory:", agent=agent, state_db=state_db)
    return TestClient(create_app(settings, registry=registry, provider=ScriptedProvider()))


@pytest.mark.parametrize("agent", ["loop", "graph"])
def test_create_list_resume(registry, agent):
    if agent == "graph":
        pytest.importorskip("langgraph")
    client = make_client(registry, agent)
    created = client.post("/api/threads", headers=AUTH, json={"user": "ana"})
    assert created.status_code == 201
    tid = created.json()["thread_id"]

    r = client.post("/api/chat", headers=AUTH, json={
        "message": "Forecast RATE_LONG 3 days ahead as of 2023-06-30", "thread_id": tid,
        "user": "ana"})
    assert r.headers["x-thread-id"] == tid
    assert parse_sse(r.text)[-1]["type"] == "run.finished"

    listing = client.get("/api/threads", headers=AUTH, params={"user": "ana"}).json()["threads"]
    assert [t["thread_id"] for t in listing] == [tid]
    assert listing[0]["title"].startswith("Forecast RATE_LONG")

    detail = client.get(f"/api/threads/{tid}", headers=AUTH, params={"user": "ana"}).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "tool", "assistant"]
    tool = detail["messages"][1]
    assert tool["name"] == "forecast_series" and tool["envelope"]["ok"] is True
    assert tool["envelope"]["provenance"]["snapshot_id"]

    # Another user label neither sees nor writes to the thread.
    assert client.get("/api/threads", headers=AUTH, params={"user": "bo"}).json() == {
        "threads": []}
    assert client.get(f"/api/threads/{tid}", headers=AUTH,
                      params={"user": "bo"}).status_code == 404
    assert client.post("/api/chat", headers=AUTH, json={
        "message": "hi", "thread_id": tid, "user": "bo"}).status_code == 404


def test_chat_without_thread_id_creates_one(registry):
    client = make_client(registry)
    r = client.post("/api/chat", headers=AUTH, json={"message": "What is a snapshot id?"})
    tid = r.headers["x-thread-id"]
    assert parse_sse(r.text)[0]["thread_id"] == tid
    assert client.get("/api/threads", headers=AUTH).json()["threads"][0]["thread_id"] == tid


def test_bad_thread_id_and_auth(registry):
    client = make_client(registry)
    assert client.post("/api/chat", headers=AUTH, json={
        "message": "x", "thread_id": "../../etc"}).status_code == 422
    assert client.get("/api/threads").status_code == 401
    assert client.get("/api/threads/abcdefgh").status_code == 401
    assert client.get("/api/threads", headers=AUTH, params={"limit": "x"}).status_code == 422


def test_loop_agent_threads_survive_restart(tmp_path, registry):
    db = str(tmp_path / "state.db")
    first = make_client(registry, state_db=db)
    r = first.post("/api/chat", headers=AUTH, json={"message": "What is a snapshot id?"})
    tid = r.headers["x-thread-id"]
    second = make_client(registry, state_db=db)   # a new process with the same state file
    detail = second.get(f"/api/threads/{tid}", headers=AUTH).json()
    assert detail["messages"][0] == {"role": "user", "content": "What is a snapshot id?"}


def test_graph_agent_threads_survive_restart(tmp_path, registry):
    pytest.importorskip("langgraph.checkpoint.sqlite")
    db = str(tmp_path / "state.db")
    first = make_client(registry, agent="graph", state_db=db)
    tid = first.post("/api/chat", headers=AUTH,
                     json={"message": "What is a snapshot id?"}).headers["x-thread-id"]
    second = make_client(registry, agent="graph", state_db=db)
    detail = second.get(f"/api/threads/{tid}", headers=AUTH).json()
    assert detail["messages"][0]["content"] == "What is a snapshot id?"
    assert detail["messages"][1]["name"] == "knowledge_search"


def test_config_usage_readyz_and_request_id(registry):
    client = make_client(registry, agent="loop")
    assert client.get("/api/config", headers=AUTH).json()["agent"] == "loop"
    assert set(client.get("/api/usage", headers=AUTH).json()) >= {"calls", "input_tokens",
                                                                    "output_tokens", "cost"}
    assert client.get("/readyz").json()["status"] == "ready"
    r = client.get("/healthz", headers={"X-Request-ID": "abc12345-req"})
    assert r.headers["x-request-id"] == "abc12345-req"
    generated = client.get("/healthz", headers={"X-Request-ID": "bad id\nwith newline"})
    assert generated.headers["x-request-id"] != "bad id\nwith newline"


def test_unknown_agent_is_rejected(registry):
    with pytest.raises(ValueError):
        make_client(registry, agent="nope")


def test_store_titles_and_ordering():
    store = ConversationStore()
    store.create("a" * 8, "u")
    store.touch("a" * 8, "u", "first question " * 10)
    assert store.get("a" * 8)["title"].endswith("...")
    store.touch("a" * 8, "u", "second question")
    assert store.get("a" * 8)["title"].startswith("first question")
    assert make_title("  spaced   out  ") == "spaced out"


def test_web_dist_is_served(tmp_path, registry):
    (tmp_path / "index.html").write_text("<!doctype html><title>built ui</title>",
                                         encoding="utf-8")
    settings = Settings(gateway_token=TOKEN, db_path=":memory:", web_dist=str(tmp_path))
    client = TestClient(create_app(settings, registry=registry, provider=ScriptedProvider()))
    assert "built ui" in client.get("/").text
    assert client.get("/healthz").json() == {"status": "ok"}
