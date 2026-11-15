# AI chatbot from scratch: an analytics assistant

[![CI](https://github.com/Avinash186200/ai-chatbot-from-scratch/actions/workflows/ci.yml/badge.svg)](https://github.com/Avinash186200/ai-chatbot-from-scratch/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Node 20+](https://img.shields.io/badge/node-20%2B-green)
![Runs offline](https://img.shields.io/badge/tests-offline%2C%20no%20API%20keys-brightgreen)

A complete, working reference for building an AI analytics chatbot: a chat
UI, an Excel client and MCP agents ask questions; an agent routes each one
to model tools, to documents or to a polite refusal; every number in an
answer comes from a tool result with its model, version, as-of date and
data snapshot. It runs end to end on a laptop with no API key. A
TLS-fronted Docker stack with Postgres and pgvector is included; its
configuration is validated in CI, but a full build-and-run has not yet been
completed (see [Status and limits](#status-and-limits)).

**[docs/BUILD_GUIDE.md](docs/BUILD_GUIDE.md) walks through building it from
an empty folder in 12 steps.**

I led the build of a production analytics platform for a financial-research
team and owned the Excel integration and the model-serving path. That code,
its models and its data are proprietary and are not here: this repository
re-implements the architecture from scratch on synthetic data, from general
knowledge and public documentation. The models are placeholders and **the
numbers they produce mean nothing**; the structure is the point.

## What it does

| | |
|---|---|
| **Ask** | "Forecast RATE_LONG 5 days ahead as of 2023-06-30" in the web chat, `=AA.FORECAST("RATE_LONG", 5, "2023-06-30")` in Excel, or an MCP tool call |
| **Route** | Rules, then a small classifier: numbers go to model tools, explanations to documents, the rest is refused; numeric questions prefer tools when unsure |
| **Answer** | Tools read a point-in-time store (no lookahead); documents come back with a citation (doc and section) or "I don't know" below a similarity floor |
| **Show** | The web UI streams each tool call with its arguments and result, and a provenance panel lists model, version, as_of and snapshot id for every number |
| **Run** | The default in-process agent loop, or a LangGraph state graph with Postgres/SQLite checkpoints (`AA_AGENT=graph`) so conversations survive restarts |
| **Measure** | Eval suites for the end-to-end question set, per-route router accuracy and retrieval recall@k, all in CI |

## Architecture

```
 browser ─┐                        ┌─► /api/chat ──► Agent (loop | LangGraph) ──► LLM adapter ──► any LLM
 Excel ───┼─► nginx (TLS) ─────────┤      router ─┬─ tool ──────► ToolRegistry ──► model packages ──► AsOfStore
 MCP ─────┘   / /api /gateway /mcp ├─► /gateway ──┘─ retrieval ─► RAG (pgvector | memory), citations    ▲
                                   └─► /mcp ──────────────────────► ToolRegistry          vendor drop-feed ingester
```

- **One registry, three doors.** MCP, the REST gateway and the agent all
  call `ToolRegistry.call`: same validation, same `as_of` resolution, same
  envelope `{ok, payload, provenance}` with stable error codes.
- **Point-in-time data.** Observations carry the date they describe and the
  date they were published; a revision is a new row; `snapshot_id` hashes
  what was known.
- **Vendor-neutral LLM adapter.** One `httpx` client for OpenAI-compatible
  chat and the Anthropic Messages API, with retries, backoff, timeouts,
  failover and one internal tool-call shape. No vendor SDK is imported
  anywhere (a CI check enforces it).
- **Retrieval.** Markdown and PDF-text ingestion, overlapping chunks, a
  pluggable embedder, an in-memory or pgvector store, citations and a floor.
- **Observability.** OpenTelemetry spans around agent runs, tool calls and
  LLM calls; token and cost counters; JSON logs with request ids; tokens
  never logged.

Design decisions and the alternatives considered are in
[docs/architecture.md](docs/architecture.md).

## Quick start 1: laptop, offline

Python 3.11+ and Node 20+. No API key.

```bash
python -m venv .venv && source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e "server[dev,mcp,graph,otel,pdf]" -e packages/aa-model-ar1 -e packages/aa-model-regime
(cd server && pytest -q)                                      # offline; a fixture blocks the network

export AA_GATEWAY_TOKEN=change-me-local-token
python -m analytics_assistant serve                           # API on http://localhost:8000
```

In a second terminal:

```bash
cd web && npm ci && npm run dev                               # UI on http://localhost:5173
```

Open http://localhost:5173, paste the token and try:

- `Forecast RATE_LONG 5 days ahead as of 2023-06-30`
- `What regime is POLICY_RATE in as of 2023-06-30?`
- `What is a snapshot id?`  (answered from the documents, with a citation)
- `Should I buy bonds today?`  (refused)

`AA_AGENT=graph` switches to the LangGraph agent. To use a real model:

```bash
export AA_LLM_PROVIDER=openai_compatible AA_LLM_BASE_URL=http://localhost:11434/v1 AA_LLM_MODEL=your-model
# or: AA_LLM_PROVIDER=anthropic AA_LLM_BASE_URL=<messages api base> AA_LLM_MODEL=... AA_LLM_API_KEY=...
```

## Quick start 2: full Docker stack

```bash
cp .env.example .env              # set AA_GATEWAY_TOKEN and POSTGRES_PASSWORD
make up                           # or: sh scripts/stack.sh up   (generates a self-signed cert)
make seed                         # synthetic vendor feed + documents
```

Open https://localhost:8443 (accept the self-signed certificate). Services:
`postgres` (pgvector), `api`, `mcp` (streamable HTTP at `/mcp`), `web`
(static build behind nginx) and `nginx` (TLS reverse proxy, the only
published port, bound to 127.0.0.1). `make down` stops it. Details:
[docs/operations.md](docs/operations.md).

## Quick start 3: Excel client

```bash
cd excel-client
npm ci && npm test && npm run build
npx office-addin-dev-certs install                            # once: trusted https://localhost cert
npm run serve                                                 # https://localhost:3000
```

Sideload `excel-client/manifest.xml` (Microsoft's "sideload Office Add-ins"
guide has the steps), open **Assistant settings** on the Home tab, set the
base URL (`http://localhost:8000` locally, `https://localhost:8443` for the
Docker stack) and the token, then:

```
=AA.TOOLS()
=AA.FORECAST("RATE_LONG", 5, DATE(2023,6,30))
=AA.REGIME("POLICY_RATE", "2023-06-30")
```

## MCP

```bash
python -m analytics_assistant mcp-stdio                       # for a local MCP client
python -m analytics_assistant mcp-http                        # http://127.0.0.1:8001/mcp, bearer token required
```

## Tests and evals

```bash
sh scripts/test-all.sh            # everything CI runs, offline
python evals/run_evals.py         # question set, router, retrieval
```

| Suite | What it checks | Current (offline) |
|---|---|---|
| Server (pytest) | store, tools, plugins, ingest, HTTP/auth, both agents, conversations, LLM adapter, router, RAG, telemetry, MCP | all pass |
| Questions (30) | route, envelope code or cited section, completion, every number grounded in a tool result | 30/30 loop, 30/30 graph |
| Router (46 labels) | per-route precision and recall, rules only vs rules + classifier | 84.8% vs 95.7% |
| Retrieval (21 queries) | recall@1, recall@3, MRR, abstention when nothing is relevant | 87.5%, 100%, 0.938, 5/5 |
| Web (Vitest) | SSE parser, API client, streaming UI, XSS rendered as text, resume, 401 handling | all pass |
| Excel client (jest) | dates, envelopes, errors, dedup, concurrency, retries, token guard | all pass |

See [docs/evaluation.md](docs/evaluation.md) for what each suite does and
does not prove.

## Repo layout

```
server/src/analytics_assistant/
    config.py  asof_store.py  synthetic.py  tools.py  envelopes.py  plugin_api.py
    gateway.py  mcp_server.py  web.py  conversations.py        HTTP, MCP, conversations
    agent.py  graph_agent.py  router.py  memory.py              agents and routing
    llm_types.py  llm_adapter.py  llm.py  offline_llm.py        LLM layer
    rag/  corpus/                                               retrieval and its documents
    telemetry.py                                                tracing, counters, JSON logs
    ingest/                                                     vendor drop feed
server/tests/       pytest suite (offline)
packages/           aa-model-ar1, aa-model-regime: placeholder models, one package each
evals/              questions.yaml, router_labels.yaml, retrieval_labels.yaml, run_evals.py
web/                React + TypeScript + Vite chat UI and its Vitest suite
excel-client/       Office.js add-in (custom functions, task pane) and its jest suite
deploy/             nginx TLS proxy template; certs/ (generated, git-ignored)
scripts/            test-all.sh, stack.sh, gen-certs.sh, check_vendor_imports.py
docs/               BUILD_GUIDE, architecture, operations, evaluation
```

## Status and limits

- Models are placeholders and data is synthetic. No number here means anything.
- One shared bearer token; conversation "users" are labels, not identities.
- The offline LLM is a keyword router: it proves the plumbing, routing,
  refusals and grounding, not language quality. Use `--live` for a real model.
- The default hashing embedder matches vocabulary, not meaning; see the
  retrieval numbers above.
- **Tested offline only.** Every check above runs without a network, a
  database server or an LLM. These paths are written and unit-tested
  against fakes or SQL/parameter checks, but have **not** been run against
  a live service: the Docker stack end to end (`docker compose config` is
  validated in CI; the last `docker compose build` attempts failed on PyPI
  network errors inside the build, so health checks, nginx streaming and
  MCP-through-nginx are unverified), the LangGraph Postgres checkpointer,
  the pgvector store against a real Postgres, the OTLP exporter, and a real
  LLM through `AA_LLM_*` (`--live` evals).
- The Excel manifest is for sideloading and has not been through store
  validation; the Excel client handles the 1900 date system only.

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).
MIT licensed.
