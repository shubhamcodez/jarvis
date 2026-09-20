"""Setup checkup (Claude Code /doctor). Never prints secret values."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from config import api_keys_status, chats_dir, get_llm_provider, get_workspace_root
from tools.git_history import git_available, is_git_repo
from tools.skills import discover_skills
from tools.slash_commands import discover_commands


def run_doctor() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("python", True, f"{sys.version.split()[0]} ({sys.executable})")
    keys = api_keys_status()
    add("openai_key", bool(keys.get("openai_set")), "set" if keys.get("openai_set") else "missing OPENAI_API_KEY")
    add("xai_key", bool(keys.get("xai_set")), "set" if keys.get("xai_set") else "missing XAI_API_KEY")
    add("llm_provider", True, get_llm_provider() or "openai")

    root = (get_workspace_root() or "").strip()
    linked = bool(root) and Path(root).is_dir()
    add("workspace", linked, root if linked else "no folder linked")
    if linked:
        add("git_repo", is_git_repo(root), "yes" if is_git_repo(root) else "not a git repo")
        add("agents_md", (Path(root) / "AGENTS.md").is_file(), "present" if (Path(root) / "AGENTS.md").is_file() else "run /init")
        skills = discover_skills(root)
        cmds = discover_commands(root)
        add("skills", True, f"{len(skills)} discovered")
        add("slash_commands", True, f"{len(cmds)} file-based commands")
    add("git_binary", git_available(), "on PATH" if git_available() else "git not found")

    try:
        d = chats_dir()
        d.mkdir(parents=True, exist_ok=True)
        add("chats_dir", d.is_dir(), str(d))
    except OSError as e:
        add("chats_dir", False, str(e))

    add("poetry", bool(shutil.which("poetry")), "on PATH" if shutil.which("poetry") else "optional")
    ok = all(c["ok"] for c in checks if c["name"] in {"python", "workspace", "chats_dir"})
    # usable if at least one LLM key exists
    has_key = bool(keys.get("openai_set") or keys.get("xai_set"))
    add("llm_ready", has_key, "at least one provider key" if has_key else "add OPENAI_API_KEY or XAI_API_KEY to .env")
    return {"ok": ok and has_key, "checks": checks}


def doctor_markdown() -> str:
    data = run_doctor()
    lines = ["# Doctor", ""]
    for c in data["checks"]:
        mark = "ok" if c["ok"] else "FAIL"
        lines.append(f"- **{c['name']}**: {mark} — {c['detail']}")
    lines.append("")
    lines.append("All good." if data["ok"] else "Fix the FAIL rows, then run `/doctor` again.")
    return "\n".join(lines)
