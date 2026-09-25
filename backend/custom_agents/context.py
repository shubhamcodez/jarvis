"""System prompt and tool filtering for a custom agent."""
from __future__ import annotations

import re
from typing import Any

from custom_agents.store import read_knowledge_text

_WORD = re.compile(r"[a-z0-9]{3,}")


def filter_supervisor_plan(agents: list[dict], tools: list[str]) -> list[dict]:
    """Keep only specialist steps whose agent name is in the custom agent's tool list."""
    allowed = {str(name).strip().lower() for name in (tools or []) if str(name).strip()}
    if not allowed:
        return []
    kept: list[dict] = []
    for item in agents or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("agent") or "").strip().lower()
        if name in allowed:
            kept.append(item)
    return kept


def _skill_block(agent: dict[str, Any], message: str) -> str:
    skills = list(agent.get("skills") or [])
    if not skills:
        return ""
    words = set(_WORD.findall((message or "").lower()))
    chosen = []
    for skill in skills:
        blob = f"{skill.get('name') or ''} {skill.get('description') or ''}".lower()
        if words and words.intersection(_WORD.findall(blob)):
            chosen.append(skill)
    if not chosen:
        chosen = skills[:2]
    parts = []
    for skill in chosen[:3]:
        body = (skill.get("body") or "").strip()
        if len(body) > 4000:
            body = body[:4000] + "\n…"
        parts.append(
            f"### Skill: {skill.get('name') or 'Skill'}\n{skill.get('description') or ''}\n{body}".strip()
        )
    return "\n\n".join(parts)


def _knowledge_block(agent: dict[str, Any], message: str) -> str:
    files = list(agent.get("knowledge") or [])
    if not files:
        return ""
    words = set(_WORD.findall((message or "").lower()))
    chunks: list[str] = []
    for entry in files:
        if not entry.get("indexed"):
            continue
        text = read_knowledge_text(agent["id"], entry.get("name") or "")
        if not text:
            continue
        if len(text) <= 4000:
            chunks.append(f"### {entry.get('name')}\n{text}")
            continue
        if not words:
            chunks.append(f"### {entry.get('name')}\n{text[:2000]}")
            continue
        hits = []
        for line in text.splitlines():
            if words.intersection(_WORD.findall(line.lower())):
                hits.append(line)
            if len(hits) >= 12:
                break
        if hits:
            chunks.append(f"### {entry.get('name')}\n" + "\n".join(hits))
    return "\n\n".join(chunks[:4])


def build_agent_system_prompt(profile: dict[str, Any] | None, message: str = "") -> str | None:
    if not profile:
        return None
    lines = [
        f"You are {profile.get('name') or 'a custom agent'}.",
    ]
    if profile.get("title"):
        lines.append(f"Title: {profile['title']}")
    if profile.get("description"):
        lines.append(profile["description"].strip())
    if profile.get("approval_boundary"):
        lines.append("Approval boundary: " + profile["approval_boundary"].strip())
    tools = profile.get("tools") or []
    if tools:
        lines.append("Enabled tools: " + ", ".join(str(t) for t in tools) + ".")
        lines.append("Do not use a specialist or action that is not in that list.")
    if profile.get("memory_enabled", True) and (profile.get("memory") or "").strip():
        lines.append("Memory:\n" + profile["memory"].strip())
    skills = _skill_block(profile, message)
    if skills:
        lines.append(skills)
    knowledge = _knowledge_block(profile, message)
    if knowledge:
        lines.append("Reference files:\n" + knowledge)
    return "\n\n".join(line for line in lines if line).strip() or None
