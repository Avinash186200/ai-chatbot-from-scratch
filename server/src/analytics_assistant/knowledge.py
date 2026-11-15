"""Phase-1 document index, kept for callers that ingest raw text by source name.

The server now uses ``rag.RAGService`` (markdown/PDF ingestion, sections,
pluggable embedders and vector stores, a similarity floor and citations).
``KnowledgeIndex`` stays as the smallest possible index with the same
``search(query, collections)`` signature, and the shared helpers are
re-exported from ``rag`` so old imports keep working.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from analytics_assistant.rag.chunking import chunk_text
from analytics_assistant.rag.embedders import STOPWORDS, HashingEmbedder
from analytics_assistant.rag.service import select_collections

__all__ = ["KnowledgeIndex", "Chunk", "HashingEmbedder", "STOPWORDS", "chunk_text",
           "select_collections"]


@dataclass(frozen=True)
class Chunk:
    collection: str
    source: str
    ordinal: int
    text: str
    vector: tuple[float, ...]


class KnowledgeIndex:
    def __init__(self, embedder: HashingEmbedder | None = None) -> None:
        self.embedder = embedder or HashingEmbedder()
        self.collections: dict[str, list[Chunk]] = {}

    def ingest(self, collection: str, source: str, text: str) -> int:
        """Idempotent per (collection, source): re-ingesting replaces the old chunks."""
        chunks = [c for c in self.collections.get(collection, []) if c.source != source]
        for i, piece in enumerate(chunk_text(text)):
            chunks.append(Chunk(collection, source, i, piece, tuple(self.embedder.embed(piece))))
        self.collections[collection] = chunks
        return sum(1 for c in chunks if c.source == source)

    def search(self, query: str, collections: Sequence[str] = (), k: int = 3) -> list[dict]:
        selected = select_collections(collections, self.collections)
        q = self.embedder.embed(query)
        scored = [
            (sum(a * b for a, b in zip(q, c.vector)), c)
            for name in selected for c in self.collections[name]
        ]
        scored.sort(key=lambda sc: (-sc[0], sc[1].source, sc[1].ordinal))
        return [
            {"collection": c.collection, "source": c.source, "chunk": c.ordinal,
             "score": round(s, 4), "text": c.text}
            for s, c in scored[:k]
        ]
