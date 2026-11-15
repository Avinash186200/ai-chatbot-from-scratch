"""Run the eval suites and print a table per suite.

    python evals/run_evals.py                      # every suite, offline, no network
    python evals/run_evals.py --suite questions    # end-to-end question set only
    python evals/run_evals.py --suite router       # intent router, per-route accuracy
    python evals/run_evals.py --suite retrieval    # RAG recall@k and abstention
    python evals/run_evals.py --agent graph        # question set through the LangGraph agent
    python evals/run_evals.py --live               # questions against the LLM in AA_LLM_*

Suites:

* **questions** (questions.yaml): each question goes through the real agent
  loop (or the LangGraph agent with ``--agent graph``), tool registry, model
  packages and point-in-time store (synthetic history plus a synthetic
  vendor drop feed). A question passes when the agent takes the expected
  route, the first tool result carries the expected envelope code, the run
  completes, and every number in the answer appears in a tool result.
* **router** (router_labels.yaml): the intent router alone, rules only and
  rules + classifier, with precision, recall and accuracy per route.
* **retrieval** (retrieval_labels.yaml): the RAG service over the packaged
  corpus: recall@k, MRR, and how often it abstains when nothing is relevant.

Exits 1 when any suite is below its threshold (from its YAML file, or
--threshold for the question set), so CI fails on a regression.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from analytics_assistant.agent import KNOWLEDGE_TOOL, Agent
from analytics_assistant.asof_store import AsOfStore
from analytics_assistant.config import Settings
from analytics_assistant.ingest.sftp_feed import DropFeedIngester, LocalDropSource
from analytics_assistant.ingest.synthetic_feed import write_synthetic_feed
from analytics_assistant.llm import Provider, ScriptedProvider, build_provider
from analytics_assistant.router import ROUTES, build_router
from analytics_assistant.synthetic import seed_store
from analytics_assistant.tools import ToolRegistry
from analytics_assistant.web import build_knowledge

HERE = Path(__file__).resolve().parent
FIXED_NOW = datetime(2025, 3, 3, 12, 0, tzinfo=UTC)

# A number as written in prose: integers, decimals, negatives, and compound
# tokens such as dates (2023-06-30) or versions (1.1.0). Digits inside
# identifiers (snap-1a2b, lower_90, ar1) are not numbers.
NUMBER = re.compile(r"(?<![\w.\-])-?\d+(?:[.\-]\d+)*(?![\w])")


# ---------------------------------------------------------------------- grounding


def numbers_in(text: str) -> list[str]:
    return NUMBER.findall(text)


def _leaves(value: Any):
    if isinstance(value, dict):
        for v in value.values():
            yield from _leaves(v)
    elif isinstance(value, list):
        for v in value:
            yield from _leaves(v)
    else:
        yield value


def grounding_vocabulary(tool_results: list[Any]) -> tuple[set[str], list[float]]:
    tokens: set[str] = set()
    values: list[float] = []
    for result in tool_results:
        for leaf in _leaves(result):
            if isinstance(leaf, bool) or leaf is None:
                continue
            if isinstance(leaf, int | float):
                values.append(float(leaf))
                tokens.add(str(leaf))
            elif isinstance(leaf, str):
                tokens.update(numbers_in(leaf))
    return tokens, values


def ungrounded_numbers(answer: str, tool_results: list[Any]) -> list[str]:
    """Numbers in ``answer`` that appear in no tool result."""
    tokens, values = grounding_vocabulary(tool_results)
    missing = []
    for tok in numbers_in(answer):
        if tok in tokens:
            continue
        try:
            number = float(tok)
        except ValueError:  # a compound token (date, version) must match exactly
            missing.append(tok)
            continue
        if not any(abs(number - v) <= 1e-9 * max(1.0, abs(v)) for v in values):
            missing.append(tok)
    return missing


# ---------------------------------------------------------------------- question suite


@dataclass
class Outcome:
    id: str
    expect_route: str
    route: str
    expect_code: str | None
    code: str | None
    expect_source: str | None
    source: str | None
    ungrounded: list[str]
    completed: bool
    answer: str

    @property
    def passed(self) -> bool:
        return (self.route == self.expect_route
                and (self.expect_code is None or self.code == self.expect_code)
                and (self.expect_source is None or self.source == self.expect_source)
                and not self.ungrounded and self.completed)


def build_registry(workdir: Path) -> ToolRegistry:
    store = AsOfStore(":memory:")
    seed_store(store, seed=7)
    write_synthetic_feed(workdir / "drop")
    DropFeedIngester(store, LocalDropSource(workdir / "drop"), workdir / "quarantine",
                     clock=lambda: FIXED_NOW, replay=True).run()
    registry = ToolRegistry(store, clock=lambda: FIXED_NOW)
    if registry.failed_plugins or not {"forecast_series", "detect_regime"} <= set(registry.tools):
        raise SystemExit("model packages are not installed: pip install -e "
                         "packages/aa-model-ar1 -e packages/aa-model-regime")
    return registry


def make_agent(kind: str, registry: ToolRegistry, provider: Provider, knowledge: Any) -> Any:
    if kind == "graph":
        from analytics_assistant.graph_agent import GraphAgent

        return GraphAgent(registry, provider, knowledge, max_steps=4, clock=lambda: FIXED_NOW)
    return Agent(registry, provider, knowledge, max_steps=4, clock=lambda: FIXED_NOW)


def run_question(registry: ToolRegistry, provider: Provider, knowledge: Any,
                 q: dict[str, Any], agent_kind: str = "loop") -> Outcome:
    agent = make_agent(agent_kind, registry, provider, knowledge)
    events = list(agent.run(q["question"], thread_id=q["id"]))
    calls = [e["data"]["name"] for e in events if e["type"] == "tool.called"]
    results = [e["data"]["envelope"] for e in events if e["type"] == "tool.result"]
    answers = [e["data"]["content"] for e in events if e["type"] == "message.completed"]
    answer = answers[-1] if answers else ""
    if not calls:
        route = "refuse"
    elif calls[0] == KNOWLEDGE_TOOL:
        route = "documents"
    else:
        route = calls[0]
    source = None
    if route == "documents" and results and results[0].get("ok") \
            and results[0]["payload"]["result"]:
        source = results[0]["payload"]["result"][0]["source"]
    code = None
    if results:
        first = results[0]
        code = "OK" if first.get("ok") else first["payload"]["error"]["code"]
    return Outcome(
        id=q["id"], expect_route=q["expect_route"], route=route,
        expect_code=q.get("expect_code"), code=code,
        expect_source=q.get("expect_source"), source=source,
        ungrounded=ungrounded_numbers(answer, results),
        completed=bool(events) and events[-1]["type"] == "run.finished" and bool(answer),
        answer=answer,
    )


def print_table(outcomes: list[Outcome]) -> None:
    head = f"{'id':28} {'route (expected -> actual)':44} {'code/source (expected -> actual)':48} " \
           f"{'grounded':9} result"
    print(head)
    print("-" * len(head))
    for o in outcomes:
        route = f"{o.expect_route} -> {o.route}"
        code = f"{o.expect_code or '-'} -> {o.code or '-'}"
        if o.expect_source:
            code = f"{o.expect_source.split('#')[-1]} -> {(o.source or '-').split('#')[-1]}"
        grounded = "yes" if not o.ungrounded else "NO " + ",".join(o.ungrounded)
        if not o.completed:
            grounded += " (incomplete)"
        print(f"{o.id:28} {route:44} {code:48} {grounded:9} {'PASS' if o.passed else 'FAIL'}")


def run_question_suite(path: str, threshold: float | None, live: bool, verbose: bool,
                       agent_kind: str) -> bool:
    spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    questions = spec["questions"]
    threshold = threshold if threshold is not None else float(spec.get("threshold", 1.0))
    ids = [q["id"] for q in questions]
    if len(ids) != len(set(ids)):
        raise SystemExit("question ids must be unique")

    logging.getLogger("analytics_assistant").setLevel(logging.ERROR)  # the demo quarantine
    knowledge = build_knowledge()
    with tempfile.TemporaryDirectory() as tmp:
        registry = build_registry(Path(tmp))
        outcomes = []
        for q in questions:
            provider = build_provider(Settings.from_env()) if live else ScriptedProvider()
            outcomes.append(run_question(registry, provider, knowledge, q, agent_kind))

    print(f"== questions ({agent_kind} agent)")
    print_table(outcomes)
    if verbose:
        for o in outcomes:
            print(f"\n[{o.id}] {o.answer}")
    passed = sum(o.passed for o in outcomes)
    rate = passed / len(outcomes)
    verdict = "OK" if rate >= threshold else "BELOW THRESHOLD"
    print(f"\npassed {passed}/{len(outcomes)} ({rate:.1%}); threshold {threshold:.0%}: {verdict}")
    if rate < threshold:
        failed = [o.id for o in outcomes if not o.passed]
        print("failed: " + json.dumps(failed))
        return False
    return True


# ---------------------------------------------------------------------- router suite


def route_report(labels: list[dict[str, Any]], predicted: list[str]) -> dict[str, Any]:
    """Per-route precision, recall and accuracy (recall = accuracy on that route's questions)."""
    gold = [item["route"] for item in labels]
    per_route = {}
    for r in ROUTES:
        tp = sum(1 for g, p in zip(gold, predicted) if g == r and p == r)
        n_gold = sum(1 for g in gold if g == r)
        n_pred = sum(1 for p in predicted if p == r)
        per_route[r] = {"n": n_gold, "correct": tp,
                        "recall": tp / n_gold if n_gold else 0.0,
                        "precision": tp / n_pred if n_pred else 0.0}
    correct = sum(g == p for g, p in zip(gold, predicted))
    return {"accuracy": correct / len(gold), "correct": correct, "n": len(gold),
            "per_route": per_route}


def run_router_suite(path: str, verbose: bool) -> bool:
    spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    labels = spec["labels"]
    threshold = float(spec.get("threshold", 0.85))
    for item in labels:
        if item["route"] not in ROUTES:
            raise SystemExit(f"bad route label {item['route']!r} for {item['q']!r}")
    print("== router")
    reports = {}
    for name, router in (("rules only", build_router(use_classifier=False)),
                         ("rules + classifier", build_router(use_classifier=True))):
        decisions = [router.decide(item["q"]) for item in labels]
        report = route_report(labels, [d.route for d in decisions])
        reports[name] = report
        print(f"\n{name}: accuracy {report['correct']}/{report['n']} ({report['accuracy']:.1%})")
        print(f"  {'route':10} {'n':>3} {'correct':>8} {'recall':>8} {'precision':>10}")
        for r, row in report["per_route"].items():
            print(f"  {r:10} {row['n']:>3} {row['correct']:>8} {row['recall']:>8.1%} "
                  f"{row['precision']:>10.1%}")
        if verbose or name == "rules + classifier":
            for item, d in zip(labels, decisions):
                if d.route != item["route"]:
                    print(f"  miss: {item['q']!r} expected {item['route']}, got {d.route} "
                          f"({d.source})")
    final = reports["rules + classifier"]["accuracy"]
    verdict = "OK" if final >= threshold else "BELOW THRESHOLD"
    print(f"\nrouter accuracy {final:.1%}; threshold {threshold:.0%}: {verdict}")
    return final >= threshold


# ---------------------------------------------------------------------- retrieval suite


def retrieval_report(rag: Any, queries: list[dict[str, Any]], ks: list[int]) -> dict[str, Any]:
    kmax = max(ks)
    answerable = [q for q in queries if q["relevant"]]
    unanswerable = [q for q in queries if not q["relevant"]]
    recall = dict.fromkeys(ks, 0)
    rr_total = 0.0
    rows = []
    for q in answerable:
        sources = [h["source"] for h in rag.search(q["q"], k=kmax)]
        rank = next((i + 1 for i, s in enumerate(sources) if s in q["relevant"]), None)
        for k in ks:
            recall[k] += rank is not None and rank <= k
        rr_total += 1.0 / rank if rank else 0.0
        rows.append((q["q"], q["relevant"], sources, rank))
    abstained = 0
    for q in unanswerable:
        hits = rag.search(q["q"], k=kmax)
        abstained += not hits
        rows.append((q["q"], [], [h["source"] for h in hits], None))
    return {
        "recall": {k: recall[k] / len(answerable) for k in ks} if answerable else {},
        "mrr": rr_total / len(answerable) if answerable else 0.0,
        "abstain": abstained / len(unanswerable) if unanswerable else 1.0,
        "n_answerable": len(answerable), "n_unanswerable": len(unanswerable), "rows": rows,
    }


def run_retrieval_suite(path: str, verbose: bool) -> bool:
    spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    ks = [int(k) for k in spec.get("k", [1, 3])]
    thresholds = spec.get("thresholds", {})
    report = retrieval_report(build_knowledge(), spec["queries"], ks)
    print("== retrieval")
    for q, relevant, sources, rank in report["rows"]:
        if relevant:
            status = f"rank {rank}" if rank else "MISS"
        else:
            status = "abstained" if not sources else "ANSWERED"
        if verbose or status in ("MISS", "ANSWERED") or (rank or 0) > 1:
            print(f"  {status:10} {q!r} -> {sources[:3]}")
    recall_line = ", ".join(f"recall@{k} {report['recall'][k]:.1%}" for k in ks)
    print(f"\n{recall_line}, MRR {report['mrr']:.3f} over {report['n_answerable']} queries; "
          f"abstained on {report['abstain']:.0%} of {report['n_unanswerable']} unanswerable")
    ok = True
    if "recall_at_3" in thresholds and 3 in report["recall"]:
        ok &= report["recall"][3] >= float(thresholds["recall_at_3"])
    if "abstain" in thresholds:
        ok &= report["abstain"] >= float(thresholds["abstain"])
    print(f"retrieval thresholds {thresholds}: {'OK' if ok else 'BELOW THRESHOLD'}")
    return ok


# ---------------------------------------------------------------------- entry point


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", choices=["all", "questions", "router", "retrieval"],
                        default="all")
    parser.add_argument("--questions", default=str(HERE / "questions.yaml"))
    parser.add_argument("--router-labels", default=str(HERE / "router_labels.yaml"))
    parser.add_argument("--retrieval-labels", default=str(HERE / "retrieval_labels.yaml"))
    parser.add_argument("--threshold", type=float, default=None,
                        help="pass-rate threshold for the question set")
    parser.add_argument("--agent", choices=["loop", "graph"], default="loop")
    parser.add_argument("--live", action="store_true",
                        help="use the LLM from AA_LLM_* settings instead of the offline one")
    parser.add_argument("--verbose", action="store_true", help="print every answer")
    ns = parser.parse_args(argv)

    results = {}
    if ns.suite in ("all", "questions"):
        results["questions"] = run_question_suite(ns.questions, ns.threshold, ns.live,
                                                  ns.verbose, ns.agent)
    if ns.suite in ("all", "router"):
        print()
        results["router"] = run_router_suite(ns.router_labels, ns.verbose)
    if ns.suite in ("all", "retrieval"):
        print()
        results["retrieval"] = run_retrieval_suite(ns.retrieval_labels, ns.verbose)
    print("\nsummary: " + ", ".join(f"{k} {'OK' if v else 'FAIL'}" for k, v in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
