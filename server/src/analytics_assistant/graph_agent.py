"""LangGraph agent option (``AA_AGENT=graph``).

The same job as ``agent.Agent``, as an explicit state graph:

    START -> router --tool------> tool --(more calls, budget left)--> tool
                    |                  \\-(text or budget spent)----> answer -> END
                    |--documents--> retrieval ----------------------> answer
                    \\--refuse----------------------------------------> answer

* **router** classifies the question with ``router.IntentRouter`` (rules,
  then a small classifier, numeric questions prefer tools).
* **tool** asks the LLM for tool calls with the registry catalog and runs
  them through the same ``ToolRegistry`` as MCP and REST.
* **retrieval** searches the RAG service directly (no LLM call needed to
  decide to search) and records it as a ``knowledge_search`` tool call.
* **answer** writes the reply: a fixed refusal for ``refuse``, the LLM's text
  for the other routes.

Every thread's state (the message history) is checkpointed after each node,
keyed by ``thread_id``: PostgreSQL when ``AA_DATABASE_URL`` is set
(``langgraph-checkpoint-postgres``), otherwise SQLite in ``AA_STATE_DB``,
otherwise memory (tests). A conversation therefore survives a restart, and
``history(thread_id)`` reads it back from the checkpoint.

``run`` yields the same event stream as the loop agent, plus
``route.decided``, so the web UI and the evals work with either agent.
"""

from __future__ import annotations

import json
import operator
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict

from analytics_assistant import telemetry
from analytics_assistant.agent import (
    KNOWLEDGE_TOOL,
    execute_tool,
    system_prompt,
    tool_catalog,
    tool_message,
)
from analytics_assistant.config import Settings
from analytics_assistant.llm_types import LLMError, Provider
from analytics_assistant.memory import PreferenceMemory
from analytics_assistant.offline_llm import REFUSAL
from analytics_assistant.router import (
    ROUTE_DOCS,
    ROUTE_REFUSE,
    IntentRouter,
    build_router,
)
from analytics_assistant.tools import ToolRegistry


class GraphState(TypedDict, total=False):
    messages: Annotated[list[dict[str, Any]], operator.add]   # checkpointed history
    question: str
    user: str
    route: str
    steps: int
    draft: str | None
    error: str | None
    emitted: list[list[Any]]   # [type, data] pairs produced by the node that just ran


def build_checkpointer(settings: Settings) -> Any:
    """Postgres if AA_DATABASE_URL is set, SQLite if AA_STATE_DB is set, else memory."""
    if settings.database_url:
        try:
            import psycopg
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("AA_DATABASE_URL with AA_AGENT=graph needs the 'postgres' "
                               "extra") from exc
        conn = psycopg.connect(settings.database_url, autocommit=True, prepare_threshold=0,
                               row_factory=dict_row)
        saver = PostgresSaver(conn)
        saver.setup()
        return saver
    if settings.state_db:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        return SqliteSaver(sqlite3.connect(settings.state_db, check_same_thread=False))
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


