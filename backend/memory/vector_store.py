"""Persistent vector store with hybrid (cosine + lexical + recency) ranking."""
from __future__ import annotations

import json
import math
import re
import threading
import time
from pathlib import Path
from typing import List, Optional

from .schemas import Chunk, SearchResult

_WORD = re.compile(r"[a-z0-9]{3,}")
_SOURCE_BOOST = {
    "decision": 0.08,
    "fact": 0.08,
    "note": 0.05,
    "summary": 0.04,
    "chat": 0.0,
    "code": 0.02,
    "doc": 0.03,
}


def _cosine_sim(a: List[float], b: List[float]) -> float:
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _lexical_overlap(query: str, text: str) -> float:
    q = set(_WORD.findall((query or "").lower()))
    if not q:
        return 0.0
    t = set(_WORD.findall((text or "").lower()))
    return len(q & t) / len(q)


def _store_path() -> Path:
    from config import data_root

    d = data_root() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d / "vector_store.jsonl"


class VectorStore:
    """
    Upserts by chunk_id. Persists under data_root()/memory so restart keeps retrieval.
    Hybrid search: 0.62 cosine + 0.26 lexical + 0.08 recency + source boost.
    """

    def __init__(self, *, max_chunks: int = 4000) -> None:
        self._chunks: List[Chunk] = []
        self._embeddings: List[List[float]] = []
        self._index: dict[str, int] = {}
        self._dim: Optional[int] = None
        self._max_chunks = max_chunks
        self._dirty = False
        self._lock = threading.Lock()

    def add(self, chunk: Chunk, embedding: List[float]) -> None:
        if not embedding:
            return
        with self._lock:
            if self._dim is None:
                self._dim = len(embedding)
            elif len(embedding) != self._dim:
                return
            meta = dict(chunk.metadata or {})
            meta.setdefault("ingested_at", time.time())
            chunk.metadata = meta
            existing = self._index.get(chunk.chunk_id)
            if existing is not None:
                self._chunks[existing] = chunk
                self._embeddings[existing] = embedding
            else:
                self._index[chunk.chunk_id] = len(self._chunks)
                self._chunks.append(chunk)
                self._embeddings.append(embedding)
            if len(self._chunks) > self._max_chunks:
                drop = len(self._chunks) - self._max_chunks
                self._chunks = self._chunks[drop:]
                self._embeddings = self._embeddings[drop:]
                self._index = {c.chunk_id: i for i, c in enumerate(self._chunks)}
            self._dirty = True

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        min_score: Optional[float] = None,
        source_types: Optional[List[str]] = None,
        query_text: str = "",
    ) -> List[SearchResult]:
        with self._lock:
            if not self._chunks or not query_embedding:
                return []
            if self._dim is not None and len(query_embedding) != self._dim:
                return []
            now = time.time()
            st_set = set(source_types) if source_types is not None else None
            scored: list[tuple[int, float]] = []
            for i, emb in enumerate(self._embeddings):
                c = self._chunks[i]
                if st_set is not None and c.source_type not in st_set:
                    continue
                cosine = _cosine_sim(query_embedding, emb)
                lex = _lexical_overlap(query_text, f"{c.content} {c.summary or ''}") if query_text else 0.0
                ingested = float((c.metadata or {}).get("ingested_at") or 0.0)
                age_days = max(0.0, (now - ingested) / 86400.0) if ingested else 30.0
                recency = 0.5 ** (age_days / 21.0)
                boost = _SOURCE_BOOST.get(c.source_type, 0.0)
                score = 0.62 * cosine + 0.26 * lex + 0.08 * recency + boost
                if min_score is not None and score < min_score:
                    continue
                scored.append((i, score))
            scored.sort(key=lambda x: -x[1])
            top = scored[:top_k]
            out: List[SearchResult] = []
            for idx, score in top:
                c = self._chunks[idx]
                out.append(
                    SearchResult(
                        chunk_id=c.chunk_id,
                        score=round(score, 4),
                        summary=c.summary,
                        metadata=c.metadata.copy(),
                        raw_content=c.content,
                        source_type=c.source_type,
                        source_id=c.source_id,
                    )
                )
            return out

    def persist(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            path = _store_path()
            tmp = path.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for chunk, emb in zip(self._chunks, self._embeddings):
                    rec = {
                        "chunk": {
                            "chunk_id": chunk.chunk_id,
                            "content": chunk.content,
                            "source_type": chunk.source_type,
                            "source_id": chunk.source_id,
                            "summary": chunk.summary,
                            "metadata": chunk.metadata,
                        },
                        "embedding": [round(float(x), 6) for x in emb],
                    }
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            tmp.replace(path)
            self._dirty = False

    def load(self) -> int:
        path = _store_path()
        if not path.exists():
            return 0
        n = 0
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                raw = rec.get("chunk") or {}
                emb = rec.get("embedding") or []
                if not raw.get("chunk_id") or not emb:
                    continue
                chunk = Chunk(
                    chunk_id=str(raw["chunk_id"]),
                    content=str(raw.get("content") or ""),
                    source_type=str(raw.get("source_type") or "chat"),
                    source_id=str(raw.get("source_id") or ""),
                    summary=raw.get("summary"),
                    metadata=dict(raw.get("metadata") or {}),
                )
                # Bypass add() lock/dirty: load is init-only
                if self._dim is None:
                    self._dim = len(emb)
                elif len(emb) != self._dim:
                    continue
                existing = self._index.get(chunk.chunk_id)
                if existing is not None:
                    self._chunks[existing] = chunk
                    self._embeddings[existing] = emb
                else:
                    self._index[chunk.chunk_id] = len(self._chunks)
                    self._chunks.append(chunk)
                    self._embeddings.append(emb)
                n += 1
        except OSError:
            return 0
        self._dirty = False
        return n

    def __len__(self) -> int:
        return len(self._chunks)
