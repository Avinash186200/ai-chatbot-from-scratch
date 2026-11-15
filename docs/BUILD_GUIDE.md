# Build an AI analytics chatbot from scratch in 12 steps

This guide rebuilds this repository from an empty folder. Each step adds one
layer, says why it exists, names the files, shows how to run and test it, and
lists the mistakes that are easy to make at that layer. Every step runs
offline: no cloud account and no API key until you choose to plug in a real
model.

The finished system answers questions such as "Forecast RATE_LONG 5 days
ahead as of 2023-06-30" with numbers that come from model tools, never from
the language model, and it cites where each number came from.

| Step | You build | Main files |
|---|---|---|
| 1 | Repo skeleton, settings, a test guard | `server/pyproject.toml`, `config.py`, `tests/conftest.py` |
| 2 | Point-in-time data store | `asof_store.py`, `synthetic.py` |
| 3 | Tool registry, envelope, model packages | `tools.py`, `envelopes.py`, `plugin_api.py`, `packages/` |
| 4 | REST gateway and MCP server | `gateway.py`, `mcp_server.py`, `web.py` |
| 5 | Data ingestion from a vendor drop | `ingest/sftp_feed.py` |
| 6 | Vendor-neutral LLM adapter | `llm_types.py`, `llm_adapter.py`, `llm.py`, `offline_llm.py` |
| 7 | Agent loop with streamed events | `agent.py`, `web.py` (`/api/chat`) |
| 8 | Retrieval (RAG) with citations | `rag/`, `corpus/` |
| 9 | Intent router and a LangGraph agent | `router.py`, `graph_agent.py`, `conversations.py` |
| 10 | Evaluation | `evals/` |
| 11 | Front ends: web chat and Excel | `web/`, `excel-client/` |
| 12 | Observability, Docker stack, CI | `telemetry.py`, `docker-compose.yml`, `deploy/`, `.github/` |

Prerequisites: Python 3.11+, Node 20+, Git. Docker only for step 12.

---

## Step 1. Skeleton, settings and a test guard

**What you build.** A Python package (`server/src/analytics_assistant/`) with
a `pyproject.toml`, a `Settings` dataclass read from environment variables,
and a pytest fixture that fails any test that opens a network connection.

**Why.** Settings from the environment keep secrets out of code and let the
same build run on a laptop, in CI and in a container. The network guard
makes "the tests run offline" a property the suite enforces, not a hope:
the first time someone adds a test that calls a real API, it fails.

**Files.** `server/pyproject.toml` (core dependencies: `starlette`,
`uvicorn`, `httpx`; everything heavier is an extra), `config.py`,
`server/tests/conftest.py` (`no_network` fixture), `.env.example`,
`.gitignore`.

