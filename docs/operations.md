# Operations

Configuration, deployment and troubleshooting. Everything is configured
through environment variables (`.env.example` lists them all with
defaults). Nothing has a secret default.

## Configuration reference

### Core

| Variable | Default (laptop) | Purpose |
|---|---|---|
| `AA_GATEWAY_TOKEN` | empty | Bearer token for `/gateway/*`, `/api/*` and `/mcp`. Empty = every request refused. |
| `AA_HOST`, `AA_PORT` | `127.0.0.1`, `8000` | HTTP bind address for `serve`. |
| `AA_DB_PATH` | `analytics_assistant.db` | SQLite point-in-time store; seeded with synthetic data on first start. |
| `AA_SYNTHETIC_SEED` | `7` | Seed for the synthetic history. |
| `AA_STATE_DB` | `assistant_state.db` | SQLite file for the conversation index, loop-agent history and (without Postgres) graph checkpoints. |
| `AA_DATABASE_URL` | empty | PostgreSQL URL. When set: pgvector store and LangGraph Postgres checkpoints. |
| `AA_WEB_DIST` | empty | Serve a built web UI (`web/dist`) at `/` from the API process. |
| `AA_CORS_ORIGINS` | `https://localhost:3000` | Origins allowed to call the gateway cross-origin (the Excel add-in dev server). The web UI is same-origin and needs none. |

### Agent and LLM

| Variable | Default | Purpose |
|---|---|---|
| `AA_AGENT` | `loop` | `loop` (in-process tool loop) or `graph` (LangGraph state graph). |
| `AA_AGENT_MAX_STEPS` | `6` | Step budget per run. |
| `AA_LLM_PROVIDER` | `scripted` | `scripted` (offline), `openai_compatible` or `anthropic`. |
| `AA_LLM_BASE_URL` | `http://localhost:11434/v1` | Base URL. OpenAI-compatible: the URL before `/chat/completions`. Messages API: the URL before `/v1/messages`. |
| `AA_LLM_MODEL` | empty | Model name; required for HTTP providers. |
| `AA_LLM_API_KEY` | empty | Sent as `Authorization: Bearer` (OpenAI-compatible) or `x-api-key` (Messages). |
| `AA_LLM_TIMEOUT_S` | `60` | Read timeout per attempt (connect timeout 10 s). |
| `AA_LLM_MAX_RETRIES` | `3` | Retries after the first attempt, for retryable failures only. |
| `AA_LLM_FALLBACK_*` | empty | `PROVIDER`, `BASE_URL`, `MODEL`, `API_KEY` of a second provider used only when the first fails. |
| `AA_LLM_PRICE_IN_PER_MTOK`, `AA_LLM_PRICE_OUT_PER_MTOK` | `0` | Your prices per million tokens; feeds the cost counter. |

### Retrieval

| Variable | Default | Purpose |
|---|---|---|
| `AA_EMBEDDER` | `hashing` | `hashing` (offline) or `sentence-transformers` (extra `embeddings`; downloads weights). |
| `AA_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Model for sentence-transformers. |
| `AA_RAG_MIN_SCORE` | `0.12` | Similarity floor; below it a search returns nothing ("I don't know"). Re-tune it when you change the embedder. |
| `AA_DOCS_DIR` | empty | Extra `.md`, `.txt` and `.pdf` files ingested into the `documents` collection at start-up. |

### Observability and MCP

| Variable | Default | Purpose |
|---|---|---|
| `AA_OTEL_EXPORTER` | `console` | `console`, `otlp` (extra `otel`; set `AA_OTEL_ENDPOINT`) or `none`. |
| `AA_OTEL_ENDPOINT` | empty | OTLP/HTTP traces endpoint, e.g. `http://collector:4318/v1/traces`. |
| `AA_LOG_FORMAT` | `json` | `json` or `text`. |
| `AA_LOG_LEVEL` | `INFO` | Root log level. |
| `AA_MCP_PORT` | `8001` | Port for `mcp-http`. |
| `AA_MCP_ALLOWED_HOSTS` | loopback | Host headers the MCP transport accepts (DNS-rebinding protection). Add your public host behind a proxy. |

### Python extras

| Extra | Adds |
|---|---|
| `mcp` | MCP server (stdio and streamable HTTP) |
| `graph` | LangGraph agent, SQLite checkpoints |
| `postgres` | psycopg 3, LangGraph Postgres checkpoints (pgvector needs no Python package) |
| `otel` | OpenTelemetry SDK and OTLP exporter |
| `pdf` | PDF text ingestion |
| `embeddings` | sentence-transformers |
| `dev` | pytest, ruff, PyYAML |

## Deploy

### Laptop, offline

```bash
pip install -e "server[dev,mcp,graph,otel,pdf]" -e packages/aa-model-ar1 -e packages/aa-model-regime
export AA_GATEWAY_TOKEN=change-me-local-token
python -m analytics_assistant serve                 # API on :8000
(cd web && npm ci && npm run dev)                   # UI on :5173, proxies /api to :8000
```

Or build the UI once and let the API serve it:
`(cd web && npm run build) && AA_WEB_DIST=web/dist python -m analytics_assistant serve`.

### Docker Compose (full stack)

