"""Memory schemas: chunks, search results, working state."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Chunk:
    """A stored unit for retrieval: raw content plus metadata and optional summary."""

    chunk_id: str
    content: str
    source_type: str  # "chat" | "code" | "doc" | "note" | "summary" | "decision"
    source_id: str  # e.g. chat_id, file path, doc id
    summary: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Embedding is stored separately in the vector store; not part of logical chunk.


@dataclass
class SearchResult:
    """One hit from vector search: chunk identity, score, and content for injection."""

    chunk_id: str
    score: float
    summary: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_content: Optional[str] = None  # Populated for top-k when injecting into prompt
    source_type: str = "chat"
    source_id: str = ""


@dataclass
class WorkingState:
    """Current task context: active topic, files, recent decisions, unresolved questions."""

    current_task: Optional[str] = None
    active_files: list[str] = field(default_factory=list)
    recent_decisions: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    last_retrieved_chunk_ids: list[str] = field(default_factory=list)
