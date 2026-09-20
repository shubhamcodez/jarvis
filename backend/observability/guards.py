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


def action_fingerprint(action: dict[str, Any] | None) -> str:
    """Identify a repeated target (clicking different squares is not a loop)."""
    if not action:
        return ""
    act = str(action.get("action") or "").strip().lower()
    x, y = action.get("x"), action.get("y")
    if x is not None and y is not None:
        try:
            return f"{act}:{int(float(x)) // 16}:{int(float(y)) // 16}"
        except (TypeError, ValueError):
            return act
    extra = action.get("text") or action.get("key") or action.get("description") or ""
    return f"{act}:{str(extra)[:40]}"


def should_stop_streak(
    current_action: str,
    current_thought: str,
    history: list[dict[str, Any]],
    streak_limit: int = 3,
) -> bool:
    """True if the last streak_limit steps hit the same target (not merely the same verb)."""
    if len(history) < streak_limit:
        return False
    tail = history[-streak_limit:]
    fps = [t.get("fingerprint") or t.get("action") for t in tail]
    if len(set(fps)) != 1 or not fps[0]:
        return False
    current_fp = fps[-1]
    if current_action and current_fp.split(":")[0] != current_action:
        return False
    thoughts = [t.get("thought", "") for t in tail]
    if current_thought and thoughts.count(current_thought) >= streak_limit:
        return True
    return True


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
