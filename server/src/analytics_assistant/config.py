"""Settings, read from environment variables only (see .env.example).

Nothing here has a secret default. With no token configured the gateway and
chat API refuse every request rather than run open.

Dataclass defaults are the safe, offline, in-memory choices used by tests;
``from_env`` defaults are the laptop choices (files in the working folder).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # Bearer token required by /gateway/*, /api/* and the MCP HTTP endpoint.
    gateway_token: str = field(default="", repr=False)
    # SQLite file for the point-in-time store; ":memory:" for an ephemeral run.
    db_path: str = "analytics_assistant.db"
    # Seed for the synthetic data generator, so every run is reproducible.
    synthetic_seed: int = 7

    # LLM: "scripted" (offline, deterministic), "openai_compatible" or "anthropic".
    llm_provider: str = "scripted"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = ""
    llm_api_key: str = field(default="", repr=False)
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 3
    # Optional second provider, used only when the first one fails.
    llm_fallback_provider: str = ""
    llm_fallback_base_url: str = ""
    llm_fallback_model: str = ""
    llm_fallback_api_key: str = field(default="", repr=False)
    # Prices in your currency per million tokens, for the cost counter. 0 = not tracked.
    llm_price_in_per_mtok: float = 0.0
    llm_price_out_per_mtok: float = 0.0

    # Agent: "loop" (default, in-process tool loop) or "graph" (LangGraph state graph).
    agent: str = "loop"
    agent_max_steps: int = 6
    # PostgreSQL URL. When set: LangGraph checkpoints and the vector store use it.
    database_url: str = field(default="", repr=False)
    # SQLite file for conversation index + graph checkpoints when no database_url.
    # Empty = in memory (tests).
    state_db: str = ""

    # Retrieval.
    embedder: str = "hashing"              # "hashing" | "sentence-transformers"
    embedding_model: str = "all-MiniLM-L6-v2"
    rag_min_score: float = 0.12
    docs_dir: str = ""                     # extra .md/.txt/.pdf documents to ingest

    # Observability.
    otel_exporter: str = "none"            # "console" | "otlp" | "none"
    otel_endpoint: str = ""
    log_format: str = "json"               # "json" | "text"
    log_level: str = "INFO"

    # Built web UI (web/dist) to serve at "/"; empty = the minimal static page.
    web_dist: str = ""

    # Origins allowed to call the gateway from a browser. Comma-separated.
    cors_origins: tuple[str, ...] = ("https://localhost:3000",)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            gateway_token=_env("AA_GATEWAY_TOKEN"),
            db_path=_env("AA_DB_PATH", "analytics_assistant.db"),
            synthetic_seed=int(_env("AA_SYNTHETIC_SEED", "7")),
            llm_provider=_env("AA_LLM_PROVIDER", "scripted"),
            llm_base_url=_env("AA_LLM_BASE_URL", "http://localhost:11434/v1"),
            llm_model=_env("AA_LLM_MODEL"),
            llm_api_key=_env("AA_LLM_API_KEY"),
            llm_timeout_s=float(_env("AA_LLM_TIMEOUT_S", "60")),
            llm_max_retries=int(_env("AA_LLM_MAX_RETRIES", "3")),
            llm_fallback_provider=_env("AA_LLM_FALLBACK_PROVIDER"),
            llm_fallback_base_url=_env("AA_LLM_FALLBACK_BASE_URL"),
            llm_fallback_model=_env("AA_LLM_FALLBACK_MODEL"),
            llm_fallback_api_key=_env("AA_LLM_FALLBACK_API_KEY"),
            llm_price_in_per_mtok=float(_env("AA_LLM_PRICE_IN_PER_MTOK", "0")),
            llm_price_out_per_mtok=float(_env("AA_LLM_PRICE_OUT_PER_MTOK", "0")),
            agent=_env("AA_AGENT", "loop"),
            agent_max_steps=int(_env("AA_AGENT_MAX_STEPS", "6")),
            database_url=_env("AA_DATABASE_URL"),
            state_db=_env("AA_STATE_DB", "assistant_state.db"),
            embedder=_env("AA_EMBEDDER", "hashing"),
            embedding_model=_env("AA_EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
            rag_min_score=float(_env("AA_RAG_MIN_SCORE", "0.12")),
            docs_dir=_env("AA_DOCS_DIR"),
            otel_exporter=_env("AA_OTEL_EXPORTER", "console"),
            otel_endpoint=_env("AA_OTEL_ENDPOINT"),
            log_format=_env("AA_LOG_FORMAT", "json"),
            log_level=_env("AA_LOG_LEVEL", "INFO"),
            web_dist=_env("AA_WEB_DIST"),
            cors_origins=tuple(
                o.strip() for o in _env("AA_CORS_ORIGINS", "https://localhost:3000").split(",")
                if o.strip()
            ),
        )
