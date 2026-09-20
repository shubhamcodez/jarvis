"""Filesystem working memory. Large outputs live here; the model gets a handle + summary."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from config import data_root


def artifacts_root(run_id: Optional[str] = None) -> Path:
    base = data_root() / "ada-artifacts"
    if run_id:
        base = base / run_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def write_artifact(
    name: str,
    content: str,
    *,
    run_id: Optional[str] = None,
    kind: str = "note",
) -> dict[str, Any]:
    safe = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in (name or "note"))[:80]
    if not safe:
        safe = "note"
    path = artifacts_root(run_id) / safe
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    if len(text) > 2_000_000:
        return {"ok": False, "error": "artifact exceeds 2MB limit", "name": safe}
    path.write_text(text, encoding="utf-8")
    summary = text.strip().replace("\n", " ")
    if len(summary) > 240:
        summary = summary[:237] + "…"
    return {
        "ok": True,
        "uri": f"artifact://{run_id or '_'}/{safe}",
        "path": str(path),
        "kind": kind,
        "bytes": path.stat().st_size,
        "summary": summary,
        "created_at": time.time(),
    }


def read_artifact(name: str, *, run_id: Optional[str] = None, max_chars: int = 8000) -> dict[str, Any]:
    safe = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in (name or ""))[:80]
    path = artifacts_root(run_id) / safe
    if not path.is_file():
        return {"ok": False, "error": f"artifact not found: {safe}"}
    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > max_chars
    return {
        "ok": True,
        "uri": f"artifact://{run_id or '_'}/{safe}",
        "content": text[:max_chars],
        "truncated": truncated,
    }


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]
