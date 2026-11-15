"""Retrieval-augmented generation service.

    documents.py   load markdown, plain text and PDF text into sections
    chunking.py    split a section into overlapping word windows
    embedders.py   Embedder protocol: hashing (offline default), sentence-transformers
    store.py       VectorStore protocol: in-memory (default), pgvector (Postgres)
    service.py     RAGService: ingest, search with a similarity floor, citations
"""

from analytics_assistant.rag.chunking import chunk_text
from analytics_assistant.rag.documents import Document, Section, load_markdown, load_path
from analytics_assistant.rag.embedders import Embedder, HashingEmbedder, build_embedder
from analytics_assistant.rag.service import RAGService, select_collections
from analytics_assistant.rag.store import InMemoryVectorStore, VectorRecord, VectorStore

__all__ = ["Document", "Section", "load_markdown", "load_path", "chunk_text", "Embedder",
           "HashingEmbedder", "build_embedder", "RAGService", "select_collections",
           "InMemoryVectorStore", "VectorRecord", "VectorStore"]
