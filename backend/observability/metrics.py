"""In-process counters and rolling histograms (Prometheus-shaped, local JSON)."""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import OPT_DIR, ensure_dirs

_LOCK = threading.Lock()
_COUNTERS: dict[str, float] = defaultdict(float)
_SUM: dict[str, float] = defaultdict(float)
_COUNT: dict[str, int] = defaultdict(int)
_LAST_FLUSH = 0.0


def incr(name: str, value: float = 1.0, **labels: Any) -> None:
    key = _key(name, labels)
    with _LOCK:
        _COUNTERS[key] += value


def observe(name: str, value: float, **labels: Any) -> None:
    key = _key(name, labels)
    with _LOCK:
        _SUM[key] += float(value)
        _COUNT[key] += 1


def snapshot() -> dict[str, Any]:
    with _LOCK:
        hist = {}
        for k, total in _SUM.items():
            n = _COUNT.get(k) or 1
            hist[k] = {"count": n, "sum": round(total, 4), "avg": round(total / n, 4)}
        return {
            "ts": time.time(),
            "counters": dict(_COUNTERS),
            "histograms": hist,
        }


def flush(force: bool = False) -> None:
    global _LAST_FLUSH
    now = time.time()
    if not force and now - _LAST_FLUSH < 15:
        return
    ensure_dirs()
    path = OPT_DIR / "metrics.json"
    tmp = path.with_suffix(".json.tmp")
    data = snapshot()
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
        _LAST_FLUSH = now
    except OSError:
        pass


def load_latest() -> dict[str, Any]:
    path = OPT_DIR / "metrics.json"
    if not path.exists():
        return snapshot()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return snapshot()


def _key(name: str, labels: dict[str, Any]) -> str:
    if not labels:
        return name
    bits = ",".join(f"{k}={labels[k]}" for k in sorted(labels) if labels[k] is not None)
    return f"{name}{{{bits}}}"
