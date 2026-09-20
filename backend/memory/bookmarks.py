"""Pinned assistant replies — message-level bookmarks users asked for on Grok/Cursor."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from config import chats_dir, data_root

_LOCK = threading.Lock()


def _path() -> Path:
    d = data_root() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d / "bookmarks.json"


def _legacy_path() -> Path:
    return Path(chats_dir()) / "bookmarks.json"


def _atomic_write(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _migrate_legacy() -> None:
    dest = _path()
    if dest.exists():
        return
    legacy = _legacy_path()
    if not legacy.exists():
        return
    try:
        dest.write_bytes(legacy.read_bytes())
    except OSError:
        return


def list_bookmarks() -> list[dict[str, Any]]:
    with _LOCK:
        _migrate_legacy()
        p = _path()
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []


def add_bookmark(chat_id: str, content: str, title: str = "") -> dict[str, Any]:
    rec = {
        "id": f"{int(time.time() * 1000)}-{int(time.perf_counter_ns() % 10000):04d}",
        "chat_id": chat_id or "",
        "title": (title or content or "Pin")[:80],
        "content": (content or "")[:4000],
        "created_at": time.time(),
    }
    with _LOCK:
        _migrate_legacy()
        items = []
        p = _path()
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    items = data
            except (OSError, json.JSONDecodeError):
                items = []
        items.insert(0, rec)
        _atomic_write(p, items[:200])
    return rec


def remove_bookmark(bookmark_id: str) -> bool:
    with _LOCK:
        _migrate_legacy()
        p = _path()
        if not p.exists():
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            items = data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return False
        nxt = [x for x in items if str(x.get("id")) != str(bookmark_id)]
        if len(nxt) == len(items):
            return False
        _atomic_write(p, nxt)
        return True
