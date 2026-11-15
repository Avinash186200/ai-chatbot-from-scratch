"""Vector stores behind one protocol.

* ``InMemoryVectorStore`` (default): a list scan. Exact, deterministic, fine
  up to tens of thousands of chunks.
* ``PgVectorStore``: PostgreSQL with the ``vector`` extension, used when
  ``AA_DATABASE_URL`` is set. Cosine distance with an HNSW index. Needs the
  ``postgres`` extra (psycopg 3). Vectors are sent as pgvector text literals,
  so the ``pgvector`` Python package is not required.

Re-ingesting a document replaces its chunks (delete then insert in one
transaction), so ingestion is idempotent per (collection, doc_id).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class VectorRecord:
    collection: str
    doc_id: str
    section: str
    heading: str
    ordinal: int
    text: str
    vector: tuple[float, ...]

    @property
    def id(self) -> str:
        return f"{self.collection}/{self.doc_id}#{self.section}/{self.ordinal}"


class VectorStore(Protocol):
    def replace_document(self, collection: str, doc_id: str,
                         records: Sequence[VectorRecord]) -> None: ...

    def delete_document(self, collection: str, doc_id: str) -> None: ...

    def collections(self) -> list[str]: ...

    def search(self, vector: Sequence[float], k: int,
               collections: Sequence[str]) -> list[tuple[float, VectorRecord]]: ...


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._records: dict[str, list[VectorRecord]] = {}

    def replace_document(self, collection: str, doc_id: str,
                         records: Sequence[VectorRecord]) -> None:
        kept = [r for r in self._records.get(collection, []) if r.doc_id != doc_id]
        self._records[collection] = kept + list(records)

    def delete_document(self, collection: str, doc_id: str) -> None:
        self.replace_document(collection, doc_id, [])

    def collections(self) -> list[str]:
        return [c for c, recs in self._records.items() if recs]

    def count(self, collection: str | None = None) -> int:
        if collection is not None:
            return len(self._records.get(collection, []))
        return sum(len(v) for v in self._records.values())

    def search(self, vector: Sequence[float], k: int,
               collections: Sequence[str]) -> list[tuple[float, VectorRecord]]:
        scored = [(sum(a * b for a, b in zip(vector, r.vector)), r)
                  for c in collections for r in self._records.get(c, [])]
        scored.sort(key=lambda sr: (-sr[0], sr[1].doc_id, sr[1].section, sr[1].ordinal))
        return scored[:k]


_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(v):.7g}" for v in vector) + "]"


class PgVectorStore:
    """pgvector-backed store. ``connect`` returns a DB-API connection (psycopg 3)."""

    def __init__(self, connect: Callable[[], Any], dimensions: int,
                 table: str = "rag_chunks") -> None:
        if not _IDENT.match(table):
            raise ValueError("table name must be a plain lowercase identifier")
        self._connect = connect
        self.dimensions = dimensions
        self.table = table
        self._ready = False

    @classmethod
    def from_url(cls, url: str, dimensions: int, table: str = "rag_chunks") -> PgVectorStore:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("pgvector store needs the 'postgres' extra (psycopg)") from exc
        return cls(lambda: psycopg.connect(url), dimensions, table)

    def schema_sql(self) -> list[str]:
        t = self.table
        return [
            "CREATE EXTENSION IF NOT EXISTS vector",
            f"CREATE TABLE IF NOT EXISTS {t} (id text PRIMARY KEY, collection text NOT NULL, "
            f"doc_id text NOT NULL, section text NOT NULL, heading text NOT NULL, "
            f"ordinal integer NOT NULL, body text NOT NULL, "
            f"embedding vector({int(self.dimensions)}) NOT NULL)",
            f"CREATE INDEX IF NOT EXISTS {t}_doc ON {t} (collection, doc_id)",
            f"CREATE INDEX IF NOT EXISTS {t}_hnsw ON {t} USING hnsw (embedding vector_cosine_ops)",
        ]

    def _ensure(self, conn: Any) -> None:
        if self._ready:
            return
        with conn.cursor() as cur:
            for stmt in self.schema_sql():
                cur.execute(stmt)
        conn.commit()
        self._ready = True

    def replace_document(self, collection: str, doc_id: str,
                         records: Sequence[VectorRecord]) -> None:
        with self._connect() as conn:
            self._ensure(conn)
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {self.table} WHERE collection = %s AND doc_id = %s",
                            (collection, doc_id))
                for r in records:
                    cur.execute(
                        f"INSERT INTO {self.table} (id, collection, doc_id, section, heading, "
                        f"ordinal, body, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s, "
                        f"%s::vector)",
                        (r.id, r.collection, r.doc_id, r.section, r.heading, r.ordinal, r.text,
                         vector_literal(r.vector)))
            conn.commit()

    def delete_document(self, collection: str, doc_id: str) -> None:
        self.replace_document(collection, doc_id, [])

    def collections(self) -> list[str]:
        with self._connect() as conn:
            self._ensure(conn)
            with conn.cursor() as cur:
                cur.execute(f"SELECT DISTINCT collection FROM {self.table} ORDER BY collection")
                return [row[0] for row in cur.fetchall()]

    def search(self, vector: Sequence[float], k: int,
               collections: Sequence[str]) -> list[tuple[float, VectorRecord]]:
        literal = vector_literal(vector)
        with self._connect() as conn:
            self._ensure(conn)
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT collection, doc_id, section, heading, ordinal, body, "
                    f"1 - (embedding <=> %s::vector) AS score FROM {self.table} "
                    f"WHERE collection = ANY(%s) ORDER BY embedding <=> %s::vector, id LIMIT %s",
                    (literal, list(collections), literal, int(k)))
                rows = cur.fetchall()
        return [(float(row[6]), VectorRecord(row[0], row[1], row[2], row[3], int(row[4]),
                                             row[5], ())) for row in rows]
