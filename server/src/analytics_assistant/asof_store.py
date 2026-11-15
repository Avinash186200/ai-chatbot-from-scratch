"""Bitemporal point-in-time store for time series.

Each row carries two dates: ``obs_date`` (the day the value describes) and
``published_at`` (the day it became known). A query "as of" date D sees only
rows published on or before D, and of several revisions of one observation it
sees the latest one published by D. That is what keeps a backtest or a
historical answer free of lookahead: a value first released, or revised,
after D cannot leak into an answer for D.

The store is plain SQLite so the project runs with no external services.

Two write paths: ``insert`` (bulk seeding of synthetic history) and
``append_feed_file`` (the vendor drop feed). The second never overwrites a
stored value: a changed value becomes a new revision row, and the rows and the
file's ledger entry commit in one transaction, so a crash cannot leave a file
half-loaded or loaded twice.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class Observation:
    series: str
    obs_date: date
    value: float
    published_at: date


_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    series       TEXT NOT NULL,
    obs_date     TEXT NOT NULL,
    published_at TEXT NOT NULL,
    value        REAL NOT NULL,
    PRIMARY KEY (series, obs_date, published_at)
);
CREATE TABLE IF NOT EXISTS series (
    name        TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    unit        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feed_files (
    checksum       TEXT PRIMARY KEY,
    feed           TEXT NOT NULL,
    filename       TEXT NOT NULL,
    arrived_at     TEXT NOT NULL,
    published_at   TEXT,
    loaded_at      TEXT NOT NULL,
    status         TEXT NOT NULL,
    reason         TEXT,
    detail         TEXT,
    rows_new       INTEGER NOT NULL DEFAULT 0,
    rows_revised   INTEGER NOT NULL DEFAULT 0,
    rows_unchanged INTEGER NOT NULL DEFAULT 0,
    rows_stale     INTEGER NOT NULL DEFAULT 0
);
-- The vendor file date behind the current value of each feed key, so a late
-- original file cannot supersede a newer restatement that arrived before it.
CREATE TABLE IF NOT EXISTS feed_keys (
    series      TEXT NOT NULL,
    obs_date    TEXT NOT NULL,
    vendor_date TEXT NOT NULL,
    PRIMARY KEY (series, obs_date)
);
"""

_LEDGER_FIELDS = ("checksum", "feed", "filename", "arrived_at", "published_at", "loaded_at",
                  "status", "reason", "detail", "rows_new", "rows_revised", "rows_unchanged",
                  "rows_stale")


class HistoryConflict(ValueError):
    """Appending a row would overwrite or rewrite what is already published."""


@dataclass(frozen=True)
class AppendCounts:
    new: int = 0
    revised: int = 0
    unchanged: int = 0
    stale: int = 0


