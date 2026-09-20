"""Compact SWE tool catalog. Stable names keep the prompt prefix cacheable."""
from __future__ import annotations

import json
from typing import Any, Optional

from tools.artifact_store import read_artifact, write_artifact
from tools.diagnostics import run_python_tests, syntax_check_python
from tools.file_grep import grep_files
from tools.overlay_workspace import OverlayWorkspace
from tools.python_sandbox import run_sandboxed_python

TOOL_CATALOG = [
    {
        "name": "list_dir",
        "risk": "read",
        "description": "List relative file paths in the workspace",
        "args": {"path": "optional subdirectory"},
    },
    {
        "name": "grep",
        "risk": "read",
        "description": "Search file contents (literal by default)",
        "args": {"pattern": "string", "regex": "bool optional", "max_results": "int optional"},
    },
    {
        "name": "read_file",
        "risk": "read",
        "description": "Read a file with 1-based line numbers. Use offset/limit for large files.",
        "args": {"path": "string", "offset": "int optional", "limit": "int optional"},
    },
    {
        "name": "apply_patch",
        "risk": "write",
        "description": "Replace old with new exactly once in path. old must be unique.",
        "args": {"path": "string", "old": "string", "new": "string"},
    },
    {
        "name": "write_file",
        "risk": "write",
        "description": "Create or replace an entire file. Prefer apply_patch for edits.",
        "args": {"path": "string", "content": "string"},
    },
    {
        "name": "run_tests",
        "risk": "read",
        "description": "Run pytest/unittest against the overlay (isolated temp copy).",
        "args": {"path": "optional tests path"},
    },
    {
        "name": "run_python",
        "risk": "read",
        "description": "Execute a short Python snippet in the sandbox (no workspace disk).",
        "args": {"code": "string"},
    },
    {
        "name": "update_plan",
        "risk": "read",
        "description": "Replace the current todo list",
        "args": {"items": [{"id": "string", "status": "pending|active|done", "text": "string"}]},
    },
    {
        "name": "write_note",
        "risk": "read",
        "description": "Save a long note to the artifact store; later steps get a handle.",
        "args": {"name": "string", "content": "string"},
    },
    {
        "name": "finish",
        "risk": "read",
        "description": "End the run. Only after tests/diagnostics, or a clear blocker.",
        "args": {"summary": "string"},
    },
]


def catalog_prompt() -> str:
    lines = []
    for t in TOOL_CATALOG:
        args = json.dumps(t.get("args") or {}, ensure_ascii=False)
        lines.append(f"- {t['name']}: {t['description']} args={args}")
    return "\n".join(lines)


def dispatch(
    workspace: OverlayWorkspace,
    name: str,
    args: Optional[dict[str, Any]],
    *,
    run_id: str,
    plan: list[dict[str, Any]],
    allow_writes: bool = True,
) -> dict[str, Any]:
    args = args or {}
    n = (name or "").strip()
    if n == "list_dir":
        prefix = str(args.get("path") or "").replace("\\", "/").strip().strip("/")
        paths = workspace.list_rel_paths()
        if prefix:
            paths = [p for p in paths if p == prefix or p.startswith(prefix + "/")]
        return {"ok": True, "paths": paths[:400], "count": len(paths)}
    if n == "grep":
        pattern = str(args.get("pattern") or "")
        regex = bool(args.get("regex"))
        max_results = int(args.get("max_results") or 40)
        raw = grep_files(
            workspace.root,
            pattern,
            max_results=max(1, min(max_results, 80)),
            fixed_string=not regex,
            ignore_case=True,
        )
        matches = []
        root_s = str(workspace.root)
        for m in raw.get("matches") or []:
            abs_path = str(m.get("path") or "")
            rel = abs_path
            if abs_path.startswith(root_s):
                rel = abs_path[len(root_s) :].lstrip("\\/").replace("\\", "/")
            matches.append({"path": rel, "line": m.get("line"), "snippet": (m.get("snippet") or "")[:200]})
        return {"ok": raw.get("ok", False), "engine": raw.get("engine"), "matches": matches, "truncated": raw.get("truncated")}
    if n == "read_file":
        return workspace.read(
            str(args.get("path") or ""),
            offset=int(args.get("offset") or 1),
            limit=int(args.get("limit") or 120),
        )
    if n == "apply_patch":
        if not allow_writes:
            return {"ok": False, "error": "read-only subagent cannot patch"}
        return workspace.apply_patch(str(args.get("path") or ""), str(args.get("old") or ""), str(args.get("new") or ""))
    if n == "write_file":
        if not allow_writes:
            return {"ok": False, "error": "read-only subagent cannot write"}
        return workspace.write(str(args.get("path") or ""), str(args.get("content") or ""))
    if n == "run_tests":
        syn = syntax_check_python(workspace)
        tests = run_python_tests(workspace, path=str(args.get("path") or ""))
        return {"ok": bool(tests.get("passed")) and bool(syn.get("ok")), "syntax": syn, "tests": tests}
    if n == "run_python":
        return run_sandboxed_python(str(args.get("code") or ""), timeout_sec=20.0)
    if n == "update_plan":
        items = args.get("items") if isinstance(args.get("items"), list) else []
        plan.clear()
        for it in items[:16]:
            if not isinstance(it, dict):
                continue
            plan.append(
                {
                    "id": str(it.get("id") or len(plan) + 1),
                    "status": str(it.get("status") or "pending"),
                    "text": str(it.get("text") or "")[:300],
                }
            )
        return {"ok": True, "plan": plan}
    if n == "write_note":
        return write_artifact(str(args.get("name") or "note.md"), str(args.get("content") or ""), run_id=run_id)
    if n == "read_note":
        return read_artifact(str(args.get("name") or ""), run_id=run_id)
    if n == "finish":
        return {"ok": True, "finished": True, "summary": str(args.get("summary") or "")}
    return {"ok": False, "error": f"unknown tool: {n}"}
