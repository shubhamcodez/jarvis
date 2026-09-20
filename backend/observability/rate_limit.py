"""Tiny in-process token bucket for expensive localhost routes."""
from __future__ import annotations

import time
from threading import Lock

_LOCK = Lock()
_HITS: dict[str, list[float]] = {}


def allow(key: str, *, limit: int = 12, window_sec: float = 10.0) -> bool:
    now = time.monotonic()
    with _LOCK:
        q = _HITS.setdefault(key, [])
        q[:] = [t for t in q if now - t < window_sec]
        if len(q) >= limit:
            return False
        q.append(now)
        return True
