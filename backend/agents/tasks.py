"""Durable task records — status lives on disk, not in the model window."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from config import chats_dir, data_root

_LOCK = threading.Lock()
_CACHE: dict[str, Any] | None = None
_CACHE_MTIME: int | None = None

_OPEN = frozenset({"pending", "active", "blocked", "waiting_approval", "paused"})
_STATUSES = _OPEN | frozenset({"complete", "completed", "error", "cancelled", "canceled", "skipped"})


def _path() -> Path:
    d = data_root() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / "tasks.json"
    legacy = Path(chats_dir()) / "tasks.json"
    if not dest.exists() and legacy.exists():
        try:
            dest.write_bytes(legacy.read_bytes())
        except OSError:
            pass
    return dest


def _empty() -> dict[str, Any]:
    return {"tasks": []}


def _load() -> dict[str, Any]:
    global _CACHE, _CACHE_MTIME
    path = _path()
    if not path.exists():
        _CACHE = _empty()
        _CACHE_MTIME = None
        return _CACHE
    try:
        mt = path.stat().st_mtime_ns
    except OSError:
        mt = None
    if _CACHE is not None and mt is not None and mt == _CACHE_MTIME:
        return _CACHE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("tasks"), list):
            _CACHE = data
            _CACHE_MTIME = mt
            return data
    except (OSError, json.JSONDecodeError):
        pass
    _CACHE = _empty()
    _CACHE_MTIME = mt
    return _CACHE


def _save(data: dict[str, Any]) -> None:
    global _CACHE, _CACHE_MTIME
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    _CACHE = data
    try:
        _CACHE_MTIME = path.stat().st_mtime_ns
    except OSError:
        _CACHE_MTIME = None


def _public(task: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in task.items() if not str(k).startswith("_")}


def create_task(
    *,
    chat_id: str = "",
    run_id: str = "",
    title: str = "",
    goal: str = "",
    owner: str = "ada",
    plan: Optional[list[dict[str, Any]]] = None,
    next_action: str = "",
) -> dict[str, Any]:
    now = time.time()
    task = {
        "id": "tsk_" + uuid.uuid4().hex[:12],
        "chat_id": chat_id or "",
        "run_id": run_id or "",
        "title": (title or goal or "Untitled task")[:160],
        "goal": (goal or "")[:2000],
        "owner": (owner or "ada")[:80],
        "status": "active",
        "next_action": (next_action or "")[:400],
        "artifact": "",
        "plan": list(plan or []),
        "error": "",
        "resumable": True,
        "created_at": now,
        "updated_at": now,
    }
    with _LOCK:
        data = _load()
        data["tasks"].insert(0, task)
        data["tasks"] = data["tasks"][:200]
        _save(data)
    return _public(task)


def get_task(task_id: str) -> Optional[dict[str, Any]]:
    tid = (task_id or "").strip()
    if not tid:
        return None
    with _LOCK:
        for t in _load().get("tasks") or []:
            if t.get("id") == tid:
                return _public(t)
    return None


def get_task_by_run(run_id: str) -> Optional[dict[str, Any]]:
    rid = (run_id or "").strip()
    if not rid:
        return None
    with _LOCK:
        for t in _load().get("tasks") or []:
            if t.get("run_id") == rid:
                return _public(t)
    return None


def update_task(task_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    tid = (task_id or "").strip()
    if not tid:
        return None
    allowed = {
        "status",
        "owner",
        "next_action",
        "artifact",
        "plan",
        "error",
        "run_id",
        "title",
        "goal",
        "resumable",
        "chat_id",
    }
    with _LOCK:
        data = _load()
        for t in data.get("tasks") or []:
            if t.get("id") != tid:
                continue
            for k, v in fields.items():
                if k not in allowed:
                    continue
                if k == "status":
                    sv = str(v or "").strip().lower()
                    if sv not in _STATUSES:
                        continue
                    t[k] = sv
                else:
                    t[k] = v
            t["updated_at"] = time.time()
            _save(data)
            return _public(t)
    return None


def list_tasks(
    *,
    chat_id: Optional[str] = None,
    open_only: bool = False,
    limit: int = 40,
) -> list[dict[str, Any]]:
    with _LOCK:
        items = list(_load().get("tasks") or [])
    if chat_id:
        items = [t for t in items if t.get("chat_id") == chat_id]
    if open_only:
        items = [t for t in items if t.get("status") in _OPEN]
    return [_public(t) for t in items[: max(1, min(200, int(limit)))]]


def pause_inflight() -> int:
    """Mark active runs as paused so they can resume after a crash/restart."""
    n = 0
    with _LOCK:
        data = _load()
        for t in data.get("tasks") or []:
            if t.get("status") != "active":
                continue
            t["status"] = "paused"
            t["next_action"] = t.get("next_action") or "Resume after restart"
            t["updated_at"] = time.time()
            n += 1
        if n:
            _save(data)
    return n


def sync_from_agent_state(state: dict[str, Any], *, status: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Keep the matching task in sync with AgentState after each plan step."""
    run_id = (state.get("run_id") or "").strip()
    if not run_id:
        return None
    plan = list(state.get("plan") or [])
    next_action = ""
    for step in plan:
        if step.get("status") in ("active", "pending"):
            next_action = f"{step.get('agent') or 'ada'}: {step.get('goal') or ''}".strip()
            break
    findings = state.get("findings") or []
    pending = False
    try:
        from agents.hitl import list_pending

        pending = bool(list_pending(state.get("chat_id")))
    except Exception:
        pending = False
    with _LOCK:
        data = _load()
        task = None
        for t in data.get("tasks") or []:
            if t.get("run_id") == run_id:
                task = t
                break
        if not task:
            return None
        artifact = findings[-1][:400] if findings else task.get("artifact") or ""
        derived = status
        if derived is None:
            statuses = {s.get("status") for s in plan}
            if state.get("errors") and "error" in statuses:
                derived = "error"
            elif any(s.get("status") == "active" for s in plan):
                derived = "active"
            elif plan and all(s.get("status") in ("complete", "skipped") for s in plan):
                derived = "complete"
            elif any(s.get("status") == "error" for s in plan):
                derived = "error"
            else:
                derived = task.get("status") or "active"
        if pending and derived == "active":
            derived = "waiting_approval"
        if derived and derived in _STATUSES:
            task["status"] = derived
        task["next_action"] = next_action[:400]
        task["artifact"] = artifact
        task["plan"] = plan
        task["error"] = (state.get("errors") or [""])[-1] if state.get("errors") else ""
        task["updated_at"] = time.time()
        _save(data)
        return _public(task)


def remaining_plan(task: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for i, step in enumerate(task.get("plan") or []):
        if step.get("status") in ("pending", "active", "error"):
            out.append({
                "index": i,
                "agent": step.get("agent"),
                "goal": step.get("goal") or task.get("goal"),
            })
    return [x for x in out if x.get("agent") and x.get("goal")]