**Run and test.**

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e "server[dev]"
(cd server && pytest -q)
```

**Common mistakes.**

- Giving a secret a default value ("dev-token"). A forgotten variable then
  ships an open server. Here an empty token means every request is refused.
- Printing settings in logs. `Settings` marks the token, keys and database
  URL `repr=False`, and a test checks they do not appear in `repr()`.
- Letting dataclass defaults point at real files: tests then write
  databases into the working folder. Dataclass defaults here are in-memory;
  `from_env()` defaults are the laptop choices.

## Step 2. A point-in-time data store

**What you build.** `AsOfStore` on SQLite, holding
`(series, obs_date, published_at, value)`. A revision is a new row. A read
"as of D" returns, per observation date, the latest row published on or
before D. A `snapshot_id` hashes exactly what was known.
`synthetic.py` generates generic series (`RATE_LONG`, `POLICY_RATE`,
`VOL_INDEX`) with publication lag and revisions.

**Why.** An analytics answer about a past date must only use data that was
available then. Without publication dates every historical answer silently
uses final revised values, which is lookahead. The snapshot id makes answers
reproducible and cacheable: equal ids mean equal inputs.

**Files.** `asof_store.py`, `synthetic.py`, `tests/test_asof_store.py`.

**Run and test.** `pytest -q server/tests/test_asof_store.py`. The tests
use a tiny hand-made store with one revision so every assertion is exact.

**Common mistakes.**

- Overwriting a value when a vendor restates it. You lose the first release,
  which is what a point-in-time answer needs.
- Using the observation date as the publication date. That is the most
  common source of lookahead.
- Hashing the query instead of the data for the snapshot id. Two queries
  that see the same data must get the same id.

## Step 3. A tool registry, an envelope, and models as packages

**What you build.** `ToolRegistry.call(name, args)`: validate arguments
against a JSON schema, resolve `as_of` (default: latest publication, never
in the future), run the tool, and wrap the result in an envelope
`{ok, payload: {result | error: {code, message}}, provenance, notes}`.
Each model is its own installable package (`packages/aa-model-ar1`,
`packages/aa-model-regime`) registered under the
`analytics_assistant.models` entry-point group.

**Why.** One entry point means MCP, REST and the agent get identical
validation and identical answers. Refusals with stable codes
(`SNAPSHOT_UNAVAILABLE`, `SCHEMA_VIOLATION`, `UNSUPPORTED_HORIZON`,
`MODEL_FAILURE`, `INTERNAL`) let the agent say "no data was published by
then" instead of guessing, and let Excel show `#N/A`. Model packages keep
heavy, conflicting dependencies out of the server, and a broken model
removes only its own tools.

**Files.** `tools.py`, `envelopes.py`, `plugin_api.py`,
`packages/*/pyproject.toml` (the entry point), `tests/test_tools.py`,
`tests/test_plugins.py`.

**Run and test.**

```bash
pip install -e packages/aa-model-ar1 -e packages/aa-model-regime
python -m analytics_assistant call forecast_series '{"horizon": 3, "as_of": "2023-06-30"}'
python -m analytics_assistant call health          # packages, versions, failures
pytest -q packages/aa-model-ar1 packages/aa-model-regime
```

**Common mistakes.**

- Raising exceptions for domain refusals. HTTP 500 hides the difference
  between "your input is wrong" and "the server broke".
- Letting clients match on error message text. Codes are the contract.
- Writing the model version by hand in provenance. The registry stamps the
  installed package's version, so it cannot drift.

## Step 4. Serve the tools: REST gateway and MCP

**What you build.** `POST /gateway/<tool>` and `GET /gateway/openapi.json`
behind a bearer token, and an MCP server exposing every registry tool over
stdio and streamable HTTP (`/mcp`, behind the same bearer check).

**Why.** Plain HTTP clients (Excel, curl, scripts) and agent clients (MCP)
reach the same registry. Bearer auth with constant-time comparison is the
minimum for anything that leaves the laptop.

**Files.** `gateway.py`, `mcp_server.py`, `web.py`,
`tests/test_http.py`, `tests/test_mcp.py`.

**Run and test.**

```bash
pip install -e "server[mcp]"
export AA_GATEWAY_TOKEN=change-me-local-token
python -m analytics_assistant serve        # REST + chat on :8000
python -m analytics_assistant mcp-http     # MCP on :8001/mcp
curl -s -H "Authorization: Bearer $AA_GATEWAY_TOKEN" -X POST \
     localhost:8000/gateway/forecast_series -d '{"horizon": 2}'
```

**Common mistakes.**

- Serving MCP over HTTP without auth because "it is only for agents".
- Turning off the MCP SDK's DNS-rebinding protection to get past an
  "Invalid Host header" error. Add the host to `AA_MCP_ALLOWED_HOSTS`
  instead.
- Echoing the `Authorization` header in an error body or a log line.

## Step 5. Data ingestion from a vendor drop

**What you build.** `DropFeedIngester`: reads a vendor's daily CSV drop
(SFTP in practice, a local folder in tests), publishes each file on the day
it arrived (never earlier than the load date), appends revisions, records a
SHA-256 per file so re-runs are no-ops, and quarantines malformed files with
a reason.

**Why.** The date inside a vendor file says what a value is about, not when
you could have known it. Arrival-based publication keeps step 2's guarantee
true for real feeds.

