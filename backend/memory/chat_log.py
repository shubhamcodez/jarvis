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
    with _LOCK:
        paths = [p for p in root.glob(f"*.{CHAT_EXT}")]
        for p in paths:
            if p.stem.lower() in RESERVED_CHAT_STEMS or not is_valid_chat_id(p.stem):
                continue
            try:
                data = _load(p)
                if not isinstance(data.get("messages"), list):
                    continue
                title = data.get("title") or _title_from_messages(data.get("messages", []))
                entries.append(
                    {
                        "id": p.stem,
                        "title": title or "New chat",
                        "parent_id": data.get("parent_id") or "",
                        "branch_label": data.get("branch_label") or "",
                        "fork_from_index": data.get("fork_from_index"),
                    }
                )
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
    with _LOCK:
        if not path.exists():
            return []
        data = _load(path)
    return [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in data.get("messages", [])]


def rename_chat(chat_id: str, title: str = "") -> dict:
    """Set a session title (Claude Code /rename). Empty title auto-generates from history."""
    with _LOCK:
        path = _chat_path(chat_id)
        if not path.exists():
            return {"ok": False, "error": "unknown chat"}
        data = _load(path)
        raw = (title or "").strip()
        if raw:
            data["title"] = raw[:CHAT_TITLE_MAX_LEN]
        else:
            data["title"] = _title_from_messages(data.get("messages") or [])
        _save(path, data)
        return {"ok": True, "id": chat_id, "title": data.get("title") or "New chat"}


def export_chat_markdown(chat_id: str) -> dict:
    """Full transcript as Markdown (OpenHands / Claude /export)."""
    path = _chat_path(chat_id)
    with _LOCK:
        if not path.exists():
            return {"ok": False, "error": "unknown chat"}
        data = _load(path)
    title = data.get("title") or _title_from_messages(data.get("messages") or []) or "chat"
    lines = [f"# {title}", "", f"id: `{chat_id}`", ""]
    if data.get("parent_id"):
        lines.append(f"parent: `{data.get('parent_id')}`")
        lines.append("")
    for m in data.get("messages") or []:
        role = m.get("role") or "user"
        text = (m.get("content") or "").rstrip()
        lines.append(f"## {role}")
        lines.append(text or "_(empty)_")
        lines.append("")
    return {
        "ok": True,
        "id": chat_id,
        "title": title,
        "filename": f"ada-{chat_id}.md",
        "markdown": "\n".join(lines).strip() + "\n",
        "message_count": len(data.get("messages") or []),
    }


def last_assistant_text(chat_id: str, nth: int = 1) -> dict:
    """nth=1 is the latest assistant reply."""
    msgs = read_chat_log(chat_id)
    assistants = [m.get("content") or "" for m in msgs if m.get("role") == "assistant"]
    if not assistants:
        return {"ok": False, "error": "no assistant replies"}
    idx = max(1, int(nth or 1))
    if idx > len(assistants):
        return {"ok": False, "error": f"only {len(assistants)} assistant replies"}
    return {"ok": True, "text": assistants[-idx], "nth": idx, "total": len(assistants)}


def chat_context_stats(chat_id: str) -> dict:
    from memory.tokens import estimate_tokens

    msgs = read_chat_log(chat_id)
    by_role: dict[str, int] = {}
    total = 0
    for m in msgs:
        role = m.get("role") or "user"
        n = estimate_tokens(m.get("content") or "")
        by_role[role] = by_role.get(role, 0) + n
        total += n
    return {
        "ok": True,
        "messages": len(msgs),
        "tokens": total,
        "by_role": by_role,
    }


def get_chat_meta(chat_id: str) -> dict:
    path = _chat_path(chat_id)
    with _LOCK:
        if not path.exists():
            return {"ok": False, "error": "unknown chat"}
        data = _load(path)
    return {
        "ok": True,
        "id": data.get("id") or chat_id,
        "title": data.get("title") or "New chat",
        "parent_id": data.get("parent_id") or "",
        "branch_label": data.get("branch_label") or "",
        "fork_from_index": data.get("fork_from_index"),
        "branches": list(data.get("branches") or []),
        "message_count": len(data.get("messages") or []),
        "goal_condition": data.get("goal_condition") or "",
        "plan": list(data.get("plan") or []),
    }


def set_chat_plan(chat_id: str, plan) -> dict:
    with _LOCK:
        path = _chat_path(chat_id)
        if not path.exists():
            return {"ok": False, "error": "unknown chat"}
        data = _load(path)
        clean = []
        for it in (plan or [])[:16]:
            if not isinstance(it, dict):
                continue
            clean.append(
                {
                    "id": str(it.get("id") or len(clean) + 1),
                    "status": str(it.get("status") or "pending")[:20],
                    "text": str(it.get("text") or "")[:300],
                }
            )
        data["plan"] = clean
        _save(path, data)
    return get_chat_meta(chat_id)


