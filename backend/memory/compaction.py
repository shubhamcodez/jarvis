"""History compaction under a token budget (MemGPT flush / ACM compact).

Extractive, deterministic, no extra LLM. Older turns become a bounded summary;
the recent tail stays verbatim and is itself trimmed if it exceeds the budget.
"""
from __future__ import annotations

from .thread_context import is_agent_trace, summarize_agent_trace
from .tokens import clip_tail_to_tokens, estimate_tokens, clip_to_tokens

_SUMMARY_PREFIX = "Earlier conversation ("


def _is_prior_summary(message: dict) -> bool:
    role = (message.get("role") or "").strip()
    text = message.get("content") or ""
    return role == "system" and text.startswith(_SUMMARY_PREFIX)


def compact_history(
    messages: list[dict],
    *,
    token_budget: int = 6000,
    keep_recent: int = 12,
) -> tuple[list[dict], dict]:
    """
    Return (messages_for_model, stats).
    Prior compaction summaries are dropped so they are not treated as policy.
    If the recent tail alone exceeds the budget, oldest recent turns are dropped
    and remaining contents are tail-clipped.
    """
    rows = []
    for m in messages or []:
        if (m.get("role") or "") not in ("user", "assistant") or _is_prior_summary(m):
            continue
        body = str(m.get("content") or "")
        if (m.get("role") or "") == "assistant" and is_agent_trace(body):
            body = summarize_agent_trace(body)
        rows.append({**m, "content": body})
    stats = {
        "original_messages": len(rows),
        "original_tokens": sum(estimate_tokens(str(m.get("content") or "")) for m in rows),
        "compacted": False,
    }
    if not rows or stats["original_tokens"] <= token_budget:
        return rows, stats

    recent = rows[-keep_recent:] if len(rows) > keep_recent else list(rows)
    older = rows[:-keep_recent] if len(rows) > keep_recent else []

    def _recent_tokens(items: list[dict]) -> int:
        return sum(estimate_tokens(str(m.get("content") or "")) for m in items)

    while len(recent) > 2 and _recent_tokens(recent) > token_budget:
        older.append(recent.pop(0))

    clipped: list[dict] = []
    remain = token_budget
    for m in reversed(recent):
        body = str(m.get("content") or "")
        cost = estimate_tokens(body)
        if cost > remain:
            budget = max(40, remain)
            if is_agent_trace(body) or body.startswith("Desktop task"):
                head = clip_to_tokens(body, max(40, budget // 3))
                tail = clip_tail_to_tokens(body, max(40, budget - estimate_tokens(head)))
                body = head if not tail or tail in head else f"{head}\n{tail}"
            else:
                body = clip_tail_to_tokens(body, budget)
            cost = estimate_tokens(body)
        if remain <= 0:
            break
        clipped.append({**m, "content": body})
        remain -= cost
    recent = list(reversed(clipped))
    recent_tokens = _recent_tokens(recent)
    summary_budget = max(80, token_budget - recent_tokens - 40)

    lines = [f"{_SUMMARY_PREFIX}{len(older)} messages, compacted):"]
    used = estimate_tokens(lines[0])
    omitted = 0
    for m in older:
        role = m.get("role") or "user"
        text = (m.get("content") or "").replace("\n", " ").strip()
        if not text:
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
