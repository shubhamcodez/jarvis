"""Build a bounded text snapshot of a local folder for LLM context (coding mode)."""
from __future__ import annotations

import os
from pathlib import Path

_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".next",
        "out",
        "coverage",
        "target",
        ".idea",
        ".vs",
        "__MACOSX",
        ".gradle",
        ".cargo",
        ".vscode",
    }
)

_TEXT_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".md",
        ".txt",
        ".rst",
        ".json",
        ".yml",
        ".yaml",
        ".toml",
        ".ini",
        ".cfg",
        ".rs",
        ".go",
        ".java",
        ".kt",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".vue",
        ".svelte",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".html",
        ".htm",
        ".xml",
        ".sql",
        ".sh",
        ".bash",
        ".zsh",
        ".ps1",
        ".bat",
        ".cmd",
        ".dockerignore",
        ".editorconfig",
        ".gitignore",
        ".gitattributes",
        ".env.example",
    }
)

_SPECIAL_FILENAMES = frozenset(
    {
        "dockerfile",
        "makefile",
        "license",
        "copying",
        "gemfile",
        "rakefile",
        "cargo.toml",
        "cargo.lock",
        "pyproject.toml",
        "poetry.lock",
        "composer.json",
        "package.json",
        "go.mod",
        "go.sum",
    }
)

MAX_INDEX_LINES = 1_200
MAX_DEPTH = 14
MAX_FILES_WITH_BODY = 72
MAX_BYTES_PER_FILE = 36_000
MAX_TOTAL_SNAPSHOT_CHARS = 28_000


def _is_probably_root(path: Path) -> bool:
    p = path.resolve()
    if p.name == p.anchor and p.anchor in ("\\", "/"):
        return True
    s = str(p)
    if len(s) <= 3 and s[1:3] in (":\\", ":/"):
        return True
    return False


def _want_file_body(rel: Path) -> bool:
    name = rel.name.lower()
    if name in _SPECIAL_FILENAMES:
        return True
    suf = rel.suffix.lower()
    return suf in _TEXT_SUFFIXES


def _read_text_limited(abs_path: Path, limit: int) -> str:
    try:
        data = abs_path.read_bytes()
    except OSError:
        return ""
    if b"\0" in data[:8192]:
        return ""
    if len(data) > limit:
        data = data[:limit]
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _file_peek_line(abs_path: Path, max_bytes: int = 512) -> str:
    """First text line or empty (for index hints)."""
    try:
        data = abs_path.read_bytes()[:max_bytes]
    except OSError:
        return ""
    if b"\0" in data[:2048]:
        return "[binary]"
    text = data.decode("utf-8", errors="replace").splitlines()
    if not text:
        return "[empty]"
    line = (text[0] or "").strip()
    if len(line) > 120:
        line = line[:117] + "..."
    return line or "[empty line]"


