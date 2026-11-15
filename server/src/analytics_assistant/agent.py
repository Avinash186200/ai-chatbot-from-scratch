"""Tool-calling chat agent that streams typed events (the default, ``AA_AGENT=loop``).

One run: the user's message goes to the LLM with the tool catalog; each tool
call the model makes is executed through the registry (same validation and
envelope as MCP/REST) and fed back; the loop ends when the model answers in
text or the step budget runs out. Every step is emitted as an event

    {v, seq, run_id, thread_id, ts, type, data}

so a UI can stream progress, show which tools ran and replay a run by
``seq``. Event types: run.started, route.decided (graph agent only),
tool.called, tool.result, message.completed, run.failed, run.finished.

Thread history goes through a ``HistoryStore`` (in memory by default; the
web app passes the SQLite/Postgres-backed conversation store so threads
survive a restart).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any, Protocol

from analytics_assistant import telemetry
from analytics_assistant.knowledge import KnowledgeIndex
from analytics_assistant.llm_types import LLMError, Provider
from analytics_assistant.memory import PreferenceMemory
from analytics_assistant.tools import ToolRegistry

SYSTEM_PROMPT = (
    "You are an analytics assistant. Answer with numbers only from tool results, and "
    "always state the as_of date and snapshot_id the number came from. If a tool "
    "refuses, say so plainly; do not guess a value. Use knowledge_search for "
    "definitions and methodology, cite the source it returns, and say you don't know "
    "when it returns nothing."
)
KNOWLEDGE_TOOL = "knowledge_search"
MAX_TOOL_RESULT_CHARS = 8000


class HistoryStore(Protocol):
    def load(self, thread_id: str) -> list[dict[str, Any]]: ...

    def save(self, thread_id: str, messages: list[dict[str, Any]]) -> None: ...


class InMemoryHistory:
    def __init__(self) -> None:
        self.threads: dict[str, list[dict[str, Any]]] = {}

    def load(self, thread_id: str) -> list[dict[str, Any]]:
        return list(self.threads.get(thread_id, []))

    def save(self, thread_id: str, messages: list[dict[str, Any]]) -> None:
        self.threads[thread_id] = list(messages)


def tool_catalog(registry: ToolRegistry, include_knowledge: bool = True) -> list[dict[str, Any]]:
    catalog = [
        {"type": "function",
         "function": {"name": name, "description": spec.description,
                      "parameters": registry.schema_for(name)}}
        for name, spec in registry.tools.items() if name != "health"
    ]
    if include_knowledge:
        catalog.append({"type": "function", "function": {
            "name": KNOWLEDGE_TOOL,
            "description": "Search the reference documents for definitions and methodology.",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string"},
                "collections": {"type": "array", "items": {"type": "string"}}},
                "required": ["query"]}}})
    return catalog


def execute_tool(registry: ToolRegistry, knowledge: Any, name: str,
                 args: dict[str, Any]) -> dict[str, Any]:
    """Run one tool call (registry tool or knowledge search) and return its envelope."""
    if name == KNOWLEDGE_TOOL:
        try:
            hits = knowledge.search(str(args.get("query", "")), args.get("collections") or [])
        except ValueError as exc:
            return {"tool": name, "ok": False,
                    "payload": {"error": {"code": "SCHEMA_VIOLATION", "message": str(exc)}}}
        return {"tool": name, "ok": True, "payload": {"result": hits}}
    if name not in registry.tools:
        return {"tool": name, "ok": False, "payload": {"error": {
            "code": "SCHEMA_VIOLATION", "message": f"no tool named {name!r}"}}}
    return registry.call(name, args)


def tool_message(call_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
    content = json.dumps(envelope)
    if len(content) > MAX_TOOL_RESULT_CHARS:
        content = content[:MAX_TOOL_RESULT_CHARS] + "...(truncated)"
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def system_prompt(memory: PreferenceMemory, user: str) -> str:
    system = SYSTEM_PROMPT
    if prefs := memory.recall(user):
        system += "\nUser preferences: " + "; ".join(prefs)
    return system


def transcript(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI-shaped history -> the items a UI shows when a conversation is resumed."""
    out: list[dict[str, Any]] = []
    calls: dict[str, dict[str, Any]] = {}
    for m in messages:
        role = m.get("role")
        if role == "user":
            out.append({"role": "user", "content": m.get("content") or ""})
        elif role == "assistant":
            for c in m.get("tool_calls") or []:
                fn = c.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls[c.get("id", "")] = {"name": fn.get("name", ""), "arguments": args}
            if m.get("content"):
                out.append({"role": "assistant", "content": m["content"]})
        elif role == "tool":
            call = calls.get(m.get("tool_call_id", ""), {"name": "", "arguments": {}})
            try:
                envelope = json.loads(m.get("content") or "{}")
            except json.JSONDecodeError:
                envelope = {"ok": False, "truncated": True}
            out.append({"role": "tool", "id": m.get("tool_call_id", ""), **call,
                        "envelope": envelope})
    return out


