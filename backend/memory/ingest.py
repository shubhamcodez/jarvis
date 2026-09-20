"""
Ingest chat history into the vector store: chunk conversations and embed.
Incremental by default — only new windows (unknown chunk_id) are embedded.
"""
from __future__ import annotations

import time

from .chat_log import read_chat_log
from .embeddings import embed_texts
from .schemas import Chunk
from .vector_store import VectorStore

CHAT_WINDOW_SIZE = 4
CHAT_WINDOW_STRIDE = 2


def _chunk_messages(chat_id: str, messages: list[dict]) -> list[Chunk]:
    chunks: list[Chunk] = []
    if not messages:
        return chunks
    step = max(1, CHAT_WINDOW_STRIDE)
    for i in range(0, len(messages), step):
        window = messages[i : i + CHAT_WINDOW_SIZE]
        if not window:
            continue
        parts = []
        for m in window:
            role = (m.get("role") or "user").strip()
            content = (m.get("content") or "").strip()
            if content:
                parts.append(f"{role}: {content}")
        content = "\n".join(parts)
        if not content.strip():
            continue
        chunk_id = f"{chat_id}:{i}:{i + len(window)}"
        summary = content[:200] + ("..." if len(content) > 200 else "")
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                content=content,
                source_type="chat",
                source_id=chat_id,
                summary=summary,
                metadata={"turn_start": i, "turn_end": i + len(window), "ingested_at": time.time()},
            )
        )
    return chunks


def ingest_chat(
    store: VectorStore,
    openai_api_key: str,
    chat_id: str,
    *,
    persist: bool = True,
    only_new: bool = True,
) -> int:
    """
    Chunk the chat, embed new windows, upsert. Returns chunks embedded this call.
    """
    messages = read_chat_log(chat_id)
    if not messages:
        return 0
    chunks = _chunk_messages(chat_id, messages)
    if only_new and hasattr(store, "has"):
        chunks = [c for c in chunks if not store.has(c.chunk_id)]
    if not chunks:
        return 0
    texts = [c.content for c in chunks]
    embeddings = embed_texts(openai_api_key, texts)
    added = 0
    for c, emb in zip(chunks, embeddings):
        if not emb:
            continue
        store.add(c, emb)
        added += 1
    if persist and added and hasattr(store, "persist"):
        store.persist()
    try:
        from observability.metrics import incr, observe

        incr("memory.ingest.calls")
        observe("memory.ingest.chunks", float(added))
    except Exception:
        pass
    return added
