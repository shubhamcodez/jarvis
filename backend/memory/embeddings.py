"""Embeddings for retrieval: query and chunk vectors. Uses OpenAI embeddings (required for memory)."""
from __future__ import annotations

import hashlib
import threading
import time
from typing import List

from openai import OpenAI

# Same model for query and chunks for correct similarity
EMBEDDING_MODEL = "text-embedding-3-small"
_MAX_CHARS = 24_000
_CLIENTS: dict[str, OpenAI] = {}
_CLIENT_LOCK = threading.Lock()
_EMBED_CACHE: dict[str, tuple[float, List[float]]] = {}
_EMBED_TTL = 45.0
_EMBED_MAX = 48


def _client(api_key: str) -> OpenAI:
    key = api_key or ""
    with _CLIENT_LOCK:
        cached = _CLIENTS.get(key)
        if cached is not None:
            return cached
        created = OpenAI(api_key=api_key, timeout=30.0)
        _CLIENTS[key] = created
        return created


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
    out: List[List[float]] = [[] for _ in texts]
    batch = 64
    for start in range(0, len(cleaned), batch):
        piece = cleaned[start : start + batch]
        try:
            resp = client.embeddings.create(input=piece, model=EMBEDDING_MODEL)
        except Exception:
            continue
        by_index = {obj.index: obj.embedding for obj in resp.data}
        for local_i, orig_i in enumerate(keep[start : start + batch]):
            vec = by_index.get(local_i)
            if vec:
                out[orig_i] = vec
    return out


def embed_single(api_key: str, text: str) -> List[float]:
    """Convenience: embed one string; returns single vector."""
    body = (text or "").strip()
    if not body:
        return []
    cache_key = hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest()
    now = time.monotonic()
    hit = _EMBED_CACHE.get(cache_key)
    if hit and now - hit[0] < _EMBED_TTL:
        return hit[1]
    vectors = embed_texts(api_key, [body])
    vec = vectors[0] if vectors else []
    if vec:
        _EMBED_CACHE[cache_key] = (now, vec)
        if len(_EMBED_CACHE) > _EMBED_MAX:
            _EMBED_CACHE.pop(next(iter(_EMBED_CACHE)))
    return vec