**Files.** `ingest/sftp_feed.py`, `ingest/synthetic_feed.py`,
`tests/test_ingest.py`.

**Run and test.**

```bash
python -m analytics_assistant generate-feed --out ./drop
python -m analytics_assistant ingest --drop ./drop --quarantine ./quarantine --replay
python -m analytics_assistant seed      # history + replayed feed + documents, idempotent
```

**Common mistakes.** Trusting the vendor's timestamp; retrying a quarantined
file forever; letting a late original overwrite a newer restatement.

## Step 6. A vendor-neutral LLM adapter

**What you build.** One internal shape (`llm_types.py`: `Completion`,
`ToolCall(id, name, arguments: dict)`, `Usage`), and one `httpx` adapter
(`llm_adapter.HTTPChatProvider`) that speaks both the OpenAI-compatible
`/chat/completions` format and the Anthropic Messages `/v1/messages`
format. It retries 408/409/425/429/500/502/503/504/529, timeouts and network errors
with exponential backoff and full jitter, honours `Retry-After`, never
retries other 4xx, and `FailoverProvider` moves to a second configured
provider when the first is exhausted. `ScriptedProvider` and the keyword
router in `offline_llm.py` stand in for a model offline.

**Why.** The agent should not know which vendor is on the other end.
Plain HTTP keeps dependencies small and lets tests mock the transport with
`httpx.MockTransport`. A script (`scripts/check_vendor_imports.py`) and a
test fail if any module imports a vendor SDK outside the adapter.

**Files.** `llm_types.py`, `llm_adapter.py`, `llm.py`, `offline_llm.py`,
`tests/test_llm_adapter.py`, `scripts/check_vendor_imports.py`.

**Run and test.**

```bash
pytest -q server/tests/test_llm_adapter.py
python scripts/check_vendor_imports.py
# Optional, real model (any OpenAI-compatible server, or the Messages API):
export AA_LLM_PROVIDER=openai_compatible AA_LLM_BASE_URL=http://localhost:11434/v1 AA_LLM_MODEL=your-model
```

**Common mistakes.**

- Retrying a 400. It will fail the same way, slower.
- Putting the response body in the error message. Some proxies echo request
  headers, including the key.
- Forgetting that the Messages format wants alternating roles: consecutive
  tool results must merge into one user turn (`to_anthropic` does this).
- Real `sleep` in retry tests. Inject the sleep function.

## Step 7. The agent loop and streamed events

**What you build.** `Agent.run(message, user, thread_id)`: send the
history and the tool catalog to the LLM, execute each tool call through the
registry, feed the envelope back, stop on a text answer or when the step
budget runs out. Every step is an event
`{v, seq, run_id, thread_id, ts, type, data}` streamed over Server-Sent
Events from `POST /api/chat`.

**Why.** Streaming lets a UI show progress and each tool call as it
happens. Typed events with a sequence number can be replayed and tested.
The system prompt says numbers come only from tool results, and the evals
in step 10 check it.

**Files.** `agent.py`, `web.py`, `memory.py`, `tests/test_agent_llm.py`.

**Run and test.**

```bash
curl -N -H "Authorization: Bearer $AA_GATEWAY_TOKEN" -H "Content-Type: application/json" \
     -d '{"message": "Forecast RATE_LONG 5 days ahead as of 2023-06-30"}' localhost:8000/api/chat
```

**Common mistakes.** No step budget (a model that keeps calling tools runs
forever); unbounded tool results in the prompt (truncate them); storing
market levels in "memory" (memory here refuses anything that looks like a
dated level).

## Step 8. Retrieval with citations and "I don't know"

**What you build.** `rag/`: load markdown (one section per heading, with
optional `{#id}`), plain text and PDF text; split sections into overlapping
word windows; embed with a pluggable embedder (a dependency-free hashing
embedder by default, sentence-transformers optional); store vectors behind
a `VectorStore` interface (in memory by default, pgvector when
`AA_DATABASE_URL` is set). Each hit carries a citation
`{doc_id, section, chunk}`. Below a similarity floor (`AA_RAG_MIN_SCORE`)
the search returns nothing and the agent says it does not know.

