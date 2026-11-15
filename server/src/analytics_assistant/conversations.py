"""Conversation index and loop-agent history, in SQLite.

``ConversationStore`` keeps one row per thread (id, user label, title,
created/updated timestamps) so the web UI can list and resume conversations,
and it implements the agent's ``HistoryStore`` so the default loop agent's
threads survive a restart. The graph agent keeps its own history in LangGraph
checkpoints (see ``graph_agent``) and uses this store for the index only.

``user`` is a label sent by the client, not an authenticated identity: every
caller shares one bearer token. Per-user authentication is out of scope for
this reference build (see docs/operations.md).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

TITLE_CHARS = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    thread_id  TEXT PRIMARY KEY,
    user       TEXT NOT NULL,
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS conversations_user ON conversations (user, updated_at);
CREATE TABLE IF NOT EXISTS thread_messages (
    thread_id TEXT PRIMARY KEY,
    messages  TEXT NOT NULL
);
"""


def make_title(message: str) -> str:
    text = " ".join(message.split())
    return text if len(text) <= TITLE_CHARS else text[:TITLE_CHARS - 3].rstrip() + "..."


class ConversationStore:
    def __init__(self, path: str = "", clock: Callable[[], datetime] | None = None) -> None:
        self._conn = sqlite3.connect(path or ":memory:", check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()
        self.now = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------ index

    def create(self, thread_id: str, user: str, title: str = "New conversation") -> dict:
        ts = self.now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO conversations VALUES (?, ?, ?, ?, ?)",
                (thread_id, user, make_title(title) or "New conversation", ts, ts))
        return self.get(thread_id)  # type: ignore[return-value]

    def touch(self, thread_id: str, user: str, message: str) -> None:
        """Record activity; the first message becomes the title of an untitled thread."""
        ts = self.now().isoformat()
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT title FROM conversations WHERE thread_id = ?",
                                     (thread_id,))
            row = cur.fetchone()
            if row is None:
                self._conn.execute("INSERT INTO conversations VALUES (?, ?, ?, ?, ?)",
                                   (thread_id, user, make_title(message), ts, ts))
            else:
                title = make_title(message) if row[0] == "New conversation" else row[0]
                self._conn.execute(
                    "UPDATE conversations SET updated_at = ?, title = ? WHERE thread_id = ?",
                    (ts, title, thread_id))

    def get(self, thread_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT thread_id, user, title, created_at, updated_at FROM conversations "
            "WHERE thread_id = ?", (thread_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, user: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT thread_id, user, title, created_at, updated_at FROM conversations "
            "WHERE user = ? ORDER BY updated_at DESC, thread_id LIMIT ?",
            (user, max(1, min(limit, 200)))).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row: tuple) -> dict[str, Any]:
        return {"thread_id": row[0], "user": row[1], "title": row[2], "created_at": row[3],
                "updated_at": row[4]}

    # ------------------------------------------------------------ HistoryStore

    def load(self, thread_id: str) -> list[dict[str, Any]]:
        row = self._conn.execute("SELECT messages FROM thread_messages WHERE thread_id = ?",
                                 (thread_id,)).fetchone()
        return json.loads(row[0]) if row else []

    def save(self, thread_id: str, messages: list[dict[str, Any]]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO thread_messages VALUES (?, ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET messages = excluded.messages",
                (thread_id, json.dumps(messages)))
