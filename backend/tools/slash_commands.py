"""File-based slash commands (.agents/commands, .claude/commands, …)."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Optional

from config import get_workspace_root
from tools.skills import _parse_frontmatter, discover_skills

COMMAND_DIRS = (
    ".agents/commands",
    ".jarvis/commands",
    ".ada/commands",
    ".cursor/commands",
    ".claude/commands",
)

_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9:_-]{0,48}$")
_CMD_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CMD_TTL = 8.0


def discover_commands(workspace_root: Optional[str] = None, max_commands: int = 40) -> list[dict[str, Any]]:
    raw = (workspace_root or get_workspace_root() or "").strip()
    if not raw:
        return []
    now = time.monotonic()
    hit = _CMD_CACHE.get(raw)
    if hit and now - hit[0] < _CMD_TTL:
        return hit[1]
    root = Path(raw)
    if not root.is_dir():
        return []
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rel in COMMAND_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*.md"):
            if path.name.upper() == "SKILL.MD":
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta, body = _parse_frontmatter(text)
            name = (meta.get("name") or path.stem).strip()
            if path.parent != base:
                ns = path.parent.relative_to(base).as_posix().replace("/", ":")
                if ":" not in name:
                    name = f"{ns}:{name}"
            key = name.lower()
            if not _NAME_RE.match(name) or key in seen:
                continue
            seen.add(key)
            found.append(
                {
                    "name": name,
                    "description": meta.get("description") or (body.splitlines()[0] if body else name),
                    "path": str(path.relative_to(root)).replace("\\", "/"),
                    "body": body,
                    "argument_hint": meta.get("argument-hint") or meta.get("argument_hint") or "",
                }
            )
            if len(found) >= max_commands:
                _CMD_CACHE[raw] = (now, found)
                return found
    _CMD_CACHE[raw] = (now, found)
    return found


def expand_command(body: str, args: str) -> str:
    text = body or ""
    repl = args or ""
    text = text.replace("$ARGUMENTS", repl)
    text = text.replace("{{args}}", repl)
    text = text.replace("$0", repl)
    if "{{args}}" not in (body or "") and "$ARGUMENTS" not in (body or "") and repl:
        text = text.rstrip() + "\n\nUser arguments: " + repl
    return text.strip()


def resolve_named(name: str, args: str = "", workspace_root: Optional[str] = None) -> dict[str, Any]:
    """Match a skill or file-based command. Returns kind=skill|command|unknown."""
    key = (name or "").strip().lstrip("/").lower()
    if not key:
        return {"ok": False, "kind": "unknown", "error": "empty name"}
    for cmd in discover_commands(workspace_root):
        if str(cmd.get("name") or "").lower() == key:
            return {
                "ok": True,
                "kind": "command",
                "name": cmd["name"],
                "prompt": expand_command(cmd.get("body") or "", args),
                "description": cmd.get("description") or "",
            }
    for skill in discover_skills(workspace_root):
        if str(skill.get("name") or "").lower() == key:
            playbook = (skill.get("body") or "").strip()
            user = (args or "").strip()
            prompt = f"Follow skill /{skill['name']}.\n\n{playbook}"
            if user:
                prompt += f"\n\nUser request: {user}"
            return {
                "ok": True,
                "kind": "skill",
                "name": skill["name"],
                "prompt": prompt,
                "description": skill.get("description") or "",
            }
    return {"ok": False, "kind": "unknown", "error": f"no command or skill named /{key}"}


BUILTIN_SLASH = [
    {"name": "help", "description": "Show the chat manual"},
    {"name": "stop", "description": "Cancel the current run"},
    {"name": "compact", "description": "Summarize earlier turns"},
    {"name": "handoff", "description": "Paste-ready session dump"},
    {"name": "recap", "description": "Task, last result, next step"},
    {"name": "btw", "description": "Side question (no agent plan)", "argument_hint": "question"},
    {"name": "side", "description": "Alias for /btw"},
    {"name": "search-memory", "description": "Search old chats", "argument_hint": "query"},
    {"name": "introduce", "description": "Fill the user profile"},
    {"name": "diff", "description": "Git status + diff of the linked folder"},
    {"name": "review", "description": "Read-only diff review"},
    {"name": "rewind", "description": "Drop the last assistant reply"},
    {"name": "undo", "description": "Restore the last edit or overlay checkpoint"},
    {"name": "usage", "description": "Recent token totals"},
    {"name": "init", "description": "Write AGENTS.md if missing"},
    {"name": "doctor", "description": "Setup checkup (never prints secrets)"},
    {"name": "export", "description": "Download this chat as Markdown"},
    {"name": "rename", "description": "Rename this chat", "argument_hint": "title"},
    {"name": "copy", "description": "Copy the last assistant reply"},
    {"name": "context", "description": "Token estimate for this chat"},
    {"name": "plan", "description": "Plan mode, or /plan show", "argument_hint": "show|task"},
    {"name": "loop", "description": "Repeat a check-in", "argument_hint": "5m prompt"},
    {"name": "tasks", "description": "Durable tasks and active runs"},
    {"name": "goal", "description": "Done-condition; critic runs after each turn", "argument_hint": "condition"},
    {"name": "model", "description": "Show or switch provider"},
    {"name": "cd", "description": "Show or relink the workspace"},
    {"name": "effort", "description": "Spend cap: low|medium|high"},
    {"name": "notify", "description": "Desktop alerts when a run finishes"},
    {"name": "commands", "description": "List skills and file-based commands"},
    {"name": "skill", "description": "Run a SKILL.md playbook", "argument_hint": "name"},
]


def catalog(workspace_root: Optional[str] = None) -> dict[str, Any]:
    skills = discover_skills(workspace_root)
    commands = discover_commands(workspace_root)
    return {
        "ok": True,
        "builtins": list(BUILTIN_SLASH),
        "skills": [{"name": s["name"], "description": s.get("description") or ""} for s in skills],
        "commands": [
            {
                "name": c["name"],
                "description": c.get("description") or "",
                "argument_hint": c.get("argument_hint") or "",
            }
            for c in commands
        ],
    }
