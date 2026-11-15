"""RAG service: ingest documents, search with a similarity floor, return citations.

A hit carries a citation ``{doc_id, section, chunk}`` and a ``source``
string ``"<doc_id>#<section>"`` that the agent quotes. A search returns no
hits when nothing scores at or above ``min_score``; the agent then says it
does not know instead of answering from a weak match.

Collections are an allowlist: a search may only name collections that
exist, and an empty selection means all of them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from analytics_assistant import telemetry
from analytics_assistant.rag.chunking import chunk_text
from analytics_assistant.rag.documents import Document, load_markdown, load_path
from analytics_assistant.rag.embedders import Embedder, HashingEmbedder
from analytics_assistant.rag.store import InMemoryVectorStore, VectorRecord, VectorStore

SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt", ".pdf")


def select_collections(requested: Sequence[str], available: Mapping[str, object] | Sequence[str]
                       ) -> list[str]:
    available = list(available)
    unknown = set(requested) - set(available)
    if unknown:
        raise ValueError(f"unknown collection(s): {', '.join(sorted(unknown))}")
    return list(dict.fromkeys(requested)) if requested else available


class RAGService:
    def __init__(self, embedder: Embedder | None = None, store: VectorStore | None = None,
                 min_score: float = 0.12, chunk_words: int = 120,
                 overlap_words: int = 30) -> None:
        self.embedder = embedder or HashingEmbedder()
        self.store = store or InMemoryVectorStore()
        self.min_score = min_score
        self.chunk_words = chunk_words
        self.overlap_words = overlap_words

    # ------------------------------------------------------------ ingestion

    def ingest_document(self, collection: str, doc: Document) -> int:
        records = []
        for section in doc.sections:
            for i, piece in enumerate(chunk_text(section.text, self.chunk_words,
                                                 self.overlap_words)):
                # The heading is embedded with every chunk: it is often the
                # only place the topic is named.
                vector = self.embedder.embed(f"{section.heading}\n{piece}")
                records.append(VectorRecord(collection, doc.doc_id, section.section_id,
                                            section.heading, i, piece, tuple(vector)))
        self.store.replace_document(collection, doc.doc_id, records)
        return len(records)

    def ingest_markdown(self, collection: str, doc_id: str, text: str) -> int:
        return self.ingest_document(collection, load_markdown(text, doc_id))

    def ingest_path(self, collection: str, path: str | Path) -> int:
        return self.ingest_document(collection, load_path(path))

    def ingest_dir(self, collection: str, folder: str | Path) -> dict[str, int]:
        out = {}
        for path in sorted(Path(folder).rglob("*")):
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
                out[path.name] = self.ingest_path(collection, path)
        return out

    # ------------------------------------------------------------ search

    def search(self, query: str, collections: Sequence[str] = (), k: int = 3,
               min_score: float | None = None) -> list[dict[str, Any]]:
        floor = self.min_score if min_score is None else min_score
        selected = select_collections(collections, self.store.collections())
        with telemetry.span("retrieval.search", {"retrieval.k": k,
                                                 "retrieval.collections": len(selected)}) as sp:
            if not selected:
                return []
            scored = self.store.search(self.embedder.embed(query), k, selected)
            hits = [{
                "collection": r.collection,
                "source": f"{r.doc_id}#{r.section}",
                "doc_id": r.doc_id,
                "section": r.section,
                "heading": r.heading,
                "chunk": r.ordinal,
                "score": round(score, 4),
                "text": r.text,
                "citation": {"doc_id": r.doc_id, "section": r.section, "chunk": r.ordinal},
            } for score, r in scored if score >= floor]
            sp.set_attribute("retrieval.hits", len(hits))
            return hits
