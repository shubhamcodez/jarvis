"""Pinned assistant replies — message-level bookmarks users asked for on Grok/Cursor."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from config import chats_dir


def _path() -> Path:
    return Path(chats_dir()) / "bookmarks.json"


def list_bookmarks() -> list[dict[str, Any]]:
    p = _path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def add_bookmark(chat_id: str, content: str, title: str = "") -> dict[str, Any]:
    items = list_bookmarks()
    rec = {
        "id": f"{int(time.time() * 1000)}",
        "chat_id": chat_id or "",
        "title": (title or content or "Pin")[:80],
        "content": (content or "")[:4000],
        "created_at": time.time(),
    }
    items.insert(0, rec)
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(items[:200], ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


def remove_bookmark(bookmark_id: str) -> bool:
    items = [x for x in list_bookmarks() if str(x.get("id")) != str(bookmark_id)]
    before = list_bookmarks()
    if len(items) == len(before):
        return False
    _path().write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return True
