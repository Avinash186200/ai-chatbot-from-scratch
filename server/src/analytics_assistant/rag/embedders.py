"""Embedders behind one small protocol.

* ``HashingEmbedder`` (default): a dependency-free, deterministic feature-
  hashing bag of words with stopwords removed. Good enough to rank a small
  corpus by shared vocabulary; it does not know synonyms.
* ``SentenceTransformerEmbedder`` (optional extra ``embeddings``): a local
  sentence-transformers model, loaded lazily. It downloads weights on first
  use, so it is never the default and never used in tests.

Every embedder returns unit-length vectors, so a dot product is a cosine.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

_TOKEN = re.compile(r"[a-z0-9]+")
# Identifiers such as SNAPSHOT_UNAVAILABLE or forecast_series are also kept
# whole, so a query naming one matches the text that defines it rather than
# every text that uses its parts.
_IDENTIFIER = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)+")
# Function words carry no topic; left in, they dominate a hashed bag of words
# and the longest document wins every short query.
STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from", "has",
     "how", "i", "in", "is", "it", "its", "of", "on", "or", "so", "that", "the", "then", "this",
     "to", "was", "what", "when", "which", "who", "why", "will", "with", "work", "works"])


class Embedder(Protocol):
    name: str
    dimensions: int

    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    name = "hashing"

    def __init__(self, dimensions: int = 512) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        lowered = text.lower()
        for token in _TOKEN.findall(lowered) + _IDENTIFIER.findall(lowered):
            if token in STOPWORDS:
                continue
            h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
            vec[h % self.dimensions] += 1.0 if (h >> 63) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class SentenceTransformerEmbedder:
    name = "sentence-transformers"

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("AA_EMBEDDER=sentence-transformers needs the 'embeddings' "
                               "extra (pip install sentence-transformers)") from exc
        self._model = SentenceTransformer(model_name)
        self.dimensions = int(self._model.get_sentence_embedding_dimension())

    def embed(self, text: str) -> list[float]:
        return [float(x) for x in self._model.encode(text, normalize_embeddings=True)]


def build_embedder(kind: str = "hashing", model_name: str = "all-MiniLM-L6-v2") -> Embedder:
    if kind == "hashing":
        return HashingEmbedder()
    if kind == "sentence-transformers":
        return SentenceTransformerEmbedder(model_name)
    raise ValueError(f"unknown AA_EMBEDDER {kind!r}")
