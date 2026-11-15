"""The one internal shape every LLM provider is normalised to.

Messages inside the agent use the OpenAI chat shape (``role`` is system,
user, assistant or tool; assistant tool calls carry JSON-encoded arguments),
because it is the most widely implemented. Each provider adapter translates
to and from its wire format, so nothing outside ``llm_adapter`` knows which
vendor is on the other end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Completion:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Filled by real providers; the offline provider leaves them empty.
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""


class LLMError(RuntimeError):
    """A provider failed. The message never includes a response body or a key."""


class Provider(Protocol):
    def complete(self, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]]) -> Completion: ...
