"""Pinned per-chat specialist context so the next turn does not forget an open job."""
from __future__ import annotations

import re
from typing import Any, Optional

_GOAL_RE = re.compile(r"Desktop task \(goal:\s*(.+?)\)\s*\.", re.S)
_UNFINISHED = (
    "loop guard",
    "goal not marked done",
    "stopped after",
    "time budget reached",
    "spend cap",
    "stopped by user",
    "stopped (repeated",
)


def is_agent_trace(text: str) -> bool:
    t = (text or "").lstrip()
    return t.startswith("Desktop task") or t.startswith("### Desktop") or "Agent thought process:" in t[:500]


def desktop_unfinished(text: str) -> bool:
    low = (text or "").lower()
    if "game over" in low and "goal not marked" not in low:
        return False
    return any(p in low for p in _UNFINISHED)


def summarize_agent_trace(text: str, *, keep_steps: int = 5) -> str:
    """Keep the goal + last actions + stop reason. Drop the middle click-by-click dump."""
    raw = (text or "").strip()
    if not raw:
        return ""
    lines = raw.splitlines()
    head: list[str] = []
    steps: list[str] = []
    footer: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("Desktop task") or s.startswith("### ") or s.startswith("Plan:"):
            head.append(s[:280])
        elif s.startswith("Step ") or s.startswith("Action:") or s.startswith("→") or s.startswith("  Action:") or s.startswith("  →"):
            steps.append(s[:220])
        elif (
            s.startswith("Loop guard")
            or s.startswith("Stopped")
            or "goal not marked" in s.lower()
            or s.startswith("Time budget")
            or s.startswith("Spend cap")
        ):
            footer.append(s[:240])
    if not head and not steps:
        return raw[:600]
    out: list[str] = []
    out.extend(head[:8] or [raw.splitlines()[0][:280]])
    if steps:
        out.append("Recent steps:")
        out.extend(f"  {x}" for x in steps[-keep_steps:])
    out.extend(footer[-3:])
    if desktop_unfinished(raw):
        out.append(
            "Status: unfinished. The desktop goal is still open — do not claim you cannot see or click the screen."
        )
    return "\n".join(out)


def extract_desktop_goal(text: str) -> Optional[str]:
    m = _GOAL_RE.search(text or "")
    if not m:
        return None
    return (m.group(1) or "").strip()[:800] or None


def infer_thread_from_turns(recent_turns: Optional[list]) -> dict[str, Any]:
    for turn in reversed(recent_turns or []):
        if not isinstance(turn, dict):
            continue
        if (turn.get("role") or "") != "assistant":
            continue
        content = str(turn.get("content") or "")
        if not is_agent_trace(content) and "Desktop task" not in content:
            continue
        goal = extract_desktop_goal(content) or ""
        return {
            "last_route": "desktop",
            "last_goal": goal,
            "last_status": "unfinished" if desktop_unfinished(content) else "complete",
            "last_summary": summarize_agent_trace(content),
        }
    return {}


def thread_from_state(state: Optional[dict]) -> dict[str, Any]:
    st = state or {}
    route = str(st.get("last_route") or "").strip()
    goal = str(st.get("last_goal") or st.get("goal") or "").strip()
    status = str(st.get("last_status") or "").strip()
    summary = str(st.get("last_summary") or "").strip()
    if not route and not goal:
        return {}
    return {
        "last_route": route,
        "last_goal": goal,
        "last_status": status,
        "last_summary": summary,
    }


def merge_thread(state_thread: dict[str, Any], inferred: dict[str, Any]) -> dict[str, Any]:
    out = dict(inferred or {})
    out.update({k: v for k, v in (state_thread or {}).items() if v})
    return out


def format_thread_context(thread: Optional[dict[str, Any]]) -> str:
    t = thread or {}
    if not t.get("last_route") and not t.get("last_goal"):
        return ""
    lines = ["THREAD CONTEXT (authoritative for this chat):"]
    if t.get("last_route"):
        lines.append(f"Last specialist: {t['last_route']}")
    if t.get("last_goal"):
        lines.append(f"Open goal: {t['last_goal']}")
    if t.get("last_status"):
        lines.append(f"Status: {t['last_status']}")
    if t.get("last_summary"):
        lines.append(t["last_summary"])
    if t.get("last_status") == "unfinished" and (t.get("last_route") or "") == "desktop":
        lines.append(
            "If the user continues, retries, or refers to this work, keep the desktop specialist. "
            "Do not deny screen control you just used."
        )
    return "\n".join(lines)
