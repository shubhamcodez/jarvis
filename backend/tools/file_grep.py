"""
Fast literal or regex search under a directory — ripgrep when available (same idea as Cursor’s rg/bash workflow), else Python scan with common ignore dirs.

No vector index: this is on-demand search against the filesystem.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

# Directory names to skip (walk and, when possible, ripgrep glob)
IGNORE_DIR_NAMES = frozenset(
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
        ".cargo",
        ".idea",
        ".vscode",
    }
)

# Skip huge / likely binary extensions
SKIP_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".7z",
        ".rar",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".pdb",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp4",
        ".mp3",
        ".wav",
        ".avi",
        ".mkv",
    }
)

MAX_FILE_BYTES = 2 * 1024 * 1024
RG_TIMEOUT_SEC = 120.0


def _rg_globs_for_ignores() -> list[str]:
    """Map ignore dir names to ripgrep --glob '!**/name/**' (best-effort)."""
    globs = [f"!**/{name}/**" for name in sorted(IGNORE_DIR_NAMES)]
    globs.extend(["!**/.env", "!**/.env.*", "!**/*.pem", "!**/*.key"])
    return globs


def search_with_ripgrep(
    root: Path,
    pattern: str,
    *,
    max_results: int,
    fixed_string: bool = True,
    ignore_case: bool = True,
) -> tuple[list[dict[str, Any]], bool, bool]:
    """
    Run ripgrep --json. Returns (matches, truncated, ran_ok).
    ran_ok is True if ripgrep executed and exited 0 or 1 (no matches vs matches).
    """
    rg = shutil.which("rg")
    if not rg:
        return [], False, False

    root = root.resolve()
    if not root.is_dir():
        return [], False, False

    cmd: list[str] = [
        rg,
        "--json",
        "--threads",
        str(min(8, (os.cpu_count() or 4))),
        "--max-count",
        str(max(1, max_results)),
        "--max-columns",
        "400",
        "--max-columns-preview",
    ]
    if ignore_case:
        cmd.append("-i")
    if fixed_string:
        cmd.append("-F")
    for g in _rg_globs_for_ignores():
        cmd.extend(["--glob", g])
    cmd.append(pattern)
    cmd.append(str(root))

    try:
        from tools.win_subprocess import run_hidden

        proc = run_hidden(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=RG_TIMEOUT_SEC,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return [], True, True
    except OSError:
        return [], False, False

    if proc.returncode not in (0, 1):
        return [], False, False

    matches: list[dict[str, Any]] = []
    truncated = False
    for raw in proc.stdout.splitlines():
        if len(matches) >= max_results:
            truncated = True
            break
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "match":
            continue
        data = obj.get("data") or {}
        path_obj = data.get("path") or {}
        path_text = path_obj.get("text")
        if not path_text:
            continue
        line_no = data.get("line_number")
        lines_obj = data.get("lines") or {}
        snippet = (lines_obj.get("text") or "").rstrip("\n\r")
        if line_no is None:
            continue
        matches.append({"path": path_text, "line": int(line_no), "snippet": snippet})

    # Normalize to absolute paths when ripgrep emits relative paths
    abs_matches: list[dict[str, Any]] = []
    for m in matches:
        p = Path(m["path"])
        if not p.is_absolute():
            p = (root / p).resolve()
        abs_matches.append({**m, "path": str(p)})
    return abs_matches, truncated, True


def search_python_scan(
    root: Path,
    pattern: str,
    *,
    max_results: int,
    fixed_string: bool = True,
    ignore_case: bool = True,
) -> tuple[list[dict[str, Any]], bool]:
    """Walk tree with ignore dirs; search line-by-line. Slower than rg on large trees."""
    root = root.resolve()
    if not root.is_dir():
        return [], False

    if fixed_string:
        needle = pattern.lower() if ignore_case else pattern

        def line_hits(text: str) -> bool:
            if ignore_case:
                return needle in text.lower()
            return needle in text

    else:
        flags = re.IGNORECASE if ignore_case else 0
        try:
            rx = re.compile(pattern, flags)
        except re.error:
            return [], False

        def line_hits(text: str) -> bool:
            return rx.search(text) is not None

    matches: list[dict[str, Any]] = []
    truncated = False

    for dirpath, dirnames, filenames in os.walk(str(root), topdown=True, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIR_NAMES]
        for name in filenames:
            if len(matches) >= max_results:
                truncated = True
                break
            fp = Path(dirpath) / name
            try:
                if not fp.is_file():
                    continue
            except OSError:
                continue
            suf = fp.suffix.lower()
            if name.lower() in {".env", ".env.local", ".env.production"} or suf in {".pem", ".key", ".p12"}:
                continue
            if suf in SKIP_EXTENSIONS:
                continue
            try:
                st = fp.stat()
            except OSError:
                continue
            if st.st_size > MAX_FILE_BYTES:
                continue
            try:
                data = fp.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:8192]:
                continue
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:
                continue
            rel = str(fp.resolve())
            for i, line in enumerate(text.splitlines(), start=1):
                if len(matches) >= max_results:
                    truncated = True
                    break
                if line_hits(line):
                    matches.append(
                        {
                            "path": rel,
                            "line": i,
                            "snippet": line[:500] if len(line) > 500 else line,
                        }
                    )
            if truncated:
                break
        if truncated:
            break

    return matches, truncated


def grep_files(
    root: Path | str,
    pattern: str,
    *,
    max_results: int = 100,
    fixed_string: bool = True,
    ignore_case: bool = True,
) -> dict[str, Any]:
    """
    Search `pattern` under `root`. Prefer ripgrep; fall back to Python scanner.
    Returns a dict suitable for JSON: ok, engine, root, pattern, matches, truncated, error?.
    """
    pattern = (pattern or "").strip()
    if not pattern:
        return {
            "ok": False,
            "engine": None,
            "root": str(root),
            "pattern": pattern,
            "matches": [],
            "truncated": False,
            "error": "empty pattern",
        }

    r = Path(root).expanduser()
    try:
        r = r.resolve()
    except OSError as e:
        return {
            "ok": False,
            "engine": None,
            "root": str(root),
            "pattern": pattern,
            "matches": [],
            "truncated": False,
            "error": str(e),
        }

    if not r.is_dir():
        return {
            "ok": False,
            "engine": None,
            "root": str(r),
            "pattern": pattern,
            "matches": [],
            "truncated": False,
            "error": "root is not a directory",
        }

    cap = max(1, min(max_results, 5000))
    matches: list[dict[str, Any]] = []
    truncated = False
    engine = "python"

    if shutil.which("rg"):
        matches, truncated, rg_ok = search_with_ripgrep(
            r,
            pattern,
            max_results=cap,
            fixed_string=fixed_string,
            ignore_case=ignore_case,
        )
        if rg_ok:
            engine = "ripgrep"
        else:
            matches, truncated = search_python_scan(
                r,
                pattern,
                max_results=cap,
                fixed_string=fixed_string,
                ignore_case=ignore_case,
            )
            engine = "python"
    else:
        matches, truncated = search_python_scan(
            r,
            pattern,
            max_results=cap,
            fixed_string=fixed_string,
            ignore_case=ignore_case,
        )
        engine = "python"

    return {
        "ok": True,
        "engine": engine,
        "root": str(r),
        "pattern": pattern,
        "matches": matches,
        "truncated": truncated,
    }
