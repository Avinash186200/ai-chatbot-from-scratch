"""HTTP application: tool gateway, streaming chat API, conversations and the web UI.

    GET  /healthz                 unauthenticated liveness
    GET  /readyz                  unauthenticated readiness (store and agent built)
    GET  /gateway/openapi.json    tool catalog (bearer)
    POST /gateway/<tool>          call one tool (bearer)
    POST /api/chat                run the agent, Server-Sent Events (bearer)
    GET  /api/threads             list conversations for a user label (bearer)
    POST /api/threads             create a conversation (bearer)
    GET  /api/threads/<id>        one conversation with its transcript (bearer)
    POST /api/memory              store a user preference (bearer)
    GET  /api/usage               LLM call, token and cost counters (bearer)
    GET  /api/config              which agent and LLM provider are active (bearer)
    GET  /                        the built web UI (AA_WEB_DIST) or a minimal static page

Every response carries an X-Request-ID (taken from the request when it is a
safe token, otherwise generated); logs and spans carry the same id.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Iterator
from importlib import resources
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from analytics_assistant import __version__, telemetry
from analytics_assistant.agent import Agent, transcript
from analytics_assistant.asof_store import AsOfStore
from analytics_assistant.config import Settings
from analytics_assistant.conversations import ConversationStore
from analytics_assistant.gateway import check_bearer, gateway_routes
from analytics_assistant.llm import Provider, build_provider
from analytics_assistant.memory import MemoryRejected, PreferenceMemory
from analytics_assistant.rag import InMemoryVectorStore, RAGService, VectorStore, build_embedder
from analytics_assistant.synthetic import seed_store
from analytics_assistant.tools import ToolRegistry

MAX_MESSAGE_CHARS = 4000
access_log = logging.getLogger("analytics_assistant.access")

REFERENCE_COLLECTION = "reference"
CORPUS_FILES = ("methodology.md", "tool-guide.md")


def build_knowledge(settings: Settings | None = None) -> RAGService:
    """The retrieval service with the packaged corpus (and AA_DOCS_DIR, if set) ingested.

    Uses pgvector when AA_DATABASE_URL is set, else an in-memory store.
    Ingestion replaces each document's chunks, so this is safe on every start.
    """
    settings = settings or Settings()
    embedder = build_embedder(settings.embedder, settings.embedding_model)
    if settings.database_url:
        from analytics_assistant.rag.store import PgVectorStore

        store: VectorStore = PgVectorStore.from_url(settings.database_url, embedder.dimensions)
    else:
        store = InMemoryVectorStore()
    rag = RAGService(embedder, store, settings.rag_min_score)
    corpus = resources.files("analytics_assistant").joinpath("corpus")
    for name in CORPUS_FILES:
        rag.ingest_markdown(REFERENCE_COLLECTION, name,
                            corpus.joinpath(name).read_text(encoding="utf-8"))
    if settings.docs_dir:
        rag.ingest_dir("documents", settings.docs_dir)
    return rag


def build_registry(settings: Settings) -> ToolRegistry:
    store = AsOfStore(settings.db_path)
    if store.latest_publication() is None:
        seed_store(store, settings.synthetic_seed)
    return ToolRegistry(store)


def sse(events: Iterator[dict]) -> Iterator[str]:
    for e in events:
        yield f"event: message\nid: {e['seq']}\ndata: {json.dumps(e)}\n\n"


def build_agent(settings: Settings, registry: ToolRegistry, provider: Provider,
                knowledge: RAGService, conversations: ConversationStore) -> Any:
    memory = PreferenceMemory()
    if settings.agent == "graph":
        from analytics_assistant.graph_agent import GraphAgent, build_checkpointer

        return GraphAgent(registry, provider, knowledge, memory, settings.agent_max_steps,
                          checkpointer=build_checkpointer(settings))
    if settings.agent != "loop":
        raise ValueError(f"unknown AA_AGENT {settings.agent!r} (use loop or graph)")
    return Agent(registry, provider, knowledge, memory, settings.agent_max_steps,
                 history=conversations)


_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class RequestIdMiddleware:
    """Pure ASGI: sets the request id context variable, echoes it, logs one access line.

    Never logs headers, query strings or bodies (they can carry tokens).
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode(
            "latin-1")
        rid = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        token = telemetry.request_id_var.set(rid)
        started = time.perf_counter()
        status = {"code": 500}

        async def send_with_id(message: dict) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message["headers"] = [*message.get("headers", []),
                                      (b"x-request-id", rid.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            access_log.info("request", extra={
                "event": "http.request", "method": scope.get("method"),
                "path": scope.get("path"), "status": status["code"],
                "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
            telemetry.request_id_var.reset(token)


def create_app(settings: Settings | None = None, registry: ToolRegistry | None = None,
               provider: Provider | None = None, agent: Any | None = None) -> Starlette:
    settings = settings or Settings.from_env()
    registry = registry or build_registry(settings)
    knowledge = build_knowledge(settings)
    conversations = ConversationStore(settings.state_db)
    agent = agent or build_agent(settings, registry, provider or build_provider(settings),
                                 knowledge, conversations)
    telemetry.configure_prices(settings.llm_price_in_per_mtok, settings.llm_price_out_per_mtok)
    token = settings.gateway_token

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def readyz(_: Request) -> JSONResponse:
        ready = registry.store.latest_publication() is not None
        return JSONResponse({"status": "ready" if ready else "starting", "agent": agent.kind},
                            status_code=200 if ready else 503)

    async def index(_: Request) -> HTMLResponse:
        html = resources.files("analytics_assistant").joinpath("static/index.html").read_text(
            encoding="utf-8")
        return HTMLResponse(html)

    async def read_json(request: Request) -> dict | None:
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return body if isinstance(body, dict) else None

    def user_label(value: Any) -> str:
        label = str(value or "local").strip()[:64]
        return label or "local"

    async def chat(request: Request):
        if denied := check_bearer(request, token):
            return denied
        body = await read_json(request)
        message = (body or {}).get("message")
        if not isinstance(message, str) or not message.strip():
            return JSONResponse({"detail": "message is required"}, status_code=422)
        if len(message) > MAX_MESSAGE_CHARS:
            return JSONResponse({"detail": "message is too long"}, status_code=413)
        raw_thread = body.get("thread_id")
        if raw_thread is not None and not (isinstance(raw_thread, str)
                                           and _SAFE_ID.match(raw_thread)):
            return JSONResponse({"detail": "thread_id must be 8-64 of [A-Za-z0-9._-]"},
                                status_code=422)
        thread_id = raw_thread or uuid.uuid4().hex
        user = user_label(body.get("user"))
        existing = conversations.get(thread_id)
        if existing is not None and existing["user"] != user:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        conversations.touch(thread_id, user, message)
        events = agent.run(message, user=user, thread_id=thread_id)
        return StreamingResponse(sse(events), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Thread-Id": thread_id})

    async def threads(request: Request) -> JSONResponse:
        if denied := check_bearer(request, token):
            return denied
        if request.method == "POST":
            body = await read_json(request) or {}
            thread = conversations.create(uuid.uuid4().hex, user_label(body.get("user")),
                                          str(body.get("title") or "New conversation"))
            return JSONResponse(thread, status_code=201)
        try:
            limit = int(request.query_params.get("limit", "50"))
        except ValueError:
            return JSONResponse({"detail": "limit must be an integer"}, status_code=422)
        user = user_label(request.query_params.get("user"))
        return JSONResponse({"threads": conversations.list(user, limit)})

    async def thread_detail(request: Request) -> JSONResponse:
        if denied := check_bearer(request, token):
            return denied
        thread_id = request.path_params["thread_id"]
        thread = conversations.get(thread_id)
        user = user_label(request.query_params.get("user"))
        if thread is None or thread["user"] != user:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return JSONResponse({**thread, "messages": transcript(agent.history(thread_id))})

    async def memory(request: Request) -> JSONResponse:
        if denied := check_bearer(request, token):
            return denied
        body = await read_json(request) or {}
        try:
            agent.memory.remember(user_label(body.get("user")), str(body.get("note") or ""))
        except MemoryRejected as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        return JSONResponse({"stored": True})

    async def usage(request: Request) -> JSONResponse:
        if denied := check_bearer(request, token):
            return denied
        return JSONResponse(telemetry.meter.snapshot())

    async def config(request: Request) -> JSONResponse:
        if denied := check_bearer(request, token):
            return denied
        return JSONResponse({"agent": agent.kind, "llm_provider": settings.llm_provider,
                             "llm_model": settings.llm_model, "version": __version__})

    routes: list[Any] = [
        Route("/healthz", healthz),
        Route("/readyz", readyz),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/threads", threads, methods=["GET", "POST"]),
        Route("/api/threads/{thread_id}", thread_detail, methods=["GET"]),
        Route("/api/memory", memory, methods=["POST"]),
        Route("/api/usage", usage, methods=["GET"]),
        Route("/api/config", config, methods=["GET"]),
        *gateway_routes(registry, token),
    ]
    if settings.web_dist and Path(settings.web_dist, "index.html").is_file():
        routes.append(Mount("/", app=StaticFiles(directory=settings.web_dist, html=True)))
    else:
        routes.append(Route("/", index))
    middleware = [Middleware(RequestIdMiddleware),
                  Middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                             allow_methods=["GET", "POST"],
                             allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
                             expose_headers=["X-Request-ID", "X-Thread-Id"])]
    app = Starlette(routes=routes, middleware=middleware)
    app.state.registry = registry
    app.state.agent = agent
    app.state.conversations = conversations
    app.state.knowledge = knowledge
    return app
