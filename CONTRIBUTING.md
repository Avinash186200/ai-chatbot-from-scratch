# Contributing

Thanks for taking a look. This is a reference implementation, so changes
are judged on whether they keep it clear, offline-testable and honest about
what it does.

## Ground rules

1. **Everything runs offline.** Tests, evals and builds must pass with no
   network and no API keys. The server test suite has a fixture that fails
   any outbound connection; web tests stub `fetch`.
2. **Numbers come from tools.** The agent must never produce a number that
   is not in a tool result. The question-set evals check it.
3. **No vendor SDKs outside the adapter.** LLM calls go through
   `server/src/analytics_assistant/llm_adapter.py` over httpx.
   `python scripts/check_vendor_imports.py` must report 0.
4. **No secrets in code, logs or telemetry.** Never print a token, key or
   database URL. Span attributes are an allowlist.
5. **Model output is text.** The web UI never renders model output as HTML.
6. **Generic names only.** Synthetic series names (`RATE_LONG`,
   `POLICY_RATE`, ...); no real company, client, product or person names in
   code, data, docs or commit messages.

## Set up

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e "server[dev,mcp,graph,otel,pdf]" -e packages/aa-model-ar1 -e packages/aa-model-regime
(cd web && npm ci)
(cd excel-client && npm ci)
```

## Before you open a pull request

```bash
sh scripts/test-all.sh
```

This runs lint, the vendor-import check, server and model-package tests,
the eval harness tests, every eval suite (including the graph agent), the
Excel client tests and type check, and the web tests and build: the same
checks as CI. If you change routing, retrieval or answers, include the eval
output in the pull request description.

## Style

- Python: `ruff check` clean (config in `ruff.toml`), type hints on public
  functions, docstrings that say why, not what.
- TypeScript: `strict` mode, no `any` in new code, no
  `dangerouslySetInnerHTML`.
- Tests next to the code they cover; every bug fix comes with a test that
  failed before the fix.
- Keep commits focused; describe the behaviour change, not the diff.

## Adding things

- **A model**: a new package under `packages/` registered in the
  `analytics_assistant.models` entry-point group (see `aa-model-ar1`).
- **A tool route or phrasing**: update `router.py` rules or training
  examples and add labelled questions to `evals/router_labels.yaml`
  (never copy eval questions into the training examples).
- **A document**: add markdown to `server/src/analytics_assistant/corpus/`
  (and to `CORPUS_FILES` in `web.py`) or point `AA_DOCS_DIR` at a folder;
  add retrieval labels for it.
- **An LLM provider format**: add a request builder and response parser to
  `WIRE_FORMATS` in `llm_adapter.py`, with MockTransport tests.
