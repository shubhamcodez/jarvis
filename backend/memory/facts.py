"""Structured facts with use-count and time decay (exact lookup, not embeddings)."""
from __future__ import annotations

import heapq
import json
import math
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from config import data_root

_LOCK = threading.Lock()
_WORD = re.compile(r"[a-z0-9]{3,}")
_HALF_LIFE_DAYS = 30.0
_CACHE: Optional[list[dict[str, Any]]] = None
_CACHE_MTIME: Optional[int] = None


def _path() -> Path:
    d = data_root() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d / "facts.json"


def _load() -> list[dict[str, Any]]:
    global _CACHE, _CACHE_MTIME
    path = _path()
    if not path.exists():
        _CACHE = []
        _CACHE_MTIME = None
        return _CACHE
    try:
        mt = path.stat().st_mtime_ns
    except OSError:
        mt = None
    if _CACHE is not None and mt is not None and mt == _CACHE_MTIME:
        return _CACHE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            _CACHE = [x for x in data if isinstance(x, dict)]
            _CACHE_MTIME = mt
            return _CACHE
    except (OSError, json.JSONDecodeError):
        pass
    _CACHE = []
    _CACHE_MTIME = mt
    return _CACHE


def _save(items: list[dict[str, Any]]) -> None:
    global _CACHE, _CACHE_MTIME
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    _CACHE = items
    try:
        _CACHE_MTIME = path.stat().st_mtime_ns
    except OSError:
        _CACHE_MTIME = None


def _score(item: dict[str, Any], now: Optional[float] = None) -> float:
    now = now or time.time()
    age_days = max(0.0, (now - float(item.get("last_used") or item.get("created_at") or now)) / 86400.0)
    decay = 0.5 ** (age_days / _HALF_LIFE_DAYS)
    uses = math.log1p(float(item.get("uses") or 0))
    conf = float(item.get("confidence") or 0.7)
    return conf * decay * (1.0 + uses)


def add_fact(
    text: str,
    *,
    key: str = "",
    source: str = "user",
    confidence: float = 0.8,
) -> Optional[dict[str, Any]]:
    body = (text or "").strip()
    if not body:
        return None
    now = time.time()
    item = {
        "id": "fct_" + uuid.uuid4().hex[:10],
        "key": (key or "")[:80],
        "text": body[:500],
        "source": (source or "user")[:40],
        "confidence": max(0.1, min(1.0, float(confidence))),
        "uses": 0,
        "created_at": now,
        "last_used": now,
    }
    with _LOCK:
        items = _load()
        if item["key"]:
            items = [x for x in items if x.get("key") != item["key"]]
        else:
            low = item["text"].lower()
            items = [x for x in items if (x.get("text") or "").lower() != low]
        items.insert(0, item)
        _save(items[:400])
    return item


def fact_count() -> int:
    with _LOCK:
        return len(_load())


def list_facts(limit: int = 40) -> list[dict[str, Any]]:
    now = time.time()
    cap = max(1, min(200, int(limit)))
    with _LOCK:
        items = _load()
    if len(items) <= cap:
        return sorted(items, key=lambda x: _score(x, now), reverse=True)
    return heapq.nlargest(cap, items, key=lambda x: _score(x, now))


def delete_fact(fact_id: str) -> bool:
    fid = (fact_id or "").strip()
    if not fid:
        return False
    with _LOCK:
        items = _load()
        nxt = [x for x in items if x.get("id") != fid]
        if len(nxt) == len(items):
            return False
        _save(nxt)
    return True


def retrieve_facts(query: str, *, limit: int = 8) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    words = set(_WORD.findall(q)) if q else set()
    if not words:
        return []
    now = time.time()
    with _LOCK:
        items = _load()
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in items:
            text = (item.get("text") or "").lower()
            key = (item.get("key") or "").lower()
            overlap = len(words & set(_WORD.findall(text + " " + key)))
            if overlap == 0:
                continue
            base = _score(item, now)
            scored.append((base + 2.5 * overlap, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        picked = [item for _, item in scored[: max(1, min(20, int(limit)))]]
        now2 = time.time()
        ids = {item.get("id") for item in picked}
        for item in items:
            if item.get("id") in ids:
                item["uses"] = int(item.get("uses") or 0) + 1
                item["last_used"] = now2
        return [dict(item) for item in picked]


def format_facts_for_prompt(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    lines = ["KNOWN FACTS (exact; prefer these over guesses):"]
    for item in items:
        lines.append(f"- {item.get('text')}")
    return "\n".join(lines)
