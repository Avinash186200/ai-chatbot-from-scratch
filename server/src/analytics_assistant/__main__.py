"""Command line: ``python -m analytics_assistant <command>``.

    serve       HTTP app (gateway, chat API, UI) on AA_HOST:AA_PORT
    mcp-stdio   MCP server over stdio (needs the ``mcp`` extra)
    mcp-http    MCP server over streamable HTTP at /mcp, bearer-protected
                (needs the ``mcp`` extra; AA_MCP_PORT, default 8001)
    seed        seed the store with synthetic history and a replayed synthetic
                vendor feed, and ingest the reference documents (idempotent)
    call        call one tool and print the envelope, e.g.
                call forecast_series '{"horizon": 3, "as_of": "2023-06-30"}'
    generate-feed  write a synthetic vendor drop folder
                generate-feed --out ./drop
    ingest      load a vendor drop folder into the store (AA_DB_PATH)
                ingest --drop ./drop --quarantine ./quarantine [--replay]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from analytics_assistant.config import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="analytics-assistant")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    sub.add_parser("mcp-stdio")
    sub.add_parser("mcp-http")
    sub.add_parser("seed")
    call = sub.add_parser("call")
    call.add_argument("tool")
    call.add_argument("args", nargs="?", default="{}")
    gen = sub.add_parser("generate-feed")
    gen.add_argument("--out", required=True)
    gen.add_argument("--days", type=int, default=60)
    gen.add_argument("--seed", type=int, default=11)
    ing = sub.add_parser("ingest")
    ing.add_argument("--drop", required=True, help="local drop folder")
    ing.add_argument("--quarantine", required=True)
    ing.add_argument("--feed", default="vendor")
    ing.add_argument("--replay", action="store_true",
                     help="publication date = arrival date (rebuilding from an archive)")
    ns = parser.parse_args(argv)

    if ns.command == "generate-feed":
        from analytics_assistant.ingest.synthetic_feed import write_synthetic_feed

        files = write_synthetic_feed(ns.out, days=ns.days, seed=ns.seed)
        print(f"wrote {len(files)} files to {ns.out}")
        return 0

    settings = Settings.from_env()
    host = os.environ.get("AA_HOST", "127.0.0.1")
    port = int(os.environ.get("AA_PORT", "8000"))

    from analytics_assistant import telemetry

    if ns.command in ("serve", "mcp-http", "seed"):
        telemetry.setup_logging(settings.log_format, settings.log_level,
                                secrets=[settings.gateway_token, settings.llm_api_key,
                                         settings.llm_fallback_api_key])

    if ns.command == "serve":
        import uvicorn

        from analytics_assistant.web import create_app

        telemetry.setup_tracing(settings.otel_exporter, settings.otel_endpoint)
        uvicorn.run(create_app(settings), host=host, port=port, access_log=False,
                    log_config=None)
        return 0

    from analytics_assistant.web import build_registry

    registry = build_registry(settings)
    if ns.command == "seed":
        return seed(settings, registry)
    if ns.command == "ingest":
        from analytics_assistant.ingest.sftp_feed import (
            DropFeedIngester,
            FeedConfig,
            LocalDropSource,
            summarise,
        )

        results = DropFeedIngester(registry.store, LocalDropSource(ns.drop), ns.quarantine,
                                   FeedConfig(name=ns.feed), replay=ns.replay).run()
        for r in results:
            if r.status != "loaded":
                print(f"{r.status:12} {r.name} {r.reason or ''} {r.detail or ''}".rstrip())
        print(json.dumps(summarise(results)))
        return 1 if any(r.status == "quarantined" for r in results) else 0

    if ns.command == "call":
        if ns.tool not in registry.tools:
            print(f"unknown tool {ns.tool!r}; known: {', '.join(registry.tools)}",
                  file=sys.stderr)
            return 2
        print(json.dumps(registry.call(ns.tool, json.loads(ns.args)), indent=2))
        return 0

    if ns.command == "mcp-stdio":
        from analytics_assistant.mcp_server import build_mcp_server

        build_mcp_server(registry).run("stdio")
        return 0

    import uvicorn

    from analytics_assistant.mcp_server import build_mcp_http_app

    telemetry.setup_tracing(settings.otel_exporter, settings.otel_endpoint)
    allowed = [h.strip() for h in os.environ.get(
        "AA_MCP_ALLOWED_HOSTS", "127.0.0.1,127.0.0.1:*,localhost,localhost:*").split(",")
        if h.strip()]
    uvicorn.run(build_mcp_http_app(registry, settings.gateway_token, host, allowed), host=host,
                port=int(os.environ.get("AA_MCP_PORT", "8001")), access_log=False,
                log_config=None)
    return 0


def seed(settings: Settings, registry) -> int:
    """Synthetic history (already seeded by build_registry) + vendor feed + documents."""
    import tempfile
    from pathlib import Path

    from analytics_assistant.ingest.sftp_feed import (
        DropFeedIngester,
        FeedConfig,
        LocalDropSource,
        summarise,
    )
    from analytics_assistant.ingest.synthetic_feed import write_synthetic_feed
    from analytics_assistant.web import build_knowledge

    with tempfile.TemporaryDirectory() as tmp:
        write_synthetic_feed(Path(tmp) / "drop")
        results = DropFeedIngester(registry.store, LocalDropSource(Path(tmp) / "drop"),
                                   Path(tmp) / "quarantine", FeedConfig(name="vendor"),
                                   replay=True).run()
    knowledge = build_knowledge(settings)
    print(json.dumps({"store": settings.db_path, "feed": summarise(results),
                      "documents": sorted(set(knowledge.store.collections())),
                      "vector_store": type(knowledge.store).__name__}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
