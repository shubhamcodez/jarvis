"""Persistent vector store with hybrid (cosine + lexical + recency) ranking."""
from __future__ import annotations

import heapq
import json
import math
import re
import threading
import time
from pathlib import Path
from typing import List, Optional, Set

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


def _vec_norm(a: List[float]) -> float:
    return math.sqrt(sum(x * x for x in a)) if a else 0.0


def _cosine_pre(a: List[float], b: List[float], na: float, nb: float) -> float:
    if not a or not b or na == 0.0 or nb == 0.0 or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _lex_tokens(text: str) -> Set[str]:
    return set(_WORD.findall((text or "").lower()))


def _cosine_sim(a: List[float], b: List[float]) -> float:
    return _cosine_pre(a, b, _vec_norm(a), _vec_norm(b))


def _lexical_overlap(query: str, text: str) -> float:
    q = _lex_tokens(query)
    if not q:
        return 0.0
    return len(q & _lex_tokens(text)) / len(q)


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
        self._norms: List[float] = []
        self._lex: List[Set[str]] = []
        self._index: dict[str, int] = {}
        self._dim: Optional[int] = None
        self._max_chunks = max_chunks
        self._dirty = False
        self._lock = threading.Lock()

    def _lex_for(self, chunk: Chunk) -> Set[str]:
        return _lex_tokens(f"{chunk.content} {chunk.summary or ''}")

    def add(self, chunk: Chunk, embedding: List[float]) -> None:
        if not embedding:
            return
        with self._lock:
            if self._dim is None:
                self._dim = len(embedding)
            elif len(embedding) != self._dim:
                import logging

                logging.getLogger("jarvis.memory").warning(
                    "drop chunk %s: embedding dim %s != store %s",
                    chunk.chunk_id,
                    len(embedding),
                    self._dim,
                )
                return
            meta = dict(chunk.metadata or {})
            meta.setdefault("ingested_at", time.time())
            chunk.metadata = meta
            existing = self._index.get(chunk.chunk_id)
            norm = _vec_norm(embedding)
            lex = self._lex_for(chunk)
            if existing is not None:
                self._chunks[existing] = chunk
                self._embeddings[existing] = embedding
                self._norms[existing] = norm
                self._lex[existing] = lex
            else:
                self._index[chunk.chunk_id] = len(self._chunks)
                self._chunks.append(chunk)
                self._embeddings.append(embedding)
                self._norms.append(norm)
                self._lex.append(lex)
            if len(self._chunks) > self._max_chunks:
                scored = []
                now = time.time()
                for i, c in enumerate(self._chunks):
                    ingested = float((c.metadata or {}).get("ingested_at") or 0.0)
                    boost = _SOURCE_BOOST.get(c.source_type, 0.0)
                    scored.append((ingested + boost * 86400.0 * 7, i))
                scored.sort()
                drop_n = len(self._chunks) - self._max_chunks
                drop_idx = set(i for _, i in scored[:drop_n])
                keep_c = []
                keep_e = []
                keep_n = []
                keep_l = []
                for i, (c, e) in enumerate(zip(self._chunks, self._embeddings)):
                    if i in drop_idx:
                        continue
                    keep_c.append(c)
                    keep_e.append(e)
                    keep_n.append(self._norms[i] if i < len(self._norms) else _vec_norm(e))
                    keep_l.append(self._lex[i] if i < len(self._lex) else self._lex_for(c))
                self._chunks = keep_c
                self._embeddings = keep_e
                self._norms = keep_n
                self._lex = keep_l
                self._index = {c.chunk_id: i for i, c in enumerate(self._chunks)}
            self._dirty = True

    def has(self, chunk_id: str) -> bool:
        with self._lock:
            return chunk_id in self._index

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
            q_words = _lex_tokens(query_text) if query_text else set()
            q_norm = _vec_norm(query_embedding)
            scored: list[tuple[float, int]] = []
            t0 = time.perf_counter()
            for i, emb in enumerate(self._embeddings):
                c = self._chunks[i]
                if st_set is not None and c.source_type not in st_set:
                    continue
                nb = self._norms[i] if i < len(self._norms) else _vec_norm(emb)
                cosine = _cosine_pre(query_embedding, emb, q_norm, nb)
                if q_words:
                    lex_set = self._lex[i] if i < len(self._lex) else self._lex_for(c)
                    lex = len(q_words & lex_set) / len(q_words)
                else:
                    lex = 0.0
                ingested = float((c.metadata or {}).get("ingested_at") or 0.0)
                age_days = max(0.0, (now - ingested) / 86400.0) if ingested else 30.0
                recency = 0.5 ** (age_days / 21.0)
                boost = _SOURCE_BOOST.get(c.source_type, 0.0)
                score = 0.62 * cosine + 0.26 * lex + 0.08 * recency + boost
                if min_score is not None and score < min_score:
                    continue
                scored.append((score, i))
            k = max(1, int(top_k))
            top_pairs = heapq.nlargest(k, scored) if len(scored) > k else sorted(scored, reverse=True)
            top = [(idx, score) for score, idx in top_pairs]
            # #region agent log
            try:
                from _perf_log import perf_log

                perf_log(
                    "vector_store.py:search",
                    "vector search",
                    {"n": len(self._chunks), "kept": len(scored), "ms": round((time.perf_counter() - t0) * 1000, 2)},
                    "D",
                )
            except Exception:
                pass
            # #endregion
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
            tmp = path.with_name(f"{path.stem}.{threading.get_ident()}.{int(time.time() * 1000)}.tmp")
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
                f.flush()
                try:
                    import os

                    os.fsync(f.fileno())
                except OSError:
                    pass
            tmp.replace(path)
            self._dirty = False

    def load(self) -> int:
        path = _store_path()
        if not path.exists():
            return 0
        n = 0
        with self._lock:
            try:
                with path.open("r", encoding="utf-8") as fh:
                    for line in fh:
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
                        if self._dim is None:
                            self._dim = len(emb)
                        elif len(emb) != self._dim:
                            import logging

                            logging.getLogger("jarvis.memory").warning(
                                "skip persisted chunk %s: dim %s != %s",
                                chunk.chunk_id,
                                len(emb),
                                self._dim,
                            )
                            continue
                        existing = self._index.get(chunk.chunk_id)
                        norm = _vec_norm(emb)
                        lex = self._lex_for(chunk)
                        if existing is not None:
                            self._chunks[existing] = chunk
                            self._embeddings[existing] = emb
                            self._norms[existing] = norm
                            self._lex[existing] = lex
                        else:
                            self._index[chunk.chunk_id] = len(self._chunks)
                            self._chunks.append(chunk)
                            self._embeddings.append(emb)
                            self._norms.append(norm)
                            self._lex.append(lex)
                        n += 1
            except OSError:
                return 0
            self._dirty = False
        return n

    def __len__(self) -> int:
        return len(self._chunks)
