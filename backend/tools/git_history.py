"""Read-only git helpers. No commit/push/reset — localization and /diff only."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

_ALLOWED = frozenset({"log", "blame", "diff", "status", "rev-parse"})


def git_available() -> bool:
    return bool(shutil.which("git"))


def is_git_repo(root: str | Path) -> bool:
    p = Path(root)
    return (p / ".git").exists() or (p / ".git").is_file()


def _run(root: str | Path, args: list[str], timeout_sec: float = 8.0) -> dict[str, Any]:
    if not args or args[0] not in _ALLOWED:
        return {"ok": False, "error": "unsupported git action"}
    if not git_available():
        return {"ok": False, "error": "git is not on PATH"}
    cwd = Path(root)
    if not cwd.is_dir():
        return {"ok": False, "error": "workspace is not a directory"}
    cmd = ["git", "-c", "safe.directory=*", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(2.0, min(float(timeout_sec), 20.0)),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "git timed out"}
    except OSError as e:
        return {"ok": False, "error": str(e)}
    out = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "output": out[:12_000],
    }


def git_status(root: str | Path) -> dict[str, Any]:
    return _run(root, ["status", "--short", "--untracked-files=all"])


def git_diff(root: str | Path, *, staged: bool = False, path: str = "") -> dict[str, Any]:
    args = ["diff", "--no-color", "--stat"]
    if staged:
        args.append("--cached")
    detail = _run(root, ["diff", "--no-color", "--cached" if staged else "--", *( [path] if path else [])])
    stat = _run(root, args + ([path] if path else []))
    body = (detail.get("output") or "").strip()
    if not body:
        body = "(no unstaged changes)" if not staged else "(no staged changes)"
    return {
        "ok": True,
        "staged": staged,
        "stat": (stat.get("output") or "")[:2000],
        "diff": body[:10_000],
    }


def git_log(root: str | Path, *, path: str = "", limit: int = 8) -> dict[str, Any]:
    n = max(1, min(int(limit or 8), 20))
    args = ["log", f"-n{n}", "--oneline", "--decorate"]
    if path:
        args.extend(["--", path.replace("\\", "/")])
    return _run(root, args)


def git_blame(root: str | Path, path: str, *, limit: int = 40) -> dict[str, Any]:
    rel = (path or "").replace("\\", "/").strip().lstrip("/")
    if not rel or ".." in Path(rel).parts:
        return {"ok": False, "error": "path required"}
    out = _run(root, ["blame", "-e", "--", rel])
    lines = (out.get("output") or "").splitlines()
    cap = max(1, min(int(limit or 40), 80))
    out["output"] = "\n".join(lines[:cap])
    out["path"] = rel
    return out


def recent_touched_files(root: str | Path, limit: int = 8) -> list[str]:
    """Files touched in recent commits — cheap extra localization signal."""
    n = max(1, min(int(limit or 8), 20))
    raw = _run(root, ["log", f"-n{n}", "--name-only", "--pretty=format:"])
    if not raw.get("ok"):
        return []
    seen: list[str] = []
    for line in (raw.get("output") or "").splitlines():
        p = line.strip().replace("\\", "/")
        if not p or p in seen:
            continue
        seen.append(p)
        if len(seen) >= limit:
            break
    return seen


def git_history(
    root: str | Path,
    action: str,
    *,
    path: str = "",
    limit: int = 8,
) -> dict[str, Any]:
    act = (action or "log").strip().lower()
    if act == "log":
        return git_log(root, path=path, limit=limit)
    if act == "blame":
        return git_blame(root, path, limit=max(limit, 20))
    if act == "diff":
        return git_diff(root, path=path)
    if act == "status":
        return git_status(root)
    return {"ok": False, "error": f"unknown action: {act}"}
