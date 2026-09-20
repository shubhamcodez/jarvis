"""Typed tool registry: capabilities, not unrestricted interfaces."""
from __future__ import annotations

from typing import Any

from tools.workspace_io import list_rel_paths, read_file, snapshot, write_file

TOOLS: dict[str, dict[str, Any]] = {
    "list_dir": {
        "risk": "read",
        "description": "List relative paths in the linked workspace",
    },
    "read_file": {
        "risk": "read",
        "description": "Read a UTF-8 file under the workspace root",
        "args": {"rel_path": "string"},
    },
    "propose_file_edit": {
        "risk": "write",
        "description": "Write a file under the workspace (user review in UI for coding proposals)",
        "args": {"rel_path": "string", "content": "string"},
    },
    "workspace_snapshot": {
        "risk": "read",
        "description": "Bounded repository snapshot for the coding agent",
    },
    "grep": {
        "risk": "read",
        "description": "Search file contents under the workspace (ripgrep or scan)",
    },
    "apply_patch": {
        "risk": "write",
        "description": "Unique search-replace patch in a workspace file",
        "args": {"rel_path": "string", "old": "string", "new": "string"},
    },
    "run_tests": {
        "risk": "read",
        "description": "Compile + pytest/unittest against an isolated overlay copy",
    },
    "swe_loop": {
        "risk": "write",
        "description": "Software-engineering loop: localize, patch, test, critic",
    },
    "github_issue": {
        "risk": "read",
        "description": "Fetch a GitHub issue via gh (implement #N)",
    },
    "weather": {"risk": "read", "description": "Current weather for a place"},
    "web_search": {"risk": "read", "description": "Web search (untrusted content)"},
    "finance_quote": {"risk": "read", "description": "Market data via yfinance"},
    "python_sandbox": {"risk": "read", "description": "Run Python in the sandbox"},
    "gmail_send": {"risk": "high_impact", "description": "Send Gmail"},
    "event_delete": {"risk": "high_impact", "description": "Delete a calendar event"},
    "event_create": {"risk": "write", "description": "Create a calendar event"},
    "shell_run": {"risk": "high_impact", "description": "Host shell command (opt-in)"},
    "desktop_gui": {"risk": "high_impact", "description": "Mouse/keyboard on the real desktop"},
}


def list_tools() -> list[dict[str, Any]]:
    return [{"name": k, **v} for k, v in TOOLS.items()]


def risk_for(name: str) -> str:
    rec = TOOLS.get(name) or {}
    return str(rec.get("risk") or "read")


def dispatch_workspace(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "list_dir":
        return {"ok": True, "paths": list_rel_paths()}
    if name == "read_file":
        return read_file(str(args.get("rel_path") or ""))
    if name == "propose_file_edit":
        return write_file(str(args.get("rel_path") or ""), str(args.get("content") or ""))
    if name == "workspace_snapshot":
        return snapshot()
    return {"ok": False, "error": f"unknown workspace tool: {name}"}
