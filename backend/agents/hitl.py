"""Human-in-the-loop gates for high-impact writes. The model cannot bypass these."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Optional

from config import get_autonomy_level, is_desktop_armed, set_desktop_armed

_LOCK = threading.Lock()
_PENDING: dict[str, dict[str, Any]] = {}

HIGH_IMPACT_GOOGLE_OPS = frozenset(
    {
        "event_create",
        "create_event",
        "calendar_event_create",
        "event_update",
        "update_event",
        "calendar_event_update",
        "event_delete",
        "delete_event",
        "calendar_event_delete",
        "gmail_send",
        "send_email",
        "send_message",
        "gmail_modify",
        "modify_message",
        "gmail_labels_modify",
    }
)

_SHELL_WRITE_HINTS = (
    "rm ",
    "rmdir",
    "del ",
    "remove-item",
    "move ",
    "mv ",
    "ni ",
    "new-item",
    "set-content",
    "out-file",
    ">",
    "mkdir",
    "git commit",
    "git push",
    "npm publish",
    "pip install",
)


def classify_google_op(op: str) -> str:
    o = (op or "").strip().lower()
    if o in HIGH_IMPACT_GOOGLE_OPS:
        if "delete" in o or o in ("gmail_send", "send_email", "send_message"):
            return "high_impact"
        return "write"
    return "read"


def classify_shell_command(command: str) -> str:
    low = (command or "").lower()
    if any(h in low for h in _SHELL_WRITE_HINTS):
        return "high_impact" if any(h in low for h in ("rm ", "del ", "git push", "remove-item")) else "write"
    return "read"


def needs_approval(risk: str, autonomy: Optional[str] = None) -> bool:
    level = (autonomy or get_autonomy_level()).strip().lower()
    r = (risk or "read").strip().lower()
    if level in ("recommend", "draft"):
        return r in ("write", "high_impact")
    if level == "low_risk_auto":
        return r in ("write", "high_impact")
    if level == "limited_auto":
        return r == "high_impact"
    # gated (default)
    return r == "high_impact"


def enqueue_approval(
    *,
    kind: str,
    summary: str,
    args: dict[str, Any],
    chat_id: Optional[str] = None,
    execute: Optional[Callable[[], Any]] = None,
) -> dict[str, Any]:
    approval_id = str(uuid.uuid4())
    rec = {
        "id": approval_id,
        "kind": kind,
        "summary": (summary or "")[:800],
        "args": args,
        "chat_id": chat_id or "",
        "status": "pending",
        "created_at": time.time(),
        "_execute": execute,
    }
    with _LOCK:
        _PENDING[approval_id] = rec
    public = {k: v for k, v in rec.items() if k != "_execute"}
    if chat_id:
        try:
            from agents.agent_state import append_hitl, load_state

            st = load_state(chat_id)
            append_hitl(st, public)
        except Exception:
            pass
    return public


def list_pending(chat_id: Optional[str] = None) -> list[dict[str, Any]]:
    with _LOCK:
        items = []
        for rec in _PENDING.values():
            if rec.get("status") != "pending":
                continue
            if chat_id and rec.get("chat_id") and rec.get("chat_id") != chat_id:
                continue
            items.append({k: v for k, v in rec.items() if k != "_execute"})
        return sorted(items, key=lambda x: x.get("created_at") or 0)


def resolve_approval(approval_id: str, approve: bool) -> dict[str, Any]:
    with _LOCK:
        rec = _PENDING.get(approval_id)
        if not rec:
            return {"ok": False, "error": "unknown_approval"}
        if rec.get("status") != "pending":
            return {"ok": False, "error": "already_resolved", "status": rec.get("status")}
        fn = rec.get("_execute")
        if not approve:
            rec["status"] = "denied"
            return {"ok": True, "status": "denied", "id": approval_id}
        rec["status"] = "approved"
    result = None
    error = None
    if callable(fn):
        try:
            result = fn()
        except Exception as e:
            error = str(e)
    public = {k: v for k, v in rec.items() if k != "_execute"}
    public["result"] = result
    public["error"] = error
    public["ok"] = error is None
    return public


def maybe_gate_google(
    op: str,
    args: dict[str, Any],
    *,
    chat_id: Optional[str] = None,
    execute: Callable[[], Any],
) -> dict[str, Any]:
    risk = classify_google_op(op)
    if not needs_approval(risk):
        return execute()
    pending = enqueue_approval(
        kind="google",
        summary=f"Google {op}: {args}",
        args={"op": op, **(args or {})},
        chat_id=chat_id,
        execute=execute,
    )
    return {
        "ok": False,
        "pending_approval": True,
        "approval_id": pending["id"],
        "error": "Awaiting user confirmation in the Ada window.",
        "op": op,
    }


def maybe_gate_shell(
    command: str,
    *,
    chat_id: Optional[str] = None,
    execute: Callable[[], Any],
) -> dict[str, Any]:
    risk = classify_shell_command(command)
    if not needs_approval(risk):
        return execute()
    pending = enqueue_approval(
        kind="shell",
        summary=f"Run host command: {command}",
        args={"command": command},
        chat_id=chat_id,
        execute=execute,
    )
    return {
        "ok": False,
        "pending_approval": True,
        "approval_id": pending["id"],
        "error": "Awaiting user confirmation before running this command.",
        "command": command,
    }


def require_desktop_armed() -> Optional[str]:
    """Return an error message if GUI control is not armed."""
    if is_desktop_armed():
        return None
    return (
        "Desktop GUI control is not armed. Turn on **Allow GUI control** in Settings "
        "for this session, then retry."
    )


def arm_desktop(armed: bool) -> bool:
    set_desktop_armed(bool(armed))
    return is_desktop_armed()
