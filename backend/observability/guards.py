"""
Loop corruption mitigation: detect repeated/same action, cap retries, degenerate output.
Used inside the desktop agent loop to break early and avoid runaway.
"""
from __future__ import annotations

from typing import Any, Optional  # noqa: I001


def check_loop_corruption(
    step: int,
    action: str,
    last_action: Optional[str] = None,
    last_thought: Optional[str] = None,
    thought: Optional[str] = None,
    max_same_action_streak: int = 3,
    max_steps: int = 15,
) -> tuple[bool, str]:
    """
    Returns (is_corrupted, reason).
    If is_corrupted True, caller should break the loop (e.g. return "done" with reason).
    """
    if step >= max_steps:
        return True, f"max_steps ({max_steps}) reached"
    if not action:
        return False, ""
    if last_action and action == last_action and (not thought or not last_thought or thought == last_thought):
        return True, f"repeated action '{action}' (streak cap {max_same_action_streak})"
    return False, ""


def should_stop_streak(
    current_action: str,
    current_thought: str,
    history: list[dict[str, Any]],
    streak_limit: int = 3,
) -> bool:
    """True if the last streak_limit steps had same action (and similar thought)."""
    if len(history) < streak_limit:
        return False
    tail = history[-streak_limit:]
    actions = [t.get("action") for t in tail]
    if not all(a == current_action for a in actions):
        return False
    thoughts = [t.get("thought", "") for t in tail]
    if current_thought and thoughts.count(current_thought) >= streak_limit:
        return True
    return True  # same action N times → stop


def action_signature(tool: str, args: Any = None) -> str:
    """Stable signature for progress detection (tool + bounded args)."""
    raw = f"{tool}|{args}"
    return raw[:240]


def should_force_replan(signatures: list[str], repeat_limit: int = 3) -> bool:
    """True when the same action signature repeats without new information."""
    if not signatures or len(signatures) < repeat_limit:
        return False
    tail = signatures[-repeat_limit:]
    return len(set(tail)) == 1 and bool(tail[0])
