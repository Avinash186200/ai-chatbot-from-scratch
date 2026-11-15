# API and MCP image: the server, both model packages and the optional extras
# the full stack uses (MCP, LangGraph, Postgres/pgvector, OpenTelemetry, PDF).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=5

WORKDIR /app
COPY server/ /app/server/
COPY packages/ /app/packages/
# The server ships no models; each model is its own package found through
# the "analytics_assistant.models" entry-point group.
RUN pip install --no-cache-dir "./server[mcp,graph,postgres,otel,pdf]" \
        ./packages/aa-model-ar1 ./packages/aa-model-regime \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /data && chown app /data

USER app
ENV AA_HOST=0.0.0.0 AA_PORT=8000 AA_DB_PATH=/data/analytics_assistant.db \
    AA_STATE_DB=/data/assistant_state.db
EXPOSE 8000 8001
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["python", "-m", "analytics_assistant", "serve"]
