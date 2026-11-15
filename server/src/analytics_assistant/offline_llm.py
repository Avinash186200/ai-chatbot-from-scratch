"""A deterministic, offline stand-in for an LLM: keyword routing plus templated answers.

It lets the chat UI, the tests and the question-set evals (``evals/``) run
with no network and no API key. It is deliberately dumb: it picks one tool
from keywords, pulls arguments (series, dates, horizon) out of the question
with regular expressions, and writes the answer from a template that only
copies values out of the tool result. It refuses anything that asks for
advice, secrets or a made-up number.

The routing order matters: refusals first, then definitional questions
("what is a ...", "how is ... computed") to the documents, then tools.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from analytics_assistant.llm_types import Completion, ToolCall
from analytics_assistant.router import DOCS_PATTERN, REFUSE_PATTERN, SERIES_PATTERN, TOOL_PATTERNS

KNOWLEDGE_TOOL = "knowledge_search"

REFUSAL = ("I can't help with that. I answer only from the analytics tools and the reference "
           "documents: no investment advice, no credentials, and no made-up numbers.")
UNROUTED = ("I'm not sure which tool answers that. Ask for a forecast, a regime, a series' "
            "values, the tool list, or a definition from the reference documents.")

# The rule patterns are shared with the intent router (router.py), so the
# offline model and the router never disagree about what a rule matches.
_REFUSE = REFUSE_PATTERN
_DOCS = DOCS_PATTERN
_ROUTES = TOOL_PATTERNS
_SERIES = SERIES_PATTERN
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_AS_OF = re.compile(r"\bas of (\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_HORIZON = re.compile(r"\b(\d+)\s*(?:business |trading )?(?:days?|steps?)\b(?![- ](?:lookback|window))",
                      re.IGNORECASE)
_LOOKBACK = re.compile(r"\b(\d+)[- ]day (?:lookback|window)\b", re.IGNORECASE)
RECENT_WINDOW_DAYS = 30


def route(question: str) -> str | None:
    """The tool the question goes to, "knowledge_search", or None to refuse."""
    if _REFUSE.search(question):
        return None
    if _DOCS.search(question):
        return KNOWLEDGE_TOOL
    for tool, pattern in _ROUTES:
        if pattern.search(question):
            return tool
    return None


def extract_args(tool: str, question: str) -> dict[str, Any]:
    args: dict[str, Any] = {}
    series = _SERIES.findall(question)
    as_of_match = _AS_OF.search(question)
    as_of = as_of_match.group(1) if as_of_match else None
    other_dates = [d for d in _DATE.findall(question) if d != as_of]
    if tool == KNOWLEDGE_TOOL:
        return {"query": question}
    if tool in ("forecast_series", "detect_regime", "query_series") and series:
        args["series"] = series[0]
    if tool in ("forecast_series", "detect_regime", "query_series") and as_of:
        args["as_of"] = as_of
    if tool == "forecast_series" and (m := _HORIZON.search(question)):
        args["horizon"] = int(m.group(1))
    if tool == "detect_regime" and (m := _LOOKBACK.search(question)):
        args["lookback_days"] = int(m.group(1))
    if tool == "query_series":
        if len(other_dates) >= 2:
            args["start_date"], args["end_date"] = other_dates[0], other_dates[1]
        elif as_of:
            # "latest" questions: a recent window keeps the result small.
            start = date.fromisoformat(as_of) - timedelta(days=RECENT_WINDOW_DAYS)
            args["start_date"] = start.isoformat()
    return args


def _last_user(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m["role"] == "user":
            return str(m.get("content") or "")
    return ""


def offline_turn(messages: list[dict[str, Any]]) -> Completion:
    last = messages[-1]
    if last["role"] == "user":
        question = str(last.get("content") or "")
        tool = route(question)
        if tool is None:
            return Completion(REFUSAL if _REFUSE.search(question) else UNROUTED)
        return Completion(tool_calls=[ToolCall("call-1", tool, extract_args(tool, question))])
    if last["role"] == "tool":
        try:
            envelope = json.loads(last["content"])
        except (TypeError, ValueError):
            return Completion("The tool result was too long to summarise here; narrow the "
                              "date range.")
        return Completion(compose(envelope))
    return Completion("Done.")


def compose(env: dict[str, Any]) -> str:
    """An answer whose every number is copied from the tool result."""
    tool = env.get("tool")
    if not env.get("ok"):
        err = (env.get("payload") or {}).get("error") or {}
        return f"The {tool} tool refused ({err.get('code')}): {err.get('message')}"
    res = env["payload"]["result"]
    prov = env.get("provenance") or {}
    cite = (f"As of {prov.get('as_of')}, snapshot {prov.get('snapshot_id')}"
            + (f", model {prov['model_id']} {prov['model_version']}"
               if prov.get("model_id") else "") + ".")
    if tool == "forecast_series":
        end = res["path"][-1]
        return (f"{res['series']} was {res['last_value']} on {res['last_observation']}. The "
                f"placeholder model puts it at {end['point']} after {res['horizon']} business "
                f"days (interval {end['lower_90']} to {end['upper_90']}). {cite}")
    if tool == "detect_regime":
        return (f"{res['series']} regime: {res['regime']} (net change {res['net_change']} "
                f"from {res['window_start']} to {res['window_end']}). {cite}")
    if tool == "query_series":
        obs = res["observations"]
        return (f"{res['series']} as known then: observations from {obs[0]['date']} to "
                f"{obs[-1]['date']}; the latest is {obs[-1]['value']} on {obs[-1]['date']}. "
                f"{cite}")
    if tool == "model_info":
        return ("Tools: " + ", ".join(t["name"] for t in res["tools"]) + ". Series: "
                + ", ".join(res["series"]) + ".")
    if tool == "health":
        loaded = ", ".join(f"{p['plugin']} {p['version']}" for p in res["plugins"]) or "none"
        failed = ", ".join(p["plugin"] for p in res["failed_plugins"]) or "none"
        return (f"Server status {res['status']}, version {res['version']}. Models loaded: "
                f"{loaded}. Failed: {failed}.")
    if tool == KNOWLEDGE_TOOL:
        if not res:
            return ("I don't know: nothing in the reference documents is close enough to that "
                    "question to answer it.")
        hit = res[0]
        text = hit["text"]
        if len(text) > 400:
            text = text[:400].rsplit(" ", 1)[0] + " ..."
        return f"From {hit['source']}: {text}"
    return "Done."
