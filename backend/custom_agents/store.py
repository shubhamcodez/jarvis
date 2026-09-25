"""Persist custom agents under the app data directory."""
from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import data_root

_LOCK = threading.Lock()
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")

DEFAULT_TOOLS = ["coding", "finance"]


def agents_root() -> Path:
    root = data_root() / "custom_agents"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _agent_dir(agent_id: str) -> Path:
    return agents_root() / agent_id


def _agent_path(agent_id: str) -> Path:
    return _agent_dir(agent_id) / "agent.json"


def _read(agent_id: str) -> Optional[dict[str, Any]]:
    path = _agent_path(agent_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write(agent: dict[str, Any]) -> dict[str, Any]:
    agent["updated_at"] = _now()
    dest = _agent_dir(agent["id"])
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "knowledge").mkdir(exist_ok=True)
    path = dest / "agent.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(agent, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return agent


def list_agents(*, include_hidden: bool = True) -> list[dict[str, Any]]:
    with _LOCK:
        found: list[dict[str, Any]] = []
        root = agents_root()
        for child in root.iterdir():
            if not child.is_dir():
                continue
            agent = _read(child.name)
            if not agent:
                continue
            if not include_hidden and agent.get("hidden"):
                continue
            found.append(agent)
        found.sort(key=lambda a: (not a.get("pinned"), (a.get("name") or "").lower()))
        return found


