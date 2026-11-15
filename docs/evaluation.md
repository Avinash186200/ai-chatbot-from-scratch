# Evaluation

`evals/run_evals.py` runs three suites. All of them run offline in CI; the
question set can also run against a real model with `--live`.

```bash
python evals/run_evals.py                              # all suites
python evals/run_evals.py --suite questions            # end to end, loop agent
python evals/run_evals.py --suite questions --agent graph
python evals/run_evals.py --suite router
python evals/run_evals.py --suite retrieval
python evals/run_evals.py --verbose                    # every answer, every hit
python evals/run_evals.py --live                       # question set against AA_LLM_*
```

The process exits 1 when any suite is below its threshold.

## 1. Question set (`evals/questions.yaml`)

**What it checks.** Each question runs through the real agent, tool
registry, model packages and point-in-time store (synthetic history plus a
replayed synthetic vendor feed, with a fixed clock). A question passes when:

| Check | Meaning |
|---|---|
| route | the first tool called (or `documents` for retrieval, `refuse` for no tool) is the expected one |
| code | the first tool result is `OK` or the expected error code (`SNAPSHOT_UNAVAILABLE`, `SCHEMA_VIOLATION`, `UNSUPPORTED_HORIZON`) |
| source | for documents, the top hit is the expected `doc#section` |
| grounded | every number in the answer appears in a tool result (dates and versions must match exactly) |
| completed | the run ends with an answer, not a failure or a step-budget stop |

The 30 questions cover forecasts, regimes, raw series, the catalog and
health, documents (including one that nothing in the corpus answers, where
the expected answer is "I don't know"), and refusals (advice, prompt
injection, a request to guess, off-topic, unroutable). Threshold: 90%.

**Current result (offline).** 30/30 with the loop agent and 30/30 with the
LangGraph agent.

**What it does not check.** Forecast quality (the models are placeholders on
synthetic data), the wording of answers, or how a real LLM phrases things.
The offline LLM is a deterministic keyword router, so a 100% offline score
says the plumbing, routing, refusals and grounding rule work; run `--live`
to measure a real model on the same set.

## 2. Router (`evals/router_labels.yaml`)

**What it checks.** The intent router alone, on 46 labelled questions
(20 tool, 14 documents, 12 refuse). About a third are phrased so that no
rule fires, to exercise the classifier and the numeric fallback. The
classifier trains on `router.TRAINING_EXAMPLES`, never on this file; a test
checks the two sets do not overlap. Threshold: 85% accuracy for rules +
classifier.

**Current result.**

| | accuracy | tool recall | documents recall | refuse recall |
|---|---|---|---|---|
| rules only | 39/46 (84.8%) | 95.0% | 57.1% | 100.0% |
| rules + classifier | 44/46 (95.7%) | 100.0% | 85.7% | 100.0% |

The two remaining misses are rule false positives, kept visible on purpose:
"Tell me about the revision policy for restated values" (the word "values"
fires the `query_series` rule) and "Describe the approach used for regime
labels" ("regime" fires `detect_regime`). Rules are final by design, so the
classifier cannot overrule them. The fix, when it matters, is a narrower
rule, not a lower threshold.

## 3. Retrieval (`evals/retrieval_labels.yaml`)

**What it checks.** The RAG service over the packaged corpus
(`server/src/analytics_assistant/corpus/*.md`): 16 queries with labelled
relevant sections, and 5 queries that nothing in the corpus answers.

| Metric | Meaning |
|---|---|
| recall@k | share of answerable queries with a relevant section in the top k |
| MRR | mean of 1 / rank of the first relevant hit |
| abstain | share of unanswerable queries that return no hit above the floor |

Thresholds: recall@3 at least 85%, abstention at least 80%.

**Current result (hashing embedder).** recall@1 87.5%, recall@3 100%,
MRR 0.938, abstained on 5 of 5 unanswerable queries. The two queries not
ranked first are "What does SNAPSHOT_UNAVAILABLE mean?" and "What does
MODEL_FAILURE mean?", where the snapshot-id and model-versions sections
share vocabulary with the error-codes section. A semantic embedder
(`AA_EMBEDDER=sentence-transformers`) is the expected fix; it is not run in
CI because it downloads model weights.

## Grounding rule, in detail

`ungrounded_numbers(answer, tool_results)` extracts numbers from the answer
(integers, decimals, negatives and compound tokens such as `2023-06-30` or
`1.1.0`; digits inside identifiers like `snap-1a2b` or `lower_90` are not
numbers) and checks each against every leaf of every tool result. A plain
number may match in another spelling (`5` and `5.0`); a compound token
must match exactly. A refusal must therefore contain no number at all.

## Adding cases

- A new question: add it to `questions.yaml` with `expect_route` and either
  `expect_code` or `expect_source`. Ids must be unique.
- A new router label: add `{q, route}` to `router_labels.yaml`. If you add
  a training example to `router.py`, do not copy an eval question.
- A new retrieval label: add `{q, relevant: ["doc.md#section"]}`, or
  `relevant: []` for a query the corpus must not answer.

Run `pytest -q evals` after editing: it checks the harness itself (the
grounding rule, per-route math, recall math and threshold exit codes).
