import asyncio

import pytest

pytest.importorskip("mcp.server.mcpserver")

from starlette.testclient import TestClient  # noqa: E402

from analytics_assistant.mcp_server import build_mcp_http_app, build_mcp_server  # noqa: E402

TOKEN = "mcp-test-token"


def test_every_registry_tool_is_an_mcp_tool(registry):
    server = build_mcp_server(registry)
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == set(registry.tools)


def test_mcp_http_requires_bearer(registry):
    app = build_mcp_http_app(registry, TOKEN)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.post("/mcp", json={}).status_code == 401
        assert client.post("/mcp", json={},
                           headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_mcp_http_with_no_token_refuses_everything(registry):
    app = build_mcp_http_app(registry, "")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/mcp", json={}, headers={"Authorization": "Bearer "}
                           ).status_code == 401


def test_mcp_http_lists_tools_with_token(registry):
    app = build_mcp_http_app(registry, TOKEN)
    headers = {"Authorization": f"Bearer {TOKEN}",
               "Accept": "application/json, text/event-stream",
               "Content-Type": "application/json"}
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"}}}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        r = client.post("/mcp", json=init, headers=headers)
        assert r.status_code == 200, r.text
        r = client.post("/mcp", headers=headers,
                        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert r.status_code == 200, r.text
        assert "forecast_series" in r.text
