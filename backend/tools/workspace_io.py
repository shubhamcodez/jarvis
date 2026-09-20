"""Allowlisted workspace file IO. Paths must stay under the linked root."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from config import get_workspace_root, set_workspace_root
from tools.project_repository import build_repository_snapshot

_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        "dist",
        "build",
        ".next",
        "target",
        ".idea",
        ".vscode",
        "coverage",
    }
)


def _root() -> Path:
    raw = get_workspace_root()
    if not raw:
        raise ValueError("No workspace folder is linked.")
    p = Path(raw).expanduser().resolve()
    if not p.is_dir():
        raise ValueError(f"Workspace path is not a directory: {p}")
    return p


def resolve_under_root(rel_path: str, root: Optional[Path] = None) -> Path:
    base = root or _root()
    rel = (rel_path or "").replace("\\", "/").strip().lstrip("/")
    if not rel or ".." in Path(rel).parts:
        raise ValueError("Invalid relative path.")
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError as e:
        raise ValueError("Path escapes the workspace root.") from e
    return target


def link_workspace(path: str) -> dict[str, Any]:
    p = Path(path or "").expanduser()
    try:
        p = p.resolve()
    except OSError as e:
        return {"ok": False, "error": str(e)}
    if not p.is_dir():
        return {"ok": False, "error": f"Not a directory: {p}"}
    set_workspace_root(str(p))
    return {"ok": True, "path": str(p), "label": p.name}


def unlink_workspace() -> None:
    set_workspace_root("")


def status() -> dict[str, Any]:
    raw = get_workspace_root()
    if not raw:
        return {"ok": True, "linked": False, "path": "", "label": ""}
    p = Path(raw)
    return {
        "ok": True,
        "linked": p.is_dir(),
        "path": str(p),
        "label": p.name if p.is_dir() else "",
    }


def list_rel_paths(max_files: int = 4000) -> list[str]:
    root = _root()
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIR_NAMES and not d.startswith("."))
        rel_dir = Path(dirpath).relative_to(root)
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            rp = (rel_dir / fn).as_posix() if rel_dir.parts else fn
            out.append(rp.replace("\\", "/"))
            if len(out) >= max_files:
                return out
    return out


def read_file(rel_path: str, max_bytes: int = 400_000) -> dict[str, Any]:
    target = resolve_under_root(rel_path)
    if not target.is_file():
        return {"ok": False, "error": "File not found.", "path": rel_path}
    data = target.read_bytes()
    if b"\0" in data[:8192]:
        return {"ok": False, "error": "Binary file.", "path": rel_path}
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return {"ok": True, "path": rel_path, "content": data.decode("utf-8", errors="replace")}


def write_file_raw(rel_path: str, content: str) -> dict[str, Any]:
    """Write without policy/checkpoint (used to restore a checkpoint)."""
    target = resolve_under_root(rel_path)
    if not target.parent.exists():
        return {"ok": False, "error": "Parent folder missing.", "path": rel_path}
    text = content if isinstance(content, str) else str(content or "")
    target.write_text(text, encoding="utf-8")
    return {"ok": True, "path": rel_path}


def write_file(rel_path: str, content: str) -> dict[str, Any]:
    from agents.execution_policy import deny_if_blocked
    from agents.run_control import record_checkpoint

    blocked = deny_if_blocked("workspace_write")
    if blocked:
        return {**blocked, "path": rel_path}
    target = resolve_under_root(rel_path)
    if not target.parent.exists():
        return {"ok": False, "error": "Parent folder missing.", "path": rel_path}
    text = content if isinstance(content, str) else str(content or "")
    before = ""
    if target.is_file():
        try:
            before = target.read_text(encoding="utf-8")
        except OSError:
            before = ""
    try:
        record_checkpoint(
            None,
            kind="workspace_write",
            summary=f"write {rel_path}",
            payload={"path": rel_path, "before": before, "after": text},
        )
    except Exception:
        pass
    target.write_text(text, encoding="utf-8")
    return {"ok": True, "path": rel_path}


def snapshot() -> dict[str, Any]:
    root = _root()
    text = build_repository_snapshot(str(root))
    rels = list_rel_paths()
    return {
        "ok": True,
        "path": str(root),
        "label": root.name,
        "snapshot": text,
        "rel_paths": rels,
    }
