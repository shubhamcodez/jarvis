"""Unique search-replace patches. Ambiguous matches fail instead of guessing."""
from __future__ import annotations

from typing import Any


def apply_unique_replace(source: str, old: str, new: str) -> dict[str, Any]:
    """
    Replace `old` with `new` exactly once. If `old` is missing or appears more than
    once, return an error so the model can add more surrounding context.
    """
    if old is None or new is None:
        return {"ok": False, "error": "old and new are required."}
    if old == "":
        return {"ok": False, "error": "old_string is empty. Use write_file to create a new file."}
    if old == new:
        return {"ok": False, "error": "old and new are identical; no change."}
    count = source.count(old)
    if count == 0:
        hint = _nearest_hint(source, old)
        return {
            "ok": False,
            "error": "old_string not found. Copy the exact text from read_file (without line-number prefixes).",
            "hint": hint,
        }
    if count > 1:
        return {
            "ok": False,
            "error": f"old_string matches {count} times. Include more unique surrounding lines.",
        }
    return {"ok": True, "content": source.replace(old, new, 1), "replacements": 1}


def _nearest_hint(source: str, old: str, window: int = 80) -> str:
    needle = (old or "").strip().splitlines()[0][:window] if old else ""
    if not needle:
        return ""
    for line in source.splitlines():
        if needle[:24] and needle[:24] in line:
            return f"nearby line: {line[:200]}"
    return ""
