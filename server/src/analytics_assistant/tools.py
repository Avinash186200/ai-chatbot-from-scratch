"""Tool registry: plugin discovery, argument validation, as-of resolution, dispatch.

Both transports (MCP and the REST gateway) and the chat agent call tools
through ``ToolRegistry.call``, so validation and the envelope are identical
whichever way a tool is reached.

Plugin isolation: a plugin that fails to import or to build its tool list is
recorded and skipped, so one broken model package removes only its own
tools; the server and every other plugin keep serving. ``health`` reports
what loaded (distribution, version, tools) and what failed.

The server ships no models. Each model is its own installable package
(``packages/aa-model-*`` in the repository) registered under the
``analytics_assistant.models`` entry-point group. The registry stamps the
owning package's name and version into the provenance of every result, so the
model version in an answer always matches what is installed.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from importlib.metadata import entry_points
from typing import Any

from analytics_assistant import __version__, telemetry
from analytics_assistant.asof_store import AsOfStore
from analytics_assistant.envelopes import (
    INTERNAL,
    MODEL_FAILURE,
    SCHEMA_VIOLATION,
    SNAPSHOT_UNAVAILABLE,
    Provenance,
    ToolError,
    ok,
    refusal,
)
from analytics_assistant.plugin_api import ModelPlugin, ToolContext, ToolOutput, ToolSpec

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "analytics_assistant.models"

AS_OF_SCHEMA = {
    "type": "string",
    "format": "date",
    "description": "Point-in-time date (YYYY-MM-DD). Only data published by this date is "
    "used. Defaults to the latest published data.",
}

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> None:
    """A deliberately small JSON-schema check: known keys, types, enums, required."""
    if not isinstance(args, dict):
        raise ToolError(SCHEMA_VIOLATION, "arguments must be a JSON object")
    props: dict[str, Any] = schema.get("properties", {})
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise ToolError(SCHEMA_VIOLATION, f"unknown argument(s): {', '.join(unknown)}")
    for name in schema.get("required", []):
        if name not in args:
            raise ToolError(SCHEMA_VIOLATION, f"missing required argument: {name}")
    for name, value in args.items():
        if value is None:
            continue
        spec = props[name]
        expected = _JSON_TYPES.get(spec.get("type", ""), (object,))
        # bool is an int subclass in Python; JSON does not treat it as a number.
        if isinstance(value, bool) and spec.get("type") in ("integer", "number"):
            raise ToolError(SCHEMA_VIOLATION, f"{name} must be a {spec['type']}")
        if not isinstance(value, expected):
            raise ToolError(SCHEMA_VIOLATION, f"{name} must be a {spec.get('type')}")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolError(SCHEMA_VIOLATION,
                            f"{name} must be one of {', '.join(map(str, spec['enum']))}")


def parse_date(name: str, value: Any) -> date:
    if not isinstance(value, str):
        raise ToolError(SCHEMA_VIOLATION, f"{name} must be a YYYY-MM-DD string")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ToolError(SCHEMA_VIOLATION, f"{name} must be a YYYY-MM-DD date") from None


def discover_plugins() -> Iterable[tuple[str, Callable[[], ModelPlugin]]]:
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        yield ep.name, ep.load


class ToolRegistry:
    def __init__(
        self,
        store: AsOfStore,
        plugin_factories: Iterable[tuple[str, Callable[[], Any]]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.now = clock or (lambda: datetime.now(UTC))
        self.tools: dict[str, ToolSpec] = {}
        self.plugins: list[dict[str, Any]] = []
        self.failed_plugins: list[dict[str, str]] = []
        # tool name -> the plugin that serves it (core tools have no owner)
        self._owner: dict[str, ModelPlugin] = {}
        self._register_core()
        if plugin_factories is None:
            plugin_factories = list(discover_plugins())
        for name, factory in plugin_factories:
            self._load_plugin(name, factory)

    def _load_plugin(self, name: str, factory: Callable[[], Any]) -> None:
        try:
            loaded = factory()
            plugin = loaded() if callable(loaded) else loaded
            if not isinstance(plugin, ModelPlugin):
                raise TypeError("entry point did not return a ModelPlugin")
            if not plugin.tools or not all(isinstance(t, ToolSpec) for t in plugin.tools):
                raise TypeError("plugin must serve at least one ToolSpec")
            names = [t.name for t in plugin.tools]
            clashes = [n for n in names if n in self.tools] + [
                n for i, n in enumerate(names) if n in names[:i]]
            if clashes:
                raise ValueError(f"tool name(s) already registered: {', '.join(clashes)}")
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            log.warning("model plugin %s failed to load: %s", name, exc)
            self.failed_plugins.append({"plugin": name, "error": type(exc).__name__})
            return
        for tool in plugin.tools:
            self.tools[tool.name] = tool
            self._owner[tool.name] = plugin
        self.plugins.append({"plugin": plugin.name, "entry_point": name,
                             "version": plugin.version, "model_id": plugin.model_id,
                             "tools": [t.name for t in plugin.tools]})

    # ------------------------------------------------------------------ core tools

    def _register_core(self) -> None:
        self.tools["health"] = ToolSpec(
            "health", "Server liveness, loaded and failed model plugins.",
            {"type": "object", "properties": {}}, self._health)
        self.tools["model_info"] = ToolSpec(
            "model_info", "List every model tool with its description.",
            {"type": "object", "properties": {}}, self._model_info)
        self.tools["query_series"] = ToolSpec(
            "query_series",
            "Raw observations of a series as they were known on the as-of date "
            "(first release or latest revision published by then).",
            {
                "type": "object",
                "properties": {
                    "series": {"type": "string", "description": "Series name; see model_info."},
                    "start_date": {"type": "string", "format": "date"},
                    "end_date": {"type": "string", "format": "date"},
                },
                "required": ["series"],
            },
            self._query_series,
        )

    def _health(self, ctx: ToolContext, _: dict[str, Any]) -> ToolOutput:
        return ToolOutput(
            {"status": "ok", "version": __version__, "plugins": self.plugins,
             "failed_plugins": self.failed_plugins,
             "latest_publication": (lp.isoformat() if (lp := self.store.latest_publication())
                                    else None)},
            Provenance(method="health", computed_at=ctx.now().isoformat()),
        )

    def _model_info(self, ctx: ToolContext, _: dict[str, Any]) -> ToolOutput:
        return ToolOutput(
            {"tools": [{"name": t.name, "description": t.description,
                        "plugin": (o.name if (o := self._owner.get(t.name)) else None),
                        "model_version": o.version if o else None}
                       for t in self.tools.values()],
             "series": self.store.series_catalog()},
            Provenance(method="catalog", computed_at=ctx.now().isoformat()),
        )

    def _query_series(self, ctx: ToolContext, args: dict[str, Any]) -> ToolOutput:
        series = args["series"]
        if series not in self.store.series_catalog():
            raise ToolError(SCHEMA_VIOLATION, f"unknown series {series!r}; see model_info")
        start = parse_date("start_date", args["start_date"]) if args.get("start_date") else None
        end = parse_date("end_date", args["end_date"]) if args.get("end_date") else None
        if start and end and start > end:
            raise ToolError(SCHEMA_VIOLATION, "start_date is after end_date")
        rows = self.store.as_of(series, ctx.as_of, start, end)
        if not rows:
            raise ToolError(SNAPSHOT_UNAVAILABLE,
                            f"no {series} observations in range published by {ctx.as_of}")
        return ToolOutput(
            {"series": series, "observations": [{"date": d.isoformat(), "value": v}
                                                for d, v in rows]},
            Provenance(
                method="point_in_time_query",
                snapshot_id=self.store.snapshot_id(series, ctx.as_of),
                as_of=ctx.as_of.isoformat(),
                note="Each value is the latest revision published on or before as_of.",
                computed_at=ctx.now().isoformat(),
                deterministic=True,
            ),
        )

    # ------------------------------------------------------------------ dispatch

    def schema_for(self, name: str) -> dict[str, Any]:
        """The tool's argument schema as clients see it, with the shared as_of field."""
        spec = self.tools[name]
        schema = {"type": "object", "properties": dict(spec.parameters.get("properties", {})),
                  "required": list(spec.parameters.get("required", []))}
        if name not in ("health", "model_info"):
            schema["properties"]["as_of"] = AS_OF_SCHEMA
        return schema

    def resolve_as_of(self, raw: Any) -> date:
        today = self.now().date()
        if raw in (None, ""):
            latest = self.store.latest_publication()
            return min(today, latest) if latest else today
        as_of = parse_date("as_of", raw)
        if as_of > today:
            raise ToolError(SCHEMA_VIOLATION, "as_of cannot be in the future")
        return as_of

    def call(self, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
        if name not in self.tools:
            raise KeyError(name)
        with telemetry.span("tool.call", {"tool.name": name}) as sp:
            envelope = self._call(name, args)
            sp.set_attribute("tool.ok", bool(envelope.get("ok")))
            if not envelope.get("ok"):
                sp.set_attribute("tool.error_code", envelope["payload"]["error"]["code"])
            return envelope

    def _call(self, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
        args = dict(args or {})
        try:
            validate_args(self.schema_for(name), args)
            as_of = self.resolve_as_of(args.pop("as_of", None))
            out = self.tools[name].handler(ToolContext(self.store, as_of, self.now), args)
        except ToolError as err:
            return refusal(name, err.code, err.message)
        except Exception:
            log.exception("tool %s raised", name)
            code = MODEL_FAILURE if name in self._owner else INTERNAL
            return refusal(name, code, "the tool failed; see the server log")
        provenance = out.provenance
        if (owner := self._owner.get(name)) is not None:
            # The installed package is the source of truth for the model version.
            provenance = dataclasses.replace(
                provenance,
                model_id=provenance.model_id or owner.model_id or owner.name,
                model_version=owner.version,
            )
        return ok(name, out.result, provenance, out.notes)
