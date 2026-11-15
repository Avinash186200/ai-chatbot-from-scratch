#!/usr/bin/env sh
# Run every offline check CI runs, in order, stopping at the first failure.
#
#   scripts/test-all.sh            # needs the repo .venv (or an active venv) and Node 20+
#
# No network and no API keys are needed once dependencies are installed.
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"

if [ -x .venv/bin/python ]; then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else PY=python; fi

step() { printf '\n==> %s\n' "$1"; }

step "lint"
"$PY" -m ruff check server packages evals scripts
step "vendor-import check"
"$PY" scripts/check_vendor_imports.py
step "server tests"
(cd server && "../$PY" -m pytest -q)
step "model package tests"
"$PY" -m pytest -q packages/aa-model-ar1
"$PY" -m pytest -q packages/aa-model-regime
step "eval harness tests"
"$PY" -m pytest -q evals
step "evals: questions (loop agent), router, retrieval"
"$PY" evals/run_evals.py
step "evals: questions (graph agent)"
"$PY" evals/run_evals.py --suite questions --agent graph
step "excel-client tests"
(cd excel-client && npm test -- --ci && npm run typecheck)
step "web tests and build"
(cd web && npm test && npm run build)
printf '\nall checks passed\n'
