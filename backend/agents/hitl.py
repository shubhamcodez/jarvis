"""Human-in-the-loop gates for high-impact writes. The model cannot bypass these."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Optional

import json

from config import data_root, get_autonomy_level, is_desktop_armed, set_desktop_armed

_LOCK = threading.Lock()
_PENDING: dict[str, dict[str, Any]] = {}
_LOADED = False


def _pending_path():
    p = data_root() / "memory" / "pending_approvals.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _persist_pending_locked() -> None:
    rows = []
    for rec in _PENDING.values():
        if rec.get("status") != "pending":
            continue
        rows.append({k: v for k, v in rec.items() if k != "_execute"})
    try:
        tmp = _pending_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_pending_path())
    except OSError:
        pass


def _ensure_pending_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    path = _pending_path()
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(raw, list):
        return
    now = time.time()
    for rec in raw:
        if not isinstance(rec, dict) or not rec.get("id"):
            continue
        if rec.get("status") != "pending":
            continue
        rec = dict(rec)
        rec["_execute"] = None
        rec["stale"] = True
        if now - float(rec.get("created_at") or 0) > _PENDING_TTL_SEC:
            continue
        _PENDING.setdefault(rec["id"], rec)

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


_SHELL_HIGH_IMPACT = (
    "rm ",
    "rmdir",
    "rd ",
    "del ",
    "remove-item",
    "ri ",
    "set-content",
    "out-file",
    ">",
    "git push",
    "npm publish",
    "format ",
    "stop-computer",
    "shutdown",
)


def classify_google_op(op: str) -> str:
    o = (op or "").strip().lower()
    if o in HIGH_IMPACT_GOOGLE_OPS:
        return "high_impact"
    return "read"


def classify_shell_command(command: str) -> str:
    low = (command or "").lower()
    if any(h in low for h in _SHELL_HIGH_IMPACT):
        return "high_impact"
    if any(h in low for h in _SHELL_WRITE_HINTS):
        return "write"
    return "read"


def needs_approval(risk: str, autonomy: Optional[str] = None) -> bool:
    from agents.execution_policy import force_hitl, mode_label

    if mode_label() == "plan":
        return True
    level = (autonomy or get_autonomy_level()).strip().lower()
    r = (risk or "read").strip().lower()
    if force_hitl("write") and r in ("write", "high_impact"):
        return True
    if level in ("recommend", "draft"):
        return r in ("write", "high_impact")
    if level == "low_risk_auto":
        return r == "high_impact"
    if level == "limited_auto":
        return r == "high_impact"
    # gated (default): confirm all writes
    return r in ("write", "high_impact")


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
        _ensure_pending_loaded()
        _prune_pending_locked()
        _PENDING[approval_id] = rec
        _persist_pending_locked()
    public = {k: v for k, v in rec.items() if k != "_execute"}
    if chat_id:
        try:
            from agents.agent_state import append_hitl, load_state

            st = load_state(chat_id)
            append_hitl(st, public)
        except Exception:
            pass
    return public


_PENDING_TTL_SEC = 6 * 60 * 60
_PENDING_MAX = 80


def _prune_pending_locked() -> None:
    now = time.time()
    stale = [
        aid
        for aid, rec in _PENDING.items()
        if rec.get("status") != "pending"
        or now - float(rec.get("created_at") or 0) > _PENDING_TTL_SEC
    ]
    for aid in stale:
        _PENDING.pop(aid, None)
    if len(_PENDING) > _PENDING_MAX:
        oldest = sorted(_PENDING.items(), key=lambda kv: kv[1].get("created_at") or 0)
        for aid, _ in oldest[: len(_PENDING) - _PENDING_MAX]:
            _PENDING.pop(aid, None)


def list_pending(chat_id: Optional[str] = None) -> list[dict[str, Any]]:
    with _LOCK:
        _ensure_pending_loaded()
        _prune_pending_locked()
        items = []
        for rec in _PENDING.values():
            if rec.get("status") != "pending":
                continue
            rec_chat = rec.get("chat_id") or ""
            if chat_id and rec_chat != chat_id:
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
        if rec.get("stale") or not callable(fn):
            rec["status"] = "expired"
            _persist_pending_locked()
            return {
                "ok": False,
                "error": "This approval expired after a restart. Ask Ada to retry the action.",
                "id": approval_id,
                "stale": True,
            }
        if not approve:
            rec["status"] = "denied"
            _persist_pending_locked()
            return {"ok": True, "status": "denied", "id": approval_id}
        rec["status"] = "approved"
        _persist_pending_locked()
    result = None
    error = None
    if callable(fn):
        try:
            from config import in_quiet_hours
            from tools.shell_runner import why_command_blocked

            kind = rec.get("kind") or ""
            args = rec.get("args") or {}
            if kind == "shell":
                if in_quiet_hours():
                    raise RuntimeError("Quiet hours are enabled; shell is paused.")
                cmd = str(args.get("command") or "")
                blocked = why_command_blocked(cmd) if cmd else None
                if blocked:
                    raise RuntimeError(f"Command blocked: {blocked}")
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
    from agents.execution_policy import deny_if_blocked

    blocked = deny_if_blocked("google_write" if classify_google_op(op) != "read" else "read")
    if blocked and classify_google_op(op) != "read":
        return blocked
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
    from agents.execution_policy import deny_if_blocked
    from agents.run_control import record_checkpoint

    blocked = deny_if_blocked("shell")
    if blocked:
        return blocked
    from config import in_quiet_hours

    if in_quiet_hours():
        return {"ok": False, "error": "Quiet hours are enabled; shell is paused."}
    risk = classify_shell_command(command)
    if risk in ("write", "high_impact"):
        try:
            record_checkpoint(
                None,
                kind="shell",
                summary=f"shell: {(command or '')[:120]}",
                payload={"command": command},
            )
        except Exception:
            pass
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
    from agents.execution_policy import assert_allowed

    blocked = assert_allowed("desktop")
    if blocked:
        return blocked
    if is_desktop_armed():
        return None
    return (
        "Desktop GUI control is not armed. Turn on **Allow GUI control** in Settings "
        "for this session, then retry."
    )


def arm_desktop(armed: bool) -> bool:
    set_desktop_armed(bool(armed))
    return is_desktop_armed()