**Why.** Definitions and methodology belong in documents, not in a model's
memory. Sections make citations meaningful. The floor stops the agent from
answering from the least-bad match.

**Files.** `rag/documents.py`, `rag/chunking.py`, `rag/embedders.py`,
`rag/store.py`, `rag/service.py`, `corpus/*.md`, `tests/test_rag.py`.

**Run and test.** `pytest -q server/tests/test_rag.py` and
`python evals/run_evals.py --suite retrieval` (recall@k, step 10).
Add your own documents with `AA_DOCS_DIR=/path/to/docs`.

**Common mistakes.**

- No overlap between chunks: a sentence cut at a boundary is lost.
- Embedding the chunk without its heading: the heading is often the only
  place the topic is named.
- Splitting identifiers into words (`SNAPSHOT_UNAVAILABLE` becomes
  "snapshot" and "unavailable", which match the wrong section). The hashing
  embedder keeps identifiers whole as extra tokens.
- Building SQL for pgvector by string formatting. Values are bound
  parameters; only a validated table name is interpolated.

## Step 9. An intent router and a LangGraph agent

**What you build.** `router.IntentRouter`: rules first (regular
expressions), then an optional small naive Bayes classifier trained on its
own examples, and when both are unsure a question that looks numeric goes to
the tool route. `graph_agent.GraphAgent`: a LangGraph state graph
`router -> tool | retrieval | answer`, using the same registry, with the
thread's history checkpointed after each node (Postgres with
`AA_DATABASE_URL`, SQLite with `AA_STATE_DB`, memory in tests).
`conversations.py` indexes threads so a UI can list and resume them.
Select the graph with `AA_AGENT=graph`; the loop from step 7 stays the
default.

**Why.** An explicit router makes the three behaviours (compute, explain,
refuse) testable on their own and saves an LLM call for refusals and
retrieval. A graph makes control flow visible and checkpointing gives
durable conversations that survive a restart. Preferring tools on
uncertainty is a deliberate bias: a tool refuses cleanly with a code; a
document answer to a numeric question sounds confident and is wrong.

**Files.** `router.py`, `graph_agent.py`, `conversations.py`,
`tests/test_router.py`, `tests/test_graph_agent.py`,
`tests/test_conversations.py`.

**Run and test.**

```bash
pip install -e "server[graph]"
AA_AGENT=graph python -m analytics_assistant serve
python evals/run_evals.py --suite router
python evals/run_evals.py --suite questions --agent graph
```

**Common mistakes.**

- Training the classifier on the evaluation labels. The training examples
  live in `router.py`; a test checks they do not overlap `router_labels.yaml`.
- Making a span "current" across a generator `yield` (it can resume in
  another thread). `telemetry.RunSpan` activates the span per step instead.
- Accumulating per-run fields in the checkpoint. Only `messages` uses an
  append reducer; route, draft, step count and error are reset each run.

## Step 10. Evaluation

**What you build.** `evals/run_evals.py` with three suites:
**questions** (30 end-to-end questions through the real agent, registry,
models and store: expected route, expected envelope code or cited section,
the run completes, and every number in the answer appears in a tool
result), **router** (46 labelled questions, per-route precision and recall
for rules only and rules + classifier), and **retrieval** (16 labelled
queries plus 5 with no answer: recall@1, recall@3, MRR and abstention).
Each suite has a threshold; CI fails under it.

**Why.** Unit tests check parts. Evals check what a user sees, including
the rule that matters most: no number the tools did not produce.

**Files.** `evals/questions.yaml`, `evals/router_labels.yaml`,
`evals/retrieval_labels.yaml`, `evals/run_evals.py`,
`evals/test_run_evals.py`. See [evaluation.md](evaluation.md).

**Run and test.** `python evals/run_evals.py` (all suites, offline);
`--live` runs the question set against the LLM in `AA_LLM_*`.

