# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-11-15

### Added

- `web/`: React + TypeScript + Vite chat front end. Streams agent events,
  shows every tool call with its arguments and result, a provenance panel
  for every number, and conversations (create, list, resume). Vitest suite
  with mocked fetch.
- LangGraph agent option (`AA_AGENT=graph`): router, tool, retrieval and
  answer nodes over the same tool registry; Postgres checkpoints with
  `AA_DATABASE_URL`, SQLite with `AA_STATE_DB`, memory in tests.
- Conversation API: `GET/POST /api/threads`, `GET /api/threads/<id>`;
  loop-agent threads are stored in SQLite and survive restarts.
- Intent router (rules, optional naive Bayes classifier, numeric questions
  prefer tools) and `evals/router_labels.yaml` with 46 labelled questions.
- RAG service: markdown, text and PDF-text ingestion, overlapping chunks,
  pluggable embedder, in-memory and pgvector stores, citations, similarity
  floor; `evals/retrieval_labels.yaml` and recall@k reporting.
- Vendor-neutral LLM adapter over httpx (OpenAI-compatible and Anthropic
  Messages), retries with backoff, timeouts, failover, normalised tool
  calls; `scripts/check_vendor_imports.py` and a test that fail on a vendor
  SDK import outside the adapter.
- OpenTelemetry tracing (console or OTLP), token and cost counters,
  `/api/usage`, JSON logs with request ids and secret redaction.
- MCP streamable HTTP behind the bearer token, with explicit allowed hosts.
- Docker Compose stack: postgres (pgvector), api, mcp, web, nginx TLS proxy;
  `scripts/gen-certs.sh`, `scripts/stack.sh`, `scripts/test-all.sh`, Makefile.
- `seed` command, `/readyz`, `/api/config`, optional `AA_WEB_DIST`.
- Docs: `BUILD_GUIDE.md` (12 steps), `operations.md`, `evaluation.md`,
  updated `architecture.md`, `docs/README.md`, `CONTRIBUTING.md`.
- CI: web build and tests, router and retrieval evals, graph-agent evals,
  vendor-import check, `docker compose config`.

### Changed

- `OpenAICompatibleProvider` is now a thin constructor over the new adapter
  (same arguments); transient failures are retried.
- The offline LLM and the router share one set of rule patterns.
- Knowledge search uses the RAG service with the packaged corpus
  (`corpus/*.md`); hits carry `doc_id`, `section` and a citation.
- The hashing embedder also keeps identifiers such as `SNAPSHOT_UNAVAILABLE`
  whole, which fixed two retrieval misses.

## [0.2.0]

### Added

- Tool registry served over MCP, a REST gateway and an in-process agent
  loop with SSE events; point-in-time store; model packages via entry
  points; vendor drop-feed ingester; keyword-retrieval knowledge;
  preference memory; offline scripted LLM and an OpenAI-compatible client;
  Excel client; question-set evals.