def set_chat_goal(chat_id: str, condition: str) -> dict:
    with _LOCK:
        path = _chat_path(chat_id)
        if not path.exists():
            return {"ok": False, "error": "unknown chat"}
        data = _load(path)
        data["goal_condition"] = (condition or "").strip()[:400]
        _save(path, data)
    return get_chat_meta(chat_id)


def fork_chat(parent_id: str, message_index: int, label: str = "") -> dict:
    """Copy messages through message_index into a new child chat."""
    global _current_path
    with _LOCK:
        parent_path = _chat_path(parent_id)
        if not parent_path.exists():
            return {"ok": False, "error": "unknown parent chat"}
        parent = _load(parent_path)
        msgs = list(parent.get("messages") or [])
        if not msgs:
            return {"ok": False, "error": "parent chat is empty"}
        idx = max(0, min(int(message_index), len(msgs) - 1))
        prefix = [dict(m) for m in msgs[: idx + 1]]
        root = chats_dir()
        child_id = _new_chat_id(root)
        child_path = root / f"{child_id}.{CHAT_EXT}"
        tag = (label or "").strip() or f"fork@{idx}"
        child = {
            "id": child_id,
            "title": f"{tag} · {parent.get('title') or 'chat'}"[:CHAT_TITLE_MAX_LEN],
            "messages": prefix,
            "agent_session_ids": [],
            "parent_id": parent_id,
            "fork_from_index": idx,
            "branch_label": tag,
            "branches": [],
        }
        branches = list(parent.get("branches") or [])
        branches.append(child_id)
        parent["branches"] = branches
        _save(parent_path, parent)
        _save(child_path, child)
        _current_path = child_path
        return {
            "ok": True,
            "id": child_id,
            "parent_id": parent_id,
            "fork_from_index": idx,
            "branch_label": tag,
            "title": child["title"],
        }


def list_branches(chat_id: str) -> list[dict]:
    meta = get_chat_meta(chat_id)
    if not meta.get("ok"):
        return []
    root_id = meta.get("parent_id") or chat_id
    try:
        root_meta = get_chat_meta(root_id)
    except Exception:
        root_meta = meta
    ids = [root_id] + list(root_meta.get("branches") or [])
    seen: set[str] = set()
    out: list[dict] = []
    for cid in ids:
        if cid in seen:
            continue
        seen.add(cid)
        try:
            info = get_chat_meta(cid)
        except Exception:
            continue
        if info.get("ok"):
            out.append(info)
    return out


def rewind_chat(chat_id: str, keep_count: Optional[int] = None) -> dict:
    """Drop trailing messages. keep_count=None removes the last assistant turn (or last pair)."""
    with _LOCK:
        path = _chat_path(chat_id)
        if not path.exists():
            return {"ok": False, "error": "unknown chat"}
        data = _load(path)
        msgs = list(data.get("messages") or [])
        before = len(msgs)
        if not msgs:
            return {"ok": False, "error": "chat is empty"}
        if keep_count is not None:
            keep = max(0, min(int(keep_count), len(msgs)))
            msgs = msgs[:keep]
        else:
            if msgs[-1].get("role") == "assistant":
                msgs = msgs[:-1]
            elif len(msgs) >= 2 and msgs[-1].get("role") == "user" and msgs[-2].get("role") == "assistant":
                msgs = msgs[:-2]
            else:
                msgs = msgs[:-1]
        data["messages"] = msgs
        _save(path, data)
        return {"ok": True, "kept": len(msgs), "dropped": before - len(msgs)}


def merge_branch(source_id: str, target_id: str = "") -> dict:
    """Append post-fork messages from source onto the parent (or target)."""
    with _LOCK:
        source_path = _chat_path(source_id)
        if not source_path.exists():
            return {"ok": False, "error": "unknown source chat"}
        source = _load(source_path)
        dest_id = (target_id or source.get("parent_id") or "").strip()
        if not dest_id:
            return {"ok": False, "error": "source has no parent to merge into"}
        dest_path = _chat_path(dest_id)
        if not dest_path.exists():
            return {"ok": False, "error": "unknown target chat"}
        dest = _load(dest_path)
        fork_idx = int(source.get("fork_from_index") or 0)
        extra = list(source.get("messages") or [])[fork_idx + 1 :]
        blocks = [
            f"# Merged branch `{source.get('branch_label') or source_id}`",
            f"From chat `{source_id}` into `{dest_id}`.",
            "",
        ]
        copied = 0
        for m in extra:
            role = m.get("role") or "assistant"
            if role == "tool":
                continue
            text = (m.get("content") or "").strip()
            if not text:
                continue
            blocks.append(f"### {role}")
            blocks.append(text[:2500])
            blocks.append("")
            copied += 1
        if copied == 0:
            blocks.append("_No new messages after the fork point._")
        dest.setdefault("messages", []).append({"role": "assistant", "content": "\n".join(blocks).strip()})
        _save(dest_path, dest)
        return {
            "ok": True,
            "source_id": source_id,
            "target_id": dest_id,
            "merged": copied,
        }