**Common mistakes.** Grading the answer text with exact string matches
(brittle; check routes, codes and grounding instead); hiding failures by
lowering the threshold; reporting only overall accuracy when one route is
weak.

## Step 11. Front ends: a web chat and an Excel client

**What you build.** `web/`: React + TypeScript + Vite. It reads the SSE
stream with `fetch` (the chat request is a POST with a bearer token, which
`EventSource` cannot send), shows each tool call with its arguments and
result envelope, keeps a provenance panel (tool, model, version, as_of,
snapshot id) for every result, and lists, creates and resumes
conversations. The token sits in `sessionStorage`. Model text is rendered
as React text nodes only. `excel-client/`: `=AA.FORECAST`, `=AA.REGIME`,
`=AA.TOOLS` custom functions over the REST gateway.

**Why.** Analysts work in a chat window and in spreadsheets; both must show
where a number came from.

**Files.** `web/src/lib/sse.ts`, `web/src/lib/api.ts`,
`web/src/lib/chat.ts`, `web/src/App.tsx`, `web/src/components/*`,
`web/test/*`, `excel-client/`.

**Run and test.**

```bash
cd web && npm ci && npm test && npm run build
cd ../excel-client && npm ci && npm test
cd ../web && npm run dev            # http://localhost:5173, proxies /api to :8000 (keeps running)
```

**Common mistakes.**

- Rendering model output as HTML (markdown-to-HTML, `innerHTML`). A test
  feeds `<img onerror>` and `<script>` and checks they stay text.
- Calling the API cross-origin from the browser. The Vite proxy and nginx
  keep one origin, so the token never goes to another host.
- Parsing SSE by splitting on `"\n\n"` per chunk. Chunks split anywhere,
  including between `\r` and `\n`; the parser test splits at every offset.

## Step 12. Observability, the Docker stack and CI

**What you build.**

- `telemetry.py`: OpenTelemetry spans around agent runs (`agent.run`),
  tool calls (`tool.call`), retrieval and LLM calls (`llm.complete`, with
  attempts and token counts); token and cost counters; JSON logs with a
  request id on every line and a redaction filter for tokens and keys.
- `docker-compose.yml`: `postgres` (pgvector), `api`, `mcp` (streamable
  HTTP), `web` (static build behind nginx) and `nginx` as a TLS reverse
  proxy with a self-signed certificate from `scripts/gen-certs.sh`. Health
  checks gate start-up order; only nginx publishes ports, on 127.0.0.1.
- `Makefile` and `scripts/` (`up`, `down`, `test`, `seed`).
- `.github/workflows/ci.yml`: server, model packages, evals (all suites and
  the graph agent), the vendor-import check, the Excel client, the web
  build and tests, and `docker compose config`.

**Status.** The compose file is validated in CI with `docker compose
config`, but a full build-and-run has not yet completed (the image build
failed on PyPI network errors). Treat this step as untested end to end; see
[operations.md](operations.md#docker-compose-full-stack).

**Why.** You cannot run what you cannot see: traces show which tool or
model call was slow or failed, counters show what it cost, request ids tie
a user's report to log lines. The compose stack is the smallest
production-shaped deployment.

**Run and test.**

```bash
cp .env.example .env              # set AA_GATEWAY_TOKEN and POSTGRES_PASSWORD
make up                           # or: sh scripts/stack.sh up
# browse to https://localhost:8443 and accept the self-signed certificate
make seed && make down
sh scripts/test-all.sh            # everything CI runs, locally
```

**Common mistakes.**

- Putting prompts, tool arguments or tokens on spans. Attributes here are
  an allowlist of ids, names, counts and codes, and a test checks the key,
  the question and the series name are absent.
- Buffering SSE in the proxy (`proxy_buffering off` on `/api/chat`).
- Publishing Postgres or the API on `0.0.0.0`.
- Committing the generated certificate or `.env` (both are git-ignored).

---

Where to go next: [architecture.md](architecture.md) for the design
decisions and alternatives, [operations.md](operations.md) for
configuration and troubleshooting, [evaluation.md](evaluation.md) for the
eval suites and current numbers.
