"""In-flight run registry: spend caps, cancel, steer, checkpoints, child specialists."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from config import chats_dir, get_spend_limits

_LOCK = threading.Lock()
_RUNS: dict[str, dict[str, Any]] = {}


def estimate_tokens(text: str) -> int:
    return max(0, len(text or "") // 4)


def _checkpoints_dir(run_id: str) -> Path:
    d = Path(chats_dir()) / "checkpoints" / "".join(
        c if c.isalnum() or c in "-_" else "_" for c in (run_id or "run")
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


def start_run(*, chat_id: str = "", task_id: str = "", run_id: Optional[str] = None) -> dict[str, Any]:
    rid = run_id or str(uuid.uuid4())
    with _LOCK:
        existing = _RUNS.get(rid)
        if existing:
            existing["chat_id"] = chat_id or existing.get("chat_id") or ""
            if task_id:
                existing["task_id"] = task_id
            existing["status"] = "running"
            existing["updated_at"] = time.time()
            return public_run(existing)
    rec = {
        "run_id": rid,
        "chat_id": chat_id or "",
        "task_id": task_id or "",
        "status": "running",
        "tokens_in": 0,
        "tokens_out": 0,
        "started_at": time.time(),
        "updated_at": time.time(),
        "cancel": threading.Event(),
        "steer": [],
        "children": [],
        "checkpoints": [],
        "stop_reason": "",
    }
    with _LOCK:
        _RUNS[rid] = rec
    return public_run(rec)


def public_run(rec: dict[str, Any]) -> dict[str, Any]:
    limits = get_spend_limits()
    used = int(rec.get("tokens_in") or 0) + int(rec.get("tokens_out") or 0)
    children = []
    for c in rec.get("children") or []:
        children.append({k: v for k, v in c.items() if k != "_cancel"})
    return {
        "run_id": rec.get("run_id"),
        "chat_id": rec.get("chat_id"),
        "task_id": rec.get("task_id"),
        "status": rec.get("status"),
        "tokens_in": rec.get("tokens_in") or 0,
        "tokens_out": rec.get("tokens_out") or 0,
        "tokens_used": used,
        "max_tokens": limits["max_tokens_per_run"],
        "warn_tokens": limits["warn_tokens"],
        "started_at": rec.get("started_at"),
        "updated_at": rec.get("updated_at"),
        "children": children,
        "checkpoints": list(rec.get("checkpoints") or [])[-12:],
        "stop_reason": rec.get("stop_reason") or "",
        "cancelled": bool(rec.get("cancel") and rec["cancel"].is_set()),
    }


def get_run(run_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not run_id:
        return None
    with _LOCK:
        rec = _RUNS.get(run_id)
        return public_run(rec) if rec else None


def list_active(chat_id: Optional[str] = None) -> list[dict[str, Any]]:
    with _LOCK:
        items = [r for r in _RUNS.values() if r.get("status") == "running"]
    if chat_id:
        items = [r for r in items if r.get("chat_id") == chat_id]
    return [public_run(r) for r in items]


def add_tokens(run_id: Optional[str], tin: int = 0, tout: int = 0) -> Optional[dict[str, Any]]:
    if not run_id:
        return None
    with _LOCK:
        rec = _RUNS.get(run_id)
        if not rec:
            return None
        rec["tokens_in"] = int(rec.get("tokens_in") or 0) + max(0, int(tin))
        rec["tokens_out"] = int(rec.get("tokens_out") or 0) + max(0, int(tout))
        rec["updated_at"] = time.time()
        return public_run(rec)


def spend_ok(run_id: Optional[str]) -> tuple[bool, dict[str, Any]]:
    limits = get_spend_limits()
    info = get_run(run_id) or {
        "tokens_used": 0,
        "max_tokens": limits["max_tokens_per_run"],
        "warn_tokens": limits["warn_tokens"],
    }
    used = int(info.get("tokens_used") or 0)
    cap = int(info.get("max_tokens") or limits["max_tokens_per_run"])
    return used < cap, info


def is_cancelled(run_id: Optional[str]) -> bool:
    if not run_id:
        return False
    with _LOCK:
        rec = _RUNS.get(run_id)
        if not rec:
            return False
        ev = rec.get("cancel")
        return bool(ev and ev.is_set())


def cancel_run(run_id: str, reason: str = "user_stop") -> Optional[dict[str, Any]]:
    with _LOCK:
        rec = _RUNS.get(run_id)
        if not rec:
            return None
        ev = rec.get("cancel")
        if ev:
            ev.set()
        rec["status"] = "cancelled"
        rec["stop_reason"] = reason
        rec["updated_at"] = time.time()
        return public_run(rec)


def finish_run(run_id: Optional[str], status: str = "complete") -> Optional[dict[str, Any]]:
    if not run_id:
        return None
    with _LOCK:
        rec = _RUNS.get(run_id)
        if not rec:
            return None
        if rec.get("status") == "running":
            rec["status"] = status
        rec["updated_at"] = time.time()
        return public_run(rec)


def push_steer(run_id: str, note: str) -> Optional[dict[str, Any]]:
    note = (note or "").strip()[:800]
    if not note:
        return get_run(run_id)
    with _LOCK:
        rec = _RUNS.get(run_id)
        if not rec:
            return None
        rec.setdefault("steer", []).append(note)
        rec["updated_at"] = time.time()
        return public_run(rec)


def pop_steer(run_id: Optional[str]) -> str:
    if not run_id:
        return ""
    with _LOCK:
        rec = _RUNS.get(run_id)
        if not rec:
            return ""
        notes = rec.get("steer") or []
        if not notes:
            return ""
        rec["steer"] = []
        return " ".join(str(n) for n in notes)


def register_child(run_id: Optional[str], *, name: str, agent: str) -> Optional[str]:
    if not run_id:
        return None
    cid = "sub_" + uuid.uuid4().hex[:8]
    with _LOCK:
        rec = _RUNS.get(run_id)
        if not rec:
            return None
        rec.setdefault("children", []).append(
            {
                "id": cid,
                "name": name,
                "agent": agent,
                "status": "running",
                "started_at": time.time(),
            }
        )
        rec["updated_at"] = time.time()
    return cid


def finish_child(run_id: Optional[str], child_id: Optional[str], status: str = "complete") -> None:
    if not run_id or not child_id:
        return
    with _LOCK:
        rec = _RUNS.get(run_id)
        if not rec:
            return
        for c in rec.get("children") or []:
            if c.get("id") == child_id:
                c["status"] = status
                c["ended_at"] = time.time()
                break
        rec["updated_at"] = time.time()


def record_checkpoint(
    run_id: Optional[str],
    *,
    kind: str,
    summary: str,
    payload: dict[str, Any],
) -> Optional[dict[str, Any]]:
    rid = run_id or "orphan"
    cid = "cp_" + uuid.uuid4().hex[:10]
    rec = {
        "id": cid,
        "run_id": rid,
        "kind": kind,
        "summary": (summary or "")[:300],
        "created_at": time.time(),
        "payload": payload,
    }
    path = _checkpoints_dir(rid) / f"{cid}.json"
    try:
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return None
    public = {k: rec[k] for k in ("id", "run_id", "kind", "summary", "created_at")}
    with _LOCK:
        live = _RUNS.get(rid)
        if live:
            live.setdefault("checkpoints", []).append(public)
            live["checkpoints"] = live["checkpoints"][-24:]
    return public


def list_checkpoints(run_id: Optional[str] = None, limit: int = 30) -> list[dict[str, Any]]:
    root = Path(chats_dir()) / "checkpoints"
    if not root.is_dir():
        return []
    files: list[Path] = []
    if run_id:
        d = root / "".join(c if c.isalnum() or c in "-_" else "_" for c in run_id)
        files = sorted(d.glob("cp_*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if d.is_dir() else []
    else:
        files = sorted(root.glob("*/*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in files[: max(1, min(80, int(limit)))]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append({k: data.get(k) for k in ("id", "run_id", "kind", "summary", "created_at")})
        except (OSError, json.JSONDecodeError):
            continue
    return out


def restore_checkpoint(checkpoint_id: str) -> dict[str, Any]:
    cid = (checkpoint_id or "").strip()
    if not cid:
        return {"ok": False, "error": "missing checkpoint id"}
    root = Path(chats_dir()) / "checkpoints"
    path = None
    if root.is_dir():
        for p in root.glob(f"*/{cid}.json"):
            path = p
            break
    if path is None or not path.exists():
        return {"ok": False, "error": "unknown checkpoint"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "error": str(e)}
    payload = data.get("payload") or {}
    kind = data.get("kind")
    if kind == "workspace_write":
        rel = payload.get("path") or ""
        before = payload.get("before")
        if rel is None or before is None:
            return {"ok": False, "error": "checkpoint has no file snapshot"}
        from tools.workspace_io import write_file_raw

        try:
            write_file_raw(rel, before)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "restored": rel, "kind": kind, "id": cid}
    return {
        "ok": False,
        "error": f"Cannot automatically undo {kind}. The command was recorded only.",
        "kind": kind,
        "summary": data.get("summary"),
    }
