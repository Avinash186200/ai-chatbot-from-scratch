"""Tests for the eval harness itself: the grounding rule and the threshold exit code."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_evals import main, numbers_in, ungrounded_numbers  # noqa: E402

ENVELOPE = {"ok": True, "payload": {"result": {"last_value": 3.4444, "horizon": 5,
                                               "path": [{"point": -0.25}],
                                               "last_observation": "2023-06-29"}},
            "provenance": {"as_of": "2023-06-30", "snapshot_id": "snap-1a2b3c4d",
                           "model_version": "1.1.0"}}


def test_numbers_in_prose():
    text = "RATE_X was 3.4444 on 2023-06-29, change -0.25, model 1.1.0, snap-1a2b, lower_90."
    assert numbers_in(text) == ["3.4444", "2023-06-29", "-0.25", "1.1.0"]


def test_grounded_answer_passes():
    answer = "It was 3.4444 on 2023-06-29; 5 days ahead it is -0.25 (model 1.1.0, " \
             "as of 2023-06-30, snap-1a2b3c4d)."
    assert ungrounded_numbers(answer, [ENVELOPE]) == []


def test_numbers_not_in_any_tool_result_are_flagged():
    answer = "It was 3.4444 and will be 3.5 by 2023-07-07 (90% sure)."
    assert ungrounded_numbers(answer, [ENVELOPE]) == ["3.5", "2023-07-07", "90"]


def test_equal_numbers_in_another_spelling_count_as_grounded():
    assert ungrounded_numbers("horizon 5.0", [ENVELOPE]) == []


def test_refusal_with_a_number_is_flagged():
    assert ungrounded_numbers("I think about 4.2", []) == ["4.2"]


def test_exit_code_below_threshold(tmp_path, capsys):
    bad = tmp_path / "q.yaml"
    bad.write_text(
        "threshold: 1.0\nquestions:\n"
        "  - id: wrong-route\n"
        "    question: Should I buy now\n"
        "    expect_route: forecast_series\n",
        encoding="utf-8")
    assert main(["--questions", str(bad)]) == 1
    assert "BELOW THRESHOLD" in capsys.readouterr().out


def test_shipped_question_set_passes(capsys):
    assert main([]) == 0


def test_route_report_per_route_math():
    from run_evals import route_report

    labels = [{"route": "tool"}, {"route": "tool"}, {"route": "documents"}, {"route": "refuse"}]
    report = route_report(labels, ["tool", "documents", "documents", "refuse"])
    assert report["accuracy"] == 0.75
    assert report["per_route"]["tool"] == {"n": 2, "correct": 1, "recall": 0.5,
                                           "precision": 1.0}
    assert report["per_route"]["documents"]["precision"] == 0.5


def test_retrieval_report_recall_mrr_and_abstention():
    from run_evals import retrieval_report

    from analytics_assistant.rag import RAGService

    rag = RAGService()
    rag.ingest_markdown("d", "g.md", "## Widgets\nwidgets gizmos\n\n## Sprockets\nsprockets cogs")
    queries = [{"q": "widgets", "relevant": ["g.md#widgets"]},
               {"q": "sprockets cogs", "relevant": ["g.md#widgets"]},
               {"q": "banana bread", "relevant": []}]
    report = retrieval_report(rag, queries, [1, 3])
    assert report["recall"] == {1: 0.5, 3: 0.5}
    assert report["mrr"] == 0.5 and report["abstain"] == 1.0


def test_router_and_retrieval_suites_pass(capsys):
    assert main(["--suite", "router"]) == 0
    assert main(["--suite", "retrieval"]) == 0
    out = capsys.readouterr().out
    assert "rules + classifier" in out and "recall@3" in out


def test_question_set_passes_with_the_graph_agent(capsys):
    import pytest

    pytest.importorskip("langgraph")
    assert main(["--suite", "questions", "--agent", "graph"]) == 0
