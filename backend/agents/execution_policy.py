"""Hard run-mode gates. Prompt instructions cannot bypass these."""
from __future__ import annotations

from typing import Any, Optional

from config import get_run_mode

WRITE_KINDS = frozenset({"shell", "workspace_write", "google_write", "desktop"})

_BLOCK_MSG = {
    "plan": "Plan mode is on — Jarvis will not {kind}. Switch to Draft or Agent to execute.",
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


def gui_is_armed() -> bool:
    try:
        from config import is_desktop_armed

        return bool(is_desktop_armed())
    except Exception:
        return False


def gui_policy_for_prompt() -> str:
    """Chat/supervisor must follow Settings → Desktop GUI control, not a blanket claim."""
    armed = gui_is_armed()
    mode = mode_label()
    if mode == "plan":
        return (
            "GUI POLICY: Plan mode is on, so mouse and keyboard will not run. "
            "Do not claim you clicked. Settings → Desktop GUI control must also be Armed to use the desktop agent."
        )
    if not armed:
        return (
            "GUI POLICY: Settings → Desktop GUI control is Off. "
            "You cannot see or click the user's screen this session. "
            "If they ask to click, play a site, or control the GUI, tell them to set "
            "Desktop GUI control to Armed in Settings, then retry. "
            "Do not invent clicks, moves, or pretend you are driving the desktop."
        )
    return (
        "GUI POLICY: Settings → Desktop GUI control is Armed. "
        "The desktop specialist can screenshot and click/type. "
        "Do not say you cannot see or interact with the screen. "
        "Unfinished desktop goals resume with that specialist, not a chat refusal."
    )
