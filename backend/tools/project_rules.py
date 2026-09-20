"""Load project instruction files (ADA.md / AGENTS.md / CLAUDE.md) into context."""
from __future__ import annotations

from pathlib import Path

from config import get_workspace_root

_RULE_NAMES = (
    "ADA.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".ada/rules.md",
    ".ada/ADA.md",
)


def load_project_rules(max_chars: int = 8000, workspace_root: str | None = None) -> str:
    raw = (workspace_root or get_workspace_root() or "").strip()
    if not raw:
        return ""
    root = Path(raw)
    if not root.is_dir():
        return ""
    chunks: list[str] = []
    used = 0
    for name in _RULE_NAMES:
        p = root / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        block = f"### {name}\n{text}"
        if used + len(block) > max_chars:
            remain = max_chars - used - 20
            if remain > 200:
                chunks.append(block[:remain] + "\n…")
            break
        chunks.append(block)
        used += len(block)
    if not chunks:
        return ""
    return "PROJECT RULES (authoritative; follow these):\n\n" + "\n\n".join(chunks)
