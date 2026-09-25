"""Run custom-agent routines on a timer."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

_TICK_SECONDS = 30


def _zone(name: str):
    raw = (name or "local").strip()
    if not raw or raw.lower() == "local":
        return datetime.now().astimezone().tzinfo or timezone.utc
    try:
        return ZoneInfo(raw)
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour, minute = (value or "08:00").split(":", 1)
        return max(0, min(23, int(hour))), max(0, min(59, int(minute)))
    except Exception:
        return 8, 0


def schedule_next(routine: dict[str, Any], *, after: datetime | None = None) -> str:
    """Next UTC ISO time this routine should run."""
    schedule = routine.get("schedule") or {}
    kind = schedule.get("kind") or "daily"
    tz = _zone(str(schedule.get("timezone") or "local"))
    now = (after or datetime.now(timezone.utc)).astimezone(tz)
    if kind == "interval":
        minutes = max(5, int(schedule.get("interval_minutes") or 60))
        nxt = now + timedelta(minutes=minutes)
    elif kind == "once":
        raw = str(schedule.get("at") or "").strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=tz)
            nxt = parsed.astimezone(tz)
        except Exception:
            nxt = now + timedelta(minutes=5)
    else:
        hour, minute = _parse_hhmm(str(schedule.get("time") or "08:00"))
        weekdays = schedule.get("weekdays")
        allowed = {int(d) for d in weekdays} if isinstance(weekdays, list) and weekdays else set(range(7))
        nxt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        for _ in range(8):
            if nxt.weekday() in allowed:
                break
            nxt += timedelta(days=1)
    return nxt.astimezone(timezone.utc).isoformat()


def _due(routine: dict[str, Any], now: datetime) -> bool:
    if not routine.get("enabled", True):
        return False
    raw = str(routine.get("next_run_at") or "").strip()
    if not raw:
        return True
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when <= now


async def execute_routine(agent_id: str, routine_id: str) -> dict[str, Any]:
    from config import get_llm_api_key, get_llm_provider, in_quiet_hours
    from memory.chat_log import append_chat_log

    from custom_agents.context import build_agent_system_prompt
    from custom_agents.store import get_agent, save_agent

    agent = get_agent(agent_id)
    if not agent:
        raise LookupError("Agent not found")
    routine = next((r for r in agent.get("routines") or [] if r.get("id") == routine_id), None)
    if not routine:
        raise LookupError("Routine not found")
    if routine.get("respect_quiet_hours", True) and in_quiet_hours():
        routine["last_status"] = "quiet_hours"
        routine["next_run_at"] = schedule_next(routine)
        save_agent(agent)
        return {"chat_id": agent.get("chat_id"), "status": "quiet_hours"}

    skill_body = ""
    skill_id = routine.get("skill_id") or ""
    if skill_id:
        for skill in agent.get("skills") or []:
            if skill.get("id") == skill_id:
                skill_body = (skill.get("body") or "").strip()
                break
    prompt = (routine.get("prompt") or "").strip() or "Run this agent's standing job."
    if skill_body:
        prompt = f"{prompt}\n\nFollow this skill:\n{skill_body}"

    chat_id = agent.get("chat_id") or ""
    status = "ok"
    error = ""
    reply = ""
    try:
        from agents.router import create_router_graph

        graph = create_router_graph()
        result = await graph.ainvoke(
            {
                "message": prompt,
                "chat_id": chat_id,
                "api_key": get_llm_api_key() or "",
                "provider": get_llm_provider(),
                "coding_mode": False,
                "coding_project_context": "",
                "custom_agent_id": agent_id,
                "custom_agent_system": build_agent_system_prompt(agent, prompt),
                "custom_agent_tools": list(agent.get("tools") or []),
            }
        )
        reply = ((result or {}).get("reply") or "").strip() or "No response."
    except Exception as exc:
        status = "error"
        error = str(exc)
        reply = f"Routine failed: {error}"

    if chat_id:
        append_chat_log("user", prompt, chat_id=chat_id)
        append_chat_log("assistant", reply, chat_id=chat_id)

    fresh = get_agent(agent_id) or agent
    current = next((r for r in fresh.get("routines") or [] if r.get("id") == routine_id), routine)
    current["last_status"] = status
    current["last_error"] = error
    runs = list(current.get("runs") or [])
    runs.insert(0, {"at": datetime.now(timezone.utc).isoformat(), "status": status, "error": error})
    current["runs"] = runs[:8]
    kind = (current.get("schedule") or {}).get("kind")
    if kind == "once":
        current["enabled"] = False
        current["next_run_at"] = ""
    else:
        current["next_run_at"] = schedule_next(current)
    save_agent(fresh)
    return {"chat_id": chat_id, "status": status, "error": error}


async def tick() -> None:
    from custom_agents.store import list_agents

    now = datetime.now(timezone.utc)
    for agent in list_agents(include_hidden=True):
        for routine in agent.get("routines") or []:
            if _due(routine, now):
                try:
                    await execute_routine(agent["id"], routine["id"])
                except Exception:
                    continue


async def scheduler_loop() -> None:
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(_TICK_SECONDS)
