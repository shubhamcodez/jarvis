"""Token estimates and budget clipping for context assembly.

Uses the same ~4 chars/token heuristic as run_control.estimate_tokens so budgets
stay consistent with spend caps. Production systems (tiktoken) can replace this
without changing callers.
"""
from __future__ import annotations


def estimate_tokens(text: str) -> int:
    return max(0, (len(text or "") + 3) // 4)


def clip_to_tokens(text: str, budget: int, *, suffix: str = "…") -> str:
    """Keep the start of `text` within `budget` tokens."""
    if budget <= 0 or not text:
        return ""
    if estimate_tokens(text) <= budget:
        return text
    # 4 chars per token, leave room for suffix
    keep = max(1, budget * 4 - len(suffix))
    return text[:keep] + suffix


def clip_tail_to_tokens(text: str, budget: int, *, prefix: str = "…") -> str:
    """Keep the end of `text` within `budget` tokens (recent tail)."""
    if budget <= 0 or not text:
        return ""
    if estimate_tokens(text) <= budget:
        return text
    keep = max(1, budget * 4 - len(prefix))
    return prefix + text[-keep:]
