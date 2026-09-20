"""Isolated workspace overlay: edits stay off the user's tree until apply/export."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Optional

_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".next",
        "target",
        ".idea",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "gold",
    }
)


def normalize_rel(rel_path: str) -> str:
    p = (rel_path or "").replace("\\", "/").strip().lstrip("/")
    if not p or p.startswith("..") or ".." in Path(p).parts:
        raise ValueError("Invalid relative path.")
    return p


def resolve_under(root: Path, rel_path: str) -> Path:
    base = Path(root).resolve()
    rel = normalize_rel(rel_path)
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError as e:
        raise ValueError("Path escapes the workspace root.") from e
    return target


class OverlayWorkspace:
    """Read-through filesystem with in-memory writes, deletes, and ada-file export."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"Workspace is not a directory: {self.root}")
        self.files: dict[str, str] = {}
        self.deleted: set[str] = set()

    def list_rel_paths(self, max_files: int = 4000) -> list[str]:
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root, topdown=True):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIR_NAMES and not d.startswith("."))
            rel_dir = Path(dirpath).relative_to(self.root)
            for fn in sorted(filenames):
                if fn.startswith("."):
                    continue
                rp = (rel_dir / fn).as_posix() if rel_dir.parts else fn
                if rp in self.deleted:
                    continue
                out.append(rp)
                if len(out) >= max_files:
                    break
            if len(out) >= max_files:
                break
        for rel in self.files:
            if rel not in out and rel not in self.deleted:
                out.append(rel)
        return sorted(set(out))

    def exists(self, rel_path: str) -> bool:
        rel = normalize_rel(rel_path)
        if rel in self.deleted:
            return False
        if rel in self.files:
            return True
        return resolve_under(self.root, rel).is_file()

    def read(self, rel_path: str, *, offset: int = 1, limit: int = 0, max_bytes: int = 400_000) -> dict[str, Any]:
        rel = normalize_rel(rel_path)
        if rel in self.deleted:
            return {"ok": False, "error": "File deleted in this session.", "path": rel}
        if rel in self.files:
            text = self.files[rel]
        else:
            target = resolve_under(self.root, rel)
            if not target.is_file():
                return {"ok": False, "error": "File not found.", "path": rel}
            data = target.read_bytes()
            if b"\0" in data[:8192]:
                return {"ok": False, "error": "Binary file.", "path": rel}
            if len(data) > max_bytes:
                data = data[:max_bytes]
            text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = max(1, int(offset or 1))
        cap = int(limit) if limit and int(limit) > 0 else 400
        cap = max(1, min(cap, 2000))
        chunk = lines[start - 1 : start - 1 + cap]
        truncated = start - 1 + cap < len(lines)
        numbered = "\n".join(f"{i}|{line}" for i, line in enumerate(chunk, start=start))
        return {
            "ok": True,
            "path": rel,
            "content": numbered,
            "line_count": len(lines),
            "offset": start,
            "truncated": truncated,
            "raw": text if (not truncated and len(text) <= 8000) else None,
        }

    def raw_text(self, rel_path: str) -> str:
        rel = normalize_rel(rel_path)
        if rel in self.deleted:
            raise FileNotFoundError(rel)
        if rel in self.files:
            return self.files[rel]
        target = resolve_under(self.root, rel)
        return target.read_text(encoding="utf-8")

    def write(self, rel_path: str, content: str) -> dict[str, Any]:
        rel = normalize_rel(rel_path)
        self.deleted.discard(rel)
        self.files[rel] = content if isinstance(content, str) else str(content or "")
        return {"ok": True, "path": rel, "bytes": len(self.files[rel].encode("utf-8"))}

    def delete(self, rel_path: str) -> dict[str, Any]:
        rel = normalize_rel(rel_path)
        self.files.pop(rel, None)
        self.deleted.add(rel)
        return {"ok": True, "path": rel, "deleted": True}

    def apply_patch(self, rel_path: str, old: str, new: str) -> dict[str, Any]:
        from tools.patch_apply import apply_unique_replace

        rel = normalize_rel(rel_path)
        try:
            current = self.raw_text(rel)
        except (OSError, FileNotFoundError, ValueError) as e:
            return {"ok": False, "error": str(e), "path": rel}
        result = apply_unique_replace(current, old, new)
        if not result.get("ok"):
            return {**result, "path": rel}
        self.write(rel, result["content"])
        return {"ok": True, "path": rel, "replacements": result.get("replacements", 1)}

    def changed_paths(self) -> list[str]:
        return sorted(set(self.files) | self.deleted)

    def ada_file_fences(self) -> str:
        blocks: list[str] = []
        for rel in sorted(self.files):
            body = self.files[rel]
            if not body.endswith("\n"):
                body += "\n"
            blocks.append(f"```ada-file:{rel}\n{body}```")
        return "\n\n".join(blocks)

    def tree_summary(self, max_files: int = 200) -> str:
        paths = self.list_rel_paths(max_files=max_files)
        extra = "…" if len(paths) >= max_files else ""
        return "\n".join(paths) + extra

    def materialize(self, dest: str | Path, *, include_all: bool = True) -> Path:
        dest_p = Path(dest)
        if dest_p.exists():
            shutil.rmtree(dest_p)
        dest_p.mkdir(parents=True, exist_ok=True)

        def _ignore(directory: str, names: list[str]) -> set[str]:
            return {n for n in names if n in _SKIP_DIR_NAMES or n.startswith(".")}

        if include_all:
            shutil.copytree(self.root, dest_p, dirs_exist_ok=True, ignore=_ignore)
        for rel in self.deleted:
            target = dest_p / rel
            if target.is_file():
                target.unlink()
        for rel, content in self.files.items():
            target = dest_p / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return dest_p

    def originals_for_changed(self) -> dict[str, Any]:
        """Disk snapshots for changed paths (call before apply_to_root)."""
        out: dict[str, Any] = {}
        for rel in self.changed_paths():
            rec: dict[str, Any] = {"deleted": rel in self.deleted, "after": self.files.get(rel)}
            try:
                target = resolve_under(self.root, rel)
                rec["before"] = target.read_text(encoding="utf-8") if target.is_file() else ""
            except (OSError, ValueError):
                rec["before"] = ""
            out[rel] = rec
        return out

    def apply_to_root(self) -> list[str]:
        written: list[str] = []
        for rel in self.deleted:
            target = resolve_under(self.root, rel)
            if target.is_file():
                target.unlink()
                written.append(rel)
        for rel, content in self.files.items():
            target = resolve_under(self.root, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(rel)
        return written
