"""Load documents into sections, the unit a citation points at.

* Markdown: one section per heading (``#`` to ``####``). The section id is
  an explicit ``{#id}`` at the end of the heading if present, otherwise a
  slug of the heading text. Text before the first heading is section
  ``intro``.
* Plain text: one section, ``body``.
* PDF: one section per page (``page-1`` ...), from the PDF's text layer
  via ``pypdf`` (optional extra ``pdf``). Scanned PDFs without a text layer
  yield nothing; OCR is out of scope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_EXPLICIT_ID = re.compile(r"\s*\{#([A-Za-z0-9_-]+)\}\s*$")


@dataclass(frozen=True)
class Section:
    section_id: str
    heading: str
    text: str


@dataclass
class Document:
    doc_id: str
    title: str
    sections: list[Section] = field(default_factory=list)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def load_markdown(text: str, doc_id: str) -> Document:
    sections: list[Section] = []
    heading, section_id, lines = "", "intro", []
    title = doc_id
    seen: dict[str, int] = {}

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body:
            sid = section_id
            if sid in seen:  # duplicate headings get a numeric suffix
                seen[sid] += 1
                sid = f"{sid}-{seen[sid]}"
            else:
                seen[sid] = 0
            sections.append(Section(sid, heading, body))

    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        match = None if in_code else _HEADING.match(line)
        if match:
            flush()
            raw = match.group(2)
            explicit = _EXPLICIT_ID.search(raw)
            heading = _EXPLICIT_ID.sub("", raw).strip()
            section_id = explicit.group(1) if explicit else slugify(heading)
            if len(match.group(1)) == 1 and title == doc_id:
                title = heading
            lines = []
        else:
            lines.append(line)
    flush()
    return Document(doc_id, title, sections)


def load_text(text: str, doc_id: str) -> Document:
    body = text.strip()
    return Document(doc_id, doc_id, [Section("body", doc_id, body)] if body else [])


def load_pdf(path: Path, doc_id: str) -> Document:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # optional extra
        raise RuntimeError("PDF ingestion needs the 'pdf' extra (pypdf)") from exc
    reader = PdfReader(str(path))
    sections = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            sections.append(Section(f"page-{i}", f"Page {i}", text))
    return Document(doc_id, doc_id, sections)


def load_path(path: str | Path, doc_id: str | None = None) -> Document:
    path = Path(path)
    doc_id = doc_id or path.name
    suffix = path.suffix.lower()
    if suffix in (".md", ".markdown"):
        return load_markdown(path.read_text(encoding="utf-8"), doc_id)
    if suffix == ".txt":
        return load_text(path.read_text(encoding="utf-8"), doc_id)
    if suffix == ".pdf":
        return load_pdf(path, doc_id)
    raise ValueError(f"unsupported document type: {suffix or path.name}")