def get_agent(agent_id: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        return _read(agent_id)


def resolve_agent(custom_agent_id: Optional[str], chat_id: Optional[str]) -> Optional[dict[str, Any]]:
    with _LOCK:
        if custom_agent_id:
            found = _read(custom_agent_id)
            if found:
                return found
        if chat_id:
            for child in agents_root().iterdir():
                if not child.is_dir():
                    continue
                agent = _read(child.name)
                if agent and agent.get("chat_id") == chat_id:
                    return agent
        return None


def create_agent(
    *,
    name: str = "New Agent",
    brief: str = "",
    title: Optional[str] = None,
    description: Optional[str] = None,
    emoji: Optional[str] = None,
) -> dict[str, Any]:
    from memory.chat_log import create_new_chat, rename_chat

    label = (name or "").strip() or "New Agent"
    text = (brief or "").strip()
    if text and label == "New Agent":
        first = text.splitlines()[0].strip()
        label = first[:48].rsplit(" ", 1)[0] if len(first) > 48 else first
        label = label or "New Agent"
    agent_id = uuid.uuid4().hex[:12]
    chat_id = create_new_chat()
    try:
        rename_chat(chat_id, label)
    except Exception:
        pass
    agent = {
        "id": agent_id,
        "name": label,
        "title": (title or "").strip(),
        "description": (description if description is not None else text).strip(),
        "brief": text,
        "emoji": (emoji or "").strip()[:8],
        "approval_boundary": "",
        "pinned": False,
        "hidden": False,
        "memory_enabled": True,
        "memory": "",
        "chat_id": chat_id,
        "tools": list(DEFAULT_TOOLS),
        "skills": [],
        "knowledge": [],
        "routines": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _LOCK:
        return _write(agent)


def update_agent(agent_id: str, patch: dict[str, Any]) -> Optional[dict[str, Any]]:
    allowed = {
        "name",
        "title",
        "description",
        "brief",
        "emoji",
        "approval_boundary",
        "pinned",
        "hidden",
        "memory_enabled",
        "memory",
        "tools",
    }
    with _LOCK:
        agent = _read(agent_id)
        if not agent:
            return None
        for key, value in patch.items():
            if key not in allowed:
                continue
            if key == "emoji" and isinstance(value, str):
                agent[key] = value[:8]
            elif key == "tools" and isinstance(value, list):
                agent[key] = [str(item) for item in value if str(item).strip()]
            elif key in {"pinned", "hidden", "memory_enabled"}:
                agent[key] = bool(value)
            elif isinstance(value, str) or value is None:
                agent[key] = value or ""
        if "name" in patch:
            try:
                from memory.chat_log import rename_chat

                rename_chat(agent.get("chat_id") or "", agent.get("name") or "Agent")
            except Exception:
                pass
        return _write(agent)


def duplicate_agent(agent_id: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        source = _read(agent_id)
        if not source:
            return None
        copy = json.loads(json.dumps(source))
    created = create_agent(
        name=f"{source.get('name') or 'Agent'} copy",
        brief=source.get("brief") or "",
        title=source.get("title") or "",
        description=source.get("description") or "",
        emoji=source.get("emoji") or "",
    )
    created.update(
        {
            "approval_boundary": copy.get("approval_boundary") or "",
            "pinned": False,
            "hidden": bool(copy.get("hidden")),
            "memory_enabled": bool(copy.get("memory_enabled", True)),
            "memory": copy.get("memory") or "",
            "tools": list(copy.get("tools") or DEFAULT_TOOLS),
            "skills": list(copy.get("skills") or []),
            "routines": [],
            "knowledge": [],
        }
    )
    src_know = _agent_dir(agent_id) / "knowledge"
    dest_know = _agent_dir(created["id"]) / "knowledge"
    if src_know.is_dir():
        dest_know.mkdir(parents=True, exist_ok=True)
        for item in src_know.iterdir():
            if item.is_file():
                shutil.copy2(item, dest_know / item.name)
        created["knowledge"] = list(copy.get("knowledge") or [])
    with _LOCK:
        return _write(created)


def delete_agent(agent_id: str) -> bool:
    with _LOCK:
        agent = _read(agent_id)
        if not agent:
            return False
        chat_id = agent.get("chat_id") or ""
        shutil.rmtree(_agent_dir(agent_id), ignore_errors=True)
    if chat_id:
        try:
            from memory.chat_log import delete_chat

            delete_chat(chat_id)
        except Exception:
            pass
    return True


def append_memory_note(agent_id: str, note: str) -> Optional[dict[str, Any]]:
    text = (note or "").strip()
    if not text:
        return get_agent(agent_id)
    with _LOCK:
        agent = _read(agent_id)
        if not agent or not agent.get("memory_enabled", True):
            return agent
        prior = (agent.get("memory") or "").rstrip()
        agent["memory"] = f"{prior}\n- {text}".strip() if prior else f"- {text}"
        return _write(agent)


def set_memory(agent_id: str, content: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        agent = _read(agent_id)
        if not agent:
            return None
        agent["memory"] = content or ""
        return _write(agent)


def upsert_skill(agent_id: str, *, name: str, description: str, body: str, skill_id: Optional[str]) -> Optional[dict[str, Any]]:
    with _LOCK:
        agent = _read(agent_id)
        if not agent:
            return None
        skills = list(agent.get("skills") or [])
        found = None
        if skill_id:
            for skill in skills:
                if skill.get("id") == skill_id:
                    found = skill
                    break
        if found is None:
            found = {"id": uuid.uuid4().hex[:10]}
            skills.append(found)
        found["name"] = (name or "Skill").strip() or "Skill"
        found["description"] = description or ""
        found["body"] = body or ""
        agent["skills"] = skills
        return _write(agent)


def delete_skill(agent_id: str, skill_id: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        agent = _read(agent_id)
        if not agent:
            return None
        agent["skills"] = [s for s in (agent.get("skills") or []) if s.get("id") != skill_id]
        return _write(agent)


def safe_filename(name: str) -> str:
    base = Path(name or "file").name
    cleaned = _SAFE_NAME.sub("_", base).strip(" .")
    return cleaned[:180] or "file"


def knowledge_dir(agent_id: str) -> Path:
    dest = _agent_dir(agent_id) / "knowledge"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def add_knowledge(agent_id: str, filename: str, data: bytes) -> Optional[dict[str, Any]]:
    name = safe_filename(filename)
    textish = _looks_like_text(name, data)
    with _LOCK:
        agent = _read(agent_id)
        if not agent:
            return None
        path = knowledge_dir(agent_id) / name
        path.write_bytes(data)
        files = [f for f in (agent.get("knowledge") or []) if f.get("name") != name]
        entry = {"name": name, "size": len(data), "indexed": textish}
        files.append(entry)
        agent["knowledge"] = files
        saved = _write(agent)
        return {"agent": saved, "added": entry, "files": files}


def delete_knowledge(agent_id: str, filename: str) -> Optional[dict[str, Any]]:
    name = safe_filename(filename)
    with _LOCK:
        agent = _read(agent_id)
        if not agent:
            return None
        path = knowledge_dir(agent_id) / name
        if path.is_file():
            path.unlink()
        agent["knowledge"] = [f for f in (agent.get("knowledge") or []) if f.get("name") != name]
        return _write(agent)


def read_knowledge_text(agent_id: str, filename: str, limit: int = 12000) -> str:
    path = knowledge_dir(agent_id) / safe_filename(filename)
    if not path.is_file():
        return ""
    try:
        raw = path.read_bytes()[: limit * 4]
    except OSError:
        return ""
    if b"\x00" in raw[:512]:
        return ""
    return raw.decode("utf-8", errors="replace")[:limit]


def _looks_like_text(name: str, data: bytes) -> bool:
    if b"\x00" in data[:512]:
        return False
    suffix = Path(name).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".exe", ".dll"}:
        return False
    return True


def upsert_routine(agent_id: str, routine: dict[str, Any], routine_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    from custom_agents.scheduler import schedule_next

    with _LOCK:
        agent = _read(agent_id)
        if not agent:
            return None
        routines = list(agent.get("routines") or [])
        found = None
        if routine_id:
            for item in routines:
                if item.get("id") == routine_id:
                    found = item
                    break
        if found is None:
            found = {"id": uuid.uuid4().hex[:10], "runs": []}
            routines.append(found)
        schedule = routine.get("schedule") if isinstance(routine.get("schedule"), dict) else {}
        found.update(
            {
                "name": (routine.get("name") or "Routine").strip() or "Routine",
                "prompt": routine.get("prompt") or "",
                "enabled": bool(routine.get("enabled", True)),
                "skill_id": routine.get("skill_id") or "",
                "run_mode": routine.get("run_mode") or "isolated",
                "respect_quiet_hours": routine.get("respect_quiet_hours", True) is not False,
                "schedule": {
                    "kind": schedule.get("kind") or "daily",
                    "time": schedule.get("time") or "08:00",
                    "timezone": schedule.get("timezone") or "local",
                    "weekdays": list(schedule.get("weekdays") or [0, 1, 2, 3, 4]),
                    "at": schedule.get("at") or "",
                    "interval_minutes": int(schedule.get("interval_minutes") or 60),
                },
            }
        )
        found["next_run_at"] = schedule_next(found)
        agent["routines"] = routines
        return _write(agent)


def delete_routine(agent_id: str, routine_id: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        agent = _read(agent_id)
        if not agent:
            return None
        agent["routines"] = [r for r in (agent.get("routines") or []) if r.get("id") != routine_id]
        return _write(agent)


def save_agent(agent: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        return _write(agent)
