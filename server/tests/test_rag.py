"""RAG service: document loading, chunking, embedders, vector stores, floor and citations."""

import pytest

from analytics_assistant.config import Settings
from analytics_assistant.rag import (
    HashingEmbedder,
    InMemoryVectorStore,
    RAGService,
    build_embedder,
    chunk_text,
    load_markdown,
    load_path,
)
from analytics_assistant.rag.store import PgVectorStore, VectorRecord, vector_literal
from analytics_assistant.web import build_knowledge

MD = """# Guide

Intro text before any section.

## Alpha topic {#alpha}

Alpha explains widgets and gizmos.

```
## not a heading inside code
```

## Beta topic

Beta covers sprockets.

## Beta topic

A second section with the same heading.
"""


def test_markdown_sections_ids_and_title():
    doc = load_markdown(MD, "guide.md")
    assert doc.title == "Guide"
    assert [s.section_id for s in doc.sections] == ["guide", "alpha", "beta-topic",
                                                    "beta-topic-1"]
    assert "not a heading" in doc.sections[1].text


def test_load_path_types(tmp_path):
    (tmp_path / "a.md").write_text("## One\nbody", encoding="utf-8")
    (tmp_path / "b.txt").write_text("plain words", encoding="utf-8")
    (tmp_path / "c.csv").write_text("x", encoding="utf-8")
    assert load_path(tmp_path / "a.md").sections[0].section_id == "one"
    assert load_path(tmp_path / "b.txt").sections[0].section_id == "body"
    with pytest.raises(ValueError):
        load_path(tmp_path / "c.csv")


def test_pdf_text_ingestion(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    # A one-page PDF with no text layer: loads, yields no sections (OCR is out of scope).
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    path = tmp_path / "blank.pdf"
    with path.open("wb") as fh:
        writer.write(fh)
    assert load_path(path).sections == []


def test_chunking_overlap_and_bounds():
    words = " ".join(f"w{i}" for i in range(25))
    chunks = chunk_text(words, size=10, overlap=3)
    assert chunks[0].split()[-3:] == chunks[1].split()[:3]
    assert chunks[-1].split()[-1] == "w24"
    assert chunk_text("") == []
    with pytest.raises(ValueError):
        chunk_text("x", size=5, overlap=5)


def test_hashing_embedder_is_unit_length_and_deterministic():
    e = HashingEmbedder(64)
    v = e.embed("Snapshot ids and SNAPSHOT_UNAVAILABLE")
    assert abs(sum(x * x for x in v) - 1.0) < 1e-9
    assert v == e.embed("Snapshot ids and SNAPSHOT_UNAVAILABLE")
    assert build_embedder("hashing").name == "hashing"
    with pytest.raises(ValueError):
        build_embedder("nope")


def test_service_citations_floor_and_idempotent_reingest():
    rag = RAGService(min_score=0.12, chunk_words=20, overlap_words=5)
    assert rag.ingest_markdown("docs", "guide.md", MD) == 4
    assert rag.ingest_markdown("docs", "guide.md", MD) == 4          # replaced, not doubled
    assert rag.store.count("docs") == 4
    hit = rag.search("widgets and gizmos")[0]
    assert hit["source"] == "guide.md#alpha"
    assert hit["citation"] == {"doc_id": "guide.md", "section": "alpha", "chunk": 0}
    assert hit["heading"] == "Alpha topic"
    # Nothing similar enough: no hits, so the agent says it does not know.
    assert rag.search("banana bread recipe") == []
    with pytest.raises(ValueError):
        rag.search("x", ["missing"])


def test_delete_document_and_collections():
    store = InMemoryVectorStore()
    rag = RAGService(store=store)
    rag.ingest_markdown("a", "one.md", "## S\nalpha words")
    rag.ingest_markdown("b", "two.md", "## S\nbeta words")
    assert sorted(store.collections()) == ["a", "b"]
    store.delete_document("a", "one.md")
    assert store.collections() == ["b"]
    assert rag.search("alpha", ["b"]) == []


def test_packaged_corpus_ingests():
    rag = build_knowledge(Settings())
    assert rag.search("What is a snapshot id?")[0]["source"] == "methodology.md#snapshot-id"
    assert rag.search("What does SNAPSHOT_UNAVAILABLE mean?", k=3)[1]["source"] \
        == "tool-guide.md#error-codes"


def test_docs_dir_is_ingested(tmp_path):
    (tmp_path / "extra.md").write_text("## Widget policy\nWidgets are reviewed weekly.",
                                       encoding="utf-8")
    rag = build_knowledge(Settings(docs_dir=str(tmp_path)))
    assert rag.search("how often are widgets reviewed")[0]["source"] == \
        "extra.md#widget-policy"


class FakeCursor:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    def execute(self, sql, params=None):
        self.log.append((sql, params))

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, log, rows=()):
        self.log, self.rows, self.commits = log, list(rows), 0

    def cursor(self):
        return FakeCursor(self.log, self.rows)

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_pgvector_store_sql_and_parameters():
    log = []
    rows = [("docs", "g.md", "alpha", "Alpha", 0, "text", 0.91)]
    store = PgVectorStore(lambda: FakeConn(log, rows), dimensions=3)
    rec = VectorRecord("docs", "g.md", "alpha", "Alpha", 0, "text", (0.1, 0.2, 0.3))
    store.replace_document("docs", "g.md", [rec])
    sqls = [s for s, _ in log]
    assert sqls[0] == "CREATE EXTENSION IF NOT EXISTS vector"
    assert "vector(3)" in sqls[1] and "hnsw" in sqls[3]
    assert sqls[4].startswith("DELETE FROM rag_chunks")
    insert_params = log[5][1]
    assert insert_params[0] == "docs/g.md#alpha/0" and insert_params[-1] == "[0.1,0.2,0.3]"
    out = store.search([0.1, 0.2, 0.3], 2, ["docs"])
    select_sql, params = log[-1]
    assert "<=>" in select_sql and "ANY(%s)" in select_sql
    assert params == ("[0.1,0.2,0.3]", ["docs"], "[0.1,0.2,0.3]", 2)  # values bound, not inlined
    assert out[0][0] == 0.91 and out[0][1].section == "alpha"


def test_pgvector_rejects_unsafe_table_name():
    with pytest.raises(ValueError):
        PgVectorStore(lambda: None, 3, table="x; DROP TABLE y")
    assert vector_literal([1, 0.5]) == "[1,0.5]"