class Agent:
    kind = "loop"

    def __init__(self, registry: ToolRegistry, provider: Provider,
                 knowledge: Any | None = None,
                 memory: PreferenceMemory | None = None, max_steps: int = 6,
                 clock: Callable[[], datetime] | None = None,
                 id_factory: Callable[[], str] | None = None,
                 history: HistoryStore | None = None) -> None:
        self.registry = registry
        self.provider = provider
        self.knowledge = knowledge or KnowledgeIndex()
        self.memory = memory or PreferenceMemory()
        self.max_steps = max_steps
        self.now = clock or (lambda: datetime.now(UTC))
        self.new_id = id_factory or (lambda: uuid.uuid4().hex)
        self.history_store: HistoryStore = history or InMemoryHistory()

    @property
    def threads(self) -> dict[str, list[dict[str, Any]]]:
        """Phase-1 accessor: the in-memory threads (only for the default history store)."""
        return getattr(self.history_store, "threads", {})

    def history(self, thread_id: str) -> list[dict[str, Any]]:
        return self.history_store.load(thread_id)

    def run(self, message: str, user: str = "local", thread_id: str | None = None
            ) -> Iterator[dict[str, Any]]:
        thread_id = thread_id or self.new_id()
        run_id = self.new_id()
        seq = 0

        def event(kind: str, data: dict[str, Any]) -> dict[str, Any]:
            nonlocal seq
            seq += 1
            return {"v": 1, "seq": seq, "run_id": run_id, "thread_id": thread_id,
                    "ts": self.now().isoformat(), "type": kind, "data": data}

        span = telemetry.RunSpan("agent.run", {"agent.kind": self.kind, "agent.run_id": run_id})
        history = self.history_store.load(thread_id)
        system = system_prompt(self.memory, user)
        history.append({"role": "user", "content": message})
        tools = tool_catalog(self.registry)
        error = None
        try:
            yield event("run.started", {"message": message, "agent": self.kind})
            for step in range(self.max_steps):
                try:
                    with span.active():
                        reply = self.provider.complete(
                            [{"role": "system", "content": system}, *history], tools)
                except LLMError as exc:
                    error = str(exc)
                    yield event("run.failed", {"error": error})
                    return
                if not reply.tool_calls:
                    history.append({"role": "assistant", "content": reply.content})
                    span.set_attribute("agent.steps", step + 1)
                    yield event("message.completed", {"content": reply.content})
                    yield event("run.finished", {"status": "completed"})
                    return
                history.append({"role": "assistant", "content": reply.content or None,
                                "tool_calls": [{"id": c.id, "type": "function",
                                                "function": {"name": c.name,
                                                             "arguments": json.dumps(c.arguments)}}
                                               for c in reply.tool_calls]})
                for call in reply.tool_calls:
                    yield event("tool.called", {"id": call.id, "name": call.name,
                                                "arguments": call.arguments})
                    with span.active():
                        envelope = execute_tool(self.registry, self.knowledge, call.name,
                                                call.arguments)
                    yield event("tool.result", {"id": call.id, "name": call.name,
                                                "ok": envelope.get("ok"), "envelope": envelope})
                    history.append(tool_message(call.id, envelope))
            error = f"stopped after {self.max_steps} steps"
            yield event("run.failed", {"error": error})
        finally:
            self.history_store.save(thread_id, history)
            span.end(error)
