"""Always-loaded identity files: SOUL.md, USER.md, MEMORY.md."""
from __future__ import annotations

from pathlib import Path

from config import data_root

_DEFAULT_SOUL = """# SOUL

Jarvis is a local assistant. Be direct, precise, and useful.

- Prefer doing the work over describing it — unless Plan or Draft mode is on.
- You have a desktop GUI agent (screenshots + mouse/keyboard). Never claim you cannot see or click the user's screen.
- Write facts down (MEMORY.md / facts) instead of relying on chat history.
- Do not send, delete, publish, or run destructive commands without a confirmed gate.
- When unsure, say so and name the missing source.
"""

_DEFAULT_USER = """# USER

Fill this in (Settings → Identity, or `/introduce`).

- Name:
- Timezone:
- How they like replies:
- Current projects:
"""

_DEFAULT_MEMORY = """# MEMORY

Curated long-term notes. Facts that must stay exact also live in the fact store.

## Preferences

## Decisions

## Standing goals
"""


def identity_dir() -> Path:
    d = data_root() / "identity"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure(name: str, default: str) -> Path:
    path = identity_dir() / name
    if not path.exists():
        path.write_text(default, encoding="utf-8")
    return path


def read_identity() -> dict[str, str]:
    soul = _ensure("SOUL.md", _DEFAULT_SOUL).read_text(encoding="utf-8")
    user = _ensure("USER.md", _DEFAULT_USER).read_text(encoding="utf-8")
    memory = _ensure("MEMORY.md", _DEFAULT_MEMORY).read_text(encoding="utf-8")
    return {"soul": soul, "user": user, "memory": memory}


def write_identity(*, soul: str | None = None, user: str | None = None, memory: str | None = None) -> dict[str, str]:
    if soul is not None:
        _ensure("SOUL.md", _DEFAULT_SOUL).write_text(soul, encoding="utf-8")
    if user is not None:
        _ensure("USER.md", _DEFAULT_USER).write_text(user, encoding="utf-8")
    if memory is not None:
        _ensure("MEMORY.md", _DEFAULT_MEMORY).write_text(memory, encoding="utf-8")
    return read_identity()


def format_identity_for_prompt(max_chars: int = 8000) -> str:
    data = read_identity()
    parts = []
    if data.get("soul"):
        parts.append(data["soul"].strip())
    if data.get("user"):
        parts.append(data["user"].strip())
    mem = (data.get("memory") or "").strip()
    if mem and mem != _DEFAULT_MEMORY.strip():
        parts.append(mem)
    text = "\n\n".join(parts).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text
