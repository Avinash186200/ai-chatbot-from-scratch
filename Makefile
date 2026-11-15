# Thin wrappers over scripts/ so the same commands work with or without make.
.PHONY: help install up down seed logs test config certs evals web-dev serve

help:
	@echo "make install   create .venv and install server, models, web and excel-client deps"
	@echo "make serve     run the server offline on http://localhost:8000"
	@echo "make web-dev   run the web UI dev server on http://localhost:5173 (proxies to :8000)"
	@echo "make test      every offline check CI runs"
	@echo "make evals     the eval suites (questions, router, retrieval)"
	@echo "make up        full Docker stack on https://localhost:8443"
	@echo "make seed      load the synthetic vendor feed and documents into the stack"
	@echo "make down      stop the stack (volumes kept)"
	@echo "make config    validate docker-compose.yml"

install:
	python -m venv .venv
	.venv/bin/pip install -e "server[dev,mcp,graph,otel,pdf]" -e packages/aa-model-ar1 -e packages/aa-model-regime
	cd web && npm ci
	cd excel-client && npm ci

serve:
	AA_GATEWAY_TOKEN=$${AA_GATEWAY_TOKEN:-change-me-local-token} .venv/bin/python -m analytics_assistant serve

web-dev:
	cd web && npm run dev

test:
	sh scripts/test-all.sh

evals:
	.venv/bin/python evals/run_evals.py

certs:
	sh scripts/gen-certs.sh

up:
	sh scripts/stack.sh up

down:
	sh scripts/stack.sh down

seed:
	sh scripts/stack.sh seed

logs:
	sh scripts/stack.sh logs

config:
	sh scripts/stack.sh config
