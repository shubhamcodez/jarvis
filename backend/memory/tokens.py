"""Token estimates and budget clipping for context assembly.

Conservative heuristic (no extra dependency): ASCII ~3 chars/token, non-ASCII
1 char/token. Overestimates vs English so budgets fail closed and do not
silently overflow provider windows.
"""
from __future__ import annotations


def estimate_tokens(text: str) -> int:
    t = text or ""
    if not t:
        return 0
    ascii_n = len(t.encode("ascii", "ignore"))
    non_ascii = len(t) - ascii_n
    return max(1, (ascii_n + 2) // 3 + non_ascii)


def clip_to_tokens(text: str, budget: int, *, suffix: str = "…") -> str:
    """Keep the start of `text` within `budget` tokens (binary search)."""
    if budget <= 0 or not text:
        return ""
    if estimate_tokens(text) <= budget:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid] + suffix) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo] + suffix) if lo else suffix


def clip_tail_to_tokens(text: str, budget: int, *, prefix: str = "…") -> str:
    """Keep the end of `text` within `budget` tokens (binary search)."""
    if budget <= 0 or not text:
        return ""
    if estimate_tokens(text) <= budget:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(prefix + text[-mid:]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return (prefix + text[-lo:]) if lo else prefix
