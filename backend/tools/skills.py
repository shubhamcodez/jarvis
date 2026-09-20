"""Portable workspace skills: AGENTS.md-style SKILL.md playbooks loaded on demand."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from config import get_workspace_root

SKILL_DIRS = (
    ".agents/skills",
    ".ada/skills",
    ".cursor/skills",
    ".claude/skills",
    "skills",
)

_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    body = text
    m = _FRONT.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip().strip('"').strip("'")
        body = text[m.end() :]
    return meta, body.strip()


def discover_skills(workspace_root: Optional[str] = None, max_skills: int = 40) -> list[dict[str, Any]]:
    raw = (workspace_root or get_workspace_root() or "").strip()
    if not raw:
        return []
    root = Path(raw)
    if not root.is_dir():
        return []
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rel in SKILL_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("SKILL.md")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta, body = _parse_frontmatter(text)
            name = meta.get("name") or path.parent.name
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {
                    "name": name,
                    "description": meta.get("description") or (body.splitlines()[0] if body else name),
                    "path": str(path.relative_to(root)).replace("\\", "/"),
                    "body": body,
                }
            )
            if len(found) >= max_skills:
                return found
    return found


def catalog_text(skills: list[dict[str, Any]]) -> str:
    if not skills:
        return ""
    lines = ["WORKSPACE SKILLS (load only when relevant; user can /skill-name):"]
    for s in skills:
        lines.append(f"- /{s['name']}: {s['description'][:160]}")
    return "\n".join(lines)


def select_skills(skills: list[dict[str, Any]], message: str, *, max_full: int = 2) -> list[dict[str, Any]]:
    msg = (message or "").lower()
    if not msg or not skills:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for s in skills:
        name = str(s.get("name") or "").lower()
        desc = str(s.get("description") or "").lower()
        score = 0
        if f"/{name}" in msg or f"skill {name}" in msg or f"use {name}" in msg:
            score += 10
        if name and name in msg.replace("-", " "):
            score += 4
        words = [w for w in re.findall(r"[a-z0-9]{4,}", desc) if w not in {"this", "that", "with", "from"}]
        score += sum(1 for w in words if w in msg)
        if score:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:max_full]]


def format_selected(selected: list[dict[str, Any]], max_chars: int = 3500) -> str:
    if not selected:
        return ""
    parts = ["ACTIVE SKILLS (follow these playbooks):"]
    used = 0
    for s in selected:
        block = f"### /{s['name']}\n{s['body']}"
        if used + len(block) > max_chars:
            remain = max_chars - used - 20
            if remain > 200:
                parts.append(block[:remain] + "\n…")
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def skills_for_prompt(message: str, workspace_root: Optional[str] = None) -> str:
    skills = discover_skills(workspace_root)
    if not skills:
        return ""
    cat = catalog_text(skills)
    full = format_selected(select_skills(skills, message))
    return "\n\n".join(p for p in (cat, full) if p)
