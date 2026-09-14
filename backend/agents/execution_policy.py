"""Hard run-mode gates. Prompt instructions cannot bypass these."""
from __future__ import annotations

from typing import Any, Optional

from config import get_run_mode

WRITE_KINDS = frozenset({"shell", "workspace_write", "google_write", "desktop"})

_BLOCK_MSG = {
    "plan": "Plan mode is on — Ada will not {kind}. Switch to Draft or Agent to execute.",
    "draft": "Draft mode is on — {kind} needs your confirmation first.",
}


def mode_label(mode: Optional[str] = None) -> str:
    return (mode or get_run_mode() or "agent").strip().lower()


def assert_allowed(kind: str) -> Optional[str]:
    """Return an error string if this action is blocked, else None."""
    mode = mode_label()
    k = (kind or "").strip().lower()
    if mode == "plan" and k in WRITE_KINDS:
        return _BLOCK_MSG["plan"].format(kind=k.replace("_", " "))
    return None


def deny_if_blocked(kind: str) -> Optional[dict[str, Any]]:
    err = assert_allowed(kind)
    if not err:
        return None
    return {
        "ok": False,
        "blocked": True,
        "run_mode": mode_label(),
        "error": err,
    }


def force_hitl(kind: str = "write") -> bool:
    """Draft mode always requires approval for writes."""
    if mode_label() == "draft" and (kind or "").strip().lower() in WRITE_KINDS | {"write", "high_impact"}:
        return True
    return False


def plan_mode_system_note() -> str:
    mode = mode_label()
    if mode == "plan":
        return (
            "RUN MODE: PLAN ONLY. Describe the plan, files, and commands you would use. "
            "Do not claim you executed anything. Shell, desktop, email send, and file writes are blocked."
        )
    if mode == "draft":
        return (
            "RUN MODE: DRAFT. Propose actions and wait for confirmation. "
            "Do not send email, run destructive shell, or write files until the user confirms."
        )
    return ""
