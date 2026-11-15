"""REST gateway over the tool registry: ``POST /gateway/<tool>`` per tool.

Same shape as an MCP-to-OpenAPI proxy, so plain HTTP clients (the Excel
add-in, curl, any HTTP client) can call MCP tools without speaking MCP. Every
request needs ``Authorization: Bearer <AA_GATEWAY_TOKEN>``; with no token
configured, every request is refused.
"""

from __future__ import annotations

import hmac
import json
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from analytics_assistant.tools import ToolRegistry


def check_bearer(request: Request, expected: str) -> JSONResponse | None:
    """None if the request carries the configured token, else the 401 to return."""
    header = request.headers.get("authorization", "")
    scheme, _, supplied = header.partition(" ")
    if (
        not expected
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(supplied.encode(), expected.encode())
    ):
        # Never echo the supplied header back.
        return JSONResponse({"detail": "Invalid or missing bearer token"}, status_code=401)
    return None


def openapi_spec(registry: ToolRegistry) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for name, spec in registry.tools.items():
        paths[f"/{name}"] = {
            "post": {
                "summary": name.replace("_", " ").title(),
                "description": spec.description,
                "operationId": name,
                "requestBody": {
                    "required": False,
                    "content": {"application/json": {"schema": registry.schema_for(name)}},
                },
                "responses": {"200": {"description": "Tool envelope"}},
            }
        }
    return {
        "openapi": "3.1.0",
        "info": {"title": "Analytics Assistant tool gateway", "version": "0.1.0"},
        "paths": paths,
    }


def gateway_routes(registry: ToolRegistry, token: str) -> list[Route]:
    async def spec(request: Request) -> JSONResponse:
        if denied := check_bearer(request, token):
            return denied
        return JSONResponse(openapi_spec(registry))

    async def call(request: Request) -> JSONResponse:
        if denied := check_bearer(request, token):
            return denied
        tool = request.path_params["tool"]
        if tool not in registry.tools:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        raw = await request.body()
        try:
            args = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return JSONResponse({"detail": "Body must be JSON"}, status_code=422)
        if not isinstance(args, dict):
            return JSONResponse({"detail": "Body must be a JSON object"}, status_code=422)
        return JSONResponse(registry.call(tool, args))

    return [
        Route("/gateway/openapi.json", spec, methods=["GET"]),
        Route("/gateway/{tool}", call, methods=["POST"]),
    ]
