"""Allowlisted workspace file IO. Paths must stay under the linked root."""
from __future__ import annotations

import os
import time
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
        ".ruff_cache",
        "out",
        "__MACOSX",
        ".gradle",
        ".cargo",
    }
)

_ROOT_CACHE: tuple[str, Path] | None = None
_PATHS_CACHE: tuple[float, list[str]] | None = None
_TREE_CACHE: tuple[float, list[str]] | None = None
_LAST_STAMP: str | None = None
_CACHE_TTL = 1.5


def _root() -> Path:
    global _ROOT_CACHE
    raw = get_workspace_root()
    if not raw:
        raise ValueError("No workspace folder is linked.")
    if _ROOT_CACHE and _ROOT_CACHE[0] == raw:
        return _ROOT_CACHE[1]
    p = Path(raw).expanduser().resolve()
    if not p.is_dir():
        raise ValueError(f"Workspace path is not a directory: {p}")
    _ROOT_CACHE = (raw, p)
    return p


def _clear_ws_caches() -> None:
    global _ROOT_CACHE, _PATHS_CACHE, _TREE_CACHE, _LAST_STAMP
    _ROOT_CACHE = None
    _PATHS_CACHE = None
    _TREE_CACHE = None
    _LAST_STAMP = None


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


_SENSITIVE_DIR_NAMES = frozenset(
    {
        "windows",
        "system32",
        "syswow64",
        "program files",
        "program files (x86)",
        "programdata",
        "$recycle.bin",
        "etc",
        "usr",
        "bin",
        "sbin",
        "root",
        "proc",
        "sys",
        "dev",
    }
)


def _is_sensitive_workspace(p: Path) -> str | None:
    try:
        resolved = p.resolve()
    except OSError:
        return None
    parts = {part.lower() for part in resolved.parts}
    if parts & _SENSITIVE_DIR_NAMES and len(resolved.parts) <= 3:
        return f"Refusing to link a system path: {resolved}"
    home = Path.home().resolve()
    if resolved == home:
        return "Refusing to link the entire home directory. Choose a project folder."
    if resolved == resolved.anchor or str(resolved) in ("/", "C:\\", "C:/"):
        return "Refusing to link a drive root."
    return None


def link_workspace(path: str) -> dict[str, Any]:
    p = Path(path or "").expanduser()
    try:
        p = p.resolve()
    except OSError as e:
        return {"ok": False, "error": str(e)}
    if not p.is_dir():
        return {"ok": False, "error": f"Not a directory: {p}"}
    blocked = _is_sensitive_workspace(p)
    if blocked:
        return {"ok": False, "error": blocked}
    set_workspace_root(str(p))
    _clear_ws_caches()
    return {"ok": True, "path": str(p), "label": p.name}


def unlink_workspace() -> None:
    set_workspace_root("")
    _clear_ws_caches()


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
    global _PATHS_CACHE
    now = time.monotonic()
    if _PATHS_CACHE and now - _PATHS_CACHE[0] < _CACHE_TTL:
        return _PATHS_CACHE[1]
    root = _root()
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES and not d.startswith(".")]
        rel_dir = Path(dirpath).relative_to(root)
        for fn in filenames:
            if fn.startswith("."):
                continue
            rp = (rel_dir / fn).as_posix() if rel_dir.parts else fn
            out.append(rp.replace("\\", "/"))
            if len(out) >= max_files:
                _PATHS_CACHE = (now, out)
                return out
    _PATHS_CACHE = (now, out)
    return out


def list_tree_paths(max_files: int = 8000, *, include_hidden: bool = True) -> list[str]:
    """Explorer listing: files plus directory paths (dirs end with '/'). Includes dotfiles."""
    global _TREE_CACHE
    now = time.monotonic()
    if include_hidden and _TREE_CACHE and now - _TREE_CACHE[0] < _CACHE_TTL:
        return _TREE_CACHE[1]
    root = _root()
    out: list[str] = []
    files = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        kept = []
        for d in dirnames:
            if d in _SKIP_DIR_NAMES:
                continue
            if not include_hidden and d.startswith("."):
                continue
            kept.append(d)
        dirnames[:] = kept
        rel_dir = Path(dirpath).relative_to(root)
        if rel_dir.parts:
            out.append(rel_dir.as_posix().replace("\\", "/") + "/")
        for fn in filenames:
            if not include_hidden and fn.startswith("."):
                continue
            rp = (rel_dir / fn).as_posix() if rel_dir.parts else fn
            out.append(rp.replace("\\", "/"))
            files += 1
            if files >= max_files:
                if include_hidden:
                    _TREE_CACHE = (now, out)
                return out
    if include_hidden:
        _TREE_CACHE = (now, out)
    return out


def tree_stamp(max_files: int = 8000) -> dict[str, Any]:
    """Cheap fingerprint so the explorer can poll without downloading the full tree."""
    global _PATHS_CACHE, _TREE_CACHE, _LAST_STAMP
    t0 = time.perf_counter()
    root = _root()
    count = 0
    latest = 0
    mix = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
        rel_dir = Path(dirpath).relative_to(root)
        if rel_dir.parts:
            key = rel_dir.as_posix().replace("\\", "/") + "/"
            mix ^= hash(key)
            count += 1
        for fn in filenames:
            rp = (rel_dir / fn).as_posix() if rel_dir.parts else fn
            rp = rp.replace("\\", "/")
            try:
                m = int((Path(dirpath) / fn).stat().st_mtime_ns)
            except OSError:
                m = 0
            if m > latest:
                latest = m
            mix ^= hash((rp, m))
            count += 1
            if count >= max_files:
                break
        if count >= max_files:
            break
    result = {"ok": True, "stamp": f"{count}:{latest}:{mix}", "count": count}
    if _LAST_STAMP != result["stamp"]:
        _PATHS_CACHE = None
        _TREE_CACHE = None
        _LAST_STAMP = result["stamp"]
    # #region agent log
    try:
        from _perf_log import perf_log

        perf_log(
            "workspace_io.py:tree_stamp",
            "tree_stamp",
            {"count": count, "ms": round((time.perf_counter() - t0) * 1000, 2), "cache": False},
            "E",
        )
    except Exception:
        pass
    # #endregion
    return result


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
    try:
        target = resolve_under_root(rel_path)
    except ValueError as e:
        return {"ok": False, "error": str(e), "path": rel_path}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": str(e), "path": rel_path}
    text = content if isinstance(content, str) else str(content or "")
    target.write_text(text, encoding="utf-8")
    # #region agent log
    try:
        import json
        import time
        from pathlib import Path as _P

        _p = _P(__file__).resolve().parents[2] / "debug-ff2cb7.log"
        with _p.open("a", encoding="utf-8") as _f:
            _f.write(
                json.dumps(
                    {
                        "sessionId": "ff2cb7",
                        "timestamp": int(time.time() * 1000),
                        "location": "workspace_io.py:write_file_raw",
                        "message": "wrote nested path",
                        "hypothesisId": "E",
                        "data": {"path": rel_path, "parent_exists": target.parent.exists()},
                    }
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion
    return {"ok": True, "path": rel_path}


def write_file(rel_path: str, content: str) -> dict[str, Any]:
    from agents.execution_policy import deny_if_blocked
    from agents.run_control import record_checkpoint

    blocked = deny_if_blocked("workspace_write")
    if blocked:
        return {**blocked, "path": rel_path}
    try:
        target = resolve_under_root(rel_path)
    except ValueError as e:
        return {"ok": False, "error": str(e), "path": rel_path}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": str(e), "path": rel_path}
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
    _clear_ws_caches()
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
