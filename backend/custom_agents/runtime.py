"""Capture explicit remember notes from a user message."""
from __future__ import annotations

import re

_REMEMBER = re.compile(r"^\s*remember(?:\s+that)?\s+(.+)$", re.IGNORECASE | re.DOTALL)


def remember_note_from_message(message: str) -> str:
    match = _REMEMBER.match(message or "")
    if not match:
        return ""
    return match.group(1).strip()
