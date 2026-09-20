"""Generate a starter AGENTS.md from a linked workspace (Claude Code /init)."""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from config import get_workspace_root
from tools.project_repository import _SKIP_DIR_NAMES, _TEXT_SUFFIXES

_LANG = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript/React",
    ".js": "JavaScript",
    ".jsx": "JavaScript/React",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".md": "Markdown",
}


def _scan(root: Path, max_files: int = 400) -> dict[str, Any]:
    langs: Counter[str] = Counter()
    tests: list[str] = []
    rules: list[str] = []
    counted = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIR_NAMES and not d.startswith("."))
        rel_dir = Path(dirpath).relative_to(root)
        for fn in filenames:
            counted += 1
            if counted > max_files:
                return {"langs": langs, "tests": tests[:12], "rules": rules}
            suf = Path(fn).suffix.lower()
            if suf in _LANG:
                langs[_LANG[suf]] += 1
            elif suf in _TEXT_SUFFIXES:
                langs[suf] += 1
            rel = (rel_dir / fn).as_posix() if rel_dir.parts else fn
            low = rel.lower()
            if fn in {"AGENTS.md", "ADA.md", "CLAUDE.md", ".ada/rules.md"} or rel in {
                "AGENTS.md",
                "ADA.md",
                "CLAUDE.md",
            }:
                rules.append(rel.replace("\\", "/"))
            if "test" in low and suf in {".py", ".ts", ".js", ".go", ".rs"}:
                tests.append(rel.replace("\\", "/"))
    return {"langs": langs, "tests": tests[:12], "rules": rules}


def draft_agents_md(workspace_root: Optional[str] = None) -> dict[str, Any]:
    raw = (workspace_root or get_workspace_root() or "").strip()
    if not raw:
        return {"ok": False, "error": "No workspace folder is linked."}
    root = Path(raw)
    if not root.is_dir():
        return {"ok": False, "error": f"Workspace is not a directory: {root}"}
    info = _scan(root)
    langs = ", ".join(f"{k} ({v})" for k, v in info["langs"].most_common(6)) or "unknown"
    tests = info["tests"]
    test_line = ", ".join(tests[:8]) if tests else "(no test files found — add pytest/unittest)"
    existing = (root / "AGENTS.md").is_file()
    body = (
        f"# AGENTS.md\n\n"
        f"Project: `{root.name}`\n\n"
        f"## Stack\n\n{langs}\n\n"
        f"## Tests\n\n{test_line}\n\n"
        f"## How to work here\n\n"
        f"- Prefer small, unique patches over rewriting files.\n"
        f"- Do not modify generated fixtures or vendor directories.\n"
        f"- Run the project tests after edits.\n"
        f"- Put secrets in `.env` — never commit them.\n"
    )
    return {
        "ok": True,
        "exists": existing,
        "path": "AGENTS.md",
        "markdown": body,
        "languages": dict(info["langs"]),
    }


def write_agents_md(*, force: bool = False, workspace_root: Optional[str] = None) -> dict[str, Any]:
    draft = draft_agents_md(workspace_root)
    if not draft.get("ok"):
        return draft
    root = Path((workspace_root or get_workspace_root() or "").strip())
    dest = root / "AGENTS.md"
    if dest.is_file() and not force:
        return {
            **draft,
            "written": False,
            "error": "AGENTS.md already exists. Pass force to replace.",
        }
    dest.write_text(draft["markdown"], encoding="utf-8")
    return {**draft, "written": True, "exists": True}
