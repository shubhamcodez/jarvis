"""HTTP API for custom agents."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from custom_agents import scheduler, store

router = APIRouter(prefix="/custom-agents", tags=["custom-agents"])

TOOL_GROUPS = [
    {
        "id": "research",
        "label": "Research",
        "tools": ["coding", "finance"],
    },
    {
        "id": "computer",
        "label": "This computer",
        "tools": ["desktop", "shell"],
    },
    {
        "id": "google",
        "label": "Google",
        "tools": ["google"],
    },
    {
        "id": "high_impact",
        "label": "Needs approval",
        "tools": ["send", "delete"],
    },
]

TOOL_INFO = [
    {"name": "coding", "description": "Read and edit a linked project, and run tests"},
    {"name": "finance", "description": "Quotes and short market comparisons"},
    {"name": "desktop", "description": "Look at the screen and ask before clicking"},
    {"name": "shell", "description": "Run terminal commands on this PC"},
    {"name": "google", "description": "Calendar and Gmail for the signed-in account"},
    {"name": "send", "description": "Send email or other outbound messages"},
    {"name": "delete", "description": "Delete files, events, or messages"},
]


class CreateAgentBody(BaseModel):
    name: str = "New Agent"
    brief: str = ""
    title: Optional[str] = None
    description: Optional[str] = None
    emoji: Optional[str] = None


class PatchAgentBody(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    brief: Optional[str] = None
    emoji: Optional[str] = None
    approval_boundary: Optional[str] = None
    pinned: Optional[bool] = None
    hidden: Optional[bool] = None
    memory_enabled: Optional[bool] = None
    memory: Optional[str] = None
    tools: Optional[list[str]] = None


class MemoryBody(BaseModel):
    content: str = ""


class SkillBody(BaseModel):
    name: str = "Skill"
    description: str = ""
    body: str = ""
    skill_id: Optional[str] = None


class RoutineSchedule(BaseModel):
    kind: str = "daily"
    time: str = "08:00"
    timezone: str = "local"
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    at: str = ""
    interval_minutes: int = 60


class RoutineBody(BaseModel):
    name: str = "Routine"
    prompt: str = ""
    enabled: bool = True
    skill_id: Optional[str] = None
    run_mode: str = "isolated"
    respect_quiet_hours: bool = True
    schedule: RoutineSchedule = Field(default_factory=RoutineSchedule)


def _require(agent_id: str) -> dict:
    agent = store.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.get("/tools")
def tool_catalog():
    return {"tools": TOOL_INFO, "groups": TOOL_GROUPS}


@router.get("")
def list_custom_agents(include_hidden: bool = Query(True)):
    return {"agents": store.list_agents(include_hidden=include_hidden)}


@router.post("")
def create_custom_agent(body: CreateAgentBody):
    return store.create_agent(
        name=body.name,
        brief=body.brief,
        title=body.title,
        description=body.description,
        emoji=body.emoji,
    )


@router.get("/{agent_id}")
def get_custom_agent(agent_id: str):
    return _require(agent_id)


@router.patch("/{agent_id}")
def patch_custom_agent(agent_id: str, body: PatchAgentBody):
    _require(agent_id)
    updated = store.update_agent(agent_id, body.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Agent not found")
    return updated


@router.post("/{agent_id}/duplicate")
def duplicate_custom_agent(agent_id: str):
    _require(agent_id)
    copy = store.duplicate_agent(agent_id)
    if not copy:
        raise HTTPException(status_code=404, detail="Agent not found")
    return copy


@router.delete("/{agent_id}")
def delete_custom_agent(agent_id: str):
    if not store.delete_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"ok": True}


@router.put("/{agent_id}/memory")
def save_memory(agent_id: str, body: MemoryBody):
    _require(agent_id)
    updated = store.set_memory(agent_id, body.content)
    if not updated:
        raise HTTPException(status_code=404, detail="Agent not found")
    return updated


@router.put("/{agent_id}/skills")
def save_skill(agent_id: str, body: SkillBody):
    _require(agent_id)
    updated = store.upsert_skill(
        agent_id,
        name=body.name,
        description=body.description,
        body=body.body,
        skill_id=body.skill_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Agent not found")
    return updated


@router.delete("/{agent_id}/skills/{skill_id}")
def remove_skill(agent_id: str, skill_id: str):
    _require(agent_id)
    updated = store.delete_skill(agent_id, skill_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Agent not found")
    return updated


@router.post("/{agent_id}/knowledge")
async def upload_knowledge(agent_id: str, files: list[UploadFile] = File(...)):
    _require(agent_id)
    added = []
    latest = None
    for upload in files:
        data = await upload.read()
        latest = store.add_knowledge(agent_id, upload.filename or "file", data)
        if latest and latest.get("added"):
            added.append(latest["added"])
    if not latest:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"files": latest.get("files") or [], "added": added}


@router.delete("/{agent_id}/knowledge/{filename}")
def remove_knowledge(agent_id: str, filename: str):
    _require(agent_id)
    updated = store.delete_knowledge(agent_id, filename)
    if not updated:
        raise HTTPException(status_code=404, detail="Agent not found")
    return updated


@router.post("/{agent_id}/routines")
def create_routine(agent_id: str, body: RoutineBody):
    _require(agent_id)
    updated = store.upsert_routine(agent_id, body.model_dump())
    if not updated:
        raise HTTPException(status_code=404, detail="Agent not found")
    return updated


@router.put("/{agent_id}/routines/{routine_id}")
def update_routine(agent_id: str, routine_id: str, body: RoutineBody):
    _require(agent_id)
    updated = store.upsert_routine(agent_id, body.model_dump(), routine_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Agent not found")
    return updated


@router.delete("/{agent_id}/routines/{routine_id}")
def remove_routine(agent_id: str, routine_id: str):
    _require(agent_id)
    updated = store.delete_routine(agent_id, routine_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Agent not found")
    return updated


@router.post("/{agent_id}/routines/{routine_id}/test")
async def test_routine(agent_id: str, routine_id: str):
    _require(agent_id)
    try:
        return await scheduler.execute_routine(agent_id, routine_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
