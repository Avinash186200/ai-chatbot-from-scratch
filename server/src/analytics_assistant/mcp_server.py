"""Expose the tool registry as an MCP server (stdio or streamable HTTP).

Requires the optional ``mcp`` extra (MCP Python SDK 2.x). Each registry tool
becomes one MCP tool taking a single ``arguments`` object, so the JSON schema
stays the registry's own and validation happens in one place.

Over HTTP (``mcp-http``), the streamable HTTP app is wrapped in the same
bearer check as the REST gateway: with no token configured, every request is
refused. stdio needs no token (the client starts the process itself).
"""

from __future__ import annotations

from typing import Any

from analytics_assistant.tools import ToolRegistry


def build_mcp_server(registry: ToolRegistry) -> Any:
    from mcp.server.mcpserver import MCPServer  # optional dependency

    server = MCPServer(
        name="analytics-assistant",
        instructions=(
            "Model and data tools. Every answer is computed only from data published by "
            "the as_of date; check provenance before quoting a number."
        ),
    )

    for name in registry.tools:
        def make(tool_name: str):
            def handler(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
                return registry.call(tool_name, arguments or {})

            handler.__name__ = tool_name
            return handler

        server.add_tool(
            make(name),
            name=name,
            description=registry.tools[name].description
            + " Pass arguments as an object matching: "
            + str(registry.schema_for(name)["properties"]),
        )
    return server


class BearerGuard:
    """ASGI wrapper: HTTP requests need ``Authorization: Bearer <token>``; lifespan passes."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope.get("path") != "/healthz":
            import hmac

            header = dict(scope.get("headers") or []).get(b"authorization", b"").decode(
                "latin-1")
            scheme, _, supplied = header.partition(" ")
            if (not self.token or scheme.lower() != "bearer"
                    or not hmac.compare_digest(supplied.encode(), self.token.encode())):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body",
                            "body": b'{"detail": "Invalid or missing bearer token"}'})
                return
        if scope["type"] == "http" and scope.get("path") == "/healthz":
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"status": "ok"}'})
            return
        await self.app(scope, receive, send)


DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*")


def build_mcp_http_app(registry: ToolRegistry, token: str, host: str = "127.0.0.1",
                       allowed_hosts: tuple[str, ...] | list[str] = DEFAULT_ALLOWED_HOSTS
                       ) -> Any:
    """The streamable HTTP MCP app at /mcp, behind the bearer guard, with /healthz open.

    DNS-rebinding protection stays on: only the listed Host headers are served
    (behind a reverse proxy, add the public host name, e.g. ``AA_MCP_ALLOWED_HOSTS``).
    """
    from mcp.server.transport_security import TransportSecuritySettings

    server = build_mcp_server(registry)
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                         allowed_hosts=list(allowed_hosts),
                                         allowed_origins=[])
    app = server.streamable_http_app(host=host, stateless_http=True,
                                     transport_security=security)
    return BearerGuard(app, token)