class AsOfStore:
    def __init__(self, path: str = ":memory:") -> None:
        # One connection shared across the server's worker threads, guarded by a lock.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def register_series(self, name: str, description: str, unit: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO series(name, description, unit) VALUES (?, ?, ?)",
                (name, description, unit),
            )

    def insert(self, rows: Iterable[Observation]) -> int:
        data = [
            (r.series, r.obs_date.isoformat(), r.published_at.isoformat(), float(r.value))
            for r in rows
        ]
        for series, obs, pub, _ in data:
            if pub < obs:
                raise ValueError(f"{series} {obs}: published before the date it describes")
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO observations(series, obs_date, published_at, value) "
                "VALUES (?, ?, ?, ?)",
                data,
            )
        return len(data)

    # ------------------------------------------------------------- drop feed

    def feed_file(self, checksum: str) -> dict[str, Any] | None:
        """The ledger entry for a file with this checksum, if it was seen before."""
        with self._lock:
            row = self._conn.execute(
                f"SELECT {', '.join(_LEDGER_FIELDS)} FROM feed_files WHERE checksum = ?",
                (checksum,),
            ).fetchone()
        return dict(zip(_LEDGER_FIELDS, row)) if row else None

    def feed_files(self, feed: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {', '.join(_LEDGER_FIELDS)} FROM feed_files WHERE feed = ? "
                "ORDER BY loaded_at, filename", (feed,)).fetchall()
        return [dict(zip(_LEDGER_FIELDS, r)) for r in rows]

    def feed_watermark(self, feed: str) -> date | None:
        """The latest publication date of any file loaded from ``feed``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(published_at) FROM feed_files WHERE feed = ? AND status = 'loaded'",
                (feed,),
            ).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    def record_feed_file(self, ledger: dict[str, Any]) -> None:
        """Record a file that was not loaded (quarantined), so it is not retried."""
        entry = {k: ledger.get(k) for k in _LEDGER_FIELDS}
        for k in ("rows_new", "rows_revised", "rows_unchanged", "rows_stale"):
            entry[k] = entry[k] or 0
        with self._lock, self._conn:
            self._conn.execute(
                f"INSERT OR IGNORE INTO feed_files({', '.join(_LEDGER_FIELDS)}) "
                f"VALUES ({', '.join('?' * len(_LEDGER_FIELDS))})",
                [entry[k] for k in _LEDGER_FIELDS],
            )

    def append_feed_file(self, rows: Iterable[Observation], ledger: dict[str, Any],
                         vendor_date: date) -> AppendCounts:
        """Append one file's rows without overwriting anything, plus its ledger entry.

        Per (series, obs_date): a newer vendor file already set the key -> stale
        (skipped); no stored row -> new; the latest stored value is equal ->
        unchanged (no row written); otherwise -> a revision row at the file's
        publication date. It raises ``HistoryConflict``, and writes nothing, if
        a row would replace a stored value at the same publication date or
        would be published before a value already stored for that key (either
        would change an answer already given for an earlier as-of).
        """
        rows = list(rows)
        new = revised = unchanged = stale = 0
        vendor = vendor_date.isoformat()
        to_write: list[tuple[str, str, str, float]] = []
        keys: list[tuple[str, str, str]] = []
        with self._lock, self._conn:
            if self._conn.execute("SELECT 1 FROM feed_files WHERE checksum = ?",
                                  (ledger["checksum"],)).fetchone():
                raise HistoryConflict("file already recorded")
            for r in rows:
                obs, pub = r.obs_date.isoformat(), r.published_at.isoformat()
                if pub < obs:
                    raise HistoryConflict(f"{r.series} {obs}: published before the date it "
                                          "describes")
                source = self._conn.execute(
                    "SELECT vendor_date FROM feed_keys WHERE series = ? AND obs_date = ?",
                    (r.series, obs)).fetchone()
                if source is not None and source[0] > vendor:
                    stale += 1
                    continue
                keys.append((r.series, obs, vendor))
                latest = self._conn.execute(
                    "SELECT published_at, value FROM observations WHERE series = ? AND "
                    "obs_date = ? ORDER BY published_at DESC LIMIT 1", (r.series, obs),
                ).fetchone()
                if latest is None:
                    new += 1
                elif latest[1] == float(r.value):
                    unchanged += 1
                    continue
                elif latest[0] >= pub:
                    raise HistoryConflict(
                        f"{r.series} {obs}: a value published {latest[0]} already exists; "
                        f"a different value published {pub} would rewrite history")
                else:
                    revised += 1
                to_write.append((r.series, obs, pub, float(r.value)))
            self._conn.executemany(
                "INSERT INTO observations(series, obs_date, published_at, value) "
                "VALUES (?, ?, ?, ?)", to_write)
            self._conn.executemany(
                "INSERT OR REPLACE INTO feed_keys(series, obs_date, vendor_date) "
                "VALUES (?, ?, ?)", keys)
            entry = {**{k: ledger.get(k) for k in _LEDGER_FIELDS}, "status": "loaded",
                     "rows_new": new, "rows_revised": revised, "rows_unchanged": unchanged,
                     "rows_stale": stale}
            self._conn.execute(
                f"INSERT INTO feed_files({', '.join(_LEDGER_FIELDS)}) "
                f"VALUES ({', '.join('?' * len(_LEDGER_FIELDS))})",
                [entry[k] for k in _LEDGER_FIELDS],
            )
        return AppendCounts(new, revised, unchanged, stale)

    def series_catalog(self) -> dict[str, dict[str, str]]:
        with self._lock:
            rows = self._conn.execute("SELECT name, description, unit FROM series ORDER BY name")
            return {n: {"description": d, "unit": u} for n, d, u in rows.fetchall()}

    def as_of(
        self,
        series: str,
        as_of: date,
        start: date | None = None,
        end: date | None = None,
    ) -> list[tuple[date, float]]:
        """Observations of ``series`` as they were known on ``as_of``, oldest first."""
        sql = """
            SELECT o.obs_date, o.value FROM observations o
            JOIN (
                SELECT obs_date, MAX(published_at) AS pub FROM observations
                WHERE series = ? AND published_at <= ?
                GROUP BY obs_date
            ) latest ON latest.obs_date = o.obs_date AND latest.pub = o.published_at
            WHERE o.series = ?
        """
        params: list[str] = [series, as_of.isoformat(), series]
        if start is not None:
            sql += " AND o.obs_date >= ?"
            params.append(start.isoformat())
        if end is not None:
            sql += " AND o.obs_date <= ?"
            params.append(end.isoformat())
        sql += " ORDER BY o.obs_date"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [(date.fromisoformat(d), v) for d, v in rows]

    def latest_published(self, series: str, as_of: date) -> date | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(published_at) FROM observations WHERE series = ? AND published_at <= ?",
                (series, as_of.isoformat()),
            ).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    def latest_publication(self) -> date | None:
        """The most recent publication date across all series."""
        with self._lock:
            row = self._conn.execute("SELECT MAX(published_at) FROM observations").fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    def snapshot_id(self, series: str, as_of: date) -> str | None:
        """A stable identity for "everything known about ``series`` on ``as_of``".

        Two calls see the same snapshot exactly when they would read the same
        rows, so a client can cache on it and an auditor can reproduce an answer.
        """
        latest = self.latest_published(series, as_of)
        if latest is None:
            return None
        with self._lock:
            count = self._conn.execute(
                "SELECT COUNT(*) FROM observations WHERE series = ? AND published_at <= ?",
                (series, as_of.isoformat()),
            ).fetchone()[0]
        digest = hashlib.sha256(f"{series}|{latest.isoformat()}|{count}".encode()).hexdigest()
        return f"snap-{digest[:16]}"