> **Status.** `docker-compose.yml` is validated with `docker compose config`
> in CI. A full `docker compose build` and `up` has not yet completed: the
> last attempts failed while pip downloaded packages inside the image build
> (PyPI network errors, e.g. "No matching distribution found for
> langgraph"). The health checks, TLS proxy, SSE streaming through nginx,
> MCP through nginx, the Postgres checkpointer and the pgvector store are
> therefore written and reviewed but not verified live. If you run the stack,
> treat it as a first run and report what breaks.

```bash
cp .env.example .env            # set AA_GATEWAY_TOKEN and POSTGRES_PASSWORD
sh scripts/gen-certs.sh         # self-signed cert for localhost
docker compose up -d --build --wait
docker compose exec api python -m analytics_assistant seed
```

| Service | Image | Role | Health check |
|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | pgvector store and graph checkpoints | `pg_isready` |
| `api` | built from `Dockerfile` | REST gateway, chat SSE, conversations | `GET /readyz` |
| `mcp` | same image, `mcp-http` | MCP over streamable HTTP | `GET /healthz` |
| `web` | built from `web/Dockerfile` | static UI on nginx, sends the CSP | `GET /` |
| `nginx` | `nginx:1.27-alpine` | TLS on `https://localhost:8443`, routes `/`, `/api`, `/gateway`, `/mcp` | `GET /nginx-health` |

Start order is enforced by health checks: postgres, then api (which seeds
the shared SQLite store), then mcp, then nginx. Only nginx publishes ports,
and only on 127.0.0.1. Data lives in the `pg-data` and `assistant-data`
volumes; `docker compose down` keeps them, `docker compose down -v`
deletes them.

The `POSTGRES_PASSWORD` is embedded in a connection URL, so use URL-safe
characters (letters, digits, `-`, `_`), or percent-encode it.

To point the Excel add-in at the stack, use `https://localhost:8443` as its
base URL after trusting the certificate.

### Behind a real domain

- Replace the self-signed certificate with one from your CA (mount it at
  `deploy/certs/localhost.crt` and `.key`, or edit the template).
- Add the public host to `AA_MCP_ALLOWED_HOSTS`.
- Set `AA_OTEL_EXPORTER=otlp` and `AA_OTEL_ENDPOINT` to your collector.
- Rotate `AA_GATEWAY_TOKEN` by changing `.env` and recreating `api` and `mcp`.

## Security model

- One shared bearer token protects every data route. The `user` field on
  conversations is a label chosen by the client, not an identity: anyone
  with the token can read any label's threads. Per-user authentication
  (OIDC in front of nginx, or signed per-user tokens) is the next step for
  a multi-user deployment and is out of scope here.
- Tokens and keys are never logged: the access log records method, path,
  status and duration only; a redaction filter masks bearer tokens,
  `key=value` secrets and the configured token and keys in every log line;
  nginx's log format omits headers; span attributes are an allowlist.
- The web UI renders model output as text, nginx sends a strict
  Content-Security-Policy, and the token stays in `sessionStorage`.
- MCP over HTTP needs the bearer token and keeps DNS-rebinding protection on.

## Observability

- **Logs**: one JSON object per line with `ts`, `level`, `logger`, `msg`,
  `request_id` and, for HTTP, `method`, `path`, `status`, `duration_ms`.
  Every response has an `X-Request-ID` header; send your own (8-64 of
  `[A-Za-z0-9._-]`) to correlate with a client log.
- **Traces**: `agent.run` with children `tool.call`, `retrieval.search`
  and `llm.complete` (attributes: provider, model, attempts, token counts,
  tool name, error code).
- **Usage**: `GET /api/usage` returns calls, input and output tokens and
  cost per provider/model since start; the same numbers go to the
  `aa.llm.tokens` and `aa.llm.cost` OpenTelemetry counters.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Every request returns 401 | `AA_GATEWAY_TOKEN` unset, or the client sends a different one | Set it in `.env` / the environment; the UI's "Sign out" clears a stale token |
| `docker compose up` stops with "set AA_GATEWAY_TOKEN in .env" | No `.env`, or the variable is empty | `cp .env.example .env` and fill it in |
| nginx exits: cannot load certificate | Certificates not generated | `sh scripts/gen-certs.sh` |
| Browser warns about the certificate | Self-signed | Trust `deploy/certs/localhost.crt` locally, or accept the warning for localhost |
| MCP returns 421 "Invalid Host header" | Host not in `AA_MCP_ALLOWED_HOSTS` | Add it; do not turn off DNS-rebinding protection |
| Chat shows nothing until the end | A proxy buffers SSE | `proxy_buffering off` for `/api/chat` (already set in `deploy/nginx`) |
| "stopped after 6 steps" | The model keeps calling tools | Raise `AA_AGENT_MAX_STEPS`, or check the tool keeps refusing with the same code |
| Answers say "I don't know" for covered topics | Floor too high for the embedder | Lower `AA_RAG_MIN_SCORE`, or run `--suite retrieval` and re-tune |
| `AA_AGENT=graph` fails at start with a missing module | Extra not installed | `pip install -e "server[graph]"` (and `[postgres]` with `AA_DATABASE_URL`) |
| `model packages are not installed` from the evals | Packages missing | `pip install -e packages/aa-model-ar1 -e packages/aa-model-regime` |
| `health` lists a failed plugin | A model package failed to import | Its tools are skipped; read the reason in `health`, fix the package; other tools keep serving |
| LLM errors "HTTP 4xx" without retries | Non-retryable client error (bad model name, bad key) | Check `AA_LLM_MODEL`, `AA_LLM_BASE_URL`, `AA_LLM_API_KEY` |
