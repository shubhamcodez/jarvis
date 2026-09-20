"""Chat log: file-based persistence, same JSON format as Rust (id, title, messages, agent_session_ids)."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from config import chats_dir

CHAT_EXT = "json"
CHAT_TITLE_MAX_LEN = 48
_CHAT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# Filenames that live (or used to live) in the chats directory but are not chats.
RESERVED_CHAT_STEMS = frozenset({"bookmarks", "tasks"})
# Slash-commands and help dumps should not become the permanent title.
_TITLE_SKIP_PREFIXES = ("/",)

_LOCK = threading.Lock()

# In-memory current chat path (per process)
_current_path: Optional[Path] = None


class InvalidChatId(ValueError):
    """Raised when a chat id is empty, reserved, or not a safe filename."""


def is_valid_chat_id(chat_id: str) -> bool:
    cid = (chat_id or "").strip()
    return bool(cid) and bool(_CHAT_ID_RE.fullmatch(cid)) and cid.lower() not in RESERVED_CHAT_STEMS


def _safe_chat_id(chat_id: str) -> str:
    cid = (chat_id or "").strip()
    if not is_valid_chat_id(cid):
        raise InvalidChatId("chat_id must be letters, digits, '_' or '-' (reserved names are not allowed)")
    return cid


def _chat_path(chat_id: str) -> Path:
    cid = _safe_chat_id(chat_id)
    root = chats_dir().resolve()
    path = (root / f"{cid}.{CHAT_EXT}").resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise InvalidChatId("chat_id resolves outside the chats directory") from exc
    return path


def _title_from_messages(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") != "user":
            continue
        t = (m.get("content") or "").strip()
        if not t:
            continue
        first = t.splitlines()[0].strip()
        if any(first.startswith(p) for p in _TITLE_SKIP_PREFIXES):
            continue
        return first[:CHAT_TITLE_MAX_LEN] + ("…" if len(first) > CHAT_TITLE_MAX_LEN else "")
    return "New chat"


def _load(path: Path) -> dict:
    if not path.exists():
        return {"id": path.stem, "title": "", "messages": [], "agent_session_ids": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"id": path.stem, "title": "", "messages": [], "agent_session_ids": []}
    if not isinstance(data, dict):
        return {"id": path.stem, "title": "", "messages": [], "agent_session_ids": []}
    data.setdefault("id", path.stem)
    data.setdefault("messages", [])
    data.setdefault("agent_session_ids", [])
    if not isinstance(data.get("messages"), list):
        data["messages"] = []
    if not data.get("title") and data.get("messages"):
        data["title"] = _title_from_messages(data["messages"])
    return data


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def append_chat_log(role: str, content: str, chat_id: Optional[str] = None) -> None:
    global _current_path
    if role not in ("user", "assistant", "tool"):
        raise ValueError("role must be 'user', 'assistant', or 'tool'")
    with _LOCK:
        root = chats_dir()
        root.mkdir(parents=True, exist_ok=True)
        if chat_id:
            path = _chat_path(chat_id)
            _current_path = path
        else:
            if _current_path is None:
                new_id = _new_chat_id(root)
                _current_path = root / f"{new_id}.{CHAT_EXT}"
            path = _current_path
        data = _load(path)
        data.setdefault("id", path.stem)
        data.setdefault("messages", []).append({"role": role, "content": (content or "").strip()})
        if not data.get("title") and role == "user":
            data["title"] = _title_from_messages(data["messages"])
        _save(path, data)


def list_chats() -> list[dict]:
    root = chats_dir()
    if not root.is_dir():
        return []
    entries = []
    for p in root.glob(f"*.{CHAT_EXT}"):
        if p.stem.lower() in RESERVED_CHAT_STEMS or not is_valid_chat_id(p.stem):
            continue
        try:
            data = _load(p)
            if not isinstance(data.get("messages"), list):
                continue
            title = data.get("title") or _title_from_messages(data.get("messages", []))
            entries.append({"id": p.stem, "title": title or "New chat"})
        except Exception:
            continue
    entries.sort(key=lambda e: e["id"], reverse=True)
    return entries


def set_current_chat(chat_id: str) -> None:
    global _current_path
    with _LOCK:
        _current_path = _chat_path(chat_id)


def get_current_chat_id() -> Optional[str]:
    if _current_path is None:
        return None
    return _current_path.stem


def _new_chat_id(root: Path) -> str:
    for _ in range(8):
        chat_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        if not (root / f"{chat_id}.{CHAT_EXT}").exists():
            return chat_id
    return uuid.uuid4().hex


def create_new_chat() -> str:
    """Create an empty chat file, set it as current, return its id."""
    global _current_path
    with _LOCK:
        root = chats_dir()
        root.mkdir(parents=True, exist_ok=True)
        chat_id = _new_chat_id(root)
        path = root / f"{chat_id}.{CHAT_EXT}"
        data = {"id": chat_id, "title": "New chat", "messages": [], "agent_session_ids": []}
        _save(path, data)
        _current_path = path
        return chat_id


def delete_chat(chat_id: str) -> bool:
    """Delete a chat by id. If it was the current chat, clear current. Returns True if deleted."""
    global _current_path
    with _LOCK:
        path = _chat_path(chat_id)
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        if _current_path is not None:
            try:
                if _current_path.resolve() == path.resolve():
                    _current_path = None
            except OSError:
                _current_path = None
        return True


def chat_exists(chat_id: str) -> bool:
    try:
        return _chat_path(chat_id).exists()
    except InvalidChatId:
        return False


def clear_current_chat() -> None:
    """Clear current chat (e.g. after storage path change)."""
    global _current_path
    with _LOCK:
        _current_path = None


def read_chat_log(chat_id: str) -> list[dict]:
    path = _chat_path(chat_id)
    if not path.exists():
        return []
    data = _load(path)
    return [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in data.get("messages", [])]
