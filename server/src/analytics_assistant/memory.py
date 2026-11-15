"""Conversational memory: durable user preferences only.

Kept apart from document retrieval on purpose (see docs/architecture.md):
memory holds things that stay true about a user ("show yields in basis
points"), never current market levels or model outputs, which go stale and
must always come from a point-in-time tool call with an as-of date. A note
that looks like a number-with-a-date is refused at write time.
"""

from __future__ import annotations

import re
from collections import defaultdict

_LOOKS_LIKE_A_LEVEL = re.compile(r"\d+(\.\d+)?\s*(%|bp|bps)|\b\d{4}-\d{2}-\d{2}\b", re.IGNORECASE)
MAX_NOTES_PER_USER = 50


class MemoryRejected(ValueError):
    pass


class PreferenceMemory:
    def __init__(self) -> None:
        self._notes: dict[str, list[str]] = defaultdict(list)

    def remember(self, user: str, note: str) -> None:
        note = note.strip()
        if not note:
            raise MemoryRejected("empty note")
        if _LOOKS_LIKE_A_LEVEL.search(note):
            raise MemoryRejected(
                "memory stores preferences, not market levels or dated facts; "
                "ask a tool with an as_of date instead"
            )
        notes = self._notes[user]
        if note not in notes:
            notes.append(note)
            del notes[:-MAX_NOTES_PER_USER]

    def recall(self, user: str) -> list[str]:
        return list(self._notes.get(user, []))

    def forget(self, user: str) -> None:
        self._notes.pop(user, None)
