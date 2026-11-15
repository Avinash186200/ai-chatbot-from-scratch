"""LangGraph agent: routing, tools through the registry, retrieval, refusals, checkpoints."""

import pytest

pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from analytics_assistant.config import Settings  # noqa: E402
from analytics_assistant.graph_agent import GraphAgent, build_checkpointer  # noqa: E402
from analytics_assistant.llm import Completion, LLMError, ScriptedProvider, ToolCall  # noqa: E402
from analytics_assistant.web import build_knowledge  # noqa: E402


@pytest.fixture
def knowledge():
    return build_knowledge(Settings())


def types(events):
    return [e["type"] for e in events]


def test_tool_route_runs_registry_tool_and_answers(registry, knowledge):
    agent = GraphAgent(registry, ScriptedProvider(), knowledge)
    events = list(agent.run("Forecast RATE_LONG 3 days ahead as of 2023-06-30", thread_id="t1"))
    assert types(events) == ["run.started", "route.decided", "tool.called", "tool.result",
                             "message.completed", "run.finished"]
    assert events[1]["data"]["route"] == "tool"
    assert events[3]["data"]["envelope"]["ok"] is True
    assert "snapshot" in events[4]["data"]["content"]
    assert [e["seq"] for e in events] == list(range(1, 7))


def test_documents_route_cites_a_section(registry, knowledge):
    agent = GraphAgent(registry, ScriptedProvider(), knowledge)
    events = list(agent.run("What is a snapshot id?"))
    assert events[1]["data"]["route"] == "documents"
    hits = events[3]["data"]["envelope"]["payload"]["result"]
    assert hits[0]["citation"] == {"doc_id": "methodology.md", "section": "snapshot-id",
                                   "chunk": 0}
    assert events[4]["data"]["content"].startswith("From methodology.md#snapshot-id")


def test_documents_route_says_i_dont_know_below_the_floor(registry, knowledge):
    events = list(GraphAgent(registry, ScriptedProvider(), knowledge).run(
        "Explain quantum chromodynamics"))
    assert events[3]["data"]["envelope"]["payload"]["result"] == []
    assert events[4]["data"]["content"].startswith("I don't know")


def test_refuse_route_calls_no_tool_and_no_llm(registry, knowledge):
    provider = ScriptedProvider([])
    events = list(GraphAgent(registry, provider, knowledge).run("Should I buy bonds?"))
    assert types(events) == ["run.started", "route.decided", "message.completed",
                             "run.finished"]
    assert provider.calls == []


def test_multi_step_tool_loop_and_step_budget(registry, knowledge):
    call = Completion(tool_calls=[ToolCall("x", "query_series", {"series": "POLICY_RATE",
                                                                 "as_of": "2021-01-15"})])
    provider = ScriptedProvider([call, call, call])
    events = list(GraphAgent(registry, provider, knowledge, max_steps=2).run(
        "Show POLICY_RATE values"))
    assert types(events).count("tool.called") == 2
    assert events[-1]["type"] == "run.failed" and "2 steps" in events[-1]["data"]["error"]


def test_llm_failure_is_a_run_failed_event(registry, knowledge):
    def boom(_messages):
        raise LLMError("primary: HTTP 503")

    events = list(GraphAgent(registry, ScriptedProvider([boom]), knowledge).run(
        "Forecast RATE_LONG 2 days ahead"))
    assert events[-1] == {**events[-1], "type": "run.failed",
                          "data": {"error": "primary: HTTP 503"}}


def test_checkpointer_keeps_thread_history_across_agent_instances(registry, knowledge):
    saver = InMemorySaver()
    first = GraphAgent(registry, ScriptedProvider(), knowledge, checkpointer=saver)
    list(first.run("What is a snapshot id?", thread_id="keep"))
    # A new agent (as after a restart) with the same checkpointer sees the thread.
    provider = ScriptedProvider()
    second = GraphAgent(registry, provider, knowledge, checkpointer=saver)
    list(second.run("Forecast RATE_LONG 2 days ahead as of 2023-06-30", thread_id="keep"))
    history = second.history("keep")
    assert [m["content"] for m in history if m["role"] == "user"] == [
        "What is a snapshot id?", "Forecast RATE_LONG 2 days ahead as of 2023-06-30"]
    # The LLM saw the earlier turn too.
    assert any(m.get("content") == "What is a snapshot id?" for m in provider.calls[0])
    assert second.history("other") == []


def test_sqlite_checkpointer_survives_restart(tmp_path, registry, knowledge):
    pytest.importorskip("langgraph.checkpoint.sqlite")
    settings = Settings(state_db=str(tmp_path / "state.db"))
    agent = GraphAgent(registry, ScriptedProvider(), knowledge,
                       checkpointer=build_checkpointer(settings))
    list(agent.run("What is a snapshot id?", thread_id="durable"))
    reopened = GraphAgent(registry, ScriptedProvider(), knowledge,
                          checkpointer=build_checkpointer(settings))
    assert reopened.history("durable")[0] == {"role": "user", "content": "What is a snapshot id?"}


def test_memory_checkpointer_is_the_default():
    assert type(build_checkpointer(Settings())).__name__ == "InMemorySaver"
