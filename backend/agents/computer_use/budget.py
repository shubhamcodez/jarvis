"""Time/step budgets from the user's wording — not task-type specific."""
from __future__ import annotations

import re
from typing import Optional

_DURATION_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b",
    re.I,
)


def parse_duration(text: str) -> Optional[float]:
    best = None
    for m in _DURATION_RE.finditer(text or ""):
        n = float(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("h"):
            sec = n * 3600
        elif unit.startswith("m"):
            sec = n * 60
        else:
            sec = n
        best = sec
    if best is None:
        return None
    return max(15.0, min(best, 2 * 3600))


def steps_for_budget(duration_sec: Optional[float], base: int = 25) -> int:
    if not duration_sec:
        return base
    return max(base, min(400, int(float(duration_sec) / 2.5) + 40))
