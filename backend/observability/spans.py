"""Hierarchical spans for a turn (local LangSmith-style, no SaaS).

turn
  ├ retrieval
  ├ tools
  ├ supervisor
  ├ llm
  └ specialist

Persisted as jsonl under jarvis-observability/traces/spans.jsonl.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator, Optional

from .config import ensure_dirs, obs_dir
from .redact import redact_text, redact_value

_CURRENT: ContextVar[Optional["Span"]] = ContextVar("ada_span", default=None)
_LOCK = threading.Lock()
SPAN_FILE = "spans.jsonl"
_MAX_BYTES = 12_000_000


def _path() -> Path:
    ensure_dirs()
    return obs_dir() / "traces" / SPAN_FILE


class Span:
    def __init__(self, name: str, *, parent: Optional["Span"] = None, **attrs: Any) -> None:
        self.span_id = uuid.uuid4().hex[:16]
        self.trace_id = parent.trace_id if parent else uuid.uuid4().hex
        self.parent_id = parent.span_id if parent else None
        self.name = name
        self.attrs = dict(attrs)
        self.started = time.time()
        self.ended: Optional[float] = None
        self.status = "ok"
        self.error: Optional[str] = None
        self.events: list[dict[str, Any]] = []

    def set(self, **attrs: Any) -> None:
        for k, v in attrs.items():
            if isinstance(v, str) and len(v) > 800:
                v = v[:799] + "…"
            self.attrs[k] = v
        if len(self.attrs) > 40:
            self.attrs = dict(list(self.attrs.items())[-40:])

    def event(self, name: str, **attrs: Any) -> None:
        self.events.append({"name": name, "ts": time.time(), **redact_value(attrs)})

    def fail(self, error: str) -> None:
        self.status = "error"
        self.error = redact_text(error, max_len=800)

    def close(self) -> None:
        if self.ended is None:
            self.ended = time.time()
        _append(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.started,
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "status": self.status,
            "error": self.error,
            "duration_ms": round(((self.ended or time.time()) - self.started) * 1000, 2),
            "attrs": redact_value(self.attrs),
            "events": self.events[-20:],
        }


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Span]:
    parent = _CURRENT.get()
    s = Span(name, parent=parent, **attrs)
    token = _CURRENT.set(s)
    try:
        yield s
    except Exception as exc:
        s.fail(str(exc))
        raise
    finally:
        s.close()
        _CURRENT.reset(token)


def current_span() -> Optional[Span]:
    return _CURRENT.get()


def current_trace_id() -> Optional[str]:
    s = _CURRENT.get()
    return s.trace_id if s else None


def _append(record: dict[str, Any]) -> None:
    try:
        path = _path()
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with _LOCK:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
            if path.stat().st_size > _MAX_BYTES:
                _rotate(path)
    except OSError:
        pass


def _rotate(path: Path) -> None:
    try:
        rotated = path.with_suffix(".jsonl.1")
        try:
            rotated.unlink(missing_ok=True)
        except TypeError:
            if rotated.exists():
                rotated.unlink()
        path.replace(rotated)
    except OSError:
        pass


def list_spans(limit: int = 200, trace_id: Optional[str] = None) -> list[dict[str, Any]]:
    from .config import read_jsonl_records

    ensure_dirs()
    rows = read_jsonl_records("traces", SPAN_FILE, limit=max(limit, 200))
    if trace_id:
        rows = [r for r in rows if r.get("trace_id") == trace_id]
    return rows[-max(1, limit) :]
