"""History compaction under a token budget (MemGPT flush / ACM compact).

Extractive, deterministic, no extra LLM. Older turns become a bounded summary;
the recent tail stays verbatim.
"""
from __future__ import annotations

from typing import Optional

from .tokens import estimate_tokens


def compact_history(
    messages: list[dict],
    *,
    token_budget: int = 6000,
    keep_recent: int = 12,
) -> tuple[list[dict], dict]:
    """
    Return (messages_for_model, stats).
    If the thread fits the budget, it is returned unchanged.
    Otherwise older user/assistant lines are condensed into one system-visible
    summary message placed at the front.
    """
    rows = [m for m in (messages or []) if (m.get("role") or "") in ("user", "assistant", "system")]
    stats = {
        "original_messages": len(rows),
        "original_tokens": sum(estimate_tokens(str(m.get("content") or "")) for m in rows),
        "compacted": False,
    }
    if not rows or stats["original_tokens"] <= token_budget:
        return rows, stats

    recent = rows[-keep_recent:] if len(rows) > keep_recent else rows
    older = rows[:-keep_recent] if len(rows) > keep_recent else []
    recent_tokens = sum(estimate_tokens(str(m.get("content") or "")) for m in recent)
    summary_budget = max(120, token_budget - recent_tokens - 40)

    lines = [f"Earlier conversation ({len(older)} messages, compacted):"]
    used = estimate_tokens(lines[0])
    omitted = 0
    for m in older:
        role = m.get("role") or "user"
        text = (m.get("content") or "").replace("\n", " ").strip()
        if not text or role == "tool":
            continue
        line = f"- {role}: {text[:180]}"
        cost = estimate_tokens(line)
        if used + cost > summary_budget:
            omitted += 1
            continue
        lines.append(line)
        used += cost
    if omitted:
        lines.append(f"- … {omitted} older lines omitted")
    summary = "\n".join(lines)
    packed = [{"role": "system", "content": summary}] + recent
    stats["compacted"] = True
    stats["kept_recent"] = len(recent)
    stats["compacted_tokens"] = estimate_tokens(summary) + recent_tokens
    return packed, stats
