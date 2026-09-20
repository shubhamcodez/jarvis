"""Embeddings for retrieval: query and chunk vectors. Uses OpenAI embeddings (required for memory)."""
from __future__ import annotations

from typing import List

from openai import OpenAI

# Same model for query and chunks for correct similarity
EMBEDDING_MODEL = "text-embedding-3-small"
_MAX_CHARS = 24_000


def _client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key, timeout=30.0)


def embed_texts(api_key: str, texts: List[str]) -> List[List[float]]:
    """
    Embed one or more texts. Uses OpenAI text-embedding-3-small.
    Returns list of embedding vectors (each is list of floats).
    Empty strings are skipped (empty vector placeholder).
    """
    if not texts:
        return []
    cleaned: list[str] = []
    keep: list[int] = []
    for i, t in enumerate(texts):
        body = (t or "").strip()
        if not body:
            continue
        cleaned.append(body[:_MAX_CHARS])
        keep.append(i)
    if not cleaned:
        return [[] for _ in texts]
    client = _client(api_key)
    resp = client.embeddings.create(input=cleaned, model=EMBEDDING_MODEL)
    by_index = {obj.index: obj.embedding for obj in resp.data}
    out: List[List[float]] = [[] for _ in texts]
    for local_i, orig_i in enumerate(keep):
        vec = by_index.get(local_i)
        if vec:
            out[orig_i] = vec
    return out


def embed_single(api_key: str, text: str) -> List[float]:
    """Convenience: embed one string; returns single vector."""
    vectors = embed_texts(api_key, [text])
    return vectors[0] if vectors else []