def build_repository_snapshot(path_str: str) -> str:
    """
    Build a repository index: recursive tree as posix paths from the root folder name,
    then optional file excerpts.

    Tree shape (example):
        myrepo/

        myrepo/subfolder1/
        myrepo/subfolder2/

        myrepo/subfolder1/subsubfolder1/file.txt | 1024 bytes | first line…
    """
    raw = (path_str or "").strip()
    if not raw:
        return ""

    try:
        root = Path(raw).expanduser()
        root = root.resolve()
    except OSError as e:
        return f"_(Could not resolve project path `{raw}`: {e})_"

    if not root.is_dir():
        return f"_(Project path is not a directory: `{root}`)_"

    if _is_probably_root(root):
        return "_(Refusing to import filesystem root as a project; choose a repository folder.)_"

    root_label = root.name or "."
    prefix = f"{root_label}/"

    subdirs: list[tuple[str, str]] = []  # (sort_key, line)
    files_meta: list[tuple[str, str, Path]] = []  # (sort_key, index_line, rel Path)
    rel_paths_for_excerpts: list[Path] = []

    try:
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            dirnames[:] = [
                d for d in dirnames if d not in _SKIP_DIR_NAMES and not d.startswith(".")
            ]
            rel_dir = Path(dirpath).relative_to(root)
            depth = len(rel_dir.parts)
            if depth > MAX_DEPTH:
                dirnames[:] = []
                continue

            if rel_dir.parts:
                posix_dir = rel_dir.as_posix()
                line = f"{prefix}{posix_dir}/"
                subdirs.append((posix_dir.lower(), line))

            for fn in filenames:
                if fn.startswith("."):
                    continue
                rp = rel_dir / fn if rel_dir.parts else Path(fn)
                rel_paths_for_excerpts.append(rp)
                abs_f = root / rp
                try:
                    st = abs_f.stat()
                    size = st.st_size
                except OSError:
                    size = -1
                posix_f = rp.as_posix()
                if size == 0:
                    peek = "[empty]"
                elif _want_file_body(rp):
                    peek = "[excerpt below]"
                else:
                    peek = _file_peek_line(abs_f)
                size_note = f"{size} bytes" if size >= 0 else "unknown size"
                idx_line = f"{prefix}{posix_f} | {size_note} | {peek}"
                files_meta.append((posix_f.lower(), idx_line, rp))
    except OSError as e:
        return f"_(Could not walk project directory `{root}`: {e})_"

    subdirs.sort(key=lambda x: x[0])
    files_meta.sort(key=lambda x: x[0])

    subdir_lines = [row for _, row in subdirs]
    file_lines = [idx for _, idx, __ in files_meta]
    truncated_tree = len(subdir_lines) + len(file_lines) > MAX_INDEX_LINES
    if truncated_tree:
        budget = MAX_INDEX_LINES
        subdir_lines = subdir_lines[:budget]
        budget = max(0, MAX_INDEX_LINES - len(subdir_lines))
        file_lines = file_lines[:budget]

    lines: list[str] = [
        "# Imported project repository",
        f"**Root on disk:** `{root}`",
        f"**Tree root label:** `{root_label}`",
        "",
        "_(Index is a path tree + file stats/peek; excerpts follow. Sandbox Python cannot open files on disk.)_",
        "",
        "## Repository tree (recursive index)",
        "",
        prefix,
        "",
    ]

    for row in subdir_lines:
        lines.append(row)
    if subdir_lines:
        lines.append("")

    for idx_line in file_lines:
        lines.append(idx_line)

    if truncated_tree:
        lines.append("")
        lines.append("_(Tree index truncated; raise MAX_INDEX_LINES or narrow the folder.)_")

    lines.extend(["", "## File excerpts", ""])

    used = sum(len(x) + 1 for x in lines)
    bodies = 0
    for rp in sorted(rel_paths_for_excerpts, key=lambda p: str(p).replace("\\", "/").lower()):
        if bodies >= MAX_FILES_WITH_BODY:
            lines.append("_(More files omitted; excerpt limit reached.)_")
            break
        if not _want_file_body(rp):
            continue
        abs_p = root / rp
        try:
            if abs_p.is_symlink() or not abs_p.is_file():
                continue
        except OSError:
            continue
        text = _read_text_limited(abs_p, MAX_BYTES_PER_FILE)
        if not text.strip():
            continue
        heading = f"### `{prefix}{rp.as_posix()}`"
        chunk = f"{heading}\n\n```\n{text}\n```\n\n"
        if used + len(chunk) > MAX_TOTAL_SNAPSHOT_CHARS:
            lines.append("_(Snapshot size limit reached; remaining files omitted.)_")
            break
        lines.append(chunk)
        used += len(chunk)
        bodies += 1

    out = "\n".join(lines).strip()
    if len(out) > MAX_TOTAL_SNAPSHOT_CHARS:
        out = out[: MAX_TOTAL_SNAPSHOT_CHARS - 20].rstrip() + "\n\n_(truncated)_"
    return out