class GraphAgent:
    kind = "graph"

    def __init__(self, registry: ToolRegistry, provider: Provider, knowledge: Any,
                 memory: PreferenceMemory | None = None, max_steps: int = 6,
                 clock: Callable[[], datetime] | None = None,
                 id_factory: Callable[[], str] | None = None,
                 checkpointer: Any = None, router: IntentRouter | None = None) -> None:
        from langgraph.checkpoint.memory import InMemorySaver

        self.registry = registry
        self.provider = provider
        self.knowledge = knowledge
        self.memory = memory or PreferenceMemory()
        self.max_steps = max_steps
        self.now = clock or (lambda: datetime.now(UTC))
        self.new_id = id_factory or (lambda: uuid.uuid4().hex)
        self.router = router or build_router()
        self.checkpointer = checkpointer if checkpointer is not None else InMemorySaver()
        self.graph = self._build().compile(checkpointer=self.checkpointer)

    # ------------------------------------------------------------ nodes

    def _route(self, state: GraphState) -> dict[str, Any]:
        decision = self.router.decide(state["question"])
        return {"route": decision.route,
                "emitted": [["route.decided", {"route": decision.route,
                                               "source": decision.source,
                                               "confidence": decision.confidence}]]}

    def _tool(self, state: GraphState) -> dict[str, Any]:
        system = system_prompt(self.memory, state.get("user", "local"))
        try:
            reply = self.provider.complete(
                [{"role": "system", "content": system}, *state["messages"]],
                tool_catalog(self.registry, include_knowledge=False))
        except LLMError as exc:
            return {"error": str(exc), "emitted": []}
        if not reply.tool_calls:
            return {"draft": reply.content, "emitted": []}
        emitted: list[list[Any]] = []
        new_messages: list[dict[str, Any]] = [{
            "role": "assistant", "content": reply.content or None,
            "tool_calls": [{"id": c.id, "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                           for c in reply.tool_calls]}]
        for call in reply.tool_calls:
            emitted.append(["tool.called", {"id": call.id, "name": call.name,
                                            "arguments": call.arguments}])
            envelope = execute_tool(self.registry, self.knowledge, call.name, call.arguments)
            emitted.append(["tool.result", {"id": call.id, "name": call.name,
                                            "ok": envelope.get("ok"), "envelope": envelope}])
            new_messages.append(tool_message(call.id, envelope))
        return {"messages": new_messages, "steps": state.get("steps", 0) + 1,
                "emitted": emitted}

    def _retrieve(self, state: GraphState) -> dict[str, Any]:
        call_id = f"rag-{state.get('steps', 0)}"
        args = {"query": state["question"]}
        envelope = execute_tool(self.registry, self.knowledge, KNOWLEDGE_TOOL, args)
        return {
            "messages": [
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": call_id, "type": "function",
                    "function": {"name": KNOWLEDGE_TOOL, "arguments": json.dumps(args)}}]},
                tool_message(call_id, envelope)],
            "steps": state.get("steps", 0) + 1,
            "emitted": [["tool.called", {"id": call_id, "name": KNOWLEDGE_TOOL,
                                         "arguments": args}],
                        ["tool.result", {"id": call_id, "name": KNOWLEDGE_TOOL,
                                         "ok": envelope.get("ok"), "envelope": envelope}]],
        }

    def _answer(self, state: GraphState) -> dict[str, Any]:
        if state.get("error"):
            return {"emitted": [["run.failed", {"error": state["error"]}]]}
        if state.get("route") == ROUTE_REFUSE:
            text = REFUSAL
        elif state.get("draft") is not None:
            text = state["draft"] or ""
        elif state.get("steps", 0) >= self.max_steps and state.get("route") != ROUTE_DOCS:
            return {"emitted": [["run.failed",
                                 {"error": f"stopped after {self.max_steps} steps"}]]}
        else:
            system = system_prompt(self.memory, state.get("user", "local"))
            try:
                text = self.provider.complete(
                    [{"role": "system", "content": system}, *state["messages"]], []).content
            except LLMError as exc:
                return {"emitted": [["run.failed", {"error": str(exc)}]]}
        return {"messages": [{"role": "assistant", "content": text}],
                "emitted": [["message.completed", {"content": text}],
                            ["run.finished", {"status": "completed"}]]}

    # ------------------------------------------------------------ edges

    def _after_router(self, state: GraphState) -> str:
        return {"tool": "tool", ROUTE_DOCS: "retrieval"}.get(state["route"], "answer")

    def _after_tool(self, state: GraphState) -> str:
        if state.get("error") or state.get("draft") is not None:
            return "answer"
        return "answer" if state.get("steps", 0) >= self.max_steps else "tool"

    def _build(self) -> Any:
        from langgraph.graph import END, START, StateGraph

        g = StateGraph(GraphState)
        g.add_node("router", self._route)
        g.add_node("tool", self._tool)
        g.add_node("retrieval", self._retrieve)
        g.add_node("answer", self._answer)
        g.add_edge(START, "router")
        g.add_conditional_edges("router", self._after_router,
                                {"tool": "tool", "retrieval": "retrieval", "answer": "answer"})
        g.add_conditional_edges("tool", self._after_tool, {"tool": "tool", "answer": "answer"})
        g.add_edge("retrieval", "answer")
        g.add_edge("answer", END)
        return g

    # ------------------------------------------------------------ public API

    def history(self, thread_id: str) -> list[dict[str, Any]]:
        snapshot = self.graph.get_state({"configurable": {"thread_id": thread_id}})
        return list((snapshot.values or {}).get("messages", []))

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
        error = None
        config = {"configurable": {"thread_id": thread_id},
                  "recursion_limit": 2 * self.max_steps + 6}
        # Per-run fields are reset; "messages" is appended to the checkpointed history.
        inputs: GraphState = {"messages": [{"role": "user", "content": message}],
                              "question": message, "user": user, "route": "", "steps": 0,
                              "draft": None, "error": None, "emitted": []}
        try:
            yield event("run.started", {"message": message, "agent": self.kind})
            stream = self.graph.stream(inputs, config, stream_mode="updates")
            while True:
                with span.active():
                    update = next(stream, None)
                if update is None:
                    break
                for _node, values in update.items():
                    for kind, data in (values or {}).get("emitted") or []:
                        if kind == "run.failed":
                            error = data.get("error")
                        yield event(kind, data)
        except Exception as exc:  # a graph or checkpointer fault, not a tool refusal
            error = f"agent failed ({type(exc).__name__})"
            yield event("run.failed", {"error": error})
        finally:
            span.end(error)
