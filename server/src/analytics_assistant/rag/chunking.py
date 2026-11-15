"""Split text into overlapping word windows.

Overlap keeps a sentence that straddles a boundary retrievable from either
side. Sizes are in words, not model tokens: close enough for ranking, and it
keeps chunking independent of any tokenizer. A final window shorter than
the overlap is not emitted on its own (its words are already in the
previous window).
"""

from __future__ import annotations


def chunk_text(text: str, size: int = 400, overlap: int = 80) -> list[str]:
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("need size > 0 and 0 <= overlap < size")
    words = text.split()
    if not words:
        return []
    step = size - overlap
    return [" ".join(words[i:i + size]) for i in range(0, max(1, len(words) - overlap), step)]
